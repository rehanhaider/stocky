from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from stocky.config import DEFAULT_BACKUP_DIR, DEFAULT_DB_PATH
from stocky.database import (
    CONSOLIDATED_TABLE,
    backup_database,
    connect,
    initialize_database,
    read_available_yahoo_symbols,
)
from stocky.sources import BhavcopyPaths, require_existing_files

NSE_LEGACY_COLUMNS = {"SERIES", "ISIN", "SYMBOL"}
NSE_UDIFF_COLUMNS = {"SctySrs", "ISIN", "TckrSymb"}

# EQ = rolling-settlement equities; BE/BZ = trade-for-trade equities.
# SME platform (SM/ST) and debt/bond/ETF series stay excluded.
NSE_EQUITY_SERIES = {"EQ", "BE", "BZ"}


@dataclass(frozen=True)
class RebuildResult:
    rows: int
    db_path: Path
    backup_path: Path | None
    dry_run: bool
    bse_bhavcopy: Path
    nse_bhavcopy: Path
    zerodha_instruments: Path


def _strip_dataframe_strings(dataframe: pd.DataFrame) -> pd.DataFrame:
    return dataframe.replace({r"^\s*|\s*$": ""}, regex=True)


def _require_columns(dataframe: pd.DataFrame, required_columns: set[str], source_name: str) -> None:
    missing = sorted(required_columns.difference(dataframe.columns))
    if missing:
        raise ValueError(f"{source_name} is missing required columns: {', '.join(missing)}")


def load_bse_equities(path: Path) -> pd.DataFrame:
    bse_bhavcopy = _strip_dataframe_strings(pd.read_csv(path))
    _require_columns(bse_bhavcopy, {"SC_TYPE", "ISIN_CODE", "SC_CODE", "SC_NAME"}, "BSE bhavcopy")

    equities = bse_bhavcopy[bse_bhavcopy["SC_TYPE"] == "Q"][["ISIN_CODE", "SC_CODE", "SC_NAME"]].copy()
    equities.rename(
        columns={"ISIN_CODE": "isin", "SC_CODE": "bse_sc_code", "SC_NAME": "bse_sc_name"},
        inplace=True,
    )
    equities["bse_sc_code"] = equities["bse_sc_code"].astype(str)
    return equities


def load_nse_equities(path: Path) -> pd.DataFrame:
    nse_bhavcopy = _strip_dataframe_strings(pd.read_csv(path))

    if NSE_LEGACY_COLUMNS.issubset(nse_bhavcopy.columns):
        equities = nse_bhavcopy[nse_bhavcopy["SERIES"].isin(NSE_EQUITY_SERIES)][["ISIN", "SYMBOL"]].copy()
        equities.rename(columns={"ISIN": "isin", "SYMBOL": "nse_symbol"}, inplace=True)
        return equities

    if NSE_UDIFF_COLUMNS.issubset(nse_bhavcopy.columns):
        equities = nse_bhavcopy[nse_bhavcopy["SctySrs"].isin(NSE_EQUITY_SERIES)][["ISIN", "TckrSymb"]].copy()
        equities.rename(columns={"ISIN": "isin", "TckrSymb": "nse_symbol"}, inplace=True)
        return equities

    raise ValueError(
        "NSE bhavcopy is missing required columns for supported formats: "
        f"legacy {sorted(NSE_LEGACY_COLUMNS)} or UDiFF {sorted(NSE_UDIFF_COLUMNS)}"
    )


def load_zerodha_instruments(path: Path) -> pd.DataFrame:
    instruments = _strip_dataframe_strings(pd.read_csv(path))
    _require_columns(instruments, {"segment", "exchange_token", "tradingsymbol"}, "Zerodha instruments")

    instruments = instruments.query("segment == 'BSE' or segment == 'NSE'")[["exchange_token", "tradingsymbol"]].copy()
    instruments["exchange_token"] = instruments["exchange_token"].astype(str)
    instruments["tradingsymbol"] = instruments["tradingsymbol"].astype(str)
    return instruments


def match_zerodha_symbol(row: pd.Series, tradingsymbols: set[str], token_to_symbol: dict[str, str]) -> str | None:
    nse_symbol = row.get("nse_symbol")
    bse_code = row.get("bse_sc_code")

    if pd.notna(nse_symbol) and str(nse_symbol) in tradingsymbols:
        return str(nse_symbol)

    if pd.notna(bse_code):
        return token_to_symbol.get(str(bse_code))

    return None


def match_yahoo_symbol(row: pd.Series, available_yahoo_symbols: set[str]) -> str | None:
    for candidate in (row.get("nse_symbol"), row.get("zd_symbol")):
        if pd.isna(candidate):
            continue

        candidate = str(candidate)
        if f"{candidate}.NS" in available_yahoo_symbols or f"{candidate}.BO" in available_yahoo_symbols:
            return candidate

    return None


def build_consolidated_dataframe(
    *,
    bse_equities: pd.DataFrame,
    nse_equities: pd.DataFrame,
    zerodha_instruments: pd.DataFrame,
    available_yahoo_symbols: set[str],
) -> pd.DataFrame:
    equities = pd.merge(nse_equities, bse_equities, on="isin", how="outer")
    equities.set_index("isin", inplace=True)
    equities["ins_type"] = "equity"

    zerodha_symbols = zerodha_instruments.dropna(subset=["exchange_token", "tradingsymbol"])
    tradingsymbols = set(zerodha_symbols["tradingsymbol"].astype(str))
    token_to_symbol = dict(
        zip(
            zerodha_symbols["exchange_token"].astype(str),
            zerodha_symbols["tradingsymbol"].astype(str),
            strict=False,
        )
    )
    equities["zd_symbol"] = equities.apply(match_zerodha_symbol, axis=1, args=(tradingsymbols, token_to_symbol))
    equities["yq_symbol"] = equities.apply(match_yahoo_symbol, axis=1, args=(available_yahoo_symbols,))

    return equities[["ins_type", "zd_symbol", "yq_symbol", "nse_symbol", "bse_sc_code", "bse_sc_name"]]


def rebuild_database(
    paths: BhavcopyPaths,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    backup: bool = True,
    dry_run: bool = False,
) -> RebuildResult:
    require_existing_files(paths)
    initialize_database(db_path)

    stocky = build_consolidated_dataframe(
        bse_equities=load_bse_equities(paths.bse),
        nse_equities=load_nse_equities(paths.nse),
        zerodha_instruments=load_zerodha_instruments(paths.zerodha),
        available_yahoo_symbols=read_available_yahoo_symbols(db_path),
    )

    backup_path = None
    if not dry_run:
        if backup:
            backup_path = backup_database(db_path, backup_dir=backup_dir)

        initialize_database(db_path)
        with connect(db_path) as con:
            stocky.to_sql(CONSOLIDATED_TABLE, con, if_exists="replace", index=True, index_label="isin")

    return RebuildResult(
        rows=len(stocky),
        db_path=db_path,
        backup_path=backup_path,
        dry_run=dry_run,
        bse_bhavcopy=paths.bse,
        nse_bhavcopy=paths.nse,
        zerodha_instruments=paths.zerodha,
    )
