from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

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
PROGRESS_EVERY = 50

ProgressCallback = Callable[[int, int, int, int], None]


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

        if missing_only:
            available = read_available_yahoo_symbols(self.db_path)
            symbols = [symbol for symbol in symbols if f"{symbol}.{suffix}" not in available]

        if dry_run:
            return YahooUpdateResult(processed=len(symbols), written=0, skipped=0, dry_run=True)

        import yahooquery as yq

        initialize_database(self.db_path)
        written = 0
        skipped = 0

        with connect(self.db_path) as con:
            for index, symbol in enumerate(symbols, start=1):
                yahoo_symbol = f"{symbol}.{suffix}"
                try:
                    data = yq.Ticker(yahoo_symbol).all_modules
                    payload = data.get(yahoo_symbol)
                except Exception:
                    data = None
                    payload = None

                if payload is None or payload == f"Quote not found for ticker symbol: {yahoo_symbol}":
                    skipped += 1
                else:
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

                if progress is not None and index % PROGRESS_EVERY == 0:
                    progress(index, len(symbols), written, skipped)

        return YahooUpdateResult(processed=len(symbols), written=written, skipped=skipped, dry_run=False)
