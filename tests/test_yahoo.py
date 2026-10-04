import sys
import types
from datetime import UTC, datetime, timedelta

import pytest

import stocky.yahoo as yahoo
from stocky.database import (
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
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

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
        def __init__(self, symbol: str) -> None:
            super().__init__(symbol)
            requested.append(symbol)

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

    class CurrentYahoo:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

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


def test_update_skips_fetch_exceptions(tmp_path, monkeypatch, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path, [_ticker_row("INE001", "BROKEN")])

    class RaisingTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

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

    fake_module = types.ModuleType("yahooquery")
    fake_module.Ticker = _FakeTicker
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

    class RaisingTicker:
        def __init__(self, symbol: str) -> None:
            attempts.append(symbol)

        @property
        def all_modules(self):
            raise RuntimeError("offline failure")

    fake_module = types.ModuleType("yahooquery")
    fake_module.Ticker = RaisingTicker
    monkeypatch.setitem(sys.modules, "yahooquery", fake_module)

    progress = []
    with pytest.raises(yahoo.YahooFetchError) as excinfo:
        YahooDataManager(db_path).update_data(progress=lambda *args: progress.append(args))

    message = str(excinfo.value)
    assert len(attempts) == yahoo.CONSECUTIVE_FAILURE_LIMIT
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


def test_update_resets_failure_counter_after_a_success(tmp_path, monkeypatch, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(
        db_path,
        [_ticker_row(f"INE{index:03d}", f"SYM{index:02d}") for index in range(9)],
    )
    # Four failures, one success, four more failures: without the counter reset
    # the ninth symbol would be the fifth consecutive failure and abort the run.
    outcomes = iter([False, False, False, False, True, False, False, False, False])

    class FlakyTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol
            self.succeeds = next(outcomes)

        @property
        def all_modules(self):
            if not self.succeeds:
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

    class FlakyTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol
            self.not_found = next(quote_not_found)

        @property
        def all_modules(self):
            if not self.not_found:
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

    class RateLimitedTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

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

    class UndecodableTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

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

    class ApiErrorTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        @property
        def all_modules(self):
            return {self.symbol: "Invalid Crumb"}

    fake_module = types.ModuleType("yahooquery")
    fake_module.Ticker = ApiErrorTicker
    monkeypatch.setitem(sys.modules, "yahooquery", fake_module)

    with pytest.raises(yahoo.YahooFetchError, match="Invalid Crumb"):
        YahooDataManager(db_path).update_data(exchange="BSE")


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
