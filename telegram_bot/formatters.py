"""
Форматирование карточек ошибок и ответов для Telegram.
"""
from typing import Any


def format_ingest_result(result: dict[str, Any], skipped_duplicate: bool = False) -> str:
    """Форматирование результата ingest для отправки в чат."""
    if skipped_duplicate:
        base = None
        try:
            base = (result.get("summary") or {}).get("base_name")
        except Exception:
            base = None
        base_part = f" для базы {base}" if base else " для этой базы"
        return f"Такая ошибка уже зарегистрирована{base_part} (ID: {result['id']}), новая запись не создана."
    lines = [
        "Ошибка добавлена",
        "",
        f"ID: {result['id']}",
        f"База: {result['summary']['base_name']}",
        f"Конфигурация: {result['summary'].get('config_type') or '—'}",
        f"Описание: {(result['summary'].get('error_description') or '—')[:200]}",
        f"Тип окна: {result['summary'].get('window_type') or '—'}",
        f"Confidence: {result['summary'].get('confidence')}",
    ]
    similar = result.get("similar_errors") or []
    if similar:
        lines.append("")
        lines.append("Похожие ошибки:")
        for s in similar:
            lines.append(f"- ID {s['error_id']} | score {s['score']}")
            if s.get("reasons"):
                lines.append("  Причины: " + ", ".join(s["reasons"]))
            if s.get("solution") and (s["solution"].get("comment") or "").strip():
                comment = (s["solution"]["comment"] or "").strip().split("\n")[0][:150]
                lines.append(f"  Решение: {comment}")
    return "\n".join(lines)


def format_error_card(row: dict[str, Any]) -> str:
    """Карточка одной ошибки для /show."""
    parts = [
        f"ID: {row.get('id')}",
        f"База: {row.get('base_name') or '—'}",
        f"Архив: {row.get('archive_name') or '—'}",
        f"Конфигурация: {row.get('config_type') or '—'}",
        f"Описание: {(row.get('error_description') or '—')[:300]}",
        f"Модуль/строка: {row.get('error_code_line') or '—'}",
        f"Тип окна: {row.get('window_type') or '—'}",
        f"Заголовок окна: {row.get('window_title') or '—'}",
        f"Статус: {row.get('status') or '—'}",
    ]
    if row.get("last_resolution_comment"):
        parts.append(f"Комментарий решения: {row['last_resolution_comment'][:200]}")
    inputs = row.get("input_fields_json")
    if inputs and isinstance(inputs, list) and inputs:
        parts.append("Поля формы: " + ", ".join(
            (x.get("name") or "") for x in inputs[:5] if isinstance(x, dict)
        ))
    elif isinstance(inputs, str) and inputs:
        parts.append("Поля формы: (см. данные)")
    return "\n".join(parts)


def format_similar_block(similar: list[dict]) -> str:
    """Блок похожих ошибок под карточкой."""
    if not similar:
        return ""
    lines = ["", "Похожие ошибки:"]
    for s in similar:
        lines.append(f"- ID {s['error_id']} | score {s['score']}")
        if s.get("reasons"):
            lines.append("  Причины: " + ", ".join(s["reasons"]))
        if s.get("solution") and (s["solution"].get("comment") or "").strip():
            lines.append("  Решение: " + (s["solution"]["comment"] or "").strip()[:150])
    return "\n".join(lines)


def format_list_errors(rows: list[dict], limit: int = 20) -> str:
    """Список ошибок для /list."""
    if not rows:
        return "Нет записей."
    lines = []
    trimmed = rows[:limit]
    for r in trimmed:
        desc = (r.get("error_description") or "-")[:50]
        lines.append(f"{r['id']} | {r['created_at'][:10]} | {r['status']} | {r['base_name']} | {desc}...")
    if len(rows) > limit:
        lines.append(f"... Показаны последние {limit} записей")
    return "\n".join(lines)


def format_report(data: dict[str, Any]) -> str:
    """Отчёт за период для /report."""
    lines = [
        f"Период: {data['from']} — {data['to']}",
        f"Всего: {data['total']}",
        "По статусам:",
    ]
    for st, cnt in data.get("by_status", {}).items():
        lines.append(f"  {st}: {cnt}")
    lines.append("По базам:")
    for base, cnt in data.get("by_base", {}).items():
        lines.append(f"  {base}: {cnt}")
    recent = data.get("recent") or []
    if recent:
        lines.append("Последние:")
        for r in recent[:5]:
            lines.append(f"  {r['id']} | {r['created_at'][:10]} | {r['status']} | {r['base_name']}")
    return "\n".join(lines)
