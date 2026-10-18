import csv
import gzip
import json
import sqlite3

import pytest

import stocky.brokers as brokers
from stocky.brokers import (
    ALICEBLUE_SOURCE_COLUMNS,
    BROKER_COLUMNS,
    GROWW_SOURCE_COLUMNS,
    UPSTOX_SOURCE_COLUMNS,
    import_broker,
    load_aliceblue,
    load_groww,
    load_upstox,
    search_brokers,
)
from stocky.pipeline import rebuild_database

GROWW = {
    "exchange": "NSE",
    "exchange_token": "02885",
    "isin": "INE002A01018",
    "trading_symbol": "RELIANCE",
    "groww_symbol": "NSE-RELIANCE",
    "name": "Reliance Industries",
    "segment": "CASH",
    "instrument_type": "EQ",
}
UPSTOX = {
    "segment": "NSE_EQ",
    "exchange_token": "02885",
    "isin": "INE002A01018",
    "trading_symbol": "RELIANCE",
    "instrument_key": "NSE_EQ|INE002A01018",
    "name": "Reliance Industries",
    "instrument_type": "EQ",
}
ALICE_NSE = {
    "Exch": "NSE",
    "Exchange Segment": "nse_cm",
    "Group Name": "EQ",
    "Symbol": "RELIANCE",
    "Token": "02885",
    "Instrument Type": "0",
    "Instrument Name": "Reliance Industries",
    "Trading Symbol": "RELIANCE-EQ",
}
ALICE_BSE = {
    "Exch": "BSE",
    "Exchange Segment": "bse_cm",
    "Symbol": "RELIANCE",
    "Token": "500325",
    "Instrument Type": "E",
    "Instrument Name": "Reliance Industries",
    "Trading Symbol": "RELIANCE",
}


def _csv(path, records, missing=None):
    columns = [column for column in records[0] if column != missing]
    with path.open("w", encoding="utf-8", newline="") as source:
        writer = csv.DictWriter(source, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    return path


def _gzip(path, records):
    with gzip.open(path, "wt", encoding="utf-8") as source:
        json.dump(records, source)
    return path


@pytest.fixture
def sources(tmp_path):
    return {
        "groww": _csv(tmp_path / "groww.csv", [GROWW]),
        "upstox": _gzip(tmp_path / "upstox.json.gz", [UPSTOX]),
        "aliceblue": _csv(tmp_path / "nse.csv", [ALICE_NSE]),
        "bse": _csv(tmp_path / "bse.csv", [ALICE_BSE]),
    }


def _read(db):
    with sqlite3.connect(db) as con:
        return con.execute("SELECT * FROM broker_symbols ORDER BY broker, exchange, exchange_token").fetchall()


def test_load_groww_maps_trimmed_text_and_nulls(tmp_path):
    record = {**GROWW, "name": " ", "exchange_token": " 02885 ", "isin": " "}
    frame = load_groww(_csv(tmp_path / "groww.csv", [record]))
    assert list(frame.columns) == list(BROKER_COLUMNS)
    assert frame.to_dict("records") == [
        {
            "broker": "groww",
            "exchange": "NSE",
            "exchange_token": "02885",
            "isin": None,
            "symbol": "RELIANCE",
            "broker_key": "NSE-RELIANCE",
            "name": None,
        }
    ]


def test_load_groww_excludes_derivatives_indices_and_other_exchanges(tmp_path):
    excluded = [
        {**GROWW, "segment": segment, "instrument_type": kind, "exchange_token": ""}
        for segment, kind in [("FNO", "FUT"), ("FNO", "CE"), ("FNO", "PE"), ("CASH", "IDX"), ("CASH", "FUT")]
    ]
    excluded.append({**GROWW, "exchange": "MCX", "exchange_token": ""})
    frame = load_groww(_csv(tmp_path / "groww.csv", [GROWW, *excluded, {**GROWW, "exchange": "BSE"}]))
    assert frame["exchange"].tolist() == ["BSE", "NSE"]


def test_load_upstox_maps_columns_and_excludes_derivatives_indices_and_warrants(tmp_path):
    excluded = [
        {"segment": segment, "instrument_type": kind}
        for segment, kind in [
            ("NSE_FO", "FUT"),
            ("BSE_FO", "CE"),
            ("NSE_COM", "PE"),
            ("NSE_INDEX", "INDEX"),
            ("BSE_INDEX", "INDEX"),
            ("NSE_EQ", "W1"),
            ("NSE_EQ", "FUT"),
        ]
    ]
    records = [UPSTOX, {**UPSTOX, "segment": "BSE_EQ", "isin": "", "instrument_key": "BSE_EQ|OTHER"}, *excluded]
    frame = load_upstox(_gzip(tmp_path / "upstox.json.gz", records))
    assert list(frame.columns) == list(BROKER_COLUMNS)
    assert len(frame) == 2
    assert frame.iloc[0].tolist() == ["upstox", "BSE", "02885", None, "RELIANCE", "BSE_EQ|OTHER", "Reliance Industries"]
    assert frame.iloc[1].broker_key == "NSE_EQ|INE002A01018"


def test_load_aliceblue_resolves_both_exchanges_and_restricts_nse_series(tmp_path, seed_consolidated, sources):
    db = tmp_path / "stocky.db"
    seed_consolidated(db)
    records = [
        {**ALICE_NSE, "Group Name": series, "Token": str(index), "Trading Symbol": f"RELIANCE-{series}"}
        for index, series in enumerate(["EQ", "BE", "BZ", "GB", "SM", "N0", "GS"])
    ]
    records += [{**ALICE_NSE, "Token": "999", "Symbol": "UNKNOWN"}]
    records += [
        {**ALICE_NSE, "Exchange Segment": "nse_idx", "Instrument Type": "INDEX"},
        {**ALICE_NSE, "Exchange Segment": "nse_fo", "Instrument Type": "FUT"},
        {**ALICE_NSE, "Exchange Segment": "nse_fo", "Instrument Type": "CE"},
        {**ALICE_NSE, "Instrument Type": "3", "Group Name": "W1"},
    ]
    _csv(sources["aliceblue"], records)
    _csv(
        sources["bse"],
        [
            ALICE_BSE,
            {**ALICE_BSE, "Token": "999", "Symbol": "RELIANCE"},
            {**ALICE_BSE, "Exchange Segment": "bse_idx", "Instrument Type": "INDEX"},
            {**ALICE_BSE, "Exchange Segment": "bse_fo", "Instrument Type": "PE"},
        ],
    )
    frame = load_aliceblue(sources["aliceblue"], sources["bse"], db)
    assert list(frame.columns) == list(BROKER_COLUMNS)
    assert len(frame) == 10
    assert (
        frame.loc[frame.exchange.eq("NSE") & frame.exchange_token.isin(["0", "1", "2", "3"]), "isin"].tolist()
        == ["INE002A01018"] * 4
    )
    assert frame.loc[frame.exchange.eq("NSE") & frame.exchange_token.isin(["4", "5", "6", "999"]), "isin"].isna().all()
    assert frame.loc[frame.exchange.eq("BSE"), "isin"].tolist() == ["INE002A01018", None]
    assert frame["exchange_token"].equals(frame["broker_key"])
    assert frame.loc[frame.exchange_token.eq("0"), "symbol"].item() == "RELIANCE-EQ"


@pytest.mark.parametrize(
    "broker,column",
    [
        *(("groww", c) for c in [*GROWW_SOURCE_COLUMNS, "segment", "instrument_type"]),
        *(("upstox", c) for c in [*UPSTOX_SOURCE_COLUMNS, "segment", "instrument_type"]),
        *(
            ("aliceblue", c)
            for c in [*ALICEBLUE_SOURCE_COLUMNS, "Exchange Segment", "Instrument Type", "Symbol", "Group Name"]
        ),
        *(("bse", c) for c in [*ALICEBLUE_SOURCE_COLUMNS, "Exchange Segment", "Instrument Type", "Symbol"]),
    ],
)
def test_missing_columns(tmp_path, seed_consolidated, sources, broker, column):
    db = tmp_path / "stocky.db"
    seed_consolidated(db)
    if broker == "upstox":
        _gzip(sources[broker], [{k: v for k, v in UPSTOX.items() if k != column}])
    else:
        _csv(sources[broker], [{"groww": GROWW, "aliceblue": ALICE_NSE, "bse": ALICE_BSE}[broker]], missing=column)
    with pytest.raises(ValueError, match=f"missing required columns: {column}"):
        if broker in ("aliceblue", "bse"):
            load_aliceblue(sources["aliceblue"], sources["bse"], db)
        else:
            {"groww": load_groww, "upstox": load_upstox}[broker](sources[broker])


def test_upstox_checks_required_keys_in_each_cash_object(tmp_path):
    with pytest.raises(ValueError, match="missing required columns: isin"):
        load_upstox(_gzip(tmp_path / "upstox.json.gz", [UPSTOX, {k: v for k, v in UPSTOX.items() if k != "isin"}]))


@pytest.mark.parametrize("broker", ["groww", "upstox", "aliceblue"])
@pytest.mark.parametrize("invalid", ["duplicate", "empty", "zero"])
def test_rejects_invalid_cash_rows(tmp_path, seed_consolidated, sources, broker, invalid):
    db = tmp_path / "stocky.db"
    seed_consolidated(db)
    base = {"groww": GROWW, "upstox": UPSTOX, "aliceblue": ALICE_NSE}[broker]
    token = "Token" if broker == "aliceblue" else "exchange_token"
    records = [base, base] if invalid == "duplicate" else [{**base, token: " "}]
    if invalid == "zero":
        records = [{**base, "segment": "NSE_FO", "Exchange Segment": "nse_fo"}]
        if broker == "aliceblue":
            _csv(sources["bse"], [{**ALICE_BSE, "Exchange Segment": "bse_fo"}])
    if broker == "upstox":
        _gzip(sources[broker], records)
    else:
        _csv(sources[broker], records)
    message = {"duplicate": "more than once", "empty": "without an exchange_token", "zero": "no cash"}[invalid]
    with pytest.raises(ValueError, match=message):
        import_broker(broker, sources[broker], db, bse_path=sources["bse"])


@pytest.mark.parametrize("broker", ["groww", "upstox", "aliceblue"])
def test_missing_source_file(tmp_path, seed_consolidated, sources, broker):
    db = tmp_path / "stocky.db"
    seed_consolidated(db)
    with pytest.raises(FileNotFoundError, match="not found"):
        import_broker(broker, tmp_path / "missing", db, bse_path=sources["bse"])


def test_aliceblue_requires_consolidated(tmp_path, sources):
    db = tmp_path / "stocky.db"
    with pytest.raises(RuntimeError, match="Run 'stocky rebuild' first"):
        import_broker("aliceblue", sources["aliceblue"], db, bse_path=sources["bse"])
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
    with pytest.raises(ValueError, match="both NSE and BSE"):
        import_broker("aliceblue", sources["aliceblue"], db)


def test_import_schema_coverage_and_per_broker_replace(tmp_path, seed_consolidated, sources):
    db = tmp_path / "stocky.db"
    seed_consolidated(db)
    with sqlite3.connect(db) as con:
        before = con.execute("SELECT * FROM consolidated").fetchall()
    for broker in ("groww", "upstox", "aliceblue"):
        results = import_broker(broker, sources[broker], db, bse_path=sources["bse"])
        assert all(result.rows == result.with_isin == result.in_consolidated == 1 for result in results)
    other_brokers = [row for row in _read(db) if row[0] != "groww"]
    _csv(sources["groww"], [{**GROWW, "exchange": "BSE", "exchange_token": "999", "isin": ""}])
    result = import_broker("groww", sources["groww"], db)[0]
    assert (result.rows, result.with_isin, result.in_consolidated) == (1, 0, 0)
    assert [row for row in _read(db) if row[0] != "groww"] == other_brokers
    assert [row[2] for row in _read(db) if row[0] == "groww"] == ["999"]
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT * FROM consolidated").fetchall() == before
        assert [(r[1], r[2], r[5]) for r in con.execute("PRAGMA table_info(broker_symbols)")] == [
            (col, "TEXT", {"broker": 1, "exchange": 2, "exchange_token": 3}.get(col, 0)) for col in BROKER_COLUMNS
        ]
        assert {r[1] for r in con.execute("PRAGMA index_list(broker_symbols)")} == {
            "idx_broker_symbols_isin",
            "idx_broker_symbols_symbol",
            "sqlite_autoindex_broker_symbols_1",
        }


def test_failed_validation_preserves_previous_rows(tmp_path, sources):
    db = tmp_path / "stocky.db"
    import_broker("groww", sources["groww"], db)
    before = _read(db)
    _csv(sources["groww"], [GROWW, GROWW])
    with pytest.raises(ValueError):
        import_broker("groww", sources["groww"], db)
    assert _read(db) == before


@pytest.mark.parametrize("initial", [False, True])
def test_failed_write_rolls_back_rows_and_schema(tmp_path, sources, monkeypatch, initial):
    db = tmp_path / "stocky.db"
    import_broker("upstox", sources["upstox"], db)
    if not initial:
        import_broker("groww", sources["groww"], db)
    before = _read(db)

    class FailingConnection(sqlite3.Connection):
        def executemany(self, sql, parameters):
            super().executemany(sql, parameters)
            raise sqlite3.OperationalError("write failed")

    monkeypatch.setattr(brokers, "connect", lambda path: sqlite3.connect(path, factory=FailingConnection))
    with pytest.raises(sqlite3.OperationalError, match="write failed"):
        import_broker("groww", sources["groww"], db)
    assert _read(db) == before


@pytest.fixture
def imported(tmp_path, seed_consolidated, sources):
    db = tmp_path / "stocky.db"
    seed_consolidated(db)
    for broker in ("groww", "upstox", "aliceblue"):
        import_broker(broker, sources[broker], db, bse_path=sources["bse"])
    return db


@pytest.mark.parametrize(
    "term,count",
    [
        ("ine002a01018", 4),
        ("reliance", 3),
        ("02885", 3),
        ("NSE_EQ|INE002A01018", 1),
        ("NSE-RELIANCE", 1),
        ("RELIANCE-EQ", 1),
    ],
)
def test_exact_identifiers(imported, term, count):
    assert search_brokers(term, imported, exact=True).total == count


def test_fragments_filters_limits_and_literal_wildcards(imported):
    assert search_brokers("industr", imported).total == 4
    assert search_brokers("relian", imported).total == 4
    assert search_brokers("Industries", imported, exact=True).total == 0
    assert search_brokers("028", imported).total == 0
    assert search_brokers("ine002", imported).total == 0
    assert search_brokers("reliance", imported, broker=" AlIceblue ", exchange=" bSe ").total == 1
    result = search_brokers("reliance", imported, limit=1)
    assert result.total == 4 and len(result.matches) == 1
    assert search_brokers("%", imported).total == search_brokers("_", imported).total == 0
    assert search_brokers("reliance", imported, exchange="MCX").total == 0


def test_exact_matches_rank_before_name_fragments(imported):
    with sqlite3.connect(imported) as con:
        con.execute("UPDATE broker_symbols SET name = 'A RELIANCE' WHERE symbol = 'RELIANCE-EQ'")
    matches = search_brokers("RELIANCE", imported).matches
    assert matches[-1].symbol == "RELIANCE-EQ"
    assert search_brokers("RELIANCE", imported, limit=1).matches[0].symbol == "RELIANCE"


def test_query_count_and_rows_share_explicit_transaction(imported, monkeypatch):
    statements = []
    with sqlite3.connect(imported) as con:
        con.set_trace_callback(statements.append)
        monkeypatch.setattr(brokers, "connect", lambda path: con)
        result = search_brokers("reliance", imported)
        assert not con.in_transaction
    count = next(i for i, sql in enumerate(statements) if sql.startswith("SELECT COUNT(*)"))
    rows = next(i for i, sql in enumerate(statements) if sql.startswith("SELECT broker,"))
    assert statements.count("BEGIN") == statements.count("COMMIT") == 1
    assert statements.index("BEGIN") < count < rows < statements.index("COMMIT")
    assert result.total == len(result.matches) == 4


def test_query_errors(tmp_path, seed_consolidated):
    db = tmp_path / "stocky.db"
    with pytest.raises(FileNotFoundError, match="Database not found"):
        search_brokers("reliance", db)
    seed_consolidated(db)
    with pytest.raises(RuntimeError, match="Run 'stocky brokers import' first"):
        search_brokers("reliance", db)
    with pytest.raises(ValueError, match="must not be empty"):
        search_brokers(" ", db)
    with pytest.raises(ValueError, match="at least 1"):
        search_brokers("reliance", db, limit=0)


def test_rebuild_keeps_broker_symbols(imported, market_csv_builder, tmp_path):
    before = _read(imported)
    rebuild_database(market_csv_builder(tmp_path / "inputs"), db_path=imported, backup=False)
    assert _read(imported) == before
