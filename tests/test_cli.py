import sqlite3
from datetime import date

import pytest
import typer

import stocky.cli as cli
from stocky import __version__
from stocky.cli import app
from stocky.database import initialize_database
from stocky.yahoo import YahooUpdateResult


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
            ("INE001", "equity", "SHARED", None, "ONE", None, "FIRST LTD"),
            ("INE002", "equity", "TWO", None, "SHARED", None, "SECOND LTD"),
        ],
    )

    result = runner.invoke(app, ["lookup", "SHARED", "--db-path", str(db_path)])

    assert result.exit_code == 0
    assert "INE001" in result.stdout
    assert "INE002" in result.stdout
    assert "Multiple instruments match; refine the identifier." in result.stdout


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
            "--key",
            "nse_symbol",
            "--db-path",
            str(db_path),
            "--limit",
            "60",
            "--missing-only",
        ],
    )

    assert result.exit_code == 0
    assert calls[0] == ("init", db_path)
    assert calls[1][1] | {"progress": None} == {
        "key": "nse_symbol",
        "exchange": "NSE",
        "dry_run": False,
        "limit": 60,
        "missing_only": True,
        "progress": None,
    }
    assert callable(calls[1][1]["progress"])
    assert "50/60 processed; 48 written; 2 skipped" in result.stdout
    assert "Processed 60; wrote 58; skipped 2; dry_run=False." in result.stdout


def test_yahoo_import_cache_skips_bad_file(tmp_path, runner) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "GOOD.NS.json").write_text('{"price": 1}', encoding="utf-8")
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


def test_interactive_searches_once_then_quits(tmp_path, monkeypatch, seed_consolidated, runner) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", db_path)

    result = runner.invoke(app, ["interactive"], input="5\nINFY\n6\n")

    assert result.exit_code == 0
    assert "Matches for 'INFY'" in result.stdout
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
