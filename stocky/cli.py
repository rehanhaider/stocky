from __future__ import annotations

import contextlib
import dataclasses
import json
import shlex
import sys
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TaskID, TextColumn, TimeRemainingColumn
from rich.table import Table

from stocky import __version__
from stocky.config import (
    DEFAULT_BHAVCOPY_DIR,
    DEFAULT_DB_PATH,
    DEFAULT_SNAPSHOT_DIR,
    DEFAULT_YAHOO_JSON_CACHE_DIR,
    DEFAULT_ZERODHA_INSTRUMENTS,
    DEFAULT_ZERODHA_MF_INSTRUMENTS,
)
from stocky.database import (
    BuildProvenance,
    DatabaseStatus,
    InstrumentMatch,
    SearchResult,
    SourceRecord,
    import_yahoo_json_cache,
    read_status,
    search_instruments,
)
from stocky.enrich import (
    CRORE,
    YAHOO_FIELD_NAMES,
    EnrichedRow,
    ScreenResult,
    export_enriched,
    lookup_fields,
    screen_instruments,
)
from stocky.export import SNAPSHOT_FORMATS, create_snapshot, export_consolidated, export_tickers
from stocky.mutual_funds import MutualFundSearchResult, import_mutual_funds, search_mutual_funds
from stocky.pipeline import DEFAULT_DIFF_SAMPLE_SIZE, RebuildDiff, RebuildResult, SourcePreview, rebuild_database
from stocky.sources import BhavcopyPaths, list_bhavcopy_pairs, resolve_bhavcopy_paths
from stocky.summary import DEFAULT_MISSING_SAMPLE_SIZE, build_refresh_summary, render_markdown
from stocky.yahoo import ProgressCallback, YahooDataManager, ticker_column

console = Console()
error_console = Console(stderr=True)
app = typer.Typer(help="Consolidate Indian market instrument symbols.", no_args_is_help=False)
yahoo_app = typer.Typer(help="Manage Yahoo Finance cache data.")
mf_app = typer.Typer(help="Map mutual fund ISINs to Zerodha scheme identifiers.")

PLAIN_PROGRESS_EVERY = 50
MAX_LISTED_BHAVCOPY_PAIRS = 10
YAHOO_EXCHANGES = ("BSE", "NSE")


def _emit_json(payload: object) -> None:
    sys.stdout.write(
        json.dumps(dataclasses.asdict(payload) if dataclasses.is_dataclass(payload) else payload, default=str, indent=2)
        + "\n"
    )


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter("Use ISO date format YYYY-MM-DD.") from exc


_COLUMN_LABELS = {
    "isin": "ISIN",
    "nse_symbol": "NSE symbol",
    "bse_symbol": "BSE symbol",
    "bse_sc_code": "BSE scrip code",
    "bse_sc_name": "BSE scrip name",
    "zd_ns": "Zerodha NSE",
    "zd_bo": "Zerodha BSE",
    "yq_ns": "Yahoo NSE",
    "yq_bo": "Yahoo BSE",
}

# Field labels and InstrumentMatch attributes in display order for the equivalents view.
_MATCH_FIELDS = (
    ("ISIN", "isin"),
    ("Type", "ins_type"),
    ("NSE", "nse_symbol"),
    ("BSE", "bse_symbol"),
    ("BSE code", "bse_sc_code"),
    ("BSE name", "bse_sc_name"),
    ("Zerodha NSE", "zd_ns"),
    ("Zerodha BSE", "zd_bo"),
    ("Yahoo NSE", "yq_ns"),
    ("Yahoo BSE", "yq_bo"),
)
# Search results list the identity columns only, so the table fits a standard terminal; lookup,
# explore, and the interactive menu's row selection show every Zerodha and Yahoo ticker.
_SEARCH_FIELDS = _MATCH_FIELDS[:6]


def _yahoo_cached_rows(status: DatabaseStatus) -> list[tuple[str, str]]:
    populated = {entry.column: entry.populated for entry in status.coverage}
    return [
        (
            f"Yahoo {exchange} tickers cached",
            f"{cached}/{populated.get(column, 0)} ({_format_percent(cached, populated.get(column, 0))})",
        )
        for exchange, column, cached in (
            ("NSE", "yq_ns", status.yahoo_cached_ns),
            ("BSE", "yq_bo", status.yahoo_cached_bo),
        )
    ]


def _split_columns(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


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

    yahoo = Table(title="Yahoo cache")
    yahoo.add_column("Field")
    yahoo.add_column("Value")
    yahoo.add_row("Newest fetch", _format_timestamp(status.yahoo_newest_fetch))
    yahoo.add_row("Oldest fetch", _format_timestamp(status.yahoo_oldest_fetch))
    for label, value in _yahoo_cached_rows(status):
        yahoo.add_row(label, value)
    console.print(yahoo)

    _print_provenance(status.provenance)


_SOURCE_LABELS = {
    "bse_bhavcopy": "BSE bhavcopy",
    "nse_bhavcopy": "NSE bhavcopy",
    "zerodha_instruments": "Zerodha instruments",
}


def _print_sources(title: str, sources: list[SourceRecord]) -> None:
    table = Table(title=title)
    table.add_column("Source")
    table.add_column("File")
    table.add_column("Trade date")
    table.add_column("Rows", justify="right")
    table.add_column("SHA-256")
    for source in sources:
        table.add_row(
            _SOURCE_LABELS.get(source.role, source.role),
            escape(source.file_name),
            source.trade_date or "-",
            str(source.rows),
            source.sha256[:12],
        )
    console.print(table)


def _print_provenance(provenance: BuildProvenance | None) -> None:
    if provenance is None:
        console.print("[yellow]No source provenance recorded. Run 'stocky rebuild' to record it.[/yellow]")
        return
    _print_sources(
        f"Built {_format_timestamp(provenance.built_at)} UTC by Stocky {provenance.stocky_version} "
        f"({provenance.consolidated_rows} rows)",
        provenance.sources,
    )


def _print_search_result(term: str, result: SearchResult, *, numbered: bool = False) -> None:
    if result.total == 0:
        console.print(f"[yellow]No matches for '{term}'.[/yellow]")
        return

    if result.fuzzy:
        console.print("[dim]No direct matches; showing closest names.[/dim]")

    table = Table(title=f"Matches for '{term}'")
    if numbered:
        table.add_column("#", justify="right")
    for header, _ in _SEARCH_FIELDS:
        table.add_column(header)
    for index, match in enumerate(result.matches, start=1):
        table.add_row(
            *([str(index)] if numbered else []),
            *(getattr(match, field) or "-" for _, field in _SEARCH_FIELDS),
        )
    console.print(table)

    if result.total > len(result.matches):
        console.print(f"[dim]Showing {len(result.matches)} of {result.total} matches. Raise --limit to see more.[/dim]")


# Field labels and MutualFundMatch attributes in display order for mutual fund search results.
_MF_FIELDS = (
    ("ISIN", "isin"),
    ("Zerodha", "zd_mf"),
    ("Name", "name"),
    ("AMC", "amc"),
    ("Type", "scheme_type"),
    ("Plan", "plan"),
    ("Option", "dividend_type"),
)


def _print_mf_search_result(term: str, result: MutualFundSearchResult) -> None:
    if result.total == 0:
        console.print(f"[yellow]No mutual funds match '{escape(term)}'.[/yellow]")
        return

    table = Table(title=f"Mutual funds matching '{escape(term)}'")
    for header, field in _MF_FIELDS:
        # Identifiers stay whole so they can be copied; names and labels wrap instead.
        table.add_column(header, no_wrap=field in ("isin", "zd_mf"))
    for match in result.matches:
        table.add_row(*(getattr(match, field) or "-" for _, field in _MF_FIELDS))
    console.print(table)

    if result.total > len(result.matches):
        console.print(f"[dim]Showing {len(result.matches)} of {result.total} matches. Raise --limit to see more.[/dim]")


def _print_equivalents(match: InstrumentMatch, term: str | None = None) -> None:
    identifier = term or match.nse_symbol or match.bse_symbol or match.isin or "-"
    table = Table(title=f"Equivalents for '{identifier}'")
    table.add_column("Field")
    table.add_column("Value")
    for label, field in _MATCH_FIELDS:
        table.add_row(label, getattr(match, field) or "-")
    console.print(table)


_YAHOO_STATUS_NOTES = {
    "missing": "No cached Yahoo response for this ticker. Run 'stocky yahoo update' to fetch it.",
    "unusable": "The cached Yahoo response is an error or names no listed security.",
}


def _format_crore(value: float | None) -> str:
    return "-" if value is None else f"{value / CRORE:,.0f}"


def _format_value(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.4g}" if abs(value) < 1 else f"{value:,.2f}"
    if isinstance(value, (int, float)):
        return f"{int(value):,}"
    return escape(str(value))


def _print_yahoo_fields(row: EnrichedRow) -> None:
    table = Table(title=f"{escape(row.yahoo_ticker)} ({row.exchange}, {row.isin})")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("yahoo_status", row.yahoo_status)
    table.add_row("fetched_at", _format_timestamp(row.fetched_at) if row.fetched_at else "-")
    for name in YAHOO_FIELD_NAMES:
        value = getattr(row.fields, name)
        table.add_row(
            name, f"{_format_crore(value)} Cr" if name == "market_cap" and value is not None else _format_value(value)
        )
    console.print(table)
    if row.yahoo_status in _YAHOO_STATUS_NOTES:
        console.print(f"[yellow]{_YAHOO_STATUS_NOTES[row.yahoo_status]}[/yellow]")


def _print_screen_result(result: ScreenResult) -> None:
    if result.total == 0:
        console.print("[yellow]No listings match the screen.[/yellow]")
        return

    table = Table(title="Screen results")
    # Identifiers and numbers stay whole so they can be copied and compared; names and sectors wrap instead.
    for header, justify, no_wrap in (
        ("Ticker", "left", True),
        ("ISIN", "left", True),
        ("Name", "left", False),
        ("Market cap (Cr)", "right", True),
        ("Price", "right", True),
        ("P/E", "right", True),
        ("Sector", "left", False),
        ("Quote date", "left", True),
    ):
        table.add_column(header, justify=justify, no_wrap=no_wrap)
    for row in result.rows:
        table.add_row(
            escape(row.yahoo_ticker),
            row.isin,
            escape(row.fields.name or row.bse_sc_name or "-"),
            _format_crore(row.fields.market_cap),
            _format_value(row.fields.price),
            _format_value(row.fields.trailing_pe),
            escape(row.fields.sector or "-"),
            (row.fields.market_time or "-")[:10],
        )
    console.print(table)

    if result.total > len(result.rows):
        console.print(f"[dim]Showing {len(result.rows)} of {result.total} matches. Raise --limit to see more.[/dim]")


def _sample_label(row: dict[str, str | None]) -> str:
    return row.get("nse_symbol") or row.get("bse_symbol") or row.get("bse_sc_name") or "-"


def _print_rebuild_diff(diff: RebuildDiff) -> None:
    if diff.current_provenance is not None:
        _print_sources("Current database was built from", diff.current_provenance.sources)
    elif diff.current_rows:
        console.print("[dim]The current database records no source provenance.[/dim]")

    counts = Table(title="Row changes")
    counts.add_column("Field")
    counts.add_column("Rows", justify="right")
    counts.add_row("Current rows", str(diff.current_rows))
    counts.add_row("Rebuilt rows", str(diff.rebuilt_rows))
    counts.add_row("Added", str(diff.added))
    counts.add_row("Removed", str(diff.removed))
    counts.add_row("Changed", str(diff.changed))
    counts.add_row("Unchanged", str(diff.unchanged))
    if diff.duplicate_current or diff.duplicate_rebuilt:
        counts.add_row("Duplicate ISIN rows (current)", str(diff.duplicate_current))
        counts.add_row("Duplicate ISIN rows (rebuilt)", str(diff.duplicate_rebuilt))
    for column, count in diff.column_changes.items():
        counts.add_row(f"  {_COLUMN_LABELS.get(column, column)} changed", str(count))
    console.print(counts)

    if diff.sample_added or diff.sample_removed or diff.sample_changed or diff.sample_duplicates:
        samples = Table(title="Sample differences")
        samples.add_column("Change")
        samples.add_column("ISIN")
        samples.add_column("Detail")
        for row in diff.sample_added:
            samples.add_row("added", str(row["isin"]), escape(_sample_label(row)))
        for row in diff.sample_removed:
            samples.add_row("removed", str(row["isin"]), escape(_sample_label(row)))
        for change in diff.sample_changed:
            detail = "; ".join(
                f"{_COLUMN_LABELS.get(column, column)}: {before or '-'} -> {after or '-'}"
                for column, (before, after) in change.changes.items()
            )
            samples.add_row("changed", change.isin, escape(detail))
        for isin in diff.sample_duplicates:
            samples.add_row("duplicate", isin, "ISIN appears more than once; the first row is compared")
        console.print(samples)


def _print_rebuild_result(result: RebuildResult) -> None:
    if result.diff is not None:
        _print_rebuild_diff(result.diff)
    if result.sources:
        _print_sources("Rebuilt from", result.sources)

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


def _print_bhavcopy_pairs(pairs: list) -> int:
    """Print the newest bhavcopy pairs and return how many rows are selectable."""
    listed = pairs[:MAX_LISTED_BHAVCOPY_PAIRS]
    table = Table(title="Available bhavcopy pairs")
    table.add_column("#", justify="right")
    table.add_column("Trade date")
    table.add_column("BSE file")
    table.add_column("NSE file")
    for index, pair in enumerate(listed, start=1):
        table.add_row(str(index), pair.trade_date.isoformat(), pair.bse.name, pair.nse.name)
    console.print(table)

    if len(pairs) > len(listed):
        console.print(f"[dim]... and {len(pairs) - len(listed)} more[/dim]")
    return len(listed)


def _print_source_preview(preview: SourcePreview, db_path: Path) -> None:
    table = Table(title="Rebuild preview")
    table.add_column("Source")
    table.add_column("File")
    table.add_column("Rows", justify="right")
    table.add_row("BSE bhavcopy", str(preview.bse_bhavcopy), str(preview.bse_rows))
    table.add_row("NSE bhavcopy", str(preview.nse_bhavcopy), str(preview.nse_rows))
    table.add_row("Zerodha instruments", str(preview.zerodha_instruments), str(preview.zerodha_rows))
    table.add_row("Database", str(db_path), "-")
    console.print(table)


def _select_bhavcopy_paths() -> BhavcopyPaths | None:
    """Ask which bhavcopy pair to rebuild from; return None to go back to the menu."""
    pairs = list_bhavcopy_pairs(DEFAULT_BHAVCOPY_DIR)
    if not pairs:
        console.print(
            f"[red]No BSE/NSE bhavcopy pairs found in {DEFAULT_BHAVCOPY_DIR}. "
            "Download the bhavcopy files into that directory first.[/red]"
        )
        return None

    listed = _print_bhavcopy_pairs(pairs)
    selection = typer.prompt("Row number, or a trade date (YYYY-MM-DD)", default="1").strip()

    if selection.isdigit():
        row_number = int(selection)
        if not 1 <= row_number <= listed:
            console.print(f"[red]Enter a number between 1 and {listed}.[/red]")
            return None
        pair = pairs[row_number - 1]
    else:
        try:
            trade_date = date.fromisoformat(selection)
        except ValueError:
            console.print("[red]Enter a row number or a date like 2026-09-19.[/red]")
            return None

        # Resolve against the discovered pairs rather than the canonical filenames, so every
        # accepted spelling works and dates below the displayed rows stay reachable.
        pair = next((candidate for candidate in pairs if candidate.trade_date == trade_date), None)
        if pair is None:
            console.print(
                f"[red]No BSE/NSE bhavcopy pair for {trade_date} in {DEFAULT_BHAVCOPY_DIR}. "
                "Pick a listed row or download that day's files.[/red]"
            )
            return None

    return BhavcopyPaths(bse=pair.bse, nse=pair.nse, zerodha=DEFAULT_ZERODHA_INSTRUMENTS)


def _interactive_rebuild() -> None:
    paths = _select_bhavcopy_paths()
    if paths is None:
        return

    try:
        with console.status("Reading source files..."):
            planned = _rebuild_impl(
                bse_bhavcopy=paths.bse,
                nse_bhavcopy=paths.nse,
                zerodha_instruments=paths.zerodha,
                db_path=DEFAULT_DB_PATH,
                dry_run=True,
            )
    except Exception as exc:
        console.print(f"[red]Cannot rebuild: {escape(str(exc))}[/red]")
        return

    rows = {source.role: source.rows for source in planned.sources}
    preview = SourcePreview(
        bse_bhavcopy=paths.bse,
        bse_rows=rows["bse_bhavcopy"],
        nse_bhavcopy=paths.nse,
        nse_rows=rows["nse_bhavcopy"],
        zerodha_instruments=paths.zerodha,
        zerodha_rows=rows["zerodha_instruments"],
    )
    _print_source_preview(preview, DEFAULT_DB_PATH)
    if planned.diff is not None:
        _print_rebuild_diff(planned.diff)
    if not typer.confirm("Rebuild the consolidated table from these files?"):
        return

    try:
        with console.status("Rebuilding...") as spinner:
            result = _rebuild_impl(
                bse_bhavcopy=paths.bse,
                nse_bhavcopy=paths.nse,
                zerodha_instruments=paths.zerodha,
                db_path=DEFAULT_DB_PATH,
                progress=lambda stage: spinner.update(f"{stage}..."),
            )
    except Exception as exc:
        console.print(f"[red]Rebuild failed: {escape(str(exc))}[/red]")
        return

    _print_rebuild_result(result)


def _interactive_show_matches(term: str, result: SearchResult) -> None:
    """Show one direct match in full, or list several and offer a row's Zerodha and Yahoo tickers."""
    if result.total == 1 and not result.fuzzy:
        _print_equivalents(result.matches[0], term)
        return

    _print_search_result(term, result, numbered=True)
    if not result.matches:
        return

    selection = typer.prompt("Row number for equivalents (blank to skip)", default="", show_default=False).strip()
    if not selection:
        return
    if selection.isdigit() and 1 <= int(selection) <= len(result.matches):
        _print_equivalents(result.matches[int(selection) - 1])
    else:
        console.print(f"[yellow]Enter a number between 1 and {len(result.matches)}.[/yellow]")


def _interactive_yahoo_update() -> None:
    exchange = typer.prompt("Exchange", default="BSE").strip().upper()
    if exchange not in YAHOO_EXCHANGES:
        console.print(f"[red]Enter one of {', '.join(YAHOO_EXCHANGES)}.[/red]")
        return

    raw_limit = typer.prompt("Limit (blank = all)", default="", show_default=False).strip()
    limit: int | None = None
    if raw_limit:
        try:
            limit = int(raw_limit)
        except ValueError:
            limit = 0
        if limit < 1:
            console.print("[red]Enter a positive number, or leave blank for all.[/red]")
            return

    missing_only = typer.confirm("Only fetch tickers with no cached response?", default=False)

    manager = YahooDataManager(DEFAULT_DB_PATH)
    try:
        planned = manager.update_data(
            exchange=exchange,
            dry_run=True,
            limit=limit,
            missing_only=missing_only,
        )
    except Exception as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        return

    console.print(
        f"[cyan]{planned.processed} tickers to fetch from Yahoo Finance ({exchange}, {ticker_column(exchange)}).[/cyan]"
    )
    if planned.processed == 0:
        # update_data raises when the column holds no tickers at all, so an empty plan means
        # missing_only filtered out every ticker it found.
        console.print(
            "[green]Nothing to fetch; every ticker already has a cached response.[/green]"
            if missing_only
            else f"[green]No {exchange} tickers found.[/green]"
        )
        return

    if not typer.confirm("Start the update? This may take a long time."):
        return

    try:
        with _yahoo_progress(exchange, json_output=False) as print_progress:
            result = manager.update_data(
                exchange=exchange,
                limit=limit,
                missing_only=missing_only,
                progress=print_progress,
            )
    except Exception as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        return

    console.print(f"[green]Processed {result.processed}; wrote {result.written}; skipped {result.skipped}.[/green]")


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
        console.print("2. Update Yahoo data")
        console.print("3. Import Yahoo JSON cache into stocky.db")
        console.print("4. Show database status")
        console.print("5. Look up an instrument")
        console.print("6. Exit")
        choice = typer.prompt("Your choice", default="6").strip()

        if choice == "1":
            _interactive_rebuild()
        elif choice == "2":
            _interactive_yahoo_update()
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
                result = search_instruments(term, DEFAULT_DB_PATH)
            except Exception as exc:
                console.print(f"[red]{exc}[/red]")
            else:
                _interactive_show_matches(term, result)
        elif choice == "6":
            raise typer.Exit()
        else:
            console.print("[red]Not a valid selection.[/red]")


@app.command()
def tui(
    db_path: Annotated[Path, typer.Option("--db-path", help="SQLite DB path.")] = DEFAULT_DB_PATH,
    input_dir: Annotated[
        Path, typer.Option("--input-dir", help="Directory containing bhavcopy files.")
    ] = DEFAULT_BHAVCOPY_DIR,
    zerodha_instruments: Annotated[
        Path,
        typer.Option("--zerodha-instruments", help="Zerodha instruments CSV path."),
    ] = DEFAULT_ZERODHA_INSTRUMENTS,
) -> None:
    """Open the full-screen terminal UI."""
    try:
        from stocky.tui import StockyApp
    except ModuleNotFoundError as exc:
        if exc.name is None or exc.name.partition(".")[0] != "textual":
            raise
        error_console.print(
            "[red]The TUI needs Textual. Install it with 'uv sync --extra tui' or 'pip install \"stocky[tui]\"'.[/red]"
        )
        raise typer.Exit(1) from exc

    StockyApp(db_path=db_path, input_dir=input_dir, zerodha_instruments=zerodha_instruments).run()


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
    sample_size: int = DEFAULT_DIFF_SAMPLE_SIZE,
    progress: Callable[[str], None] | None = None,
) -> RebuildResult:
    paths = resolve_bhavcopy_paths(
        bse_bhavcopy=bse_bhavcopy,
        nse_bhavcopy=nse_bhavcopy,
        trade_date=_parse_date(trade_date),
        input_dir=input_dir,
        zerodha=zerodha_instruments,
        latest=latest,
    )
    return rebuild_database(
        paths,
        db_path=db_path,
        backup=not no_backup,
        dry_run=dry_run,
        sample_size=sample_size,
        progress=progress,
    )


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
        typer.Option(
            "--dry-run",
            help="Build the table and compare it with the current database without writing anything.",
        ),
    ] = False,
    no_backup: Annotated[
        bool,
        typer.Option("--no-backup", help="Do not back up an existing DB before writing."),
    ] = False,
    sample: Annotated[
        int,
        typer.Option("--sample", min=0, help="Number of added, removed, and changed rows to show from the comparison."),
    ] = DEFAULT_DIFF_SAMPLE_SIZE,
    json_output: Annotated[bool, typer.Option("--json", help="Print the result as JSON on stdout.")] = False,
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
            sample_size=sample,
        )
    except Exception as exc:
        (error_console if json_output else console).print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    if json_output:
        _emit_json(result)
    else:
        _print_rebuild_result(result)


@app.command()
def status(
    db_path: Annotated[Path, typer.Option("--db-path", help="SQLite DB path.")] = DEFAULT_DB_PATH,
    json_output: Annotated[bool, typer.Option("--json", help="Print the result as JSON on stdout.")] = False,
) -> None:
    """Show database statistics, coverage, and Yahoo cache freshness."""
    try:
        result = read_status(db_path)
    except Exception as exc:
        (error_console if json_output else console).print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    if json_output:
        _emit_json(result)
    else:
        _print_status(result)


@app.command()
def summary(
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Markdown file to write. Omit to print it on stdout."),
    ] = None,
    sample: Annotated[
        int,
        typer.Option("--sample", min=0, help="Number of tickers without a Yahoo response to list per exchange."),
    ] = DEFAULT_MISSING_SAMPLE_SIZE,
    db_path: Annotated[Path, typer.Option("--db-path", help="SQLite DB path.")] = DEFAULT_DB_PATH,
    json_output: Annotated[bool, typer.Option("--json", help="Print the summary as JSON on stdout.")] = False,
) -> None:
    """Validate the database and write the refresh summary: sources, row counts, and Yahoo gaps.

    Exits with status 1 when a validation check fails, after writing the summary.
    """
    try:
        result = build_refresh_summary(db_path, sample_size=sample)
    except Exception as exc:
        error_console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc

    if json_output:
        _emit_json(result)
    elif output is None:
        sys.stdout.write(render_markdown(result))
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_markdown(result), encoding="utf-8")
        error_console.print(f"Wrote the refresh summary to {escape(str(output))}.")

    if not result.passed:
        failed = ", ".join(check.name for check in result.checks if not check.passed)
        error_console.print(f"[red]Validation failed: {escape(failed)}.[/red]")
        raise typer.Exit(1)


@app.command()
def query(
    term: Annotated[str, typer.Argument(help="Symbol, ISIN, BSE scrip code, or name fragment.")],
    limit: Annotated[int, typer.Option("--limit", help="Maximum number of matches to display.")] = 20,
    exact: Annotated[
        bool, typer.Option("--exact", help="Match symbols/ISIN/code exactly instead of substring search.")
    ] = False,
    db_path: Annotated[Path, typer.Option("--db-path", help="SQLite DB path.")] = DEFAULT_DB_PATH,
    json_output: Annotated[bool, typer.Option("--json", help="Print the result as JSON on stdout.")] = False,
) -> None:
    """Look up an instrument across all symbol namespaces."""
    try:
        result = search_instruments(term, db_path, limit=limit, exact=exact)
    except Exception as exc:
        (error_console if json_output else console).print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    if json_output:
        _emit_json({"term": term, **dataclasses.asdict(result)})
    else:
        _print_search_result(term, result)


@app.command()
def lookup(
    identifier: Annotated[str, typer.Argument(help="Exact symbol, ISIN, BSE scrip code, or name.")],
    limit: Annotated[
        int, typer.Option("--limit", help="Maximum number of candidates to display when the identifier is ambiguous.")
    ] = 20,
    db_path: Annotated[Path, typer.Option("--db-path", help="SQLite DB path.")] = DEFAULT_DB_PATH,
    json_output: Annotated[bool, typer.Option("--json", help="Print the result as JSON on stdout.")] = False,
) -> None:
    """Show every known identifier for one exact match."""
    try:
        result = search_instruments(identifier, db_path, exact=True, limit=limit)
    except Exception as exc:
        (error_console if json_output else console).print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    if result.total == 0:
        (error_console if json_output else console).print(
            f"[red]No instrument matches '{identifier}' exactly.\n"
            f"Try 'stocky query {shlex.quote(identifier)}' for a fuzzy search.[/red]"
        )
        raise typer.Exit(1)
    if result.total > 1:
        if json_output:
            _emit_json({"identifier": identifier, **dataclasses.asdict(result)})
            error_console.print("[dim]Multiple instruments match; refine the identifier.[/dim]")
        else:
            _print_search_result(identifier, result)
            console.print("[dim]Multiple instruments match; refine the identifier.[/dim]")
        return

    if json_output:
        _emit_json(result.matches[0])
    else:
        _print_equivalents(result.matches[0], identifier)


@app.command()
def explore(
    db_path: Annotated[Path, typer.Option("--db-path", help="SQLite DB path.")] = DEFAULT_DB_PATH,
    limit: Annotated[int, typer.Option("--limit", help="Maximum number of matches to display.")] = 20,
    json_output: Annotated[bool, typer.Option("--json", help="Print the result as JSON on stdout.")] = False,
) -> None:
    """Search instruments and inspect their equivalent identifiers."""
    if limit < 1:
        (error_console if json_output else console).print("[red]Limit must be at least 1.[/red]")
        raise typer.Exit(1)

    if not db_path.exists():
        (error_console if json_output else console).print(
            f"[red]Database not found: {db_path}. Run 'stocky rebuild' first.[/red]"
        )
        raise typer.Exit(1)

    while True:
        term = typer.prompt("Search (blank to quit)", default="", show_default=False, err=json_output).strip()
        if not term:
            (error_console if json_output else console).print("[dim]Bye.[/dim]")
            return

        try:
            result = search_instruments(term, db_path, limit=limit)
        except ValueError as exc:
            (error_console if json_output else console).print(f"[red]{exc}[/red]")
            continue
        except Exception as exc:
            (error_console if json_output else console).print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc

        if json_output:
            _emit_json({"term": term, **dataclasses.asdict(result)})
        else:
            _print_search_result(term, result, numbered=True)
        if not result.matches:
            continue

        while True:
            selection = typer.prompt(
                "Row number for equivalents (blank to search again)", default="", show_default=False, err=json_output
            ).strip()
            if not selection:
                break
            try:
                row_number = int(selection)
            except ValueError:
                row_number = 0
            if not 1 <= row_number <= len(result.matches):
                (error_console if json_output else console).print(
                    f"[yellow]Enter a number between 1 and {len(result.matches)}.[/yellow]"
                )
                continue

            if json_output:
                _emit_json(result.matches[row_number - 1])
            else:
                _print_equivalents(result.matches[row_number - 1])
            break


@app.command()
def export(
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="File to write. Omit to write to stdout."),
    ] = None,
    format: Annotated[
        str | None,
        typer.Option("--format", help="Output format: csv, json, or parquet. Inferred from --output when omitted."),
    ] = None,
    columns: Annotated[
        str | None,
        typer.Option("--columns", help="Comma-separated columns to export, in the order given."),
    ] = None,
    require: Annotated[
        str | None,
        typer.Option("--require", help="Comma-separated columns that must be populated for a row to be exported."),
    ] = None,
    tickers: Annotated[
        str | None,
        typer.Option(
            "--tickers",
            help=(
                "Write one ticker per line from this column "
                "(nse_symbol, bse_symbol, bse_sc_code, zd_ns, zd_bo, yq_ns, or yq_bo)."
            ),
        ),
    ] = None,
    suffix: Annotated[
        str | None,
        typer.Option("--suffix", help="Text appended to every ticker. Needs --tickers."),
    ] = None,
    db_path: Annotated[Path, typer.Option("--db-path", help="SQLite DB path.")] = DEFAULT_DB_PATH,
) -> None:
    """Export the consolidated table to CSV, JSON, or Parquet, or write a ticker list."""
    try:
        if tickers is not None:
            if format is not None or columns is not None:
                raise ValueError(
                    "--tickers writes a plain ticker list; it cannot be combined with --format or --columns."
                )
            result = export_tickers(
                db_path,
                output=output,
                column=tickers.strip(),
                suffix=suffix or "",
                require=_split_columns(require),
            )
        else:
            if suffix is not None:
                raise ValueError("--suffix only applies with --tickers.")
            result = export_consolidated(
                db_path,
                output=output,
                format=format,
                columns=_split_columns(columns),
                require=_split_columns(require),
            )
    except Exception as exc:
        error_console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc

    if result.output is None:
        return
    if result.format == "tickers":
        console.print(
            f"[green]Wrote {result.rows} tickers from {escape(result.columns[0])} "
            f"to {escape(str(result.output))}.[/green]"
        )
    else:
        console.print(
            f"[green]Wrote {result.rows} rows ({escape(', '.join(result.columns))}) "
            f"to {escape(str(result.output))} as {result.format}.[/green]"
        )


@app.command()
def snapshot(
    output_dir: Annotated[
        Path, typer.Option("--output-dir", help="Directory that holds versioned snapshots.")
    ] = DEFAULT_SNAPSHOT_DIR,
    version: Annotated[
        str | None,
        typer.Option("--version", help="Snapshot version label. Defaults to today's UTC date (YYYY-MM-DD)."),
    ] = None,
    formats: Annotated[
        str | None,
        typer.Option("--formats", help=f"Comma-separated formats to write. Default: {','.join(SNAPSHOT_FORMATS)}."),
    ] = None,
    db_path: Annotated[Path, typer.Option("--db-path", help="SQLite DB path.")] = DEFAULT_DB_PATH,
    json_output: Annotated[bool, typer.Option("--json", help="Print the result as JSON on stdout.")] = False,
) -> None:
    """Write a versioned, checksummed snapshot of the consolidated table that downstream projects can pin."""
    try:
        result = create_snapshot(db_path, output_dir=output_dir, version=version, formats=_split_columns(formats))
    except Exception as exc:
        error_console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc

    if json_output:
        _emit_json(result)
        return

    console.print(
        f"[green]Wrote snapshot {escape(result.version)} ({result.rows} rows) to {escape(str(result.directory))}.[/green]"
    )
    for file in result.files:
        console.print(f"  {escape(file.name)}  sha256 {file.sha256}")


@app.command()
def screen(
    exchange: Annotated[
        str | None, typer.Option("--exchange", help="Only NSE (yq_ns tickers) or BSE (yq_bo tickers) listings.")
    ] = None,
    market_cap_gt: Annotated[
        float | None, typer.Option("--market-cap-gt", min=0, help="Market cap above this many crore.")
    ] = None,
    market_cap_lt: Annotated[
        float | None, typer.Option("--market-cap-lt", min=0, help="Market cap below this many crore.")
    ] = None,
    sector: Annotated[str | None, typer.Option("--sector", help="Sector containing this text, any case.")] = None,
    industry: Annotated[str | None, typer.Option("--industry", help="Industry containing this text, any case.")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, help="Maximum number of listings to display.")] = 20,
    db_path: Annotated[Path, typer.Option("--db-path", help="SQLite DB path.")] = DEFAULT_DB_PATH,
    json_output: Annotated[bool, typer.Option("--json", help="Print the result as JSON on stdout.")] = False,
) -> None:
    """Screen listings by cached Yahoo market cap, sector, and industry, largest market cap first.

    Reads only the cached Yahoo responses; it never calls Yahoo Finance.
    """
    try:
        result = screen_instruments(
            db_path,
            exchange=exchange,
            market_cap_gt=market_cap_gt,
            market_cap_lt=market_cap_lt,
            sector=sector,
            industry=industry,
            limit=limit,
        )
    except Exception as exc:
        (error_console if json_output else console).print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc

    if json_output:
        _emit_json({"total": result.total, "rows": [row.as_record() for row in result.rows]})
    else:
        _print_screen_result(result)


@contextlib.contextmanager
def _yahoo_progress(exchange: str, json_output: bool) -> Iterator[ProgressCallback]:
    """Yield the progress callback that suits the output mode: JSON lines, a bar, or plain lines."""

    def json_progress(index: int, total: int, written: int, skipped: int) -> None:
        sys.stderr.write(
            json.dumps(
                {"event": "progress", "processed": index, "total": total, "written": written, "skipped": skipped}
            )
            + "\n"
        )
        sys.stderr.flush()

    def plain_progress(index: int, total: int, written: int, skipped: int) -> None:
        if index % PLAIN_PROGRESS_EVERY == 0 or index == total:
            error_console.print(f"{index}/{total} processed; {written} written; {skipped} skipped")

    if json_output:
        yield json_progress
        return

    if not error_console.is_terminal:
        yield plain_progress
        return

    bar = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("{task.fields[written]} written, {task.fields[skipped]} skipped"),
        TimeRemainingColumn(),
        console=error_console,
    )
    task_id: TaskID | None = None

    def bar_progress(index: int, total: int, written: int, skipped: int) -> None:
        nonlocal task_id
        if task_id is None:
            task_id = bar.add_task(
                f"Fetching {exchange}", total=total, completed=index, written=written, skipped=skipped
            )
        bar.update(task_id, completed=index, written=written, skipped=skipped)

    with bar:
        yield bar_progress


@yahoo_app.command("update")
def yahoo_update(
    exchange: Annotated[
        str, typer.Option("--exchange", help="Exchange to query: BSE (yq_bo tickers) or NSE (yq_ns tickers).")
    ] = "BSE",
    db_path: Annotated[Path, typer.Option("--db-path", help="SQLite DB path.")] = DEFAULT_DB_PATH,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Count tickers without calling Yahoo Finance.")] = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Optional maximum number of tickers to process.")] = None,
    missing_only: Annotated[
        bool,
        typer.Option("--missing-only", help="Only fetch tickers that have no cached response yet."),
    ] = False,
    symbols: Annotated[
        str | None,
        typer.Option(
            "--symbols",
            help="Comma-separated tickers or exchange symbols to fetch, such as RELIANCE.NS,INFY. "
            "Each must be listed for --exchange.",
        ),
    ] = None,
    stale_days: Annotated[
        float | None,
        typer.Option(
            "--stale-days",
            min=0,
            help="Only fetch tickers with no cached response or one fetched more than this many days ago.",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the result as JSON on stdout and progress events as JSON lines on stderr."),
    ] = False,
) -> None:
    """Update Yahoo Finance responses in SQLite."""
    try:
        with _yahoo_progress(exchange, json_output) as print_progress:
            result = YahooDataManager(db_path).update_data(
                exchange=exchange,
                dry_run=dry_run,
                limit=limit,
                missing_only=missing_only,
                symbols=_split_columns(symbols),
                stale_days=stale_days,
                progress=print_progress,
            )
    except Exception as exc:
        if json_output:
            sys.stderr.write(json.dumps({"event": "error", "message": str(exc)}) + "\n")
            sys.stderr.flush()
        else:
            error_console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc

    if json_output:
        _emit_json(result)
    else:
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
    json_output: Annotated[bool, typer.Option("--json", help="Print the result as JSON on stdout.")] = False,
) -> None:
    """Import legacy Yahoo JSON files into SQLite."""
    result = import_yahoo_json_cache(cache_dir, db_path)
    if json_output:
        _emit_json(result)
    else:
        console.print(f"[green]Imported {result.imported}; skipped {result.skipped}.[/green]")


@yahoo_app.command("fields")
def yahoo_fields(
    identifier: Annotated[str, typer.Argument(help="Exact symbol, Yahoo ticker, ISIN, BSE scrip code, or name.")],
    db_path: Annotated[Path, typer.Option("--db-path", help="SQLite DB path.")] = DEFAULT_DB_PATH,
    json_output: Annotated[bool, typer.Option("--json", help="Print the result as JSON on stdout.")] = False,
) -> None:
    """Show the cached Yahoo fields of an instrument, one table per exchange that lists it."""
    try:
        rows = lookup_fields(identifier, db_path)
    except Exception as exc:
        (error_console if json_output else console).print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc

    if not rows:
        (error_console if json_output else console).print(
            f"[red]No instrument with a Yahoo ticker matches '{escape(identifier)}' exactly.\n"
            f"Try 'stocky query {escape(shlex.quote(identifier))}' for a fuzzy search.[/red]"
        )
        raise typer.Exit(1)

    if json_output:
        _emit_json([row.as_record() for row in rows])
        return
    for row in rows:
        _print_yahoo_fields(row)


@yahoo_app.command("enriched")
def yahoo_enriched(
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="File to write. Omit to write to stdout."),
    ] = None,
    format: Annotated[
        str | None,
        typer.Option("--format", help="Output format: csv, json, or parquet. Inferred from --output when omitted."),
    ] = None,
    exchange: Annotated[
        str | None, typer.Option("--exchange", help="Only NSE (yq_ns tickers) or BSE (yq_bo tickers) listings.")
    ] = None,
    db_path: Annotated[Path, typer.Option("--db-path", help="SQLite DB path.")] = DEFAULT_DB_PATH,
) -> None:
    """Export the consolidated table enriched with cached Yahoo fields, one row per instrument per exchange."""
    try:
        result = export_enriched(db_path, output=output, format=format, exchange=exchange)
    except Exception as exc:
        error_console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc

    if result.output is not None:
        console.print(
            f"[green]Wrote {result.rows} enriched rows to {escape(str(result.output))} as {result.format}.[/green]"
        )


@mf_app.command("import")
def mf_import(
    mf_instruments: Annotated[
        Path,
        typer.Option("--mf-instruments", help="Zerodha mutual fund instruments CSV path."),
    ] = DEFAULT_ZERODHA_MF_INSTRUMENTS,
    db_path: Annotated[Path, typer.Option("--db-path", help="SQLite DB path.")] = DEFAULT_DB_PATH,
    json_output: Annotated[bool, typer.Option("--json", help="Print the result as JSON on stdout.")] = False,
) -> None:
    """Replace the mutual_funds table with the schemes in Zerodha's mutual fund instruments file."""
    try:
        result = import_mutual_funds(mf_instruments, db_path)
    except Exception as exc:
        (error_console if json_output else console).print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc

    if json_output:
        _emit_json(result)
    else:
        console.print(
            f"[green]Imported {result.rows} mutual funds from {escape(str(result.mf_instruments))} "
            f"into {escape(str(result.db_path))}.[/green]"
        )


@mf_app.command("query")
def mf_query(
    term: Annotated[str, typer.Argument(help="ISIN, Zerodha scheme identifier, scheme name, or AMC fragment.")],
    limit: Annotated[int, typer.Option("--limit", help="Maximum number of matches to display.")] = 20,
    exact: Annotated[
        bool, typer.Option("--exact", help="Match the ISIN, Zerodha identifier, or full name exactly.")
    ] = False,
    db_path: Annotated[Path, typer.Option("--db-path", help="SQLite DB path.")] = DEFAULT_DB_PATH,
    json_output: Annotated[bool, typer.Option("--json", help="Print the result as JSON on stdout.")] = False,
) -> None:
    """Look up a mutual fund by ISIN, Zerodha identifier, name, or AMC."""
    try:
        result = search_mutual_funds(term, db_path, limit=limit, exact=exact)
    except Exception as exc:
        (error_console if json_output else console).print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc

    if json_output:
        _emit_json({"term": term, **dataclasses.asdict(result)})
    else:
        _print_mf_search_result(term, result)


app.add_typer(yahoo_app, name="yahoo")
app.add_typer(mf_app, name="mf")


def main() -> None:
    app()
