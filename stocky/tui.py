"""Full-screen Textual interface over the same code paths as ``stocky rebuild`` and ``stocky yahoo update``."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import typer
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    DirectoryTree,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    RadioButton,
    RadioSet,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from stocky.cli import (
    _COLUMN_LABELS,
    _MATCH_FIELDS,
    _SEARCH_FIELDS,
    PLAIN_PROGRESS_EVERY,
    YAHOO_EXCHANGES,
    _format_percent,
    _format_size,
    _format_timestamp,
    _rebuild_impl,
    _yahoo_cached_rows,
)
from stocky.config import DEFAULT_BHAVCOPY_DIR, DEFAULT_DB_PATH, DEFAULT_ZERODHA_INSTRUMENTS
from stocky.database import DatabaseStatus, InstrumentMatch, read_status, search_instruments
from stocky.export import export_consolidated
from stocky.pipeline import RebuildResult, preview_sources
from stocky.sources import BhavcopyPair, list_bhavcopy_pairs, resolve_bhavcopy_paths
from stocky.yahoo import YahooDataManager, YahooUpdateResult, ticker_column

SOURCE_LATEST = "latest"
SOURCE_DATE = "date"
SOURCE_FILES = "files"
PICK_TARGETS = ("bse", "nse", "zerodha")
SEARCH_LIMIT = 50


class RunCancelled(Exception):
    """Raised from a progress callback to stop a run when the user quits."""


class StockyApp(App[None]):
    """Tabs for rebuild, Yahoo update, status, and search, with one shared log of every run."""

    TITLE = "Stocky"
    CSS = """
    TabbedContent { height: 1fr; }
    #log { height: 10; border: round $primary; }
    .row { height: auto; margin-bottom: 1; }
    .row > * { margin-right: 2; }
    .row Label { padding-top: 1; }
    #source-mode, #pick-target { width: 24; }
    .row Vertical { height: auto; width: 1fr; }
    .row Vertical Label { padding-top: 0; }
    .row Select { width: 24; }
    .row Vertical Select { width: 1fr; }
    .row Checkbox { width: auto; }
    #bse-path, #nse-path, #zerodha-path, #search-term { width: 1fr; }
    #limit { width: 28; }
    #export-path { width: 1fr; }
    #picker { height: 12; border: round $secondary; }
    DataTable { height: auto; max-height: 16; margin-bottom: 1; }
    """
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(
        self,
        *,
        db_path: Path = DEFAULT_DB_PATH,
        input_dir: Path = DEFAULT_BHAVCOPY_DIR,
        zerodha_instruments: Path = DEFAULT_ZERODHA_INSTRUMENTS,
    ) -> None:
        super().__init__()
        self.db_path = db_path
        self.input_dir = input_dir
        self.zerodha_instruments = zerodha_instruments
        self.pairs: list[BhavcopyPair] = []
        self.matches: list[InstrumentMatch] = []
        self.busy = False
        self.status_loading = False
        self.cancel_requested = False

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="rebuild"):
            with TabPane("Rebuild", id="rebuild"), VerticalScroll():
                with Horizontal(classes="row"):
                    with RadioSet(id="source-mode"):
                        yield RadioButton("Latest pair", id=f"mode-{SOURCE_LATEST}", value=True)
                        yield RadioButton("Trade date", id=f"mode-{SOURCE_DATE}")
                        yield RadioButton("Explicit files", id=f"mode-{SOURCE_FILES}")
                    with Vertical():
                        yield Label("Trade date (discovered BSE/NSE pairs)")
                        yield Select([], id="pair", prompt="No bhavcopy pairs found")
                with Horizontal(classes="row"):
                    yield Input(placeholder="BSE bhavcopy path", id="bse-path")
                    yield Input(placeholder="NSE bhavcopy path", id="nse-path")
                    yield Input(
                        str(self.zerodha_instruments), placeholder="Zerodha instruments path", id="zerodha-path"
                    )
                with Horizontal(classes="row"):
                    yield Label("Picked file fills")
                    with RadioSet(id="pick-target"):
                        yield RadioButton("BSE", id="pick-bse", value=True)
                        yield RadioButton("NSE", id="pick-nse")
                        yield RadioButton("Zerodha", id="pick-zerodha")
                yield DirectoryTree(self.input_dir if self.input_dir.is_dir() else Path.cwd(), id="picker")
                with Horizontal(classes="row"):
                    yield Checkbox("Dry run", id="rebuild-dry-run")
                    yield Checkbox("No backup", id="rebuild-no-backup")
                    yield Button("Preview", id="rebuild-preview")
                    yield Button("Rebuild", id="rebuild-run", variant="primary")
                yield DataTable(id="rebuild-preview-table", show_cursor=False)
                yield Static("Idle", id="rebuild-stage")
                yield ProgressBar(total=1, id="rebuild-progress", show_eta=False)
            with TabPane("Yahoo update", id="yahoo"), VerticalScroll():
                with Horizontal(classes="row"):
                    yield Select(
                        [(item, item) for item in YAHOO_EXCHANGES], value="BSE", allow_blank=False, id="exchange"
                    )
                    yield Input(placeholder="Limit (blank = all)", id="limit", type="integer")
                    yield Checkbox("Missing only", id="missing-only")
                with Horizontal(classes="row"):
                    yield Button("Count tickers", id="yahoo-plan")
                    yield Button("Update", id="yahoo-run", variant="primary")
                yield Static("Idle", id="yahoo-stage")
                yield ProgressBar(total=1, id="yahoo-progress")
            with TabPane("Status", id="status"), VerticalScroll():
                with Horizontal(classes="row"):
                    yield Button("Refresh", id="status-refresh")
                    yield Button("Open database folder", id="open-folder")
                with Horizontal(classes="row"):
                    yield Input(placeholder="Export to file (.csv, .json, .parquet)", id="export-path")
                    yield Button("Export", id="export-run")
                yield Static("", id="status-message")
                yield DataTable(id="status-overview", show_cursor=False)
                yield DataTable(id="status-coverage", show_cursor=False)
                yield DataTable(id="status-yahoo", show_cursor=False)
            with TabPane("Search", id="search"), VerticalScroll():
                with Horizontal(classes="row"):
                    yield Input(placeholder="Symbol, ISIN, BSE scrip code, or name fragment", id="search-term")
                    yield Checkbox("Exact", id="search-exact")
                yield Static("", id="search-message")
                yield DataTable(id="search-results", cursor_type="row")
                yield DataTable(id="equivalents", show_cursor=False)
        yield RichLog(id="log", wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = str(self.db_path)
        self.query_one("#rebuild-preview-table", DataTable).add_columns("Source", "File", "Rows")
        self.query_one("#search-results", DataTable).add_columns(*(label for label, _ in _SEARCH_FIELDS))
        self.query_one("#equivalents", DataTable).add_columns("Field", "Value")
        self.refresh_pairs()
        self.refresh_status()
        self.log_line("Ready. Every run logs its progress and errors here.", "dim")

    # Shared helpers

    def log_line(self, message: str, style: str = "") -> None:
        self.query_one("#log", RichLog).write(Text(message, style=style))

    def log_error(self, message: str) -> None:
        self.log_line(message, "bold red")
        self.notify(message, severity="error")

    def start_job(self) -> bool:
        if self.busy:
            self.notify("Another run is still in progress.", severity="warning")
            return False
        if self.status_loading:
            self.notify("Database status is still loading; start the run when it finishes.", severity="warning")
            return False
        self.busy = True
        for button_id in ("#rebuild-run", "#yahoo-run"):
            self.query_one(button_id, Button).disabled = True
        return True

    def finish_job(self) -> None:
        self.busy = False
        for button_id in ("#rebuild-run", "#yahoo-run"):
            self.query_one(button_id, Button).disabled = False
        self.refresh_status()

    async def action_quit(self) -> None:
        if self.busy:
            # Quitting waits for the run's thread, so stop it at its next progress report rather
            # than hanging until a long Yahoo update finishes.
            self.cancel_requested = True
            self.log_line("Stopping the current run, then quitting...", "bold yellow")
        self.exit()

    def check_cancelled(self) -> None:
        if self.cancel_requested:
            raise RunCancelled

    # Rebuild

    def refresh_pairs(self) -> None:
        self.pairs = list_bhavcopy_pairs(self.input_dir) if self.input_dir.is_dir() else []
        select = self.query_one("#pair", Select)
        select.set_options(
            (f"{pair.trade_date.isoformat()}  {pair.bse.name} / {pair.nse.name}", index)
            for index, pair in enumerate(self.pairs)
        )
        if self.pairs:
            select.value = 0

    def source_mode(self) -> str:
        pressed = self.query_one("#source-mode", RadioSet).pressed_button
        return pressed.id.removeprefix("mode-") if pressed is not None and pressed.id else SOURCE_LATEST

    def set_source_mode(self, mode: str) -> None:
        self.query_one(f"#mode-{mode}", RadioButton).value = True

    def rebuild_request(self) -> dict[str, Any]:
        """Translate the form into keyword arguments for ``_rebuild_impl``."""
        zerodha = self.query_one("#zerodha-path", Input).value.strip()
        request: dict[str, Any] = {
            "input_dir": self.input_dir,
            "zerodha_instruments": Path(zerodha) if zerodha else self.zerodha_instruments,
            "db_path": self.db_path,
            "dry_run": self.query_one("#rebuild-dry-run", Checkbox).value,
            "no_backup": self.query_one("#rebuild-no-backup", Checkbox).value,
        }

        mode = self.source_mode()
        if mode == SOURCE_LATEST:
            request["latest"] = True
        elif mode == SOURCE_DATE:
            selected = self.query_one("#pair", Select).value
            if not isinstance(selected, int):
                raise ValueError(f"No BSE/NSE bhavcopy pairs found in {self.input_dir}. Download them first.")
            # Use the discovered files rather than canonical names, as the interactive menu does,
            # so an unzipped or differently cased bhavcopy still resolves.
            pair = self.pairs[selected]
            request["bse_bhavcopy"] = pair.bse
            request["nse_bhavcopy"] = pair.nse
        else:
            bse = self.query_one("#bse-path", Input).value.strip()
            nse = self.query_one("#nse-path", Input).value.strip()
            request["bse_bhavcopy"] = Path(bse) if bse else None
            request["nse_bhavcopy"] = Path(nse) if nse else None
            if request["bse_bhavcopy"] is None or request["nse_bhavcopy"] is None:
                raise ValueError("Pick both a BSE and an NSE bhavcopy for explicit files.")
        return request

    def pick_file(self, path: Path) -> None:
        pressed = self.query_one("#pick-target", RadioSet).pressed_button
        target = pressed.id.removeprefix("pick-") if pressed is not None and pressed.id else PICK_TARGETS[0]
        self.query_one(f"#{target}-path", Input).value = str(path)
        if target != "zerodha":
            self.set_source_mode(SOURCE_FILES)
        self.log_line(f"Picked {path} as the {target.upper() if target != 'zerodha' else 'Zerodha'} input.")

    @on(DirectoryTree.FileSelected, "#picker")
    def on_picker_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.pick_file(event.path)

    @on(Button.Pressed, "#rebuild-preview")
    def on_rebuild_preview(self) -> None:
        try:
            request = self.rebuild_request()
            paths = resolve_bhavcopy_paths(
                bse_bhavcopy=request.get("bse_bhavcopy"),
                nse_bhavcopy=request.get("nse_bhavcopy"),
                input_dir=self.input_dir,
                zerodha=request["zerodha_instruments"],
                latest=request.get("latest", False),
            )
            preview = preview_sources(paths)
        except Exception as exc:
            self.log_error(f"Cannot preview: {exc}")
            return

        table = self.query_one("#rebuild-preview-table", DataTable)
        table.clear()
        table.add_row("BSE bhavcopy", str(preview.bse_bhavcopy), str(preview.bse_rows))
        table.add_row("NSE bhavcopy", str(preview.nse_bhavcopy), str(preview.nse_rows))
        table.add_row("Zerodha instruments", str(preview.zerodha_instruments), str(preview.zerodha_rows))
        table.add_row("Database", str(self.db_path), "-")
        self.log_line(f"Previewed {preview.bse_bhavcopy.name} and {preview.nse_bhavcopy.name}.")

    @on(Button.Pressed, "#rebuild-run")
    def on_rebuild_run(self) -> None:
        try:
            request = self.rebuild_request()
        except ValueError as exc:
            self.log_error(str(exc))
            return
        if not self.start_job():
            return
        self.query_one("#rebuild-progress", ProgressBar).update(total=None, progress=0)
        self.log_line("Rebuild started.", "bold")
        self.run_rebuild(request)

    @work(thread=True, group="job")
    def run_rebuild(self, request: dict[str, Any]) -> None:
        def progress(stage: str) -> None:
            self.check_cancelled()
            self.call_from_thread(self.on_rebuild_stage, stage)

        try:
            result = _rebuild_impl(**request, progress=progress)
        except RunCancelled:
            return
        except Exception as exc:
            self.call_from_thread(self.on_rebuild_failed, exc)
        else:
            self.call_from_thread(self.on_rebuild_done, result)

    def on_rebuild_stage(self, stage: str) -> None:
        self.query_one("#rebuild-stage", Static).update(f"{stage}...")
        self.log_line(f"{stage}...")

    def on_rebuild_failed(self, exc: Exception) -> None:
        self.query_one("#rebuild-stage", Static).update("Rebuild failed")
        self.query_one("#rebuild-progress", ProgressBar).update(total=1, progress=0)
        self.log_error(f"Rebuild failed: {exc}")
        self.finish_job()

    def on_rebuild_done(self, result: RebuildResult) -> None:
        self.query_one("#rebuild-stage", Static).update("Dry run complete" if result.dry_run else "Rebuild complete")
        self.query_one("#rebuild-progress", ProgressBar).update(total=1, progress=1)
        backup = f"; backup {result.backup_path}" if result.backup_path else ""
        verb = "Validated" if result.dry_run else "Wrote"
        self.log_line(f"{verb} {result.rows} rows to {result.db_path}{backup}.", "bold green")
        if result.diff is not None:
            diff = result.diff
            self.log_line(
                f"Against the previous table: {diff.added} added, {diff.removed} removed, "
                f"{diff.changed} changed, {diff.unchanged} unchanged."
            )
        self.finish_job()

    # Yahoo update

    def yahoo_request(self) -> dict[str, Any]:
        raw_limit = self.query_one("#limit", Input).value.strip()
        limit = int(raw_limit) if raw_limit else None
        if limit is not None and limit < 1:
            raise ValueError("Enter a positive limit, or leave it blank for all tickers.")
        return {
            "exchange": str(self.query_one("#exchange", Select).value),
            "limit": limit,
            "missing_only": self.query_one("#missing-only", Checkbox).value,
        }

    @on(Button.Pressed, "#yahoo-plan")
    def on_yahoo_plan(self) -> None:
        try:
            request = self.yahoo_request()
            planned = YahooDataManager(self.db_path).update_data(**request, dry_run=True)
        except Exception as exc:
            self.log_error(str(exc))
            return
        message = f"{planned.processed} tickers to fetch ({request['exchange']}, {ticker_column(request['exchange'])})."
        self.query_one("#yahoo-stage", Static).update(message)
        self.log_line(message)

    @on(Button.Pressed, "#yahoo-run")
    def on_yahoo_run(self) -> None:
        try:
            request = self.yahoo_request()
        except ValueError as exc:
            self.log_error(str(exc))
            return
        if not self.start_job():
            return
        self.query_one("#yahoo-progress", ProgressBar).update(total=None, progress=0)
        self.log_line(f"Yahoo update started ({request['exchange']}, {ticker_column(request['exchange'])}).", "bold")
        self.run_yahoo(request)

    @work(thread=True, group="job")
    def run_yahoo(self, request: dict[str, Any]) -> None:
        def progress(index: int, total: int, written: int, skipped: int) -> None:
            self.check_cancelled()
            self.call_from_thread(self.on_yahoo_progress, index, total, written, skipped)

        try:
            result = YahooDataManager(self.db_path).update_data(**request, progress=progress)
        except RunCancelled:
            return
        except Exception as exc:
            self.call_from_thread(self.on_yahoo_failed, exc)
        else:
            self.call_from_thread(self.on_yahoo_done, result)

    def on_yahoo_progress(self, index: int, total: int, written: int, skipped: int) -> None:
        self.query_one("#yahoo-progress", ProgressBar).update(total=total, progress=index)
        summary = f"{index}/{total} processed; {written} written; {skipped} skipped"
        self.query_one("#yahoo-stage", Static).update(summary)
        if index % PLAIN_PROGRESS_EVERY == 0 or index == total:
            self.log_line(summary)

    def on_yahoo_failed(self, exc: Exception) -> None:
        self.query_one("#yahoo-stage", Static).update("Yahoo update failed")
        self.log_error(str(exc))
        self.finish_job()

    def on_yahoo_done(self, result: YahooUpdateResult) -> None:
        if result.processed == 0:
            self.query_one("#yahoo-progress", ProgressBar).update(total=1, progress=1)
        message = f"Processed {result.processed}; wrote {result.written}; skipped {result.skipped}."
        self.query_one("#yahoo-stage", Static).update(message)
        self.log_line(message, "bold green")
        self.finish_job()

    # Status, database location, and export

    def refresh_status(self) -> None:
        # read_status scans the whole database and can take many seconds on a full one, so it
        # runs off the UI thread; runs wait for it because they write to the same file. It is a
        # daemon thread rather than a worker so quitting does not wait for a read-only query.
        self.status_loading = True
        self.query_one("#status-message", Static).update(Text("Reading database status...", style="dim"))
        threading.Thread(target=self.load_status, name="stocky-status", daemon=True).start()

    def load_status(self) -> None:
        try:
            status: DatabaseStatus | Exception = read_status(self.db_path)
        except Exception as exc:
            status = exc
        if self.is_running:
            self.call_from_thread(self.show_status, status)

    def show_status(self, status: DatabaseStatus | Exception) -> None:
        self.status_loading = False
        tables = [self.query_one(f"#status-{name}", DataTable) for name in ("overview", "coverage", "yahoo")]
        for table in tables:
            table.clear(columns=True)
        message = self.query_one("#status-message", Static)
        if isinstance(status, Exception):
            message.update(Text(str(status), style="red"))
            return
        message.update(f"Database: {status.db_path}")

        overview, coverage, yahoo = tables
        overview.add_columns("Field", "Value")
        overview.add_row("Database", str(status.db_path))
        overview.add_row("Size", _format_size(status.db_size_bytes))
        overview.add_row("Consolidated rows", str(status.consolidated_rows))
        for ins_type, count in status.ins_type_counts.items():
            overview.add_row(f"  {ins_type}", str(count))
        overview.add_row("Yahoo responses", str(status.yahoo_rows))
        for exchange, count in status.yahoo_exchange_counts.items():
            overview.add_row(f"  {exchange}", str(count))

        coverage.add_columns("Column", "Populated", "Missing", "Coverage")
        for entry in status.coverage:
            coverage.add_row(
                _COLUMN_LABELS.get(entry.column, entry.column),
                str(entry.populated),
                str(status.consolidated_rows - entry.populated),
                _format_percent(entry.populated, status.consolidated_rows),
            )

        yahoo.add_columns("Yahoo cache", "Value")
        yahoo.add_row("Newest fetch", _format_timestamp(status.yahoo_newest_fetch))
        yahoo.add_row("Oldest fetch", _format_timestamp(status.yahoo_oldest_fetch))
        for label, value in _yahoo_cached_rows(status):
            yahoo.add_row(label, value)

    @on(Button.Pressed, "#status-refresh")
    def on_status_refresh(self) -> None:
        if self.busy or self.status_loading:
            self.notify("Wait for the current run or status read to finish.", severity="warning")
            return
        self.refresh_status()

    @on(Button.Pressed, "#open-folder")
    def on_open_folder(self) -> None:
        folder = self.db_path.resolve().parent
        if not folder.is_dir():
            self.log_error(f"Database folder not found: {folder}. Run a rebuild first.")
            return
        # typer.launch returns non-zero instead of raising when no opener (such as xdg-open) exists.
        if typer.launch(str(folder)) != 0:
            self.log_error(f"Could not open a file manager. The database folder is {folder}.")
            return
        self.log_line(f"Opened {folder}.")

    @on(Button.Pressed, "#export-run")
    @on(Input.Submitted, "#export-path")
    def on_export(self) -> None:
        raw_output = self.query_one("#export-path", Input).value.strip()
        if not raw_output:
            self.log_error("Enter a file path to export to, such as data/output/consolidated.csv.")
            return
        try:
            result = export_consolidated(self.db_path, output=Path(raw_output), format=None)
        except Exception as exc:
            self.log_error(f"Export failed: {exc}")
            return
        self.log_line(f"Exported {result.rows} rows to {result.output} as {result.format}.", "bold green")

    # Search and equivalents

    @on(Input.Submitted, "#search-term")
    def on_search(self) -> None:
        term = self.query_one("#search-term", Input).value.strip()
        results = self.query_one("#search-results", DataTable)
        message = self.query_one("#search-message", Static)
        results.clear()
        self.query_one("#equivalents", DataTable).clear()
        self.matches = []
        try:
            result = search_instruments(
                term, self.db_path, limit=SEARCH_LIMIT, exact=self.query_one("#search-exact", Checkbox).value
            )
        except Exception as exc:
            message.update(Text(str(exc), style="red"))
            return

        self.matches = result.matches
        for match in result.matches:
            results.add_row(*(getattr(match, field) or "-" for _, field in _SEARCH_FIELDS))

        if result.total == 0:
            message.update(Text(f"No matches for '{term}'.", style="yellow"))
        elif result.fuzzy:
            message.update("No direct matches; showing closest names. Select a row for its equivalents.")
        elif result.total > len(result.matches):
            message.update(f"Showing {len(result.matches)} of {result.total} matches. Refine the search to narrow it.")
        else:
            message.update(f"{result.total} matches. Select a row for its equivalents.")
        if len(result.matches) == 1:
            self.show_equivalents(result.matches[0])

    @on(DataTable.RowSelected, "#search-results")
    def on_search_row_selected(self, event: DataTable.RowSelected) -> None:
        self.show_equivalents(self.matches[event.cursor_row])

    def show_equivalents(self, match: InstrumentMatch) -> None:
        table = self.query_one("#equivalents", DataTable)
        table.clear()
        for label, field in _MATCH_FIELDS:
            table.add_row(label, getattr(match, field) or "-")
