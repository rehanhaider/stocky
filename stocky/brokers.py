from __future__ import annotations

import gzip
import json
import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pandas as pd

from stocky.config import DEFAULT_DB_PATH
from stocky.database import _escape_like, connect, table_exists
from stocky.pipeline import NSE_EQUITY_SERIES

BROKER_TABLE = "broker_symbols"
BROKER_COLUMNS = ("broker", "exchange", "exchange_token", "isin", "symbol", "broker_key", "name")
GROWW_SOURCE_COLUMNS = {
    "exchange": "exchange",
    "exchange_token": "exchange_token",
    "isin": "isin",
    "trading_symbol": "symbol",
    "groww_symbol": "broker_key",
    "name": "name",
}
UPSTOX_SOURCE_COLUMNS = {
    "exchange_token": "exchange_token",
    "isin": "isin",
    "trading_symbol": "symbol",
    "instrument_key": "broker_key",
    "name": "name",
}
ALICEBLUE_SOURCE_COLUMNS = {
    "Exch": "exchange",
    "Token": "exchange_token",
    "Trading Symbol": "symbol",
    "Instrument Name": "name",
}
EXACT_COLUMNS = ("isin", "symbol", "exchange_token", "broker_key")
FRAGMENT_COLUMNS = ("symbol", "name")


class Broker(str, Enum):
    groww = "groww"
    upstox = "upstox"
    aliceblue = "aliceblue"


@dataclass(frozen=True)
class BrokerImportResult:
    broker: str
    exchange: str
    rows: int
    with_isin: int
    in_consolidated: int


@dataclass(frozen=True)
class BrokerMatch:
    broker: str
    exchange: str
    exchange_token: str
    isin: str | None
    symbol: str | None
    broker_key: str | None
    name: str | None


@dataclass(frozen=True)
class BrokerSearchResult:
    matches: list[BrokerMatch]
    total: int


def _require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {', '.join(missing)}")


def _read_csv(path: Path, required: set[str], label: str) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    _require_columns(frame, required, f"{label} ({path})")
    return frame.apply(lambda column: column.str.strip())


def _finish(frame: pd.DataFrame, broker: str) -> pd.DataFrame:
    if frame.empty:
        raise ValueError(f"{broker} instruments has no cash, non-index rows.")
    frame = frame.copy()
    frame["broker"] = broker
    if frame["exchange_token"].eq("").any():
        raise ValueError(f"{broker} instruments has rows without an exchange_token.")
    duplicated = frame.duplicated(["exchange", "exchange_token"])
    if duplicated.any():
        tokens = frame.loc[duplicated, "exchange_token"].tolist()
        raise ValueError(f"{broker} instruments lists exchange tokens more than once: {', '.join(tokens[:10])}")
    return (
        frame[list(BROKER_COLUMNS)]
        .replace({"": None})
        .sort_values(["exchange", "exchange_token"])
        .reset_index(drop=True)
    )


def load_groww(path: Path) -> pd.DataFrame:
    """Keep NSE/BSE CASH EQ rows, excluding CASH IDX rows and every derivative segment."""
    frame = _read_csv(path, set(GROWW_SOURCE_COLUMNS) | {"segment", "instrument_type"}, "Groww instruments")
    keep = frame["exchange"].isin(["NSE", "BSE"]) & frame["segment"].eq("CASH") & frame["instrument_type"].eq("EQ")
    return _finish(frame.loc[keep, list(GROWW_SOURCE_COLUMNS)].rename(columns=GROWW_SOURCE_COLUMNS), "groww")


def load_upstox(path: Path) -> pd.DataFrame:
    """Keep NSE_EQ/BSE_EQ cash rows, excluding indices, derivatives and W1 warrants."""
    if not path.is_file():
        raise FileNotFoundError(f"Upstox instruments not found: {path}")
    with gzip.open(path, "rt", encoding="utf-8") as source:
        records = json.load(source)
    required = set(UPSTOX_SOURCE_COLUMNS) | {"segment", "instrument_type"}
    frame = pd.DataFrame(records, dtype=str).fillna("")
    _require_columns(frame, required, f"Upstox instruments ({path})")
    frame = frame.apply(lambda column: column.str.strip())
    keep = frame["segment"].isin(["NSE_EQ", "BSE_EQ"])
    keep &= ~frame["instrument_type"].isin(["INDEX", "IDX", "FUT", "CE", "PE", "W1"])
    # JSON objects have different fields. Check each kept object, not just their combined columns.
    for record in (records[index] for index in frame.index[keep]):
        missing = sorted(required.difference(record))
        if missing:
            raise ValueError(f"Upstox instruments ({path}) is missing required columns: {', '.join(missing)}")
    cash = frame.loc[keep].copy()
    cash["exchange"] = cash["segment"].map({"NSE_EQ": "NSE", "BSE_EQ": "BSE"})
    return _finish(cash.rename(columns=UPSTOX_SOURCE_COLUMNS), "upstox")


def _load_aliceblue(nse_path: Path, bse_path: Path, con: sqlite3.Connection) -> pd.DataFrame:
    if not table_exists(con, "consolidated"):
        raise RuntimeError("Database table 'consolidated' does not exist. Run 'stocky rebuild' first.")
    mappings = con.execute("SELECT isin, nse_symbol, bse_sc_code FROM consolidated").fetchall()
    nse_isins = {symbol: isin for isin, symbol, _ in mappings if symbol}
    bse_isins = {code: isin for isin, _, code in mappings if code}
    frames = []
    for path, exchange, types in ((nse_path, "NSE", ["0", "2", "4"]), (bse_path, "BSE", ["E"])):
        required = set(ALICEBLUE_SOURCE_COLUMNS) | {"Exchange Segment", "Instrument Type", "Symbol"}
        if exchange == "NSE":
            required.add("Group Name")
        frame = _read_csv(path, required, "Alice Blue instruments")
        keep = frame["Exch"].eq(exchange) & frame["Exchange Segment"].eq(f"{exchange.lower()}_cm")
        keep &= frame["Instrument Type"].isin(types)
        cash = frame.loc[keep].copy()
        if exchange == "NSE":
            cash["isin"] = cash["Symbol"].map(nse_isins).where(cash["Group Name"].isin(NSE_EQUITY_SERIES | {"GB"}))
        else:
            cash["isin"] = cash["Token"].map(bse_isins)
        cash["isin"] = cash["isin"].fillna("")
        cash["broker_key"] = cash["Token"]
        frames.append(cash.rename(columns=ALICEBLUE_SOURCE_COLUMNS))
    return _finish(pd.concat(frames, ignore_index=True), "aliceblue")


def load_aliceblue(nse_path: Path, bse_path: Path, db_path: Path = DEFAULT_DB_PATH) -> pd.DataFrame:
    """Resolve cash identifiers against consolidated in one read snapshot."""
    with connect(db_path) as con:
        con.execute("BEGIN")
        return _load_aliceblue(nse_path, bse_path, con)


def import_broker(
    broker: Broker | str,
    path: Path,
    db_path: Path = DEFAULT_DB_PATH,
    *,
    bse_path: Path | None = None,
) -> list[BrokerImportResult]:
    """Replace one broker's rows in one transaction, preserving every other broker."""
    broker = Broker(broker).value
    if broker == "aliceblue" and bse_path is None:
        raise ValueError("Alice Blue requires both NSE and BSE files.")
    cash = load_groww(path) if broker == "groww" else load_upstox(path) if broker == "upstox" else None
    with connect(db_path) as con:
        con.execute("BEGIN")
        if broker == "aliceblue":
            cash = _load_aliceblue(path, bse_path, con)
        con.execute(
            f"CREATE TABLE IF NOT EXISTS {BROKER_TABLE} "
            f"({', '.join(f'{column} TEXT' for column in BROKER_COLUMNS)}, "
            "PRIMARY KEY (broker, exchange, exchange_token))"
        )
        con.execute(f"DELETE FROM {BROKER_TABLE} WHERE broker = ?", (broker,))
        con.executemany(
            f"INSERT INTO {BROKER_TABLE} ({', '.join(BROKER_COLUMNS)}) VALUES ({', '.join('?' for _ in BROKER_COLUMNS)})",
            cash.itertuples(index=False, name=None),
        )
        for column in ("isin", "symbol"):
            con.execute(f"CREATE INDEX IF NOT EXISTS idx_{BROKER_TABLE}_{column} ON {BROKER_TABLE} ({column})")
        membership = (
            "EXISTS (SELECT 1 FROM consolidated c WHERE c.isin = b.isin)" if table_exists(con, "consolidated") else "0"
        )
        rows = con.execute(
            f"SELECT broker, exchange, COUNT(*), COUNT(isin), SUM({membership}) "
            f"FROM {BROKER_TABLE} b WHERE broker = ? GROUP BY broker, exchange ORDER BY exchange",
            (broker,),
        ).fetchall()
    return [BrokerImportResult(*row) for row in rows]


def search_brokers(
    term: str,
    db_path: Path = DEFAULT_DB_PATH,
    *,
    limit: int = 20,
    exact: bool = False,
    broker: str | None = None,
    exchange: str | None = None,
) -> BrokerSearchResult:
    cleaned = term.strip()
    if not cleaned:
        raise ValueError("Search term must not be empty.")
    if limit < 1:
        raise ValueError("Limit must be at least 1.")
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}. Run 'stocky brokers import' first.")
    equality = " OR ".join(f"UPPER({column}) = :exact" for column in EXACT_COLUMNS)
    where = f"({equality})"
    params: dict[str, object] = {"exact": cleaned.upper()}
    if not exact:
        fragment = " OR ".join(f"UPPER({column}) LIKE :fragment ESCAPE '\\'" for column in FRAGMENT_COLUMNS)
        where = f"({equality} OR {fragment})"
        params["fragment"] = f"%{_escape_like(cleaned.upper())}%"
    for column, value in (("broker", broker), ("exchange", exchange)):
        if value is not None:
            where += f" AND UPPER({column}) = :{column}"
            params[column] = value.strip().upper()
    with connect(db_path) as con:
        # COUNT and SELECT must see the same snapshot during a concurrent import.
        con.execute("BEGIN")
        if not table_exists(con, BROKER_TABLE):
            raise RuntimeError(
                f"Database table '{BROKER_TABLE}' does not exist in {db_path}. Run 'stocky brokers import' first."
            )
        total = con.execute(f"SELECT COUNT(*) FROM {BROKER_TABLE} WHERE {where}", params).fetchone()[0]
        rows = con.execute(
            f"SELECT {', '.join(BROKER_COLUMNS)} FROM {BROKER_TABLE} WHERE {where} "
            f"ORDER BY CASE WHEN {equality} THEN 0 ELSE 1 END, name, exchange_token, broker, exchange LIMIT :limit",
            {**params, "limit": limit},
        ).fetchall()
    return BrokerSearchResult(matches=[BrokerMatch(*row) for row in rows], total=total)
