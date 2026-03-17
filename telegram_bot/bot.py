"""
Точка входа Telegram-бота. Запуск: python -m telegram_bot.bot
"""
import os
import sys
import logging
from pathlib import Path

# Корень проекта в path для импорта db, services
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root / ".env")

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from telegram_bot.handlers import (
    cmd_start,
    cmd_help,
    cmd_list,
    cmd_show,
    cmd_report,
    cmd_apply_related,
    build_conversation_handlers,
)


def setup_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    # Prevent leaking bot token via verbose HTTP logs (URLs contain /bot<TOKEN>/...)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)


def main() -> None:
    setup_logging()
    logger = logging.getLogger(__name__)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN не задан. Укажите в .env")
        sys.exit(1)
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    for conv in build_conversation_handlers():
        app.add_handler(conv)
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("show", cmd_show))
    app.add_handler(CommandHandler("apply_related", cmd_apply_related))
    app.add_handler(CommandHandler("report", cmd_report))
    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
