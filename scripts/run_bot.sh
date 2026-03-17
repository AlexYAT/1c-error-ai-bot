#!/usr/bin/env bash
# Run Telegram bot from project root. Use from systemd or manually.
# Example: ./scripts/run_bot.sh

set -e
cd "$(dirname "$0")/.."
if [ -d ".venv" ]; then
  . .venv/bin/activate
fi
if [ -f .env ]; then
  set -a
  . .env
  set +a
fi
exec python -m telegram_bot.bot
