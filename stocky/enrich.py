from __future__ import annotations

import math
import re
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, fields
from io import BytesIO
from pathlib import Path

import pandas as pd

from stocky.config import DEFAULT_DB_PATH
from stocky.database import (
    CONSOLIDATED_TABLE,
    YAHOO_RESPONSES_TABLE,
    connect,
    decode_response_json,
    is_usable_yahoo_payload,
    require_consolidated_table,
    search_instruments,
    table_exists,
)
from stocky.export import _render_csv, _render_json, _require_pyarrow, _resolve_format, _write
from stocky.yahoo import TICKER_COLUMNS, ticker_column

# Market cap bounds on the command line are in crore, the unit Indian market caps are quoted in.
CRORE = 10_000_000

# A cached response is "ok" when it names a listed security, "unusable" when it holds an error or
# an answer for something else (an index, or Yahoo's NONE type), and "missing" when nothing is cached.
YAHOO_OK = "ok"
YAHOO_UNUSABLE = "unusable"
YAHOO_MISSING = "missing"

# Yahoo's earnings dates arrive through yahooquery as e.g. "2020-08-13 05:30:S"; only the date is reliable.
_DATE_PREFIX = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class YahooFields:
    """Fields read from one ticker's cached ``all_modules`` payload. Any field the payload lacks is None."""

    name: str | None = None
    quote_type: str | None = None
    currency: str | None = None
    market_cap: float | None = None
    sector: str | None = None
    industry: str | None = None
    price: float | None = None
    previous_close: float | None = None
    volume: float | None = None
    market_time: str | None = None
    fifty_two_week_high: float | None = None
    fifty_two_week_low: float | None = None
    trailing_pe: float | None = None
    trailing_eps: float | None = None
    book_value: float | None = None
    price_to_book: float | None = None
    dividend_yield: float | None = None
    beta: float | None = None
    shares_outstanding: float | None = None
    enterprise_value: float | None = None
    earnings_date: str | None = None
    earnings_date_end: str | None = None


YAHOO_FIELD_NAMES = tuple(field.name for field in fields(YahooFields))
_TEXT_FIELDS = frozenset(
    {"name", "quote_type", "currency", "sector", "industry", "market_time", "earnings_date", "earnings_date_end"}
)


@dataclass(frozen=True)
class EnrichedRow:
    """One instrument's listing on one exchange, with the Yahoo fields cached for that exchange's ticker."""

    isin: str
    exchange: str
    yahoo_ticker: str
    nse_symbol: str | None
    bse_symbol: str | None
    bse_sc_code: str | None
    bse_sc_name: str | None
    yahoo_status: str
    fetched_at: str | None
    fields: YahooFields

    def as_record(self) -> dict[str, object]:
        record = {field.name: getattr(self, field.name) for field in fields(self) if field.name != "fields"}
        return {**record, **asdict(self.fields)}


ENRICHED_COLUMNS = (
    *(field.name for field in fields(EnrichedRow) if field.name != "fields"),
    *YAHOO_FIELD_NAMES,
)


@dataclass(frozen=True)
class ScreenResult:
    rows: list[EnrichedRow]
    total: int


@dataclass(frozen=True)
class EnrichedExportResult:
    rows: int
    format: str
    output: Path | None


def _module(payload: dict, name: str) -> dict:
    module = payload.get(name)
    return module if isinstance(module, dict) else {}


def _number(value: object) -> float | None:
    # Yahoo sends "Infinity" for some ratios and {} for some missing numbers; neither is a value.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _first(*values: object) -> object:
    return next((value for value in values if value is not None), None)


def _earnings_dates(calendar: dict) -> tuple[str | None, str | None]:
    earnings = calendar.get("earnings")
    raw = earnings.get("earningsDate") if isinstance(earnings, dict) else None
    if not isinstance(raw, list):
        return None, None
    dates = sorted(match.group(1) for item in raw if isinstance(item, str) and (match := _DATE_PREFIX.match(item)))
    if not dates:
        return None, None
    return dates[0], dates[-1]


def extract_fields(payload: object) -> YahooFields:
    """Read the enrichment fields from one ticker's payload, tolerating any missing module or key.

    Where two modules carry the same value, the first one that holds it wins: market cap and currency
    come from ``price`` then ``summaryDetail``, sector and industry from ``assetProfile`` then
    ``summaryProfile``. A payload that is not a dict yields empty fields.
    """
    if not isinstance(payload, dict):
        return YahooFields()

    quote_type = _module(payload, "quoteType")
    price = _module(payload, "price")
    summary = _module(payload, "summaryDetail")
    statistics = _module(payload, "defaultKeyStatistics")
    profile = _module(payload, "assetProfile")
    summary_profile = _module(payload, "summaryProfile")
    earnings_date, earnings_date_end = _earnings_dates(_module(payload, "calendarEvents"))

    return YahooFields(
        name=_first(_text(quote_type.get("longName")), _text(quote_type.get("shortName"))),
        quote_type=_text(quote_type.get("quoteType")),
        currency=_first(_text(price.get("currency")), _text(summary.get("currency"))),
        market_cap=_first(_number(price.get("marketCap")), _number(summary.get("marketCap"))),
        sector=_first(_text(profile.get("sector")), _text(summary_profile.get("sector"))),
        industry=_first(_text(profile.get("industry")), _text(summary_profile.get("industry"))),
        price=_number(price.get("regularMarketPrice")),
        previous_close=_first(_number(summary.get("previousClose")), _number(price.get("regularMarketPreviousClose"))),
        volume=_number(price.get("regularMarketVolume")),
        market_time=_text(price.get("regularMarketTime")),
        fifty_two_week_high=_number(summary.get("fiftyTwoWeekHigh")),
        fifty_two_week_low=_number(summary.get("fiftyTwoWeekLow")),
        trailing_pe=_number(summary.get("trailingPE")),
        trailing_eps=_number(statistics.get("trailingEps")),
        book_value=_number(statistics.get("bookValue")),
        price_to_book=_number(statistics.get("priceToBook")),
        dividend_yield=_number(summary.get("dividendYield")),
        beta=_number(statistics.get("beta")),
        shares_outstanding=_number(statistics.get("sharesOutstanding")),
        enterprise_value=_number(statistics.get("enterpriseValue")),
        earnings_date=earnings_date,
        earnings_date_end=earnings_date_end,
    )


def _ticker_payload(yahoo_ticker: str, response_json: bytes | None) -> tuple[str, object]:
    """Decode a cached response and return its status with the ticker's own payload."""
    if response_json is None:
        return YAHOO_MISSING, None
    try:
        response = decode_response_json(response_json)
    except (OSError, ValueError):
        # A blob that is not gzipped JSON holds nothing to read, like an error answer.
        return YAHOO_UNUSABLE, None
    payload = response.get(yahoo_ticker) if isinstance(response, dict) else None
    if not is_usable_yahoo_payload(payload):
        return YAHOO_UNUSABLE, None
    return YAHOO_OK, payload


def _resolve_exchanges(exchange: str | None) -> tuple[str, ...]:
    if exchange is None:
        return tuple(TICKER_COLUMNS)
    ticker_column(exchange)
    return (exchange.upper(),)


def read_enriched(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    exchange: str | None = None,
    isins: Sequence[str] | None = None,
) -> list[EnrichedRow]:
    """Join each instrument's Yahoo fields onto it, one row per exchange that lists it.

    Each exchange's row reads only the response cached under that exchange's ticker column, ``yq_ns``
    for NSE and ``yq_bo`` for BSE. A bare symbol is never matched, because the same symbol can name
    different companies on the two exchanges. Instruments without a ticker on an exchange have no row
    for it; a ticker with nothing cached keeps its row with empty fields and status "missing".
    """
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}. Run 'stocky rebuild' first.")

    exchanges = _resolve_exchanges(exchange)
    rows: list[EnrichedRow] = []
    with connect(db_path) as con:
        require_consolidated_table(con, db_path)
        has_responses = table_exists(con, YAHOO_RESPONSES_TABLE)
        for name in exchanges:
            column = TICKER_COLUMNS[name]
            response_columns = "y.response_json, y.fetched_at" if has_responses else "NULL, NULL"
            join = f"LEFT JOIN {YAHOO_RESPONSES_TABLE} y ON y.yahoo_symbol = c.{column}" if has_responses else ""
            query = f"""
                SELECT c.isin, c.{column}, c.nse_symbol, c.bse_symbol, c.bse_sc_code, c.bse_sc_name,
                       {response_columns}
                FROM {CONSOLIDATED_TABLE} c
                {join}
                WHERE c.{column} IS NOT NULL AND TRIM(c.{column}) != ''
            """
            params: list[str] = []
            if isins is not None:
                query += f" AND c.isin IN ({', '.join('?' for _ in isins)})"
                params.extend(isins)
            for (
                isin,
                ticker,
                nse_symbol,
                bse_symbol,
                bse_sc_code,
                bse_sc_name,
                response_json,
                fetched_at,
            ) in con.execute(query, params).fetchall():
                status, payload = _ticker_payload(ticker, response_json)
                rows.append(
                    EnrichedRow(
                        isin=isin,
                        exchange=name,
                        yahoo_ticker=ticker,
                        nse_symbol=nse_symbol,
                        bse_symbol=bse_symbol,
                        bse_sc_code=bse_sc_code,
                        bse_sc_name=bse_sc_name,
                        yahoo_status=status,
                        fetched_at=fetched_at,
                        fields=extract_fields(payload),
                    )
                )

    rows.sort(key=lambda row: (row.isin or "", row.exchange))
    return rows


def lookup_fields(identifier: str, db_path: Path = DEFAULT_DB_PATH, *, limit: int = 20) -> list[EnrichedRow]:
    """Return the Yahoo fields of every instrument that exactly matches a symbol, ticker, ISIN, code, or name."""
    result = search_instruments(identifier, db_path, exact=True, limit=limit)
    isins = [match.isin for match in result.matches if match.isin]
    if not isins:
        return []
    return read_enriched(db_path, isins=isins)


def _matches_text(value: str | None, wanted: str | None) -> bool:
    return wanted is None or (value is not None and wanted.strip().casefold() in value.casefold())


def screen_instruments(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    exchange: str | None = None,
    market_cap_gt: float | None = None,
    market_cap_lt: float | None = None,
    sector: str | None = None,
    industry: str | None = None,
    limit: int | None = 20,
) -> ScreenResult:
    """Filter listings with usable cached Yahoo data, largest market cap first.

    Market cap bounds are in crore and exclusive. Sector and industry match a case-insensitive
    substring. A listing without a market cap fails any market cap bound and sorts last otherwise.
    """
    if limit is not None and limit < 1:
        raise ValueError("Limit must be at least 1.")

    rows = [row for row in read_enriched(db_path, exchange=exchange) if row.yahoo_status == YAHOO_OK]
    lower = None if market_cap_gt is None else market_cap_gt * CRORE
    upper = None if market_cap_lt is None else market_cap_lt * CRORE

    selected = []
    for row in rows:
        cap = row.fields.market_cap
        if lower is not None and (cap is None or cap <= lower):
            continue
        if upper is not None and (cap is None or cap >= upper):
            continue
        if not _matches_text(row.fields.sector, sector) or not _matches_text(row.fields.industry, industry):
            continue
        selected.append(row)

    selected.sort(key=lambda row: (row.fields.market_cap is None, -(row.fields.market_cap or 0), row.yahoo_ticker))
    return ScreenResult(rows=selected if limit is None else selected[:limit], total=len(selected))


def _enriched_frame(rows: Sequence[EnrichedRow]) -> pd.DataFrame:
    return pd.DataFrame([row.as_record() for row in rows], columns=list(ENRICHED_COLUMNS))


def _render_enriched_parquet(frame: pd.DataFrame) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    numeric = set(YAHOO_FIELD_NAMES) - _TEXT_FIELDS
    schema = pa.schema((column, pa.float64() if column in numeric else pa.string()) for column in frame.columns)
    table = pa.Table.from_pandas(frame, schema=schema, preserve_index=False)
    buffer = BytesIO()
    pq.write_table(table, buffer)
    return buffer.getvalue()


def export_enriched(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    output: Path | None,
    format: str | None,
    exchange: str | None = None,
) -> EnrichedExportResult:
    """Write the enriched view, one row per instrument per exchange, as CSV, JSON, or Parquet."""
    resolved_format = _resolve_format(output, format)
    if resolved_format == "parquet":
        _require_pyarrow()
        if output is None and sys.stdout.isatty():
            raise ValueError("Parquet output is binary. Pass --output FILE or redirect stdout to a file.")

    frame = _enriched_frame(read_enriched(db_path, exchange=exchange))
    if resolved_format == "csv":
        payload: str | bytes = _render_csv(frame)
    elif resolved_format == "json":
        payload = _render_json(frame)
    else:
        payload = _render_enriched_parquet(frame)
    _write(payload, output)

    return EnrichedExportResult(rows=len(frame.index), format=resolved_format, output=output)
