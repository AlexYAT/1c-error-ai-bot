"""
Telegram command handlers. Бизнес-логика в services/ и db/, здесь только вызовы и форматирование.
"""
import os
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from db import repository as repo
from services.ingest_service import ingest_with_errors
from services.similarity import find_similar_errors
from services.json_parser import get_module_name_from_stack

from telegram_bot.formatters import (
    format_ingest_result,
    format_error_card,
    format_similar_block,
    format_list_errors,
    format_report,
)
from telegram_bot.states import NewErrorState, StatusState, QuickIngestState

logger = logging.getLogger(__name__)

# Максимальная длина сообщения Telegram
MAX_MESSAGE_LENGTH = 4000
# Порог для кросс-базовых подсказок (минимальный score)
CROSS_BASE_SCORE_THRESHOLD = 0.7


def _safe_unlink(path: Path) -> None:
    """Safely delete a file. Never raises; logs on failure."""
    try:
        if path.exists():
            path.unlink()
    except OSError:
        logger.exception("Failed to delete temp file: %s", path)


def _cleanup_user_context(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cleanup conversation-related user_data keys and remove pending zip file if any.
    Works for both /new and quick ingest flows.
    """
    context.user_data.pop("ingest_base", None)
    context.user_data.pop("ingest_zip_path", None)
    pending = context.user_data.pop("pending_zip_path", None)
    if pending:
        try:
            _safe_unlink(Path(pending))
        except Exception:
            # Absolute safety: never break bot due to cleanup
            logger.exception("Unexpected cleanup error for pending_zip_path=%s", pending)


def _normalize_base_name(base_name: str | None) -> str:
    """Нормализация имени базы для сравнения: strip + casefold."""
    if not base_name or not isinstance(base_name, str):
        return ""
    return base_name.strip().casefold()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _get_api_key() -> str:
    key = _env("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY не задан в окружении")
    return key


def _temp_dir() -> Path:
    p = _env("TEMP_DIR", "tmp")
    path = Path(p)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    path.mkdir(parents=True, exist_ok=True)
    return path


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "1C Error Analyzer Bot.\n\n"
        "Команды:\n"
        "/new — добавить ошибку (база + zip)\n"
        "/list [new|in_progress|resolved|ignored] — список ошибок\n"
        "/show <id> — карточка ошибки\n"
        "/status <id> <status> [comment] — сменить статус\n"
        "/report <дата_от> <дата_до> — отчёт\n"
        "/help — справка"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "/new — пошагово: название базы, затем zip-архив с screenshot.png и report.json\n"
        "/list — последние ошибки; /list new — только со статусом new\n"
        "/show 15 — полная карточка ошибки 15 и похожие\n"
        "/status 15 resolved — закрыть с комментарием (бот спросит комментарий)\n"
        "/apply_related 15 — применить статус ошибки 15 к похожим ошибкам в других базах\n"
        "/report 2026-03-01 2026-03-17 — сводка за период"
    )


# --- /new conversation ---


async def new_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("ingest_base", None)
    context.user_data.pop("ingest_zip_path", None)
    await update.message.reply_text("Введите название базы 1С (например: ТЭРС-М):")
    return NewErrorState.ASK_BASE


async def new_receive_base(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    base = (update.message.text or "").strip()
    if not base:
        await update.message.reply_text("Название базы не может быть пустым. Введите снова:")
        return NewErrorState.ASK_BASE
    context.user_data["ingest_base"] = base
    await update.message.reply_text("Отправьте zip-архив ошибки (файл .zip с screenshot.png и report.json):")
    return NewErrorState.ASK_ZIP


async def new_receive_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    if not doc:
        await update.message.reply_text("Отправьте файл (документ).")
        return NewErrorState.ASK_ZIP
    fname = (doc.file_name or "").lower()
    if not fname.endswith(".zip"):
        await update.message.reply_text("Принимаются только файлы .zip. Отправьте архив с расширением .zip")
        return NewErrorState.ASK_ZIP
    base_name = context.user_data.get("ingest_base") or "Unknown"
    await update.message.reply_text("Обрабатываю архив...")
    tmp_dir = _temp_dir()
    # Use unique temp filename to avoid collisions
    unique = doc.file_unique_id or uuid.uuid4().hex
    out_path = tmp_dir / f"upload_{unique}.zip"
    try:
        file = await context.bot.get_file(doc.file_id)
        await file.download_to_drive(out_path)
    except Exception as e:
        logger.exception("Download failed")
        await update.message.reply_text(f"Ошибка загрузки файла: {e}")
        return ConversationHandler.END
    try:
        api_key = _get_api_key()
        result, err = ingest_with_errors(out_path, base_name, api_key)
    except ValueError as e:
        await update.message.reply_text(str(e))
        return ConversationHandler.END
    finally:
        try:
            _safe_unlink(out_path)
        except Exception:
            logger.exception("Unexpected cleanup error (new_receive_document)")
    if err:
        await update.message.reply_text(f"Ошибка: {err}")
        return ConversationHandler.END
    text = format_ingest_result(result, result.get("skipped_duplicate") or False)
    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[:MAX_MESSAGE_LENGTH - 20] + "\n..."
    await update.message.reply_text(text)
    context.user_data.pop("ingest_base", None)
    return ConversationHandler.END


async def _new_ask_zip_again(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отправьте zip-архив (файл .zip) как документ.")
    return NewErrorState.ASK_ZIP


async def new_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _cleanup_user_context(context)
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


# --- zip without /new (quick flow) ---


async def zip_received_without_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Entry point: user sent .zip as document without /new.
    Save file to temp dir, ask for base name, store path in context.user_data["pending_zip_path"].
    """
    doc = update.message.document
    if not doc:
        return ConversationHandler.END
    fname = (doc.file_name or "").lower()
    if not fname.endswith(".zip"):
        await update.message.reply_text("Принимаются только файлы .zip. Отправьте архив с расширением .zip")
        return ConversationHandler.END
    tmp_dir = _temp_dir()
    # Use unique temp filename to avoid collisions
    unique = doc.file_unique_id or uuid.uuid4().hex
    out_path = tmp_dir / f"upload_{unique}.zip"
    try:
        file = await context.bot.get_file(doc.file_id)
        await file.download_to_drive(out_path)
    except Exception as e:
        logger.exception("Download failed")
        await update.message.reply_text(f"Ошибка загрузки файла: {e}")
        return ConversationHandler.END
    context.user_data["pending_zip_path"] = str(out_path)
    logger.info("zip received without /new, waiting for base")
    await update.message.reply_text("Укажи название базы (например: ТЭРС-М)")
    return QuickIngestState.WAITING_FOR_BASE_AFTER_FILE


async def receive_base_after_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    State: WAITING_FOR_BASE_AFTER_FILE.
    Use pending_zip_path + base_name to run ingest, send result, cleanup.
    """
    base_name = (update.message.text or "").strip()
    if not base_name:
        await update.message.reply_text("Название базы не может быть пустым. Введите снова:")
        return QuickIngestState.WAITING_FOR_BASE_AFTER_FILE
    pending = context.user_data.get("pending_zip_path")
    if not pending:
        await update.message.reply_text("Ошибка: архив не найден в контексте. Отправьте zip заново.")
        context.user_data.pop("pending_zip_path", None)
        return ConversationHandler.END
    zip_path = Path(pending)
    await update.message.reply_text("Обрабатываю архив...")
    try:
        api_key = _get_api_key()
        result, err = ingest_with_errors(zip_path, base_name, api_key)
    except ValueError as e:
        await update.message.reply_text(str(e))
        err = None
        result = None
    finally:
        _safe_unlink(zip_path)
        context.user_data.pop("pending_zip_path", None)
    if not result:
        await update.message.reply_text(f"Ошибка: {err or 'неизвестная ошибка'}")
        return ConversationHandler.END
    text = format_ingest_result(result, result.get("skipped_duplicate") or False)
    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[:MAX_MESSAGE_LENGTH - 20] + "\n..."
    await update.message.reply_text(text)
    return ConversationHandler.END


# --- /list ---


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = (context.args or [])
    status = args[0] if args else None
    if status and status not in ("new", "in_progress", "resolved", "ignored"):
        await update.message.reply_text("Статус: new, in_progress, resolved, ignored")
        return
    try:
        rows = repo.list_errors(status=status, limit=20)
    except ValueError as e:
        await update.message.reply_text(str(e))
        return
    text = format_list_errors(rows, limit=20)
    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[:MAX_MESSAGE_LENGTH - 20] + "\n..."
    await update.message.reply_text(text or "Нет записей.")


# --- /show ---


async def cmd_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = (context.args or [])
    if not args:
        await update.message.reply_text("Использование: /show <id>")
        return
    try:
        eid = int(args[0])
    except ValueError:
        await update.message.reply_text("ID должен быть числом.")
        return
    row = repo.get_error_by_id(eid)
    if not row:
        await update.message.reply_text(f"Ошибка с id={eid} не найдена.")
        return
    stack = row.get("stack_json")
    if isinstance(stack, str):
        import json
        try:
            stack = json.loads(stack)
        except Exception:
            stack = []
    similar = find_similar_errors(
        error_description=row.get("error_description"),
        module_name=get_module_name_from_stack(stack),
        code_line=row.get("error_code_line"),
        window_type=row.get("window_type"),
        window_title=row.get("window_title"),
        exclude_id=eid,
        top_n=3,
    )
    text = format_error_card(row) + format_similar_block(similar)
    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[:MAX_MESSAGE_LENGTH - 20] + "\n..."
    await update.message.reply_text(text)


# --- helpers for cross-base suggestions after status change ---


async def _send_cross_base_suggestions(
    update: Update,
    *,
    error_id: int,
    new_status: str,
) -> None:
    """
    Найти похожие ошибки в других базах и отправить подсказку.
    Вызывается только после успешного resolved/ignored.
    """
    if new_status not in ("resolved", "ignored"):
        return
    row = repo.get_error_by_id(error_id)
    if not row:
        return
    cross_base = _get_cross_base_candidates(row, exclude_id=error_id, top_n=5)
    if not cross_base:
        return
    lines = [
        "",
        "Найдены похожие ошибки в других базах:",
    ]
    for sid, other_base, score in cross_base:
        lines.append(f"- ID {sid} | база {other_base} | score {score}")
    lines.append(
        "При необходимости можно применить тот же статус к этим ошибкам отдельно (через /status)."
    )
    text = "\n".join(lines)
    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[:MAX_MESSAGE_LENGTH - 20] + "\n..."
    await update.message.reply_text(text)


def _get_cross_base_candidates(
    source_row: dict[str, Any],
    exclude_id: int,
    top_n: int = 5,
) -> list[tuple[int, str, float]]:
    """
    Общая логика поиска похожих ошибок в других базах:
    - тот же механизм similarity
    - только другие базы
    - только score >= CROSS_BASE_SCORE_THRESHOLD
    - только не resolved/ignored
    Возвращает список (id, base_name, score).
    """
    base_name = source_row.get("base_name")
    stack = source_row.get("stack_json")
    if isinstance(stack, str):
        import json

        try:
            stack = json.loads(stack)
        except Exception:
            stack = []
    similar = find_similar_errors(
        error_description=source_row.get("error_description"),
        module_name=get_module_name_from_stack(stack),
        code_line=source_row.get("error_code_line"),
        window_type=source_row.get("window_type"),
        window_title=source_row.get("window_title"),
        exclude_id=exclude_id,
        top_n=top_n,
    )
    if not similar:
        return []
    cross_base: list[tuple[int, str, float]] = []
    for s in similar:
        sid = s.get("error_id")
        if not sid:
            continue
        score = s.get("score", 0.0)
        if score < CROSS_BASE_SCORE_THRESHOLD:
            continue
        other = repo.get_error_by_id(sid)
        if not other:
            continue
        other_base = other.get("base_name")
        # Сравнение баз через нормализацию (strip + casefold)
        if _normalize_base_name(base_name) and _normalize_base_name(base_name) == _normalize_base_name(other_base):
            continue
        # Исключаем уже закрытые/игнорируемые ошибки
        if (other.get("status") or "").lower() in ("resolved", "ignored"):
            continue
        cross_base.append((sid, other_base or "—", score))
    return cross_base


# --- /status ---


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    args = (context.args or [])
    if len(args) < 2:
        await update.message.reply_text("Использование: /status <id> <new|in_progress|resolved|ignored> [комментарий]")
        return ConversationHandler.END
    try:
        eid = int(args[0])
    except ValueError:
        await update.message.reply_text("ID должен быть числом.")
        return ConversationHandler.END
    new_status = args[1].lower()
    comment = " ".join(args[2:]).strip() if len(args) > 2 else None
    if new_status == "resolved" and not comment:
        context.user_data["status_error_id"] = eid
        context.user_data["status_new_status"] = new_status
        await update.message.reply_text("Введите комментарий решения (одним сообщением):")
        return StatusState.AWAIT_COMMENT
    try:
        ok = repo.set_status(eid, new_status, comment=comment)
    except ValueError as e:
        await update.message.reply_text(str(e))
        return ConversationHandler.END
    if not ok:
        await update.message.reply_text(f"Ошибка с id={eid} не найдена.")
        return ConversationHandler.END
    await update.message.reply_text(f"Статус ошибки {eid} обновлён на: {new_status}")
    # Подсказка по похожим ошибкам в других базах
    await _send_cross_base_suggestions(update, error_id=eid, new_status=new_status)
    return ConversationHandler.END


async def status_receive_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    eid = context.user_data.pop("status_error_id", None)
    new_status = context.user_data.pop("status_new_status", "resolved")
    comment = (update.message.text or "").strip()
    if eid is None:
        await update.message.reply_text("Сессия сброшена. Используйте /status <id> resolved снова.")
        return ConversationHandler.END
    try:
        ok = repo.set_status(eid, new_status, comment=comment or None)
    except ValueError as e:
        await update.message.reply_text(str(e))
        return ConversationHandler.END
    if not ok:
        await update.message.reply_text(f"Ошибка с id={eid} не найдена.")
        return ConversationHandler.END
    await update.message.reply_text(
        f"Статус ошибки {eid} обновлён на: {new_status}"
        + (", комментарий сохранён." if comment else "")
    )
    # Подсказка по похожим ошибкам в других базах
    await _send_cross_base_suggestions(update, error_id=eid, new_status=new_status)
    return ConversationHandler.END


# --- /apply_related ---


async def cmd_apply_related(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Массовое применение статуса исходной ошибки к похожим ошибкам в других базах.
    Использование: /apply_related <source_id>
    """
    args = (context.args or [])
    if len(args) < 1:
        await update.message.reply_text("Использование: /apply_related <source_id>")
        return
    try:
        source_id = int(args[0])
    except ValueError:
        await update.message.reply_text("ID должен быть числом.")
        return
    source = repo.get_error_by_id(source_id)
    if not source:
        await update.message.reply_text(f"Ошибка с id={source_id} не найдена.")
        return
    status = (source.get("status") or "").lower()
    if status not in ("resolved", "ignored"):
        await update.message.reply_text(
            "Команда доступна только для ошибок со статусом resolved или ignored."
        )
        return
    # Найти похожие в других базах с теми же фильтрами, что в подсказке
    candidates = _get_cross_base_candidates(source, exclude_id=source_id, top_n=10)
    if not candidates:
        await update.message.reply_text(
            "Подходящие похожие ошибки в других базах не найдены."
        )
        return
    # Комментарий-решение для resolved (если есть)
    comment_to_apply: str | None = None
    if status == "resolved":
        solution = repo.get_solution_for_error(source_id)
        if solution and (solution.get("comment") or "").strip():
            comment_to_apply = solution["comment"]
        elif source.get("last_resolution_comment"):
            comment_to_apply = source["last_resolution_comment"]
    updated: list[tuple[int, str]] = []
    skipped = 0
    errors = 0
    for cid, other_base, _score in candidates:
        try:
            ok = repo.set_status(
                cid,
                status,
                comment=comment_to_apply if status == "resolved" else None,
            )
        except ValueError:
            errors += 1
            continue
        if ok:
            updated.append((cid, other_base))
        else:
            skipped += 1
    lines = [
        "Применение завершено.",
        f"Источник: ID {source_id} | статус {status}",
        f"Обновлено: {len(updated)}",
        f"Пропущено: {skipped}",
        f"Ошибки: {errors}",
    ]
    if updated:
        lines.append("")
        lines.append("Обновленные записи:")
        for cid, other_base in updated:
            lines.append(f"- ID {cid} | база {other_base}")
    text = "\n".join(lines)
    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[:MAX_MESSAGE_LENGTH - 20] + "\n..."
    await update.message.reply_text(text)


async def status_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("status_error_id", None)
    context.user_data.pop("status_new_status", None)
    # Also cleanup potential pending zip from quick flow
    _cleanup_user_context(context)
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


# --- /report ---


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = (context.args or [])
    if len(args) < 2:
        await update.message.reply_text("Использование: /report <дата_от> <дата_до>, например: /report 2026-03-01 2026-03-17")
        return
    date_from, date_to = args[0], args[1]
    data = repo.report_by_date(date_from, date_to)
    text = format_report(data)
    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[:MAX_MESSAGE_LENGTH - 20] + "\n..."
    await update.message.reply_text(text)


def build_conversation_handlers() -> list:
    """Собрать ConversationHandler для /new и для /status (ожидание комментария)."""
    from telegram_bot.states import NewErrorState, StatusState, QuickIngestState

    new_conv = ConversationHandler(
        entry_points=[CommandHandler("new", new_start)],
        states={
            NewErrorState.ASK_BASE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_receive_base),
            ],
            NewErrorState.ASK_ZIP: [
                MessageHandler(filters.Document.ALL, new_receive_document),
                MessageHandler(filters.TEXT & ~filters.COMMAND, _new_ask_zip_again),
            ],
        },
        fallbacks=[CommandHandler("cancel", new_cancel)],
    )

    quick_ingest_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Document.ALL, zip_received_without_new)],
        states={
            QuickIngestState.WAITING_FOR_BASE_AFTER_FILE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_base_after_file),
            ],
        },
        fallbacks=[CommandHandler("cancel", new_cancel)],
    )

    status_conv = ConversationHandler(
        entry_points=[CommandHandler("status", cmd_status)],
        states={
            StatusState.AWAIT_COMMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, status_receive_comment),
            ],
        },
        fallbacks=[CommandHandler("cancel", status_cancel)],
    )

    return [new_conv, quick_ingest_conv, status_conv]
