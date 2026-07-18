from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd

from stocky.pipeline import build_consolidated_dataframe, load_bse_equities, load_nse_equities


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
