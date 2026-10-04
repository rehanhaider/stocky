from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from stocky.config import DEFAULT_DB_PATH
from stocky.database import (
    connect,
    delete_yahoo_response,
    encode_response_json,
    fetch_consolidated_symbols,
    initialize_database,
    is_usable_yahoo_payload,
    read_available_yahoo_symbols,
    read_yahoo_fetch_times,
    split_yahoo_symbol,
    upsert_yahoo_response,
)

COMMIT_EVERY = 25
CONSECUTIVE_FAILURE_LIMIT = 5
NOT_FOUND_PREFIX = "Quote not found for"

ProgressCallback = Callable[[int, int, int, int], None]


class YahooFetchError(RuntimeError):
    """Raised when Yahoo Finance keeps failing and the run cannot continue."""


Outcome = Literal["success", "not_found", "failure"]


def classify_payload(yahoo_symbol: str, data: object, payload: object) -> tuple[Outcome, str]:
    """Classify one symbol's ``all_modules`` result as success, not_found or failure.

    yahooquery reports most failures as return values rather than exceptions: a
    response it cannot decode becomes ``{"error": ...}`` for the whole call
    (base.py:188-190), and an API error becomes the error description in place
    of the symbol payload (base.py:290-303). Both must count as failures so a
    rate-limited run aborts instead of marking every symbol skipped.

    Yahoo answers an unlisted ticker with ``Quote not found for symbol: X`` and
    has also used ``Quote not found for ticker symbol: X``, so the prefix is
    matched rather than one exact sentence. A payload that does not name a listed
    security, such as an empty answer or an index under the same ticker, is not
    found too: it holds no data for the share.
    """
    if isinstance(payload, str) and payload.startswith(NOT_FOUND_PREFIX):
        return "not_found", ""
    if isinstance(payload, dict):
        if "error" in payload:
            return "failure", str(payload["error"])
        if not is_usable_yahoo_payload(payload):
            return "not_found", ""
        return "success", ""
    if payload is None:
        if isinstance(data, dict) and "error" in data:
            return "failure", str(data["error"])
        return "failure", f"no payload returned for {yahoo_symbol}"
    return "failure", str(payload)


@dataclass(frozen=True)
class YahooUpdateResult:
    processed: int
    written: int
    skipped: int
    dry_run: bool


# Each exchange reads the consolidated column that holds its full Yahoo tickers.
TICKER_COLUMNS = {"NSE": "yq_ns", "BSE": "yq_bo"}
TICKER_SUFFIXES = {"NSE": ".NS", "BSE": ".BO"}


def ticker_column(exchange: str) -> str:
    column = TICKER_COLUMNS.get(exchange.upper())
    if column is None:
        raise ValueError(f"Unsupported exchange: {exchange}. Expected NSE or BSE.")
    return column


def normalize_tickers(symbols: Sequence[str], exchange: str) -> list[str]:
    """Turn exchange symbols or full tickers into the exchange's Yahoo tickers, keeping the first of any repeats."""
    suffix = TICKER_SUFFIXES[exchange.upper()]
    tickers: list[str] = []
    for symbol in symbols:
        cleaned = symbol.strip().upper()
        if not cleaned:
            continue
        # A ticker that already names an exchange is kept, so one from the other exchange is reported as given.
        ticker = cleaned if cleaned.endswith(tuple(TICKER_SUFFIXES.values())) else f"{cleaned}{suffix}"
        if ticker not in tickers:
            tickers.append(ticker)
    return tickers


def _is_stale(fetched_at: str | None, cutoff: datetime) -> bool:
    if fetched_at is None:
        return True
    fetched = datetime.fromisoformat(fetched_at)
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=UTC)
    return fetched < cutoff


def _named_tickers(listed: list[str], symbols: Sequence[str], exchange: str, column: str) -> list[str]:
    """Keep the listed tickers that ``symbols`` names, rejecting any name the column does not hold."""
    wanted = set(normalize_tickers(symbols, exchange))
    if not wanted:
        raise ValueError("No symbols given.")
    unknown = sorted(wanted.difference(listed))
    if unknown:
        raise ValueError(f"Not {exchange.upper()} tickers in {column}: {', '.join(unknown)}")
    return [ticker for ticker in listed if ticker in wanted]


class YahooDataManager:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path

    def select_tickers(
        self,
        *,
        exchange: str,
        limit: int | None = None,
        missing_only: bool = False,
        symbols: Sequence[str] | None = None,
        stale_days: float | None = None,
    ) -> list[str]:
        """Return the exchange's tickers to fetch, narrowed by every selection given.

        ``symbols`` keeps only the named tickers and rejects any that the exchange's column does not
        hold. ``missing_only`` keeps tickers with no cached response; ``stale_days`` keeps those with no
        response or one fetched more than that many days ago. ``limit`` caps the selection last, so a
        limited run of missing or stale tickers fetches up to ``limit`` of them.
        """
        column = ticker_column(exchange)
        if limit is not None and limit < 0:
            raise ValueError("Limit must not be negative.")
        if stale_days is not None and stale_days < 0:
            raise ValueError("Stale days must not be negative.")

        listed = fetch_consolidated_symbols(self.db_path, key=column)
        if not listed and limit != 0:
            raise ValueError(
                f"No {exchange.upper()} tickers found in {column} in {self.db_path}. Run 'stocky rebuild' first."
            )

        selected = listed if symbols is None else _named_tickers(listed, symbols, exchange, column)

        if missing_only:
            available = read_available_yahoo_symbols(self.db_path)
            selected = [ticker for ticker in selected if ticker not in available]

        if stale_days is not None:
            cutoff = datetime.now(UTC) - timedelta(days=stale_days)
            fetched = read_yahoo_fetch_times(self.db_path)
            selected = [ticker for ticker in selected if _is_stale(fetched.get(ticker), cutoff)]

        return selected if limit is None else selected[:limit]

    def update_data(
        self,
        *,
        exchange: str = "BSE",
        dry_run: bool = False,
        limit: int | None = None,
        missing_only: bool = False,
        symbols: Sequence[str] | None = None,
        stale_days: float | None = None,
        progress: ProgressCallback | None = None,
    ) -> YahooUpdateResult:
        tickers = self.select_tickers(
            exchange=exchange, limit=limit, missing_only=missing_only, symbols=symbols, stale_days=stale_days
        )

        if dry_run:
            return YahooUpdateResult(processed=len(tickers), written=0, skipped=0, dry_run=True)

        import yahooquery as yq

        initialize_database(self.db_path)
        written = 0
        skipped = 0
        consecutive_failures = 0

        # The sqlite3 connection context manager commits on a clean exit, so the
        # writes after the last COMMIT_EVERY boundary are persisted there. The
        # abort path commits explicitly because that exit rolls back instead.
        with connect(self.db_path) as con:
            for index, yahoo_symbol in enumerate(tickers, start=1):
                cause: Exception | None = None
                try:
                    data = yq.Ticker(yahoo_symbol).all_modules
                    payload = data.get(yahoo_symbol) if isinstance(data, dict) else None
                except Exception as exc:
                    cause = exc
                    outcome: Outcome = "failure"
                    detail = repr(exc)
                else:
                    outcome, detail = classify_payload(yahoo_symbol, data, payload)

                if outcome == "failure":
                    consecutive_failures += 1
                    skipped += 1
                    if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                        con.commit()
                        raise YahooFetchError(
                            f"Yahoo Finance request failed for {yahoo_symbol} "
                            f"({consecutive_failures} consecutive failures; last error: {detail}). "
                            "Check the network connection, or wait if Yahoo is rate limiting, "
                            "then re-run with --missing-only to resume from the symbols already saved."
                        ) from cause
                elif outcome == "not_found":
                    consecutive_failures = 0
                    skipped += 1
                    # Drop an older answer, so status and --missing-only stop treating it as data.
                    delete_yahoo_response(con, yahoo_symbol)
                else:
                    consecutive_failures = 0
                    upsert_yahoo_response(
                        con,
                        yahoo_symbol=yahoo_symbol,
                        symbol=split_yahoo_symbol(yahoo_symbol)[0],
                        exchange=exchange.upper(),
                        response_json=encode_response_json(data),
                        source="yahooquery",
                    )
                    written += 1
                    if written % COMMIT_EVERY == 0:
                        con.commit()

                if progress is not None:
                    progress(index, len(tickers), written, skipped)

        return YahooUpdateResult(processed=len(tickers), written=written, skipped=skipped, dry_run=False)
