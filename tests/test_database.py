import sqlite3

import pytest

from stocky.database import (
    CONSOLIDATED_TABLE,
    backup_database,
    connect,
    decode_response_json,
    encode_response_json,
    fetch_consolidated_symbols,
    import_yahoo_json_cache,
    initialize_database,
    read_available_yahoo_symbols,
    read_status,
    search_instruments,
    split_yahoo_symbol,
    upsert_yahoo_response,
)


def _seed_yahoo(db_path, yahoo_symbols: list[tuple[str, str, str]]) -> None:
    initialize_database(db_path)
    with connect(db_path) as con:
        for yahoo_symbol, symbol, exchange in yahoo_symbols:
            upsert_yahoo_response(
                con,
                yahoo_symbol=yahoo_symbol,
                symbol=symbol,
                exchange=exchange,
                response_json=encode_response_json({"price": 1}),
                source="test",
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


def test_read_available_yahoo_symbols_does_not_create_missing_table(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "legacy.db"
    seed_consolidated(db_path)

    assert read_available_yahoo_symbols(db_path) == set()

    with sqlite3.connect(db_path) as con:
        names = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert names == {"consolidated"}


def test_import_yahoo_json_cache_skips_bad_file(tmp_path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "GOOD.NS.json").write_text('{"price": 1}', encoding="utf-8")
    (cache_dir / "BAD.NS.json").write_text("not json", encoding="utf-8")

    result = import_yahoo_json_cache(cache_dir, tmp_path / "stocky.db")

    assert result.imported == 1
    assert result.skipped == 1


def test_consolidated_table_name_is_the_db_contract(tmp_path) -> None:
    db_path = tmp_path / "stocky.db"
    with sqlite3.connect(db_path) as con:
        con.execute(f"CREATE TABLE {CONSOLIDATED_TABLE} (isin TEXT, zd_symbol TEXT, yq_symbol TEXT)")
        con.execute(f"INSERT INTO {CONSOLIDATED_TABLE} VALUES ('INE1', 'RELIANCE', 'RELIANCE')")

    from stocky.database import fetch_consolidated_symbols

    assert fetch_consolidated_symbols(db_path) == ["RELIANCE"]


def test_read_status_reports_counts_and_coverage(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)
    _seed_yahoo(
        db_path,
        [
            ("RELIANCE.NS", "RELIANCE", "NSE"),
            ("RELIANCE.BO", "RELIANCE", "BSE"),
        ],
    )

    status = read_status(db_path)

    assert status.consolidated_rows == 4
    assert status.ins_type_counts == {"equity": 4}
    assert {entry.column: entry.populated for entry in status.coverage} == {
        "isin": 4,
        "zd_symbol": 4,
        "yq_symbol": 2,
        "nse_symbol": 2,
        "bse_sc_code": 3,
        "bse_sc_name": 4,
    }
    assert status.yahoo_rows == 2
    assert status.yahoo_exchange_counts == {"NSE": 1, "BSE": 1}
    assert status.yahoo_oldest_fetch is not None
    assert status.yahoo_newest_fetch is not None
    assert status.yahoo_cached_yq_symbols == 1
    assert status.db_size_bytes > 0


def test_read_status_missing_db_raises_without_creating_file(tmp_path) -> None:
    db_path = tmp_path / "missing.db"

    with pytest.raises(FileNotFoundError):
        read_status(db_path)

    assert not db_path.exists()


def test_read_status_without_consolidated_table(tmp_path) -> None:
    db_path = tmp_path / "yahoo-only.db"
    _seed_yahoo(db_path, [("RELIANCE.NS", "RELIANCE", "NSE")])

    status = read_status(db_path)

    assert status.consolidated_rows == 0
    assert status.ins_type_counts == {}
    assert status.coverage == []
    assert status.yahoo_rows == 1
    assert status.yahoo_cached_yq_symbols == 0


def test_read_status_without_yahoo_table(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "consolidated-only.db"
    seed_consolidated(db_path)

    status = read_status(db_path)

    assert status.consolidated_rows == 4
    assert status.yahoo_rows == 0
    assert status.yahoo_exchange_counts == {}
    assert status.yahoo_oldest_fetch is None
    assert status.yahoo_newest_fetch is None


def test_search_exact_symbol_is_case_insensitive(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = search_instruments("reliance", db_path)

    assert result.total == 1
    assert result.matches[0].isin == "INE002A01018"


def test_search_matches_name_fragment_and_bse_code(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    by_name = search_instruments("micron", db_path)
    assert [match.zd_symbol for match in by_name.matches] == ["20MICRONS"]

    by_code = search_instruments("500209", db_path)
    assert [match.zd_symbol for match in by_code.matches] == ["INFY"]


def test_search_ranks_exact_matches_first(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = search_instruments("INFY", db_path)

    assert result.total == 2
    assert result.matches[0].zd_symbol == "INFY"
    assert result.matches[1].zd_symbol == "INFYBEES"


def test_search_exact_flag_disables_substring_matching(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    assert search_instruments("INFY", db_path, exact=True).total == 1
    assert search_instruments("INF", db_path, exact=True).total == 0


def test_search_limit_truncates_but_reports_total(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    result = search_instruments("INE", db_path, limit=2)

    assert result.total == 4
    assert len(result.matches) == 2


def test_search_escapes_like_wildcards(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    assert search_instruments("%", db_path).total == 0
    assert search_instruments("INF_", db_path).total == 0


def test_search_rejects_blank_term_and_missing_table(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    with pytest.raises(ValueError):
        search_instruments("   ", db_path)

    yahoo_only = tmp_path / "yahoo_only.db"
    initialize_database(yahoo_only)
    with pytest.raises(RuntimeError):
        search_instruments("INFY", yahoo_only)


def test_search_rejects_limit_below_one(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    with pytest.raises(ValueError, match="Limit must be at least 1"):
        search_instruments("INFY", db_path, limit=0)


def test_fetch_consolidated_symbols_limit_and_failures(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    assert fetch_consolidated_symbols(db_path, key="nse_symbol", limit=1) == ["RELIANCE"]

    with pytest.raises(ValueError, match="Unsupported symbol key"):
        fetch_consolidated_symbols(db_path, key="bad_key")
    with pytest.raises(FileNotFoundError, match="Database not found"):
        fetch_consolidated_symbols(tmp_path / "missing.db")

    yahoo_only = tmp_path / "yahoo-only.db"
    initialize_database(yahoo_only)
    with pytest.raises(RuntimeError, match="does not exist"):
        fetch_consolidated_symbols(yahoo_only)


def test_split_yahoo_symbol_without_suffix_uses_unknown_exchange() -> None:
    assert split_yahoo_symbol("RELIANCE") == ("RELIANCE", "UNKNOWN")


def test_backup_database_copies_source_bytes(tmp_path) -> None:
    db_path = tmp_path / "stocky.db"
    db_path.write_bytes(b"sqlite source bytes")

    backup_path = backup_database(db_path, tmp_path / "backups")

    assert backup_path is not None
    assert backup_path.exists()
    assert backup_path.read_bytes() == db_path.read_bytes()


def test_backup_database_returns_none_for_missing_source(tmp_path) -> None:
    backup_dir = tmp_path / "backups"

    assert backup_database(tmp_path / "missing.db", backup_dir) is None
    assert not backup_dir.exists()
