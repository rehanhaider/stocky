import json
import sys

import pytest

from stocky.database import connect, encode_response_json, initialize_database, upsert_yahoo_response
from stocky.enrich import (
    CRORE,
    ENRICHED_COLUMNS,
    YahooFields,
    export_enriched,
    extract_fields,
    lookup_fields,
    read_enriched,
    screen_instruments,
)


def _payload(
    name: str,
    *,
    market_cap: object = None,
    price: object = 100.0,
    sector: str | None = None,
    industry: str | None = None,
    quote_type: str = "EQUITY",
) -> dict:
    payload: dict = {
        "quoteType": {"quoteType": quote_type, "longName": name, "shortName": name.upper()},
        "price": {"currency": "INR", "marketCap": market_cap, "regularMarketPrice": price},
    }
    if sector is not None or industry is not None:
        payload["assetProfile"] = {"sector": sector, "industry": industry}
    return payload


def _seed_responses(db_path, responses: dict[str, object]) -> None:
    """Cache each ticker's payload the way 'yahoo update' writes it: the full answer keyed by ticker."""
    initialize_database(db_path)
    with connect(db_path) as con:
        for ticker, payload in responses.items():
            upsert_yahoo_response(
                con,
                yahoo_symbol=ticker,
                symbol=ticker.rsplit(".", 1)[0],
                exchange="NSE" if ticker.endswith(".NS") else "BSE",
                response_json=encode_response_json({ticker: payload}),
                source="test",
                fetched_at="2026-10-01T00:00:00+00:00",
            )


# GLOBE.NS is Globe Textiles; GLOBE.BO is Confidence Futuristic, a different company with its own ISIN.
# Matching on the bare symbol "GLOBE" would attach one company's Yahoo data to the other.
GLOBE_ROWS = (
    ("INE581X01010", "equity", "GLOBE", None, None, None, "GLOBE", None, "GLOBE.NS", None),
    ("INE07JQ01015", "equity", None, "GLOBE", "540266", "CONFIDENCE FUTURISTIC", None, "GLOBE", None, "GLOBE.BO"),
    (
        "INE002A01018",
        "equity",
        "RELIANCE",
        "RELIANCE",
        "500325",
        "RELIANCE INDUSTRIES",
        "RELIANCE",
        "RELIANCE",
        "RELIANCE.NS",
        "RELIANCE.BO",
    ),
)
GLOBE_RESPONSES = {
    "GLOBE.NS": _payload("Globe Textiles (India) Limited", market_cap=150 * CRORE, sector="Consumer Cyclical"),
    "GLOBE.BO": _payload("Confidence Futuristic Energetech Limited", market_cap=400 * CRORE, sector="Energy"),
    "RELIANCE.NS": _payload("Reliance Industries Limited", market_cap=1_348_695 * CRORE, sector="Energy"),
    "RELIANCE.BO": _payload("Reliance Industries Limited", market_cap=1_182_543 * CRORE),
}


@pytest.fixture
def globe_db(tmp_path, seed_consolidated):
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path, GLOBE_ROWS)
    _seed_responses(db_path, GLOBE_RESPONSES)
    return db_path


def test_extract_fields_reads_every_target_field_from_a_full_payload() -> None:
    payload = {
        "quoteType": {"quoteType": "EQUITY", "longName": "Reliance Industries Limited", "shortName": "RELIANCE"},
        "price": {
            "currency": "INR",
            "marketCap": 13_486_950_000_000,
            "regularMarketPrice": 1994.5,
            "regularMarketVolume": 9_054_509,
            "regularMarketTime": "2021-04-30 15:30:00",
        },
        "summaryDetail": {
            "previousClose": 2024.05,
            "fiftyTwoWeekHigh": 2369.35,
            "fiftyTwoWeekLow": 1089.67,
            "trailingPE": 30.5,
            "dividendYield": 0.0032,
        },
        "defaultKeyStatistics": {
            "trailingEps": 65.39,
            "bookValue": 951.519,
            "priceToBook": 2.096,
            "beta": 1.01,
            "sharesOutstanding": 6_762_070_016,
            "enterpriseValue": 14_345_429_843_968,
        },
        "assetProfile": {"sector": "Energy", "industry": "Oil & Gas Refining & Marketing"},
        "calendarEvents": {"earnings": {"earningsDate": ["2021-07-23 05:30:S", "2021-07-19 05:30:S"]}},
    }

    assert extract_fields(payload) == YahooFields(
        name="Reliance Industries Limited",
        quote_type="EQUITY",
        currency="INR",
        market_cap=13_486_950_000_000,
        sector="Energy",
        industry="Oil & Gas Refining & Marketing",
        price=1994.5,
        previous_close=2024.05,
        volume=9_054_509,
        market_time="2021-04-30 15:30:00",
        fifty_two_week_high=2369.35,
        fifty_two_week_low=1089.67,
        trailing_pe=30.5,
        trailing_eps=65.39,
        book_value=951.519,
        price_to_book=2.096,
        dividend_yield=0.0032,
        beta=1.01,
        shares_outstanding=6_762_070_016,
        enterprise_value=14_345_429_843_968,
        earnings_date="2021-07-19",
        earnings_date_end="2021-07-23",
    )


def test_extract_fields_leaves_the_fields_of_missing_modules_empty() -> None:
    # About two in five cached BSE responses carry quoteType, price and summaryDetail but no
    # assetProfile, summaryProfile, defaultKeyStatistics or calendarEvents.
    fields = extract_fields(
        {
            "quoteType": {"quoteType": "EQUITY", "shortName": "SMALLCO"},
            "price": {"currency": "INR", "regularMarketPrice": 12.5},
        }
    )

    assert fields.name == "SMALLCO"
    assert fields.price == 12.5
    assert fields.market_cap is None
    assert fields.sector is None
    assert fields.industry is None
    assert fields.trailing_pe is None
    assert fields.trailing_eps is None
    assert fields.earnings_date is None
    assert fields.earnings_date_end is None


@pytest.mark.parametrize("payload", [None, "Quote not found for symbol: X.BO", [], {}])
def test_extract_fields_returns_empty_fields_for_a_payload_without_modules(payload) -> None:
    assert extract_fields(payload) == YahooFields()


def test_extract_fields_drops_values_of_the_wrong_shape() -> None:
    fields = extract_fields(
        {
            "quoteType": "EQUITY",
            "price": {"marketCap": {}, "regularMarketPrice": True, "currency": ""},
            "summaryDetail": {"trailingPE": "Infinity", "fiftyTwoWeekHigh": float("nan")},
            "defaultKeyStatistics": [],
            "calendarEvents": {"earnings": {"earningsDate": "2021-07-19 05:30:S"}},
        }
    )

    assert fields == YahooFields()


def test_extract_fields_falls_back_to_the_second_module_that_carries_a_value() -> None:
    fields = extract_fields(
        {
            "price": {"marketCap": None},
            "summaryDetail": {"marketCap": 4_200_000_000, "currency": "INR"},
            "assetProfile": {"sector": ""},
            "summaryProfile": {"sector": "Industrials", "industry": "Conglomerates"},
        }
    )

    assert fields.market_cap == 4_200_000_000
    assert fields.currency == "INR"
    assert fields.sector == "Industrials"
    assert fields.industry == "Conglomerates"


def test_extract_fields_keeps_only_well_formed_earnings_dates() -> None:
    fields = extract_fields({"calendarEvents": {"earnings": {"earningsDate": ["2020-08-13 05:30:S", 0, "soon"]}}})

    assert fields.earnings_date == "2020-08-13"
    assert fields.earnings_date_end == "2020-08-13"


def test_read_enriched_attaches_each_exchange_response_to_its_own_instrument(globe_db) -> None:
    rows = {(row.isin, row.exchange): row for row in read_enriched(globe_db)}

    # Each GLOBE company has a row only on the exchange that lists it, with that ticker's data.
    assert set(rows) == {
        ("INE581X01010", "NSE"),
        ("INE07JQ01015", "BSE"),
        ("INE002A01018", "NSE"),
        ("INE002A01018", "BSE"),
    }
    textiles = rows[("INE581X01010", "NSE")]
    assert textiles.yahoo_ticker == "GLOBE.NS"
    assert textiles.fields.name == "Globe Textiles (India) Limited"
    assert textiles.fields.market_cap == 150 * CRORE

    futuristic = rows[("INE07JQ01015", "BSE")]
    assert futuristic.yahoo_ticker == "GLOBE.BO"
    assert futuristic.fields.name == "Confidence Futuristic Energetech Limited"
    assert futuristic.fields.market_cap == 400 * CRORE

    # A dual-listed instrument keeps each exchange's own figures.
    assert rows[("INE002A01018", "NSE")].fields.market_cap == 1_348_695 * CRORE
    assert rows[("INE002A01018", "BSE")].fields.market_cap == 1_182_543 * CRORE


def test_read_enriched_marks_missing_and_unusable_responses(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(
        db_path,
        (
            ("INE000A00001", "equity", "OK", None, None, None, None, None, "OK.NS", None),
            ("INE000A00002", "equity", "GONE", None, None, None, None, None, "GONE.NS", None),
            ("INE000A00003", "equity", "IDX", None, None, None, None, None, "IDX.NS", None),
            ("INE000A00004", "equity", "NEVER", None, None, None, None, None, "NEVER.NS", None),
        ),
    )
    _seed_responses(
        db_path,
        {
            "OK.NS": _payload("Ok Limited", market_cap=10 * CRORE),
            "GONE.NS": "Quote not found for symbol: GONE.NS",
            "IDX.NS": _payload("Some Index", market_cap=10 * CRORE, quote_type="INDEX"),
        },
    )

    with connect(db_path) as con:
        con.execute("INSERT INTO yahoo_responses VALUES ('BAD.NS', 'BAD', 'NSE', x'00ff', '2026-10-01', 'test')")
        con.execute("INSERT INTO consolidated (isin, ins_type, yq_ns) VALUES ('INE000A00005', 'equity', 'BAD.NS')")
        truncated = encode_response_json({"CUT.NS": _payload("Cut Limited")})[:-8]
        con.execute(
            "INSERT INTO yahoo_responses VALUES ('CUT.NS', 'CUT', 'NSE', ?, '2026-10-01', 'test')", (truncated,)
        )
        con.execute("INSERT INTO consolidated (isin, ins_type, yq_ns) VALUES ('INE000A00006', 'equity', 'CUT.NS')")

    rows = {row.yahoo_ticker: row for row in read_enriched(db_path)}

    assert rows["OK.NS"].yahoo_status == "ok"
    assert rows["BAD.NS"].yahoo_status == "unusable"
    assert rows["CUT.NS"].yahoo_status == "unusable"
    assert rows["OK.NS"].fetched_at == "2026-10-01T00:00:00+00:00"
    assert rows["GONE.NS"].yahoo_status == "unusable"
    # An index answer carries the index's figures, not the share's, so none of them are attached.
    assert rows["IDX.NS"].yahoo_status == "unusable"
    assert rows["IDX.NS"].fields == YahooFields()
    assert rows["NEVER.NS"].yahoo_status == "missing"
    assert rows["NEVER.NS"].fetched_at is None


def test_read_enriched_works_before_any_yahoo_response_is_cached(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    rows = read_enriched(db_path)

    assert {row.yahoo_ticker for row in rows} == {"RELIANCE.NS", "RELIANCE.BO", "INFY.NS", "INFY.BO"}
    assert {row.yahoo_status for row in rows} == {"missing"}


def test_read_enriched_narrows_to_one_exchange(globe_db) -> None:
    assert {row.yahoo_ticker for row in read_enriched(globe_db, exchange="bse")} == {"GLOBE.BO", "RELIANCE.BO"}


def test_read_enriched_rejects_an_unknown_exchange(globe_db) -> None:
    with pytest.raises(ValueError, match="Unsupported exchange"):
        read_enriched(globe_db, exchange="MCX")


def test_read_enriched_rejects_a_missing_database(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="Database not found"):
        read_enriched(tmp_path / "missing.db")


def test_lookup_fields_returns_every_instrument_an_identifier_names(globe_db) -> None:
    # "GLOBE" is the NSE symbol of one company and the BSE symbol of another; both are shown, each
    # with its own exchange's data.
    rows = lookup_fields("GLOBE", globe_db)

    assert [(row.isin, row.yahoo_ticker, row.fields.name) for row in rows] == [
        ("INE07JQ01015", "GLOBE.BO", "Confidence Futuristic Energetech Limited"),
        ("INE581X01010", "GLOBE.NS", "Globe Textiles (India) Limited"),
    ]
    assert [row.yahoo_ticker for row in lookup_fields("INE002A01018", globe_db)] == ["RELIANCE.BO", "RELIANCE.NS"]
    assert lookup_fields("NOPE", globe_db) == []


def test_screen_filters_on_exchange_market_cap_in_crore_and_sector(globe_db) -> None:
    result = screen_instruments(globe_db, exchange="BSE", market_cap_gt=300)

    assert [row.yahoo_ticker for row in result.rows] == ["RELIANCE.BO", "GLOBE.BO"]
    assert result.total == 2

    result = screen_instruments(globe_db, market_cap_gt=100, market_cap_lt=1000)
    assert [row.yahoo_ticker for row in result.rows] == ["GLOBE.BO", "GLOBE.NS"]

    # Bounds are exclusive.
    assert screen_instruments(globe_db, market_cap_gt=400, market_cap_lt=1000).total == 0

    result = screen_instruments(globe_db, sector="ENERGY")
    assert [row.yahoo_ticker for row in result.rows] == ["RELIANCE.NS", "GLOBE.BO"]


def test_screen_matches_an_industry_substring(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)
    _seed_responses(
        db_path,
        {
            "INFY.NS": _payload("Infosys", market_cap=5 * CRORE, industry="Information Technology Services"),
            "RELIANCE.NS": _payload("Reliance", market_cap=9 * CRORE, industry="Oil & Gas Refining & Marketing"),
        },
    )

    assert [row.yahoo_ticker for row in screen_instruments(db_path, industry="technology").rows] == ["INFY.NS"]


def test_screen_excludes_listings_without_usable_data_and_sorts_missing_market_caps_last(
    tmp_path, seed_consolidated
) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)
    _seed_responses(
        db_path,
        {
            "INFY.NS": _payload("Infosys", market_cap=None),
            "INFY.BO": _payload("Infosys", market_cap=5 * CRORE),
            "RELIANCE.NS": "Quote not found for symbol: RELIANCE.NS",
        },
    )

    result = screen_instruments(db_path)

    # RELIANCE.BO has nothing cached and RELIANCE.NS holds an error, so neither is screenable.
    assert [row.yahoo_ticker for row in result.rows] == ["INFY.BO", "INFY.NS"]
    # A listing without a market cap fails any market cap bound.
    assert [row.yahoo_ticker for row in screen_instruments(db_path, market_cap_lt=100).rows] == ["INFY.BO"]


def test_screen_limits_the_rows_but_counts_every_match(globe_db) -> None:
    result = screen_instruments(globe_db, limit=1)

    assert [row.yahoo_ticker for row in result.rows] == ["RELIANCE.NS"]
    assert result.total == 4

    with pytest.raises(ValueError, match="at least 1"):
        screen_instruments(globe_db, limit=0)


def test_screen_never_imports_yahooquery(globe_db, monkeypatch) -> None:
    # A None entry makes any import of the module fail, so a screen that tried to call Yahoo would raise.
    monkeypatch.setitem(sys.modules, "yahooquery", None)

    assert screen_instruments(globe_db).total == 4
    assert lookup_fields("GLOBE", globe_db)


def test_export_enriched_writes_one_row_per_listing_as_csv_and_json(globe_db, tmp_path) -> None:
    csv_path = tmp_path / "enriched.csv"
    result = export_enriched(globe_db, output=csv_path, format=None)

    assert (result.rows, result.format) == (4, "csv")
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ",".join(ENRICHED_COLUMNS)
    assert len(lines) == 5

    json_path = tmp_path / "enriched.json"
    export_enriched(globe_db, output=json_path, format=None, exchange="NSE")
    records = json.loads(json_path.read_text(encoding="utf-8"))
    assert [(record["yahoo_ticker"], record["market_cap"]) for record in records] == [
        ("RELIANCE.NS", 1_348_695 * CRORE),
        ("GLOBE.NS", 150 * CRORE),
    ]
    assert records[1]["trailing_pe"] is None


def test_export_enriched_writes_numeric_parquet_columns(globe_db, tmp_path) -> None:
    import pyarrow.parquet as pq

    path = tmp_path / "enriched.parquet"
    export_enriched(globe_db, output=path, format=None)

    table = pq.read_table(path)
    assert table.column_names == list(ENRICHED_COLUMNS)
    assert str(table.schema.field("market_cap").type) == "double"
    assert str(table.schema.field("sector").type) == "string"
    assert table.num_rows == 4


def test_export_enriched_needs_a_format_for_stdout(globe_db) -> None:
    with pytest.raises(ValueError, match="Pass --format"):
        export_enriched(globe_db, output=None, format=None)


def test_lookup_fields_returns_every_exact_match_beyond_the_search_default(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    # 25 schemes share one BSE name, more than a search returns by default.
    seed_consolidated(
        db_path,
        [
            (
                f"INF000A{index:05d}",
                "equity",
                None,
                f"FUND{index}",
                None,
                "SAME FUND HOUSE",
                None,
                None,
                None,
                f"FUND{index}.BO",
            )
            for index in range(25)
        ],
    )

    assert len(lookup_fields("SAME FUND HOUSE", db_path)) == 25
