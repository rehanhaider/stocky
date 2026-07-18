from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from stocky import __version__
from stocky.config import (
    DEFAULT_BHAVCOPY_DIR,
    DEFAULT_DB_PATH,
    DEFAULT_YAHOO_JSON_CACHE_DIR,
    DEFAULT_ZERODHA_INSTRUMENTS,
)
from stocky.database import (
    DatabaseStatus,
    SearchResult,
    import_yahoo_json_cache,
    read_status,
    search_instruments,
)
from stocky.pipeline import RebuildResult, rebuild_database
from stocky.sources import resolve_bhavcopy_paths
from stocky.yahoo import YahooDataManager

console = Console()
app = typer.Typer(help="Consolidate Indian market instrument symbols.", no_args_is_help=False)
yahoo_app = typer.Typer(help="Manage Yahoo Finance cache data.")


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter("Use ISO date format YYYY-MM-DD.") from exc


_COLUMN_LABELS = {
    "isin": "ISIN",
    "zd_symbol": "Zerodha symbol",
    "yq_symbol": "Yahoo symbol",
    "nse_symbol": "NSE symbol",
    "bse_sc_code": "BSE scrip code",
    "bse_sc_name": "BSE scrip name",
}


def _format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    units = ("B", "KB", "MB", "GB", "TB")
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    if index == 0:
        return f"{int(size)} B"
    return f"{size:.1f} {units[index]}"


def _format_percent(part: int, whole: int) -> str:
    if whole == 0:
        return "n/a"
    return f"{100 * part / whole:.1f}%"


def _format_timestamp(value: str | None) -> str:
    if not value:
        return "None"
    return value[:19].replace("T", " ")


def _print_status(status: DatabaseStatus) -> None:
    overview = Table(title="Database overview")
    overview.add_column("Field")
    overview.add_column("Value")
    overview.add_row("Database", str(status.db_path))
    overview.add_row("Size", _format_size(status.db_size_bytes))
    overview.add_row("Consolidated rows", str(status.consolidated_rows))
    for ins_type, count in status.ins_type_counts.items():
        overview.add_row(f"  {ins_type}", str(count))
    overview.add_row("Yahoo responses", str(status.yahoo_rows))
    for exchange, count in status.yahoo_exchange_counts.items():
        overview.add_row(f"  {exchange}", str(count))
    console.print(overview)

    coverage = Table(title="Consolidated coverage")
    coverage.add_column("Column")
    coverage.add_column("Populated", justify="right")
    coverage.add_column("Missing", justify="right")
    coverage.add_column("Coverage", justify="right")
    for entry in status.coverage:
        coverage.add_row(
            _COLUMN_LABELS.get(entry.column, entry.column),
            str(entry.populated),
            str(status.consolidated_rows - entry.populated),
            _format_percent(entry.populated, status.consolidated_rows),
        )
    console.print(coverage)

    yq_populated = next((entry.populated for entry in status.coverage if entry.column == "yq_symbol"), 0)
    yahoo = Table(title="Yahoo cache")
    yahoo.add_column("Field")
    yahoo.add_column("Value")
    yahoo.add_row("Newest fetch", _format_timestamp(status.yahoo_newest_fetch))
    yahoo.add_row("Oldest fetch", _format_timestamp(status.yahoo_oldest_fetch))
    yahoo.add_row(
        "Consolidated symbols cached",
        f"{status.yahoo_cached_yq_symbols}/{yq_populated}"
        f" ({_format_percent(status.yahoo_cached_yq_symbols, yq_populated)})",
    )
    console.print(yahoo)


def _print_search_result(term: str, result: SearchResult) -> None:
    if result.total == 0:
        console.print(f"[yellow]No matches for '{term}'.[/yellow]")
        return

    table = Table(title=f"Matches for '{term}'")
    for header in ("ISIN", "Type", "Zerodha", "Yahoo", "NSE", "BSE code", "BSE name"):
        table.add_column(header)
    for match in result.matches:
        table.add_row(
            match.isin or "-",
            match.ins_type or "-",
            match.zd_symbol or "-",
            match.yq_symbol or "-",
            match.nse_symbol or "-",
            match.bse_sc_code or "-",
            match.bse_sc_name or "-",
        )
    console.print(table)

    if result.total > len(result.matches):
        console.print(f"[dim]Showing {len(result.matches)} of {result.total} matches. Raise --limit to see more.[/dim]")


def _print_rebuild_result(result: RebuildResult) -> None:
    table = Table(title="Rebuild summary")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Rows", str(result.rows))
    table.add_row("Database", str(result.db_path))
    table.add_row("Dry run", str(result.dry_run))
    table.add_row("BSE bhavcopy", str(result.bse_bhavcopy))
    table.add_row("NSE bhavcopy", str(result.nse_bhavcopy))
    table.add_row("Zerodha instruments", str(result.zerodha_instruments))
    table.add_row("Backup", str(result.backup_path) if result.backup_path else "None")
    console.print(table)


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    show_version: Annotated[bool, typer.Option("--version", help="Show version and exit.")] = False,
) -> None:
    if show_version:
        console.print(__version__)
        raise typer.Exit()

    if ctx.invoked_subcommand is None:
        interactive()


@app.command("version")
def version_command() -> None:
    """Show Stocky version."""
    console.print(__version__)


@app.command()
def interactive() -> None:
    """Run the interactive menu."""
    while True:
        console.print("\n[green]Choose an option[/green]")
        console.print("1. Rebuild stocky.db from scratch")
        console.print("2. Update all Yahoo data")
        console.print("3. Import Yahoo JSON cache into stocky.db")
        console.print("4. Show database status")
        console.print("5. Look up an instrument")
        console.print("6. Exit")
        choice = typer.prompt("Your choice", default="6").strip()

        if choice == "1":
            if typer.confirm("Rebuilding will replace the consolidated table. Continue?"):
                try:
                    result = _rebuild_impl(latest=True)
                    _print_rebuild_result(result)
                except Exception as exc:
                    console.print(f"[red]{exc}[/red]")
        elif choice == "2":
            if typer.confirm("This will call Yahoo Finance and may take a long time. Continue?"):
                manager = YahooDataManager()
                result = manager.update_data(exchange="BSE")
                console.print(
                    f"[green]Processed {result.processed}; wrote {result.written}; skipped {result.skipped}.[/green]"
                )
        elif choice == "3":
            result = import_yahoo_json_cache(DEFAULT_YAHOO_JSON_CACHE_DIR, DEFAULT_DB_PATH)
            console.print(f"[green]Imported {result.imported}; skipped {result.skipped}.[/green]")
        elif choice == "4":
            try:
                _print_status(read_status(DEFAULT_DB_PATH))
            except Exception as exc:
                console.print(f"[red]{exc}[/red]")
        elif choice == "5":
            term = typer.prompt("Search term").strip()
            try:
                _print_search_result(term, search_instruments(term, DEFAULT_DB_PATH))
            except Exception as exc:
                console.print(f"[red]{exc}[/red]")
        elif choice == "6":
            raise typer.Exit()
        else:
            console.print("[red]Not a valid selection.[/red]")


def _rebuild_impl(
    *,
    trade_date: str | None = None,
    latest: bool = False,
    input_dir: Path = DEFAULT_BHAVCOPY_DIR,
    bse_bhavcopy: Path | None = None,
    nse_bhavcopy: Path | None = None,
    zerodha_instruments: Path = DEFAULT_ZERODHA_INSTRUMENTS,
    db_path: Path = DEFAULT_DB_PATH,
    dry_run: bool = False,
    no_backup: bool = False,
) -> RebuildResult:
    paths = resolve_bhavcopy_paths(
        bse_bhavcopy=bse_bhavcopy,
        nse_bhavcopy=nse_bhavcopy,
        trade_date=_parse_date(trade_date),
        input_dir=input_dir,
        zerodha=zerodha_instruments,
        latest=latest,
    )
    return rebuild_database(paths, db_path=db_path, backup=not no_backup, dry_run=dry_run)


@app.command()
def rebuild(
    trade_date: Annotated[str | None, typer.Option("--date", help="Resolve bhavcopy filenames for YYYY-MM-DD.")] = None,
    latest: Annotated[
        bool,
        typer.Option("--latest", help="Use the latest matching BSE/NSE files in --input-dir."),
    ] = False,
    input_dir: Annotated[
        Path, typer.Option("--input-dir", help="Directory containing bhavcopy files.")
    ] = DEFAULT_BHAVCOPY_DIR,
    bse_bhavcopy: Annotated[Path | None, typer.Option("--bse-bhavcopy", help="Explicit BSE bhavcopy path.")] = None,
    nse_bhavcopy: Annotated[Path | None, typer.Option("--nse-bhavcopy", help="Explicit NSE bhavcopy path.")] = None,
    zerodha_instruments: Annotated[
        Path,
        typer.Option("--zerodha-instruments", help="Zerodha instruments CSV path."),
    ] = DEFAULT_ZERODHA_INSTRUMENTS,
    db_path: Annotated[Path, typer.Option("--db-path", help="SQLite output DB path.")] = DEFAULT_DB_PATH,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Validate and build data without writing SQLite output."),
    ] = False,
    no_backup: Annotated[
        bool,
        typer.Option("--no-backup", help="Do not back up an existing DB before writing."),
    ] = False,
) -> None:
    """Rebuild the consolidated instruments table."""
    try:
        result = _rebuild_impl(
            trade_date=trade_date,
            latest=latest,
            input_dir=input_dir,
            bse_bhavcopy=bse_bhavcopy,
            nse_bhavcopy=nse_bhavcopy,
            zerodha_instruments=zerodha_instruments,
            db_path=db_path,
            dry_run=dry_run,
            no_backup=no_backup,
        )
    except Exception as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    _print_rebuild_result(result)


@app.command()
def status(
    db_path: Annotated[Path, typer.Option("--db-path", help="SQLite DB path.")] = DEFAULT_DB_PATH,
) -> None:
    """Show database statistics, coverage, and Yahoo cache freshness."""
    try:
        result = read_status(db_path)
    except Exception as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    _print_status(result)


@app.command()
def query(
    term: Annotated[str, typer.Argument(help="Symbol, ISIN, BSE scrip code, or name fragment.")],
    limit: Annotated[int, typer.Option("--limit", help="Maximum number of matches to display.")] = 20,
    exact: Annotated[
        bool, typer.Option("--exact", help="Match symbols/ISIN/code exactly instead of substring search.")
    ] = False,
    db_path: Annotated[Path, typer.Option("--db-path", help="SQLite DB path.")] = DEFAULT_DB_PATH,
) -> None:
    """Look up an instrument across all symbol namespaces."""
    try:
        result = search_instruments(term, db_path, limit=limit, exact=exact)
    except Exception as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    _print_search_result(term, result)


@yahoo_app.command("update")
def yahoo_update(
    exchange: Annotated[str, typer.Option("--exchange", help="Exchange to query: BSE or NSE.")] = "BSE",
    key: Annotated[
        str, typer.Option("--key", help="Column in consolidated table to use as the base symbol.")
    ] = "zd_symbol",
    db_path: Annotated[Path, typer.Option("--db-path", help="SQLite DB path.")] = DEFAULT_DB_PATH,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Count symbols without calling Yahoo Finance.")] = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Optional maximum number of symbols to process.")] = None,
) -> None:
    """Update Yahoo Finance responses in SQLite."""
    try:
        result = YahooDataManager(db_path).update_data(key=key, exchange=exchange, dry_run=dry_run, limit=limit)
    except Exception as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(
        f"[green]Processed {result.processed}; wrote {result.written}; skipped {result.skipped}; "
        f"dry_run={result.dry_run}.[/green]"
    )


@yahoo_app.command("import-cache")
def yahoo_import_cache(
    cache_dir: Annotated[
        Path,
        typer.Option("--cache-dir", help="Directory of legacy JSON files."),
    ] = DEFAULT_YAHOO_JSON_CACHE_DIR,
    db_path: Annotated[Path, typer.Option("--db-path", help="SQLite DB path.")] = DEFAULT_DB_PATH,
) -> None:
    """Import legacy Yahoo JSON files into SQLite."""
    result = import_yahoo_json_cache(cache_dir, db_path)
    console.print(f"[green]Imported {result.imported}; skipped {result.skipped}.[/green]")


app.add_typer(yahoo_app, name="yahoo")


def main() -> None:
    app()
