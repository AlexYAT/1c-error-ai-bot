"""
Orchestrates ingest: zip -> report parsing -> vision -> verify -> merge -> similarity -> save.
"""
import json
import logging
from pathlib import Path

from db import repository as repo
from services.json_parser import get_module_name_from_stack, parse_report
from services.similarity import find_similar_errors, is_duplicate_of
from services.verifier import verify
from services.vision_analyzer import analyze_screenshot
from services.zip_handler import (
    ZipHandlerError,
    extract_zip,
    get_report_path,
    get_screenshot_path,
)

logger = logging.getLogger(__name__)


def run_ingest(
    zip_path: str | Path,
    base_name: str,
    api_key: str,
) -> dict:
    """
    Full ingest pipeline. Returns dict with: id, similar_ids, summary.
    """
    zip_path = Path(zip_path)
    archive_name = zip_path.name
    tmp = extract_zip(zip_path)
    try:
        report_path = get_report_path(tmp.name)
        screenshot_path = get_screenshot_path(tmp.name)
        report_data = parse_report(report_path)
        logger.info("Parsed report: config_type=%s", report_data.get("config_type"))

        vision_result = analyze_screenshot(screenshot_path, api_key)
        verify_result = verify(screenshot_path, vision_result, api_key)
        confidence = verify_result.get("confidence")
        if confidence is None:
            confidence = vision_result.get("confidence", 0.0)

        # Merge: report + vision
        application = vision_result.get("application")
        window_title = vision_result.get("window_title")
        window_type = vision_result.get("window_type")
        screen_summary = vision_result.get("screen_summary")
        input_fields_json = json.dumps(vision_result.get("input_fields") or [], ensure_ascii=False)
        tables_json = json.dumps(vision_result.get("tables") or [], ensure_ascii=False)

        error_description = report_data.get("error_description")
        stack_json = report_data.get("stack_json")
        error_code_line = report_data.get("error_code_line")
        module_name = get_module_name_from_stack(report_data.get("stack") or [])

        # Защита от дублей: если уже есть запись с тем же description + module + code_line — не создавать
        candidates = repo.get_errors_for_similarity(exclude_id=None)
        duplicate = next(
            (
                c
                for c in candidates
                if is_duplicate_of(c, base_name, error_description, module_name, error_code_line)
            ),
            None,
        )
        if duplicate:
            existing_id = duplicate["id"]
            logger.info("Похожая ошибка уже существует (ID: %s), новая запись не создана", existing_id)
            return {
                "id": existing_id,
                "similar_ids": [],
                "similar_errors": [],
                "summary": {
                    "base_name": base_name,
                    "archive": archive_name,
                    "config_type": report_data.get("config_type"),
                    "error_description": (error_description or "")[:200],
                    "confidence": confidence,
                    "window_type": window_type,
                },
                "skipped_duplicate": True,
            }

        # Similar errors (before insert so we can exclude_id)
        similar = find_similar_errors(
            error_description=error_description,
            module_name=module_name,
            code_line=error_code_line,
            window_type=window_type,
            window_title=window_title,
            exclude_id=None,
            top_n=3,
        )
        similar_ids = [s["error_id"] for s in similar]

        error_id = repo.insert_error(
            base_name=base_name,
            archive_name=archive_name,
            config_type=report_data.get("config_type"),
            error_description=error_description,
            stack_json=stack_json,
            error_code_line=error_code_line,
            application=application,
            window_title=window_title,
            window_type=window_type,
            screen_summary=screen_summary,
            confidence=confidence,
            input_fields_json=input_fields_json,
            tables_json=tables_json,
        )
        logger.info("Saved error id=%s", error_id)

        return {
            "id": error_id,
            "similar_ids": similar_ids,
            "similar_errors": similar,
            "summary": {
                "base_name": base_name,
                "archive": archive_name,
                "config_type": report_data.get("config_type"),
                "error_description": (error_description or "")[:200],
                "confidence": confidence,
                "window_type": window_type,
            },
        }
    finally:
        tmp.cleanup()


def ingest_with_errors(zip_path: str | Path, base_name: str, api_key: str) -> tuple[dict | None, str | None]:
    """
    Run ingest; on success return (result_dict, None), on failure return (None, error_message).
    """
    try:
        result = run_ingest(zip_path, base_name, api_key)
        return result, None
    except ZipHandlerError as e:
        return None, str(e)
    except Exception as e:  # json_parser, vision, verifier, db
        logger.exception("Ingest failed")
        return None, str(e)
