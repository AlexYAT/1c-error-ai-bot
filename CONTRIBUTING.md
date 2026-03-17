# Contributing to 1C Error Analyzer

## Как запустить проект

1. Клонируйте репозиторий и перейдите в каталог проекта.
2. Создайте виртуальное окружение и установите зависимости:

   ```bash
   python -m venv .venv
   source .venv/bin/activate   # Linux/macOS
   # .venv\Scripts\activate   # Windows
   pip install -r requirements.txt
   ```

3. Скопируйте `.env.example` в `.env` и заполните:
   - `OPENAI_API_KEY` — для CLI ingest и Telegram-бота (добавление ошибок).
   - `TELEGRAM_BOT_TOKEN` — только для запуска бота.

4. Запуск CLI:
   ```bash
   python app.py --help
   python app.py list
   ```

5. Запуск Telegram-бота:
   ```bash
   python -m telegram_bot.bot
   ```

## Как вносить изменения

- Не меняйте сигнатуры публичных функций в `services/` и `db/` без необходимости — их используют и CLI, и бот.
- Бизнес-логика остаётся в `services/` и `db/`; слой `telegram_bot/` только вызывает сервисы и форматирует ответы.
- Стиль кода: 4 пробела, строки до ~120 символов, типизация где уместно.

## Как тестировать

- Убедитесь, что `python app.py list` и `python app.py report --from 2026-01-01 --to 2026-12-31` работают без ошибок.
- Для бота: запустите бота локально, проверьте команды `/start`, `/new` (база + zip), `/list`, `/show 1`, `/status 1 resolved`, `/report 2026-01-01 2026-12-31`.
- Тестовый zip должен содержать `screenshot.png` и `report.json` в корне.

## Pull Request

Опишите цель изменений и затронутые компоненты (CLI / бот / оба). Проверьте, что не сломаны существующие команды CLI.
