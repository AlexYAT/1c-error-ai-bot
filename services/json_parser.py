"""
Parse report.json from 1C error archive.
Real format: configInfo, errorInfo.applicationErrorInfo.errors, errorInfo.applicationErrorInfo.stack
"""
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class JsonParserError(Exception):
    """Raised when report.json is invalid or missing required structure."""


def _extract_config_type(data: dict[str, Any]) -> str | None:
    """Extract config type from configInfo.name or configInfo.description."""
    config_info = data.get("configInfo")
    if not isinstance(config_info, dict):
        logger.debug("configInfo not found or not a dict")
        return None
    name = config_info.get("name")
    if name is not None and isinstance(name, str) and name.strip():
        logger.debug("config_type from configInfo.name: %s", name[:60])
        return name.strip()
    desc = config_info.get("description")
    if desc is not None and isinstance(desc, str) and desc.strip():
        logger.debug("config_type from configInfo.description: %s", desc[:60])
        return desc.strip()
    return None


def _extract_error_description(data: dict[str, Any]) -> str | None:
    """Extract error text from errorInfo.applicationErrorInfo.errors[0][0]."""
    error_info = data.get("errorInfo")
    if not isinstance(error_info, dict):
        logger.debug("errorInfo not found or not a dict")
        return None
    app_err = error_info.get("applicationErrorInfo")
    if not isinstance(app_err, dict):
        logger.debug("applicationErrorInfo not found or not a dict")
        return None
    errors = app_err.get("errors")
    if not isinstance(errors, list) or len(errors) == 0:
        logger.debug("errors empty or not a list")
        return None
    first_record = errors[0]
    if not isinstance(first_record, (list, tuple)) or len(first_record) == 0:
        logger.debug("first errors record invalid format")
        return None
    text = first_record[0]
    if text is None:
        return None
    s = str(text).strip() if text else None
    if s:
        logger.debug("error_description extracted, len=%d", len(s))
    return s if s else None


def _normalize_stack_frame(frame: Any) -> dict[str, Any] | None:
    """Convert raw stack frame [module_name, line_number, code_line] to normalized dict."""
    if not isinstance(frame, (list, tuple)) or len(frame) < 2:
        return None
    module_name = frame[0]
    line_number = frame[1] if len(frame) > 1 else None
    code_line = frame[2] if len(frame) > 2 else None
    if module_name is not None:
        module_name = str(module_name).strip() or None
    if line_number is not None and not isinstance(line_number, (int, float)):
        line_number = None
    elif line_number is not None:
        line_number = int(line_number)
    if code_line is not None:
        code_line = str(code_line).strip() or None
    return {
        "module_name": module_name,
        "procedure_name": None,
        "line_number": line_number,
        "code_line": code_line,
    }


def _extract_stack(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract and normalize stack from errorInfo.applicationErrorInfo.stack."""
    error_info = data.get("errorInfo")
    if not isinstance(error_info, dict):
        return []
    app_err = error_info.get("applicationErrorInfo")
    if not isinstance(app_err, dict):
        return []
    stack_raw = app_err.get("stack")
    if not isinstance(stack_raw, list):
        logger.debug("stack not a list")
        return []
    result = []
    for frame in stack_raw:
        normalized = _normalize_stack_frame(frame)
        if normalized:
            result.append(normalized)
    logger.debug("stack frames normalized: %d", len(result))
    return result


def parse_report(report_path: str | Path) -> dict[str, Any]:
    """
    Read and parse report.json (1C real format).
    Extracts: config_type, error_description, stack (normalized list of dicts), error_code_line.
    """
    path = Path(report_path)
    if not path.exists():
        raise JsonParserError(f"File not found: {report_path}")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise JsonParserError(f"Cannot read {report_path}") from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise JsonParserError(f"Invalid JSON in {report_path}") from e
    if not isinstance(data, dict):
        raise JsonParserError("report.json root must be an object")

    top_keys = list(data.keys())
    logger.info("report.json top-level keys: %s", top_keys)
    logger.debug("configInfo present: %s", "configInfo" in data)
    logger.debug("errorInfo present: %s", "errorInfo" in data)
    app_err = None
    if isinstance(data.get("errorInfo"), dict):
        app_err = data["errorInfo"].get("applicationErrorInfo")
    logger.debug("applicationErrorInfo present: %s", isinstance(app_err, dict))

    config_type = _extract_config_type(data)
    error_description = _extract_error_description(data)
    stack = _extract_stack(data)

    errors_raw = []
    if isinstance(app_err, dict) and isinstance(app_err.get("errors"), list):
        errors_raw = app_err["errors"]
    logger.info("errors count: %d, stack frames: %d", len(errors_raw), len(stack))
    logger.info("config_type extracted: %s", config_type or "(none)")
    logger.info("error_description extracted: %s", bool(error_description))

    # error_code_line = строка из последнего кадра стека (корневая причина), не из первого
    error_code_line = None
    if stack:
        last_frame = stack[-1]
        if last_frame.get("code_line"):
            error_code_line = (last_frame["code_line"] or "").strip()
    if not error_code_line:
        error_code_line = None

    return {
        "config_type": config_type,
        "error_description": error_description,
        "stack": stack,
        "stack_json": json.dumps(stack, ensure_ascii=False),
        "error_code_line": error_code_line,
    }


def get_module_name_from_stack(stack_list: list[Any]) -> str | None:
    """Extract first module name from normalized stack (e.g. for similarity)."""
    if not stack_list:
        return None
    first = stack_list[0]
    if isinstance(first, dict) and first.get("module_name"):
        return str(first["module_name"]).strip() or None
    # Fallback for legacy format
    if isinstance(first, dict):
        name = first.get("module") or first.get("name")
        if name and isinstance(name, str):
            return name.strip()
    if isinstance(first, str) and first.strip():
        return first.strip()[:200]
    return None


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    report_path = sys.argv[1] if len(sys.argv) > 1 else "report.json"
    path = Path(report_path)
    if not path.exists():
        print(f"File not found: {report_path}", file=sys.stderr)
        sys.exit(1)
    try:
        result = parse_report(path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except JsonParserError as e:
        print(f"Parse error: {e}", file=sys.stderr)
        sys.exit(1)
