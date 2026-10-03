from __future__ import annotations

import gzip
import json
import shutil
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path

from stocky.config import DEFAULT_BACKUP_DIR, DEFAULT_DB_PATH

CONSOLIDATED_TABLE = "consolidated"
YAHOO_RESPONSES_TABLE = "yahoo_responses"

# Every symbol column belongs to one exchange and stays empty when that exchange does not list the ISIN.
SYMBOL_COLUMNS = ("nse_symbol", "bse_symbol", "bse_sc_code", "zd_ns", "zd_bo", "yq_ns", "yq_bo")
CONSOLIDATED_COLUMNS = (
    "isin",
    "ins_type",
    "nse_symbol",
    "bse_symbol",
    "bse_sc_code",
    "bse_sc_name",
    "zd_ns",
    "zd_bo",
    "yq_ns",
    "yq_bo",
)
SEARCHABLE_COLUMNS = tuple(column for column in CONSOLIDATED_COLUMNS if column != "ins_type")


@dataclass(frozen=True)
class JsonImportResult:
    imported: int
    skipped: int


@dataclass(frozen=True)
class ColumnCoverage:
    column: str
    populated: int


@dataclass(frozen=True)
class DatabaseStatus:
    db_path: Path
    db_size_bytes: int
    consolidated_rows: int
    ins_type_counts: dict[str, int]
    coverage: list[ColumnCoverage]
    yahoo_rows: int
    yahoo_exchange_counts: dict[str, int]
    yahoo_oldest_fetch: str | None
    yahoo_newest_fetch: str | None
    yahoo_cached_ns: int
    yahoo_cached_bo: int


@dataclass(frozen=True)
class InstrumentMatch:
    isin: str | None
    ins_type: str | None
    nse_symbol: str | None
    bse_symbol: str | None
    bse_sc_code: str | None
    bse_sc_name: str | None
    zd_ns: str | None
    zd_bo: str | None
    yq_ns: str | None
    yq_bo: str | None


@dataclass(frozen=True)
class SearchResult:
    matches: list[InstrumentMatch]
    total: int
    fuzzy: bool = False


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(db_path)


def initialize_database(db_path: Path = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as con:
        con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {YAHOO_RESPONSES_TABLE} (
                yahoo_symbol TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                exchange TEXT NOT NULL,
                response_json BLOB NOT NULL,
                fetched_at TEXT NOT NULL,
                source TEXT
            )
            """
        )
        con.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{YAHOO_RESPONSES_TABLE}_exchange
            ON {YAHOO_RESPONSES_TABLE} (exchange)
            """
        )


def table_exists(con: sqlite3.Connection, table_name: str) -> bool:
    row = con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)).fetchone()
    return row is not None


def require_consolidated_table(
    con: sqlite3.Connection, db_path: Path, columns: Sequence[str] = CONSOLIDATED_COLUMNS
) -> None:
    if not table_exists(con, CONSOLIDATED_TABLE):
        raise RuntimeError(f"Database table '{CONSOLIDATED_TABLE}' does not exist in {db_path}")
    require_consolidated_columns(con, db_path, columns)


def require_consolidated_columns(
    con: sqlite3.Connection, db_path: Path, columns: Sequence[str] = CONSOLIDATED_COLUMNS
) -> None:
    """Fail with a rebuild hint when a table built before the per-exchange columns lacks ones a read needs."""
    present = {row[1] for row in con.execute(f"PRAGMA table_info({CONSOLIDATED_TABLE})")}
    missing = [column for column in columns if column not in present]
    if missing:
        raise RuntimeError(
            f"The '{CONSOLIDATED_TABLE}' table in {db_path} has an older layout without {', '.join(missing)}. "
            "Run 'stocky rebuild' to recreate it."
        )


def backup_database(
    db_path: Path = DEFAULT_DB_PATH,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
) -> Path | None:
    if not db_path.exists():
        return None

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{timestamp}-{db_path.name}"
    shutil.copy2(db_path, backup_path)
    return backup_path


def read_available_yahoo_symbols(db_path: Path = DEFAULT_DB_PATH) -> set[str]:
    if not db_path.exists():
        return set()

    with connect(db_path) as con:
        if not table_exists(con, YAHOO_RESPONSES_TABLE):
            return set()
        rows = con.execute(f"SELECT yahoo_symbol FROM {YAHOO_RESPONSES_TABLE}").fetchall()
    return {row[0] for row in rows}


def fetch_consolidated_symbols(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    key: str,
    limit: int | None = None,
) -> list[str]:
    if key not in SYMBOL_COLUMNS:
        raise ValueError(f"Unsupported symbol key: {key}")

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}. Run 'stocky rebuild' first.")

    with connect(db_path) as con:
        require_consolidated_table(con, db_path, ("isin", key))

        query = f"""
            SELECT {key}
            FROM {CONSOLIDATED_TABLE}
            WHERE {key} IS NOT NULL AND TRIM({key}) != ''
            ORDER BY isin
        """
        if limit is not None:
            query += " LIMIT ?"
            rows = con.execute(query, (limit,)).fetchall()
        else:
            rows = con.execute(query).fetchall()

    return [str(row[0]) for row in rows]


def read_status(db_path: Path = DEFAULT_DB_PATH) -> DatabaseStatus:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}. Run 'stocky rebuild' first.")

    with connect(db_path) as con:
        consolidated_rows = 0
        ins_type_counts: dict[str, int] = {}
        coverage: list[ColumnCoverage] = []
        if table_exists(con, CONSOLIDATED_TABLE):
            require_consolidated_columns(con, db_path)
            populated_sums = ", ".join(
                f"SUM(CASE WHEN {column} IS NOT NULL AND TRIM(CAST({column} AS TEXT)) != '' THEN 1 ELSE 0 END)"
                for column in SEARCHABLE_COLUMNS
            )
            row = con.execute(f"SELECT COUNT(*), {populated_sums} FROM {CONSOLIDATED_TABLE}").fetchone()
            consolidated_rows = row[0]
            coverage = [
                ColumnCoverage(column=column, populated=row[index + 1] or 0)
                for index, column in enumerate(SEARCHABLE_COLUMNS)
            ]
            ins_type_counts = dict(
                con.execute(
                    f"SELECT COALESCE(ins_type, 'unknown'), COUNT(*) FROM {CONSOLIDATED_TABLE} "
                    "GROUP BY 1 ORDER BY 2 DESC"
                ).fetchall()
            )

        yahoo_rows = 0
        yahoo_exchange_counts: dict[str, int] = {}
        yahoo_oldest_fetch: str | None = None
        yahoo_newest_fetch: str | None = None
        yahoo_cached_ns = 0
        yahoo_cached_bo = 0
        if table_exists(con, YAHOO_RESPONSES_TABLE):
            yahoo_rows, yahoo_oldest_fetch, yahoo_newest_fetch = con.execute(
                f"SELECT COUNT(*), MIN(fetched_at), MAX(fetched_at) FROM {YAHOO_RESPONSES_TABLE}"
            ).fetchone()
            yahoo_exchange_counts = dict(
                con.execute(
                    f"SELECT exchange, COUNT(*) FROM {YAHOO_RESPONSES_TABLE} GROUP BY exchange ORDER BY COUNT(*) DESC"
                ).fetchall()
            )
            if table_exists(con, CONSOLIDATED_TABLE):
                # yq_ns and yq_bo hold full tickers, so they match yahoo_symbol, the primary key.
                yahoo_cached_ns, yahoo_cached_bo = (
                    count or 0
                    for count in con.execute(
                        f"""
                        SELECT
                            SUM(c.yq_ns IN (SELECT yahoo_symbol FROM {YAHOO_RESPONSES_TABLE})),
                            SUM(c.yq_bo IN (SELECT yahoo_symbol FROM {YAHOO_RESPONSES_TABLE}))
                        FROM {CONSOLIDATED_TABLE} c
                        """
                    ).fetchone()
                )

    return DatabaseStatus(
        db_path=db_path,
        db_size_bytes=db_path.stat().st_size,
        consolidated_rows=consolidated_rows,
        ins_type_counts=ins_type_counts,
        coverage=coverage,
        yahoo_rows=yahoo_rows,
        yahoo_exchange_counts=yahoo_exchange_counts,
        yahoo_oldest_fetch=yahoo_oldest_fetch,
        yahoo_newest_fetch=yahoo_newest_fetch,
        yahoo_cached_ns=yahoo_cached_ns,
        yahoo_cached_bo=yahoo_cached_bo,
    )


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_instruments(
    term: str,
    db_path: Path = DEFAULT_DB_PATH,
    *,
    limit: int = 20,
    exact: bool = False,
) -> SearchResult:
    cleaned = term.strip()
    if not cleaned:
        raise ValueError("Search term must not be empty.")
    if limit < 1:
        raise ValueError("Limit must be at least 1.")

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}. Run 'stocky rebuild' first.")

    equality = " OR ".join(f"UPPER(CAST({column} AS TEXT)) = :exact" for column in SEARCHABLE_COLUMNS)
    where = f"({equality})"
    params: dict[str, object] = {"exact": cleaned.upper()}
    if not exact:
        fuzzy = " OR ".join(f"UPPER(CAST({column} AS TEXT)) LIKE :fuzzy ESCAPE '\\'" for column in SEARCHABLE_COLUMNS)
        where = f"({equality} OR {fuzzy})"
        params["fuzzy"] = f"%{_escape_like(cleaned.upper())}%"

    selected = ", ".join(CONSOLIDATED_COLUMNS)
    with connect(db_path) as con:
        require_consolidated_table(con, db_path)

        total = con.execute(f"SELECT COUNT(*) FROM {CONSOLIDATED_TABLE} WHERE {where}", params).fetchone()[0]
        rows = con.execute(
            f"""
            SELECT {selected}
            FROM {CONSOLIDATED_TABLE}
            WHERE {where}
            ORDER BY CASE WHEN {equality} THEN 0 ELSE 1 END, isin
            LIMIT :limit
            """,
            {**params, "limit": limit},
        ).fetchall()

        fuzzy_matches = False
        if not exact and total == 0:
            fuzzy_rows = []
            for row in con.execute(
                f"""
                SELECT {selected}
                FROM {CONSOLIDATED_TABLE}
                """
            ).fetchall():
                record = dict(zip(CONSOLIDATED_COLUMNS, row, strict=True))
                name_candidates = []
                if record["bse_sc_name"] is not None:
                    name = str(record["bse_sc_name"]).upper()
                    name_candidates = [name, *(word for word in name.split() if len(word) >= 3)]
                name_score = max(
                    (
                        SequenceMatcher(None, cleaned.upper(), str(candidate).upper()).ratio()
                        for candidate in name_candidates
                    ),
                    default=0.0,
                )
                symbol_score = max(
                    (
                        SequenceMatcher(None, cleaned.upper(), str(candidate).upper()).ratio()
                        for candidate in (record[column] for column in ("nse_symbol", "bse_symbol", "zd_ns", "zd_bo"))
                        if candidate is not None
                    ),
                    default=0.0,
                )
                if name_score >= 0.6 or symbol_score >= 0.6:
                    fuzzy_rows.append((name_score, symbol_score, row))

            fuzzy_rows.sort(
                key=lambda item: (-max(item[0], item[1]), -item[0], "" if item[2][0] is None else str(item[2][0]))
            )
            total = len(fuzzy_rows)
            rows = [row for _, _, row in fuzzy_rows[:limit]]
            fuzzy_matches = bool(rows)

    matches = [InstrumentMatch(*(str(value) if value is not None else None for value in row)) for row in rows]
    return SearchResult(matches=matches, total=total, fuzzy=fuzzy_matches)


def upsert_yahoo_response(
    con: sqlite3.Connection,
    *,
    yahoo_symbol: str,
    symbol: str,
    exchange: str,
    response_json: bytes,
    source: str,
    fetched_at: str | None = None,
) -> None:
    if fetched_at is None:
        fetched_at = datetime.now(UTC).isoformat()

    con.execute(
        f"""
        INSERT INTO {YAHOO_RESPONSES_TABLE}
            (yahoo_symbol, symbol, exchange, response_json, fetched_at, source)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(yahoo_symbol) DO UPDATE SET
            symbol = excluded.symbol,
            exchange = excluded.exchange,
            response_json = excluded.response_json,
            fetched_at = excluded.fetched_at,
            source = excluded.source
        """,
        (yahoo_symbol, symbol, exchange, response_json, fetched_at, source),
    )


def encode_response_json(response: object) -> bytes:
    payload = json.dumps(response, default=str, separators=(",", ":")).encode("utf-8")
    return gzip.compress(payload)


def decode_response_json(response_json: bytes) -> object:
    return json.loads(gzip.decompress(response_json).decode("utf-8"))


def split_yahoo_symbol(yahoo_symbol: str) -> tuple[str, str]:
    if "." not in yahoo_symbol:
        return yahoo_symbol, "UNKNOWN"

    symbol, suffix = yahoo_symbol.rsplit(".", 1)
    exchange = {"NS": "NSE", "BO": "BSE"}.get(suffix.upper(), suffix.upper())
    return symbol, exchange


def import_yahoo_json_cache(
    cache_dir: Path,
    db_path: Path = DEFAULT_DB_PATH,
) -> JsonImportResult:
    initialize_database(db_path)

    imported = 0
    skipped = 0
    with connect(db_path) as con:
        for file_path in sorted(cache_dir.glob("*.json")):
            yahoo_symbol = file_path.stem
            symbol, exchange = split_yahoo_symbol(yahoo_symbol)

            try:
                response = json.loads(file_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                skipped += 1
                continue

            fetched_at = datetime.fromtimestamp(file_path.stat().st_mtime, UTC).isoformat()
            upsert_yahoo_response(
                con,
                yahoo_symbol=yahoo_symbol,
                symbol=symbol,
                exchange=exchange,
                response_json=encode_response_json(response),
                fetched_at=fetched_at,
                source=str(file_path),
            )
            imported += 1

    return JsonImportResult(imported=imported, skipped=skipped)
