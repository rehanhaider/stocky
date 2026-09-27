import sqlite3

import pandas as pd
import pytest

from stocky.database import (
    CONSOLIDATED_TABLE,
    connect,
    encode_response_json,
    initialize_database,
    upsert_yahoo_response,
)
from stocky.enrich import (
    ENRICHED_VIEW,
    FIELD_COLUMNS,
    YAHOO_FIELDS_TABLE,
    extract_fields,
    materialize_yahoo_fields,
    read_yahoo_fields,
    screen_instruments,
)

FULL_PAYLOAD = {
    "price": {
        "longName": "Reliance Industries Limited",
        "shortName": "RELIANCE INDUSTRIES LTD.",
        "quoteType": "EQUITY",
        "currency": "INR",
        "marketCap": 13_486_949_138_432,
        "regularMarketPrice": 1994.5,
        "regularMarketChangePercent": -0.0146,
        "regularMarketVolume": 9_054_509,
        "regularMarketTime": "2021-04-30 15:30:00",
        "regularMarketPreviousClose": 2024.05,
    },
    "summaryDetail": {
        "fiftyTwoWeekHigh": 2369.35,
        "fiftyTwoWeekLow": 1089.67,
        "trailingPE": 30.5,
        "dividendYield": 0.0032,
    },
    "assetProfile": {"sector": "Energy", "industry": "Oil & Gas Refining & Marketing"},
    "defaultKeyStatistics": {
        "forwardPE": 19.58,
        "priceToBook": 2.1,
        "beta": 1.01,
        "trailingEps": 65.39,
        "bookValue": 951.52,
        "enterpriseValue": 14_345_429_843_968,
        "sharesOutstanding": 6_762_070_016,
    },
    "calendarEvents": {"earnings": {"earningsDate": ["2021-07-23 05:30:S", "2021-07-19 05:30:S"]}},
}


def _seed_responses(db_path, responses, fetched_at: str = "2026-05-31T00:00:00+00:00") -> None:
    initialize_database(db_path)
    with connect(db_path) as con:
        for yahoo_symbol, payload in responses.items():
            symbol, suffix = yahoo_symbol.rsplit(".", 1)
            upsert_yahoo_response(
                con,
                yahoo_symbol=yahoo_symbol,
                symbol=symbol,
                exchange={"NS": "NSE", "BO": "BSE"}[suffix],
                response_json=encode_response_json({yahoo_symbol: payload}),
                source="test",
                fetched_at=fetched_at,
            )


def _cap(crore: float) -> dict:
    return {"price": {"marketCap": int(crore * 10_000_000)}}


def _seed_screen_db(db_path, seed_consolidated) -> None:
    seed_consolidated(db_path)
    _seed_responses(
        db_path,
        {
            "RELIANCE.NS": FULL_PAYLOAD,
            "RELIANCE.BO": _cap(1_100_000),
            "INFY.NS": {**_cap(500_000), "summaryProfile": {"sector": "Technology", "industry": "IT Services"}},
            "INFY.BO": "Quote not found for symbol: INFY.BO",
            "20MICRONS.BO": _cap(500),
        },
    )


def test_extract_fields_reads_every_group() -> None:
    fields = extract_fields(FULL_PAYLOAD)

    assert fields is not None
    assert fields["name"] == "Reliance Industries Limited"
    assert fields["market_cap"] == 13_486_949_138_432
    assert (fields["sector"], fields["industry"]) == ("Energy", "Oil & Gas Refining & Marketing")
    assert fields["regular_market_price"] == 1994.5
    assert fields["regular_market_time"] == "2021-04-30 15:30:00"
    assert fields["trailing_pe"] == 30.5
    assert fields["price_to_book"] == 2.1
    assert fields["shares_outstanding"] == 6_762_070_016
    assert (fields["earnings_date"], fields["earnings_date_end"]) == ("2021-07-19", "2021-07-23")
    assert set(fields) == set(FIELD_COLUMNS)


def test_extract_fields_tolerates_missing_modules() -> None:
    fields = extract_fields({"price": {"shortName": "TINY LTD"}})

    assert fields is not None
    assert fields["name"] == "TINY LTD"
    assert all(value is None for column, value in fields.items() if column != "name")


def test_extract_fields_falls_back_between_modules() -> None:
    fields = extract_fields(
        {
            "summaryDetail": {"marketCap": 42, "beta": 0.8},
            "summaryProfile": {"sector": "Utilities", "industry": "Power"},
            "calendarEvents": {"earnings": {"earningsDate": ["2020-08-13 05:30:S"]}},
        }
    )

    assert fields is not None
    assert (fields["market_cap"], fields["beta"]) == (42, 0.8)
    assert (fields["sector"], fields["industry"]) == ("Utilities", "Power")
    assert (fields["earnings_date"], fields["earnings_date_end"]) == ("2020-08-13", None)


@pytest.mark.parametrize(
    ("module", "value"),
    [("marketCap", {}), ("marketCap", "Infinity"), ("marketCap", True), ("marketCap", float("inf"))],
)
def test_extract_fields_drops_values_that_are_not_numbers(module: str, value: object) -> None:
    fields = extract_fields({"price": {module: value}})

    assert fields is not None
    assert fields["market_cap"] is None


def test_extract_fields_ignores_unparseable_earnings_dates() -> None:
    fields = extract_fields({"calendarEvents": {"earnings": {"earningsDate": ["soon", 1600000000]}}})

    assert fields is not None
    assert fields["earnings_date"] is None


@pytest.mark.parametrize("payload", ["Quote not found for symbol: X.BO", None, ["price"]])
def test_extract_fields_returns_none_without_module_data(payload: object) -> None:
    assert extract_fields(payload) is None


def test_materialize_writes_table_and_view(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    _seed_screen_db(db_path, seed_consolidated)

    result = materialize_yahoo_fields(db_path)

    assert (result.extracted, result.skipped) == (4, 1)
    with sqlite3.connect(db_path) as con:
        assert con.execute(f"SELECT COUNT(*) FROM {YAHOO_FIELDS_TABLE}").fetchone()[0] == 5
        rows = con.execute(
            f"SELECT isin, yahoo_symbol, exchange, sector FROM {ENRICHED_VIEW} ORDER BY yahoo_symbol"
        ).fetchall()
    # Only NSE responses join, on the exact NSE symbol; .BO tickers cannot be tied to an ISIN.
    assert rows == [
        ("INE009A01021", "INFY.NS", "NSE", "Technology"),
        ("INE002A01018", "RELIANCE.NS", "NSE", "Energy"),
    ]


def test_materialize_replaces_previous_extraction(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    _seed_screen_db(db_path, seed_consolidated)
    materialize_yahoo_fields(db_path)
    _seed_responses(db_path, {"RELIANCE.NS": _cap(7)})

    materialize_yahoo_fields(db_path)

    with sqlite3.connect(db_path) as con:
        market_cap, sector = con.execute(
            f"SELECT market_cap, sector FROM {YAHOO_FIELDS_TABLE} WHERE yahoo_symbol = 'RELIANCE.NS'"
        ).fetchone()
    assert (market_cap, sector) == (70_000_000, None)


def test_materialize_requires_yahoo_responses(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    with pytest.raises(RuntimeError, match="yahoo_responses"):
        materialize_yahoo_fields(db_path)


def test_materialize_requires_database(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="Database not found"):
        materialize_yahoo_fields(tmp_path / "missing.db")


def test_view_survives_consolidated_rebuild(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    _seed_screen_db(db_path, seed_consolidated)
    materialize_yahoo_fields(db_path)

    # Rebuild replaces consolidated through pandas, which drops and recreates the table.
    frame = pd.DataFrame(
        {"ins_type": ["equity"], "zd_symbol": ["INFY"], "yq_symbol": ["INFY"], "nse_symbol": ["INFY"]},
        index=pd.Index(["INE009A01021"], name="isin"),
    ).assign(bse_sc_code="500209", bse_sc_name="INFOSYS LTD")
    with connect(db_path) as con:
        frame.to_sql(CONSOLIDATED_TABLE, con, if_exists="replace", index=True, index_label="isin")

    with sqlite3.connect(db_path) as con:
        rows = con.execute(f"SELECT yahoo_symbol FROM {ENRICHED_VIEW}").fetchall()
    assert rows == [("INFY.NS",)]


def test_read_yahoo_fields_matches_bare_symbol_on_both_exchanges(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    _seed_screen_db(db_path, seed_consolidated)

    results = read_yahoo_fields("infy", db_path)

    assert [(r["yahoo_symbol"], r["available"]) for r in results] == [("INFY.BO", False), ("INFY.NS", True)]
    assert results[0]["market_cap"] is None
    assert results[1]["sector"] == "Technology"


def test_read_yahoo_fields_matches_full_ticker(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    _seed_screen_db(db_path, seed_consolidated)

    results = read_yahoo_fields("RELIANCE.NS", db_path)

    assert [r["yahoo_symbol"] for r in results] == ["RELIANCE.NS"]
    assert results[0]["earnings_date"] == "2021-07-19"


def test_read_yahoo_fields_rejects_blank_symbol(tmp_path) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        read_yahoo_fields("  ", tmp_path / "stocky.db")


def test_screen_filters_by_market_cap_in_crore_and_exchange(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    _seed_screen_db(db_path, seed_consolidated)
    materialize_yahoo_fields(db_path)

    result = screen_instruments(db_path, market_cap_gt=600_000, exchange="nse")

    assert result.total == 1
    assert [row["yahoo_symbol"] for row in result.rows] == ["RELIANCE.NS"]
    assert result.stale is False


def test_screen_orders_largest_first_and_limits(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    _seed_screen_db(db_path, seed_consolidated)
    materialize_yahoo_fields(db_path)

    result = screen_instruments(db_path, market_cap_gt=100_000, limit=1)

    assert result.total == 2
    assert [row["yahoo_symbol"] for row in result.rows] == ["RELIANCE.NS"]


def test_screen_filters_by_sector_and_industry_case_insensitively(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    _seed_screen_db(db_path, seed_consolidated)
    materialize_yahoo_fields(db_path)

    by_sector = screen_instruments(db_path, sector="technology")
    by_industry = screen_instruments(db_path, industry="OIL & GAS REFINING & MARKETING")

    assert [row["isin"] for row in by_sector.rows] == ["INE009A01021"]
    assert [row["yahoo_symbol"] for row in by_industry.rows] == ["RELIANCE.NS"]


def test_screen_flags_responses_newer_than_extraction(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    _seed_screen_db(db_path, seed_consolidated)
    materialize_yahoo_fields(db_path)
    _seed_responses(db_path, {"RELIANCE.NS": _cap(7)}, fetched_at="2026-09-01T00:00:00+00:00")

    assert screen_instruments(db_path).stale is True


def test_screen_requires_extraction(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    _seed_screen_db(db_path, seed_consolidated)

    with pytest.raises(RuntimeError, match="stocky yahoo extract"):
        screen_instruments(db_path)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [({"exchange": "LSE"}, "Unsupported exchange"), ({"exchange": "BSE"}, "NSE quotes only"), ({"limit": 0}, "Limit")],
)
def test_screen_rejects_bad_arguments(tmp_path, seed_consolidated, kwargs, message) -> None:
    db_path = tmp_path / "stocky.db"
    _seed_screen_db(db_path, seed_consolidated)
    materialize_yahoo_fields(db_path)

    with pytest.raises(ValueError, match=message):
        screen_instruments(db_path, **kwargs)


def test_extract_fields_drops_integers_outside_sqlite_range() -> None:
    fields = extract_fields({"price": {"marketCap": 2**63}})

    assert fields is not None
    assert fields["market_cap"] is None


def test_failed_materialize_keeps_previous_snapshot(tmp_path, seed_consolidated, monkeypatch) -> None:
    db_path = tmp_path / "stocky.db"
    _seed_screen_db(db_path, seed_consolidated)
    materialize_yahoo_fields(db_path)

    import stocky.enrich as enrich

    calls = 0
    real_extract = enrich.extract_fields

    def failing_extract(payload):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("corrupt blob")
        return real_extract(payload)

    monkeypatch.setattr(enrich, "extract_fields", failing_extract)
    with pytest.raises(RuntimeError, match="corrupt blob"):
        materialize_yahoo_fields(db_path)

    with sqlite3.connect(db_path) as con:
        assert con.execute(f"SELECT COUNT(*) FROM {YAHOO_FIELDS_TABLE}").fetchone()[0] == 5
        assert con.execute(f"SELECT COUNT(*) FROM {ENRICHED_VIEW}").fetchone()[0] == 2


def test_view_does_not_attach_bse_ticker_of_another_company(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    # Globe Textiles trades as GLOBE on NSE, but Yahoo's GLOBE.BO is a different company.
    seed_consolidated(db_path, [("INE581X01021", "equity", "GLOBE", "GLOBE", "GLOBE", "543253", "GLOBE TEXTILES")])
    _seed_responses(
        db_path,
        {
            "GLOBE.NS": {"price": {"longName": "Globe Textiles (India) Limited", "marketCap": 493_225_088}},
            "GLOBE.BO": {"price": {"longName": "Confidence Futuristic Energetech Limited", "marketCap": 10**14}},
        },
    )
    materialize_yahoo_fields(db_path)

    result = screen_instruments(db_path)

    assert [(row["yahoo_symbol"], row["name"]) for row in result.rows] == [
        ("GLOBE.NS", "Globe Textiles (India) Limited")
    ]
