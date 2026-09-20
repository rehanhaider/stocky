import sqlite3
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd
import pytest

from stocky.database import CONSOLIDATED_TABLE
from stocky.pipeline import (
    build_consolidated_dataframe,
    load_bse_equities,
    load_nse_equities,
    load_zerodha_instruments,
    match_yahoo_symbol,
    match_zerodha_symbol,
    rebuild_database,
)


def test_build_consolidated_dataframe_matches_symbols() -> None:
    bse = pd.DataFrame(
        [
            {
                "isin": "INE002A01018",
                "bse_sc_code": "500325",
                "bse_sc_name": "RELIANCE",
            }
        ]
    )
    nse = pd.DataFrame([{"isin": "INE002A01018", "nse_symbol": "RELIANCE"}])
    zerodha = pd.DataFrame([{"exchange_token": "500325", "tradingsymbol": "RELIANCE"}])

    result = build_consolidated_dataframe(
        bse_equities=bse,
        nse_equities=nse,
        zerodha_instruments=zerodha,
        available_yahoo_symbols={"RELIANCE.NS"},
    )

    row = result.loc["INE002A01018"]
    assert row["zd_symbol"] == "RELIANCE"
    assert row["yq_symbol"] == "RELIANCE"


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
        {"isin": "INE002A01018", "nse_symbol": "RELIANCE"},
        {"isin": "INE111B01023", "nse_symbol": "63MOONS"},
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
        {"isin": "INE117A01022", "bse_sc_code": "500002", "bse_sc_name": "ABB INDIA LIMITED"},
        {"isin": "INE111B01023", "bse_sc_code": "538565", "bse_sc_name": "63 MOONS TECHNOLOGIES"},
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
        {"isin": "INE002A01018", "nse_symbol": "RELIANCE"},
        {"isin": "INE111B01023", "nse_symbol": "63MOONS"},
        {"isin": "INE574I01035", "nse_symbol": "ARCOTECH"},
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
        {"isin": "INE002A01018", "bse_sc_code": "500325", "bse_sc_name": "RELIANCE INDUSTRIES"}
    ]


def test_load_bse_equities_rejects_unknown_columns(tmp_path) -> None:
    csv_path = tmp_path / "bse.csv"
    csv_path.write_text("UNKNOWN,OTHER\nvalue,other\n", encoding="utf-8")

    with pytest.raises(ValueError, match="BSE bhavcopy is missing required columns"):
        load_bse_equities(csv_path)


def test_build_consolidated_dataframe_keeps_exchange_only_isins_and_nan_fallbacks() -> None:
    bse = pd.DataFrame([{"isin": "BSE_ONLY", "bse_sc_code": "500001", "bse_sc_name": "BSE ONLY"}])
    nse = pd.DataFrame([{"isin": "NSE_ONLY", "nse_symbol": "NSEONLY"}])
    zerodha = pd.DataFrame(columns=["exchange_token", "tradingsymbol"])

    result = build_consolidated_dataframe(
        bse_equities=bse,
        nse_equities=nse,
        zerodha_instruments=zerodha,
        available_yahoo_symbols=set(),
    )

    assert set(result.index) == {"BSE_ONLY", "NSE_ONLY"}
    assert pd.isna(result.loc["BSE_ONLY", "nse_symbol"])
    assert pd.isna(result.loc["NSE_ONLY", "bse_sc_code"])
    assert result.loc["BSE_ONLY", "zd_symbol"] is None
    assert result.loc["NSE_ONLY", "yq_symbol"] is None


def test_rebuild_database_dry_run_does_not_create_database(tmp_path, market_csv_builder) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    db_path = tmp_path / "fresh.db"

    result = rebuild_database(paths, db_path=db_path, backup_dir=tmp_path / "backups", dry_run=True)

    assert result.rows == 1
    assert result.dry_run is True
    assert result.backup_path is None
    assert not db_path.exists()


def test_rebuild_database_replaces_consolidated_table_without_backup(
    tmp_path, market_csv_builder, seed_consolidated
) -> None:
    paths = market_csv_builder(tmp_path / "inputs")
    db_path = tmp_path / "stocky.db"
    backup_dir = tmp_path / "backups"
    seed_consolidated(db_path)

    result = rebuild_database(paths, db_path=db_path, backup_dir=backup_dir, backup=False)

    with sqlite3.connect(db_path) as con:
        rows = con.execute(f"SELECT isin, zd_symbol FROM {CONSOLIDATED_TABLE}").fetchall()
    assert rows == [("INE002A01018", "RELIANCE")]
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
        {"exchange_token": "123", "tradingsymbol": "RELIANCE"},
        {"exchange_token": "500325", "tradingsymbol": "RELIANCE"},
    ]


def test_load_zerodha_instruments_requires_expected_columns(tmp_path) -> None:
    path = tmp_path / "instruments.csv"
    path.write_text("segment,tradingsymbol\nNSE,RELIANCE\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing required columns: exchange_token"):
        load_zerodha_instruments(path)


def test_match_zerodha_symbol_uses_bse_token_then_returns_none() -> None:
    bse_only = pd.Series({"nse_symbol": float("nan"), "bse_sc_code": "500325"})
    unmatched = pd.Series({"nse_symbol": "UNKNOWN", "bse_sc_code": float("nan")})

    assert match_zerodha_symbol(bse_only, set(), {"500325": "RELIANCE"}) == "RELIANCE"
    assert match_zerodha_symbol(unmatched, {"RELIANCE"}, {}) is None


def test_match_yahoo_symbol_uses_bo_after_nan_and_returns_none() -> None:
    bse_only = pd.Series({"nse_symbol": float("nan"), "zd_symbol": "RELIANCE"})
    unmatched = pd.Series({"nse_symbol": float("nan"), "zd_symbol": "UNKNOWN"})

    assert match_yahoo_symbol(bse_only, {"RELIANCE.BO"}) == "RELIANCE"
    assert match_yahoo_symbol(unmatched, {"RELIANCE.BO"}) is None
