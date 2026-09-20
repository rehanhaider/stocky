from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from stocky.config import DEFAULT_DB_PATH
from stocky.database import (
    connect,
    encode_response_json,
    fetch_consolidated_symbols,
    initialize_database,
    read_available_yahoo_symbols,
    upsert_yahoo_response,
)

COMMIT_EVERY = 25
CONSECUTIVE_FAILURE_LIMIT = 5

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
    """
    if payload == f"Quote not found for ticker symbol: {yahoo_symbol}":
        return "not_found", ""
    if isinstance(payload, dict):
        if "error" in payload:
            return "failure", str(payload["error"])
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


def exchange_suffix(exchange: str) -> str:
    normalized = exchange.upper()
    if normalized == "NSE":
        return "NS"
    if normalized == "BSE":
        return "BO"
    raise ValueError(f"Unsupported exchange: {exchange}. Expected NSE or BSE.")


class YahooDataManager:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path

    def update_data(
        self,
        *,
        key: str = "zd_symbol",
        exchange: str = "BSE",
        dry_run: bool = False,
        limit: int | None = None,
        missing_only: bool = False,
        progress: ProgressCallback | None = None,
    ) -> YahooUpdateResult:
        suffix = exchange_suffix(exchange)
        symbols = fetch_consolidated_symbols(self.db_path, key=key, limit=limit)

        if not symbols:
            raise ValueError(
                f"No symbols found for --key {key} in {self.db_path}. "
                "Try another --key (zd_symbol, yq_symbol, nse_symbol, bse_sc_code) or run 'stocky rebuild' first."
            )

        if missing_only:
            available = read_available_yahoo_symbols(self.db_path)
            symbols = [symbol for symbol in symbols if f"{symbol}.{suffix}" not in available]

        if dry_run:
            return YahooUpdateResult(processed=len(symbols), written=0, skipped=0, dry_run=True)

        import yahooquery as yq

        initialize_database(self.db_path)
        written = 0
        skipped = 0
        consecutive_failures = 0

        # The sqlite3 connection context manager commits on a clean exit, so the
        # writes after the last COMMIT_EVERY boundary are persisted there. The
        # abort path commits explicitly because that exit rolls back instead.
        with connect(self.db_path) as con:
            for index, symbol in enumerate(symbols, start=1):
                yahoo_symbol = f"{symbol}.{suffix}"
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
                else:
                    consecutive_failures = 0
                    upsert_yahoo_response(
                        con,
                        yahoo_symbol=yahoo_symbol,
                        symbol=symbol,
                        exchange=exchange.upper(),
                        response_json=encode_response_json(data),
                        source="yahooquery",
                    )
                    written += 1
                    if written % COMMIT_EVERY == 0:
                        con.commit()

                if progress is not None:
                    progress(index, len(symbols), written, skipped)

        return YahooUpdateResult(processed=len(symbols), written=written, skipped=skipped, dry_run=False)
