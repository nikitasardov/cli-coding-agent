import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

import tools
from dotenv import load_dotenv

load_dotenv()


def _default_agent_system_prompt_path() -> Path:
    raw = os.getenv("AGENT_SYSTEM_PROMPT_FILE", "").strip()
    base = Path(__file__).resolve().parent
    if raw:
        path = Path(raw)
        return path if path.is_absolute() else base / path
    return base / "agent_system_prompt.md"


def _load_default_agent_system_prompt() -> str:
    path = _default_agent_system_prompt_path()
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return (
            "You are a coding assistant with file tools under project/. "
            f"The system prompt file is missing or unreadable: {path}. "
            "Restore agent_system_prompt.md or set AGENT_SYSTEM_PROMPT_FILE."
        )


LLM_BACKEND = os.getenv("LLM_BACKEND", "ollama").strip().lower()

VSEGPT_API_URL = os.getenv("VSEGPT_API_URL", "https://api.vsegpt.ru/v1/chat/completions")
VSEGPT_MODEL = os.getenv("VSEGPT_MODEL", "openai/gpt-4o-mini")
VSEGPT_MAX_RETRIES = int(os.getenv("VSEGPT_MAX_RETRIES", "5"))
VSEGPT_RETRY_BASE_SECONDS = float(os.getenv("VSEGPT_RETRY_BASE_SECONDS", "1.0"))
VSEGPT_RETRY_MAX_SECONDS = float(os.getenv("VSEGPT_RETRY_MAX_SECONDS", "20.0"))

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "smollm2:1.7b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "300"))

# Дефолтный текст: agent_system_prompt.md рядом с llm.py (или путь в AGENT_SYSTEM_PROMPT_FILE).
# Полное переопределение строкой: OLLAMA_SYSTEM_PROMPT / VSEGPT_SYSTEM_PROMPT в .env.
DEFAULT_AGENT_SYSTEM_PROMPT = _load_default_agent_system_prompt()

OLLAMA_SYSTEM_PROMPT = os.getenv("OLLAMA_SYSTEM_PROMPT", DEFAULT_AGENT_SYSTEM_PROMPT)
VSEGPT_SYSTEM_PROMPT = os.getenv("VSEGPT_SYSTEM_PROMPT", DEFAULT_AGENT_SYSTEM_PROMPT)


def _normalize_leaked_tool_obj(obj: object) -> dict | None:
    if not isinstance(obj, dict):
        return None
    name = obj.get('name')
    arguments = obj.get('arguments')
    if arguments is None:
        arguments = {}
    if (not isinstance(name, str) or not name.strip()) and 'function' in obj:
        fn = obj['function']
        if isinstance(fn, str) and fn.strip():
            name = fn
        elif isinstance(fn, dict):
            inner_name = fn.get('name')
            if isinstance(inner_name, str) and inner_name.strip():
                name = inner_name
            inner_args = fn.get('arguments', '{}')
            if not arguments:
                arguments = inner_args
    if not isinstance(name, str) or not name.strip():
        return None
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    return {'name': name.strip(), 'arguments': arguments}


def _ollama_extract_tool_call_from_content(content: str) -> dict | None:
    """Если API не вернул tool_calls, вытащить вызов из текста: чистый JSON, ```json ... ```, или первый объект {...}."""
    if not content or not content.strip():
        return None
    for match in re.finditer(r'```(?:json)?\s*\n?(.*?)```', content, re.DOTALL):
        inner = match.group(1).strip()
        obj = _json_loads_lenient_object(inner)
        if obj is None:
            continue
        normalized = _normalize_leaked_tool_obj(obj)
        if normalized:
            return normalized
    text = content.strip()
    if text.startswith('{'):
        obj = _json_loads_lenient_object(text)
        if obj is not None:
            normalized = _normalize_leaked_tool_obj(obj)
            if normalized:
                return normalized
    start = content.find('{')
    if start != -1:
        tail = content[start:]
        obj = None
        try:
            obj, _ = json.JSONDecoder().raw_decode(tail)
        except json.JSONDecodeError:
            try:
                obj, _ = json.JSONDecoder().raw_decode(_quote_bareword_tool_name_values(tail))
            except json.JSONDecodeError:
                pass
        if obj is not None:
            normalized = _normalize_leaked_tool_obj(obj)
            if normalized:
                return normalized
    return None


def _parse_tool_call(raw_tool_call: dict) -> dict:
    function_data = raw_tool_call.get('function', {})
    function_name = function_data.get('name', '')
    raw_arguments = function_data.get('arguments', '{}')
    if isinstance(raw_arguments, dict):
        arguments = raw_arguments
    else:
        try:
            arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
        except json.JSONDecodeError:
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    return {
        'name': function_name,
        'arguments': arguments,
    }


def _quote_bareword_tool_name_values(text: str) -> str:
    """Чинит типичную ошибку моделей: \"name\": code_search без кавычек у значения."""

    def repl(m: re.Match) -> str:
        return f'{m.group(1)}: "{m.group(2)}"'

    text = re.sub(
        r'("name")\s*:\s*([a-zA-Z_][\w]*)(?=\s*[,}])',
        repl,
        text,
    )
    text = re.sub(
        r'("function")\s*:\s*([a-zA-Z_][\w]*)(?=\s*[,}])',
        repl,
        text,
    )
    return text


def _json_loads_lenient_object(s: str) -> object | None:
    s = s.strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        try:
            return json.loads(_quote_bareword_tool_name_values(s))
        except json.JSONDecodeError:
            return None


def _messages_for_ollama(messages: list[dict]) -> list[dict]:
    """Ollama /api/chat: другой формат, чем OpenAI (см. docs) — иначе 400 mismatch / invalid tool usage."""
    out: list[dict] = []
    for m in messages:
        role = m.get('role')
        if role == 'tool':
            tool_name = m.get('name') or m.get('tool_name') or ''
            out.append({
                'role': 'tool',
                'content': m.get('content', '') or '',
                'tool_name': str(tool_name),
            })
            continue
        if role == 'assistant' and m.get('tool_calls'):
            ollama_calls: list[dict] = []
            for tc in m['tool_calls']:
                fn = tc.get('function')
                if not isinstance(fn, dict):
                    continue
                raw_name = fn.get('name', '')
                raw_args = fn.get('arguments', {})
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args) if raw_args.strip() else {}
                    except json.JSONDecodeError:
                        args = {}
                elif isinstance(raw_args, dict):
                    args = raw_args
                else:
                    args = {}
                ollama_calls.append({
                    'function': {
                        'name': raw_name,
                        'arguments': args,
                    },
                })
            out.append({
                'role': 'assistant',
                'content': m.get('content') or '',
                'tool_calls': ollama_calls,
            })
            continue
        out.append({
            'role': role,
            'content': m.get('content', '') or '',
        })
    return out


def _retry_delay_seconds(error: urllib.error.HTTPError, attempt: int) -> float:
    retry_after = error.headers.get("Retry-After")
    if retry_after:
        try:
            return min(float(retry_after), VSEGPT_RETRY_MAX_SECONDS)
        except ValueError:
            pass
    delay = VSEGPT_RETRY_BASE_SECONDS * (2 ** attempt)
    return min(delay, VSEGPT_RETRY_MAX_SECONDS)


def _complete_vsegpt(messages: list[dict]) -> dict:
    api_key = os.getenv("VSEGPT_API_KEY")
    if not api_key:
        return {
            'content': "VSEGPT_API_KEY is not set",
            'wants_tool': False
        }

    payload = {
        "model": VSEGPT_MODEL,
        "temperature": 0.2,
        "messages": [{"role": "system", "content": VSEGPT_SYSTEM_PROMPT}] + messages,
        "tools": tools.get_tools_for_llm(),
        "tool_choice": "auto",
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        VSEGPT_API_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    for attempt in range(VSEGPT_MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read().decode("utf-8")
                response_json = json.loads(body)
                message = response_json["choices"][0]["message"]
                tool_calls = message.get("tool_calls", [])
                content = message.get("content") or ""
                if tool_calls:
                    tool_call_id = tool_calls[0].get('id', 'call_1')
                    return {
                        'content': str(content),
                        'wants_tool': True,
                        'tool_call': _parse_tool_call(tool_calls[0]),
                        'tool_call_id': tool_call_id,
                    }
                return {
                    'content': str(content),
                    'wants_tool': False
                }
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < VSEGPT_MAX_RETRIES:
                time.sleep(_retry_delay_seconds(error, attempt))
                continue
            return {
                'content': f'LLM request failed: HTTP {error.code}',
                'wants_tool': False
            }
        except TimeoutError:
            return {
                'content': 'LLM request timed out (HTTP 60s limit in client for VseGPT). Retry or increase timeout in code if needed.',
                'wants_tool': False
            }
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
            return {
                'content': f'LLM request failed: {error}',
                'wants_tool': False
            }

    return {
        'content': 'LLM request failed: max retries exceeded',
        'wants_tool': False
    }


def _complete_ollama(messages: list[dict]) -> dict:
    url = f"{OLLAMA_BASE_URL}/api/chat"
    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "messages": [{"role": "system", "content": OLLAMA_SYSTEM_PROMPT}] + _messages_for_ollama(messages),
        "tools": tools.get_tools_for_llm(),
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=OLLAMA_TIMEOUT) as response:
            body = response.read().decode("utf-8")
            response_json = json.loads(body)
            message = response_json.get("message", {})
            tool_calls = message.get("tool_calls") or []
            content = message.get("content") or ""
            if tool_calls:
                raw = tool_calls[0]
                if "function" not in raw and "name" in raw:
                    raw = {"function": raw}
                tool_call_id = raw.get("id", "call_1")
                return {
                    'content': str(content),
                    'wants_tool': True,
                    'tool_call': _parse_tool_call(raw),
                    'tool_call_id': tool_call_id,
                }
            leaked = _ollama_extract_tool_call_from_content(content)
            if leaked:
                return {
                    'content': '',
                    'wants_tool': True,
                    'tool_call': leaked,
                    'tool_call_id': 'call_1',
                }
            return {
                'content': str(content),
                'wants_tool': False
            }
    except urllib.error.HTTPError as error:
        try:
            detail = error.read().decode('utf-8', errors='replace')
        except Exception:
            detail = ''
        suffix = f' Body: {detail}' if detail else ''
        return {
            'content': f'Ollama request failed: HTTP {error.code}: {error.reason}.{suffix}',
            'wants_tool': False
        }
    except TimeoutError:
        return {
            'content': (
                f'Ollama request timed out after {OLLAMA_TIMEOUT}s (client HTTP read timeout). '
                f'Raise OLLAMA_TIMEOUT in .env for slow or remote servers; if the server returns 500 near 2m, '
                f'check Ollama/proxy limits and model load.'
            ),
            'wants_tool': False
        }
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, TypeError) as error:
        return {
            'content': f'Ollama request failed: {error}',
            'wants_tool': False
        }


def complete(messages: list[dict]) -> dict:
    if LLM_BACKEND == "vsegpt":
        return _complete_vsegpt(messages)
    if LLM_BACKEND == "ollama":
        return _complete_ollama(messages)
    return {
        'content': f'Unknown LLM_BACKEND={LLM_BACKEND!r}; use ollama or vsegpt',
        'wants_tool': False
    }
