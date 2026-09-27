from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from stocky.config import DEFAULT_DB_PATH
from stocky.database import (
    CONSOLIDATED_TABLE,
    YAHOO_RESPONSES_TABLE,
    connect,
    decode_response_json,
    table_exists,
)

YAHOO_FIELDS_TABLE = "yahoo_fields"
ENRICHED_VIEW = "consolidated_yahoo"
CRORE = 10_000_000
SQLITE_INTEGER_MAX = 2**63 - 1

# Every extracted column, its SQLite type, and where it comes from. A column with several
# sources takes the first one that holds a usable value, because not every module is
# present for every ticker (assetProfile is missing for ~40% of the cache).
FIELD_SOURCES: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "name": ("TEXT", (("price", "longName"), ("price", "shortName"), ("quoteType", "longName"))),
    "quote_type": ("TEXT", (("price", "quoteType"), ("quoteType", "quoteType"))),
    "currency": ("TEXT", (("price", "currency"), ("summaryDetail", "currency"))),
    "sector": ("TEXT", (("assetProfile", "sector"), ("summaryProfile", "sector"))),
    "industry": ("TEXT", (("assetProfile", "industry"), ("summaryProfile", "industry"))),
    "market_cap": ("INTEGER", (("price", "marketCap"), ("summaryDetail", "marketCap"))),
    "regular_market_price": ("REAL", (("price", "regularMarketPrice"), ("financialData", "currentPrice"))),
    "regular_market_change_percent": ("REAL", (("price", "regularMarketChangePercent"),)),
    "regular_market_volume": ("INTEGER", (("price", "regularMarketVolume"), ("summaryDetail", "volume"))),
    "regular_market_time": ("TEXT", (("price", "regularMarketTime"),)),
    "previous_close": ("REAL", (("price", "regularMarketPreviousClose"), ("summaryDetail", "previousClose"))),
    "fifty_two_week_high": ("REAL", (("summaryDetail", "fiftyTwoWeekHigh"),)),
    "fifty_two_week_low": ("REAL", (("summaryDetail", "fiftyTwoWeekLow"),)),
    "trailing_pe": ("REAL", (("summaryDetail", "trailingPE"), ("defaultKeyStatistics", "trailingPE"))),
    "forward_pe": ("REAL", (("defaultKeyStatistics", "forwardPE"), ("summaryDetail", "forwardPE"))),
    "price_to_book": ("REAL", (("defaultKeyStatistics", "priceToBook"),)),
    "beta": ("REAL", (("defaultKeyStatistics", "beta"), ("summaryDetail", "beta"))),
    "trailing_eps": ("REAL", (("defaultKeyStatistics", "trailingEps"),)),
    "book_value": ("REAL", (("defaultKeyStatistics", "bookValue"),)),
    "dividend_yield": ("REAL", (("summaryDetail", "dividendYield"),)),
    "enterprise_value": ("INTEGER", (("defaultKeyStatistics", "enterpriseValue"),)),
    "shares_outstanding": ("INTEGER", (("defaultKeyStatistics", "sharesOutstanding"),)),
}
EARNINGS_COLUMNS = ("earnings_date", "earnings_date_end")
FIELD_COLUMNS = (*FIELD_SOURCES, *EARNINGS_COLUMNS)
KEY_COLUMNS = ("yahoo_symbol", "symbol", "exchange", "fetched_at")
SCREEN_EXCHANGES = ("BSE", "NSE")


@dataclass(frozen=True)
class ExtractResult:
    extracted: int
    skipped: int


@dataclass(frozen=True)
class ScreenResult:
    rows: list[dict[str, object]]
    total: int
    stale: bool


def _number(value: object) -> int | float | None:
    # Yahoo sometimes sends "Infinity" for P/E or an empty dict for market cap; neither is a number.
    # An integer past SQLite's 64-bit range would abort the whole extraction, so it is dropped too.
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, int) and abs(value) > SQLITE_INTEGER_MAX:
        return None
    return value


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _earnings_dates(payload: dict) -> tuple[str | None, str | None]:
    """Return the first and last date of Yahoo's earnings window as ISO dates.

    Yahoo sends zero, one, or two timestamps (a single date or a start/end window), in a
    malformed shape such as ``2020-08-13 05:30:S``, so only the date part is trusted.
    """
    calendar = payload.get("calendarEvents")
    earnings = calendar.get("earnings") if isinstance(calendar, dict) else None
    raw_dates = earnings.get("earningsDate") if isinstance(earnings, dict) else None
    if not isinstance(raw_dates, list):
        return None, None

    dates: list[str] = []
    for raw in raw_dates:
        if not isinstance(raw, str):
            continue
        try:
            dates.append(date.fromisoformat(raw[:10]).isoformat())
        except ValueError:
            continue
    if not dates:
        return None, None
    return min(dates), max(dates) if len(dates) > 1 else None


def extract_fields(payload: object) -> dict[str, object] | None:
    """Pull the queryable fields out of one symbol's ``all_modules`` payload.

    Returns None when the payload is not a module dictionary (Yahoo stores an error string
    for some tickers). Missing modules and keys become None rather than errors.
    """
    if not isinstance(payload, dict):
        return None

    fields: dict[str, object] = {}
    for column, (sql_type, sources) in FIELD_SOURCES.items():
        convert = _text if sql_type == "TEXT" else _number
        value = None
        for module_name, key in sources:
            module = payload.get(module_name)
            if isinstance(module, dict):
                value = convert(module.get(key))
            if value is not None:
                break
        fields[column] = value

    fields["earnings_date"], fields["earnings_date_end"] = _earnings_dates(payload)
    return fields


def _response_payload(yahoo_symbol: str, response: object) -> object:
    # all_modules is keyed by the requested symbol; the payload sits one level down.
    return response.get(yahoo_symbol) if isinstance(response, dict) else None


def _create_fields_table(con: sqlite3.Connection) -> None:
    columns = ",\n".join(
        [
            "yahoo_symbol TEXT PRIMARY KEY",
            "symbol TEXT NOT NULL",
            "exchange TEXT NOT NULL",
            "fetched_at TEXT NOT NULL",
            "available INTEGER NOT NULL",
            *(f"{column} {sql_type}" for column, (sql_type, _) in FIELD_SOURCES.items()),
            *(f"{column} TEXT" for column in EARNINGS_COLUMNS),
        ]
    )
    con.execute(f"DROP TABLE IF EXISTS {YAHOO_FIELDS_TABLE}")
    con.execute(f"CREATE TABLE {YAHOO_FIELDS_TABLE} (\n{columns}\n)")
    con.execute(f"CREATE INDEX idx_{YAHOO_FIELDS_TABLE}_symbol ON {YAHOO_FIELDS_TABLE} (symbol)")


def _create_enriched_view(con: sqlite3.Connection) -> None:
    # One row per consolidated instrument and exchange with a usable cached response. The join
    # mirrors how rebuild picks yq_symbol: the bare symbol behind a cached .NS or .BO ticker.
    selected = ", ".join(
        [
            "c.isin",
            "c.ins_type",
            "c.zd_symbol",
            "c.yq_symbol",
            "c.nse_symbol",
            "c.bse_sc_code",
            "c.bse_sc_name",
            *(f"f.{column}" for column in (*KEY_COLUMNS, *FIELD_COLUMNS) if column != "symbol"),
        ]
    )
    con.execute(f"DROP VIEW IF EXISTS {ENRICHED_VIEW}")
    con.execute(
        f"""
        CREATE VIEW {ENRICHED_VIEW} AS
        SELECT {selected}
        FROM {CONSOLIDATED_TABLE} c
        JOIN {YAHOO_FIELDS_TABLE} f ON f.symbol = c.yq_symbol
        WHERE f.available = 1
        """
    )


def materialize_yahoo_fields(db_path: Path = DEFAULT_DB_PATH) -> ExtractResult:
    """Rebuild the ``yahoo_fields`` table from every cached response and (re)create the view."""
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}. Run 'stocky rebuild' first.")

    extracted = 0
    skipped = 0
    with connect(db_path) as con:
        if not table_exists(con, YAHOO_RESPONSES_TABLE):
            raise RuntimeError(
                f"Database table '{YAHOO_RESPONSES_TABLE}' does not exist in {db_path}. "
                "Run 'stocky yahoo update' or 'stocky yahoo import-cache' first."
            )

        # sqlite3 commits DDL immediately outside a transaction. Opening one explicitly keeps the
        # previous snapshot intact if extraction fails partway: the drop rolls back with the inserts.
        con.execute("BEGIN")
        _create_fields_table(con)
        # Unusable responses are stored too, flagged unavailable, so screen can tell a
        # snapshot that is out of date from one that simply skipped Yahoo error strings.
        columns = (*KEY_COLUMNS, "available", *FIELD_COLUMNS)
        insert = f"INSERT INTO {YAHOO_FIELDS_TABLE} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})"
        rows = con.execute(
            f"SELECT yahoo_symbol, symbol, exchange, fetched_at, response_json FROM {YAHOO_RESPONSES_TABLE}"
        ).fetchall()
        for yahoo_symbol, symbol, exchange, fetched_at, response_json in rows:
            fields = extract_fields(_response_payload(yahoo_symbol, decode_response_json(response_json)))
            if fields is None:
                skipped += 1
                values = (None,) * len(FIELD_COLUMNS)
            else:
                extracted += 1
                values = tuple(fields[column] for column in FIELD_COLUMNS)
            con.execute(insert, (yahoo_symbol, symbol, exchange, fetched_at, int(fields is not None), *values))

        _create_enriched_view(con)

    return ExtractResult(extracted=extracted, skipped=skipped)


def read_yahoo_fields(symbol: str, db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, object]]:
    """Extract fields straight from the cached blobs for a bare symbol or a full Yahoo ticker.

    ``RELIANCE`` returns both the ``.NS`` and ``.BO`` responses; ``RELIANCE.NS`` returns one.
    Reading the blob rather than ``yahoo_fields`` means this works before any extraction run.
    """
    cleaned = symbol.strip().upper()
    if not cleaned:
        raise ValueError("Symbol must not be empty.")
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}. Run 'stocky rebuild' first.")

    with connect(db_path) as con:
        if not table_exists(con, YAHOO_RESPONSES_TABLE):
            raise RuntimeError(
                f"Database table '{YAHOO_RESPONSES_TABLE}' does not exist in {db_path}. "
                "Run 'stocky yahoo update' or 'stocky yahoo import-cache' first."
            )
        rows = con.execute(
            f"""
            SELECT yahoo_symbol, symbol, exchange, fetched_at, response_json
            FROM {YAHOO_RESPONSES_TABLE}
            WHERE UPPER(yahoo_symbol) = :symbol OR UPPER(symbol) = :symbol
            ORDER BY exchange, yahoo_symbol
            """,
            {"symbol": cleaned},
        ).fetchall()

    results: list[dict[str, object]] = []
    for yahoo_symbol, bare_symbol, exchange, fetched_at, response_json in rows:
        fields = extract_fields(_response_payload(yahoo_symbol, decode_response_json(response_json)))
        results.append(
            {
                "yahoo_symbol": yahoo_symbol,
                "symbol": bare_symbol,
                "exchange": exchange,
                "fetched_at": fetched_at,
                "available": fields is not None,
                **(fields or dict.fromkeys(FIELD_COLUMNS)),
            }
        )
    return results


def screen_instruments(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    market_cap_gt: float | None = None,
    market_cap_lt: float | None = None,
    exchange: str | None = None,
    sector: str | None = None,
    industry: str | None = None,
    limit: int = 50,
) -> ScreenResult:
    """Filter the enriched view. Market cap bounds are in crore INR; results are largest first."""
    if limit < 1:
        raise ValueError("Limit must be at least 1.")
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}. Run 'stocky rebuild' first.")

    conditions: list[str] = []
    params: dict[str, object] = {"limit": limit}
    if exchange is not None:
        normalized = exchange.strip().upper()
        if normalized not in SCREEN_EXCHANGES:
            raise ValueError(f"Unsupported exchange: {exchange}. Expected NSE or BSE.")
        conditions.append("exchange = :exchange")
        params["exchange"] = normalized
    if market_cap_gt is not None:
        conditions.append("market_cap > :market_cap_gt")
        params["market_cap_gt"] = market_cap_gt * CRORE
    if market_cap_lt is not None:
        conditions.append("market_cap < :market_cap_lt")
        params["market_cap_lt"] = market_cap_lt * CRORE
    if sector is not None:
        conditions.append("UPPER(sector) = UPPER(:sector)")
        params["sector"] = sector.strip()
    if industry is not None:
        conditions.append("UPPER(industry) = UPPER(:industry)")
        params["industry"] = industry.strip()
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with connect(db_path) as con:
        if not table_exists(con, YAHOO_FIELDS_TABLE):
            raise RuntimeError(f"No extracted Yahoo fields in {db_path}. Run 'stocky yahoo extract' first.")
        if not table_exists(con, CONSOLIDATED_TABLE):
            raise RuntimeError(f"Database table '{CONSOLIDATED_TABLE}' does not exist in {db_path}")

        total = con.execute(f"SELECT COUNT(*) FROM {ENRICHED_VIEW} {where}", params).fetchone()[0]
        cursor = con.execute(
            f"""
            SELECT isin, yahoo_symbol, exchange, name, sector, industry, market_cap,
                   regular_market_price, trailing_pe
            FROM {ENRICHED_VIEW}
            {where}
            ORDER BY market_cap IS NULL, market_cap DESC, yahoo_symbol
            LIMIT :limit
            """,
            params,
        )
        names = [description[0] for description in cursor.description]
        rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

        # yahoo_fields is a snapshot; flag it when responses were added or refetched since.
        stale = (
            con.execute(
                f"""
                SELECT 1 FROM {YAHOO_RESPONSES_TABLE} y
                LEFT JOIN {YAHOO_FIELDS_TABLE} f ON f.yahoo_symbol = y.yahoo_symbol
                WHERE f.yahoo_symbol IS NULL OR f.fetched_at != y.fetched_at
                LIMIT 1
                """
            ).fetchone()
            is not None
        )

    return ScreenResult(rows=rows, total=total, stale=stale)
