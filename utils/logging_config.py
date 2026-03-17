"""
Configure logging for CLI and bot.
"""
import logging
import os
import sys


def setup_logging(verbose: bool = False) -> None:
    level_name = os.environ.get("LOG_LEVEL", "").strip().upper() if not verbose else ""
    if verbose:
        level = logging.DEBUG
    elif level_name and hasattr(logging, level_name):
        level = getattr(logging, level_name)
    else:
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
