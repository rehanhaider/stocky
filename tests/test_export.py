import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from stocky import __version__
from stocky.cli import app
from stocky.database import CONSOLIDATED_TABLE
from stocky.export import (
    EXPORT_COLUMNS,
    SNAPSHOT_MANIFEST,
    create_snapshot,
    export_consolidated,
    export_tickers,
    read_consolidated,
)


def _seeded_db(tmp_path, seed_consolidated):
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)
    return db_path


def test_csv_export_keeps_column_order_empty_nulls_and_text_codes(tmp_path, seed_consolidated) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)
    output = tmp_path / "out" / "all.csv"

    result = export_consolidated(db_path, output=output, format=None)

    assert result.rows == 4
    assert result.columns == EXPORT_COLUMNS
    assert result.format == "csv"
    assert result.output == output

    lines = output.read_text(encoding="utf-8").split("\n")
    assert lines[0] == ",".join(EXPORT_COLUMNS)
    assert (
        lines[1]
        == "INE002A01018,equity,RELIANCE,RELIANCE,500325,RELIANCE INDUSTRIES,RELIANCE,RELIANCE,RELIANCE.NS,RELIANCE.BO"
    )
    assert lines[4] == "INE999Z01019,equity,,,,INFY ETF,INFYBEES,,,"


def test_csv_export_writes_header_for_zero_rows(tmp_path) -> None:
    db_path = tmp_path / "empty.db"
    with sqlite3.connect(db_path) as con:
        con.execute(f"CREATE TABLE {CONSOLIDATED_TABLE} (isin TEXT, bse_sc_code TEXT)")
    output = tmp_path / "none.csv"

    result = export_consolidated(db_path, output=output, format=None, columns=["isin", "bse_sc_code"])

    assert result.rows == 0
    assert output.read_text(encoding="utf-8") == "isin,bse_sc_code\n"


def test_json_export_preserves_key_order_and_nulls(tmp_path, seed_consolidated) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)
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
    assert records[2]["yq_bo"] == ""
    assert records[2]["yq_ns"] is None
    assert last["bse_sc_code"] is None


def test_json_export_of_zero_rows_is_an_empty_list(tmp_path) -> None:
    db_path = tmp_path / "empty.db"
    with sqlite3.connect(db_path) as con:
        con.execute(f"CREATE TABLE {CONSOLIDATED_TABLE} (isin TEXT, zd_ns TEXT)")
    output = tmp_path / "empty.json"

    result = export_consolidated(db_path, output=output, format=None, columns=["isin", "zd_ns"])

    assert result.rows == 0
    assert json.loads(output.read_text(encoding="utf-8")) == []


def test_parquet_export_preserves_schema_text_codes_and_nulls(tmp_path, seed_consolidated) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)
    output = tmp_path / "out" / "all.parquet"

    result = export_consolidated(db_path, output=output, format=None)

    assert result.rows == 4
    assert result.columns == EXPORT_COLUMNS
    assert result.format == "parquet"
    frame = pd.read_parquet(output)
    assert list(frame.columns) == list(EXPORT_COLUMNS)
    assert pd.api.types.is_string_dtype(frame["bse_sc_code"].dtype)
    assert frame.loc[0, "bse_sc_code"] == "500325"
    assert pd.isna(frame.loc[3, "bse_sc_code"])
    schema = pq.read_schema(output)
    assert schema.names == list(EXPORT_COLUMNS)
    assert all(field.type == pa.string() for field in schema)


def test_parquet_export_of_zero_rows_keeps_full_schema(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "empty.db"
    seed_consolidated(db_path, [])
    output = tmp_path / "empty.parquet"

    result = export_consolidated(db_path, output=output, format=None)

    assert result.rows == 0
    frame = pd.read_parquet(output)
    assert frame.empty
    assert list(frame.columns) == list(EXPORT_COLUMNS)
    assert all(field.type == pa.string() for field in pq.read_schema(output))


def test_parquet_export_honours_columns_and_require(tmp_path, seed_consolidated) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)
    output = tmp_path / "filtered.parquet"

    result = export_consolidated(
        db_path,
        output=output,
        format="parquet",
        columns=["bse_sc_code", "isin"],
        require=["zd_bo", "yq_bo"],
    )

    assert result.rows == 2
    frame = pd.read_parquet(output)
    assert list(frame.columns) == ["bse_sc_code", "isin"]
    assert frame["bse_sc_code"].tolist() == ["500325", "500209"]


def test_columns_subset_and_order_are_honoured(tmp_path, seed_consolidated) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)

    frame = read_consolidated(db_path, columns=["zd_ns", "isin"])

    assert list(frame.columns) == ["zd_ns", "isin"]
    assert frame["isin"].tolist() == [
        "INE002A01018",
        "INE009A01021",
        "INE144J01027",
        "INE999Z01019",
    ]


def test_require_filters_blank_and_null_values(tmp_path, seed_consolidated) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)

    # 20MICRONS has zd_bo but a blank yq_bo, and INFYBEES has neither.
    frame = read_consolidated(db_path, require=["zd_bo", "yq_bo"])

    assert frame["zd_bo"].tolist() == ["RELIANCE", "INFY"]


def test_unknown_columns_raise_value_error_listing_valid_columns(tmp_path, seed_consolidated) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)

    with pytest.raises(ValueError) as column_error:
        read_consolidated(db_path, columns=["isin", "nope"])
    assert "Unknown column 'nope'" in str(column_error.value)
    assert "bse_sc_name" in str(column_error.value)

    with pytest.raises(ValueError) as require_error:
        read_consolidated(db_path, require=["bse_sc_name"])
    assert "Unknown require column 'bse_sc_name'" in str(require_error.value)
    assert "nse_symbol, bse_symbol, bse_sc_code, zd_ns, zd_bo, yq_ns, yq_bo" in str(require_error.value)

    with pytest.raises(ValueError):
        read_consolidated(db_path, columns=["isin", "isin"])


def test_format_inference_and_its_failures(tmp_path, seed_consolidated) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)

    upper = export_consolidated(db_path, output=tmp_path / "out.CSV", format=None)
    assert upper.format == "csv"
    assert export_consolidated(db_path, output=tmp_path / "out.json", format=None).format == "json"
    assert export_consolidated(db_path, output=tmp_path / "out.parquet", format=None).format == "parquet"

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


def test_missing_consolidated_table_raises_runtime_error(tmp_path) -> None:
    db_path = tmp_path / "empty.db"
    sqlite3.connect(db_path).close()

    with pytest.raises(RuntimeError, match="consolidated.*does not exist"):
        read_consolidated(db_path)


def test_cli_export_to_file_prints_summary(tmp_path, seed_consolidated, runner) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)
    output = tmp_path / "out.csv"

    result = runner.invoke(app, ["export", "-o", str(output), "--db-path", str(db_path)])

    assert result.exit_code == 0
    assert "Wrote 4 rows" in result.stdout
    assert output.exists()


def test_cli_parquet_export_to_file_prints_summary(tmp_path, seed_consolidated, runner) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)
    output = tmp_path / "out.parquet"

    result = runner.invoke(app, ["export", "-o", str(output), "--db-path", str(db_path)])

    assert result.exit_code == 0
    assert "Wrote 4 rows" in result.stdout
    assert "as parquet." in result.stdout.replace("\n", "")
    assert output.exists()


def test_cli_parquet_export_to_stdout_writes_binary(tmp_path, seed_consolidated, runner) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)

    result = runner.invoke(app, ["export", "--format", "parquet", "--db-path", str(db_path)])

    assert result.exit_code == 0
    assert result.stdout_bytes.startswith(b"PAR1")


def test_parquet_export_refuses_interactive_stdout_before_database_read(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "missing.db"
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    with pytest.raises(
        ValueError,
        match=r"^Parquet output is binary\. Pass --output FILE or redirect stdout to a file\.$",
    ):
        export_consolidated(db_path, output=None, format="parquet")

    assert not db_path.exists()


def test_parquet_export_writes_binary_to_piped_stdout(tmp_path, seed_consolidated, monkeypatch, capsysbinary) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)

    result = export_consolidated(db_path, output=None, format="parquet")

    assert result.rows == 4
    assert capsysbinary.readouterr().out.startswith(b"PAR1")


def test_cli_parquet_export_to_interactive_stdout_reports_error(tmp_path, monkeypatch, runner) -> None:
    db_path = tmp_path / "missing.db"
    with runner.isolation():
        runner_stdout_type = type(sys.stdout)
    monkeypatch.setattr(runner_stdout_type, "isatty", lambda self: True)

    result = runner.invoke(app, ["export", "--format", "parquet", "--db-path", str(db_path)])

    assert result.exit_code == 1
    assert "Parquet output is binary. Pass --output FILE or redirect stdout to a file." in result.stderr
    assert result.stdout == ""
    assert not db_path.exists()


def test_cli_export_to_stdout_writes_json(tmp_path, seed_consolidated, runner) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)

    result = runner.invoke(
        app,
        ["export", "--format", "json", "--columns", "isin, zd_ns", "--db-path", str(db_path)],
    )

    assert result.exit_code == 0
    records = json.loads(result.stdout)
    assert list(records[0]) == ["isin", "zd_ns"]


def test_cli_export_with_invalid_column_reports_on_stderr(tmp_path, seed_consolidated, runner) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)

    result = runner.invoke(app, ["export", "--format", "csv", "--columns", "nope", "--db-path", str(db_path)])

    assert result.exit_code == 1
    assert "Unknown column 'nope'" in result.stderr
    assert "Unknown column" not in result.stdout
    assert result.stdout == ""


def test_cli_export_with_missing_database_keeps_stdout_empty(tmp_path, runner) -> None:
    db_path = tmp_path / "missing.db"

    result = runner.invoke(app, ["export", "--format", "csv", "--db-path", str(db_path)])

    assert result.exit_code == 1
    assert "Database not found" in result.stderr
    assert "Database not found" not in result.stdout
    assert result.stdout == ""


def test_missing_pyarrow_fails_before_database_read_or_output_creation(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "missing.db"
    output = tmp_path / "out.parquet"
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", None)

    with pytest.raises(RuntimeError, match="uv sync --extra parquet"):
        export_consolidated(db_path, output=output, format="parquet")

    assert not db_path.exists()
    assert not output.exists()


def test_empty_columns_or_require_selection_raises_value_error(tmp_path, seed_consolidated) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)

    with pytest.raises(ValueError) as columns_error:
        read_consolidated(db_path, columns=[])
    assert "--columns must name at least one column." in str(columns_error.value)

    with pytest.raises(ValueError) as require_error:
        read_consolidated(db_path, require=[])
    assert "--require must name at least one column." in str(require_error.value)


def test_cli_export_with_empty_columns_reports_on_stderr(tmp_path, seed_consolidated, runner) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)

    result = runner.invoke(app, ["export", "--format", "csv", "--columns", "", "--db-path", str(db_path)])

    assert result.exit_code == 1
    assert "--columns must name at least one column." in result.stderr
    assert result.stdout == ""


def test_cli_export_with_empty_require_reports_on_stderr(tmp_path, seed_consolidated, runner) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)

    result = runner.invoke(app, ["export", "--format", "csv", "--require", "", "--db-path", str(db_path)])

    assert result.exit_code == 1
    assert "--require must name at least one column." in result.stderr
    assert result.stdout == ""


def test_cli_export_summary_escapes_markup_in_output_path(tmp_path, seed_consolidated, runner) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)
    output = tmp_path / "out[/red].csv"

    result = runner.invoke(app, ["export", "-o", str(output), "--db-path", str(db_path)])

    assert result.exit_code == 0
    assert result.exception is None
    assert output.exists()
    assert output.read_text(encoding="utf-8").split("\n")[0] == ",".join(EXPORT_COLUMNS)
    assert "Wrote 4 rows" in result.stdout
    assert "out[/red].csv" in result.stdout.replace("\n", "")


def test_tickers_skip_unpopulated_rows_and_keep_isin_order(tmp_path, seed_consolidated) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)
    output = tmp_path / "out" / "yahoo.txt"

    result = export_tickers(db_path, output=output, column="yq_bo")

    assert result.rows == 2
    assert result.columns == ("yq_bo",)
    assert result.format == "tickers"
    assert output.read_text(encoding="utf-8") == "RELIANCE.BO\nINFY.BO\n"


def test_tickers_append_suffix_and_drop_repeats(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(
        db_path,
        [
            ("INE001", "equity", "AAA", None, "1", "A", None, None, None, None),
            ("INE002", "equity", " AAA ", None, "2", "A2", None, None, None, None),
            ("INE003", "equity", "BBB", None, None, "B", None, None, None, None),
        ],
    )
    output = tmp_path / "yahoo.txt"

    result = export_tickers(db_path, output=output, column="nse_symbol", suffix=".NS")

    assert result.rows == 2
    assert output.read_text(encoding="utf-8") == "AAA.NS\nBBB.NS\n"


def test_tickers_honour_extra_require_and_text_codes(tmp_path, seed_consolidated) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)
    output = tmp_path / "bse.txt"

    result = export_tickers(db_path, output=output, column="bse_sc_code", require=["nse_symbol", "bse_sc_code"])

    assert result.rows == 2
    assert output.read_text(encoding="utf-8") == "500325\n500209\n"


def test_tickers_reject_unknown_column(tmp_path, seed_consolidated) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)

    with pytest.raises(ValueError, match="Unknown tickers column 'isin'"):
        export_tickers(db_path, output=None, column="isin")


def test_tickers_of_zero_rows_write_an_empty_file(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path, [])
    output = tmp_path / "none.txt"

    result = export_tickers(db_path, output=output, column="zd_ns")

    assert result.rows == 0
    assert output.read_text(encoding="utf-8") == ""


def test_cli_tickers_to_stdout_write_only_tickers(tmp_path, seed_consolidated, runner) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)

    result = runner.invoke(
        app,
        ["export", "--tickers", "zd_ns", "--suffix", ".NS", "--require", "nse_symbol", "--db-path", str(db_path)],
    )

    assert result.exit_code == 0
    assert result.stdout == "RELIANCE.NS\nINFY.NS\n"


def test_cli_tickers_to_file_prints_summary(tmp_path, seed_consolidated, runner) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)
    output = tmp_path / "zerodha.txt"

    result = runner.invoke(app, ["export", "--tickers", "zd_bo", "-o", str(output), "--db-path", str(db_path)])

    assert result.exit_code == 0
    assert "Wrote 3 tickers from zd_bo" in result.stdout
    assert output.read_text(encoding="utf-8").splitlines() == ["RELIANCE", "INFY", "20MICRONS"]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--tickers", "yq_ns", "--format", "csv"], "cannot be combined with --format or --columns"),
        (["--tickers", "yq_ns", "--columns", "isin"], "cannot be combined with --format or --columns"),
        (["--format", "csv", "--suffix", ".NS"], "--suffix only applies with --tickers"),
    ],
)
def test_cli_tickers_reject_conflicting_options(tmp_path, seed_consolidated, runner, args, message) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)

    result = runner.invoke(app, ["export", *args, "--db-path", str(db_path)])

    assert result.exit_code == 1
    assert message in " ".join(result.stderr.split())
    assert result.stdout == ""


def test_snapshot_writes_versioned_files_and_checksummed_manifest(tmp_path, seed_consolidated) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)
    output_dir = tmp_path / "snapshots"

    result = create_snapshot(db_path, output_dir=output_dir, version="2026.09")

    directory = output_dir / "consolidated-2026.09"
    assert result.directory == directory
    assert result.version == "2026.09"
    assert result.rows == 4
    assert sorted(path.name for path in directory.iterdir()) == [
        "consolidated.csv",
        "consolidated.parquet",
        SNAPSHOT_MANIFEST,
    ]
    assert [path.name for path in output_dir.iterdir()] == ["consolidated-2026.09"]

    manifest = json.loads((directory / SNAPSHOT_MANIFEST).read_text(encoding="utf-8"))
    assert manifest["version"] == "2026.09"
    assert manifest["stocky_version"] == __version__
    assert manifest["table"] == CONSOLIDATED_TABLE
    assert manifest["rows"] == 4
    assert manifest["columns"] == list(EXPORT_COLUMNS)
    assert [entry["name"] for entry in manifest["files"]] == ["consolidated.csv", "consolidated.parquet"]
    for entry in manifest["files"]:
        data = (directory / entry["name"]).read_bytes()
        assert entry["bytes"] == len(data)
        assert entry["sha256"] == hashlib.sha256(data).hexdigest()

    csv_lines = (directory / "consolidated.csv").read_text(encoding="utf-8").splitlines()
    assert (
        csv_lines[1]
        == "INE002A01018,equity,RELIANCE,RELIANCE,500325,RELIANCE INDUSTRIES,RELIANCE,RELIANCE,RELIANCE.NS,RELIANCE.BO"
    )
    table = pq.read_table(directory / "consolidated.parquet")
    assert table.num_rows == 4
    assert all(field.type == pa.string() for field in table.schema)


def test_snapshot_defaults_to_todays_utc_date(tmp_path, seed_consolidated) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)

    result = create_snapshot(db_path, output_dir=tmp_path, formats=["json"])

    assert result.version == datetime.now(timezone.utc).date().isoformat()
    assert [file.name for file in result.files] == ["consolidated.json"]
    assert len(json.loads((result.directory / "consolidated.json").read_text(encoding="utf-8"))) == 4


def test_snapshot_never_overwrites_an_existing_version(tmp_path, seed_consolidated) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)
    create_snapshot(db_path, output_dir=tmp_path / "snapshots", version="v1", formats=["csv"])
    csv_path = tmp_path / "snapshots" / "consolidated-v1" / "consolidated.csv"
    original = csv_path.read_bytes()

    with pytest.raises(FileExistsError, match="already exists"):
        create_snapshot(db_path, output_dir=tmp_path / "snapshots", version="v1", formats=["csv"])

    assert csv_path.read_bytes() == original


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"version": "../escape"}, "Invalid snapshot version"),
        ({"version": ""}, "Invalid snapshot version"),
        ({"formats": ["xml"]}, "Unknown format 'xml'"),
        ({"formats": []}, "--formats must name at least one format"),
    ],
)
def test_snapshot_rejects_bad_version_or_formats_before_writing(tmp_path, seed_consolidated, kwargs, message) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)
    output_dir = tmp_path / "snapshots"

    with pytest.raises(ValueError, match=message):
        create_snapshot(db_path, output_dir=output_dir, **kwargs)

    assert not output_dir.exists()


def test_snapshot_failure_leaves_no_partial_version(tmp_path) -> None:
    output_dir = tmp_path / "snapshots"
    with sqlite3.connect(tmp_path / "stocky.db") as con:
        con.execute(f"CREATE TABLE {CONSOLIDATED_TABLE} (isin TEXT)")

    with pytest.raises(RuntimeError, match="older layout"):
        create_snapshot(tmp_path / "stocky.db", output_dir=output_dir, version="v1", formats=["csv"])

    assert not output_dir.exists() or list(output_dir.iterdir()) == []


def test_snapshot_render_failure_removes_staging_directory(tmp_path, seed_consolidated, monkeypatch) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)
    output_dir = tmp_path / "snapshots"

    def fail(frame):
        raise OSError("disk full")

    monkeypatch.setattr("stocky.export._render_parquet", fail)

    with pytest.raises(OSError, match="disk full"):
        create_snapshot(db_path, output_dir=output_dir, version="v1")

    assert list(output_dir.iterdir()) == []


def test_snapshot_without_pyarrow_fails_before_writing(tmp_path, monkeypatch) -> None:
    output_dir = tmp_path / "snapshots"
    monkeypatch.setitem(sys.modules, "pyarrow", None)

    with pytest.raises(RuntimeError, match="uv sync --extra parquet"):
        create_snapshot(tmp_path / "missing.db", output_dir=output_dir, version="v1")

    assert not output_dir.exists()


def test_cli_snapshot_prints_summary_and_json(tmp_path, seed_consolidated, runner) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)
    output_dir = tmp_path / "snapshots"

    result = runner.invoke(
        app,
        ["snapshot", "--output-dir", str(output_dir), "--version", "v1", "--formats", "csv", "--db-path", str(db_path)],
    )
    assert result.exit_code == 0
    assert "Wrote snapshot v1 (4 rows)" in result.stdout
    assert "consolidated.csv  sha256" in result.stdout

    result = runner.invoke(
        app,
        ["snapshot", "--output-dir", str(output_dir), "--version", "v2", "--db-path", str(db_path), "--json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["version"] == "v2"
    assert payload["rows"] == 4
    assert [file["format"] for file in payload["files"]] == ["csv", "parquet"]


def test_cli_snapshot_existing_version_reports_on_stderr(tmp_path, seed_consolidated, runner) -> None:
    db_path = _seeded_db(tmp_path, seed_consolidated)
    args = ["snapshot", "--output-dir", str(tmp_path), "--version", "v1", "--formats", "csv", "--db-path", str(db_path)]
    assert runner.invoke(app, args).exit_code == 0

    result = runner.invoke(app, args)

    assert result.exit_code == 1
    assert "already exists" in result.stderr
    assert result.stdout == ""
