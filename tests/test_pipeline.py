from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd

from stocky.pipeline import build_consolidated_dataframe, load_nse_equities


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
        "2026-05-22,2026-05-22,CM,NSE,STK,2,IN0020200104,SGBJUN28,GB,15783.00\n"
    )
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as zip_file:
        zip_file.writestr(csv_name, csv_content)

    result = load_nse_equities(zip_path)

    assert result.to_dict("records") == [{"isin": "INE002A01018", "nse_symbol": "RELIANCE"}]
