import sqlite3

from stocky.database import (
    CONSOLIDATED_TABLE,
    decode_response_json,
    import_yahoo_json_cache,
    read_available_yahoo_symbols,
)


def test_import_yahoo_json_cache_to_sqlite(tmp_path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "RELIANCE.NS.json").write_text('{"RELIANCE.NS": {"price": 1}}', encoding="utf-8")
    (cache_dir / "INFY.BO.json").write_text('{"INFY.BO": {"price": 2}}', encoding="utf-8")
    db_path = tmp_path / "stocky.db"

    result = import_yahoo_json_cache(cache_dir, db_path)

    assert result.imported == 2
    assert result.skipped == 0
    assert read_available_yahoo_symbols(db_path) == {"RELIANCE.NS", "INFY.BO"}

    with sqlite3.connect(db_path) as con:
        stored_payload = con.execute(
            "SELECT response_json FROM yahoo_responses WHERE yahoo_symbol = 'RELIANCE.NS'"
        ).fetchone()[0]
    assert decode_response_json(stored_payload) == {"RELIANCE.NS": {"price": 1}}


def test_consolidated_table_name_is_the_db_contract(tmp_path) -> None:
    db_path = tmp_path / "stocky.db"
    with sqlite3.connect(db_path) as con:
        con.execute(f"CREATE TABLE {CONSOLIDATED_TABLE} (isin TEXT, zd_symbol TEXT, yq_symbol TEXT)")
        con.execute(f"INSERT INTO {CONSOLIDATED_TABLE} VALUES ('INE1', 'RELIANCE', 'RELIANCE')")

    from stocky.database import fetch_consolidated_symbols

    assert fetch_consolidated_symbols(db_path) == ["RELIANCE"]
