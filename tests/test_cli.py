import json
import sqlite3
import sys
import types
from datetime import date
from zipfile import ZipFile

import pytest
import typer

import stocky.cli as cli
from stocky import __version__
from stocky.cli import app
from stocky.database import connect, encode_response_json, initialize_database, upsert_yahoo_response
from stocky.sources import bse_filename_for_date, nse_filename_for_date
from stocky.yahoo import YahooUpdateResult


class _FakeTicker:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    @property
    def all_modules(self):
        return {
            self.symbol: {
                "quoteType": {"quoteType": "EQUITY", "longName": self.symbol},
                "price": {"regularMarketPrice": 100},
            }
        }


def _use_fake_yahooquery(monkeypatch) -> None:
    fake_module = types.ModuleType("yahooquery")
    fake_module.Ticker = _FakeTicker
    monkeypatch.setitem(sys.modules, "yahooquery", fake_module)


def _seed_cached_yahoo_responses(db_path, symbols, suffix: str = "BO") -> None:
    initialize_database(db_path)
    with connect(db_path) as con:
        for symbol in symbols:
            upsert_yahoo_response(
                con,
                yahoo_symbol=f"{symbol}.{suffix}",
                symbol=symbol,
                exchange="BSE",
                response_json=encode_response_json({"price": 1}),
                source="test",
            )


def _use_interactive_defaults(monkeypatch, paths, db_path) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(cli, "DEFAULT_BHAVCOPY_DIR", paths.bse.parent)
    monkeypatch.setattr(cli, "DEFAULT_ZERODHA_INSTRUMENTS", paths.zerodha)
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", db_path)


def _rebuild_path_args(paths, db_path) -> list[str]:
    return [
        "--bse-bhavcopy",
        str(paths.bse),
        "--nse-bhavcopy",
        str(paths.nse),
        "--zerodha-instruments",
        str(paths.zerodha),
        "--db-path",
        str(db_path),
    ]


def test_rebuild_dry_run_prints_summary_without_creating_database(tmp_path, market_csv_builder, runner) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    db_path = tmp_path / "fresh.db"

    result = runner.invoke(app, ["rebuild", *_rebuild_path_args(paths, db_path), "--dry-run"])

    assert result.exit_code == 0
    assert "Rebuild summary" in result.stdout
    assert "Dry run" in result.stdout
    assert "True" in result.stdout
    assert not db_path.exists()


def test_rebuild_json_prints_result(tmp_path, market_csv_builder, runner) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    db_path = tmp_path / "fresh.db"

    result = runner.invoke(app, ["rebuild", *_rebuild_path_args(paths, db_path), "--dry-run", "--json"])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["rows"] == 1
    assert payload["db_path"] == str(db_path)
    assert payload["dry_run"] is True


def test_rebuild_dry_run_shows_row_changes_and_samples_against_the_current_database(
    tmp_path, market_csv_builder, seed_consolidated, runner
) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = runner.invoke(app, ["rebuild", *_rebuild_path_args(paths, db_path), "--dry-run"], env={"COLUMNS": "200"})

    assert result.exit_code == 0
    assert "Row changes" in result.stdout
    assert "The current database records no source provenance." in result.stdout
    assert "Sample differences" in result.stdout
    assert "BSE symbol: RELIANCE -> -" in result.stdout
    assert "Rebuilt from" in result.stdout
    assert "2021-05-03" in result.stdout


def test_rebuild_json_carries_sources_and_a_sample_limited_diff(
    tmp_path, market_csv_builder, seed_consolidated, runner
) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = runner.invoke(
        app, ["rebuild", *_rebuild_path_args(paths, db_path), "--dry-run", "--json", "--sample", "1"]
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["diff"]["removed"] == 3
    assert len(payload["diff"]["sample_removed"]) == 1
    assert payload["diff"]["sample_changed"][0]["changes"]["yq_bo"] == ["RELIANCE.BO", None]
    assert [source["role"] for source in payload["sources"]] == ["bse_bhavcopy", "nse_bhavcopy", "zerodha_instruments"]
    assert payload["build_id"] is None


def test_status_shows_the_sources_of_the_latest_rebuild(tmp_path, market_csv_builder, runner) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    db_path = tmp_path / "stocky.db"
    runner.invoke(app, ["rebuild", *_rebuild_path_args(paths, db_path), "--no-backup"])

    result = runner.invoke(app, ["status", "--db-path", str(db_path)], env={"COLUMNS": "200"})
    payload = json.loads(runner.invoke(app, ["status", "--db-path", str(db_path), "--json"]).stdout)

    assert result.exit_code == 0
    assert "by Stocky" in result.stdout
    assert "NSE-cm03MAY2021bhav.csv" in result.stdout
    assert payload["provenance"]["consolidated_rows"] == 1
    assert {source["trade_date"] for source in payload["provenance"]["sources"]} == {"2021-05-03", None}


def test_status_says_when_no_provenance_is_recorded(tmp_path, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = runner.invoke(app, ["status", "--db-path", str(db_path)])

    assert "No source provenance recorded" in result.stdout


def test_rebuild_with_explicit_paths_writes_database_without_backup(
    tmp_path, market_csv_builder, seed_consolidated, runner
) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = runner.invoke(app, ["rebuild", *_rebuild_path_args(paths, db_path), "--no-backup"])

    assert result.exit_code == 0
    assert "Rows" in result.stdout
    assert "Backup" in result.stdout
    assert "None" in result.stdout
    with sqlite3.connect(db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM consolidated").fetchone()[0] == 1


def test_rebuild_latest_uses_input_directory(tmp_path, market_csv_builder, monkeypatch, runner) -> None:
    paths = market_csv_builder(tmp_path / "inputs", trade_date=date(2021, 9, 30))
    db_path = tmp_path / "latest.db"
    calls = []
    real_resolver = cli.resolve_bhavcopy_paths

    def recording_resolver(**options):
        calls.append(options.copy())
        return real_resolver(**options)

    monkeypatch.setattr(cli, "resolve_bhavcopy_paths", recording_resolver)

    result = runner.invoke(
        app,
        [
            "rebuild",
            "--latest",
            "--input-dir",
            str(paths.bse.parent),
            "--zerodha-instruments",
            str(paths.zerodha),
            "--db-path",
            str(db_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert calls[0]["latest"] is True
    assert calls[0]["input_dir"] == paths.bse.parent
    assert not db_path.exists()


def test_rebuild_date_resolves_bhavcopy_names(tmp_path, market_csv_builder, monkeypatch, runner) -> None:
    paths = market_csv_builder(tmp_path / "inputs", trade_date=date(2021, 5, 3))
    db_path = tmp_path / "dated.db"
    calls = []
    real_resolver = cli.resolve_bhavcopy_paths

    def recording_resolver(**options):
        calls.append(options.copy())
        return real_resolver(**options)

    monkeypatch.setattr(cli, "resolve_bhavcopy_paths", recording_resolver)

    result = runner.invoke(
        app,
        [
            "rebuild",
            "--date",
            "2021-05-03",
            "--input-dir",
            str(paths.bse.parent),
            "--zerodha-instruments",
            str(paths.zerodha),
            "--db-path",
            str(db_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert calls[0]["trade_date"] == date(2021, 5, 3)
    assert calls[0]["input_dir"] == paths.bse.parent


def test_rebuild_missing_files_reports_failure(tmp_path, runner) -> None:
    missing = tmp_path / "missing.csv"

    result = runner.invoke(
        app,
        [
            "rebuild",
            "--bse-bhavcopy",
            str(missing),
            "--nse-bhavcopy",
            str(missing),
            "--zerodha-instruments",
            str(missing),
            "--db-path",
            str(tmp_path / "stocky.db"),
        ],
    )

    assert result.exit_code == 1
    assert "Required input files are missing" in result.stdout


def test_status_prints_seeded_database(tmp_path, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = runner.invoke(app, ["status", "--db-path", str(db_path)])

    assert result.exit_code == 0
    assert "Database overview" in result.stdout
    assert "Consolidated coverage" in result.stdout
    assert "Yahoo cache" in result.stdout
    assert "4" in result.stdout
    assert "50.0%" in result.stdout
    assert "None" in result.stdout
    assert "Yahoo NSE tickers cached" in result.stdout
    assert "Yahoo BSE tickers cached" in result.stdout


def test_status_json_prints_database_status(tmp_path, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = runner.invoke(app, ["status", "--db-path", str(db_path), "--json"])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["db_path"] == str(db_path)
    assert payload["consolidated_rows"] == 4
    assert payload["coverage"][0] == {"column": "isin", "populated": 4}
    assert payload["yahoo_cached_ns"] == 0
    assert payload["yahoo_cached_bo"] == 0
    assert "yahoo_cached_yq_symbols" not in payload


def test_status_prints_cached_tickers_per_exchange(tmp_path, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)
    _seed_cached_yahoo_responses(db_path, ["RELIANCE", "INFY"], suffix="NS")
    _seed_cached_yahoo_responses(db_path, ["INFY"], suffix="BO")

    result = runner.invoke(app, ["status", "--db-path", str(db_path)], env={"COLUMNS": "200"})

    lines = {" ".join(line.replace("│", " ").split()) for line in result.stdout.splitlines()}
    assert result.exit_code == 0
    assert "Yahoo NSE tickers cached 2/2 (100.0%)" in lines
    assert "Yahoo BSE tickers cached 1/2 (50.0%)" in lines


def test_status_reports_missing_database(tmp_path, runner) -> None:
    result = runner.invoke(app, ["status", "--db-path", str(tmp_path / "missing.db")])

    assert result.exit_code == 1
    assert "Database not found" in result.stdout


def test_status_handles_database_without_tables(tmp_path, runner) -> None:
    db_path = tmp_path / "empty.db"
    sqlite3.connect(db_path).close()

    result = runner.invoke(app, ["status", "--db-path", str(db_path)])

    assert result.exit_code == 0
    assert "Consolidated rows" in result.stdout
    assert "Yahoo responses" in result.stdout
    assert "0/0 (n/a)" in result.stdout


def test_query_prints_hit_and_truncation(tmp_path, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = runner.invoke(app, ["query", "INFY", "--limit", "1", "--db-path", str(db_path)])

    assert result.exit_code == 0
    assert "Matches for 'INFY'" in result.stdout
    assert "INE009A01021" in result.stdout
    assert "Showing 1 of 2 matches" in result.stdout


def test_query_json_prints_term_and_search_result(tmp_path, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = runner.invoke(app, ["query", "INFY", "--limit", "1", "--db-path", str(db_path), "--json"])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["term"] == "INFY"
    assert payload["total"] == 2
    assert payload["matches"][0]["isin"] == "INE009A01021"


def test_query_prints_no_hit(tmp_path, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = runner.invoke(app, ["query", "MISSING", "--db-path", str(db_path)])

    assert result.exit_code == 0
    assert "No matches for 'MISSING'." in result.stdout


def test_query_prints_fuzzy_hint(tmp_path, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = runner.invoke(app, ["query", "RELIANC INDUSTRES", "--db-path", str(db_path)])

    assert result.exit_code == 0
    assert "No direct matches; showing closest names." in result.stdout
    assert "RELIANCE" in result.stdout
    assert "INDUSTRIES" in result.stdout


def test_lookup_prints_single_match_equivalents(tmp_path, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = runner.invoke(app, ["lookup", "500325", "--db-path", str(db_path)])

    assert result.exit_code == 0
    assert "Equivalents for '500325'" in result.stdout
    for value in ("INE002A01018", "equity", "RELIANCE", "500325", "RELIANCE INDUSTRIES"):
        assert value in result.stdout
    for value in ("Zerodha NSE", "Zerodha BSE", "Yahoo NSE", "RELIANCE.NS", "Yahoo BSE", "RELIANCE.BO"):
        assert value in result.stdout


def test_lookup_json_prints_single_match(tmp_path, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = runner.invoke(app, ["lookup", "500325", "--db-path", str(db_path), "--json"])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["isin"] == "INE002A01018"
    assert payload["zd_bo"] == "RELIANCE"
    assert payload["yq_ns"] == "RELIANCE.NS"
    assert payload["bse_sc_code"] == "500325"


def test_lookup_reports_no_exact_match(tmp_path, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = runner.invoke(app, ["lookup", "RELIANC", "--db-path", str(db_path)])

    assert result.exit_code == 1
    assert "No instrument matches 'RELIANC' exactly." in result.stdout
    assert "Try 'stocky query RELIANC' for a fuzzy" in result.stdout


def test_lookup_quotes_multi_word_identifier_in_query_hint(tmp_path, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = runner.invoke(app, ["lookup", "RELIANC INDUSTRIES", "--db-path", str(db_path)])

    assert result.exit_code == 1
    assert "stocky query 'RELIANC INDUSTRIES'" in result.stdout


def test_lookup_prints_multiple_match_hint(tmp_path, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(
        db_path,
        [
            ("INE001", "equity", "ONE", None, None, "FIRST LTD", "SHARED", None, None, None),
            ("INE002", "equity", "SHARED", None, None, "SECOND LTD", "TWO", None, None, None),
        ],
    )

    result = runner.invoke(app, ["lookup", "SHARED", "--db-path", str(db_path)])

    assert result.exit_code == 0
    assert "INE001" in result.stdout
    assert "INE002" in result.stdout
    assert "Multiple instruments match; refine the identifier." in result.stdout


def test_lookup_limit_controls_ambiguous_candidates(tmp_path, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    rows = [
        (
            f"INE{index:06d}01016",
            "equity",
            None,
            None,
            str(500000 + index),
            "MIRAE ASSET MUTUAL FUND",
            f"FUND{index}",
            None,
            None,
            None,
        )
        for index in range(21)
    ]
    seed_consolidated(db_path, rows)

    default = runner.invoke(app, ["lookup", "MIRAE ASSET MUTUAL FUND", "--db-path", str(db_path)])
    raised = runner.invoke(app, ["lookup", "MIRAE ASSET MUTUAL FUND", "--limit", "25", "--db-path", str(db_path)])

    assert default.exit_code == 0
    assert "Showing 20 of 21 matches" in default.stdout
    assert raised.exit_code == 0
    assert "Showing 20 of 21" not in raised.stdout
    assert raised.stdout.count("FUND") >= 21


def test_explore_searches_selects_and_reprompts(tmp_path, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = runner.invoke(
        app,
        ["explore", "--db-path", str(db_path)],
        input="INFY\n3\n1\nRELIANC INDUSTRES\n\n\n",
    )

    assert result.exit_code == 0
    assert "#" in result.stdout
    assert "Enter a number between 1 and 2." in result.stdout
    assert "Equivalents for 'INFY'" in result.stdout
    assert "No direct matches; showing closest names." in result.stdout
    assert "RELIANCE" in result.stdout
    assert "Bye." in result.stdout


def test_explore_json_prints_search_and_selection_documents(tmp_path, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = runner.invoke(
        app,
        ["explore", "--db-path", str(db_path), "--json"],
        input="INFY\n1\n\n",
    )

    # CliRunner echoes piped input to stdout; a real terminal echoes it itself, so skip to each document start.
    decoder = json.JSONDecoder()
    documents = []
    position = result.stdout.find("{")
    while position != -1:
        document, position = decoder.raw_decode(result.stdout, position)
        documents.append(document)
        position = result.stdout.find("{", position)

    assert result.exit_code == 0
    assert documents[0]["term"] == "INFY"
    assert documents[0]["total"] == 2
    assert documents[1]["isin"] == "INE009A01021"


def test_explore_missing_database_exits(tmp_path, runner) -> None:
    result = runner.invoke(app, ["explore", "--db-path", str(tmp_path / "missing.db")])

    assert result.exit_code == 1
    assert "Database not found" in result.stdout


def test_explore_exits_when_consolidated_table_is_missing(tmp_path, runner) -> None:
    db_path = tmp_path / "stocky.db"
    initialize_database(db_path)

    result = runner.invoke(app, ["explore", "--db-path", str(db_path)], input="INFY\n")

    assert result.exit_code == 1
    assert "does not exist" in result.stdout
    assert result.stdout.count("Search (blank to quit)") == 1


def test_explore_rejects_invalid_limit_before_prompting(tmp_path, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = runner.invoke(app, ["explore", "--limit", "0", "--db-path", str(db_path)])

    assert result.exit_code == 1
    assert "Limit must be at least 1." in result.stdout
    assert "Search" not in result.stdout


def test_query_rejects_invalid_limit(tmp_path, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = runner.invoke(app, ["query", "INFY", "--limit", "0", "--db-path", str(db_path)])

    assert result.exit_code == 1
    assert "Limit must be at least 1" in result.stdout


def test_yahoo_update_passes_options_and_prints_progress(tmp_path, monkeypatch, runner) -> None:
    db_path = tmp_path / "stocky.db"
    calls = []

    class FakeManager:
        def __init__(self, selected_db_path) -> None:
            calls.append(("init", selected_db_path))

        def update_data(self, **options):
            calls.append(("update", options.copy()))
            options["progress"](50, 60, 48, 2)
            return YahooUpdateResult(processed=60, written=58, skipped=2, dry_run=False)

    monkeypatch.setattr(cli, "YahooDataManager", FakeManager)

    result = runner.invoke(
        app,
        [
            "yahoo",
            "update",
            "--exchange",
            "NSE",
            "--db-path",
            str(db_path),
            "--limit",
            "60",
            "--missing-only",
            "--symbols",
            "RELIANCE.NS, INFY",
            "--stale-days",
            "90",
        ],
    )

    assert result.exit_code == 0
    assert calls[0] == ("init", db_path)
    assert calls[1][1] | {"progress": None} == {
        "exchange": "NSE",
        "dry_run": False,
        "limit": 60,
        "missing_only": True,
        "symbols": ["RELIANCE.NS", "INFY"],
        "stale_days": 90.0,
        "progress": None,
    }
    assert callable(calls[1][1]["progress"])
    assert "50/60 processed; 48 written; 2 skipped" in result.stderr
    assert "50/60 processed" not in result.stdout
    assert "Processed 60; wrote 58; skipped 2; dry_run=False." in result.stdout


def test_yahoo_update_no_longer_accepts_key(tmp_path, runner) -> None:
    result = runner.invoke(app, ["yahoo", "update", "--key", "zd_symbol", "--db-path", str(tmp_path / "stocky.db")])

    assert result.exit_code == 2
    assert "No such option" in result.output


def test_yahoo_update_throttles_plain_progress_lines(tmp_path, monkeypatch, runner) -> None:
    db_path = tmp_path / "stocky.db"

    class FakeManager:
        def __init__(self, selected_db_path) -> None:
            assert selected_db_path == db_path

        def update_data(self, **options):
            for index in range(1, 52):
                options["progress"](index, 51, index, 0)
            return YahooUpdateResult(processed=51, written=51, skipped=0, dry_run=False)

    monkeypatch.setattr(cli, "YahooDataManager", FakeManager)

    result = runner.invoke(app, ["yahoo", "update", "--db-path", str(db_path)])

    lines = [line for line in result.stderr.splitlines() if line.strip()]

    assert result.exit_code == 0
    assert lines == [
        "50/51 processed; 50 written; 0 skipped",
        "51/51 processed; 51 written; 0 skipped",
    ]


def test_yahoo_update_json_emits_progress_events_on_stderr(tmp_path, monkeypatch, runner) -> None:
    db_path = tmp_path / "stocky.db"

    class FakeManager:
        def __init__(self, selected_db_path) -> None:
            assert selected_db_path == db_path

        def update_data(self, **options):
            options["progress"](1, 2, 1, 0)
            options["progress"](2, 2, 1, 1)
            return YahooUpdateResult(processed=2, written=1, skipped=1, dry_run=False)

    monkeypatch.setattr(cli, "YahooDataManager", FakeManager)

    result = runner.invoke(app, ["yahoo", "update", "--db-path", str(db_path), "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"processed": 2, "written": 1, "skipped": 1, "dry_run": False}
    events = [json.loads(line) for line in result.stderr.splitlines() if line.strip()]
    assert events == [
        {"event": "progress", "processed": 1, "total": 2, "written": 1, "skipped": 0},
        {"event": "progress", "processed": 2, "total": 2, "written": 1, "skipped": 1},
    ]
    assert "2/2 processed" not in result.stdout


def test_yahoo_update_reports_missing_database(tmp_path, runner) -> None:
    result = runner.invoke(app, ["yahoo", "update", "--db-path", str(tmp_path / "missing.db")])

    message = " ".join(result.stderr.split())

    assert result.exit_code == 1
    assert "Database not found" in message
    assert "Run 'stocky rebuild' first." in message
    assert result.stdout == ""


def test_yahoo_update_json_reports_missing_database_as_an_event(tmp_path, runner) -> None:
    result = runner.invoke(app, ["yahoo", "update", "--db-path", str(tmp_path / "missing.db"), "--json"])

    events = [json.loads(line) for line in result.stderr.splitlines() if line.strip()]

    assert result.exit_code == 1
    assert result.stdout == ""
    assert len(events) == 1
    assert events[0]["event"] == "error"
    assert "Run 'stocky rebuild' first." in events[0]["message"]


def test_yahoo_import_cache_skips_bad_file(tmp_path, runner) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "GOOD.NS.json").write_text(
        '{"GOOD.NS": {"quoteType": {"quoteType": "EQUITY", "longName": "Good Ltd"}}}', encoding="utf-8"
    )
    (cache_dir / "BAD.NS.json").write_text("not json", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "yahoo",
            "import-cache",
            "--cache-dir",
            str(cache_dir),
            "--db-path",
            str(tmp_path / "stocky.db"),
        ],
    )

    assert result.exit_code == 0
    assert "Imported 1; skipped 1." in result.stdout


def test_yahoo_import_cache_json_prints_result(tmp_path, runner) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "GOOD.NS.json").write_text(
        '{"GOOD.NS": {"quoteType": {"quoteType": "EQUITY", "longName": "Good Ltd"}}}', encoding="utf-8"
    )
    (cache_dir / "BAD.NS.json").write_text("not json", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "yahoo",
            "import-cache",
            "--cache-dir",
            str(cache_dir),
            "--db-path",
            str(tmp_path / "stocky.db"),
            "--json",
        ],
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload == {"imported": 1, "skipped": 1}


def test_interactive_searches_once_then_quits(tmp_path, monkeypatch, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", db_path)

    result = runner.invoke(app, ["interactive"], input="5\nINFY\n\n6\n")

    assert result.exit_code == 0
    assert "Matches for 'INFY'" in result.stdout
    assert "Equivalents" not in result.stdout
    assert result.stdout.count("Choose an option") == 2


def test_interactive_search_shows_tickers_for_a_selected_row(tmp_path, monkeypatch, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", db_path)

    result = runner.invoke(app, ["interactive"], input="5\nINFY\n2\n6\n")

    assert result.exit_code == 0
    assert "Matches for 'INFY'" in result.stdout
    assert "Zerodha NSE" in result.stdout
    assert "INFYBEES" in result.stdout
    assert result.stdout.count("Choose an option") == 2


def test_interactive_search_shows_tickers_for_a_single_match(tmp_path, monkeypatch, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", db_path)

    result = runner.invoke(app, ["interactive"], input="5\n500325\n6\n")

    assert result.exit_code == 0
    assert "Equivalents for '500325'" in result.stdout
    assert "RELIANCE.NS" in result.stdout
    assert "RELIANCE.BO" in result.stdout
    assert result.stdout.count("Choose an option") == 2


def test_interactive_reports_yahoo_update_errors_and_keeps_going(
    tmp_path, monkeypatch, seed_consolidated, runner
) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path, [("INE001", "equity", None, None, None, None, None, None, "", "")])
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", db_path)

    result = runner.invoke(app, ["interactive"], input="2\nBSE\n\nn\n6\n")

    message = " ".join(result.stdout.split())

    assert result.exit_code == 0
    assert "No BSE tickers found in yq_bo" in message
    assert "Run 'stocky rebuild' first." in message
    assert result.stdout.count("Choose an option") == 2


def test_interactive_rebuild_previews_and_writes_database(tmp_path, market_csv_builder, monkeypatch, runner) -> None:
    paths = market_csv_builder(tmp_path / "inputs", trade_date=date(2021, 5, 3))
    db_path = tmp_path / "stocky.db"
    _use_interactive_defaults(monkeypatch, paths, db_path)

    result = runner.invoke(app, ["interactive"], input="1\n1\ny\n6\n")

    message = " ".join(result.stdout.split())

    assert result.exit_code == 0
    assert "Available bhavcopy pairs" in message
    assert "2021-05-03" in message
    assert paths.bse.name in message
    assert paths.nse.name in message
    assert "Rebuild preview" in message
    assert str(paths.zerodha) in message
    assert "Rebuild summary" in message
    with sqlite3.connect(db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM consolidated").fetchone()[0] == 1
    assert result.stdout.count("Choose an option") == 2


def test_interactive_rebuild_reports_empty_input_directory(tmp_path, monkeypatch, runner) -> None:
    empty_dir = tmp_path / "inputs"
    empty_dir.mkdir()
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(cli, "DEFAULT_BHAVCOPY_DIR", empty_dir)
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", tmp_path / "stocky.db")

    result = runner.invoke(app, ["interactive"], input="1\n6\n")

    message = " ".join(result.stdout.split())

    assert result.exit_code == 0
    assert f"No BSE/NSE bhavcopy pairs found in {empty_dir}" in message
    assert "Download the bhavcopy files into that directory first." in message
    assert result.stdout.count("Choose an option") == 2


def test_interactive_rebuild_reports_malformed_source_file(tmp_path, market_csv_builder, monkeypatch, runner) -> None:
    paths = market_csv_builder(tmp_path / "inputs", trade_date=date(2021, 5, 3))
    paths.bse.write_text("UNKNOWN,OTHER\nvalue,other\n", encoding="utf-8")
    db_path = tmp_path / "stocky.db"
    _use_interactive_defaults(monkeypatch, paths, db_path)

    result = runner.invoke(app, ["interactive"], input="1\n1\n6\n")

    message = " ".join(result.stdout.split())

    assert result.exit_code == 0
    assert "Cannot rebuild: BSE bhavcopy is missing required columns" in message
    assert str(paths.bse) in message
    assert "Rebuild preview" not in message
    assert not db_path.exists()
    assert result.stdout.count("Choose an option") == 2


def test_interactive_rebuild_reports_date_without_a_pair(tmp_path, market_csv_builder, monkeypatch, runner) -> None:
    paths = market_csv_builder(tmp_path / "inputs", trade_date=date(2021, 5, 3))
    db_path = tmp_path / "stocky.db"
    _use_interactive_defaults(monkeypatch, paths, db_path)

    result = runner.invoke(app, ["interactive"], input="1\n2021-05-04\n6\n")

    message = " ".join(result.stdout.split())

    assert result.exit_code == 0
    assert f"No BSE/NSE bhavcopy pair for 2021-05-04 in {paths.bse.parent}" in message
    assert "Pick a listed row or download that day's files." in message
    assert "Rebuild preview" not in message
    assert not db_path.exists()
    assert result.stdout.count("Choose an option") == 2


def test_interactive_rebuild_rejects_out_of_range_row(tmp_path, market_csv_builder, monkeypatch, runner) -> None:
    paths = market_csv_builder(tmp_path / "inputs", trade_date=date(2021, 5, 3))
    db_path = tmp_path / "stocky.db"
    _use_interactive_defaults(monkeypatch, paths, db_path)

    result = runner.invoke(app, ["interactive"], input="1\n4\n6\n")

    message = " ".join(result.stdout.split())

    assert result.exit_code == 0
    assert "Enter a number between 1 and 1." in message
    assert not db_path.exists()


def test_interactive_rebuild_rejects_unparseable_selection(tmp_path, market_csv_builder, monkeypatch, runner) -> None:
    paths = market_csv_builder(tmp_path / "inputs", trade_date=date(2021, 5, 3))
    db_path = tmp_path / "stocky.db"
    _use_interactive_defaults(monkeypatch, paths, db_path)

    result = runner.invoke(app, ["interactive"], input="1\nlatest\n6\n")

    message = " ".join(result.stdout.split())

    assert result.exit_code == 0
    assert "Enter a row number or a date like 2026-09-19." in message
    assert not db_path.exists()


def test_interactive_rebuild_declined_leaves_database_untouched(
    tmp_path, market_csv_builder, monkeypatch, runner
) -> None:
    paths = market_csv_builder(tmp_path / "inputs", trade_date=date(2021, 5, 3))
    db_path = tmp_path / "stocky.db"
    _use_interactive_defaults(monkeypatch, paths, db_path)

    result = runner.invoke(app, ["interactive"], input="1\n1\nn\n6\n")

    message = " ".join(result.stdout.split())

    assert result.exit_code == 0
    assert "Rebuild preview" in message
    assert "Rebuild summary" not in message
    assert not db_path.exists()
    assert result.stdout.count("Choose an option") == 2


def test_interactive_rebuild_accepts_a_date_with_an_unzipped_nse_file(
    tmp_path, market_csv_builder, monkeypatch, runner
) -> None:
    paths = market_csv_builder(tmp_path / "inputs", trade_date=date(2026, 7, 17))
    unzipped_nse = paths.nse.with_suffix("")
    paths.nse.rename(unzipped_nse)
    db_path = tmp_path / "stocky.db"
    _use_interactive_defaults(monkeypatch, paths, db_path)

    result = runner.invoke(app, ["interactive"], input="1\n2026-07-17\ny\n6\n")

    message = " ".join(result.stdout.split())

    assert result.exit_code == 0
    assert str(unzipped_nse) in message
    assert "Rebuild summary" in message
    with sqlite3.connect(db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM consolidated").fetchone()[0] == 1


def test_interactive_rebuild_accepts_a_date_below_the_listed_rows(
    tmp_path, market_csv_builder, monkeypatch, runner
) -> None:
    paths = market_csv_builder(tmp_path / "inputs", trade_date=date(2021, 5, 3))
    for day in range(1, 11):
        newer = date(2021, 6, day)
        (paths.bse.parent / bse_filename_for_date(newer)).write_text("", encoding="utf-8")
        (paths.bse.parent / nse_filename_for_date(newer)).write_text("", encoding="utf-8")
    db_path = tmp_path / "stocky.db"
    _use_interactive_defaults(monkeypatch, paths, db_path)

    result = runner.invoke(app, ["interactive"], input="1\n2021-05-03\ny\n6\n")

    message = " ".join(result.stdout.split())

    assert result.exit_code == 0
    assert "... and 1 more" in message
    assert str(paths.bse) in message
    assert "Rebuild summary" in message
    with sqlite3.connect(db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM consolidated").fetchone()[0] == 1


def test_interactive_yahoo_update_prompts_and_reports_progress(
    tmp_path, monkeypatch, seed_consolidated, runner
) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", db_path)
    _use_fake_yahooquery(monkeypatch)

    result = runner.invoke(app, ["interactive"], input="2\nnse\n2\nn\ny\n6\n")

    message = " ".join(result.stdout.split())

    assert result.exit_code == 0
    assert "2 tickers to fetch from Yahoo Finance (NSE, yq_ns)." in message
    assert "Processed 2; wrote 2; skipped 0." in message
    assert "2/2 processed; 2 written; 0 skipped" in result.stderr
    assert result.stdout.count("Choose an option") == 2


def test_interactive_yahoo_update_rejects_unknown_exchange(tmp_path, monkeypatch, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", db_path)

    result = runner.invoke(app, ["interactive"], input="2\nNYSE\n6\n")

    message = " ".join(result.stdout.split())

    assert result.exit_code == 0
    assert "Enter one of BSE, NSE." in message
    assert result.stdout.count("Choose an option") == 2


def test_interactive_yahoo_update_rejects_non_numeric_limit(tmp_path, monkeypatch, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", db_path)

    result = runner.invoke(app, ["interactive"], input="2\nBSE\nmany\n6\n")

    message = " ".join(result.stdout.split())

    assert result.exit_code == 0
    assert "Enter a positive number, or leave blank for all." in message


def test_interactive_yahoo_update_stops_when_nothing_to_fetch(tmp_path, monkeypatch, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)
    _seed_cached_yahoo_responses(db_path, ["RELIANCE", "INFY", "20MICRONS", "INFYBEES"])
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", db_path)

    result = runner.invoke(app, ["interactive"], input="2\nBSE\n\ny\n6\n")

    message = " ".join(result.stdout.split())

    assert result.exit_code == 0
    assert "0 tickers to fetch from Yahoo Finance (BSE, yq_bo)." in message
    assert "Nothing to fetch; every ticker already has a cached response." in message
    assert "Start the update?" not in message
    assert result.stdout.count("Choose an option") == 2


def test_interactive_yahoo_update_rejects_zero_limit(tmp_path, monkeypatch, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", db_path)

    result = runner.invoke(app, ["interactive"], input="2\nBSE\n0\n6\n")

    message = " ".join(result.stdout.split())

    assert result.exit_code == 0
    assert "Enter a positive number, or leave blank for all." in message
    assert "tickers to fetch from Yahoo Finance" not in message
    assert result.stdout.count("Choose an option") == 2


@pytest.mark.parametrize("arguments", [["version"], ["--version"]])
def test_version_commands_print_package_version(arguments, runner) -> None:
    result = runner.invoke(app, arguments)

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_main_calls_app(monkeypatch) -> None:
    called = []
    monkeypatch.setattr(cli, "app", lambda: called.append(True))

    cli.main()

    assert called == [True]


def test_parse_date_accepts_none_and_iso_date() -> None:
    assert cli._parse_date(None) is None
    assert cli._parse_date("2026-09-20") == date(2026, 9, 20)


def test_parse_date_rejects_invalid_date() -> None:
    with pytest.raises(typer.BadParameter, match="ISO date format"):
        cli._parse_date("20-09-2026")


@pytest.mark.parametrize(
    ("num_bytes", "expected"),
    [(0, "0 B"), (1023, "1023 B"), (1024, "1.0 KB"), (1024**5, "1024.0 TB")],
)
def test_format_size(num_bytes, expected) -> None:
    assert cli._format_size(num_bytes) == expected


@pytest.mark.parametrize(
    ("part", "whole", "expected"),
    [(0, 0, "n/a"), (0, 4, "0.0%"), (1, 4, "25.0%")],
)
def test_format_percent(part, whole, expected) -> None:
    assert cli._format_percent(part, whole) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "None"), ("", "None"), ("2026-09-20T12:34:56.123+00:00", "2026-09-20 12:34:56")],
)
def test_format_timestamp(value, expected) -> None:
    assert cli._format_timestamp(value) == expected


def _seed_yahoo_payloads(db_path, payloads) -> None:
    initialize_database(db_path)
    with connect(db_path) as con:
        for ticker, (name, market_cap, sector) in payloads.items():
            payload = {
                "quoteType": {"quoteType": "EQUITY", "longName": name},
                "price": {"currency": "INR", "marketCap": market_cap, "regularMarketPrice": 100.0},
                "assetProfile": {"sector": sector, "industry": "Testing"},
            }
            upsert_yahoo_response(
                con,
                yahoo_symbol=ticker,
                symbol=ticker.rsplit(".", 1)[0],
                exchange="NSE" if ticker.endswith(".NS") else "BSE",
                response_json=encode_response_json({ticker: payload}),
                source="test",
            )


def _seed_enrichable_db(tmp_path, seed_consolidated):
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)
    _seed_yahoo_payloads(
        db_path,
        {
            "RELIANCE.NS": ("Reliance Industries Limited", 1_348_695 * 10_000_000, "Energy"),
            "RELIANCE.BO": ("Reliance Industries Limited", 1_182_543 * 10_000_000, "Energy"),
            "INFY.NS": ("Infosys Limited", 574_958 * 10_000_000, "Technology"),
        },
    )
    return db_path


def test_screen_prints_listings_largest_market_cap_first(tmp_path, seed_consolidated, runner, monkeypatch) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    db_path = _seed_enrichable_db(tmp_path, seed_consolidated)

    result = runner.invoke(
        app, ["screen", "--market-cap-gt", "500000", "--exchange", "NSE", "--limit", "1", "--db-path", str(db_path)]
    )

    assert result.exit_code == 0
    assert "Screen results" in result.stdout
    assert "RELIANCE.NS" in result.stdout
    assert "1,348,695" in result.stdout
    assert "RELIANCE.BO" not in result.stdout
    assert "Showing 1 of 2 matches" in result.stdout


def test_screen_json_prints_total_and_records(tmp_path, seed_consolidated, runner) -> None:
    db_path = _seed_enrichable_db(tmp_path, seed_consolidated)

    result = runner.invoke(app, ["screen", "--sector", "tech", "--db-path", str(db_path), "--json"])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["total"] == 1
    assert payload["rows"][0]["yahoo_ticker"] == "INFY.NS"
    assert payload["rows"][0]["exchange"] == "NSE"
    assert payload["rows"][0]["sector"] == "Technology"


def test_screen_reports_no_match_and_rejects_an_unknown_exchange(tmp_path, seed_consolidated, runner) -> None:
    db_path = _seed_enrichable_db(tmp_path, seed_consolidated)

    result = runner.invoke(app, ["screen", "--market-cap-lt", "1", "--db-path", str(db_path)])
    assert result.exit_code == 0
    assert "No listings match the screen." in result.stdout

    result = runner.invoke(app, ["screen", "--exchange", "MCX", "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "Unsupported exchange: MCX" in result.stdout


def test_yahoo_fields_prints_one_table_per_exchange(tmp_path, seed_consolidated, runner, monkeypatch) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    db_path = _seed_enrichable_db(tmp_path, seed_consolidated)

    result = runner.invoke(app, ["yahoo", "fields", "INFY", "--db-path", str(db_path)])

    assert result.exit_code == 0
    assert "INFY.NS (NSE, INE009A01021)" in result.stdout
    assert "574,958 Cr" in result.stdout
    assert "INFY.BO (BSE, INE009A01021)" in result.stdout
    assert "No cached Yahoo response for this ticker." in result.stdout


def test_yahoo_fields_json_prints_records(tmp_path, seed_consolidated, runner) -> None:
    db_path = _seed_enrichable_db(tmp_path, seed_consolidated)

    result = runner.invoke(app, ["yahoo", "fields", "RELIANCE.BO", "--db-path", str(db_path), "--json"])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert [(record["yahoo_ticker"], record["market_cap"]) for record in payload] == [
        ("RELIANCE.BO", 1_182_543 * 10_000_000),
        ("RELIANCE.NS", 1_348_695 * 10_000_000),
    ]


def test_yahoo_fields_reports_no_exact_match(tmp_path, seed_consolidated, runner) -> None:
    db_path = _seed_enrichable_db(tmp_path, seed_consolidated)

    result = runner.invoke(app, ["yahoo", "fields", "RELIANC", "--db-path", str(db_path)])

    assert result.exit_code == 1
    assert "No instrument with a Yahoo ticker matches 'RELIANC' exactly." in result.stdout


def test_yahoo_enriched_writes_the_view_to_a_file(tmp_path, seed_consolidated, runner) -> None:
    db_path = _seed_enrichable_db(tmp_path, seed_consolidated)
    output = tmp_path / "out" / "enriched.json"

    result = runner.invoke(
        app, ["yahoo", "enriched", "--output", str(output), "--exchange", "BSE", "--db-path", str(db_path)]
    )

    assert result.exit_code == 0
    assert "Wrote 2 enriched rows" in result.stdout
    records = json.loads(output.read_text(encoding="utf-8"))
    assert [(record["yahoo_ticker"], record["yahoo_status"]) for record in records] == [
        ("RELIANCE.BO", "ok"),
        ("INFY.BO", "missing"),
    ]


def test_yahoo_enriched_writes_csv_to_stdout(tmp_path, seed_consolidated, runner) -> None:
    db_path = _seed_enrichable_db(tmp_path, seed_consolidated)

    result = runner.invoke(app, ["yahoo", "enriched", "--format", "csv", "--db-path", str(db_path)])

    assert result.exit_code == 0
    assert result.stdout.splitlines()[0].startswith("isin,exchange,yahoo_ticker,")
    assert len(result.stdout.splitlines()) == 5


@pytest.mark.parametrize(
    ("rupees", "shown"),
    [(None, "-"), (2_100_635, "0.21"), (25 * 10_000_000 + 5_000_000, "25.50"), (1_348_695 * 10_000_000, "1,348,695")],
)
def test_format_crore_keeps_the_fraction_of_small_market_caps(rupees, shown) -> None:
    assert cli._format_crore(rupees) == shown


def _write_cli_mf_files(directory):
    mf_path = directory / "mf.csv"
    mf_path.write_text(
        "tradingsymbol,name,amc,scheme_type,plan,dividend_type\n"
        "INF179K01KG8,HDFC Liquid Fund - Direct Plan,HDFCMutualFund_MF,Debt,direct,growth\n"
        "INF179K01WT6,HDFC Liquid Fund,HDFCMutualFund_MF,Debt,regular,payout\n"
        "INF209K01YN0,Aditya Birla Sun Life Tax Relief 96,ABSLMutualFund_MF,Equity,regular,growth\n",
        encoding="utf-8",
    )
    amfi_path = directory / "NAVAll.txt"
    amfi_path.write_text(
        "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date\n"
        "100001;INF179K01KG8;INF179K01WT6;HDFC Liquid Fund;Direct;Growth;100;18-Oct-2026\n"
        "100002;INF179K01WT6;-;Other Fund;Regular;Payout;100;18-Oct-2026\n",
        encoding="utf-8",
    )
    return mf_path, amfi_path


def test_mf_import_reports_amfi_counts_and_ambiguous_isins(tmp_path, runner) -> None:
    mf_path, amfi_path = _write_cli_mf_files(tmp_path)

    result = runner.invoke(
        app,
        [
            "mf",
            "import",
            "--mf-instruments",
            str(mf_path),
            "--amfi",
            str(amfi_path),
            "--db-path",
            str(tmp_path / "x.db"),
        ],
    )

    assert result.exit_code == 0
    assert "Imported 3 mutual funds" in result.stdout
    assert "Rows with an AMFI code: 1" in result.stdout
    assert "Ambiguous ISINs: 1" in result.stdout
    assert "INF179K01WT6" in result.stdout


def test_mf_import_lists_at_most_ten_ambiguous_isins(tmp_path, runner) -> None:
    mf_path, amfi_path = _write_cli_mf_files(tmp_path)
    isins = [f"INF{i:09d}" for i in range(12)]
    mf_path.write_text(
        "tradingsymbol,name,amc,scheme_type,plan,dividend_type\n"
        + "".join(f"{isin},Fund,AMC,Debt,direct,growth\n" for isin in isins),
        encoding="utf-8",
    )
    amfi_path.write_text(
        "".join(f"{code};{isin};;Fund;Direct;Growth;100;18-Oct-2026\n" for isin in isins for code in (100001, 100002)),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "mf",
            "import",
            "--mf-instruments",
            str(mf_path),
            "--amfi",
            str(amfi_path),
            "--db-path",
            str(tmp_path / "x.db"),
        ],
    )

    assert result.exit_code == 0
    assert "Rows with an AMFI code: 0" in result.stdout
    assert "Ambiguous ISINs: 12" in result.stdout
    assert all(isin in result.stdout for isin in isins[:10])
    assert all(isin not in result.stdout for isin in isins[10:])


def test_mf_import_uses_default_amfi_path(tmp_path, monkeypatch, runner) -> None:
    mf_path, amfi_path = _write_cli_mf_files(tmp_path)
    destination = tmp_path / "data/marketData/amfi/NAVAll.txt"
    destination.parent.mkdir(parents=True)
    amfi_path.rename(destination)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["mf", "import", "--mf-instruments", str(mf_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["amfi"] == "data/marketData/amfi/NAVAll.txt"
    assert payload["rows"] == 3
    assert payload["coded_rows"] == 1
    assert payload["ambiguous_isins"] == ["INF179K01WT6"]


@pytest.mark.parametrize("json_output", [False, True])
def test_mf_import_requires_amfi_file(tmp_path, monkeypatch, runner, json_output) -> None:
    mf_path, _ = _write_cli_mf_files(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app, ["mf", "import", "--mf-instruments", str(mf_path), *(["--json"] if json_output else [])]
    )

    assert result.exit_code == 1
    message = " ".join((result.stderr if json_output else result.stdout).split())
    assert "AMFI NAV file not found: data/marketData/amfi/NAVAll.txt" in message
    assert "https://www.amfiindia.com/spages/NAVAll.txt" in message
    assert not (tmp_path / "data/output/stocky.db").exists()
    if json_output:
        assert result.stdout == ""


def test_mf_query_by_amfi_code_keeps_all_values_at_80_columns(tmp_path, monkeypatch, runner) -> None:
    monkeypatch.setenv("COLUMNS", "80")
    mf_path, amfi_path = _write_cli_mf_files(tmp_path)
    db_path = tmp_path / "stocky.db"
    imported = runner.invoke(
        app, ["mf", "import", "--mf-instruments", str(mf_path), "--amfi", str(amfi_path), "--db-path", str(db_path)]
    )

    queried = runner.invoke(app, ["mf", "query", "100001", "--exact", "--db-path", str(db_path)])
    partial = runner.invoke(app, ["mf", "query", "10000", "--db-path", str(db_path), "--json"])
    json_result = runner.invoke(app, ["mf", "query", "100001", "--db-path", str(db_path), "--json"])

    assert imported.exit_code == 0
    assert queried.exit_code == 0
    lines = queried.stdout.splitlines()
    assert "AMFI" in queried.stdout
    assert "…" not in queried.stdout
    assert all(len(line) <= 80 for line in lines)
    cells = [line.split("│") for line in lines if "│" in line]
    expected = (
        "INF179K01KG8",
        "INF179K01KG8",
        "100001",
        "HDFC Liquid Fund - Direct Plan",
        "HDFCMutualFund_MF",
        "Debt",
        "direct",
        "growth",
    )
    for index, value in enumerate(expected):
        assert "".join(row[index].strip() for row in cells).replace(" ", "") == value.replace(" ", "")
    assert partial.exit_code == 0
    assert json.loads(partial.stdout)["total"] == 0
    assert json_result.exit_code == 0
    assert json.loads(json_result.stdout)["matches"][0]["amfi_code"] == "100001"


def _write_cli_debt_zip(path):
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "fgroup16102026.csv",
            "Security_cd,sc_name,ISIN No.,FACE VALUE,COUP0N (%),Maturity Date\n"
            "978034,0MCIBPL28,INE0K0Y08060,100000.00,,\n",
        )
        archive.writestr(
            "icdm16102026.csv",
            "Security Code,Issuer Name,Coupon (%),Maturity Date,ISIN No.,Face Value\n"
            "INE0K0Y08060,Industrial Finance,7.4000,18/06/2031,INE0K0Y08060,100000.00\n",
        )
        archive.writestr(
            "wdm16102026.csv",
            "Scrip Code,Security Code,Security Description,Coupon Rate (%),MaturityDate\n"
            "400536,667GOI50,6.67 GOI BOND 2050,6.67,17/12/2050\n",
        )
    return path


def test_debt_import_and_query_commands(tmp_path, monkeypatch, runner) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    db_path = tmp_path / "stocky.db"
    debt_path = _write_cli_debt_zip(tmp_path / "debt.zip")

    imported = runner.invoke(app, ["debt", "import", "--file", str(debt_path), "--db", str(db_path)])
    queried = runner.invoke(app, ["debt", "query", "INE", "--limit", "1", "--db", str(db_path)])

    assert imported.exit_code == 0
    assert "Imported 3 BSE debt rows" in imported.stdout
    assert queried.exit_code == 0
    assert "BSE debt matching 'INE'" in queried.stdout
    assert "INE0K0Y08060" in queried.stdout
    assert "Industrial Finance" not in queried.stdout
    assert "Showing 1 of 2 matches" in queried.stdout


@pytest.mark.parametrize("trade_date", ["2026-10-16", "2026-10-15"])
def test_debt_query_keeps_metadata_readable_at_80_columns(tmp_path, monkeypatch, runner, trade_date) -> None:
    monkeypatch.setenv("COLUMNS", "80")
    db_path = tmp_path / "stocky.db"
    debt_path = _write_cli_debt_zip(tmp_path / "debt.zip")
    imported = runner.invoke(app, ["debt", "import", "--file", str(debt_path), "--db", str(db_path)])
    with sqlite3.connect(db_path) as con:
        con.execute(
            "UPDATE bse_debt SET name = 'TAPIR CONSTRUCTIONS LIMITED', trade_date = ? WHERE family = 'icdm'",
            (trade_date,),
        )
        con.execute("UPDATE bse_debt SET symbol = '1250TCL30' WHERE family = 'fgroup'")

    queried = runner.invoke(app, ["debt", "query", "INE0K0Y08060", "--exact", "--db", str(db_path)])

    assert imported.exit_code == 0
    assert queried.exit_code == 0
    lines = queried.stdout.splitlines()
    dates = ", ".join(sorted({"2026-10-16", trade_date}))
    assert lines[-1] == f"Trade date: {dates}"
    assert all("Trade date" not in line for line in lines[:-1])
    assert any(all(value in line for value in ("2031-06-18", "INE0K0Y08060", "100000.00", "7.4000")) for line in lines)
    assert any(all(value in line for value in ("fgroup", "978034", "1250TCL30")) for line in lines)
    name_cells = [line.split("│")[4].strip() for line in lines if "│" in line]
    assert name_cells == ["-", "TAPIR", "CONSTRUCTIONS", "LIMITED"]
    assert all(len(line) <= 80 for line in lines)
    assert "…" not in queried.stdout


def test_debt_commands_print_json(tmp_path, runner) -> None:
    db_path = tmp_path / "stocky.db"
    debt_path = _write_cli_debt_zip(tmp_path / "debt.zip")

    imported = runner.invoke(app, ["debt", "import", "--file", str(debt_path), "--db", str(db_path), "--json"])
    queried = runner.invoke(app, ["debt", "query", "INE0K0Y08060", "--exact", "--db", str(db_path), "--json"])

    assert imported.exit_code == 0
    assert json.loads(imported.stdout) == {"rows": 3, "db_path": str(db_path), "file": str(debt_path)}
    assert queried.exit_code == 0
    payload = json.loads(queried.stdout)
    assert payload["term"] == "INE0K0Y08060"
    assert payload["total"] == 2
    assert payload["matches"][0]["symbol"] == "0MCIBPL28"
    assert payload["matches"][1]["coupon_pct"] == "7.4000"
    assert all(match["trade_date"] == "2026-10-16" for match in payload["matches"])


def test_debt_import_defaults_to_newest_archive(tmp_path, monkeypatch, runner) -> None:
    debt_dir = tmp_path / "data/marketData/bhavCopies/bseDebt"
    debt_dir.mkdir(parents=True)
    _write_cli_debt_zip(debt_dir / "DEBTBHAVCOPY31122025.zip")
    newest = _write_cli_debt_zip(debt_dir / "DEBTBHAVCOPY16102026.zip")
    monkeypatch.chdir(tmp_path)

    imported = runner.invoke(app, ["debt", "import", "--json"])

    assert imported.exit_code == 0
    payload = json.loads(imported.stdout)
    assert payload["file"] == str(newest.relative_to(tmp_path))
    assert payload["db_path"] == "data/output/stocky.db"
    assert payload["rows"] == 3


def test_debt_query_reports_no_match_and_missing_table(tmp_path, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    missing_table = runner.invoke(app, ["debt", "query", "GOI", "--db", str(db_path), "--json"])
    runner.invoke(
        app, ["debt", "import", "--file", str(_write_cli_debt_zip(tmp_path / "debt.zip")), "--db", str(db_path)]
    )
    no_match = runner.invoke(app, ["debt", "query", "nothing-like-this", "--db", str(db_path)])

    assert missing_table.exit_code == 1
    assert missing_table.stdout == ""
    assert "stocky debt import" in missing_table.stderr
    assert no_match.exit_code == 0
    assert "No BSE debt instruments match 'nothing-like-this'" in no_match.stdout


def test_debt_import_reports_missing_file(tmp_path, runner) -> None:
    result = runner.invoke(
        app, ["debt", "import", "--file", str(tmp_path / "absent.zip"), "--db", str(tmp_path / "x.db")]
    )

    assert result.exit_code == 1
    assert "BSE debt bhavcopy not found" in result.stdout


def test_debt_import_reports_missing_archive_on_stderr_with_json(tmp_path, monkeypatch, runner) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["debt", "import", "--json"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "BSE debt bhavcopy archives not found" in result.stderr


def _write_cli_indices_csv(path):
    path.write_text(
        "instrument_token,exchange_token,tradingsymbol,name,segment,exchange\n"
        "256265,1001,NIFTY 50,NIFTY 50,INDICES,NSE\n"
        "260105,1016,NIFTY BANK,NIFTY BANK,INDICES,NSE\n"
        "265,1,SENSEX,SENSEX,INDICES,BSE\n",
        encoding="utf-8",
    )
    return path


def test_indices_import_and_query_commands(tmp_path, runner) -> None:
    db_path = tmp_path / "stocky.db"
    path = _write_cli_indices_csv(tmp_path / "instruments.csv")

    imported = runner.invoke(app, ["indices", "import", "--file", str(path), "--db", str(db_path)])
    queried = runner.invoke(app, ["indices", "query", "nifty", "--limit", "1", "--db", str(db_path)])

    assert imported.exit_code == 0
    assert "Imported 3 indices" in imported.stdout
    assert queried.exit_code == 0
    assert "Indices matching 'nifty'" in queried.stdout
    assert "NIFTY 50" in queried.stdout
    assert "NIFTY BANK" not in queried.stdout
    assert "Showing 1 of 2 matches" in queried.stdout


def test_indices_query_keeps_identifiers_and_wraps_long_name_at_80_columns(tmp_path, monkeypatch, runner) -> None:
    monkeypatch.setenv("COLUMNS", "80")
    db_path = tmp_path / "stocky.db"
    path = _write_cli_indices_csv(tmp_path / "instruments.csv")
    imported = runner.invoke(app, ["indices", "import", "--file", str(path), "--db", str(db_path)])
    name = "NIFTY BANK LONG BENCHMARK NAME FOR LARGE BANKING COMPANIES"
    with sqlite3.connect(db_path) as con:
        con.execute("UPDATE indices SET name = ? WHERE zd_instrument_token = '260105'", (name,))

    queried = runner.invoke(app, ["indices", "query", "NIFTY BANK", "--exact", "--db", str(db_path)])

    assert imported.exit_code == 0
    assert queried.exit_code == 0
    lines = queried.stdout.splitlines()
    assert all(len(line) <= 80 for line in lines)
    assert any(all(value in line for value in ("NSE", "NIFTY BANK", "260105", "1016")) for line in lines)
    name_cells = [line.split("│")[2].strip() for line in lines if "│" in line]
    assert len(name_cells) > 1
    assert "".join(name_cells).replace(" ", "") == name.replace(" ", "")
    assert "…" not in queried.stdout


def test_indices_commands_print_json_and_filter_exchange(tmp_path, runner) -> None:
    db_path = tmp_path / "stocky.db"
    path = _write_cli_indices_csv(tmp_path / "instruments.csv")

    imported = runner.invoke(app, ["indices", "import", "--file", str(path), "--db", str(db_path), "--json"])
    queried = runner.invoke(
        app, ["indices", "query", "1016", "--exact", "--exchange", "nse", "--db", str(db_path), "--json"]
    )
    filtered = runner.invoke(app, ["indices", "query", "NIFTY", "--exchange", "bse", "--db", str(db_path), "--json"])

    assert imported.exit_code == 0
    assert json.loads(imported.stdout) == {"rows": 3, "db_path": str(db_path), "file": str(path)}
    assert queried.exit_code == 0
    assert json.loads(queried.stdout) == {
        "term": "1016",
        "total": 1,
        "matches": [
            {
                "exchange": "NSE",
                "zd_symbol": "NIFTY BANK",
                "name": "NIFTY BANK",
                "zd_instrument_token": "260105",
                "exchange_token": "1016",
            }
        ],
    }
    assert filtered.exit_code == 0
    assert json.loads(filtered.stdout)["total"] == 0


def test_indices_import_defaults_to_zerodha_file(tmp_path, monkeypatch, runner) -> None:
    directory = tmp_path / "data/marketData/zerodha"
    directory.mkdir(parents=True)
    path = _write_cli_indices_csv(directory / "instruments.csv")
    monkeypatch.chdir(tmp_path)

    imported = runner.invoke(app, ["indices", "import", "--json"])

    assert imported.exit_code == 0
    assert json.loads(imported.stdout) == {
        "rows": 3,
        "db_path": "data/output/stocky.db",
        "file": str(path.relative_to(tmp_path)),
    }


def test_indices_query_reports_no_match_and_missing_table(tmp_path, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    missing = runner.invoke(app, ["indices", "query", "NIFTY", "--db", str(db_path), "--json"])
    runner.invoke(
        app,
        [
            "indices",
            "import",
            "--file",
            str(_write_cli_indices_csv(tmp_path / "instruments.csv")),
            "--db",
            str(db_path),
        ],
    )
    no_match = runner.invoke(app, ["indices", "query", "nothing-like-this", "--db", str(db_path)])

    assert missing.exit_code == 1
    assert missing.stdout == ""
    assert "stocky indices import" in missing.stderr
    assert no_match.exit_code == 0
    assert "No indices match 'nothing-like-this'" in no_match.stdout


def test_indices_query_reports_no_match_when_matches_are_empty(monkeypatch, runner) -> None:
    monkeypatch.setattr(cli, "search_indices", lambda *args, **kwargs: cli.IndicesSearchResult(matches=[], total=131))

    result = runner.invoke(app, ["indices", "query", "NIFTY"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "No indices match 'NIFTY'."


@pytest.mark.parametrize("json_output", [False, True])
def test_indices_import_reports_missing_file(tmp_path, runner, json_output) -> None:
    result = runner.invoke(
        app,
        [
            "indices",
            "import",
            "--file",
            str(tmp_path / "absent.csv"),
            "--db",
            str(tmp_path / "x.db"),
            *(["--json"] if json_output else []),
        ],
    )

    assert result.exit_code == 1
    assert "Zerodha instruments not found" in (result.stderr if json_output else result.stdout)
    assert not (tmp_path / "x.db").exists()
    if json_output:
        assert result.stdout == ""
