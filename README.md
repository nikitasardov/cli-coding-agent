# make-agent

Минимальный CLI-агент с циклом **LLM + tools** (чтение/список/правка файлов, поиск по коду). Идея и мотивация — в статье [Geoffrey Huntley: *Please, don’t make me code in your text box*](https://ghuntley.com/agent/).

## Зависимости

- Python **3.10+**
- `pip install python-dotenv`
- Для `code_search`: [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`) в `PATH`
- **Ollama** локально или удалённо, либо **VseGPT** (OpenAI-совместимый API) — см. `.env.example`

## Быстрый старт

```bash
cp .env.example .env
# заполнить .env (хотя бы LLM_BACKEND и параметры выбранного бэкенда)
python3 agent.py
```

Инструменты работают только внутри каталога **`project/`**. Выполнение **bash** по умолчанию выключено; включение: `TOOLS_ALLOW_BASH=1` (небезопасно).

## Конфигурация

Переменные окружения описаны в **`.env.example`**: бэкенд (`ollama` / `vsegpt`), модель, таймауты, опционально путь к **`agent_system_prompt.md`**.

## Структура

| Файл | Назначение |
|------|------------|
| `agent.py` | REPL, цикл tools, вывод в консоль |
| `llm.py` | Ollama / VseGPT, нормализация сообщений под Ollama |
| `tools.py` | Песочница `project/`, регистрация инструментов |
| `agent_system_prompt.md` | Системный промпт по умолчанию |
