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


def _refreshed_database(tmp_path, market_csv_builder, import_mapping_tables):
    """A rebuilt database whose mapping tables were all imported, as a full refresh leaves it."""
    db_path = _rebuilt_database(tmp_path, market_csv_builder)
    import_mapping_tables(tmp_path / "mapping", db_path)
    return db_path


def _checks(summary) -> dict[str, bool]:
    return {check.name: check.passed for check in summary.checks}


def test_summary_of_a_refreshed_database_passes_and_lists_sources_counts_and_gaps(
    tmp_path, market_csv_builder, import_mapping_tables
) -> None:
    db_path = _refreshed_database(tmp_path, market_csv_builder, import_mapping_tables)
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
        "broker_symbols": 1,
        "bse_debt": 3,
        "consolidated": 1,
        "indices": 1,
        "mutual_funds": 1,
        "provenance_builds": 1,
        "provenance_imports": 5,
        "provenance_sources": 3,
        "yahoo_responses": 1,
    }
    assert [(record.table_name, record.role, record.file_name, record.rows) for record in summary.imports] == [
        ("broker_symbols", "groww", "instrument.csv", 1),
        ("bse_debt", "bse_debt_bhavcopy", "DEBTBHAVCOPY16102026.zip", 3),
        ("indices", "zerodha_instruments", "instruments.csv", 1),
        ("mutual_funds", "amfi_nav", "NAVAll.txt", 1),
        ("mutual_funds", "zerodha_mf_instruments", "mf_instruments.csv", 1),
    ]
    modified = summary.imports[2].modified_at[:10]
    details = {check.name: check.detail for check in summary.checks}
    assert details["indices import"] == f"1 rows from `instruments.csv` (modified {modified})."
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
    record = summary.imports[1]
    assert (
        f"| `bse_debt` | BSE debt bhavcopy | `DEBTBHAVCOPY16102026.zip` | 3 | {record.size_bytes} "
        f"| {record.modified_at[:19].replace('T', ' ')} UTC | {record.imported_at[:19].replace('T', ' ')} UTC "
        f"by Stocky {record.stocky_version} | `{record.sha256}` |"
    ) in markdown


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
    assert "No import provenance is recorded" in render_markdown(summary)


def test_summary_fails_when_a_mapping_table_has_no_import_record(
    tmp_path, market_csv_builder, import_mapping_tables
) -> None:
    db_path = _refreshed_database(tmp_path, market_csv_builder, import_mapping_tables)
    with sqlite3.connect(db_path) as con:
        # A table filled before imports recorded their source files.
        con.execute("DELETE FROM provenance_imports WHERE table_name = 'mutual_funds'")

    summary = build_refresh_summary(db_path)

    assert not summary.passed
    assert [check.name for check in summary.checks if not check.passed] == ["mutual_funds import"]
    assert {check.name: check.detail for check in summary.checks}["mutual_funds import"] == (
        "1 rows; no import recorded. Run 'stocky mf import' to record it."
    )


def test_summary_fails_when_one_broker_with_rows_has_no_import_record(
    tmp_path, market_csv_builder, import_mapping_tables
) -> None:
    db_path = _refreshed_database(tmp_path, market_csv_builder, import_mapping_tables)
    with sqlite3.connect(db_path) as con:
        # Upstox rows left from before imports recorded their source files, beside a recorded Groww import.
        con.execute(
            "INSERT INTO broker_symbols (broker, exchange, exchange_token, isin, symbol, broker_key, name) "
            "VALUES ('upstox', 'NSE', '2885', 'INE002A01018', 'RELIANCE', 'NSE_EQ|INE002A01018', 'Reliance')"
        )

    summary = build_refresh_summary(db_path)

    assert [check.name for check in summary.checks if not check.passed] == ["broker_symbols import"]
    detail = {check.name: check.detail for check in summary.checks}["broker_symbols import"]
    assert detail.endswith("No import recorded for upstox. Run 'stocky brokers import --broker upstox' to record it.")


def test_summary_fails_when_a_mapping_table_is_empty(tmp_path, market_csv_builder, import_mapping_tables) -> None:
    db_path = _refreshed_database(tmp_path, market_csv_builder, import_mapping_tables)
    with sqlite3.connect(db_path) as con:
        con.execute("DELETE FROM broker_symbols")
        con.execute("DROP TABLE indices")

    summary = build_refresh_summary(db_path)

    details = {check.name: check.detail for check in summary.checks if not check.passed}
    assert details == {
        "indices import": "No rows. Run 'stocky indices import'.",
        "broker_symbols import": "No rows. Run 'stocky brokers import'.",
    }


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


def test_summary_command_writes_markdown_and_exits_cleanly_when_valid(
    tmp_path, market_csv_builder, import_mapping_tables, runner
) -> None:
    db_path = _refreshed_database(tmp_path, market_csv_builder, import_mapping_tables)
    output = tmp_path / "reports" / "refresh-summary.md"

    result = runner.invoke(app, ["summary", "--db-path", str(db_path), "--output", str(output)])

    assert result.exit_code == 0
    assert output.read_text(encoding="utf-8").startswith("# Stocky refresh summary")
    assert "Wrote the refresh summary" in result.stderr
    payload = json.loads(runner.invoke(app, ["summary", "--db-path", str(db_path), "--json"]).stdout)
    assert payload["passed"] is True
    assert {(record["table_name"], record["file_name"]) for record in payload["imports"]} == {
        ("broker_symbols", "instrument.csv"),
        ("bse_debt", "DEBTBHAVCOPY16102026.zip"),
        ("indices", "instruments.csv"),
        ("mutual_funds", "NAVAll.txt"),
        ("mutual_funds", "mf_instruments.csv"),
    }


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
    assert (
        "Validation failed: Yahoo tickers follow exchange symbols, Source provenance, mutual_funds import, "
        "bse_debt import, indices import, broker_symbols import." in " ".join(result.stderr.split())
    )
    assert payload["passed"] is False
    assert payload["table_counts"] == {"consolidated": 4}


def test_summary_command_reports_a_missing_database(tmp_path, runner) -> None:
    result = runner.invoke(app, ["summary", "--db-path", str(tmp_path / "missing.db")])

    assert result.exit_code == 1
    assert "Database not found" in result.stderr
