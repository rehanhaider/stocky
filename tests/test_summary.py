import json
import sqlite3

import pytest

from stocky.cli import app
from stocky.database import connect, encode_response_json, initialize_database, upsert_yahoo_response
from stocky.pipeline import rebuild_database
from stocky.summary import build_refresh_summary, render_markdown


def _rebuilt_database(tmp_path, market_csv_builder):
    db_path = tmp_path / "stocky.db"
    rebuild_database(market_csv_builder(tmp_path / "inputs"), db_path=db_path, backup=False)
    return db_path


def _checks(summary) -> dict[str, bool]:
    return {check.name: check.passed for check in summary.checks}


def test_summary_of_a_rebuilt_database_passes_and_lists_sources_counts_and_gaps(tmp_path, market_csv_builder) -> None:
    db_path = _rebuilt_database(tmp_path, market_csv_builder)
    initialize_database(db_path)
    with connect(db_path) as con:
        upsert_yahoo_response(
            con,
            yahoo_symbol="RELIANCE.NS",
            symbol="RELIANCE",
            exchange="NSE",
            response_json=encode_response_json({}),
            source="test",
        )

    summary = build_refresh_summary(db_path)

    assert summary.passed, summary.checks
    assert summary.table_counts == {
        "consolidated": 1,
        "provenance_builds": 1,
        "provenance_sources": 3,
        "yahoo_responses": 1,
    }
    assert [(entry.exchange, entry.listed, entry.missing) for entry in summary.missing_yahoo] == [
        ("NSE", 1, 0),
        # The legacy fixture bhavcopy has no BSE trading symbol, so no BSE ticker is listed.
        ("BSE", 0, 0),
    ]
    markdown = render_markdown(summary)
    assert "**Validation: passed**" in markdown
    assert "| BSE bhavcopy | `BSE-EQ_ISINCODE_030521.CSV` | 2021-05-03 | 1 |" in markdown
    assert "| `yahoo_responses` | 1 |" in markdown
    assert "| `yq_ns` | 1 | 0 | 100.0% |" in markdown


def test_summary_lists_tickers_without_a_yahoo_response_up_to_the_sample_size(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    summary = build_refresh_summary(db_path, sample_size=1)

    nse, bse = summary.missing_yahoo
    assert (nse.listed, nse.missing, nse.sample) == (2, 2, ["INFY.NS"])
    assert (bse.listed, bse.missing) == (2, 2)
    assert "NSE (first 1 of 2): `INFY.NS`" in render_markdown(summary)


def test_summary_fails_without_provenance(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    summary = build_refresh_summary(db_path)

    assert not summary.passed
    assert _checks(summary)["Source provenance"] is False
    assert "No source provenance is recorded" in render_markdown(summary)


def test_summary_fails_when_the_table_no_longer_matches_its_build(tmp_path, market_csv_builder) -> None:
    db_path = _rebuilt_database(tmp_path, market_csv_builder)
    with sqlite3.connect(db_path) as con:
        # A duplicate ISIN whose Yahoo ticker does not follow its symbol, written outside a rebuild.
        con.execute(
            "INSERT INTO consolidated (isin, ins_type, nse_symbol, yq_ns) "
            "VALUES ('INE002A01018', 'equity', 'RELIANCE', 'RIL.NS')"
        )

    checks = _checks(build_refresh_summary(db_path))

    assert checks["ISINs present and unique"] is False
    assert checks["Yahoo tickers follow exchange symbols"] is False
    assert checks["Source provenance"] is False
    assert checks["Consolidated table layout"] is True


def test_summary_reports_an_older_layout_without_failing_to_run(tmp_path) -> None:
    db_path = tmp_path / "stocky.db"
    with sqlite3.connect(db_path) as con:
        con.execute("CREATE TABLE consolidated (isin TEXT, zd_symbol TEXT)")
        con.execute("INSERT INTO consolidated VALUES ('INE002A01018', 'RELIANCE')")

    summary = build_refresh_summary(db_path)

    assert _checks(summary)["Consolidated table layout"] is False
    assert summary.status is None
    assert "**Validation: FAILED**" in render_markdown(summary)


def test_summary_rejects_a_missing_database(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="Database not found"):
        build_refresh_summary(tmp_path / "missing.db")


def test_summary_command_writes_markdown_and_exits_cleanly_when_valid(tmp_path, market_csv_builder, runner) -> None:
    db_path = _rebuilt_database(tmp_path, market_csv_builder)
    output = tmp_path / "reports" / "refresh-summary.md"

    result = runner.invoke(app, ["summary", "--db-path", str(db_path), "--output", str(output)])

    assert result.exit_code == 0
    assert output.read_text(encoding="utf-8").startswith("# Stocky refresh summary")
    assert "Wrote the refresh summary" in result.stderr


def test_summary_command_writes_the_summary_and_then_fails_on_a_failed_check(
    tmp_path, seed_consolidated, runner
) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = runner.invoke(app, ["summary", "--db-path", str(db_path)])
    payload = json.loads(runner.invoke(app, ["summary", "--db-path", str(db_path), "--json"]).stdout)

    assert result.exit_code == 1
    assert "**Validation: FAILED**" in result.stdout
    # The seed also gives 20MICRONS a BSE symbol without its Yahoo ticker.
    assert "Validation failed: Yahoo tickers follow exchange symbols, Source provenance." in result.stderr
    assert payload["passed"] is False
    assert payload["table_counts"] == {"consolidated": 4}


def test_summary_command_reports_a_missing_database(tmp_path, runner) -> None:
    result = runner.invoke(app, ["summary", "--db-path", str(tmp_path / "missing.db")])

    assert result.exit_code == 1
    assert "Database not found" in result.stderr
