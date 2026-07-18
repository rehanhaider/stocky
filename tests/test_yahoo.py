import sqlite3
import sys
import types

from stocky.database import (
    connect,
    encode_response_json,
    initialize_database,
    read_available_yahoo_symbols,
    upsert_yahoo_response,
)
from stocky.yahoo import YahooDataManager


def _seed_consolidated(db_path, zd_symbols: list[str]) -> None:
    with sqlite3.connect(db_path) as con:
        con.execute("CREATE TABLE consolidated (isin TEXT, zd_symbol TEXT)")
        con.executemany(
            "INSERT INTO consolidated VALUES (?, ?)",
            [(f"INE{index:03d}", symbol) for index, symbol in enumerate(zd_symbols)],
        )


def _seed_cached_response(db_path, yahoo_symbol: str) -> None:
    initialize_database(db_path)
    with connect(db_path) as con:
        symbol, _, _ = yahoo_symbol.partition(".")
        upsert_yahoo_response(
            con,
            yahoo_symbol=yahoo_symbol,
            symbol=symbol,
            exchange="BSE",
            response_json=encode_response_json({"price": 1}),
            source="test",
        )


class _FakeTicker:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    @property
    def all_modules(self):
        if "BAD" in self.symbol:
            return {self.symbol: f"Quote not found for ticker symbol: {self.symbol}"}
        return {self.symbol: {"price": 100}}


def test_dry_run_missing_only_excludes_cached_symbols(tmp_path) -> None:
    db_path = tmp_path / "stocky.db"
    _seed_consolidated(db_path, ["RELIANCE", "INFY", "NEWIPO"])
    _seed_cached_response(db_path, "RELIANCE.BO")

    manager = YahooDataManager(db_path)

    assert manager.update_data(exchange="BSE", dry_run=True).processed == 3
    assert manager.update_data(exchange="BSE", dry_run=True, missing_only=True).processed == 2
    # The cached response is for .BO, so an NSE run still needs all three.
    assert manager.update_data(exchange="NSE", dry_run=True, missing_only=True).processed == 3


def test_update_writes_responses_and_skips_unknown_symbols(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "stocky.db"
    _seed_consolidated(db_path, ["RELIANCE", "BADSYMBOL"])

    fake_module = types.ModuleType("yahooquery")
    fake_module.Ticker = _FakeTicker
    monkeypatch.setitem(sys.modules, "yahooquery", fake_module)

    result = YahooDataManager(db_path).update_data(exchange="BSE")

    assert result.processed == 2
    assert result.written == 1
    assert result.skipped == 1
    assert read_available_yahoo_symbols(db_path) == {"RELIANCE.BO"}
