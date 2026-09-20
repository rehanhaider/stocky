import json
import sqlite3

import pytest
from typer.testing import CliRunner

from stocky.cli import app
from stocky.database import CONSOLIDATED_TABLE
from stocky.export import EXPORT_COLUMNS, export_consolidated, read_consolidated

runner = CliRunner()


def _seed_consolidated(db_path) -> None:
    with sqlite3.connect(db_path) as con:
        con.execute(
            f"""
            CREATE TABLE {CONSOLIDATED_TABLE} (
                isin TEXT, ins_type TEXT, zd_symbol TEXT, yq_symbol TEXT,
                nse_symbol TEXT, bse_sc_code TEXT, bse_sc_name TEXT
            )
            """
        )
        con.executemany(
            f"INSERT INTO {CONSOLIDATED_TABLE} VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("INE002A01018", "equity", "RELIANCE", "RELIANCE", "RELIANCE", "500325", "RELIANCE INDUSTRIES"),
                ("INE009A01021", "equity", "INFY", "INFY", "INFY", "500209", "INFOSYS LTD"),
                ("INE144J01027", "equity", "20MICRONS", "", None, "533022", "20 MICRONS LTD"),
                ("INE999Z01019", "equity", "INFYBEES", None, None, None, "INFY ETF"),
            ],
        )


def _seeded_db(tmp_path):
    db_path = tmp_path / "stocky.db"
    _seed_consolidated(db_path)
    return db_path


def test_csv_export_keeps_column_order_empty_nulls_and_text_codes(tmp_path) -> None:
    db_path = _seeded_db(tmp_path)
    output = tmp_path / "out" / "all.csv"

    result = export_consolidated(db_path, output=output, format=None)

    assert result.rows == 4
    assert result.columns == EXPORT_COLUMNS
    assert result.format == "csv"
    assert result.output == output

    lines = output.read_text(encoding="utf-8").split("\n")
    assert lines[0] == ",".join(EXPORT_COLUMNS)
    assert lines[1] == "INE002A01018,equity,RELIANCE,RELIANCE,RELIANCE,500325,RELIANCE INDUSTRIES"
    assert lines[4] == "INE999Z01019,equity,INFYBEES,,,,INFY ETF"


def test_csv_export_writes_header_for_zero_rows(tmp_path) -> None:
    db_path = tmp_path / "empty.db"
    with sqlite3.connect(db_path) as con:
        con.execute(f"CREATE TABLE {CONSOLIDATED_TABLE} (isin TEXT, bse_sc_code TEXT)")
    output = tmp_path / "none.csv"

    result = export_consolidated(db_path, output=output, format=None, columns=["isin", "bse_sc_code"])

    assert result.rows == 0
    assert output.read_text(encoding="utf-8") == "isin,bse_sc_code\n"


def test_json_export_preserves_key_order_and_nulls(tmp_path) -> None:
    db_path = _seeded_db(tmp_path)
    output = tmp_path / "all.json"

    result = export_consolidated(db_path, output=output, format=None)

    assert result.format == "json"
    text = output.read_text(encoding="utf-8")
    assert text.endswith("\n")
    records = json.loads(text)
    assert len(records) == 4
    assert list(records[0]) == list(EXPORT_COLUMNS)
    assert records[0]["bse_sc_code"] == "500325"
    last = records[-1]
    assert last["yq_symbol"] is None
    assert last["bse_sc_code"] is None


def test_json_export_of_zero_rows_is_an_empty_list(tmp_path) -> None:
    db_path = tmp_path / "empty.db"
    with sqlite3.connect(db_path) as con:
        con.execute(f"CREATE TABLE {CONSOLIDATED_TABLE} (isin TEXT, zd_symbol TEXT)")
    output = tmp_path / "empty.json"

    result = export_consolidated(db_path, output=output, format=None, columns=["isin", "zd_symbol"])

    assert result.rows == 0
    assert json.loads(output.read_text(encoding="utf-8")) == []


def test_columns_subset_and_order_are_honoured(tmp_path) -> None:
    db_path = _seeded_db(tmp_path)

    frame = read_consolidated(db_path, columns=["zd_symbol", "isin"])

    assert list(frame.columns) == ["zd_symbol", "isin"]
    assert frame["isin"].tolist() == [
        "INE002A01018",
        "INE009A01021",
        "INE144J01027",
        "INE999Z01019",
    ]


def test_require_filters_blank_and_null_values(tmp_path) -> None:
    db_path = _seeded_db(tmp_path)

    frame = read_consolidated(db_path, require=["zd_symbol", "yq_symbol"])

    assert frame["zd_symbol"].tolist() == ["RELIANCE", "INFY"]


def test_unknown_columns_raise_value_error_listing_valid_columns(tmp_path) -> None:
    db_path = _seeded_db(tmp_path)

    with pytest.raises(ValueError) as column_error:
        read_consolidated(db_path, columns=["isin", "nope"])
    assert "Unknown column 'nope'" in str(column_error.value)
    assert "bse_sc_name" in str(column_error.value)

    with pytest.raises(ValueError) as require_error:
        read_consolidated(db_path, require=["bse_sc_name"])
    assert "Unknown require column 'bse_sc_name'" in str(require_error.value)
    assert "zd_symbol, yq_symbol, nse_symbol, bse_sc_code" in str(require_error.value)

    with pytest.raises(ValueError):
        read_consolidated(db_path, columns=["isin", "isin"])


def test_format_inference_and_its_failures(tmp_path) -> None:
    db_path = _seeded_db(tmp_path)

    upper = export_consolidated(db_path, output=tmp_path / "out.CSV", format=None)
    assert upper.format == "csv"
    assert export_consolidated(db_path, output=tmp_path / "out.json", format=None).format == "json"

    with pytest.raises(ValueError, match="Pass --format when writing to stdout."):
        export_consolidated(db_path, output=None, format=None)

    with pytest.raises(ValueError) as suffix_error:
        export_consolidated(db_path, output=tmp_path / "out.txt", format=None)
    assert "csv, json" in str(suffix_error.value)

    with pytest.raises(ValueError):
        export_consolidated(db_path, output=tmp_path / "out.csv", format="xml")


def test_missing_database_raises_without_creating_the_file(tmp_path) -> None:
    db_path = tmp_path / "missing.db"
    output = tmp_path / "out.csv"

    with pytest.raises(FileNotFoundError):
        export_consolidated(db_path, output=output, format=None)

    assert not db_path.exists()
    assert not output.exists()


def test_cli_export_to_file_prints_summary(tmp_path) -> None:
    db_path = _seeded_db(tmp_path)
    output = tmp_path / "out.csv"

    result = runner.invoke(app, ["export", "-o", str(output), "--db-path", str(db_path)])

    assert result.exit_code == 0
    assert "Wrote 4 rows" in result.stdout
    assert output.exists()


def test_cli_export_to_stdout_writes_json(tmp_path) -> None:
    db_path = _seeded_db(tmp_path)

    result = runner.invoke(
        app,
        ["export", "--format", "json", "--columns", "isin, zd_symbol", "--db-path", str(db_path)],
    )

    assert result.exit_code == 0
    records = json.loads(result.stdout)
    assert list(records[0]) == ["isin", "zd_symbol"]


def test_cli_export_with_invalid_column_reports_on_stderr(tmp_path) -> None:
    db_path = _seeded_db(tmp_path)

    result = runner.invoke(app, ["export", "--format", "csv", "--columns", "nope", "--db-path", str(db_path)])

    assert result.exit_code == 1
    assert "Unknown column 'nope'" in result.stderr
    assert "Unknown column" not in result.stdout
    assert result.stdout == ""


def test_cli_export_with_missing_database_keeps_stdout_empty(tmp_path) -> None:
    db_path = tmp_path / "missing.db"

    result = runner.invoke(app, ["export", "--format", "csv", "--db-path", str(db_path)])

    assert result.exit_code == 1
    assert "Database not found" in result.stderr
    assert "Database not found" not in result.stdout
    assert result.stdout == ""
