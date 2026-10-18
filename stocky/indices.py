from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from stocky.config import DEFAULT_DB_PATH
from stocky.database import _escape_like, connect, table_exists

INDICES_TABLE = "indices"
INDICES_COLUMNS = ("exchange", "zd_symbol", "name", "zd_instrument_token", "exchange_token")
INDICES_SOURCE_COLUMNS = {
    "exchange": "exchange",
    "tradingsymbol": "zd_symbol",
    "name": "name",
    "instrument_token": "zd_instrument_token",
    "exchange_token": "exchange_token",
}
EXACT_COLUMNS = ("zd_symbol", "zd_instrument_token", "exchange_token")
FRAGMENT_COLUMNS = ("zd_symbol", "name")


@dataclass(frozen=True)
class IndicesImportResult:
    rows: int
    db_path: Path
    file: Path


@dataclass(frozen=True)
class IndexMatch:
    exchange: str | None
    zd_symbol: str | None
    name: str | None
    zd_instrument_token: str
    exchange_token: str | None


@dataclass(frozen=True)
class IndicesSearchResult:
    matches: list[IndexMatch]
    total: int


def load_indices(path: Path) -> pd.DataFrame:
    """Read only INDICES rows from Zerodha's instruments file."""
    if not path.is_file():
        raise FileNotFoundError(f"Zerodha instruments not found: {path}")

    instruments = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = sorted((set(INDICES_SOURCE_COLUMNS) | {"segment"}).difference(instruments.columns))
    if missing:
        raise ValueError(f"Zerodha instruments ({path}) is missing required columns: {', '.join(missing)}")

    indices = instruments.loc[instruments["segment"].eq("INDICES"), list(INDICES_SOURCE_COLUMNS)]
    if indices.empty:
        raise ValueError(f"Zerodha instruments ({path}) has no INDICES rows.")
    indices = indices.apply(lambda column: column.str.strip())
    if (indices["instrument_token"] == "").any():
        raise ValueError(f"Zerodha instruments ({path}) has rows without an instrument_token.")
    duplicated = sorted(set(indices.loc[indices["instrument_token"].duplicated(), "instrument_token"]))
    if duplicated:
        raise ValueError(
            f"Zerodha instruments ({path}) lists these instrument_tokens more than once: {', '.join(duplicated[:10])}"
        )

    indices = indices.rename(columns=INDICES_SOURCE_COLUMNS).replace({"": None})
    return indices[list(INDICES_COLUMNS)].sort_values("zd_instrument_token").reset_index(drop=True)


def import_indices(path: Path, db_path: Path = DEFAULT_DB_PATH) -> IndicesImportResult:
    """Replace the `indices` table with one Zerodha file; other tables stay as they are."""
    indices = load_indices(path)
    column_definitions = ", ".join(
        f"{column} TEXT PRIMARY KEY" if column == "zd_instrument_token" else f"{column} TEXT"
        for column in INDICES_COLUMNS
    )
    placeholders = ", ".join("?" for _ in INDICES_COLUMNS)

    with connect(db_path) as con:
        # One transaction, so a failed write leaves the previous table in place.
        con.execute("BEGIN")
        con.execute(f"DROP TABLE IF EXISTS {INDICES_TABLE}")
        con.execute(f"CREATE TABLE {INDICES_TABLE} ({column_definitions})")
        con.executemany(
            f"INSERT INTO {INDICES_TABLE} ({', '.join(INDICES_COLUMNS)}) VALUES ({placeholders})",
            indices.itertuples(index=False, name=None),
        )
        con.execute(f"CREATE INDEX idx_{INDICES_TABLE}_zd_symbol ON {INDICES_TABLE} (zd_symbol)")

    return IndicesImportResult(rows=len(indices), db_path=db_path, file=path)


def search_indices(
    term: str,
    db_path: Path = DEFAULT_DB_PATH,
    *,
    limit: int = 20,
    exact: bool = False,
    exchange: str | None = None,
) -> IndicesSearchResult:
    cleaned = term.strip()
    if not cleaned:
        raise ValueError("Search term must not be empty.")
    if limit < 1:
        raise ValueError("Limit must be at least 1.")

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}. Run 'stocky indices import' first.")

    equality = " OR ".join(f"UPPER({column}) = :exact" for column in EXACT_COLUMNS)
    where = f"({equality})"
    params: dict[str, object] = {"exact": cleaned.upper()}
    if not exact:
        fragment = " OR ".join(f"UPPER({column}) LIKE :fragment ESCAPE '\\'" for column in FRAGMENT_COLUMNS)
        where = f"({equality} OR {fragment})"
        params["fragment"] = f"%{_escape_like(cleaned.upper())}%"
    if exchange is not None:
        where += " AND UPPER(exchange) = :exchange"
        params["exchange"] = exchange.strip().upper()

    with connect(db_path) as con:
        # Keep the count and matching rows in one snapshot during concurrent imports.
        con.execute("BEGIN")
        if not table_exists(con, INDICES_TABLE):
            raise RuntimeError(
                f"Database table '{INDICES_TABLE}' does not exist in {db_path}. Run 'stocky indices import' first."
            )

        total = con.execute(f"SELECT COUNT(*) FROM {INDICES_TABLE} WHERE {where}", params).fetchone()[0]
        rows = con.execute(
            f"""
            SELECT {", ".join(INDICES_COLUMNS)}
            FROM {INDICES_TABLE}
            WHERE {where}
            ORDER BY CASE WHEN {equality} THEN 0 ELSE 1 END, name, zd_instrument_token
            LIMIT :limit
            """,
            {**params, "limit": limit},
        ).fetchall()

    return IndicesSearchResult(matches=[IndexMatch(*row) for row in rows], total=total)
