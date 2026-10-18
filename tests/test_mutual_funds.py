import json
import sqlite3

import pytest

from stocky.cli import app
from stocky.database import CONSOLIDATED_TABLE
from stocky.mutual_funds import (
    MUTUAL_FUND_COLUMNS,
    MUTUAL_FUNDS_TABLE,
    import_mutual_funds,
    load_amfi_codes,
    load_mf_instruments,
    search_mutual_funds,
)
from stocky.pipeline import rebuild_database

MF_HEADER = (
    "tradingsymbol,amc,name,purchase_allowed,redemption_allowed,minimum_purchase_amount,purchase_amount_multiplier,"
    "minimum_additional_purchase_amount,minimum_redemption_quantity,redemption_quantity_multiplier,dividend_type,"
    "scheme_type,plan,settlement_type,last_price,last_price_date"
)
MF_ROWS = (
    "INF179K01KG8,HDFCMutualFund_MF,HDFC Liquid Fund,0,0,5000.0,0.01,1000.0,5.0,0.001,growth,Debt,regular,T1,"
    "4020.0875,2021-04-07",
    "INF179K01WT6,HDFCMutualFund_MF,HDFC Liquid Fund - Direct Plan,0,0,10000.0,0.01,5000.0,0.001,0.001,growth,Debt,"
    "direct,T1,4048.011,2021-04-07",
    "INF209K01YN0, ABSLMutualFund_MF ,Aditya Birla Sun Life Tax Relief 96,1,1,500.0,1.0,500.0,0.001,0.001,payout,"
    "Equity,regular,T3,150.2,2021-04-08",
)


def _write_mf_csv(path, rows=MF_ROWS, header=MF_HEADER):
    path.write_text("\n".join((header, *rows)) + "\n", encoding="utf-8")
    return path


AMFI_HEADER = (
    "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date"
)
AMFI_ROWS = ("100001;INF179K01KG8;INF179K01WT6;HDFC Liquid Fund;Regular;Growth;100;18-Oct-2026",)


def _write_amfi(path, rows=AMFI_ROWS):
    path.write_text("\n".join((AMFI_HEADER, *rows)) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def amfi_path(tmp_path):
    return _write_amfi(tmp_path / "NAVAll.txt")


def _read_funds(db_path):
    with sqlite3.connect(db_path) as con:
        return con.execute(
            f"SELECT {', '.join(MUTUAL_FUND_COLUMNS)} FROM {MUTUAL_FUNDS_TABLE} ORDER BY isin"
        ).fetchall()


def test_load_mf_instruments_maps_isin_and_zerodha_identifier(tmp_path) -> None:
    funds = load_mf_instruments(_write_mf_csv(tmp_path / "mf.csv"))

    assert list(funds.columns) == list(MUTUAL_FUND_COLUMNS)
    assert funds.to_dict("records")[0] == {
        "isin": "INF179K01KG8",
        "zd_mf": "INF179K01KG8",
        "amfi_code": None,
        "name": "HDFC Liquid Fund",
        "amc": "HDFCMutualFund_MF",
        "scheme_type": "Debt",
        "plan": "regular",
        "dividend_type": "growth",
    }
    assert funds.loc[funds["isin"] == "INF209K01YN0", "amc"].item() == "ABSLMutualFund_MF"


def test_load_mf_instruments_rejects_missing_columns(tmp_path) -> None:
    path = _write_mf_csv(tmp_path / "mf.csv", rows=("INF179K01KG8,HDFC Liquid Fund",), header="tradingsymbol,name")

    with pytest.raises(ValueError, match="missing required columns: amc, dividend_type, plan, scheme_type"):
        load_mf_instruments(path)


def test_load_mf_instruments_rejects_duplicate_tradingsymbols(tmp_path) -> None:
    path = _write_mf_csv(tmp_path / "mf.csv", rows=(MF_ROWS[0], MF_ROWS[0]))

    with pytest.raises(ValueError, match="more than once: INF179K01KG8"):
        load_mf_instruments(path)


def test_load_mf_instruments_rejects_blank_tradingsymbol(tmp_path) -> None:
    path = _write_mf_csv(tmp_path / "mf.csv", rows=(MF_ROWS[0], "," + MF_ROWS[1].split(",", 1)[1]))

    with pytest.raises(ValueError, match="without a tradingsymbol"):
        load_mf_instruments(path)


def test_load_mf_instruments_reports_missing_file(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="mutual fund instruments not found"):
        load_mf_instruments(tmp_path / "absent.csv")


def test_load_amfi_codes_skips_headers_sections_and_invalid_rows(tmp_path) -> None:
    path = _write_amfi(
        tmp_path / "NAVAll.txt",
        rows=(
            "",
            "Open Ended Schemes (Debt)",
            "HDFC Mutual Fund",
            *AMFI_ROWS,
            *AMFI_ROWS,
            "not-a-code;INF209K01YN0;-;Fund;Regular;Growth;100;18-Oct-2026",
            "100002;INF209K01YN0;-;wrong field count",
        ),
    )

    codes, ambiguous = load_amfi_codes(path)

    assert codes == {"INF179K01KG8": "100001", "INF179K01WT6": "100001"}
    assert ambiguous == set()


def test_load_amfi_codes_skips_non_isin_values(tmp_path) -> None:
    path = _write_amfi(
        tmp_path / "NAVAll.txt",
        rows=(
            "100001;-;;Fund;Regular;Growth;100;18-Oct-2026",
            "100002;Redeemed;HDFCNIVODG;Fund;Regular;Growth;100;18-Oct-2026",
            "100003;inf179k01kg8;INF179K01KG88;Fund;Regular;Growth;100;18-Oct-2026",
        ),
    )

    assert load_amfi_codes(path) == ({}, set())


def test_load_amfi_codes_excludes_ambiguous_isins_regardless_of_order(tmp_path) -> None:
    rows = (
        *AMFI_ROWS,
        "100002;INF179K01KG8;INF000000001;Other Fund;Direct;Growth;100;18-Oct-2026",
        "100003;INF000000001;-;Another Fund;Direct;Growth;100;18-Oct-2026",
        *AMFI_ROWS,
    )

    for ordered_rows in (rows, tuple(reversed(rows))):
        codes, ambiguous = load_amfi_codes(_write_amfi(tmp_path / "NAVAll.txt", rows=ordered_rows))

        assert codes == {"INF179K01WT6": "100001"}
        assert ambiguous == {"INF179K01KG8", "INF000000001"}


def test_load_amfi_codes_reports_missing_file(tmp_path) -> None:
    path = tmp_path / "absent.txt"

    with pytest.raises(ValueError, match="AMFI NAV file not found") as error:
        load_amfi_codes(path)

    assert str(path) in str(error.value)
    assert "https://www.amfiindia.com/spages/NAVAll.txt" in str(error.value)


def test_load_amfi_codes_rejects_file_without_scheme_rows(tmp_path) -> None:
    path = _write_amfi(tmp_path / "NAVAll.txt", rows=("", "HDFC Mutual Fund"))

    with pytest.raises(ValueError, match="has no scheme rows"):
        load_amfi_codes(path)


def test_import_mutual_funds_leaves_unmatched_and_ambiguous_codes_null(tmp_path) -> None:
    db_path = tmp_path / "stocky.db"
    amfi_path = _write_amfi(
        tmp_path / "NAVAll.txt",
        rows=(
            *AMFI_ROWS,
            "100002;INF179K01KG8;INF000000001;Other Fund;Direct;Growth;100;18-Oct-2026",
            "100003;INF000000001;-;Another Fund;Direct;Growth;100;18-Oct-2026",
        ),
    )

    result = import_mutual_funds(_write_mf_csv(tmp_path / "mf.csv"), amfi_path, db_path)

    assert result.rows == 3
    assert result.coded_rows == 1
    assert result.ambiguous_isins == ["INF179K01KG8"]
    assert [(row[0], row[2]) for row in _read_funds(db_path)] == [
        ("INF179K01KG8", None),
        ("INF179K01WT6", "100001"),
        ("INF209K01YN0", None),
    ]


def test_failed_amfi_import_keeps_previous_table(tmp_path, amfi_path) -> None:
    db_path = tmp_path / "stocky.db"
    mf_path = _write_mf_csv(tmp_path / "mf.csv")
    import_mutual_funds(mf_path, amfi_path, db_path)
    before = _read_funds(db_path)

    for invalid_path in (tmp_path / "absent.txt", _write_amfi(tmp_path / "empty.txt", rows=())):
        with pytest.raises(ValueError):
            import_mutual_funds(mf_path, invalid_path, db_path)

        assert _read_funds(db_path) == before


def test_search_mutual_funds_matches_amfi_codes_exactly(tmp_path, amfi_path) -> None:
    db_path = tmp_path / "stocky.db"
    import_mutual_funds(_write_mf_csv(tmp_path / "mf.csv"), amfi_path, db_path)

    for exact in (False, True):
        result = search_mutual_funds("100001", db_path, exact=exact)

        assert result.total == 2
        assert [match.isin for match in result.matches] == ["INF179K01KG8", "INF179K01WT6"]
        assert all(match.amfi_code == "100001" for match in result.matches)
        assert search_mutual_funds("10000", db_path, exact=exact).total == 0


def test_import_mutual_funds_leaves_consolidated_untouched(tmp_path, seed_consolidated, amfi_path) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)
    with sqlite3.connect(db_path) as con:
        before = con.execute(f"SELECT * FROM {CONSOLIDATED_TABLE} ORDER BY isin").fetchall()

    result = import_mutual_funds(_write_mf_csv(tmp_path / "mf.csv"), amfi_path, db_path)

    assert result.rows == 3
    assert result.coded_rows == 2
    assert result.ambiguous_isins == []
    assert [row[0] for row in _read_funds(db_path)] == ["INF179K01KG8", "INF179K01WT6", "INF209K01YN0"]
    with sqlite3.connect(db_path) as con:
        assert con.execute(f"SELECT * FROM {CONSOLIDATED_TABLE} ORDER BY isin").fetchall() == before
        primary_key = [row[1] for row in con.execute(f"PRAGMA table_info({MUTUAL_FUNDS_TABLE})") if row[5]]
    assert primary_key == ["isin"]


def test_import_mutual_funds_replaces_previous_rows(tmp_path, amfi_path) -> None:
    db_path = tmp_path / "stocky.db"
    import_mutual_funds(_write_mf_csv(tmp_path / "mf.csv"), amfi_path, db_path)

    import_mutual_funds(_write_mf_csv(tmp_path / "mf.csv", rows=MF_ROWS[:1]), amfi_path, db_path)

    assert [row[0] for row in _read_funds(db_path)] == ["INF179K01KG8"]


def test_failed_import_keeps_previous_table(tmp_path, amfi_path) -> None:
    db_path = tmp_path / "stocky.db"
    import_mutual_funds(_write_mf_csv(tmp_path / "mf.csv"), amfi_path, db_path)

    with pytest.raises(ValueError):
        import_mutual_funds(_write_mf_csv(tmp_path / "bad.csv", rows=(MF_ROWS[0], MF_ROWS[0])), amfi_path, db_path)

    assert len(_read_funds(db_path)) == 3


def test_search_mutual_funds_matches_fragments_and_exact_identifiers(tmp_path, amfi_path) -> None:
    db_path = tmp_path / "stocky.db"
    import_mutual_funds(_write_mf_csv(tmp_path / "mf.csv"), amfi_path, db_path)

    by_name = search_mutual_funds("liquid", db_path)
    by_amc = search_mutual_funds("abslmutual", db_path)
    by_isin = search_mutual_funds("inf179k01wt6", db_path, exact=True)
    partial_exact = search_mutual_funds("HDFC", db_path, exact=True)

    assert [match.isin for match in by_name.matches] == ["INF179K01KG8", "INF179K01WT6"]
    assert [match.isin for match in by_amc.matches] == ["INF209K01YN0"]
    assert by_isin.total == 1
    assert by_isin.matches[0].name == "HDFC Liquid Fund - Direct Plan"
    assert partial_exact.total == 0


def test_search_mutual_funds_ranks_exact_matches_first_and_limits(tmp_path, amfi_path) -> None:
    db_path = tmp_path / "stocky.db"
    import_mutual_funds(_write_mf_csv(tmp_path / "mf.csv"), amfi_path, db_path)

    result = search_mutual_funds("HDFC Liquid Fund - Direct Plan", db_path, limit=1)
    limited = search_mutual_funds("INF", db_path, limit=2)

    assert result.matches[0].isin == "INF179K01WT6"
    assert limited.total == 3
    assert len(limited.matches) == 2


def test_search_mutual_funds_treats_like_wildcards_literally(tmp_path, amfi_path) -> None:
    db_path = tmp_path / "stocky.db"
    import_mutual_funds(_write_mf_csv(tmp_path / "mf.csv"), amfi_path, db_path)

    assert search_mutual_funds("%", db_path).total == 0
    assert search_mutual_funds("Fund_MF", db_path).total == 3


def test_search_mutual_funds_requires_imported_table(tmp_path, seed_consolidated) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    with pytest.raises(RuntimeError, match="Run 'stocky mf import' first"):
        search_mutual_funds("HDFC", db_path)
    with pytest.raises(FileNotFoundError, match="Database not found"):
        search_mutual_funds("HDFC", tmp_path / "absent.db")
    with pytest.raises(ValueError, match="must not be empty"):
        search_mutual_funds("  ", db_path)
    with pytest.raises(ValueError, match="at least 1"):
        search_mutual_funds("HDFC", db_path, limit=0)


def test_mf_import_and_query_commands(tmp_path, monkeypatch, runner, amfi_path) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    db_path = tmp_path / "stocky.db"
    mf_path = _write_mf_csv(tmp_path / "mf.csv")

    imported = runner.invoke(
        app, ["mf", "import", "--amfi", str(amfi_path), "--mf-instruments", str(mf_path), "--db-path", str(db_path)]
    )
    queried = runner.invoke(app, ["mf", "query", "liquid", "--limit", "1", "--db-path", str(db_path)])

    assert imported.exit_code == 0
    assert "Imported 3 mutual funds" in imported.stdout
    assert queried.exit_code == 0
    assert "Mutual funds matching 'liquid'" in queried.stdout
    assert "INF179K01KG8" in queried.stdout
    assert "INF179K01WT6" not in queried.stdout
    assert "Showing 1 of 2 matches" in queried.stdout


def test_mf_commands_print_json(tmp_path, runner, amfi_path) -> None:
    db_path = tmp_path / "stocky.db"
    mf_path = _write_mf_csv(tmp_path / "mf.csv")

    imported = runner.invoke(
        app,
        [
            "mf",
            "import",
            "--amfi",
            str(amfi_path),
            "--mf-instruments",
            str(mf_path),
            "--db-path",
            str(db_path),
            "--json",
        ],
    )
    queried = runner.invoke(app, ["mf", "query", "INF209K01YN0", "--exact", "--db-path", str(db_path), "--json"])

    assert imported.exit_code == 0
    assert json.loads(imported.stdout) == {
        "rows": 3,
        "coded_rows": 2,
        "ambiguous_isins": [],
        "db_path": str(db_path),
        "mf_instruments": str(mf_path),
        "amfi": str(amfi_path),
    }
    assert queried.exit_code == 0
    payload = json.loads(queried.stdout)
    assert payload["term"] == "INF209K01YN0"
    assert payload["total"] == 1
    assert payload["matches"][0]["zd_mf"] == "INF209K01YN0"
    assert payload["matches"][0]["amfi_code"] is None
    assert payload["matches"][0]["scheme_type"] == "Equity"


def test_mf_query_reports_no_match_and_missing_table(tmp_path, seed_consolidated, runner, amfi_path) -> None:
    db_path = tmp_path / "stocky.db"
    seed_consolidated(db_path)

    missing_table = runner.invoke(app, ["mf", "query", "HDFC", "--db-path", str(db_path), "--json"])
    import_mutual_funds(_write_mf_csv(tmp_path / "mf.csv"), amfi_path, db_path)
    no_match = runner.invoke(app, ["mf", "query", "nothing-like-this", "--db-path", str(db_path)])

    assert missing_table.exit_code == 1
    assert missing_table.stdout == ""
    assert "stocky mf import" in missing_table.stderr
    assert no_match.exit_code == 0
    assert "No mutual funds match 'nothing-like-this'" in no_match.stdout


def test_mf_import_reports_missing_file(tmp_path, runner, amfi_path) -> None:
    result = runner.invoke(
        app,
        [
            "mf",
            "import",
            "--amfi",
            str(amfi_path),
            "--mf-instruments",
            str(tmp_path / "absent.csv"),
            "--db-path",
            str(tmp_path / "x.db"),
        ],
    )

    assert result.exit_code == 1
    assert "mutual fund instruments not found" in result.stdout


def test_rebuild_keeps_mutual_funds_table(tmp_path, market_csv_builder, amfi_path) -> None:
    db_path = tmp_path / "stocky.db"
    import_mutual_funds(_write_mf_csv(tmp_path / "mf.csv"), amfi_path, db_path)

    rebuild_database(market_csv_builder(tmp_path / "inputs"), db_path=db_path, backup=False)

    assert len(_read_funds(db_path)) == 3
