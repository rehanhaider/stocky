from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from stocky.config import DEFAULT_DB_PATH
from stocky.database import _escape_like, connect, table_exists

MUTUAL_FUNDS_TABLE = "mutual_funds"

# Zerodha's mf_instruments.csv names each scheme option by its ISIN in `tradingsymbol`, the key Kite's
# mutual fund orders take, so `zd_mf` keeps that value and `isin` keeps it as the shared identifier.
MUTUAL_FUND_COLUMNS = ("isin", "zd_mf", "amfi_code", "name", "amc", "scheme_type", "plan", "dividend_type")
MF_SOURCE_COLUMNS = {"tradingsymbol", "name", "amc", "scheme_type", "plan", "dividend_type"}
EXACT_COLUMNS = ("isin", "zd_mf", "amfi_code", "name")
FRAGMENT_COLUMNS = ("isin", "zd_mf", "name", "amc")


@dataclass(frozen=True)
class MutualFundImportResult:
    rows: int
    coded_rows: int
    ambiguous_isins: list[str]
    db_path: Path
    mf_instruments: Path
    amfi: Path


@dataclass(frozen=True)
class MutualFundMatch:
    isin: str
    zd_mf: str
    amfi_code: str | None
    name: str | None
    amc: str | None
    scheme_type: str | None
    plan: str | None
    dividend_type: str | None


@dataclass(frozen=True)
class MutualFundSearchResult:
    matches: list[MutualFundMatch]
    total: int


def load_mf_instruments(path: Path) -> pd.DataFrame:
    """Read Zerodha's mutual fund instruments into one row per ISIN with the `mutual_funds` columns."""
    if not path.is_file():
        raise FileNotFoundError(f"Zerodha mutual fund instruments not found: {path}")

    instruments = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = sorted(MF_SOURCE_COLUMNS.difference(instruments.columns))
    if missing:
        raise ValueError(f"Zerodha mutual fund instruments ({path}) is missing required columns: {', '.join(missing)}")

    funds = instruments[sorted(MF_SOURCE_COLUMNS)].apply(lambda column: column.str.strip())
    if (funds["tradingsymbol"] == "").any():
        raise ValueError(f"Zerodha mutual fund instruments ({path}) has rows without a tradingsymbol.")
    duplicated = sorted(set(funds.loc[funds["tradingsymbol"].duplicated(), "tradingsymbol"]))
    if duplicated:
        raise ValueError(
            f"Zerodha mutual fund instruments ({path}) lists these tradingsymbols more than once: "
            f"{', '.join(duplicated[:10])}"
        )

    funds["isin"] = funds["tradingsymbol"]
    funds["amfi_code"] = None
    funds = funds.rename(columns={"tradingsymbol": "zd_mf"}).replace({"": None})
    return funds[list(MUTUAL_FUND_COLUMNS)].sort_values("isin").reset_index(drop=True)


def load_amfi_codes(path: Path) -> tuple[dict[str, str], set[str]]:
    """Read unambiguous ISIN-to-scheme mappings and report ISINs with more than one scheme code."""
    if not path.is_file():
        raise ValueError(
            f"AMFI NAV file not found: {path}. Download it from https://www.amfiindia.com/spages/NAVAll.txt"
        )

    codes: dict[str, set[str]] = {}
    scheme_rows = 0
    with path.open(encoding="utf-8-sig") as source:
        for line in source:
            fields = [field.strip() for field in line.split(";")]
            if len(fields) != 8 or not fields[0].isdigit():
                continue
            scheme_rows += 1
            for isin in fields[1:3]:
                if re.fullmatch(r"IN[A-Z0-9]{10}", isin):
                    codes.setdefault(isin, set()).add(fields[0])

    if not scheme_rows:
        raise ValueError(f"AMFI NAV file ({path}) has no scheme rows.")

    ambiguous = {isin for isin, scheme_codes in codes.items() if len(scheme_codes) > 1}
    return {isin: next(iter(scheme_codes)) for isin, scheme_codes in codes.items() if isin not in ambiguous}, ambiguous


def import_mutual_funds(path: Path, amfi_path: Path, db_path: Path = DEFAULT_DB_PATH) -> MutualFundImportResult:
    """Replace the `mutual_funds` table with the schemes in one Zerodha file; other tables stay as they are."""
    funds = load_mf_instruments(path)
    codes, ambiguous = load_amfi_codes(amfi_path)
    funds["amfi_code"] = funds["isin"].map(codes).replace({float("nan"): None})
    column_definitions = ", ".join(
        f"{column} TEXT PRIMARY KEY" if column == "isin" else f"{column} TEXT" for column in MUTUAL_FUND_COLUMNS
    )
    placeholders = ", ".join("?" for _ in MUTUAL_FUND_COLUMNS)

    with connect(db_path) as con:
        # One transaction, so a failed write leaves the previous table in place.
        con.execute("BEGIN")
        con.execute(f"DROP TABLE IF EXISTS {MUTUAL_FUNDS_TABLE}")
        con.execute(f"CREATE TABLE {MUTUAL_FUNDS_TABLE} ({column_definitions})")
        con.executemany(
            f"INSERT INTO {MUTUAL_FUNDS_TABLE} ({', '.join(MUTUAL_FUND_COLUMNS)}) VALUES ({placeholders})",
            funds.itertuples(index=False, name=None),
        )

    return MutualFundImportResult(
        rows=len(funds),
        coded_rows=int(funds["amfi_code"].notna().sum()),
        ambiguous_isins=sorted(ambiguous.intersection(funds["isin"])),
        db_path=db_path,
        mf_instruments=path,
        amfi=amfi_path,
    )


def search_mutual_funds(
    term: str,
    db_path: Path = DEFAULT_DB_PATH,
    *,
    limit: int = 20,
    exact: bool = False,
) -> MutualFundSearchResult:
    cleaned = term.strip()
    if not cleaned:
        raise ValueError("Search term must not be empty.")
    if limit < 1:
        raise ValueError("Limit must be at least 1.")

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}. Run 'stocky mf import' first.")

    equality = " OR ".join(f"UPPER({column}) = :exact" for column in EXACT_COLUMNS)
    where = f"({equality})"
    params: dict[str, object] = {"exact": cleaned.upper()}
    if not exact:
        fragment = " OR ".join(f"UPPER({column}) LIKE :fragment ESCAPE '\\'" for column in FRAGMENT_COLUMNS)
        where = f"({equality} OR {fragment})"
        params["fragment"] = f"%{_escape_like(cleaned.upper())}%"

    with connect(db_path) as con:
        if not table_exists(con, MUTUAL_FUNDS_TABLE):
            raise RuntimeError(
                f"Database table '{MUTUAL_FUNDS_TABLE}' does not exist in {db_path}. Run 'stocky mf import' first."
            )

        total = con.execute(f"SELECT COUNT(*) FROM {MUTUAL_FUNDS_TABLE} WHERE {where}", params).fetchone()[0]
        rows = con.execute(
            f"""
            SELECT {", ".join(MUTUAL_FUND_COLUMNS)}
            FROM {MUTUAL_FUNDS_TABLE}
            WHERE {where}
            ORDER BY CASE WHEN {equality} THEN 0 ELSE 1 END, name, isin
            LIMIT :limit
            """,
            {**params, "limit": limit},
        ).fetchall()

    return MutualFundSearchResult(matches=[MutualFundMatch(*row) for row in rows], total=total)
