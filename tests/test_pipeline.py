import pandas as pd

from stocky.pipeline import build_consolidated_dataframe


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
