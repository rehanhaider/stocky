from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from stocky.config import DEFAULT_BSE_DEBT_DIR, DEFAULT_DB_PATH
from stocky.database import _escape_like, connect, table_exists

BSE_DEBT_TABLE = "bse_debt"
BSE_DEBT_COLUMNS = (
    "family",
    "trade_date",
    "scrip_code",
    "symbol",
    "isin",
    "name",
    "coupon_pct",
    "maturity_date",
    "face_value",
)
# The families use different identifiers; an ISIN can appear in both fgroup and icdm.
DEBT_SOURCE_COLUMNS = {
    "fgroup": {
        "Security_cd": "scrip_code",
        "sc_name": "symbol",
        "ISIN No.": "isin",
        "COUP0N (%)": "coupon_pct",
        "Maturity Date": "maturity_date",
        "FACE VALUE": "face_value",
    },
    "icdm": {
        "ISIN No.": "isin",
        "Issuer Name": "name",
        "Coupon (%)": "coupon_pct",
        "Maturity Date": "maturity_date",
        "Face Value": "face_value",
    },
    "wdm": {
        "Scrip Code": "scrip_code",
        "Security Code": "symbol",
        "Security Description": "name",
        "Coupon Rate (%)": "coupon_pct",
        "MaturityDate": "maturity_date",
    },
}
EXACT_COLUMNS = ("isin", "scrip_code", "symbol")
FRAGMENT_COLUMNS = (*EXACT_COLUMNS, "name")


@dataclass(frozen=True)
class BseDebtImportResult:
    rows: int
    db_path: Path
    file: Path


@dataclass(frozen=True)
class BseDebtMatch:
    family: str
    trade_date: str
    scrip_code: str | None
    symbol: str | None
    isin: str | None
    name: str | None
    coupon_pct: str | None
    maturity_date: str | None
    face_value: str | None


@dataclass(frozen=True)
class BseDebtSearchResult:
    matches: list[BseDebtMatch]
    total: int


def newest_bse_debt_archive(input_dir: Path = DEFAULT_BSE_DEBT_DIR) -> Path:
    """Find the newest debt archive by the trade date in its name."""
    archives = []
    for path in input_dir.glob("DEBTBHAVCOPY*.zip"):
        match = re.fullmatch(r"DEBTBHAVCOPY(\d{8})\.zip", path.name)
        if not path.is_file() or match is None:
            continue
        try:
            trade_date = datetime.strptime(match[1], "%d%m%Y").date()
        except ValueError:
            continue
        archives.append((trade_date, path))
    if not archives:
        raise FileNotFoundError(f"BSE debt bhavcopy archives not found: {input_dir}")
    return max(archives)[1]


def load_bse_debt(path: Path) -> pd.DataFrame:
    """Read the three debt families directly from a BSE bhavcopy zip."""
    if not path.is_file():
        raise FileNotFoundError(f"BSE debt bhavcopy not found: {path}")

    families = []
    with ZipFile(path) as archive:
        for family, mapping in DEBT_SOURCE_COLUMNS.items():
            members = [name for name in archive.namelist() if re.fullmatch(rf"{family}\d{{8}}\.csv", name)]
            if not members:
                raise ValueError(f"BSE debt bhavcopy ({path}) is missing required member: {family}<DDMMYYYY>.csv")
            if len(members) != 1:
                raise ValueError(f"BSE debt bhavcopy ({path}) lists {family} members more than once.")
            member = members[0]
            with archive.open(member) as source:
                instruments = pd.read_csv(source, dtype=str, keep_default_na=False)
            required = set(mapping) | ({"Security Code"} if family == "icdm" else set())
            missing = sorted(required.difference(instruments.columns))
            if missing:
                raise ValueError(f"BSE debt bhavcopy ({member}) is missing required columns: {', '.join(missing)}")

            instruments = instruments.apply(lambda column: column.str.strip())
            instruments = instruments.loc[instruments.ne("").any(axis=1)]
            if family == "icdm":
                note = instruments["Security Code"].eq('NOTE:-"Trading price indicates price in per 100 basis"')
                note &= instruments.drop(columns="Security Code").eq("").all(axis=1)
                instruments = instruments.loc[~note]
            debt = instruments[list(mapping)].rename(columns=mapping)
            identifier = "isin" if family == "icdm" else "scrip_code"
            if (debt[identifier] == "").any():
                raise ValueError(f"BSE debt bhavcopy ({member}) has rows without a {identifier}.")
            duplicated = sorted(set(debt.loc[debt[identifier].duplicated(), identifier]))
            if duplicated:
                raise ValueError(
                    f"BSE debt bhavcopy ({member}) lists these {identifier}s more than once: "
                    f"{', '.join(duplicated[:10])}"
                )

            debt = debt.replace({"": None})
            try:
                debt["trade_date"] = datetime.strptime(member[len(family) : -4], "%d%m%Y").date().isoformat()
                debt["maturity_date"] = debt["maturity_date"].map(
                    lambda value: datetime.strptime(value, "%d/%m/%Y").date().isoformat() if value else None
                )
            except ValueError as exc:
                raise ValueError(f"BSE debt bhavcopy ({member}) has an invalid date: {exc}") from exc
            debt["family"] = family
            for column in BSE_DEBT_COLUMNS:
                if column not in debt:
                    debt[column] = None
            families.append(debt[list(BSE_DEBT_COLUMNS)])

    return pd.concat(families, ignore_index=True).sort_values(["family", "isin", "scrip_code"]).reset_index(drop=True)


def import_bse_debt(path: Path, db_path: Path = DEFAULT_DB_PATH) -> BseDebtImportResult:
    """Replace the `bse_debt` table with one archive; other tables stay as they are."""
    debt = load_bse_debt(path)
    column_definitions = ", ".join(f"{column} TEXT" for column in BSE_DEBT_COLUMNS)
    placeholders = ", ".join("?" for _ in BSE_DEBT_COLUMNS)

    with connect(db_path) as con:
        # One transaction, so a failed write leaves the previous table in place.
        con.execute("BEGIN")
        con.execute(f"DROP TABLE IF EXISTS {BSE_DEBT_TABLE}")
        con.execute(f"CREATE TABLE {BSE_DEBT_TABLE} ({column_definitions})")
        con.executemany(
            f"INSERT INTO {BSE_DEBT_TABLE} ({', '.join(BSE_DEBT_COLUMNS)}) VALUES ({placeholders})",
            debt.itertuples(index=False, name=None),
        )
        for column in ("isin", "scrip_code"):
            con.execute(f"CREATE INDEX idx_{BSE_DEBT_TABLE}_{column} ON {BSE_DEBT_TABLE} ({column})")

    return BseDebtImportResult(rows=len(debt), db_path=db_path, file=path)


def search_bse_debt(
    term: str,
    db_path: Path = DEFAULT_DB_PATH,
    *,
    limit: int = 20,
    exact: bool = False,
) -> BseDebtSearchResult:
    cleaned = term.strip()
    if not cleaned:
        raise ValueError("Search term must not be empty.")
    if limit < 1:
        raise ValueError("Limit must be at least 1.")

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}. Run 'stocky debt import' first.")

    equality = " OR ".join(f"UPPER({column}) = :exact" for column in EXACT_COLUMNS)
    where = f"({equality})"
    params: dict[str, object] = {"exact": cleaned.upper()}
    if not exact:
        fragment = " OR ".join(f"UPPER({column}) LIKE :fragment ESCAPE '\\'" for column in FRAGMENT_COLUMNS)
        where = f"({equality} OR {fragment})"
        params["fragment"] = f"%{_escape_like(cleaned.upper())}%"

    with connect(db_path) as con:
        if not table_exists(con, BSE_DEBT_TABLE):
            raise RuntimeError(
                f"Database table '{BSE_DEBT_TABLE}' does not exist in {db_path}. Run 'stocky debt import' first."
            )

        total = con.execute(f"SELECT COUNT(*) FROM {BSE_DEBT_TABLE} WHERE {where}", params).fetchone()[0]
        rows = con.execute(
            f"""
            SELECT {", ".join(BSE_DEBT_COLUMNS)}
            FROM {BSE_DEBT_TABLE}
            WHERE {where}
            ORDER BY CASE WHEN {equality} THEN 0 ELSE 1 END, name, isin
            LIMIT :limit
            """,
            {**params, "limit": limit},
        ).fetchall()

    return BseDebtSearchResult(matches=[BseDebtMatch(*row) for row in rows], total=total)
