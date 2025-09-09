from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from stocky.config import DEFAULT_DB_PATH
from stocky.database import (
    connect,
    encode_response_json,
    fetch_consolidated_symbols,
    initialize_database,
    upsert_yahoo_response,
)


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
    ) -> YahooUpdateResult:
        suffix = exchange_suffix(exchange)
        symbols = fetch_consolidated_symbols(self.db_path, key=key, limit=limit)

        if dry_run:
            return YahooUpdateResult(processed=len(symbols), written=0, skipped=0, dry_run=True)

        import yahooquery as yq

        initialize_database(self.db_path)
        written = 0
        skipped = 0

        with connect(self.db_path) as con:
            for symbol in symbols:
                yahoo_symbol = f"{symbol}.{suffix}"
                ticker = yq.Ticker(yahoo_symbol)
                data = ticker.all_modules
                payload = data.get(yahoo_symbol)

                if payload == f"Quote not found for ticker symbol: {yahoo_symbol}":
                    skipped += 1
                    continue

                upsert_yahoo_response(
                    con,
                    yahoo_symbol=yahoo_symbol,
                    symbol=symbol,
                    exchange=exchange.upper(),
                    response_json=encode_response_json(data),
                    source="yahooquery",
                )
                written += 1

        return YahooUpdateResult(processed=len(symbols), written=written, skipped=skipped, dry_run=False)
