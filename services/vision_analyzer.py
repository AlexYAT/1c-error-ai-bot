"""
OpenAI Vision: analyze screenshot and return structured JSON.
"""
import base64
import json
import logging
from pathlib import Path

from openai import OpenAI

logger = logging.getLogger(__name__)

VISION_SYSTEM = """Ты ассистент по анализу интерфейсов 1С и бизнес-приложений.

Твоя задача: проанализировать скриншот и извлечь ТОЛЬКО явно видимые данные.

КРИТИЧЕСКИЕ ПРАВИЛА:
- НИКОГДА не выдумывай значения
- Если не уверен — возвращай null или пустой список
- Не интерпретируй данные, только извлекай
- Не додумывай скрытые поля

ОПРЕДЕЛЕНИЕ ТИПА ОКНА (window_type):
Используй СТРОГО один из: list_form, document_form, processing_form, report_form, dialog, error_message, other
НЕ использовать "form".

РАЗНИЦА dialog и processing_form:
- dialog: маленькое модальное окно; текст ошибки/подтверждение/вопрос; обычно кнопки OK/Отмена; НЕТ полноценного набора полей.
- processing_form: полноценная форма обработки; несколько полей ввода (3+); кнопка действия ("Выполнить", "Загрузить"); путь к файлу, параметры и т.д.
ПРАВИЛО: если на экране несколько полей ввода (3 и более) и есть кнопка действия — это processing_form, а НЕ dialog.

ПОЛЯ ВВОДА (input_fields):
Извлеки ВСЕ видимые поля формы — даже если значение пустое, даже если поле не заполнено.
Формат: [ {"name": "Название поля", "value": "значение или пустая строка"} ]

ТАБЛИЦЫ (tables):
Если есть таблицы — каждая отдельным объектом, извлечь названия колонок.
Формат: [ {"table_name": null или "название", "columns": [...]} ]
Если не уверен в table_name → null.

ВЫХОД — строго JSON (без markdown, без пояснений):
{
  "application": null,
  "window_title": null,
  "window_type": null,
  "input_fields": [],
  "tables": [],
  "screen_summary": null,
  "confidence": 0.0
}"""

DEFAULT_STRUCTURE = {
    "application": None,
    "window_title": None,
    "window_type": None,
    "input_fields": [],
    "tables": [],
    "screen_summary": None,
    "confidence": 0.0,
}


class VisionAnalyzerError(Exception):
    """Vision API or parsing error."""


def analyze_screenshot(
    image_path: str | Path,
    api_key: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.1,
) -> dict:
    """
    Send screenshot to OpenAI Vision and return structured JSON.
    """
    path = Path(image_path)
    if not path.is_file():
        raise VisionAnalyzerError(f"Image not found: {image_path}")
    try:
        image_data = path.read_bytes()
    except OSError as e:
        raise VisionAnalyzerError(f"Cannot read image: {image_path}") from e
    b64 = base64.standard_b64encode(image_data).decode("ascii")
    client = OpenAI(api_key=api_key)
    try:
        response = client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": VISION_SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                },
            ],
        )
    except Exception as e:
        logger.exception("OpenAI Vision request failed")
        raise VisionAnalyzerError(f"Vision API error: {e}") from e
    text = (response.choices[0].message.content or "").strip()
    # Strip markdown code block if present
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise VisionAnalyzerError(f"Vision returned invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise VisionAnalyzerError("Vision response root must be an object")
    # Normalize to expected keys
    out = dict(DEFAULT_STRUCTURE)
    for key in out:
        if key in data:
            val = data[key]
            if key == "input_fields" and isinstance(val, list):
                out[key] = [
                    {"name": str(x.get("name", "")).strip(), "value": str(x.get("value", "")).strip()}
                    for x in val
                    if isinstance(x, dict)
                ]
                out[key] = _clean_input_fields(out[key])
            elif key == "tables" and isinstance(val, list):
                out[key] = [
                    {
                        "table_name": None if x.get("table_name") in (None, "") else (str(x.get("table_name")).strip() or None),
                        "columns": list(x.get("columns", [])) if isinstance(x.get("columns"), list) else [],
                    }
                    for x in val
                    if isinstance(x, dict)
                ]
            elif key == "window_type" and isinstance(val, str) and val.strip():
                out[key] = _normalize_window_type(val.strip())
            elif key == "confidence" and isinstance(val, (int, float)):
                out[key] = max(0.0, min(1.0, float(val)))
            elif val is not None:
                out[key] = val
    # Post-processing: normalize window_type
    if out.get("window_type") and not _is_valid_window_type(out["window_type"]):
        out["window_type"] = "other"
    # dialog + 3+ input fields → полноценная форма, не диалог
    if out.get("window_type") == "dialog":
        n_fields = len(out.get("input_fields") or [])
        if n_fields >= 3:
            out["window_type"] = "processing_form"
    if out.get("confidence") is not None:
        out["confidence"] = max(0.0, min(1.0, float(out["confidence"])))
    logger.info("Vision analysis confidence: %s", out.get("confidence"))
    return out


VALID_WINDOW_TYPES = frozenset({
    "list_form", "document_form", "processing_form",
    "report_form", "dialog", "error_message", "other",
})


def _is_valid_window_type(s: str) -> bool:
    return s in VALID_WINDOW_TYPES


def _normalize_window_type(s: str) -> str:
    if s in VALID_WINDOW_TYPES:
        return s
    return "other"


def _clean_input_fields(fields: list[dict]) -> list[dict]:
    """Remove duplicates by name, trim, drop empty names."""
    seen: set[str] = set()
    result = []
    for f in fields:
        name = (f.get("name") or "").strip()
        if not name:
            continue
        if name in seen:
            continue
        seen.add(name)
        result.append({"name": name, "value": (f.get("value") or "").strip()})
    return result
