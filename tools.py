import os
from pathlib import Path

tools = []


def _project_root() -> Path:
    return Path(__file__).resolve().parent / "project"


def _resolve_under_project(user_path: str) -> tuple[Path | None, str | None]:
    root = _project_root()
    if not root.is_dir():
        return None, f'Sandbox: {root} is not a directory'
    raw = Path(user_path)
    if raw.is_absolute():
        candidate = raw.resolve()
    else:
        candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None, f'Access denied: must stay under {root} (got {user_path!r})'
    return candidate, None


def execute_tool(tool_call: dict | str) -> str:
    global tools
    if isinstance(tool_call, dict):
        tool_name = tool_call.get('name', '')
        arguments = tool_call.get('arguments', {})
    else:
        tool_name = tool_call.replace('()', '')
        arguments = {}

    tool = next((tool for tool in tools if tool['name'] == tool_name), None)
    if tool:
        try:
            return str(tool['function'](**arguments))
        except TypeError as error:
            return f'Invalid arguments for tool {tool_name}: {error}'
        except Exception as error:
            return f'Tool {tool_name} failed: {error}'
    else:
        return f'Tool {tool_name} not found'


def register_tools() -> None:
    global tools

    tools = [
        {
            'name': 'read_file',
            'description': 'Read a file under the project/ folder. Paths are relative to project/ (e.g. app/main.py).',
            'input_schema': {
                'type': 'object',
                'properties': {
                    'file_path': {'type': 'string'},
                },
                'required': ['file_path'],
            },
            'function': 'read_file',
        },
        {
            'name': 'list_files',
            'description': 'List files and directories under project/. Path is relative to project/ (use . for project root).',
            'input_schema': {
                'type': 'object',
                'properties': {
                    'path': {'type': 'string'},
                },
                'required': ['path'],
            },
            'function': 'list_files',
        },
        {
            'name': 'bash',
            'description': 'Execute a bash command (disabled by default in project sandbox).',
            'input_schema': {
                'type': 'object',
                'properties': {
                    'command': {'type': 'string'},
                },
                'required': ['command'],
            },
            'function': 'bash'
        },
        {
            'name': 'edit_file',
            'description': 'Create or edit a text file under project/. Path relative to project/. '
            'Creates the file if it does not exist. For a new file or empty file, set old_str to "" and new_str to the full desired content. '
            'For edits, old_str must match an existing substring (all occurrences become new_str). Prefer this over bash for writing files.',
            'input_schema': {
                'type': 'object',
                'properties': {
                    'file_path': {'type': 'string'},
                    'old_str': {'type': 'string', 'description': 'Empty string for new/empty file with content only in new_str; otherwise exact substring to replace.'},
                    'new_str': {'type': 'string'},
                },
                'required': ['file_path', 'old_str', 'new_str'],
            },
            'function': 'edit_file',
        },
        {
            'name': 'code_search',
            'description': 'Search under project/ with ripgrep (rg). If file type is obvious, pass file_type (py, js, md). If unsure, omit file_type first. directory is relative to project/ (default .).',
            'input_schema': {
                'type': 'object',
                'properties': {
                    'pattern': {'type': 'string'},
                    'file_type': {'type': 'string', 'description': 'Optional extension without dot (py, js, md). Omit for broad search if unsure.'},
                    'directory': {'type': 'string'},
                },
                'required': ['pattern'],
            },
            'function': 'code_search',
        }
    ]

    for tool in tools:
        tool['function'] = globals()[tool['function']]


def get_tools_for_llm() -> list[dict]:
    return [
        {
            'type': 'function',
            'function': {
                'name': tool['name'],
                'description': tool['description'],
                'parameters': tool['input_schema'],
            },
        }
        for tool in tools
    ]


def read_file(file_path: str) -> str:
    resolved, err = _resolve_under_project(file_path)
    if err:
        return err
    try:
        with open(resolved, 'r', encoding='utf-8') as file:
            return file.read()
    except FileNotFoundError:
        return f'File not found: {resolved}'
    except IsADirectoryError:
        return f'Path is a directory, expected file: {resolved}'
    except OSError as error:
        return f'Cannot read file {resolved}: {error}'


def list_files(path: str = '.') -> str:
    resolved, err = _resolve_under_project(path)
    if err:
        return err
    return '\n'.join(sorted(os.listdir(resolved)))


def bash(command: str) -> str:
    if os.getenv("TOOLS_ALLOW_BASH", "0") != "1":
        return (
            'bash is disabled in project sandbox. '
            'Use read_file, list_files, edit_file, code_search under project/. '
            'Set TOOLS_ALLOW_BASH=1 to allow (unsafe).'
        )
    import subprocess
    return subprocess.run(command, shell=True, capture_output=True, text=True).stdout


def edit_file(file_path: str, old_str: str, new_str: str) -> str:
    resolved, err = _resolve_under_project(file_path)
    if err:
        return err
    if not os.path.exists(resolved):
        with open(resolved, 'w', encoding='utf-8') as file:
            file.write('')
    with open(resolved, 'r', encoding='utf-8') as file:
        content = file.read()
    content = content.replace(old_str, new_str)
    with open(resolved, 'w', encoding='utf-8') as file:
        file.write(content)
    return f'File {resolved} edited'


def code_search(pattern: str, file_type: str = '', directory: str = '.') -> str:
    import shutil
    import subprocess
    dir_resolved, err = _resolve_under_project(directory)
    if err:
        return err
    if shutil.which('rg') is None:
        return 'ripgrep (rg) is not installed. Install it to use code_search.'

    command = ['rg', pattern, str(dir_resolved)]
    hint = ''
    if file_type:
        command.extend(['--glob', f'*.{file_type.lstrip(".")}'])
    else:
        hint = 'Tip: for more precise search, pass file_type (for example: py, js, md).\n'

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode == 0:
        return hint + result.stdout
    if result.returncode == 1:
        return hint + f'No matches found for pattern: {pattern}'
    return f'code_search failed: {result.stderr.strip() or "unknown error"}'
