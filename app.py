#!/usr/bin/env python3
"""
1C Error Analyzer — CLI для анализа ошибок 1С по архивам (zip).

Команды: ingest, list, show, set-status, report.
"""
from dotenv import load_dotenv

load_dotenv()

from cli.commands import app

if __name__ == "__main__":
    app()
