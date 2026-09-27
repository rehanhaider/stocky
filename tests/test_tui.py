import asyncio
import sqlite3
import sys
import threading
import time
from datetime import date

from textual.widgets import Button, Checkbox, DataTable, Input, ProgressBar, RadioButton, RichLog, Select, Static

import stocky.cli as cli
import stocky.tui as tui
from stocky.cli import app
from stocky.tui import StockyApp
from stocky.yahoo import YahooUpdateResult


def _run(stocky_app, scenario) -> None:
    async def main() -> None:
        async with stocky_app.run_test(size=(160, 60)) as pilot:
            await pilot.pause()
            await _settle(stocky_app, pilot)
            await scenario(stocky_app, pilot)

    asyncio.run(main())


async def _press(stocky_app, pilot, button_id: str) -> None:
    stocky_app.query_one(button_id, Button).press()
    await pilot.pause()
    await _settle(stocky_app, pilot)


async def _settle(stocky_app, pilot) -> None:
    """Wait for runs and status reads; DirectoryTree keeps a loader worker alive for the app's lifetime."""
    while stocky_app.status_loading or (
        jobs := [worker for worker in stocky_app.workers if worker.group == "job" and not worker.is_finished]
    ):
        if stocky_app.status_loading:
            await pilot.pause(0.02)
        else:
            # An empty list means "every worker" to wait_for_complete, hence the loop condition.
            await stocky_app.workers.wait_for_complete(jobs)
        await pilot.pause()


def _squash(text: str) -> str:
    return "".join(text.split())


def _log_text(stocky_app) -> str:
    """Return the log without whitespace, since RichLog wraps long paths across lines."""
    return _squash("".join(line.text for line in stocky_app.query_one("#log", RichLog).lines))


def _static_text(stocky_app, widget_id: str) -> str:
    return str(stocky_app.query_one(widget_id, Static).render())


def _rows(table: DataTable) -> list[list[str]]:
    return [[str(cell) for cell in table.get_row_at(index)] for index in range(table.row_count)]


def test_rebuild_latest_pair_writes_database_and_refreshes_status(tmp_path, market_csv_builder) -> None:
    paths = market_csv_builder(tmp_path / "inputs", trade_date=date(2021, 9, 30))
    db_path = tmp_path / "stocky.db"
    stocky_app = StockyApp(db_path=db_path, input_dir=paths.bse.parent, zerodha_instruments=paths.zerodha)

    async def scenario(stocky_app, pilot) -> None:
        stocky_app.query_one("#rebuild-no-backup", Checkbox).value = True
        await pilot.pause()
        await _press(stocky_app, pilot, "#rebuild-run")

        log = _log_text(stocky_app)
        assert _squash("Loading BSE bhavcopy...") in log
        assert _squash("Writing consolidated table...") in log
        assert _squash(f"Wrote 1 rows to {db_path}") in log
        assert _static_text(stocky_app, "#rebuild-stage") == "Rebuild complete"
        assert stocky_app.query_one("#rebuild-progress", ProgressBar).percentage == 1
        assert ["Consolidated rows", "1"] in _rows(stocky_app.query_one("#status-overview", DataTable))
        assert not stocky_app.busy

    _run(stocky_app, scenario)

    with sqlite3.connect(db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM consolidated").fetchone()[0] == 1


def test_rebuild_trade_date_previews_the_selected_pair(tmp_path, market_csv_builder) -> None:
    inputs = tmp_path / "inputs"
    market_csv_builder(inputs, trade_date=date(2021, 9, 30))
    older = market_csv_builder(inputs, trade_date=date(2021, 5, 3))
    db_path = tmp_path / "stocky.db"
    stocky_app = StockyApp(db_path=db_path, input_dir=inputs, zerodha_instruments=older.zerodha)

    async def scenario(stocky_app, pilot) -> None:
        stocky_app.query_one("#mode-date", RadioButton).value = True
        stocky_app.query_one("#pair", Select).value = 1
        await pilot.pause()
        await _press(stocky_app, pilot, "#rebuild-preview")

        assert _rows(stocky_app.query_one("#rebuild-preview-table", DataTable)) == [
            ["BSE bhavcopy", str(older.bse), "1"],
            ["NSE bhavcopy", str(older.nse), "1"],
            ["Zerodha instruments", str(older.zerodha), "2"],
            ["Database", str(db_path), "-"],
        ]
        assert not db_path.exists()

    _run(stocky_app, scenario)


def test_rebuild_explicit_files_come_from_the_file_picker(tmp_path, market_csv_builder) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    db_path = tmp_path / "stocky.db"
    stocky_app = StockyApp(db_path=db_path, input_dir=tmp_path / "missing", zerodha_instruments=paths.zerodha)

    async def scenario(stocky_app, pilot) -> None:
        stocky_app.pick_file(paths.bse)
        stocky_app.query_one("#pick-nse", RadioButton).value = True
        await pilot.pause()
        stocky_app.pick_file(paths.nse)
        stocky_app.query_one("#rebuild-dry-run", Checkbox).value = True
        await pilot.pause()

        assert stocky_app.source_mode() == "files"
        assert stocky_app.query_one("#bse-path", Input).value == str(paths.bse)
        assert stocky_app.query_one("#nse-path", Input).value == str(paths.nse)

        await _press(stocky_app, pilot, "#rebuild-run")

        assert _squash("Validated 1 rows") in _log_text(stocky_app)
        assert _static_text(stocky_app, "#rebuild-stage") == "Dry run complete"

    _run(stocky_app, scenario)

    assert not db_path.exists()


def test_rebuild_explicit_files_require_both_bhavcopies(tmp_path, market_csv_builder) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    stocky_app = StockyApp(db_path=tmp_path / "stocky.db", input_dir=paths.bse.parent)

    async def scenario(stocky_app, pilot) -> None:
        stocky_app.pick_file(paths.bse)
        await pilot.pause()
        await _press(stocky_app, pilot, "#rebuild-run")

        assert _squash("Pick both a BSE and an NSE bhavcopy for explicit files.") in _log_text(stocky_app)
        assert not stocky_app.busy

    _run(stocky_app, scenario)


def test_rebuild_validation_failure_is_shown_inline(tmp_path, market_csv_builder) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    paths.bse.write_text("NOT_A_BHAVCOPY\nx\n", encoding="utf-8")
    db_path = tmp_path / "stocky.db"
    stocky_app = StockyApp(db_path=db_path, input_dir=paths.bse.parent, zerodha_instruments=paths.zerodha)

    async def scenario(stocky_app, pilot) -> None:
        await _press(stocky_app, pilot, "#rebuild-run")

        log = _log_text(stocky_app)
        assert _squash("Rebuild failed: BSE bhavcopy is missing required columns") in log
        assert _static_text(stocky_app, "#rebuild-stage") == "Rebuild failed"
        assert not stocky_app.query_one("#rebuild-run", Button).disabled

    _run(stocky_app, scenario)

    assert not db_path.exists()


def test_rebuild_preview_reports_an_empty_input_directory(tmp_path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    stocky_app = StockyApp(db_path=tmp_path / "stocky.db", input_dir=inputs)

    async def scenario(stocky_app, pilot) -> None:
        stocky_app.query_one("#mode-date", RadioButton).value = True
        await pilot.pause()
        await _press(stocky_app, pilot, "#rebuild-preview")

        assert _squash(f"No BSE/NSE bhavcopy pairs found in {inputs}") in _log_text(stocky_app)

    _run(stocky_app, scenario)


def test_rebuild_is_refused_while_a_run_or_status_read_is_in_progress(
    tmp_path, market_csv_builder, monkeypatch
) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    stocky_app = StockyApp(db_path=tmp_path / "stocky.db", input_dir=paths.bse.parent)
    calls = []
    monkeypatch.setattr(stocky_app, "run_rebuild", lambda request: calls.append(request))

    async def scenario(stocky_app, pilot) -> None:
        stocky_app.busy = True
        stocky_app.on_rebuild_run()
        stocky_app.busy = False
        # A status read holds the database open, so a run would race it for the write lock.
        stocky_app.status_loading = True
        stocky_app.on_rebuild_run()
        await pilot.pause()

        assert calls == []
        stocky_app.status_loading = False
        stocky_app.on_rebuild_run()
        assert len(calls) == 1

    _run(stocky_app, scenario)


def test_yahoo_update_drives_the_progress_bar(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "stocky.db"
    calls = []

    class FakeManager:
        def __init__(self, selected_db_path) -> None:
            assert selected_db_path == db_path

        def update_data(self, **options):
            calls.append({key: value for key, value in options.items() if key != "progress"})
            if options.get("dry_run"):
                return YahooUpdateResult(processed=3, written=0, skipped=0, dry_run=True)
            options["progress"](1, 3, 1, 0)
            options["progress"](2, 3, 1, 1)
            options["progress"](3, 3, 2, 1)
            return YahooUpdateResult(processed=3, written=2, skipped=1, dry_run=False)

    monkeypatch.setattr(tui, "YahooDataManager", FakeManager)
    stocky_app = StockyApp(db_path=db_path, input_dir=tmp_path)

    async def scenario(stocky_app, pilot) -> None:
        stocky_app.query_one("#exchange", Select).value = "NSE"
        stocky_app.query_one("#key", Select).value = "nse_symbol"
        stocky_app.query_one("#limit", Input).value = "3"
        stocky_app.query_one("#missing-only", Checkbox).value = True
        await pilot.pause()

        await _press(stocky_app, pilot, "#yahoo-plan")
        assert _static_text(stocky_app, "#yahoo-stage") == "3 symbols to fetch (NSE, key nse_symbol)."

        await _press(stocky_app, pilot, "#yahoo-run")
        bar = stocky_app.query_one("#yahoo-progress", ProgressBar)
        assert (bar.progress, bar.total) == (3, 3)
        log = _log_text(stocky_app)
        assert _squash("3/3 processed; 2 written; 1 skipped") in log
        assert _squash("2/3 processed") not in log
        assert _squash("Processed 3; wrote 2; skipped 1.") in log

    _run(stocky_app, scenario)

    request = {"exchange": "NSE", "key": "nse_symbol", "limit": 3, "missing_only": True}
    assert calls == [request | {"dry_run": True}, request]


def test_quitting_stops_a_running_yahoo_update(tmp_path, monkeypatch) -> None:
    started = threading.Event()
    outcome = []

    class EndlessManager:
        def __init__(self, selected_db_path) -> None:
            pass

        def update_data(self, **options):
            try:
                for index in range(1, 100_000):
                    options["progress"](index, 100_000, index, 0)
                    started.set()
                    time.sleep(0.001)
            except tui.RunCancelled:
                outcome.append(index)
                raise
            outcome.append("finished")
            return YahooUpdateResult(processed=100_000, written=100_000, skipped=0, dry_run=False)

    monkeypatch.setattr(tui, "YahooDataManager", EndlessManager)
    stocky_app = StockyApp(db_path=tmp_path / "stocky.db", input_dir=tmp_path)

    async def scenario(stocky_app, pilot) -> None:
        stocky_app.query_one("#yahoo-run", Button).press()
        await pilot.pause()
        while not started.is_set():
            await pilot.pause(0.01)
        await stocky_app.action_quit()

    _run(stocky_app, scenario)

    assert len(outcome) == 1
    assert outcome[0] != "finished"


def test_yahoo_update_reports_a_missing_database(tmp_path) -> None:
    db_path = tmp_path / "missing.db"
    stocky_app = StockyApp(db_path=db_path, input_dir=tmp_path)

    async def scenario(stocky_app, pilot) -> None:
        await _press(stocky_app, pilot, "#yahoo-run")

        assert _squash(f"Database not found: {db_path}") in _log_text(stocky_app)
        assert _static_text(stocky_app, "#yahoo-stage") == "Yahoo update failed"
        assert not stocky_app.busy

    _run(stocky_app, scenario)


def test_yahoo_update_rejects_a_zero_limit(tmp_path, monkeypatch) -> None:
    stocky_app = StockyApp(db_path=tmp_path / "stocky.db", input_dir=tmp_path)
    monkeypatch.setattr(stocky_app, "run_yahoo", lambda request: (_ for _ in ()).throw(AssertionError("ran")))

    async def scenario(stocky_app, pilot) -> None:
        stocky_app.query_one("#limit", Input).value = "0"
        await pilot.pause()
        await _press(stocky_app, pilot, "#yahoo-run")

        assert _squash("Enter a positive limit, or leave it blank for all symbols.") in _log_text(stocky_app)

    _run(stocky_app, scenario)


def test_status_tab_shows_tables_or_the_missing_database(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"

    async def missing(stocky_app, pilot) -> None:
        assert "Database not found" in _static_text(stocky_app, "#status-message")
        assert stocky_app.query_one("#status-overview", DataTable).row_count == 0

    _run(StockyApp(db_path=db_path, input_dir=tmp_path), missing)

    seed_consolidated(db_path)

    async def present(stocky_app, pilot) -> None:
        assert ["Consolidated rows", "4"] in _rows(stocky_app.query_one("#status-overview", DataTable))
        assert ["Yahoo symbol", "2", "2", "50.0%"] in _rows(stocky_app.query_one("#status-coverage", DataTable))
        assert ["Consolidated symbols cached", "0/2 (0.0%)"] in _rows(stocky_app.query_one("#status-yahoo", DataTable))

    _run(StockyApp(db_path=db_path, input_dir=tmp_path), present)


def test_export_writes_the_consolidated_table(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)
    output = tmp_path / "out" / "consolidated.csv"

    async def scenario(stocky_app, pilot) -> None:
        await _press(stocky_app, pilot, "#export-run")
        assert _squash("Enter a file path to export to") in _log_text(stocky_app)

        stocky_app.query_one("#export-path", Input).value = str(output)
        await _press(stocky_app, pilot, "#export-run")
        assert _squash(f"Exported 4 rows to {output} as csv.") in _log_text(stocky_app)

    _run(StockyApp(db_path=db_path, input_dir=tmp_path), scenario)

    assert output.read_text(encoding="utf-8").splitlines()[0].startswith("isin,")


def test_open_folder_launches_the_database_directory(tmp_path, seed_consolidated, monkeypatch) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)
    launched = []
    monkeypatch.setattr(tui.typer, "launch", lambda target: launched.append(target) or 0)

    async def scenario(stocky_app, pilot) -> None:
        await _press(stocky_app, pilot, "#open-folder")

    _run(StockyApp(db_path=db_path, input_dir=tmp_path), scenario)

    assert launched == [str(tmp_path.resolve())]


def test_open_folder_reports_a_missing_directory(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "nowhere" / "stocky.db"
    monkeypatch.setattr(tui.typer, "launch", lambda target: (_ for _ in ()).throw(AssertionError(target)))

    async def scenario(stocky_app, pilot) -> None:
        await _press(stocky_app, pilot, "#open-folder")
        assert _squash("Database folder not found") in _log_text(stocky_app)

    _run(StockyApp(db_path=db_path, input_dir=tmp_path), scenario)


def test_search_lists_matches_and_shows_equivalents(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    async def scenario(stocky_app, pilot) -> None:
        search = stocky_app.query_one("#search-term", Input)
        search.value = "INFY"
        await search.action_submit()
        await pilot.pause()

        results = stocky_app.query_one("#search-results", DataTable)
        assert [row[2] for row in _rows(results)] == ["INFY", "INFYBEES"]
        assert "2 matches" in _static_text(stocky_app, "#search-message")

        results.move_cursor(row=1)
        results.action_select_cursor()
        await pilot.pause()
        assert ["ISIN", "INE999Z01019"] in _rows(stocky_app.query_one("#equivalents", DataTable))

        stocky_app.query_one("#search-exact", Checkbox).value = True
        search.value = "500325"
        await search.action_submit()
        await pilot.pause()
        assert ["BSE name", "RELIANCE INDUSTRIES"] in _rows(stocky_app.query_one("#equivalents", DataTable))

        search.value = "zzzzqqq"
        await search.action_submit()
        await pilot.pause()
        assert "No matches for 'zzzzqqq'." in _static_text(stocky_app, "#search-message")
        assert results.row_count == 0

    _run(StockyApp(db_path=db_path, input_dir=tmp_path), scenario)


def test_tui_command_launches_the_app(tmp_path, monkeypatch, runner) -> None:
    launched = []
    monkeypatch.setattr(
        StockyApp, "run", lambda self: launched.append((self.db_path, self.input_dir, self.zerodha_instruments))
    )

    result = runner.invoke(
        app,
        [
            "tui",
            "--db-path",
            str(tmp_path / "x.db"),
            "--input-dir",
            str(tmp_path),
            "--zerodha-instruments",
            str(tmp_path / "z.csv"),
        ],
    )

    assert result.exit_code == 0
    assert launched == [(tmp_path / "x.db", tmp_path, tmp_path / "z.csv")]


def test_tui_command_explains_how_to_install_textual(monkeypatch, runner) -> None:
    monkeypatch.delitem(sys.modules, "stocky.tui")
    monkeypatch.setitem(sys.modules, "textual", None)

    result = runner.invoke(cli.app, ["tui"])

    assert result.exit_code == 1
    assert "uv sync --extra tui" in " ".join(result.stderr.split())
