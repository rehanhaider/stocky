from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from stocky.config import DEFAULT_BACKUP_DIR, DEFAULT_DB_PATH
from stocky.database import (
    CONSOLIDATED_COLUMNS,
    CONSOLIDATED_TABLE,
    backup_database,
    connect,
    initialize_database,
)
from stocky.sources import BhavcopyPaths, require_existing_files

NSE_LEGACY_COLUMNS = {"SERIES", "ISIN", "SYMBOL"}
NSE_UDIFF_COLUMNS = {"SctySrs", "ISIN", "TckrSymb", "FinInstrmId"}

# EQ = rolling-settlement equities; BE/BZ = trade-for-trade equities.
# SME platform (SM/ST) and debt/bond/ETF series stay excluded.
NSE_EQUITY_SERIES = {"EQ", "BE", "BZ"}

BSE_LEGACY_COLUMNS = {"SC_TYPE", "ISIN_CODE", "SC_CODE", "SC_NAME"}
BSE_UDIFF_COLUMNS = {"SctySrs", "ISIN", "FinInstrmId", "TckrSymb", "FinInstrmNm"}

# The legacy files' SC_TYPE "Q" filter kept every group except fixed income (F)
# and gilts/SGBs (G); the UDiFF file has no SC_TYPE, so exclude those series instead.
BSE_NON_EQUITY_UDIFF_SERIES = {"F", "G"}


@dataclass(frozen=True)
class SourcePreview:
    bse_bhavcopy: Path
    bse_rows: int
    nse_bhavcopy: Path
    nse_rows: int
    zerodha_instruments: Path
    zerodha_rows: int


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

    if BSE_LEGACY_COLUMNS.issubset(bse_bhavcopy.columns):
        equities = bse_bhavcopy[bse_bhavcopy["SC_TYPE"] == "Q"][["ISIN_CODE", "SC_CODE", "SC_NAME"]].copy()
        equities.rename(
            columns={"ISIN_CODE": "isin", "SC_CODE": "bse_sc_code", "SC_NAME": "bse_sc_name"},
            inplace=True,
        )
        equities["bse_sc_code"] = equities["bse_sc_code"].astype(str)
        # Legacy files carry no BSE trading symbol.
        equities["bse_symbol"] = None
        return equities

    if BSE_UDIFF_COLUMNS.issubset(bse_bhavcopy.columns):
        equities = bse_bhavcopy[~bse_bhavcopy["SctySrs"].isin(BSE_NON_EQUITY_UDIFF_SERIES)][
            ["ISIN", "FinInstrmId", "TckrSymb", "FinInstrmNm"]
        ].copy()
        equities.rename(
            columns={
                "ISIN": "isin",
                "FinInstrmId": "bse_sc_code",
                "TckrSymb": "bse_symbol",
                "FinInstrmNm": "bse_sc_name",
            },
            inplace=True,
        )
        equities["bse_sc_code"] = equities["bse_sc_code"].astype(str)
        return equities

    raise ValueError(
        "BSE bhavcopy is missing required columns for supported formats: "
        f"legacy {sorted(BSE_LEGACY_COLUMNS)} or UDiFF {sorted(BSE_UDIFF_COLUMNS)} ({path})"
    )


def load_nse_equities(path: Path) -> pd.DataFrame:
    nse_bhavcopy = _strip_dataframe_strings(pd.read_csv(path))

    if NSE_LEGACY_COLUMNS.issubset(nse_bhavcopy.columns):
        equities = nse_bhavcopy[nse_bhavcopy["SERIES"].isin(NSE_EQUITY_SERIES)][["ISIN", "SYMBOL"]].copy()
        equities.rename(columns={"ISIN": "isin", "SYMBOL": "nse_symbol"}, inplace=True)
        # Legacy files carry no instrument token, so Zerodha's NSE symbol cannot be matched.
        equities["nse_token"] = None
        return equities

    if NSE_UDIFF_COLUMNS.issubset(nse_bhavcopy.columns):
        equities = nse_bhavcopy[nse_bhavcopy["SctySrs"].isin(NSE_EQUITY_SERIES)][
            ["ISIN", "TckrSymb", "FinInstrmId"]
        ].copy()
        equities.rename(columns={"ISIN": "isin", "TckrSymb": "nse_symbol", "FinInstrmId": "nse_token"}, inplace=True)
        equities["nse_token"] = equities["nse_token"].astype(str)
        return equities

    raise ValueError(
        "NSE bhavcopy is missing required columns for supported formats: "
        f"legacy {sorted(NSE_LEGACY_COLUMNS)} or UDiFF {sorted(NSE_UDIFF_COLUMNS)} ({path})"
    )


def load_zerodha_instruments(path: Path) -> pd.DataFrame:
    instruments = _strip_dataframe_strings(pd.read_csv(path))
    _require_columns(instruments, {"segment", "exchange_token", "tradingsymbol"}, f"Zerodha instruments ({path})")

    instruments = instruments.query("segment == 'BSE' or segment == 'NSE'")[
        ["segment", "exchange_token", "tradingsymbol"]
    ].copy()
    instruments["exchange_token"] = instruments["exchange_token"].astype(str)
    instruments["tradingsymbol"] = instruments["tradingsymbol"].astype(str)
    return instruments


def match_zerodha_symbols(tokens: pd.Series, zerodha_instruments: pd.DataFrame, segment: str) -> pd.Series:
    """Map exchange tokens to Zerodha trading symbols from one segment only.

    Zerodha's exchange_token is the exchange's own instrument number: the NSE token for the NSE
    segment and the BSE scrip code for the BSE segment. Matching by token rather than by symbol keeps
    an NSE share from picking up a different company's BSE symbol, and yields Zerodha's own spelling,
    such as AAREYDRUGS-BE for NSE trade-for-trade shares.
    """
    segment_rows = zerodha_instruments[zerodha_instruments["segment"] == segment]
    token_to_symbol = dict(zip(segment_rows["exchange_token"], segment_rows["tradingsymbol"], strict=False))
    return tokens.map(lambda token: token_to_symbol.get(str(token)) if pd.notna(token) else None)


def yahoo_tickers(symbols: pd.Series, suffix: str) -> pd.Series:
    """Append Yahoo's exchange suffix to an exchange's own symbol, leaving unlisted rows empty."""
    return symbols.map(lambda symbol: f"{symbol}{suffix}" if pd.notna(symbol) and str(symbol) else None)


def build_consolidated_dataframe(
    *,
    bse_equities: pd.DataFrame,
    nse_equities: pd.DataFrame,
    zerodha_instruments: pd.DataFrame,
) -> pd.DataFrame:
    equities = pd.merge(nse_equities, bse_equities, on="isin", how="outer")
    equities.set_index("isin", inplace=True)
    equities["ins_type"] = "equity"
    equities["zd_ns"] = match_zerodha_symbols(equities["nse_token"], zerodha_instruments, "NSE")
    equities["zd_bo"] = match_zerodha_symbols(equities["bse_sc_code"], zerodha_instruments, "BSE")
    equities["yq_ns"] = yahoo_tickers(equities["nse_symbol"], ".NS")
    equities["yq_bo"] = yahoo_tickers(equities["bse_symbol"], ".BO")

    return equities[[column for column in CONSOLIDATED_COLUMNS if column != "isin"]]


def preview_sources(paths: BhavcopyPaths) -> SourcePreview:
    """Load the three source files and report how many usable rows each holds."""
    require_existing_files(paths)

    return SourcePreview(
        bse_bhavcopy=paths.bse,
        bse_rows=len(load_bse_equities(paths.bse)),
        nse_bhavcopy=paths.nse,
        nse_rows=len(load_nse_equities(paths.nse)),
        zerodha_instruments=paths.zerodha,
        zerodha_rows=len(load_zerodha_instruments(paths.zerodha)),
    )


def rebuild_database(
    paths: BhavcopyPaths,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    backup: bool = True,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
) -> RebuildResult:
    def report(stage: str) -> None:
        if progress is not None:
            progress(stage)

    require_existing_files(paths)

    report("Loading BSE bhavcopy")
    bse_equities = load_bse_equities(paths.bse)
    report("Loading NSE bhavcopy")
    nse_equities = load_nse_equities(paths.nse)
    report("Loading Zerodha instruments")
    zerodha_instruments = load_zerodha_instruments(paths.zerodha)

    report("Consolidating")
    stocky = build_consolidated_dataframe(
        bse_equities=bse_equities,
        nse_equities=nse_equities,
        zerodha_instruments=zerodha_instruments,
    )

    backup_path = None
    if dry_run:
        return RebuildResult(
            rows=len(stocky),
            db_path=db_path,
            backup_path=backup_path,
            dry_run=dry_run,
            bse_bhavcopy=paths.bse,
            nse_bhavcopy=paths.nse,
            zerodha_instruments=paths.zerodha,
        )

    initialize_database(db_path)
    if backup:
        report("Backing up database")
        backup_path = backup_database(db_path, backup_dir=backup_dir)

    report("Writing consolidated table")
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
