import hashlib
import sqlite3
from datetime import date
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd
import pytest

from stocky import __version__
from stocky.database import CONSOLIDATED_COLUMNS, CONSOLIDATED_TABLE, PROVENANCE_BUILDS_TABLE, read_latest_provenance
from stocky.pipeline import (
    RowChange,
    SourcePreview,
    build_consolidated_dataframe,
    diff_consolidated,
    load_bse_equities,
    load_nse_equities,
    load_zerodha_instruments,
    match_zerodha_symbols,
    match_zerodha_symbols_by_name,
    preview_sources,
    rebuild_database,
)


def test_build_consolidated_dataframe_fills_each_exchange_only_where_it_lists_the_isin() -> None:
    bse = pd.DataFrame(
        [
            {"isin": "INE002A01018", "bse_sc_code": "500325", "bse_symbol": "RELIANCE", "bse_sc_name": "RELIANCE"},
            {"isin": "INE198N01017", "bse_sc_code": "534796", "bse_symbol": "CDG", "bse_sc_name": "CDG PETCHEM"},
        ]
    )
    nse = pd.DataFrame(
        [
            {"isin": "INE002A01018", "nse_symbol": "RELIANCE", "nse_token": "2885"},
            {"isin": "INE581X01021", "nse_symbol": "GLOBE", "nse_token": "1111"},
            {"isin": "INE593W01028", "nse_symbol": "FOCUS", "nse_token": "2222"},
        ]
    )
    zerodha = pd.DataFrame(
        [
            {"segment": "NSE", "exchange_token": "2885", "tradingsymbol": "RELIANCE"},
            {"segment": "BSE", "exchange_token": "500325", "tradingsymbol": "RELIANCE"},
            {"segment": "NSE", "exchange_token": "1111", "tradingsymbol": "GLOBE"},
            {"segment": "BSE", "exchange_token": "534796", "tradingsymbol": "CDG"},
            {"segment": "NSE", "exchange_token": "2222", "tradingsymbol": "FOCUS-BE"},
            # A different company that BSE lists under the NSE share's symbol.
            {"segment": "BSE", "exchange_token": "999999", "tradingsymbol": "FOCUS"},
        ]
    )

    result = build_consolidated_dataframe(bse_equities=bse, nse_equities=nse, zerodha_instruments=zerodha)

    columns = ["nse_symbol", "bse_symbol", "zd_ns", "zd_bo", "yq_ns", "yq_bo"]
    rows = result[columns].astype(object).where(result[columns].notna(), None).to_dict("index")
    assert rows == {
        "INE002A01018": {
            "nse_symbol": "RELIANCE",
            "bse_symbol": "RELIANCE",
            "zd_ns": "RELIANCE",
            "zd_bo": "RELIANCE",
            "yq_ns": "RELIANCE.NS",
            "yq_bo": "RELIANCE.BO",
        },
        "INE581X01021": {
            "nse_symbol": "GLOBE",
            "bse_symbol": None,
            "zd_ns": "GLOBE",
            "zd_bo": None,
            "yq_ns": "GLOBE.NS",
            "yq_bo": None,
        },
        "INE198N01017": {
            "nse_symbol": None,
            "bse_symbol": "CDG",
            "zd_ns": None,
            "zd_bo": "CDG",
            "yq_ns": None,
            "yq_bo": "CDG.BO",
        },
        "INE593W01028": {
            "nse_symbol": "FOCUS",
            "bse_symbol": None,
            "zd_ns": "FOCUS-BE",
            "zd_bo": None,
            "yq_ns": "FOCUS.NS",
            "yq_bo": None,
        },
    }
    assert list(result.columns) == [
        "ins_type",
        "nse_symbol",
        "bse_symbol",
        "bse_sc_code",
        "bse_sc_name",
        "zd_ns",
        "zd_bo",
        "yq_ns",
        "yq_bo",
    ]


def test_load_nse_equities_supports_udiff_zip(tmp_path) -> None:
    zip_path = tmp_path / "BhavCopy_NSE_CM_0_0_0_20260522_F_0000.csv.zip"
    csv_name = "BhavCopy_NSE_CM_0_0_0_20260522_F_0000.csv"
    csv_content = (
        "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,ClsPric\n"
        "2026-05-22,2026-05-22,CM,NSE,STK,1,INE002A01018,RELIANCE,EQ,100.00\n"
        "2026-05-22,2026-05-22,CM,NSE,STK,2,INE111B01023,63MOONS,BE,250.00\n"
        "2026-05-22,2026-05-22,CM,NSE,STK,3,IN0020200104,SGBJUN28,GB,15783.00\n"
    )
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as zip_file:
        zip_file.writestr(csv_name, csv_content)

    result = load_nse_equities(zip_path)

    assert result.to_dict("records") == [
        {"isin": "INE002A01018", "nse_symbol": "RELIANCE", "nse_token": "1"},
        {"isin": "INE111B01023", "nse_symbol": "63MOONS", "nse_token": "2"},
    ]


def test_load_bse_equities_udiff_excludes_fixed_income_and_gilts(tmp_path) -> None:
    csv_path = tmp_path / "BhavCopy_BSE_CM_0_0_0_20260717_F_0000.CSV"
    csv_path.write_text(
        "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,FinInstrmNm,ClsPric\n"
        "2026-07-17,2026-07-17,CM,BSE,STK,500002,INE117A01022,ABB,A,ABB INDIA LIMITED,7509.85\n"
        "2026-07-17,2026-07-17,CM,BSE,STK,538565,INE111B01023,63MOONS,T,63 MOONS TECHNOLOGIES,250.00\n"
        "2026-07-17,2026-07-17,CM,BSE,STK,975840,INE528G08394,915YESBANK28,F,YES BANK PERPETUAL BOND,100.00\n"
        "2026-07-17,2026-07-17,CM,BSE,STK,800403,IN0020200104,SGBJUN28,G,SOVEREIGN GOLD BOND,15783.00\n",
        encoding="utf-8",
    )

    result = load_bse_equities(csv_path)

    assert result.to_dict("records") == [
        {"isin": "INE117A01022", "bse_sc_code": "500002", "bse_symbol": "ABB", "bse_sc_name": "ABB INDIA LIMITED"},
        {
            "isin": "INE111B01023",
            "bse_sc_code": "538565",
            "bse_symbol": "63MOONS",
            "bse_sc_name": "63 MOONS TECHNOLOGIES",
        },
    ]


def test_load_nse_equities_legacy_includes_trade_for_trade_series(tmp_path) -> None:
    csv_path = tmp_path / "NSE-cm03MAY2021bhav.csv"
    csv_path.write_text(
        "SYMBOL,SERIES,OPEN,CLOSE,ISIN\n"
        "RELIANCE,EQ,1900.00,1994.45,INE002A01018\n"
        "63MOONS,BE,90.00,94.50,INE111B01023\n"
        "ARCOTECH,BZ,2.10,2.15,INE574I01035\n"
        "SMESTOCK,SM,50.00,51.00,INE999A01010\n"
        "SGBJUN28,GB,4700.00,4710.00,IN0020200104\n",
        encoding="utf-8",
    )

    result = load_nse_equities(csv_path)

    assert result.to_dict("records") == [
        {"isin": "INE002A01018", "nse_symbol": "RELIANCE", "nse_token": None},
        {"isin": "INE111B01023", "nse_symbol": "63MOONS", "nse_token": None},
        {"isin": "INE574I01035", "nse_symbol": "ARCOTECH", "nse_token": None},
    ]


def test_load_nse_equities_rejects_unknown_columns(tmp_path) -> None:
    csv_path = tmp_path / "nse.csv"
    csv_path.write_text("UNKNOWN,OTHER\nvalue,other\n", encoding="utf-8")

    with pytest.raises(ValueError, match="NSE bhavcopy is missing required columns"):
        load_nse_equities(csv_path)


def test_load_bse_equities_legacy_filters_sc_type_q(tmp_path) -> None:
    csv_path = tmp_path / "bse.csv"
    csv_path.write_text(
        "SC_TYPE,ISIN_CODE,SC_CODE,SC_NAME\n"
        "Q,INE002A01018,500325,RELIANCE INDUSTRIES\n"
        "F,INE000F01000,900000,EXCLUDED BOND\n",
        encoding="utf-8",
    )

    result = load_bse_equities(csv_path)

    assert result.to_dict("records") == [
        {"isin": "INE002A01018", "bse_sc_code": "500325", "bse_sc_name": "RELIANCE INDUSTRIES", "bse_symbol": None}
    ]


def test_load_bse_equities_rejects_unknown_columns(tmp_path) -> None:
    csv_path = tmp_path / "bse.csv"
    csv_path.write_text("UNKNOWN,OTHER\nvalue,other\n", encoding="utf-8")

    with pytest.raises(ValueError, match="BSE bhavcopy is missing required columns"):
        load_bse_equities(csv_path)


def test_build_consolidated_dataframe_keeps_exchange_only_isins_without_zerodha_matches() -> None:
    bse = pd.DataFrame(
        [{"isin": "BSE_ONLY", "bse_sc_code": "500001", "bse_symbol": "BSEONLY", "bse_sc_name": "BSE ONLY"}]
    )
    nse = pd.DataFrame([{"isin": "NSE_ONLY", "nse_symbol": "NSEONLY", "nse_token": "1"}])
    zerodha = pd.DataFrame(columns=["segment", "exchange_token", "tradingsymbol"])

    result = build_consolidated_dataframe(bse_equities=bse, nse_equities=nse, zerodha_instruments=zerodha)

    assert set(result.index) == {"BSE_ONLY", "NSE_ONLY"}
    assert pd.isna(result.loc["BSE_ONLY", "nse_symbol"])
    assert pd.isna(result.loc["NSE_ONLY", "bse_sc_code"])
    assert pd.isna(result.loc["BSE_ONLY", "zd_bo"])
    assert pd.isna(result.loc["NSE_ONLY", "zd_ns"])
    assert pd.isna(result.loc["BSE_ONLY", "yq_ns"])
    assert result.loc["BSE_ONLY", "yq_bo"] == "BSEONLY.BO"
    assert result.loc["NSE_ONLY", "yq_ns"] == "NSEONLY.NS"
    assert pd.isna(result.loc["NSE_ONLY", "yq_bo"])


def test_rebuild_database_dry_run_does_not_create_database(tmp_path, market_csv_builder) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    db_path = tmp_path / "fresh.db"

    result = rebuild_database(paths, db_path=db_path, backup_dir=tmp_path / "backups", dry_run=True)

    assert result.rows == 1
    assert result.dry_run is True
    assert result.backup_path is None
    assert not db_path.exists()


def test_rebuild_database_dry_run_leaves_existing_database_untouched(
    tmp_path, market_csv_builder, seed_consolidated
) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    db_path = tmp_path / "legacy.db"
    seed_consolidated(db_path)
    before = db_path.read_bytes()

    result = rebuild_database(paths, db_path=db_path, backup_dir=tmp_path / "backups", dry_run=True)

    assert result.dry_run is True
    assert db_path.read_bytes() == before
    assert not (tmp_path / "backups").exists()


def test_rebuild_database_replaces_consolidated_table_without_backup(
    tmp_path, market_csv_builder, seed_consolidated
) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    db_path = tmp_path / "stocky.db"
    backup_dir = tmp_path / "backups"
    seed_consolidated(db_path)

    result = rebuild_database(paths, db_path=db_path, backup_dir=backup_dir, backup=False)

    with sqlite3.connect(db_path) as con:
        rows = con.execute(f"SELECT isin, zd_ns, zd_bo, yq_ns, yq_bo FROM {CONSOLIDATED_TABLE}").fetchall()
    # The fixture writes legacy files: no NSE token, so zd_ns comes from the NSE symbol, and no BSE
    # trading symbol, so yq_bo stays empty.
    assert rows == [("INE002A01018", "RELIANCE", "RELIANCE", "RELIANCE.NS", None)]
    assert result.backup_path is None
    assert not backup_dir.exists()


def test_rebuild_database_backs_up_existing_table_before_overwrite(
    tmp_path, market_csv_builder, seed_consolidated
) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = rebuild_database(paths, db_path=db_path, backup_dir=tmp_path / "backups", backup=True)

    assert result.backup_path is not None
    assert result.backup_path.exists()
    with sqlite3.connect(result.backup_path) as backup_con:
        backup_rows = backup_con.execute(f"SELECT COUNT(*) FROM {CONSOLIDATED_TABLE}").fetchone()[0]
    with sqlite3.connect(db_path) as live_con:
        live_rows = live_con.execute(f"SELECT COUNT(*) FROM {CONSOLIDATED_TABLE}").fetchone()[0]
    assert backup_rows == 4
    assert live_rows == 1


def test_load_zerodha_instruments_filters_segments_and_normalizes_tokens(tmp_path, market_csv_builder) -> None:
    paths = market_csv_builder(tmp_path / "inputs")

    result = load_zerodha_instruments(paths.zerodha)

    assert result.to_dict("records") == [
        {"segment": "NSE", "exchange_token": "123", "tradingsymbol": "RELIANCE"},
        {"segment": "BSE", "exchange_token": "500325", "tradingsymbol": "RELIANCE"},
    ]


def test_load_zerodha_instruments_requires_expected_columns(tmp_path) -> None:
    path = tmp_path / "instruments.csv"
    path.write_text("segment,tradingsymbol\nNSE,RELIANCE\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing required columns: exchange_token"):
        load_zerodha_instruments(path)


def test_match_zerodha_symbols_reads_only_the_named_segment() -> None:
    zerodha = pd.DataFrame(
        [
            {"segment": "NSE", "exchange_token": "500325", "tradingsymbol": "NSE-ONLY-TOKEN-CLASH"},
            {"segment": "BSE", "exchange_token": "500325", "tradingsymbol": "RELIANCE"},
        ]
    )
    tokens = pd.Series(["500325", float("nan"), "404"])

    bse = match_zerodha_symbols(tokens, zerodha, "BSE")
    nse = match_zerodha_symbols(tokens, zerodha, "NSE")

    # The same token in both segments resolves per segment, whichever row comes last.
    assert bse[0] == "RELIANCE"
    assert nse[0] == "NSE-ONLY-TOKEN-CLASH"
    assert bse[1:].isna().all()
    assert nse[1:].isna().all()


def test_build_consolidated_dataframe_falls_back_to_the_nse_symbol_when_the_token_changed() -> None:
    nse = pd.DataFrame(
        [
            # NSE moved these to the BE series, so the bhavcopy token differs from Zerodha's.
            {"isin": "INE07S101020", "nse_symbol": "PAVNAIND", "nse_token": "16201"},
            {"isin": "INE00CE01017", "nse_symbol": "SVLL", "nse_token": "5000"},
            {"isin": "INE000000001", "nse_symbol": "NSEONLY", "nse_token": "6000"},
        ]
    )
    bse = pd.DataFrame(columns=["isin", "bse_sc_code", "bse_symbol", "bse_sc_name"])
    zerodha = pd.DataFrame(
        [
            {"segment": "NSE", "exchange_token": "16192", "tradingsymbol": "PAVNAIND"},
            {"segment": "NSE", "exchange_token": "5001", "tradingsymbol": "SVLL-BE"},
            # A different company on BSE with the NSE share's symbol must not be picked up.
            {"segment": "BSE", "exchange_token": "999999", "tradingsymbol": "NSEONLY"},
        ]
    )

    result = build_consolidated_dataframe(bse_equities=bse, nse_equities=nse, zerodha_instruments=zerodha)

    assert result.loc["INE07S101020", "zd_ns"] == "PAVNAIND"
    assert result.loc["INE00CE01017", "zd_ns"] == "SVLL-BE"
    assert pd.isna(result.loc["INE000000001", "zd_ns"])


def test_match_zerodha_symbols_by_name_reads_only_the_named_segment() -> None:
    zerodha = pd.DataFrame(
        [
            {"segment": "BSE", "exchange_token": "1", "tradingsymbol": "FOCUS"},
            {"segment": "NSE", "exchange_token": "2", "tradingsymbol": "TAKE"},
        ]
    )

    matched = match_zerodha_symbols_by_name(pd.Series(["FOCUS", "TAKE", float("nan")]), zerodha, "NSE")

    assert pd.isna(matched[0])
    assert matched[1] == "TAKE"
    assert pd.isna(matched[2])


def test_preview_sources_reports_row_counts_per_file(tmp_path, market_csv_builder) -> None:
    paths = market_csv_builder(tmp_path / "inputs")

    preview = preview_sources(paths)

    assert preview == SourcePreview(
        bse_bhavcopy=paths.bse,
        bse_rows=1,
        nse_bhavcopy=paths.nse,
        nse_rows=1,
        zerodha_instruments=paths.zerodha,
        zerodha_rows=2,
    )


def test_preview_sources_rejects_missing_files(tmp_path, market_csv_builder) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    paths.nse.unlink()

    with pytest.raises(FileNotFoundError, match="Required input files are missing"):
        preview_sources(paths)


def test_preview_sources_names_the_file_in_validation_errors(tmp_path, market_csv_builder) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    paths.bse.write_text("UNKNOWN,OTHER\nvalue,other\n", encoding="utf-8")

    with pytest.raises(ValueError, match="BSE bhavcopy is missing required columns") as error:
        preview_sources(paths)

    assert str(paths.bse) in str(error.value)


def test_rebuild_database_reports_progress_stages(tmp_path, market_csv_builder, seed_consolidated) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)
    stages: list[str] = []

    rebuild_database(paths, db_path=db_path, backup_dir=tmp_path / "backups", progress=stages.append)

    assert stages == [
        "Loading BSE bhavcopy",
        "Loading NSE bhavcopy",
        "Loading Zerodha instruments",
        "Consolidating",
        "Recording source files",
        "Comparing with the current database",
        "Backing up database",
        "Writing consolidated table",
    ]


def test_rebuild_database_progress_skips_write_stages_on_dry_run(tmp_path, market_csv_builder) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    stages: list[str] = []

    rebuild_database(paths, db_path=tmp_path / "stocky.db", dry_run=True, progress=stages.append)

    assert stages == [
        "Loading BSE bhavcopy",
        "Loading NSE bhavcopy",
        "Loading Zerodha instruments",
        "Consolidating",
        "Recording source files",
        "Comparing with the current database",
    ]


def test_rebuild_database_progress_skips_backup_stage_when_disabled(tmp_path, market_csv_builder) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    stages: list[str] = []

    rebuild_database(paths, db_path=tmp_path / "stocky.db", backup=False, progress=stages.append)

    assert stages[-2:] == ["Comparing with the current database", "Writing consolidated table"]


def test_rebuild_database_records_the_source_files_that_built_the_table(tmp_path, market_csv_builder) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    db_path = tmp_path / "stocky.db"

    result = rebuild_database(paths, db_path=db_path, backup=False)

    with sqlite3.connect(db_path) as con:
        provenance = read_latest_provenance(con)
    assert provenance is not None
    assert provenance.build_id == result.build_id
    assert provenance.built_at == result.built_at
    assert provenance.stocky_version == __version__
    assert provenance.consolidated_rows == 1
    sources = {source.role: source for source in provenance.sources}
    assert sources["bse_bhavcopy"].file_name == "BSE-EQ_ISINCODE_030521.CSV"
    assert sources["bse_bhavcopy"].trade_date == "2021-05-03"
    assert sources["nse_bhavcopy"].file_name == "NSE-cm03MAY2021bhav.csv"
    assert sources["nse_bhavcopy"].trade_date == "2021-05-03"
    assert sources["zerodha_instruments"].trade_date is None
    assert sources["zerodha_instruments"].sha256 == hashlib.sha256(paths.zerodha.read_bytes()).hexdigest()
    assert sources["zerodha_instruments"].size_bytes == paths.zerodha.stat().st_size
    assert [sources[role].rows for role in ("bse_bhavcopy", "nse_bhavcopy", "zerodha_instruments")] == [1, 1, 2]
    # The database is committed, so a local directory must not leak into it.
    assert all(str(tmp_path) not in source.file_name for source in provenance.sources)


def test_rebuild_database_keeps_earlier_builds_and_reads_the_latest(tmp_path, market_csv_builder) -> None:
    db_path = tmp_path / "stocky.db"
    rebuild_database(market_csv_builder(tmp_path / "first"), db_path=db_path, backup=False)
    second = market_csv_builder(tmp_path / "second", trade_date=date(2021, 5, 4))

    rebuild_database(second, db_path=db_path, backup=False)

    with sqlite3.connect(db_path) as con:
        assert con.execute(f"SELECT COUNT(*) FROM {PROVENANCE_BUILDS_TABLE}").fetchone()[0] == 2
        provenance = read_latest_provenance(con)
    assert provenance is not None
    assert provenance.build_id == 2
    assert {source.trade_date for source in provenance.sources} == {"2021-05-04", None}


def test_rebuild_database_writes_the_table_and_provenance_together(tmp_path, market_csv_builder, monkeypatch) -> None:
    import stocky.pipeline as pipeline

    paths = market_csv_builder(tmp_path / "inputs", isin="INE009A01021", symbol="INFY", bse_code="500209")
    db_path = tmp_path / "stocky.db"
    rebuild_database(paths, db_path=db_path, backup=False)

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(pipeline, "record_build_provenance", fail)
    with pytest.raises(sqlite3.OperationalError):
        rebuild_database(market_csv_builder(tmp_path / "other"), db_path=db_path, backup=False)

    with sqlite3.connect(db_path) as con:
        assert con.execute("SELECT isin FROM consolidated").fetchall() == [("INE009A01021",)]
        assert con.execute(f"SELECT COUNT(*) FROM {PROVENANCE_BUILDS_TABLE}").fetchone()[0] == 1


def test_rebuild_database_dry_run_writes_no_provenance_and_creates_no_file(tmp_path, market_csv_builder) -> None:
    db_path = tmp_path / "stocky.db"

    result = rebuild_database(market_csv_builder(tmp_path / "inputs"), db_path=db_path, dry_run=True)

    assert not db_path.exists()
    assert result.build_id is None
    assert [source.role for source in result.sources] == ["bse_bhavcopy", "nse_bhavcopy", "zerodha_instruments"]
    assert result.diff is not None
    assert (result.diff.current_rows, result.diff.added, result.diff.removed) == (0, 1, 0)
    assert result.diff.current_provenance is None


def test_rebuild_dry_run_reports_added_removed_and_changed_rows(
    tmp_path, market_csv_builder, seed_consolidated
) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    diff = rebuild_database(market_csv_builder(tmp_path / "inputs"), db_path=db_path, dry_run=True).diff

    assert diff is not None
    assert (diff.current_rows, diff.rebuilt_rows) == (4, 1)
    assert (diff.added, diff.removed, diff.changed, diff.unchanged) == (0, 3, 1, 0)
    # The legacy fixture files carry no BSE trading symbol, so RELIANCE loses its BSE symbol and Yahoo ticker.
    assert diff.column_changes == {"bse_symbol": 1, "yq_bo": 1}
    assert diff.sample_changed == [
        RowChange(isin="INE002A01018", changes={"bse_symbol": ("RELIANCE", None), "yq_bo": ("RELIANCE.BO", None)})
    ]
    assert [row["isin"] for row in diff.sample_removed] == ["INE009A01021", "INE144J01027", "INE999Z01019"]
    assert diff.sample_removed[0]["nse_symbol"] == "INFY"
    with sqlite3.connect(db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM consolidated").fetchone()[0] == 4


def test_diff_consolidated_treats_null_and_empty_as_equal_and_caps_samples(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)
    with sqlite3.connect(db_path) as con:
        current = pd.read_sql("SELECT * FROM consolidated", con, dtype=object).set_index("isin")
    rebuilt = current.copy()
    # 20MICRONS stores yq_bo as '' in the seed; NULL in the rebuild is the same empty value.
    rebuilt.loc["INE144J01027", "yq_bo"] = None
    for index in range(3):
        rebuilt.loc[f"INE000N0{index}000"] = ["equity", f"NEW{index}", *[None] * 7]

    diff = diff_consolidated(db_path, rebuilt, sample_size=2)

    assert (diff.added, diff.removed, diff.changed, diff.unchanged) == (3, 0, 0, 4)
    assert [row["nse_symbol"] for row in diff.sample_added] == ["NEW0", "NEW1"]


def test_diff_consolidated_compares_shared_columns_of_an_older_layout(tmp_path) -> None:
    db_path = tmp_path / "stocky.db"
    with sqlite3.connect(db_path) as con:
        con.execute("CREATE TABLE consolidated (isin TEXT, nse_symbol TEXT, zd_symbol TEXT)")
        con.execute("INSERT INTO consolidated VALUES ('INE002A01018', 'RELIANCE', 'RELIANCE')")
    rebuilt = pd.DataFrame(
        [dict.fromkeys(CONSOLIDATED_COLUMNS) | {"isin": "INE002A01018", "nse_symbol": "RELIANCE", "zd_ns": "RELIANCE"}]
    ).set_index("isin")

    diff = diff_consolidated(db_path, rebuilt)

    assert diff.compared_columns == ["nse_symbol"]
    assert (diff.changed, diff.unchanged) == (0, 1)


def test_diff_consolidated_counts_duplicate_isins_so_every_row_is_accounted_for(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    reliance = ("INE002A01018", "equity", "RELIANCE", None, None, None, None, None, "RELIANCE.NS", None)
    infy = ("INE009A01021", "equity", "INFY", None, None, None, None, None, "INFY.NS", None)
    seed_consolidated(db_path, [reliance, reliance, infy])
    rebuilt = pd.DataFrame(
        [dict(zip(CONSOLIDATED_COLUMNS, row, strict=True)) for row in (reliance, infy, infy, infy)]
    ).set_index("isin")
    rebuilt.iloc[1, rebuilt.columns.get_loc("nse_symbol")] = "INFYNEW"

    diff = diff_consolidated(db_path, rebuilt)

    assert (diff.current_rows, diff.rebuilt_rows) == (3, 4)
    assert (diff.duplicate_current, diff.duplicate_rebuilt) == (1, 2)
    assert diff.added + diff.changed + diff.unchanged + diff.duplicate_rebuilt == diff.rebuilt_rows
    assert diff.current_rows - diff.removed - diff.duplicate_current == diff.changed + diff.unchanged
    assert (diff.changed, diff.unchanged) == (1, 1)
    assert diff.sample_duplicates == ["INE002A01018", "INE009A01021"]
