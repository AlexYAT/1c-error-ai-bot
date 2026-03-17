"""
Unpack zip archive and provide paths to screenshot.png and report.json.
"""
import logging
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

logger = logging.getLogger(__name__)

REPORT_JSON = "report.json"
SCREENSHOT_PNG = "screenshot.png"


class ZipHandlerError(Exception):
    """Raised when zip is invalid or required files are missing."""


def extract_zip(zip_path: str | Path) -> "TemporaryDirectory[str]":
    """
    Extract zip to a temporary directory.
    Returns the TemporaryDirectory object; caller must use it as context manager or cleanup.
    """
    path = Path(zip_path)
    if not path.exists():
        raise ZipHandlerError(f"Archive not found: {zip_path}")
    if not path.suffix.lower() == ".zip":
        raise ZipHandlerError(f"Not a zip file: {zip_path}")
    try:
        with zipfile.ZipFile(path, "r") as zf:
            zf.testzip()
    except zipfile.BadZipFile as e:
        raise ZipHandlerError(f"Invalid zip: {zip_path}") from e
    tmp = TemporaryDirectory(prefix="1c_error_")
    with zipfile.ZipFile(path, "r") as zf:
        zf.extractall(tmp.name)
    logger.info("Extracted %s to %s", path.name, tmp.name)
    return tmp


def get_report_path(extract_root: str | Path) -> Path:
    """Return path to report.json inside extracted root. Raises if missing."""
    p = Path(extract_root) / REPORT_JSON
    if not p.is_file():
        raise ZipHandlerError(f"Missing file in archive: {REPORT_JSON}")
    return p


def get_screenshot_path(extract_root: str | Path) -> Path:
    """Return path to screenshot.png inside extracted root. Raises if missing."""
    p = Path(extract_root) / SCREENSHOT_PNG
    if not p.is_file():
        raise ZipHandlerError(f"Missing file in archive: {SCREENSHOT_PNG}")
    return p
