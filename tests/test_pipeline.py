import sqlite3
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd
import pytest

from stocky.database import CONSOLIDATED_TABLE
from stocky.pipeline import (
    SourcePreview,
    build_consolidated_dataframe,
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
    ]


def test_rebuild_database_progress_skips_backup_stage_when_disabled(tmp_path, market_csv_builder) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    stages: list[str] = []

    rebuild_database(paths, db_path=tmp_path / "stocky.db", backup=False, progress=stages.append)

    assert stages[-2:] == ["Consolidating", "Writing consolidated table"]
