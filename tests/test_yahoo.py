import os
import sqlite3
import sys
import types
from datetime import UTC, datetime, timedelta

import pytest

import stocky.yahoo as yahoo
from stocky.database import (
    SCHEMA_VERSION,
    OrphanVacuumError,
    connect,
    decode_response_json,
    encode_response_json,
    initialize_database,
    read_available_yahoo_symbols,
    upsert_yahoo_response,
)
from stocky.yahoo import YahooDataManager, ticker_column


def _ticker_row(isin: str, symbol: str) -> tuple[object, ...]:
    """A consolidated row that both exchanges list, with only the Yahoo tickers filled."""
    tickers = (f"{symbol}.NS", f"{symbol}.BO") if symbol else ("", "")
    return (isin, "equity", None, None, None, None, None, None, *tickers)


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
    """Stands in for ``yahooquery.Ticker``: one client per run, its symbol swapped before each request."""

    def __init__(self, symbols: str) -> None:
        self.symbols = symbols

    @property
    def symbol(self) -> str:
        return self.symbols

    @property
    def all_modules(self):
        if "BAD" in self.symbol:
            return {self.symbol: f"Quote not found for symbol: {self.symbol}"}
        return {
            self.symbol: {
                "quoteType": {"quoteType": "EQUITY", "longName": self.symbol},
                "price": {"regularMarketPrice": 100},
            }
        }


def _seed_fetched_at(db_path, yahoo_symbol: str, fetched_at: str) -> None:
    _seed_cached_response(db_path, yahoo_symbol)
    with connect(db_path) as con:
        con.execute("UPDATE yahoo_responses SET fetched_at = ? WHERE yahoo_symbol = ?", (fetched_at, yahoo_symbol))


def _days_ago(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def test_dry_run_symbols_selects_named_tickers_from_symbols_or_full_tickers(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(
        db_path, [_ticker_row(f"INE{index:03d}", s) for index, s in enumerate(["RELIANCE", "INFY", "TCS"])]
    )

    manager = YahooDataManager(db_path)

    assert manager.update_data(exchange="NSE", dry_run=True, symbols=["infy", "RELIANCE.NS", "INFY.NS"]).processed == 2
    assert manager.update_data(exchange="BSE", dry_run=True, symbols=["TCS.BO"], limit=0).processed == 0


def test_update_rejects_symbols_the_exchange_does_not_list(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path, [_ticker_row("INE001", "RELIANCE")])

    with pytest.raises(ValueError, match=r"Not NSE tickers in yq_ns: GLOBE\.NS, RELIANCE\.BO$"):
        YahooDataManager(db_path).update_data(
            exchange="NSE", dry_run=True, symbols=["RELIANCE", "GLOBE", "RELIANCE.BO"]
        )
    with pytest.raises(ValueError, match="No symbols given"):
        YahooDataManager(db_path).update_data(exchange="NSE", dry_run=True, symbols=[" "])


def test_dry_run_stale_days_selects_missing_and_old_responses(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path, [_ticker_row(f"INE{index:03d}", s) for index, s in enumerate(["OLD", "FRESH", "NEW"])])
    _seed_fetched_at(db_path, "OLD.BO", _days_ago(120))
    _seed_fetched_at(db_path, "FRESH.BO", _days_ago(5))
    # A naive timestamp from an older import counts as UTC.
    _seed_fetched_at(db_path, "OLD.NS", (datetime.now(UTC) - timedelta(days=120)).replace(tzinfo=None).isoformat())

    manager = YahooDataManager(db_path)

    assert manager.update_data(exchange="BSE", dry_run=True, stale_days=90).processed == 2
    assert manager.update_data(exchange="BSE", dry_run=True, stale_days=1).processed == 3
    assert manager.update_data(exchange="BSE", dry_run=True, stale_days=90, missing_only=True).processed == 1
    assert manager.update_data(exchange="NSE", dry_run=True, stale_days=90, symbols=["OLD", "FRESH"]).processed == 2


def test_update_stale_days_fetches_only_the_selected_tickers(tmp_path, monkeypatch, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path, [_ticker_row(f"INE{index:03d}", s) for index, s in enumerate(["OLD", "FRESH"])])
    _seed_fetched_at(db_path, "OLD.BO", _days_ago(120))
    _seed_fetched_at(db_path, "FRESH.BO", _days_ago(5))
    fetched: list[str] = []

    class RecordingTicker(_FakeTicker):
        @property
        def all_modules(self):
            fetched.append(self.symbol)
            return super().all_modules

    monkeypatch.setitem(sys.modules, "yahooquery", types.SimpleNamespace(Ticker=RecordingTicker))

    result = YahooDataManager(db_path).update_data(exchange="BSE", stale_days=90)

    assert fetched == ["OLD.BO"]
    assert (result.processed, result.written) == (1, 1)


def test_limit_applies_after_the_missing_only_selection(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path, [_ticker_row(f"INE{index:03d}", s) for index, s in enumerate(["AAA", "BBB", "CCC"])])
    _seed_cached_response(db_path, "AAA.BO")

    # The first ticker is cached, so a limit of one still finds a missing ticker to fetch.
    assert (
        YahooDataManager(db_path).update_data(exchange="BSE", dry_run=True, missing_only=True, limit=1).processed == 1
    )


def test_dry_run_missing_only_excludes_cached_symbols(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(
        db_path,
        [_ticker_row(f"INE{index:03d}", symbol) for index, symbol in enumerate(["RELIANCE", "INFY", "NEWIPO"])],
    )
    _seed_cached_response(db_path, "RELIANCE.BO")

    manager = YahooDataManager(db_path)

    assert manager.update_data(exchange="BSE", dry_run=True).processed == 3
    assert manager.update_data(exchange="BSE", dry_run=True, missing_only=True).processed == 2
    # The cached response is for .BO, so an NSE run still needs all three.
    assert manager.update_data(exchange="NSE", dry_run=True, missing_only=True).processed == 3
    assert manager.update_data(exchange="BSE", dry_run=True, limit=1).processed == 1
    with connect(db_path) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 0


def test_update_writes_responses_and_skips_unknown_symbols(tmp_path, monkeypatch, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(
        db_path,
        [_ticker_row(f"INE{index:03d}", symbol) for index, symbol in enumerate(["RELIANCE", "BADSYMBOL"])],
    )

    fake_module = types.ModuleType("yahooquery")
    fake_module.Ticker = _FakeTicker
    monkeypatch.setitem(sys.modules, "yahooquery", fake_module)

    result = YahooDataManager(db_path).update_data(exchange="BSE")

    assert result.processed == 2
    assert result.written == 1
    assert result.skipped == 1
    assert read_available_yahoo_symbols(db_path) == {"RELIANCE.BO"}
    with connect(db_path) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_an_interrupted_update_rolls_the_schema_number_back_with_its_rows(
    tmp_path, monkeypatch, seed_consolidated
) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path, [_ticker_row("INE001", "RELIANCE"), _ticker_row("INE002", "INFY")])
    initialize_database(db_path)
    _use_fake_yahooquery(monkeypatch)
    upsert = yahoo.upsert_yahoo_response
    calls = []

    def interrupted(*args, **kwargs):
        calls.append(kwargs["yahoo_symbol"])
        if len(calls) == 2:
            raise KeyboardInterrupt
        upsert(*args, **kwargs)

    monkeypatch.setattr(yahoo, "upsert_yahoo_response", interrupted)

    with pytest.raises(KeyboardInterrupt):
        YahooDataManager(db_path).update_data(exchange="NSE")

    with connect(db_path) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM yahoo_responses").fetchone()[0] == 0


def test_update_fetches_only_the_exchange_column_tickers(tmp_path, monkeypatch, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(
        db_path,
        [
            (
                "INE002A01018",
                "equity",
                "RELIANCE",
                "RELIANCE",
                "500325",
                "R",
                "RELIANCE",
                "RELIANCE",
                "RELIANCE.NS",
                "RELIANCE.BO",
            ),
            ("INE581X01021", "equity", "GLOBE", None, None, None, "GLOBE", None, "GLOBE.NS", None),
            ("INE198N01017", "equity", None, "CDG", "534796", "C", None, "CDG", None, "CDG.BO"),
        ],
    )
    requested: list[str] = []

    class RecordingTicker(_FakeTicker):
        @property
        def all_modules(self):
            requested.append(self.symbol)
            return super().all_modules

    fake_module = types.ModuleType("yahooquery")
    fake_module.Ticker = RecordingTicker
    monkeypatch.setitem(sys.modules, "yahooquery", fake_module)

    YahooDataManager(db_path).update_data(exchange="BSE")
    YahooDataManager(db_path).update_data(exchange="NSE")

    # GLOBE is NSE-only, so no GLOBE.BO is ever requested; CDG is BSE-only, so no CDG.NS.
    assert requested == ["RELIANCE.BO", "CDG.BO", "RELIANCE.NS", "GLOBE.NS"]
    with connect(db_path) as con:
        stored = con.execute(
            "SELECT yahoo_symbol, symbol, exchange FROM yahoo_responses ORDER BY yahoo_symbol"
        ).fetchall()
    assert stored == [
        ("CDG.BO", "CDG", "BSE"),
        ("GLOBE.NS", "GLOBE", "NSE"),
        ("RELIANCE.BO", "RELIANCE", "BSE"),
        ("RELIANCE.NS", "RELIANCE", "NSE"),
    ]


def test_update_drops_a_saved_answer_when_yahoo_now_has_no_usable_data(
    tmp_path, monkeypatch, seed_consolidated
) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(
        db_path,
        [
            ("INF769K01PR2", "equity", None, "ENERGY", "1", "M", None, "ENERGY", None, "ENERGY.BO"),
            ("INE106T01025", "equity", None, "HITECH", "2", "H", None, "HITECH", None, "HITECH.BO"),
        ],
    )
    # Earlier saved answers: an index under the ETF's ticker, and an out-of-date company name.
    _seed_cached_response(db_path, "ENERGY.BO")
    _seed_cached_response(db_path, "HITECH.BO")

    class CurrentYahoo(_FakeTicker):
        @property
        def all_modules(self):
            if self.symbol == "ENERGY.BO":
                return {self.symbol: {"quoteType": {"quoteType": "INDEX", "longName": "S&P BSE ENERGY"}}}
            return {self.symbol: {"quoteType": {"quoteType": "EQUITY", "longName": "Hi-Tech Pipes Limited"}}}

    fake_module = types.ModuleType("yahooquery")
    fake_module.Ticker = CurrentYahoo
    monkeypatch.setitem(sys.modules, "yahooquery", fake_module)

    result = YahooDataManager(db_path).update_data(exchange="BSE")

    assert (result.written, result.skipped) == (1, 1)
    assert read_available_yahoo_symbols(db_path) == {"HITECH.BO"}
    with connect(db_path) as con:
        (blob,) = con.execute("SELECT response_json FROM yahoo_responses WHERE yahoo_symbol = 'HITECH.BO'").fetchone()
    assert decode_response_json(blob)["HITECH.BO"]["quoteType"]["longName"] == "Hi-Tech Pipes Limited"


def test_update_builds_one_client_for_every_ticker(tmp_path, monkeypatch, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    symbols = ["RELIANCE", "BADSYMBOL", "INFY", "TCS"]
    seed_consolidated(db_path, [_ticker_row(f"INE{index:03d}", symbol) for index, symbol in enumerate(symbols)])
    constructions: list[str] = []
    requested: list[str] = []

    class CountingTicker(_FakeTicker):
        def __init__(self, symbols: str) -> None:
            super().__init__(symbols)
            constructions.append(symbols)

        @property
        def all_modules(self):
            requested.append(self.symbol)
            return super().all_modules

    monkeypatch.setitem(sys.modules, "yahooquery", types.SimpleNamespace(Ticker=CountingTicker))

    result = YahooDataManager(db_path).update_data(exchange="NSE")

    # One client, built for the first ticker, then each later ticker is one request on it.
    assert constructions == ["RELIANCE.NS"]
    assert requested == [f"{symbol}.NS" for symbol in symbols]
    assert (result.processed, result.written, result.skipped) == (4, 3, 1)
    assert read_available_yahoo_symbols(db_path) == {"RELIANCE.NS", "INFY.NS", "TCS.NS"}


def test_update_builds_a_new_client_when_the_crumb_lapses(tmp_path, monkeypatch, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    symbols = [f"SYM{index:02d}" for index in range(5)]
    seed_consolidated(db_path, [_ticker_row(f"INE{index:03d}", symbol) for index, symbol in enumerate(symbols)])
    clients: list[_FakeTicker] = []
    requested: list[tuple[int, str]] = []

    class ExpiringTicker(_FakeTicker):
        """The first client's crumb lapses after two requests, as Yahoo's does during a long run."""

        def __init__(self, symbols: str) -> None:
            super().__init__(symbols)
            self.number = len(clients)
            self.requests = 0
            clients.append(self)

        @property
        def all_modules(self):
            self.requests += 1
            requested.append((self.number, self.symbol))
            if self.number == 0 and self.requests > 2:
                return {self.symbol: "Invalid Crumb"}
            return super().all_modules

    monkeypatch.setitem(sys.modules, "yahooquery", types.SimpleNamespace(Ticker=ExpiringTicker))
    progress = []

    result = YahooDataManager(db_path).update_data(exchange="BSE", progress=lambda *args: progress.append(args))

    # SYM02 meets the lapsed crumb, is retried once on a new client, and that client serves the rest.
    assert len(clients) == 2
    assert requested == [
        (0, "SYM00.BO"),
        (0, "SYM01.BO"),
        (0, "SYM02.BO"),
        (1, "SYM02.BO"),
        (1, "SYM03.BO"),
        (1, "SYM04.BO"),
    ]
    assert (result.processed, result.written, result.skipped) == (5, 5, 0)
    assert progress == [(index, 5, index, 0) for index in range(1, 6)]
    assert read_available_yahoo_symbols(db_path) == {f"{symbol}.BO" for symbol in symbols}


def test_is_auth_failure_matches_crumb_and_cookie_answers() -> None:
    assert yahoo.is_auth_failure("Invalid Crumb")
    assert yahoo.is_auth_failure("Invalid Cookie")
    assert yahoo.is_auth_failure("invalid crumb")
    assert not yahoo.is_auth_failure("User is unable to access this feature")
    assert not yahoo.is_auth_failure("Too Many Requests")
    assert not yahoo.is_auth_failure("HTTP 404 Not Found.  Please try again")
    assert not yahoo.is_auth_failure("RuntimeError('offline failure')")


def test_update_skips_fetch_exceptions(tmp_path, monkeypatch, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path, [_ticker_row("INE001", "BROKEN")])

    class RaisingTicker(_FakeTicker):
        @property
        def all_modules(self):
            raise RuntimeError("offline failure")

    fake_module = types.ModuleType("yahooquery")
    fake_module.Ticker = RaisingTicker
    monkeypatch.setitem(sys.modules, "yahooquery", fake_module)

    result = YahooDataManager(db_path).update_data()

    assert result.processed == 1
    assert result.written == 0
    assert result.skipped == 1


def test_update_commits_every_25_reports_progress_and_writes_nse(tmp_path, monkeypatch, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    symbols = [f"SYM{index:02d}" for index in range(51)]
    seed_consolidated(
        db_path,
        [_ticker_row(f"INE{index:03d}", symbol) for index, symbol in enumerate(symbols)],
    )

    constructions = []

    class CountingTicker(_FakeTicker):
        def __init__(self, symbols: str) -> None:
            super().__init__(symbols)
            constructions.append(symbols)

    fake_module = types.ModuleType("yahooquery")
    fake_module.Ticker = CountingTicker
    monkeypatch.setitem(sys.modules, "yahooquery", fake_module)

    commits = []
    real_connect = yahoo.connect

    class TrackingConnection:
        def __init__(self, path) -> None:
            self.connection = real_connect(path)

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def execute(self, *args, **kwargs):
            return self.connection.execute(*args, **kwargs)

        def commit(self) -> None:
            commits.append(True)
            self.connection.commit()

    monkeypatch.setattr(yahoo, "connect", TrackingConnection)
    progress = []

    result = YahooDataManager(db_path).update_data(exchange="NSE", progress=lambda *args: progress.append(args))

    assert result.processed == 51
    assert result.written == 51
    assert result.skipped == 0
    assert commits == [True, True]
    assert constructions == ["SYM00.NS"]
    assert len(progress) == 51
    assert progress[0] == (1, 51, 1, 0)
    assert progress[-1] == (51, 51, 51, 0)
    assert "SYM00.NS" in read_available_yahoo_symbols(db_path)
    with real_connect(db_path) as con:
        assert con.execute("SELECT DISTINCT exchange FROM yahoo_responses").fetchall() == [("NSE",)]


def test_update_rejects_empty_symbol_set(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path, [_ticker_row("INE001", "")])

    with pytest.raises(ValueError) as excinfo:
        YahooDataManager(db_path).update_data(dry_run=True)

    message = str(excinfo.value)
    assert "yq_bo" in message
    assert "stocky rebuild" in message


def test_update_aborts_after_five_consecutive_failures(tmp_path, monkeypatch, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(
        db_path,
        [_ticker_row(f"INE{index:03d}", f"SYM{index:02d}") for index in range(8)],
    )

    attempts = []
    constructions = []

    class RaisingTicker(_FakeTicker):
        def __init__(self, symbols: str) -> None:
            super().__init__(symbols)
            constructions.append(symbols)

        @property
        def all_modules(self):
            attempts.append(self.symbol)
            raise RuntimeError("offline failure")

    fake_module = types.ModuleType("yahooquery")
    fake_module.Ticker = RaisingTicker
    monkeypatch.setitem(sys.modules, "yahooquery", fake_module)

    progress = []
    with pytest.raises(yahoo.YahooFetchError) as excinfo:
        YahooDataManager(db_path).update_data(progress=lambda *args: progress.append(args))

    message = str(excinfo.value)
    assert len(attempts) == yahoo.CONSECUTIVE_FAILURE_LIMIT
    # A failure that is not about the crumb or cookie keeps the client.
    assert len(constructions) == 1
    # The aborting symbol raises before its progress call, so the callback runs
    # once for each of the four symbols that completed.
    assert len(progress) == yahoo.CONSECUTIVE_FAILURE_LIMIT - 1
    assert "--missing-only" in message
    assert "5 consecutive failures" in message
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_update_persists_rows_written_before_aborting(tmp_path, monkeypatch, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    good = ["RELIANCE", "INFY", "TCS"]
    broken = [f"BROKEN{index}" for index in range(5)]
    seed_consolidated(
        db_path,
        [_ticker_row(f"INE{index:03d}", symbol) for index, symbol in enumerate([*good, *broken])],
    )

    class PartlyRaisingTicker(_FakeTicker):
        @property
        def all_modules(self):
            if "BROKEN" in self.symbol:
                raise RuntimeError("offline failure")
            return {
                self.symbol: {
                    "quoteType": {"quoteType": "EQUITY", "longName": self.symbol},
                    "price": {"regularMarketPrice": 100},
                }
            }

    fake_module = types.ModuleType("yahooquery")
    fake_module.Ticker = PartlyRaisingTicker
    monkeypatch.setitem(sys.modules, "yahooquery", fake_module)

    with pytest.raises(yahoo.YahooFetchError):
        YahooDataManager(db_path).update_data(exchange="BSE")

    assert read_available_yahoo_symbols(db_path) == {f"{symbol}.BO" for symbol in good}


def _use_fake_yahooquery(monkeypatch, ticker=_FakeTicker) -> None:
    fake_module = types.ModuleType("yahooquery")
    fake_module.Ticker = ticker
    monkeypatch.setitem(sys.modules, "yahooquery", fake_module)


def _cached_rows(db_path) -> dict[str, tuple[object, ...]]:
    with connect(db_path) as con:
        rows = con.execute(
            "SELECT yahoo_symbol, symbol, exchange, response_json, fetched_at, source FROM yahoo_responses"
        )
        return {row[0]: row for row in rows}


def _seed_orphans_beside_listed(db_path, seed_consolidated) -> None:
    """Consolidated lists RELIANCE and INFY on both exchanges; two cached tickers are listed nowhere."""
    seed_consolidated(
        db_path, [_ticker_row("INE001", "RELIANCE"), _ticker_row("INE002", "INFY"), _ticker_row("INE003", "")]
    )
    for yahoo_symbol in ["INFY.NS", "INFY.BO", "RELIANCE.BO", "DELISTED.NS", "MERGED.BO"]:
        _seed_cached_response(db_path, yahoo_symbol)


def test_update_prunes_responses_whose_ticker_left_consolidated(tmp_path, monkeypatch, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    _seed_orphans_beside_listed(db_path, seed_consolidated)
    kept_before = {key: row for key, row in _cached_rows(db_path).items() if key not in {"DELISTED.NS", "MERGED.BO"}}
    _use_fake_yahooquery(monkeypatch)

    result = YahooDataManager(db_path).update_data(exchange="NSE", symbols=["RELIANCE"])

    assert (result.processed, result.written, result.pruned) == (1, 1, 2)
    after = _cached_rows(db_path)
    assert set(after) == {"RELIANCE.NS", *kept_before}
    # Rows of listed tickers that this run did not fetch keep every column, the other exchange's included.
    assert {key: after[key] for key in kept_before} == kept_before


def test_dry_run_counts_orphans_without_deleting(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    _seed_orphans_beside_listed(db_path, seed_consolidated)
    before = _cached_rows(db_path)

    result = YahooDataManager(db_path).update_data(exchange="BSE", dry_run=True)

    assert (result.processed, result.pruned, result.dry_run) == (2, 2, True)
    assert _cached_rows(db_path) == before


def test_dry_run_counts_no_orphans_without_a_response_table(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    assert YahooDataManager(db_path).update_data(dry_run=True).pruned == 0


def test_update_prunes_nothing_when_consolidated_is_empty(tmp_path, monkeypatch, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path, [])
    for yahoo_symbol in ["INFY.NS", "RELIANCE.BO"]:
        _seed_cached_response(db_path, yahoo_symbol)
    before = _cached_rows(db_path)
    _use_fake_yahooquery(monkeypatch)

    # A limit of 0 is the only update an empty table allows; every other run fails for want of tickers.
    assert YahooDataManager(db_path).update_data(exchange="NSE", limit=0, dry_run=True).pruned == 0
    assert YahooDataManager(db_path).update_data(exchange="NSE", limit=0).pruned == 0
    assert _cached_rows(db_path) == before


def test_update_keeps_an_exchanges_responses_when_its_column_lists_no_ticker(
    tmp_path, monkeypatch, seed_consolidated
) -> None:
    # A rebuild from legacy bhavcopies has no BSE trading symbol, so every yq_bo is empty.
    db_path = tmp_path / "stocky.db"
    rows = [(*_ticker_row("INE001", "RELIANCE")[:-1], None), (*_ticker_row("INE002", "INFY")[:-1], "")]
    seed_consolidated(db_path, rows)
    for yahoo_symbol in ["RELIANCE.NS", "DELISTED.NS", "RELIANCE.BO", "INFY.BO"]:
        _seed_cached_response(db_path, yahoo_symbol)
    _use_fake_yahooquery(monkeypatch)

    assert YahooDataManager(db_path).update_data(exchange="NSE", limit=0, dry_run=True).pruned == 1
    assert YahooDataManager(db_path).update_data(exchange="NSE", limit=0).pruned == 1
    assert set(_cached_rows(db_path)) == {"RELIANCE.NS", "RELIANCE.BO", "INFY.BO"}


@pytest.mark.parametrize(
    ("failure", "pruned", "warning"),
    [
        (
            sqlite3.OperationalError("database is locked"),
            0,
            "The update was saved, but removing the responses of tickers no longer in consolidated failed: "
            "database is locked",
        ),
        (
            OrphanVacuumError(2, sqlite3.OperationalError("database or disk is full")),
            2,
            "Removed 2 responses of tickers no longer in consolidated, but VACUUM failed, so the file has not "
            "shrunk yet: database or disk is full",
        ),
    ],
)
def test_a_failed_prune_after_a_finished_update_is_a_warning(
    tmp_path, monkeypatch, seed_consolidated, failure, pruned, warning
) -> None:
    db_path = tmp_path / "stocky.db"
    _seed_orphans_beside_listed(db_path, seed_consolidated)
    _use_fake_yahooquery(monkeypatch)

    def failing(db_path):
        raise failure

    monkeypatch.setattr(yahoo, "prune_orphan_yahoo_responses", failing)

    result = YahooDataManager(db_path).update_data(exchange="NSE", symbols=["RELIANCE"])

    assert (result.written, result.pruned, result.warnings) == (1, pruned, [warning])
    assert "RELIANCE.NS" in _cached_rows(db_path)


def test_aborted_update_prunes_nothing(tmp_path, monkeypatch, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    _seed_orphans_beside_listed(db_path, seed_consolidated)
    before = _cached_rows(db_path)

    class RaisingTicker(_FakeTicker):
        @property
        def all_modules(self):
            raise RuntimeError("offline failure")

    _use_fake_yahooquery(monkeypatch, RaisingTicker)
    monkeypatch.setattr(yahoo, "CONSECUTIVE_FAILURE_LIMIT", 2)

    with pytest.raises(yahoo.YahooFetchError):
        YahooDataManager(db_path).update_data(exchange="BSE")

    assert _cached_rows(db_path) == before


def test_pruning_vacuums_the_freed_pages_away(tmp_path, monkeypatch, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path, [_ticker_row("INE001", "RELIANCE")])
    initialize_database(db_path)
    with connect(db_path) as con:
        # Random bytes do not compress, so the orphan takes about 2 MB of pages.
        upsert_yahoo_response(
            con,
            yahoo_symbol="DELISTED.NS",
            symbol="DELISTED",
            exchange="NSE",
            response_json=os.urandom(2_000_000),
            source="test",
        )
    size_before = db_path.stat().st_size
    _use_fake_yahooquery(monkeypatch)

    result = YahooDataManager(db_path).update_data(exchange="NSE", limit=0)

    assert result.pruned == 1
    assert db_path.stat().st_size < size_before - 1_900_000
    with connect(db_path) as con:
        assert con.execute("PRAGMA freelist_count").fetchone() == (0,)
        assert con.execute("PRAGMA integrity_check").fetchone() == ("ok",)

    # A second run has nothing to prune and leaves the file as it is.
    size_after = db_path.stat().st_size
    assert YahooDataManager(db_path).update_data(exchange="NSE", limit=0).pruned == 0
    assert db_path.stat().st_size == size_after


def test_update_resets_failure_counter_after_a_success(tmp_path, monkeypatch, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(
        db_path,
        [_ticker_row(f"INE{index:03d}", f"SYM{index:02d}") for index in range(9)],
    )
    # Four failures, one success, four more failures: without the counter reset
    # the ninth symbol would be the fifth consecutive failure and abort the run.
    outcomes = iter([False, False, False, False, True, False, False, False, False])

    class FlakyTicker(_FakeTicker):
        @property
        def all_modules(self):
            if not next(outcomes):
                raise RuntimeError("temporary failure")
            return {
                self.symbol: {
                    "quoteType": {"quoteType": "EQUITY", "longName": self.symbol},
                    "price": {"regularMarketPrice": 100},
                }
            }

    fake_module = types.ModuleType("yahooquery")
    fake_module.Ticker = FlakyTicker
    monkeypatch.setitem(sys.modules, "yahooquery", fake_module)

    result = YahooDataManager(db_path).update_data(exchange="BSE")

    assert result.processed == 9
    assert result.written == 1
    assert result.skipped == 8


def test_update_resets_failure_counter_after_a_not_found(tmp_path, monkeypatch, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(
        db_path,
        [_ticker_row(f"INE{index:03d}", f"SYM{index:02d}") for index in range(9)],
    )
    # A symbol Yahoo does not know about is an answer, not a failure, so it
    # clears the counter the same way a successful fetch does.
    quote_not_found = iter([False, False, False, False, True, False, False, False, False])

    class FlakyTicker(_FakeTicker):
        @property
        def all_modules(self):
            if not next(quote_not_found):
                raise RuntimeError("temporary failure")
            return {self.symbol: f"Quote not found for symbol: {self.symbol}"}

    fake_module = types.ModuleType("yahooquery")
    fake_module.Ticker = FlakyTicker
    monkeypatch.setitem(sys.modules, "yahooquery", fake_module)

    result = YahooDataManager(db_path).update_data(exchange="BSE")

    assert result.processed == 9
    assert result.written == 0
    assert result.skipped == 9


def test_update_treats_error_payloads_as_failures(tmp_path, monkeypatch, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(
        db_path,
        [_ticker_row(f"INE{index:03d}", f"SYM{index:02d}") for index in range(8)],
    )

    class RateLimitedTicker(_FakeTicker):
        @property
        def all_modules(self):
            return {self.symbol: {"error": "Too Many Requests"}}

    fake_module = types.ModuleType("yahooquery")
    fake_module.Ticker = RateLimitedTicker
    monkeypatch.setitem(sys.modules, "yahooquery", fake_module)

    with pytest.raises(yahoo.YahooFetchError, match="Too Many Requests"):
        YahooDataManager(db_path).update_data(exchange="BSE")

    assert read_available_yahoo_symbols(db_path) == set()


def test_update_treats_missing_payloads_as_failures(tmp_path, monkeypatch, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(
        db_path,
        [_ticker_row(f"INE{index:03d}", f"SYM{index:02d}") for index in range(8)],
    )

    class UndecodableTicker(_FakeTicker):
        @property
        def all_modules(self):
            # yahooquery returns this for a response it cannot decode, so the
            # symbol has no payload at all.
            return {"error": "HTTP 404 Not Found.  Please try again"}

    fake_module = types.ModuleType("yahooquery")
    fake_module.Ticker = UndecodableTicker
    monkeypatch.setitem(sys.modules, "yahooquery", fake_module)

    with pytest.raises(yahoo.YahooFetchError, match="HTTP 404 Not Found"):
        YahooDataManager(db_path).update_data(exchange="BSE")


def test_update_treats_unexpected_payload_text_as_failure(tmp_path, monkeypatch, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(
        db_path,
        [_ticker_row(f"INE{index:03d}", f"SYM{index:02d}") for index in range(8)],
    )

    constructions = []
    requests = []

    class ApiErrorTicker(_FakeTicker):
        def __init__(self, symbols: str) -> None:
            super().__init__(symbols)
            constructions.append(symbols)

        @property
        def all_modules(self):
            requests.append(self.symbol)
            return {self.symbol: "Invalid Crumb"}

    fake_module = types.ModuleType("yahooquery")
    fake_module.Ticker = ApiErrorTicker
    monkeypatch.setitem(sys.modules, "yahooquery", fake_module)

    with pytest.raises(yahoo.YahooFetchError, match="Invalid Crumb"):
        YahooDataManager(db_path).update_data(exchange="BSE")

    # Each ticker is retried once on a new client and then counts as one failure, so the run
    # still aborts at the fifth ticker rather than retrying without end.
    limit = yahoo.CONSECUTIVE_FAILURE_LIMIT
    assert requests == [f"SYM{index:02d}.BO" for index in range(limit) for _ in range(2)]
    assert len(constructions) == limit + 1


def test_classify_payload_maps_yahooquery_outcomes() -> None:
    listed = {"quoteType": {"quoteType": "EQUITY", "longName": "Reliance Industries Limited"}}
    assert yahoo.classify_payload("RELIANCE.BO", {}, listed)[0] == "success"
    # Yahoo answers with no named, listed security hold no data for the share.
    assert yahoo.classify_payload("PAVNAIND.NS", {}, {"quoteType": {"quoteType": "NONE"}}) == ("not_found", "")
    assert yahoo.classify_payload(
        "ENERGY.BO", {}, {"quoteType": {"quoteType": "INDEX", "longName": "S&P BSE ENERGY"}}
    ) == ("not_found", "")
    assert yahoo.classify_payload("BHAGERIA.BO", {}, {"quoteType": {"quoteType": "MUTUALFUND"}}) == ("not_found", "")
    assert yahoo.classify_payload("RELIANCE.BO", {}, "Quote not found for symbol: RELIANCE.BO") == ("not_found", "")
    # Yahoo has also used this older wording, so both must classify the same way.
    assert yahoo.classify_payload("RELIANCE.BO", {}, "Quote not found for ticker symbol: RELIANCE.BO") == (
        "not_found",
        "",
    )
    assert yahoo.classify_payload("RELIANCE.BO", {}, 'For input string: "42525.0000000001"')[0] == "failure"
    assert yahoo.classify_payload("RELIANCE.BO", {}, {"error": "Too Many Requests"}) == (
        "failure",
        "Too Many Requests",
    )
    assert yahoo.classify_payload("RELIANCE.BO", {"error": "HTTP 404"}, None) == ("failure", "HTTP 404")
    assert yahoo.classify_payload("RELIANCE.BO", {}, None)[0] == "failure"


def test_update_allows_an_empty_result_for_limit_zero(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = YahooDataManager(db_path).update_data(limit=0, dry_run=True)

    assert result.processed == 0


def test_ticker_column_rejects_unknown_exchange() -> None:
    with pytest.raises(ValueError, match="Unsupported exchange"):
        ticker_column("MCX")
