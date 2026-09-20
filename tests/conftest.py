import sqlite3
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stocky.database import CONSOLIDATED_TABLE
from stocky.sources import BhavcopyPaths, bse_filename_for_date, nse_filename_for_date

CONSOLIDATED_COLUMNS = (
    "isin",
    "ins_type",
    "zd_symbol",
    "yq_symbol",
    "nse_symbol",
    "bse_sc_code",
    "bse_sc_name",
)

CONSOLIDATED_ROWS = (
    ("INE002A01018", "equity", "RELIANCE", "RELIANCE", "RELIANCE", "500325", "RELIANCE INDUSTRIES"),
    ("INE009A01021", "equity", "INFY", "INFY", "INFY", "500209", "INFOSYS LTD"),
    ("INE144J01027", "equity", "20MICRONS", "", None, "533022", "20 MICRONS LTD"),
    ("INE999Z01019", "equity", "INFYBEES", None, None, None, "INFY ETF"),
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def seed_consolidated() -> Callable[[Path, Sequence[tuple[object, ...]] | None], None]:
    def seed(db_path: Path, rows: Sequence[tuple[object, ...]] | None = None) -> None:
        selected_rows = CONSOLIDATED_ROWS if rows is None else rows
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
                selected_rows,
            )

    return seed


@pytest.fixture
def market_csv_builder() -> Callable[..., BhavcopyPaths]:
    def build(
        directory: Path,
        *,
        trade_date: date = date(2021, 5, 3),
        isin: str = "INE002A01018",
        symbol: str = "RELIANCE",
        bse_code: str = "500325",
    ) -> BhavcopyPaths:
        directory.mkdir(parents=True, exist_ok=True)
        bse_path = directory / bse_filename_for_date(trade_date)
        nse_path = directory / nse_filename_for_date(trade_date)
        zerodha_path = directory / "instruments.csv"

        bse_path.write_text(
            "SC_TYPE,ISIN_CODE,SC_CODE,SC_NAME\n"
            f"Q,{isin},{bse_code},{symbol} INDUSTRIES\n"
            "F,INE000F01000,900000,EXCLUDED BOND\n",
            encoding="utf-8",
        )
        nse_path.write_text(
            f"SYMBOL,SERIES,ISIN\n{symbol},EQ,{isin}\nEXCLUDED,GB,IN0000000000\n",
            encoding="utf-8",
        )
        zerodha_path.write_text(
            f"segment,exchange_token,tradingsymbol\nNSE,123,{symbol}\nBSE,{bse_code},{symbol}\nNFO,999,EXCLUDED\n",
            encoding="utf-8",
        )
        return BhavcopyPaths(bse=bse_path, nse=nse_path, zerodha=zerodha_path)

    return build
