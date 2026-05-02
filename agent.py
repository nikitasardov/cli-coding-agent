import json
import os
import sys
import threading
import llm
import tools

MAX_TOOL_STEPS = 50
MAX_TOOL_OUTPUT_PRINT = 6000

_RESET = '\033[0m'
_USER = '\033[1;36m'
_TOOL = '\033[1;33m'
_ASSISTANT = '\033[1;32m'
_SYSTEM = '\033[1;31m'
_META = '\033[90m'


def _ansi_enabled() -> bool:
    return sys.stdout.isatty() and not os.environ.get('NO_COLOR', '').strip()


def _paint(code: str, text: str) -> str:
    if not _ansi_enabled():
        return text
    return f'{code}{text}{_RESET}'


def _progress_label(text: str) -> str:
    return _paint(_META, text)


def _tool_block_title(tool_name: str) -> str:
    if tool_name == 'system':
        return _paint(_SYSTEM, '[Система]')
    return _paint(_TOOL, f'[Инструмент «{tool_name}»]')


def _you_prompt() -> str:
    return f'{_paint(_USER, "You:")} ' if _ansi_enabled() else 'You: '


def _assistant_heading() -> str:
    return _paint(_ASSISTANT, 'Ассистент:')


def _run_with_progress(label: str, fn):
    """Пока выполняется fn(), печатает label и точки, чтобы было видно, что процесс жив."""
    stop = threading.Event()

    def _dots():
        sys.stdout.write(label)
        sys.stdout.flush()
        while not stop.wait(0.75):
            sys.stdout.write('.')
            sys.stdout.flush()
        sys.stdout.write('\n')
        sys.stdout.flush()

    thread = threading.Thread(target=_dots, daemon=True)
    thread.start()
    try:
        return fn()
    finally:
        stop.set()
        thread.join(timeout=5.0)
        if thread.is_alive():
            sys.stdout.write('\n')
            sys.stdout.flush()


def _execute_tool_deduped(
    tool_call: dict,
    tool_name: str,
    turn_exact_sigs: set[str],
    turn_code_search_scope: set[tuple[str, str]],
) -> str:
    """Один раз за ход пользователя: тот же вызов или тот же code_search(pattern, dir) — без повторного rg."""
    arguments = tool_call.get('arguments', {})
    if not isinstance(arguments, dict):
        arguments = {}
    exact_sig = f'{tool_name}:{json.dumps(arguments, sort_keys=True, ensure_ascii=True)}'
    if exact_sig in turn_exact_sigs:
        return (
            'Duplicate call skipped: this tool and arguments already ran in this user turn. '
            'Use the previous tool output. Next step: a different tool or different arguments '
            "(e.g. list_files with path='.')."
        )
    if tool_name == 'code_search':
        pattern = str(arguments.get('pattern', ''))
        directory = str(arguments.get('directory', '.')) or '.'
        scope = (pattern, directory)
        if scope in turn_code_search_scope:
            return (
                'code_search with this pattern and directory was already run in this turn '
                '(changing only file_type does not help). Do not repeat. '
                "Call list_files(path='.') to list file names, try a different pattern, or read_file if you know the path."
            )
        turn_code_search_scope.add(scope)
    turn_exact_sigs.add(exact_sig)
    return tools.execute_tool(tool_call)


def _tool_args_preview(tool_name: str, arguments: object) -> str:
    """Краткая строка аргументов для строки прогресса (без переносов)."""
    if not isinstance(arguments, dict):
        arguments = {}
    if tool_name == 'code_search':
        parts = [f"pattern={arguments.get('pattern', '')!r}"]
        directory = arguments.get('directory', '.')
        if directory not in (None, '', '.'):
            parts.append(f"dir={directory!r}")
        file_type = arguments.get('file_type', '')
        if file_type:
            parts.append(f"type={file_type!r}")
        inner = ', '.join(parts)
    elif tool_name == 'read_file':
        inner = f"path={arguments.get('file_path', '')!r}"
    elif tool_name == 'list_files':
        inner = f"path={arguments.get('path', '.')!r}"
    elif tool_name == 'edit_file':
        inner = f"path={arguments.get('file_path', '')!r}"
    elif tool_name == 'bash':
        cmd = str(arguments.get('command', ''))
        if len(cmd) > 48:
            cmd = cmd[:45] + '...'
        inner = f"cmd={cmd!r}"
    else:
        inner = json.dumps(arguments, ensure_ascii=True, sort_keys=True)
    out = f'({inner})'
    if len(out) > 90:
        out = out[:87] + '...'
    return out


def _print_turn(turn_tool_results: list[tuple[str, str]], assistant_text: str) -> None:
    """Печать одного пользовательского хода: инструменты (если были), затем ответ ассистента."""
    for tool_name, result in turn_tool_results:
        print(f'\n{_tool_block_title(tool_name)}')
        out = result
        if len(out) > MAX_TOOL_OUTPUT_PRINT:
            out = out[:MAX_TOOL_OUTPUT_PRINT] + '\n… (вывод обрезан)'
        print(out)
    print(f'\n{_assistant_heading()}')
    text = (assistant_text or '').strip()
    print(text if text else '(пусто)')
    print()


tools.register_tools()

messages: list[dict] = []

while True:
    try:
        user_input = input(_you_prompt())
    except (EOFError, KeyboardInterrupt):
        print("\nBye!")
        break
    messages.append({'role': 'user', 'content': user_input})

    turn_tool_results: list[tuple[str, str]] = []
    turn_exact_sigs: set[str] = set()
    turn_code_search_scope: set[tuple[str, str]] = set()

    response = _run_with_progress(
        _progress_label('Ожидание ответа модели '),
        lambda: llm.complete(messages),
    )
    assistant_content = response.get('content', '')

    tool_steps = 0
    while response.get('wants_tool', False):
        tool_steps += 1
        if tool_steps > MAX_TOOL_STEPS:
            turn_tool_results.append(('system', 'Tool loop stopped: too many steps'))
            assistant_content = ''
            break

        tool_call = response.get('tool_call', {})
        tool_call_id = response.get('tool_call_id', f'call_{tool_steps}')
        tool_name = tool_call.get('name', '')
        messages.append({
            'role': 'assistant',
            'content': assistant_content,
            'tool_calls': [
                {
                    'id': tool_call_id,
                    'type': 'function',
                    'function': {
                        'name': tool_name,
                        'arguments': json.dumps(tool_call.get('arguments', {})),
                    },
                }
            ],
        })

        args_preview = _tool_args_preview(tool_name, tool_call.get('arguments', {}))
        tool_label = (
            _progress_label('Выполняется инструмент ')
            + _paint(_TOOL, f'«{tool_name}»')
            + f' {args_preview} '
        )
        result = _run_with_progress(
            tool_label,
            lambda tc=tool_call, tn=tool_name: _execute_tool_deduped(
                tc, tn, turn_exact_sigs, turn_code_search_scope
            ),
        )
        turn_tool_results.append((tool_name, result))
        messages.append({
            'role': 'tool',
            'tool_call_id': tool_call_id,
            'name': tool_name,
            'content': result,
        })

        response = _run_with_progress(
            _progress_label('Ожидание ответа модели '),
            lambda: llm.complete(messages),
        )
        assistant_content = response.get('content', '')

    _print_turn(turn_tool_results, assistant_content)
