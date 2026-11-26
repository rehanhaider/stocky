import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from datetime import date
from pathlib import Path
from zipfile import ZipFile

import pytest
from typer.testing import CliRunner

from stocky.brokers import import_broker
from stocky.bse_debt import import_bse_debt
from stocky.database import CONSOLIDATED_TABLE
from stocky.indices import import_indices
from stocky.mutual_funds import import_mutual_funds
from stocky.sources import BhavcopyPaths, bse_filename_for_date, nse_filename_for_date

CONSOLIDATED_COLUMNS = (
    "isin",
    "ins_type",
    "nse_symbol",
    "bse_symbol",
    "bse_sc_code",
    "bse_sc_name",
    "zd_ns",
    "zd_bo",
    "yq_ns",
    "yq_bo",
)

CONSOLIDATED_ROWS = (
    (
        "INE002A01018",
        "equity",
        "RELIANCE",
        "RELIANCE",
        "500325",
        "RELIANCE INDUSTRIES",
        "RELIANCE",
        "RELIANCE",
        "RELIANCE.NS",
        "RELIANCE.BO",
    ),
    ("INE009A01021", "equity", "INFY", "INFY", "500209", "INFOSYS LTD", "INFY", "INFY", "INFY.NS", "INFY.BO"),
    ("INE144J01027", "equity", None, "20MICRONS", "533022", "20 MICRONS LTD", None, "20MICRONS", None, ""),
    ("INE999Z01019", "equity", None, None, None, "INFY ETF", "INFYBEES", None, None, None),
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def seed_consolidated() -> Callable[[Path, Sequence[tuple[object, ...]] | None], None]:
    def seed(db_path: Path, rows: Sequence[tuple[object, ...]] | None = None) -> None:
        selected_rows = CONSOLIDATED_ROWS if rows is None else rows
        column_definitions = ", ".join(f"{column} TEXT" for column in CONSOLIDATED_COLUMNS)
        placeholders = ", ".join("?" for _ in CONSOLIDATED_COLUMNS)
        with closing(sqlite3.connect(db_path)) as con, con:
            con.execute(f"CREATE TABLE {CONSOLIDATED_TABLE} ({column_definitions})")
            con.executemany(
                f"INSERT INTO {CONSOLIDATED_TABLE} VALUES ({placeholders})",
                selected_rows,
            )

    return seed


@pytest.fixture
def old_backups() -> Callable[[Path, str, int], list[Path]]:
    """Write ``count`` backups named as ``backup_database`` names them, oldest first, dated 2025-01-0N."""

    def seed(backup_dir: Path, db_name: str, count: int) -> list[Path]:
        backup_dir.mkdir(parents=True, exist_ok=True)
        paths = [backup_dir / f"202501{day:02d}-120000-{db_name}" for day in range(1, count + 1)]
        for path in paths:
            path.write_bytes(b"old backup")
        return paths

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


@pytest.fixture
def import_mapping_tables() -> Callable[[Path, Path], None]:
    """Fill every mapping table through its importer, so each table has rows and an import record."""

    def run(directory: Path, db_path: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        mf = directory / "mf_instruments.csv"
        mf.write_text(
            "tradingsymbol,amc,name,scheme_type,plan,dividend_type\n"
            "INF179K01KG8,HDFCMutualFund_MF,HDFC Liquid Fund,Debt,regular,growth\n",
            encoding="utf-8",
        )
        amfi = directory / "NAVAll.txt"
        amfi.write_text("100001;INF179K01KG8;-;HDFC Liquid Fund;Regular;Growth;100;18-Oct-2026\n", encoding="utf-8")
        import_mutual_funds(mf, amfi, db_path)

        debt = directory / "DEBTBHAVCOPY16102026.zip"
        with ZipFile(debt, "w") as archive:
            archive.writestr(
                "fgroup16102026.csv",
                "Security_cd,sc_name,ISIN No.,FACE VALUE,COUP0N (%),Maturity Date\n978034,0MCIBPL28,INE0K0Y08060,,,\n",
            )
            archive.writestr(
                "icdm16102026.csv",
                "Security Code,Issuer Name,Coupon (%),Maturity Date,ISIN No.,Face Value\n"
                "INE0K0Y08060,Industrial Finance,7.4000,18/06/2031,INE0K0Y08060,100000.00\n",
            )
            archive.writestr(
                "wdm16102026.csv",
                "Scrip Code,Security Code,Security Description,Coupon Rate (%),MaturityDate\n"
                "400536,667GOI50,6.67 GOI BOND 2050,6.67,17/12/2050\n",
            )
        import_bse_debt(debt, db_path)

        instruments = directory / "instruments.csv"
        instruments.write_text(
            "instrument_token,exchange_token,tradingsymbol,name,segment,exchange\n"
            "256265,1001,NIFTY 50,NIFTY 50,INDICES,NSE\n",
            encoding="utf-8",
        )
        import_indices(instruments, db_path)

        groww = directory / "instrument.csv"
        groww.write_text(
            "exchange,exchange_token,isin,trading_symbol,groww_symbol,name,segment,instrument_type\n"
            "NSE,02885,INE002A01018,RELIANCE,NSE-RELIANCE,Reliance Industries,CASH,EQ\n",
            encoding="utf-8",
        )
        import_broker("groww", groww, db_path)

    return run
