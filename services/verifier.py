"""
Second LLM call: verify Vision result and return valid/invalid + updated confidence.
"""
import base64
import json
import logging
from pathlib import Path

from openai import OpenAI

logger = logging.getLogger(__name__)

VERIFIER_SYSTEM = """Ты проверяешь корректность извлечения данных со скриншота.

Тебе дан:
- JSON результата анализа
- изображение

Задача:
- проверить, что данные действительно присутствуют на изображении
- найти ошибки и галлюцинации (выдуманные или неверные поля)

НЕ дублируй анализ — только верифицируй существующий результат.

Вернуть строго JSON (без markdown):
{
  "is_valid": true/false,
  "invalid_fields": ["field_name"],
  "confidence": 0.0-1.0
}

invalid_fields — список полей, которые неверны или выдуманы (например: "input_fields", "window_title", "tables")."""


class VerifierError(Exception):
    """Verification API or parsing error."""


def verify(
    image_path: str | Path,
    vision_result: dict,
    api_key: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.1,
) -> dict:
    """
    Verify vision result against the image. Returns:
    { "valid": bool, "problematic_fields": [...], "confidence": float }
    """
    path = Path(image_path)
    if not path.is_file():
        raise VerifierError(f"Image not found: {image_path}")
    try:
        image_data = path.read_bytes()
    except OSError as e:
        raise VerifierError(f"Cannot read image: {image_path}") from e
    b64 = base64.standard_b64encode(image_data).decode("ascii")
    client = OpenAI(api_key=api_key)
    vision_json = json.dumps(vision_result, ensure_ascii=False, indent=2)
    try:
        response = client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": VERIFIER_SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Previous analysis JSON:\n{vision_json}"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                },
            ],
        )
    except Exception as e:
        logger.exception("Verifier request failed")
        raise VerifierError(f"Verifier API error: {e}") from e
    text = (response.choices[0].message.content or "").strip()
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
        raise VerifierError(f"Verifier returned invalid JSON: {e}") from e
    valid = data.get("is_valid", data.get("valid", True))
    invalid = data.get("invalid_fields", data.get("problematic_fields"))
    if not isinstance(invalid, list):
        invalid = []
    confidence = data.get("confidence")
    if confidence is not None and isinstance(confidence, (int, float)):
        confidence = max(0.0, min(1.0, float(confidence)))
    else:
        confidence = vision_result.get("confidence", 0.0)
    # Apply invalid_fields: null out problematic fields in vision_result (mutate in place)
    _apply_invalid_fields(vision_result, invalid)
    logger.info("Verification: valid=%s, confidence=%s", valid, confidence)
    if invalid:
        logger.info("invalid_fields: %s", invalid)
    return {
        "valid": bool(valid),
        "problematic_fields": invalid,
        "invalid_fields": invalid,
        "confidence": confidence,
    }


def _apply_invalid_fields(vision_result: dict, invalid_fields: list) -> None:
    """Null out fields listed in invalid_fields (mutates vision_result in place)."""
    FIELD_MAP = {
        "application": None,
        "window_title": None,
        "window_type": None,
        "input_fields": [],
        "tables": [],
        "screen_summary": None,
    }
    for name in invalid_fields:
        if isinstance(name, str) and name in FIELD_MAP:
            vision_result[name] = FIELD_MAP[name]
