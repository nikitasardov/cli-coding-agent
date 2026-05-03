# cli-coding-agent

Минимальный CLI-агент с циклом **LLM + tools** (чтение/список/правка файлов, поиск по коду). Идея и пример в статье [Geoffrey Huntley](https://ghuntley.com/agent/).

## Зависимости

- Python **3.10+**
- Для `code_search`: должен быть установлен **`rg`**. На Debian/Ubuntu обычно: `sudo apt install ripgrep`. На других системах — см. [установку в README](https://github.com/BurntSushi/ripgrep).
- **Ollama** локально или удалённо, либо **VseGPT** (OpenAI-совместимый API) — см. `.env.example`

Python-пакеты перечислены в **`requirements.txt`** (сейчас достаточно `python-dotenv`).

## Установка и запуск

```bash
cd /path/to/repo               # корень клонированного репозитория

python3 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# отредактировать .env: LLM_BACKEND и настройки Ollama или VseGPT

python3 agent.py
```

Выход из агента: **Ctrl+C** или **Ctrl+D** (EOF).

Инструменты работают только внутри каталога **`project/`**. Выполнение **bash** по умолчанию выключено; включение: `TOOLS_ALLOW_BASH=1` (небезопасно).

## Конфигурация

Переменные окружения описаны в **`.env.example`**: бэкенд (`ollama` / `vsegpt`), модель, таймауты, опционально путь к **`agent_system_prompt.md`**.

## Структура

| Файл                     | Назначение                                         |
| ------------------------ | -------------------------------------------------- |
| `agent.py`               | REPL, цикл tools, вывод в консоль                  |
| `llm.py`                 | Ollama / VseGPT, нормализация сообщений под Ollama |
| `tools.py`               | Песочница `project/`, регистрация инструментов     |
| `agent_system_prompt.md` | Системный промпт по умолчанию                      |
