import csv
import sqlite3

import pytest

import stocky.indices as indices
from stocky.database import CONSOLIDATED_TABLE
from stocky.indices import (
    INDICES_COLUMNS,
    INDICES_SOURCE_COLUMNS,
    INDICES_TABLE,
    import_indices,
    load_indices,
    search_indices,
)
from stocky.pipeline import rebuild_database

INDICES_HEADERS = (
    "instrument_token",
    "exchange_token",
    "tradingsymbol",
    "name",
    "last_price",
    "expiry",
    "strike",
    "tick_size",
    "lot_size",
    "instrument_type",
    "segment",
    "exchange",
)
INDICES_ROWS = (
    ("256265", "1001", "NIFTY 50", "NIFTY 50", "0", "", "0", "0", "0", "EQ", "INDICES", "NSE"),
    ("260105", "1016", "NIFTY BANK", "NIFTY BANK", "0", "", "0", "0", "0", "EQ", "INDICES", "NSE"),
    ("265", "1", "SENSEX", "SENSEX", "0", "", "0", "0", "0", "EQ", "INDICES", "BSE"),
)


def _write_indices_csv(path, rows=INDICES_ROWS, missing=None):
    columns = [column for column in INDICES_HEADERS if column != missing]
    with path.open("w", newline="", encoding="utf-8") as source:
        writer = csv.DictWriter(source, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(dict(zip(INDICES_HEADERS, row, strict=True)) for row in rows)
    return path


def _read_indices(db_path):
    with sqlite3.connect(db_path) as con:
        return con.execute(
            f"SELECT {', '.join(INDICES_COLUMNS)} FROM {INDICES_TABLE} ORDER BY zd_instrument_token"
        ).fetchall()


def test_load_indices_maps_source_text_and_null_cells(tmp_path) -> None:
    row = (" 0256265 ", " 01001 ", " NIFTY 50 ", " ", "9.25", "", "0", "0", "0", "EQ", "INDICES", " NSE ")

    frame = load_indices(_write_indices_csv(tmp_path / "instruments.csv", rows=(row,)))

    assert list(frame.columns) == list(INDICES_COLUMNS)
    assert frame.to_dict("records") == [
        {
            "exchange": "NSE",
            "zd_symbol": "NIFTY 50",
            "name": None,
            "zd_instrument_token": "0256265",
            "exchange_token": "01001",
        }
    ]


def test_load_indices_keeps_only_indices_segment(tmp_path) -> None:
    excluded = (
        ("", "1", "FUT", "Excluded", "0", "", "0", "0", "1", "FUT", "NFO-FUT", "NFO"),
        ("", "1", "CE", "Excluded", "0", "", "0", "0", "1", "CE", "NFO-OPT", "NFO"),
        ("", "1", "GOLD", "Excluded", "0", "", "0", "0", "1", "EQ", "NCO", "NCO"),
    )
    frame = load_indices(_write_indices_csv(tmp_path / "instruments.csv", rows=(*INDICES_ROWS, *excluded)))

    assert frame["zd_instrument_token"].tolist() == ["256265", "260105", "265"]
    assert frame["exchange"].tolist() == ["NSE", "NSE", "BSE"]


@pytest.mark.parametrize("column", [*INDICES_SOURCE_COLUMNS, "segment"])
def test_load_indices_rejects_missing_columns(tmp_path, column) -> None:
    path = _write_indices_csv(tmp_path / "instruments.csv", missing=column)

    with pytest.raises(ValueError, match=f"missing required columns: {column}"):
        load_indices(path)


def test_load_indices_rejects_duplicate_tokens_across_exchanges(tmp_path) -> None:
    duplicate = (*INDICES_ROWS[0][:-1], "BSE")
    path = _write_indices_csv(tmp_path / "instruments.csv", rows=(*INDICES_ROWS, duplicate))

    with pytest.raises(ValueError, match="instrument_tokens more than once: 256265"):
        load_indices(path)


def test_load_indices_rejects_empty_token(tmp_path) -> None:
    path = _write_indices_csv(tmp_path / "instruments.csv", rows=((" ", *INDICES_ROWS[0][1:]),))

    with pytest.raises(ValueError, match="without an instrument_token"):
        load_indices(path)


@pytest.mark.parametrize("segment", [None, "NSE", "indices", " INDICES "])
def test_load_indices_rejects_zero_indices_rows(tmp_path, segment) -> None:
    rows = () if segment is None else ((*INDICES_ROWS[0][:10], segment, "NSE"),)
    path = _write_indices_csv(tmp_path / "instruments.csv", rows=rows)

    with pytest.raises(ValueError, match="no INDICES rows"):
        load_indices(path)


def test_load_indices_reports_missing_file(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="Zerodha instruments not found"):
        load_indices(tmp_path / "absent.csv")


def test_import_indices_leaves_consolidated_untouched(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)
    with sqlite3.connect(db_path) as con:
        before = con.execute(f"SELECT * FROM {CONSOLIDATED_TABLE} ORDER BY isin").fetchall()

    result = import_indices(_write_indices_csv(tmp_path / "instruments.csv"), db_path)

    assert result.rows == 3
    assert len(_read_indices(db_path)) == 3
    with sqlite3.connect(db_path) as con:
        assert con.execute(f"SELECT * FROM {CONSOLIDATED_TABLE} ORDER BY isin").fetchall() == before
        assert [(row[1], row[2], row[5]) for row in con.execute(f"PRAGMA table_info({INDICES_TABLE})")] == [
            (column, "TEXT", int(column == "zd_instrument_token")) for column in INDICES_COLUMNS
        ]
        assert {row[1] for row in con.execute(f"PRAGMA index_list({INDICES_TABLE})")} == {
            "idx_indices_zd_symbol",
            "sqlite_autoindex_indices_1",
        }


def test_import_indices_replaces_previous_rows(tmp_path) -> None:
    db_path = tmp_path / "stocky.db"
    path = _write_indices_csv(tmp_path / "instruments.csv")
    import_indices(path, db_path)

    import_indices(_write_indices_csv(path, rows=INDICES_ROWS[:1]), db_path)

    assert _read_indices(db_path) == [("NSE", "NIFTY 50", "NIFTY 50", "256265", "1001")]


def test_failed_import_keeps_previous_table(tmp_path) -> None:
    db_path = tmp_path / "stocky.db"
    path = _write_indices_csv(tmp_path / "instruments.csv")
    import_indices(path, db_path)
    before = _read_indices(db_path)

    with pytest.raises(ValueError):
        import_indices(_write_indices_csv(path, rows=INDICES_ROWS * 2), db_path)

    assert _read_indices(db_path) == before


def test_failed_write_rolls_back_previous_table(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "stocky.db"
    path = _write_indices_csv(tmp_path / "instruments.csv")
    import_indices(path, db_path)
    before = _read_indices(db_path)

    class FailingConnection(sqlite3.Connection):
        def executemany(self, sql, parameters):
            super().executemany(sql, parameters)
            raise sqlite3.OperationalError("write failed")

    monkeypatch.setattr(indices, "connect", lambda path: sqlite3.connect(path, factory=FailingConnection))
    with pytest.raises(sqlite3.OperationalError, match="write failed"):
        import_indices(_write_indices_csv(path, rows=INDICES_ROWS[:1]), db_path)

    assert _read_indices(db_path) == before
    with sqlite3.connect(db_path) as con:
        assert len(con.execute(f"PRAGMA index_list({INDICES_TABLE})").fetchall()) == 2


def test_search_indices_matches_fragments_and_exact_identifiers(tmp_path) -> None:
    db_path = tmp_path / "stocky.db"
    import_indices(_write_indices_csv(tmp_path / "instruments.csv"), db_path)

    assert search_indices("nifty", db_path).total == 2
    assert search_indices("nifty bank", db_path, exact=True).matches[0].zd_instrument_token == "260105"
    assert search_indices("260105", db_path, exact=True).matches[0].zd_symbol == "NIFTY BANK"
    assert search_indices("1016", db_path, exact=True).matches[0].zd_symbol == "NIFTY BANK"
    assert search_indices("260105", db_path).total == 1
    assert search_indices("1016", db_path).total == 1
    assert search_indices("2601", db_path).total == 0
    assert search_indices("101", db_path).total == 0
    assert search_indices("nifty", db_path, exact=True).total == 0


def test_search_indices_reads_count_and_matches_in_one_transaction(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "stocky.db"
    import_indices(_write_indices_csv(tmp_path / "instruments.csv"), db_path)
    statements = []
    with sqlite3.connect(db_path) as con:
        con.set_trace_callback(statements.append)
        monkeypatch.setattr(indices, "connect", lambda path: con)

        result = search_indices("nifty", db_path)

        assert not con.in_transaction

    count_position = next(i for i, sql in enumerate(statements) if sql.startswith("SELECT COUNT(*)"))
    rows_position = next(i for i, sql in enumerate(statements) if sql.lstrip().startswith("SELECT exchange,"))
    assert statements.count("BEGIN") == 1
    assert statements.count("COMMIT") == 1
    assert statements.index("BEGIN") < count_position < rows_position < statements.index("COMMIT")
    assert result.total == len(result.matches) == 2


def test_search_indices_matches_name_fragments_but_not_exact_names(tmp_path) -> None:
    db_path = tmp_path / "stocky.db"
    row = (*INDICES_ROWS[0][:3], "Large cap benchmark", *INDICES_ROWS[0][4:])
    import_indices(_write_indices_csv(tmp_path / "instruments.csv", rows=(row,)), db_path)

    assert search_indices("cap bench", db_path).matches[0].zd_symbol == "NIFTY 50"
    assert search_indices("Large cap benchmark", db_path, exact=True).total == 0


@pytest.mark.parametrize("exact", [False, True])
def test_search_indices_filters_exchange_case_insensitively(tmp_path, exact) -> None:
    db_path = tmp_path / "stocky.db"
    duplicate_symbol = ("999", *INDICES_ROWS[0][1:-1], "BSE")
    import_indices(_write_indices_csv(tmp_path / "instruments.csv", rows=(*INDICES_ROWS, duplicate_symbol)), db_path)

    result = search_indices("NIFTY 50", db_path, exact=exact, exchange=" bSe ")

    assert result.total == 1
    assert result.matches[0].zd_instrument_token == "999"
    assert search_indices("NIFTY 50", db_path, exact=exact, exchange="GLOBAL").total == 0


def test_search_indices_ranks_exact_matches_first_and_limits(tmp_path) -> None:
    db_path = tmp_path / "stocky.db"
    row = ("999", "123", "OTHER", "NIFTY BANK", *INDICES_ROWS[0][4:])
    import_indices(_write_indices_csv(tmp_path / "instruments.csv", rows=(*INDICES_ROWS, row)), db_path)

    result = search_indices("NIFTY BANK", db_path, limit=1)

    assert result.total == 2
    assert len(result.matches) == 1
    assert result.matches[0].zd_instrument_token == "260105"
    assert [match.zd_symbol for match in search_indices("NIFTY", db_path).matches] == [
        "NIFTY 50",
        "NIFTY BANK",
        "OTHER",
    ]


def test_search_indices_treats_like_wildcards_literally(tmp_path) -> None:
    db_path = tmp_path / "stocky.db"
    import_indices(_write_indices_csv(tmp_path / "instruments.csv"), db_path)

    assert search_indices("%", db_path).total == 0
    assert search_indices("_", db_path).total == 0


def test_search_indices_requires_imported_table(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    with pytest.raises(RuntimeError, match="Run 'stocky indices import' first"):
        search_indices("NIFTY", db_path)
    with pytest.raises(FileNotFoundError, match="Database not found"):
        search_indices("NIFTY", tmp_path / "absent.db")
    with pytest.raises(ValueError, match="must not be empty"):
        search_indices("  ", db_path)
    with pytest.raises(ValueError, match="at least 1"):
        search_indices("NIFTY", db_path, limit=0)


def test_rebuild_keeps_indices_table(tmp_path, market_csv_builder) -> None:
    db_path = tmp_path / "stocky.db"
    import_indices(_write_indices_csv(tmp_path / "instruments.csv"), db_path)
    before = _read_indices(db_path)

    rebuild_database(market_csv_builder(tmp_path / "inputs"), db_path=db_path, backup=False)

    assert _read_indices(db_path) == before
