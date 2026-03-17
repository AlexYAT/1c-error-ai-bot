"""
CLI command handlers: ingest, list, show, set_status, report.
"""
import json
import sys
from pathlib import Path

import typer

from db import repository as repo
from services.ingest_service import ingest_with_errors
from services.json_parser import get_module_name_from_stack
from services.similarity import find_similar_errors
from utils.logging_config import setup_logging

app = typer.Typer(help="1C Error Analyzer — анализ ошибок 1С по архивам zip")


def _get_api_key() -> str:
    import os
    from dotenv import load_dotenv
    load_dotenv()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        typer.echo("Ошибка: OPENAI_API_KEY не задан. Добавьте в .env или экспортируйте переменную.", err=True)
        raise typer.Exit(1)
    return key


@app.command("ingest")
def cmd_ingest(
    base: str = typer.Option(..., "--base", "-b", help="Имя базы 1С"),
    zip_path: str = typer.Option(..., "--zip", "-z", help="Путь к архиву .zip"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Добавить новую ошибку из архива (screenshot.png + report.json)."""
    setup_logging(verbose)
    path = Path(zip_path)
    if not path.exists():
        typer.echo(f"Файл не найден: {zip_path}", err=True)
        raise typer.Exit(1)
    api_key = _get_api_key()
    result, err = ingest_with_errors(path, base, api_key)
    if err:
        typer.echo(f"Ошибка: {err}", err=True)
        raise typer.Exit(1)
    if result.get("skipped_duplicate"):
        base_name = (result.get("summary") or {}).get("base_name") or base
        typer.echo(
            f"Такая ошибка уже зарегистрирована для базы {base_name} (ID: {result['id']}), новая запись не создана."
        )
        return
    typer.echo("Ошибка добавлена.")
    typer.echo(f"  ID: {result['id']}")
    typer.echo(f"  База: {result['summary']['base_name']}")
    typer.echo(f"  Архив: {result['summary']['archive']}")
    desc = result["summary"]["error_description"] or ""
    typer.echo(f"  Описание: {desc[:80]}{'...' if len(desc) > 80 else ''}")
    typer.echo(f"  Confidence: {result['summary']['confidence']}")
    _echo_similar_errors(result.get("similar_errors") or [])


@app.command("list")
def cmd_list(
    status: str | None = typer.Option(None, "--status", "-s", help="Фильтр по статусу: new, in_progress, resolved, ignored"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Список ошибок."""
    setup_logging(verbose)
    try:
        rows = repo.list_errors(status=status)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    if not rows:
        typer.echo("Нет записей.")
        return
    for r in rows:
        desc = (r.get("error_description") or "-")[:60]
        typer.echo(f"  {r['id']}  {r['created_at'][:10]}  {r['status']:12}  {r['base_name']:20}  {desc}...")


def _echo_similar_errors(similar_errors: list) -> None:
    """Вывести блок «Похожие ошибки» с score, reasons, solution."""
    if not similar_errors:
        return
    typer.echo("Похожие ошибки:")
    for s in similar_errors:
        eid = s.get("error_id")
        score = s.get("score", 0)
        reasons = s.get("reasons") or []
        solution = s.get("solution")
        typer.echo(f"  - ID: {eid} (score: {score})")
        if reasons:
            typer.echo("    Причины:")
            for r in reasons:
                typer.echo(f"      - {r}")
        if solution:
            comment = (solution.get("comment") or "").strip()
            if comment:
                typer.echo("    Решение:")
                for line in comment.split("\n")[:3]:
                    typer.echo(f"      {line.strip()}")
        else:
            typer.echo("    Решение: —")


@app.command("show")
def cmd_show(
    id: int = typer.Option(..., "--id", "-i", help="ID ошибки"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Показать одну ошибку по ID."""
    setup_logging(verbose)
    row = repo.get_error_by_id(id)
    if not row:
        typer.echo(f"Ошибка с id={id} не найдена.", err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps({k: v for k, v in row.items()}, ensure_ascii=False, indent=2))
    stack = row.get("stack_json")
    if isinstance(stack, str):
        try:
            stack = json.loads(stack)
        except Exception:
            stack = []
    elif not stack:
        stack = []
    similar = find_similar_errors(
        error_description=row.get("error_description"),
        module_name=get_module_name_from_stack(stack),
        code_line=row.get("error_code_line"),
        window_type=row.get("window_type"),
        window_title=row.get("window_title"),
        exclude_id=id,
        top_n=3,
    )
    if similar:
        typer.echo("")
        _echo_similar_errors(similar)


@app.command("set-status")
def cmd_set_status(
    id: int = typer.Option(..., "--id", "-i"),
    status: str = typer.Option(..., "--status", "-s", help="new | in_progress | resolved | ignored"),
    comment: str | None = typer.Option(None, "--comment", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Изменить статус ошибки (и опционально комментарий решения)."""
    setup_logging(verbose)
    try:
        ok = repo.set_status(id, status, comment=comment)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    if not ok:
        typer.echo(f"Ошибка с id={id} не найдена.", err=True)
        raise typer.Exit(1)
    typer.echo(f"Статус ошибки {id} обновлён на: {status}")


@app.command("report")
def cmd_report(
    from_date: str = typer.Option(..., "--from", "-f", help="Начало периода, например 2026-03-01"),
    to_date: str = typer.Option(..., "--to", "-t", help="Конец периода, например 2026-03-17"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Отчёт за период: по статусам, по базам, последние записи."""
    setup_logging(verbose)
    data = repo.report_by_date(from_date, to_date)
    typer.echo(f"Период: {data['from']} — {data['to']}")
    typer.echo(f"Всего: {data['total']}")
    typer.echo("По статусам:")
    for st, cnt in data["by_status"].items():
        typer.echo(f"  {st}: {cnt}")
    typer.echo("По базам:")
    for base, cnt in data["by_base"].items():
        typer.echo(f"  {base}: {cnt}")
    if data["recent"]:
        typer.echo("Последние записи:")
        for r in data["recent"][:10]:
            typer.echo(f"  {r['id']}  {r['created_at'][:10]}  {r['status']}  {r['base_name']}")
