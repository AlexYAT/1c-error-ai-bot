# 1C Error Analyzer

CLI и Telegram-бот для анализа ошибок 1С на основе архивов (zip) со скриншотом и отчётом. Ускоряет диагностику и накапливает базу знаний по типовым сбоям.

---

## 1. Что это за проект

**1C Error Analyzer** — это инструмент для разработчиков 1С, который:

- Принимает zip-архив с ошибкой (`screenshot.png` + `report.json`).
- Парсит отчёт (тип конфигурации, описание, стек, строка кода).
- Анализирует скриншот через OpenAI Vision и верифицирует результат.
- Сохраняет карточку в SQLite, ищет похожие ошибки и подтягивает решения.
- Позволяет вести статусы (new → in_progress → resolved/ignored) и строить отчёты.

Доступны два интерфейса: **CLI** (локально или на сервере) и **Telegram-бот** (удобно с телефона и для команды).

---

## 2. Для чего нужен

- **Типовые сбои**: многие ошибки повторяются; база накопленных разборов сокращает время диагностики.
- **Скриншот + отчёт**: Vision извлекает поля и таблицы с экрана, отчёт даёт стек и строку кода.
- **Единое хранилище**: все случаи в одной SQLite-базе с статусами и комментариями решений.
- **Telegram**: добавление ошибок и просмотр через бота без доступа к CLI.

---

## 3. Архитектура

```
Project/
├── app.py                    # Точка входа CLI
├── cli/
│   └── commands.py           # Команды: ingest, list, show, set-status, report
├── telegram_bot/
│   ├── bot.py                # Запуск Telegram Application
│   ├── handlers.py           # Команды и сценарии (/new, /list, /show, /status, /report)
│   ├── formatters.py         # Форматирование карточек и ответов для Telegram
│   └── states.py             # Состояния ConversationHandler
├── services/
│   ├── zip_handler.py        # Распаковка zip, пути к screenshot/report
│   ├── json_parser.py        # Парсинг report.json
│   ├── vision_analyzer.py    # OpenAI Vision
│   ├── verifier.py           # Верификация результата Vision
│   ├── similarity.py         # Поиск похожих ошибок (score, reasons, solution)
│   └── ingest_service.py     # Оркестрация ingest-пайплайна
├── db/
│   ├── models.py             # Схема SQLite (errors, error_solutions)
│   └── repository.py         # Работа с БД
├── utils/
│   └── logging_config.py    # Логирование
├── deploy/
│   └── onec-error-bot.service  # systemd unit для VPS
└── scripts/
    └── run_bot.sh            # Скрипт запуска бота
```

- **Ядро**: бизнес-логика в `services/` и `db/`. CLI и бот только вызывают сервисы и форматируют вывод.
- **БД**: SQLite, путь задаётся через `DB_PATH` в `.env` или по умолчанию `errors.db` в корне проекта.

---

## 4. Локальный запуск CLI

Требуется **Python 3.11+**.

```bash
cd /path/to/project
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate   # Windows
pip install -r requirements.txt
cp .env.example .env
# Отредактируйте .env: OPENAI_API_KEY=...
```

Команды:

```bash
python app.py --help
python app.py ingest --base "ИмяБазы" --zip "/path/to/file.zip"
python app.py list
python app.py list --status new
python app.py show --id 1
python app.py set-status --id 1 --status resolved --comment "Решение"
python app.py report --from 2026-03-01 --to 2026-03-17
```

---

## 5. Локальный запуск Telegram-бота

В `.env` добавьте:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_from_BotFather
OPENAI_API_KEY=your_openai_key
```

Запуск:

```bash
python -m telegram_bot.bot
```

Или через скрипт (из корня проекта):

```bash
chmod +x scripts/run_bot.sh
./scripts/run_bot.sh
```

Команды бота:

- `/start`, `/help` — справка
- `/new` — добавить ошибку (бот запросит название базы, затем zip-файл)
- `/list [new|in_progress|resolved|ignored]` — список ошибок
- `/show <id>` — карточка ошибки и похожие
- `/status <id> <status> [comment]` — смена статуса; для `resolved` бот может запросить комментарий отдельным сообщением
- `/report <дата_от> <дата_до>` — отчёт за период
- `/cancel` — отмена текущего диалога (/new или ожидание комментария к /status)

---

## 6. Переменные окружения

Пример `.env` (см. `.env.example`):

| Переменная | Описание |
|------------|----------|
| `OPENAI_API_KEY` | Ключ OpenAI (нужен для ingest и бота при добавлении ошибки) |
| `TELEGRAM_BOT_TOKEN` | Токен бота от @BotFather (для `telegram_bot`) |
| `DB_PATH` | Путь к SQLite (по умолчанию `errors.db` в корне) |
| `LOG_LEVEL` | Уровень логов: DEBUG, INFO, WARNING, ERROR |
| `TEMP_DIR` | Каталог для временных файлов (загрузки zip в боте) |

---

## 7. Примеры команд

**CLI**

```bash
python app.py ingest --base "ТЭРС-М" --zip "Ошибка_123.zip"
python app.py list --status resolved
python app.py show --id 5
python app.py report --from 2026-03-01 --to 2026-03-17
```

**Telegram**

- Отправить `/new` → ввести базу → отправить .zip как документ.
- `/show 5` — карточка ошибки 5 и похожие с причинами и решением.
- `/status 5 resolved` — бот попросит ввести комментарий решения.
- `/apply_related 5` — применить статус ошибки 5 ко всем похожим ошибкам в других базах.

---

## 8. Запуск на VPS

1. Клонируйте репозиторий на сервер.
2. Создайте venv и установите зависимости:
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
3. Создайте `.env` в корне проекта (скопируйте с `.env.example` и заполните `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY` и при необходимости `DB_PATH`, `LOG_LEVEL`).
4. Скопируйте и отредактируйте systemd-unit:
   ```bash
   sudo cp deploy/onec-error-bot.service /etc/systemd/system/
   sudo nano /etc/systemd/system/onec-error-bot.service
   ```
   Замените `YOUR_USER`, пути `WorkingDirectory`, `EnvironmentFile`, `Environment=PATH`, `ExecStart` на фактические (например, `/home/bot/1c-error-analyzer`).
5. Запуск:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable onec-error-bot
   sudo systemctl start onec-error-bot
   journalctl -u onec-error-bot -f
   ```

Альтернатива без systemd: в cron или вручную запускать `./scripts/run_bot.sh` (через `nohup` или screen/tmux).

---

## 9. Ограничения MVP

- Поиск похожих — по score (описание, module_name, строка кода, window_type, window_title), без embeddings.
- Один скриншот и один report.json в архиве; принимаются только .zip.
- Нет веб-интерфейса, нет RBAC в боте (все пользователи с доступом к боту видят одни и те же данные).
- SQLite — подходит для одной команды и умеренной нагрузки.

---

## 10. Roadmap

- Ограничение доступа к боту (whitelist chat_id или простой пароль).
- Поддержка логов 1С и привязка к ошибкам.
- Векторный поиск похожих (embeddings).
- Опциональный Docker-образ для деплоя.

---

## Лицензия

MIT. См. [LICENSE](LICENSE).
