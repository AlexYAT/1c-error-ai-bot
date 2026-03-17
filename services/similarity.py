"""
Find similar errors by scored matching (description, module_name, code_line, window_type, window_title).
Returns top N with reasons and solution. No embeddings, no extra deps.
"""
import json
import logging
import hashlib
from typing import Any

from db import repository as repo

logger = logging.getLogger(__name__)

# Веса факторов
WEIGHT_DESCRIPTION = 0.5
WEIGHT_MODULE = 0.2
WEIGHT_CODE_LINE = 0.2
WEIGHT_WINDOW_TYPE = 0.05
WEIGHT_WINDOW_TITLE = 0.05
# Минимальный порог: ниже — не включать в результат
SCORE_THRESHOLD = 0.35
# Бонус за решённую ошибку с комментарием
RESOLVED_BONUS = 0.1


def _normalize(s: str | None) -> str:
    """Нормализация строки для сравнения: lower, только буквы/цифры/пробелы, сжатие пробелов."""
    if not s or not isinstance(s, str):
        return ""
    out = []
    for c in s.lower():
        if c.isalnum() or c.isspace():
            out.append(c)
    return " ".join("".join(out).split())


def _normalize_base_name(base_name: str | None) -> str:
    """Нормализация имени базы для дедупликации: strip + casefold."""
    if not base_name or not isinstance(base_name, str):
        return ""
    return base_name.strip().casefold()


def _extract_module_from_stack(stack_json: str | None) -> str | None:
    """Извлечь первый module_name из stack_json."""
    if not stack_json:
        return None
    try:
        stack = json.loads(stack_json) if isinstance(stack_json, str) else stack_json
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(stack, list) or not stack:
        return None
    first = stack[0]
    if isinstance(first, dict) and first.get("module_name"):
        return (first["module_name"] or "").strip() or None
    return None


def _partial_match(a: str, b: str, min_len: int = 4) -> bool:
    """Есть ли пересечение: одна строка содержит существенную подстроку другой или совпадение по словам."""
    an, bn = _normalize(a), _normalize(b)
    if not an or not bn:
        return False
    if len(an) < min_len or len(bn) < min_len:
        return an == bn
    if an in bn or bn in an:
        return True
    aw, bw = set(an.split()), set(bn.split())
    common = aw & bw
    return len(common) >= min(2, len(aw), len(bw))


def _score_one(
    candidate: dict[str, Any],
    error_description: str | None,
    module_name: str | None,
    code_line: str | None,
    window_type: str | None,
    window_title: str | None,
) -> tuple[float, list[str]]:
    """Вернуть (score, reasons) для одного кандидата."""
    score = 0.0
    reasons: list[str] = []

    cand_desc = (candidate.get("error_description") or "").strip()
    cand_module = _extract_module_from_stack(candidate.get("stack_json"))
    cand_code = (candidate.get("error_code_line") or "").strip()
    cand_wtype = (candidate.get("window_type") or "").strip()
    cand_wtitle = (candidate.get("window_title") or "").strip()

    if error_description and cand_desc and _partial_match(error_description, cand_desc):
        score += WEIGHT_DESCRIPTION
        reasons.append("похожее описание ошибки")

    if module_name and cand_module:
        if _normalize(module_name) == _normalize(cand_module):
            score += WEIGHT_MODULE
            reasons.append("совпадает module_name")
        elif _partial_match(module_name, cand_module, min_len=3):
            score += WEIGHT_MODULE * 0.5
            reasons.append("похожий module_name")

    if code_line and cand_code and _partial_match(code_line, cand_code):
        score += WEIGHT_CODE_LINE
        reasons.append("похожая строка кода")

    if window_type and cand_wtype and _normalize(window_type) == _normalize(cand_wtype):
        score += WEIGHT_WINDOW_TYPE
        reasons.append("одинаковый window_type")

    if window_title and cand_wtitle and _partial_match(window_title, cand_wtitle, min_len=2):
        score += WEIGHT_WINDOW_TITLE
        reasons.append("похожий window_title")

    return (round(score, 2), reasons)


def is_duplicate_of(
    candidate: dict[str, Any],
    base_name: str | None,
    error_description: str | None,
    module_name: str | None,
    error_code_line: str | None,
) -> bool:
    """
    Дубль = одинаковая ошибка в пределах одной и той же базы.
    Проверка выполняется по комбинации:
    - base_name (строго)
    - fingerprint (нормализованные: description + module_name + code_line)
    С fallback на partial_match (на случай мелких отличий текста).
    """
    cand_base = candidate.get("base_name")
    if not _normalize_base_name(base_name) or _normalize_base_name(base_name) != _normalize_base_name(cand_base):
        return False
    cand_desc = (candidate.get("error_description") or "").strip()
    cand_module = _extract_module_from_stack(candidate.get("stack_json"))
    cand_code = (candidate.get("error_code_line") or "").strip()
    fp_new = _fingerprint(error_description, module_name, error_code_line)
    fp_cand = _fingerprint(cand_desc, cand_module, cand_code)
    if fp_new and fp_cand and fp_new == fp_cand:
        return True
    if not error_description or not cand_desc or not _partial_match(error_description, cand_desc):
        return False
    if not module_name or not cand_module or _normalize(module_name) != _normalize(cand_module):
        return False
    if not error_code_line or not cand_code or not _partial_match(error_code_line, cand_code):
        return False
    return True


def _fingerprint(error_description: str | None, module_name: str | None, code_line: str | None) -> str | None:
    """Stable fingerprint for duplicate detection (no DB changes)."""
    d = _normalize(error_description)
    m = _normalize(module_name)
    c = _normalize(code_line)
    if not d or not m or not c:
        return None
    raw = f"{d}|{m}|{c}".encode("utf-8", errors="ignore")
    return hashlib.sha1(raw).hexdigest()


def find_similar_errors(
    error_description: str | None,
    module_name: str | None,
    code_line: str | None,
    window_type: str | None = None,
    window_title: str | None = None,
    exclude_id: int | None = None,
    top_n: int = 3,
) -> list[dict[str, Any]]:
    """
    Вернуть top N похожих ошибок с score, reasons и solution.
    Каждый элемент: { "error_id", "score", "reasons", "solution" } (+ поля ошибки при необходимости).
    """
    candidates = repo.get_errors_for_similarity(exclude_id=exclude_id)
    if not candidates:
        return []

    scored: list[tuple[float, list[str], dict]] = []
    for c in candidates:
        s, reasons = _score_one(
            c,
            error_description=error_description,
            module_name=module_name,
            code_line=code_line,
            window_type=window_type,
            window_title=window_title,
        )
        if s >= SCORE_THRESHOLD:
            scored.append((s, reasons, c))

    # Бонус за resolved с комментарием; привязать solution и created_at для сортировки
    with_solution: list[tuple[bool, float, str, list[str], dict, Any]] = []
    for s, reasons, cand in scored:
        solution = repo.get_solution_for_error(cand["id"])
        has_sol = bool(solution and (solution.get("comment") or "").strip())
        if has_sol:
            s = min(1.0, s + RESOLVED_BONUS)
        created_at = cand.get("created_at") or ""
        with_solution.append((has_sol, round(s, 2), created_at, reasons, cand, solution))

    # Сортировка: сначала с решением, затем по score, затем по дате (новые выше)
    with_solution.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    top = with_solution[:top_n]

    result = []
    for _has, score, _created, reasons, cand, solution in top:
        result.append({
            "error_id": cand["id"],
            "score": score,
            "reasons": reasons,
            "solution": solution,
        })
    return result
