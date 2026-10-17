import csv
import io
import sqlite3
from zipfile import ZipFile

import pytest

import stocky.bse_debt as bse_debt
from stocky.bse_debt import (
    BSE_DEBT_COLUMNS,
    BSE_DEBT_TABLE,
    import_bse_debt,
    load_bse_debt,
    newest_bse_debt_archive,
    search_bse_debt,
)
from stocky.database import CONSOLIDATED_TABLE
from stocky.pipeline import rebuild_database

DEBT_HEADERS = {
    "fgroup": "Security_cd,sc_name,Open Price,ISIN No.,FACE VALUE,COUP0N (%),Maturity Date",
    "icdm": "Security Code,Issuer Name,Coupon (%),Maturity Date,LTP,ISIN No.,Face Value",
    "wdm": "Scrip Code,Security Code,Security Description,Coupon Rate (%),Open Price,MaturityDate",
}
DEBT_ROWS = {
    "fgroup": (" 978034 , 0MCIBPL28   ,101989.10, INE0K0Y08060 ,100000.00, ,",),
    "icdm": ("INE0K0Y08060, Industrial Finance ,7.4000,18/06/2031,96.9886,INE0K0Y08060,100000.00",),
    "wdm": ("400536,667GOI50   ,6.67 GOI BOND 2050,6.67,89.36,17/12/2050",),
}


def _write_debt_zip(path, rows=None, headers=None, missing=None, date="16102026"):
    rows = {**DEBT_ROWS, **(rows or {})}
    headers = {**DEBT_HEADERS, **(headers or {})}
    with ZipFile(path, "w") as archive:
        for family in DEBT_HEADERS:
            if family != missing:
                archive.writestr(f"{family}{date}.csv", "\n".join((headers[family], *rows[family])) + "\n")
    return path


def _read_debt(db_path):
    with sqlite3.connect(db_path) as con:
        return con.execute(
            f"SELECT {', '.join(BSE_DEBT_COLUMNS)} FROM {BSE_DEBT_TABLE} ORDER BY family, scrip_code, isin"
        ).fetchall()


def test_load_bse_debt_maps_families_and_keeps_source_numbers(tmp_path) -> None:
    debt = load_bse_debt(_write_debt_zip(tmp_path / "debt.zip"))

    assert list(debt.columns) == list(BSE_DEBT_COLUMNS)
    assert debt.to_dict("records") == [
        {
            "family": "fgroup",
            "trade_date": "2026-10-16",
            "scrip_code": "978034",
            "symbol": "0MCIBPL28",
            "isin": "INE0K0Y08060",
            "name": None,
            "coupon_pct": None,
            "maturity_date": None,
            "face_value": "100000.00",
        },
        {
            "family": "icdm",
            "trade_date": "2026-10-16",
            "scrip_code": None,
            "symbol": None,
            "isin": "INE0K0Y08060",
            "name": "Industrial Finance",
            "coupon_pct": "7.4000",
            "maturity_date": "2031-06-18",
            "face_value": "100000.00",
        },
        {
            "family": "wdm",
            "trade_date": "2026-10-16",
            "scrip_code": "400536",
            "symbol": "667GOI50",
            "isin": None,
            "name": "6.67 GOI BOND 2050",
            "coupon_pct": "6.67",
            "maturity_date": "2050-12-17",
            "face_value": None,
        },
    ]


def test_load_bse_debt_drops_blank_rows_and_icdm_note(tmp_path) -> None:
    rows = {
        family: (*records, "", "," * (len(DEBT_HEADERS[family].split(",")) - 1))
        for family, records in DEBT_ROWS.items()
    }
    rows["icdm"] += ('NOTE:-"Trading price indicates price in per 100 basis"',)

    assert len(load_bse_debt(_write_debt_zip(tmp_path / "debt.zip", rows=rows))) == 3


@pytest.mark.parametrize("family", DEBT_HEADERS)
def test_load_bse_debt_rejects_missing_columns(tmp_path, family) -> None:
    path = _write_debt_zip(tmp_path / "debt.zip", headers={family: "unexpected"}, rows={family: ("value",)})

    with pytest.raises(ValueError, match=rf"{family}16102026.csv.*missing required columns"):
        load_bse_debt(path)


@pytest.mark.parametrize("family", DEBT_HEADERS)
def test_load_bse_debt_rejects_missing_member(tmp_path, family) -> None:
    path = _write_debt_zip(tmp_path / "debt.zip", missing=family)

    with pytest.raises(ValueError, match=rf"missing required member: {family}"):
        load_bse_debt(path)


@pytest.mark.parametrize("family", DEBT_HEADERS)
def test_load_bse_debt_rejects_duplicate_identifiers(tmp_path, family) -> None:
    path = _write_debt_zip(tmp_path / "debt.zip", rows={family: DEBT_ROWS[family] * 2})

    with pytest.raises(ValueError, match=rf"{family}16102026.csv.*more than once"):
        load_bse_debt(path)


@pytest.mark.parametrize("family", DEBT_HEADERS)
def test_load_bse_debt_rejects_empty_identifier(tmp_path, family) -> None:
    row = next(csv.reader(io.StringIO(DEBT_ROWS[family][0])))
    row[5 if family == "icdm" else 0] = " "
    path = _write_debt_zip(tmp_path / "debt.zip", rows={family: (",".join(row),)})

    with pytest.raises(ValueError, match=rf"{family}16102026.csv.*without a"):
        load_bse_debt(path)


def test_load_bse_debt_reports_missing_file(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="BSE debt bhavcopy not found"):
        load_bse_debt(tmp_path / "absent.zip")


def test_load_bse_debt_converts_fgroup_date_and_null_optional_cells(tmp_path) -> None:
    path = _write_debt_zip(
        tmp_path / "debt.zip",
        rows={
            "fgroup": ("978034,0MCIBPL28,101989.10,INE0K0Y08060,100000.00,7.00,01/04/2028",),
            "icdm": ("INE0K0Y08060,Industrial Finance,,,96.9886,INE0K0Y08060,",),
            "wdm": ("400536,667GOI50,6.67 GOI BOND 2050,,89.36,",),
        },
    )
    debt = load_bse_debt(path).set_index("family")

    assert debt.loc["fgroup", "coupon_pct"] == "7.00"
    assert debt.loc["fgroup", "maturity_date"] == "2028-04-01"
    assert debt.loc["icdm", "face_value"] is None
    for family in ("icdm", "wdm"):
        assert debt.loc[family, "coupon_pct"] is None
        assert debt.loc[family, "maturity_date"] is None


def test_load_bse_debt_uses_member_trade_date(tmp_path) -> None:
    path = _write_debt_zip(tmp_path / "DEBTBHAVCOPY01012027.zip", date="31122026")

    assert load_bse_debt(path)["trade_date"].tolist() == ["2026-12-31"] * 3


def test_load_bse_debt_rejects_invalid_maturity_date(tmp_path) -> None:
    path = _write_debt_zip(
        tmp_path / "debt.zip", rows={"wdm": (DEBT_ROWS["wdm"][0].replace("17/12/2050", "31/02/2050"),)}
    )

    with pytest.raises(ValueError, match="wdm16102026.csv.*invalid date"):
        load_bse_debt(path)


def test_newest_bse_debt_archive_uses_filename_date(tmp_path) -> None:
    _write_debt_zip(tmp_path / "DEBTBHAVCOPY31122025.zip")
    newest = _write_debt_zip(tmp_path / "DEBTBHAVCOPY01012026.zip")
    _write_debt_zip(tmp_path / "DEBTBHAVCOPY16102025.zip")
    (tmp_path / "BSE-DE-311221.csv").write_text("legacy", encoding="utf-8")
    (tmp_path / "DEBTBHAVCOPY31132026.zip").touch()
    (tmp_path / "DEBTBHAVCOPYinvalid.zip").touch()
    (tmp_path / "DEBTBHAVCOPY31122027.zip").mkdir()

    assert newest_bse_debt_archive(tmp_path) == newest


def test_newest_bse_debt_archive_reports_missing_archive(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="BSE debt bhavcopy archives not found"):
        newest_bse_debt_archive(tmp_path)


def test_import_bse_debt_leaves_consolidated_untouched(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)
    with sqlite3.connect(db_path) as con:
        before = con.execute(f"SELECT * FROM {CONSOLIDATED_TABLE} ORDER BY isin").fetchall()

    result = import_bse_debt(_write_debt_zip(tmp_path / "debt.zip"), db_path)

    assert result.rows == 3
    assert len(_read_debt(db_path)) == 3
    with sqlite3.connect(db_path) as con:
        assert con.execute(f"SELECT * FROM {CONSOLIDATED_TABLE} ORDER BY isin").fetchall() == before
        assert [(row[1], row[2]) for row in con.execute(f"PRAGMA table_info({BSE_DEBT_TABLE})")] == [
            (column, "TEXT") for column in BSE_DEBT_COLUMNS
        ]
        assert {row[1] for row in con.execute(f"PRAGMA index_list({BSE_DEBT_TABLE})")} == {
            "idx_bse_debt_isin",
            "idx_bse_debt_scrip_code",
        }


def test_import_bse_debt_replaces_previous_rows(tmp_path) -> None:
    db_path = tmp_path / "stocky.db"
    path = _write_debt_zip(tmp_path / "debt.zip")
    import_bse_debt(path, db_path)

    import_bse_debt(_write_debt_zip(path, rows={"fgroup": ()}), db_path)

    assert [row[0] for row in _read_debt(db_path)] == ["icdm", "wdm"]


def test_failed_import_keeps_previous_table(tmp_path) -> None:
    db_path = tmp_path / "stocky.db"
    import_bse_debt(_write_debt_zip(tmp_path / "debt.zip"), db_path)
    before = _read_debt(db_path)
    path = _write_debt_zip(tmp_path / "bad.zip", rows={"wdm": DEBT_ROWS["wdm"] * 2})

    with pytest.raises(ValueError):
        import_bse_debt(path, db_path)

    assert _read_debt(db_path) == before


def test_failed_write_rolls_back_previous_table(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "stocky.db"
    path = _write_debt_zip(tmp_path / "debt.zip")
    import_bse_debt(path, db_path)
    before = _read_debt(db_path)

    class FailingConnection(sqlite3.Connection):
        def executemany(self, sql, parameters):
            super().executemany(sql, parameters)
            raise sqlite3.OperationalError("write failed")

    monkeypatch.setattr(bse_debt, "connect", lambda path: sqlite3.connect(path, factory=FailingConnection))
    with pytest.raises(sqlite3.OperationalError, match="write failed"):
        import_bse_debt(_write_debt_zip(path, rows={"fgroup": ()}), db_path)

    assert _read_debt(db_path) == before
    with sqlite3.connect(db_path) as con:
        assert len(con.execute(f"PRAGMA index_list({BSE_DEBT_TABLE})").fetchall()) == 2


def test_search_bse_debt_matches_fragments_and_exact_identifiers(tmp_path) -> None:
    db_path = tmp_path / "stocky.db"
    import_bse_debt(_write_debt_zip(tmp_path / "debt.zip"), db_path)

    assert search_bse_debt("industrial", db_path).matches[0].family == "icdm"
    assert search_bse_debt("978", db_path).matches[0].family == "fgroup"
    assert search_bse_debt("goi", db_path).matches[0].family == "wdm"
    assert search_bse_debt("ine0k0y08060", db_path, exact=True).total == 2
    assert search_bse_debt("978034", db_path, exact=True).matches[0].family == "fgroup"
    assert search_bse_debt("667goi50", db_path, exact=True).matches[0].family == "wdm"
    assert search_bse_debt("Industrial Finance", db_path, exact=True).total == 0
    assert search_bse_debt("GOI", db_path, exact=True).total == 0


def test_search_bse_debt_ranks_exact_matches_first_and_limits(tmp_path) -> None:
    db_path = tmp_path / "stocky.db"
    path = _write_debt_zip(
        tmp_path / "debt.zip", rows={"icdm": (DEBT_ROWS["icdm"][0].replace("Industrial Finance", "667GOI50"),)}
    )
    import_bse_debt(path, db_path)

    result = search_bse_debt("667GOI50", db_path, limit=1)

    assert result.total == 2
    assert len(result.matches) == 1
    assert result.matches[0].family == "wdm"
    assert [match.family for match in search_bse_debt("INE", db_path).matches] == ["fgroup", "icdm"]


def test_search_bse_debt_treats_like_wildcards_literally(tmp_path) -> None:
    db_path = tmp_path / "stocky.db"
    import_bse_debt(_write_debt_zip(tmp_path / "debt.zip"), db_path)

    assert search_bse_debt("%", db_path).total == 0
    assert search_bse_debt("_", db_path).total == 0


def test_search_bse_debt_requires_imported_table(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    with pytest.raises(RuntimeError, match="Run 'stocky debt import' first"):
        search_bse_debt("GOI", db_path)
    with pytest.raises(FileNotFoundError, match="Database not found"):
        search_bse_debt("GOI", tmp_path / "absent.db")
    with pytest.raises(ValueError, match="must not be empty"):
        search_bse_debt("  ", db_path)
    with pytest.raises(ValueError, match="at least 1"):
        search_bse_debt("GOI", db_path, limit=0)


def test_rebuild_keeps_bse_debt_table(tmp_path, market_csv_builder) -> None:
    db_path = tmp_path / "stocky.db"
    import_bse_debt(_write_debt_zip(tmp_path / "debt.zip"), db_path)
    before = _read_debt(db_path)

    rebuild_database(market_csv_builder(tmp_path / "inputs"), db_path=db_path, backup=False)

    assert _read_debt(db_path) == before
