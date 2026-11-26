from __future__ import annotations

import gzip
import hashlib
import json
import re
import shutil
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path

from stocky import __version__
from stocky.config import DEFAULT_BACKUP_DIR, DEFAULT_DB_PATH

CONSOLIDATED_TABLE = "consolidated"
YAHOO_RESPONSES_TABLE = "yahoo_responses"
# Every rebuild replaces the whole consolidated table, so one build's sources produced every row in it.
PROVENANCE_BUILDS_TABLE = "provenance_builds"
PROVENANCE_SOURCES_TABLE = "provenance_sources"
# Each import replaces its own rows of a mapping table, so the latest record per table and source describes them.
PROVENANCE_IMPORTS_TABLE = "provenance_imports"

# The layout of every table Stocky writes, provenance_imports included, stored in the file's PRAGMA user_version by
# each write that creates or replaces a table. Raise it when a table or its columns change. A file written before
# Stocky recorded the number reads 0.
SCHEMA_VERSION = 1
# The schema number in which the consolidated table took its current layout. Reads of consolidated check the file
# against this, not SCHEMA_VERSION, so raising SCHEMA_VERSION for another table does not ask for a rebuild.
CONSOLIDATED_LAYOUT_VERSION = 1

# Yahoo quote types that describe a listed security. Yahoo labels some thinly traded Indian
# shares MUTUALFUND and liquid ETFs BOND; an INDEX answer carries index levels, not the share's.
LISTED_QUOTE_TYPES = frozenset({"EQUITY", "ETF", "MUTUALFUND", "BOND"})

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


def missing_database_message(db_path: Path, first_step: str) -> str:
    """Name the absolute path, since every default path is relative to the current directory."""
    return (
        f"Database not found: {db_path.resolve()}. Run '{first_step}' first. "
        "To use an existing database, pass --db-path or run from the repository root."
    )


@dataclass(frozen=True)
class JsonImportResult:
    imported: int
    skipped: int


@dataclass(frozen=True)
class ColumnCoverage:
    column: str
    populated: int


@dataclass(frozen=True)
class SourceRecord:
    """One input file of a rebuild, identified by content so a renamed or re-downloaded file is detectable."""

    role: str
    file_name: str
    trade_date: str | None
    sha256: str
    size_bytes: int
    modified_at: str
    rows: int


@dataclass(frozen=True)
class BuildProvenance:
    build_id: int
    built_at: str
    stocky_version: str
    consolidated_rows: int
    sources: list[SourceRecord]


@dataclass(frozen=True)
class ImportRecord:
    """One source file of the latest import into a mapping table such as `mutual_funds` or `broker_symbols`."""

    table_name: str
    role: str
    file_name: str
    sha256: str
    size_bytes: int
    modified_at: str
    rows: int
    imported_at: str
    stocky_version: str


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
    schema_version: int
    provenance: BuildProvenance | None = None
    imports: list[ImportRecord] = field(default_factory=list)


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


class ClosingConnection(sqlite3.Connection):
    """A connection whose ``with`` block commits or rolls back as usual, then closes it."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(db_path, factory=ClosingConnection)


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


def initialize_provenance(con: sqlite3.Connection) -> None:
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {PROVENANCE_BUILDS_TABLE} (
            build_id INTEGER PRIMARY KEY AUTOINCREMENT,
            built_at TEXT NOT NULL,
            stocky_version TEXT NOT NULL,
            consolidated_rows INTEGER NOT NULL
        )
        """
    )
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {PROVENANCE_SOURCES_TABLE} (
            build_id INTEGER NOT NULL REFERENCES {PROVENANCE_BUILDS_TABLE} (build_id),
            role TEXT NOT NULL,
            file_name TEXT NOT NULL,
            trade_date TEXT,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            modified_at TEXT NOT NULL,
            rows INTEGER NOT NULL,
            PRIMARY KEY (build_id, role)
        )
        """
    )


def record_build_provenance(
    con: sqlite3.Connection,
    *,
    built_at: str,
    stocky_version: str,
    consolidated_rows: int,
    sources: Sequence[SourceRecord],
) -> int:
    """Append one build and its source files; earlier builds stay as history."""
    initialize_provenance(con)
    cursor = con.execute(
        f"INSERT INTO {PROVENANCE_BUILDS_TABLE} (built_at, stocky_version, consolidated_rows) VALUES (?, ?, ?)",
        (built_at, stocky_version, consolidated_rows),
    )
    build_id = int(cursor.lastrowid)
    con.executemany(
        f"""
        INSERT INTO {PROVENANCE_SOURCES_TABLE}
            (build_id, role, file_name, trade_date, sha256, size_bytes, modified_at, rows)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                build_id,
                source.role,
                source.file_name,
                source.trade_date,
                source.sha256,
                source.size_bytes,
                source.modified_at,
                source.rows,
            )
            for source in sources
        ],
    )
    return build_id


def read_latest_provenance(con: sqlite3.Connection) -> BuildProvenance | None:
    """Return the build that wrote the current consolidated table, or None for a database built without provenance."""
    if not table_exists(con, PROVENANCE_BUILDS_TABLE) or not table_exists(con, PROVENANCE_SOURCES_TABLE):
        return None
    build = con.execute(
        f"""
        SELECT build_id, built_at, stocky_version, consolidated_rows
        FROM {PROVENANCE_BUILDS_TABLE}
        ORDER BY build_id DESC
        LIMIT 1
        """
    ).fetchone()
    if build is None:
        return None
    sources = [
        SourceRecord(*row)
        for row in con.execute(
            f"""
            SELECT role, file_name, trade_date, sha256, size_bytes, modified_at, rows
            FROM {PROVENANCE_SOURCES_TABLE}
            WHERE build_id = ?
            ORDER BY role
            """,
            (build[0],),
        ).fetchall()
    ]
    return BuildProvenance(*build, sources=sources)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def record_import_provenance(
    con: sqlite3.Connection,
    table_name: str,
    sources: Sequence[tuple[str, Path, int]],
) -> None:
    """Record the files an import read, as (role, path, rows) entries, in the caller's open transaction.

    Each entry replaces the earlier record for the same table and role, so a broker import keeps the other brokers'
    records. Only the file name is kept, not the local directory, as in `provenance_sources`.
    """
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {PROVENANCE_IMPORTS_TABLE} (
            table_name TEXT NOT NULL,
            role TEXT NOT NULL,
            file_name TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            modified_at TEXT NOT NULL,
            rows INTEGER NOT NULL,
            imported_at TEXT NOT NULL,
            stocky_version TEXT NOT NULL,
            PRIMARY KEY (table_name, role)
        )
        """
    )
    imported_at = datetime.now(UTC).isoformat()
    for role, path, rows in sources:
        stat = path.stat()
        con.execute(
            f"""
            INSERT OR REPLACE INTO {PROVENANCE_IMPORTS_TABLE}
                (table_name, role, file_name, sha256, size_bytes, modified_at, rows, imported_at, stocky_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                table_name,
                role,
                path.name,
                file_sha256(path),
                stat.st_size,
                datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                rows,
                imported_at,
                __version__,
            ),
        )


def read_import_provenance(con: sqlite3.Connection) -> list[ImportRecord]:
    """Return the recorded source files of every mapping table, or an empty list for a database without any."""
    if not table_exists(con, PROVENANCE_IMPORTS_TABLE):
        return []
    return [
        ImportRecord(*row)
        for row in con.execute(
            f"""
            SELECT table_name, role, file_name, sha256, size_bytes, modified_at, rows, imported_at, stocky_version
            FROM {PROVENANCE_IMPORTS_TABLE}
            ORDER BY table_name, role
            """
        ).fetchall()
    ]


def read_schema_version(con: sqlite3.Connection) -> int:
    """Return the file's schema number: SCHEMA_VERSION or an earlier one, or 0 if written before Stocky recorded it."""
    return con.execute("PRAGMA user_version").fetchone()[0]


def describe_schema_version(schema_version: int) -> str:
    if schema_version == 0:
        return "0 (written before Stocky recorded a schema number)"
    return str(schema_version)


def write_schema_version(con: sqlite3.Connection, *, replaces_consolidated: bool = False) -> None:
    """Record SCHEMA_VERSION inside the caller's write transaction, so it commits or rolls back with the tables.

    Only a rebuild can vouch for consolidated, so a write that leaves that table alone never numbers a file whose
    consolidated has no number yet or an older one. A v2.0.0 file, for one, has every current column but labels ETFs
    as equity; stamping it would make the layout check trust a table only a rebuild can bring up to date.
    """
    if (
        not replaces_consolidated
        and table_exists(con, CONSOLIDATED_TABLE)
        and (read_schema_version(con) == 0 or older_layout(con) is not None)
    ):
        return
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def table_exists(con: sqlite3.Connection, table_name: str) -> bool:
    row = con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)).fetchone()
    return row is not None


def require_consolidated_table(
    con: sqlite3.Connection, db_path: Path, columns: Sequence[str] = CONSOLIDATED_COLUMNS
) -> None:
    if not table_exists(con, CONSOLIDATED_TABLE):
        raise RuntimeError(f"Database table '{CONSOLIDATED_TABLE}' does not exist in {db_path}")
    require_consolidated_columns(con, db_path, columns)


def older_layout(con: sqlite3.Connection, columns: Sequence[str] = CONSOLIDATED_COLUMNS) -> str | None:
    """Describe how the consolidated table's layout is older than this Stocky's, or return None when it is not.

    The schema number decides when the file records one. A file written before Stocky recorded it (0) is judged by
    whether the table has the columns a read needs.
    """
    schema_version = read_schema_version(con)
    if schema_version:
        if schema_version < CONSOLIDATED_LAYOUT_VERSION:
            return f"(schema {schema_version}; this Stocky reads schema {CONSOLIDATED_LAYOUT_VERSION} or later)"
        return None
    present = {row[1] for row in con.execute(f"PRAGMA table_info({CONSOLIDATED_TABLE})")}
    missing = [column for column in columns if column not in present]
    return f"without {', '.join(missing)}" if missing else None


def require_consolidated_columns(
    con: sqlite3.Connection, db_path: Path, columns: Sequence[str] = CONSOLIDATED_COLUMNS
) -> None:
    """Fail with a rebuild hint when the consolidated table has an older layout than a read needs."""
    problem = older_layout(con, columns)
    if problem:
        raise RuntimeError(
            f"The '{CONSOLIDATED_TABLE}' table in {db_path} has an older layout {problem}. "
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
    name = db_path.name
    if not backups_belong_to(db_path, backup_dir):
        # A database away from this folder marks its backups with its own path, so they never pass for the backups
        # of the database that lives beside the folder under the same file name, which pruning removes.
        name = f"{hashlib.sha256(str(db_path.resolve()).encode()).hexdigest()[:8]}-{name}"
    backup_path = backup_dir / f"{timestamp}-{name}"
    shutil.copy2(db_path, backup_path)
    return backup_path


def backups_belong_to(db_path: Path, backup_dir: Path) -> bool:
    """Whether backup_dir sits beside db_path, so the backups named after it in there are its own.

    The backup directory does not follow --db-path, so a database elsewhere with the same file name backs up into
    the same folder. Only a database beside its backup folder prunes it, and a database elsewhere names its
    backups with a hash of its path (see backup_database), so neither touches the other's backups.
    """
    return backup_dir.resolve().parent == db_path.resolve().parent


class BackupPruneError(OSError):
    """Raised when some old backups could not be removed; ``removed`` lists the ones that were."""

    def __init__(self, removed: list[Path], failures: list[str]) -> None:
        super().__init__("; ".join(failures))
        self.removed = removed


def prune_backups(db_path: Path, backup_dir: Path, keep: int, *, current: Path | None = None) -> list[Path]:
    """Delete all but the newest ``keep`` backups of ``db_path`` and return the removed paths, oldest first.

    Only files named the way ``backup_database`` names them for this database count, so other
    files in the directory, and backups of other databases, are never touched. ``current``, the backup
    the caller just took, always stays and counts toward ``keep``: backup names carry the local time,
    so after the clock goes back an older backup can sort after it.
    """
    if keep < 1:
        raise ValueError(f"Keep at least 1 backup, not {keep}.")
    if not backup_dir.is_dir():
        return []

    pattern = re.compile(rf"\d{{8}}-\d{{6}}-{re.escape(db_path.name)}")
    # The name starts with the timestamp, so sorting by name sorts oldest to newest.
    backups = sorted(
        (path for path in backup_dir.iterdir() if pattern.fullmatch(path.name) and path.is_file() and path != current),
        key=lambda path: path.name,
    )
    kept = keep - 1 if current is not None and current.is_file() else keep
    removed: list[Path] = []
    failures: list[str] = []
    for path in backups[: max(0, len(backups) - kept)]:
        try:
            path.unlink()
        except FileNotFoundError:
            # Another rebuild removed it first; it is gone either way.
            continue
        except OSError as exc:
            failures.append(f"{path}: {exc.strerror or exc}")
            continue
        removed.append(path)
    if failures:
        raise BackupPruneError(removed, failures)
    return removed


def read_available_yahoo_symbols(db_path: Path = DEFAULT_DB_PATH) -> set[str]:
    if not db_path.exists():
        return set()

    with connect(db_path) as con:
        if not table_exists(con, YAHOO_RESPONSES_TABLE):
            return set()
        rows = con.execute(f"SELECT yahoo_symbol FROM {YAHOO_RESPONSES_TABLE}").fetchall()
    return {row[0] for row in rows}


def read_yahoo_fetch_times(db_path: Path = DEFAULT_DB_PATH) -> dict[str, str]:
    """Map each cached Yahoo ticker to the time its response was fetched."""
    if not db_path.exists():
        return {}

    with connect(db_path) as con:
        if not table_exists(con, YAHOO_RESPONSES_TABLE):
            return {}
        return dict(con.execute(f"SELECT yahoo_symbol, fetched_at FROM {YAHOO_RESPONSES_TABLE}").fetchall())


def fetch_consolidated_symbols(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    key: str,
    limit: int | None = None,
) -> list[str]:
    if key not in SYMBOL_COLUMNS:
        raise ValueError(f"Unsupported symbol key: {key}")

    if not db_path.exists():
        raise FileNotFoundError(missing_database_message(db_path, "stocky rebuild"))

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


def has_sgb_rows(db_path: Path = DEFAULT_DB_PATH) -> bool:
    """Report whether the consolidated table holds sovereign gold bonds, so a refresh can keep them by default."""
    if not db_path.exists():
        return False

    with connect(db_path) as con:
        if not table_exists(con, CONSOLIDATED_TABLE):
            return False
        if "ins_type" not in {row[1] for row in con.execute(f"PRAGMA table_info({CONSOLIDATED_TABLE})")}:
            return False
        row = con.execute(f"SELECT 1 FROM {CONSOLIDATED_TABLE} WHERE ins_type = 'sgb' LIMIT 1").fetchone()
    return row is not None


def read_status(db_path: Path = DEFAULT_DB_PATH) -> DatabaseStatus:
    if not db_path.exists():
        raise FileNotFoundError(missing_database_message(db_path, "stocky rebuild"))

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
        provenance = read_latest_provenance(con)
        imports = read_import_provenance(con)
        schema_version = read_schema_version(con)
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
        schema_version=schema_version,
        provenance=provenance,
        imports=imports,
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
        raise FileNotFoundError(missing_database_message(db_path, "stocky rebuild"))

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


def is_usable_yahoo_payload(payload: object) -> bool:
    """Whether one ticker's Yahoo payload names a listed security, as opposed to an error, an empty
    answer, or an index that Yahoo files under the same ticker."""
    if not isinstance(payload, dict):
        return False
    quote_type = payload.get("quoteType")
    if not isinstance(quote_type, dict):
        return False
    return quote_type.get("quoteType") in LISTED_QUOTE_TYPES and bool(
        quote_type.get("longName") or quote_type.get("shortName")
    )


def delete_yahoo_response(con: sqlite3.Connection, yahoo_symbol: str) -> None:
    con.execute(f"DELETE FROM {YAHOO_RESPONSES_TABLE} WHERE yahoo_symbol = ?", (yahoo_symbol,))


# A response is orphaned once its ticker is in neither Yahoo column of consolidated. The NULL checks
# matter: NOT IN against a list holding NULL is never true, so it would match no row at all.
# A column that lists no ticker at all says nothing about which of that exchange's responses are stale: an empty
# table, or a rebuild from legacy bhavcopies, which carry no BSE trading symbol and leave every yq_bo empty, would
# otherwise make every .NS or .BO response look orphaned. So a .NS or .BO response is pruned only while its own
# column lists at least one ticker, and any other response only while the table has rows.
_ORPHAN_YAHOO_RESPONSES = f"""
    FROM {YAHOO_RESPONSES_TABLE}
    WHERE yahoo_symbol NOT IN (SELECT yq_ns FROM {CONSOLIDATED_TABLE} WHERE yq_ns IS NOT NULL)
      AND yahoo_symbol NOT IN (SELECT yq_bo FROM {CONSOLIDATED_TABLE} WHERE yq_bo IS NOT NULL)
      AND CASE
          WHEN yahoo_symbol LIKE '%.NS'
              THEN EXISTS (SELECT 1 FROM {CONSOLIDATED_TABLE} WHERE yq_ns IS NOT NULL AND yq_ns <> '')
          WHEN yahoo_symbol LIKE '%.BO'
              THEN EXISTS (SELECT 1 FROM {CONSOLIDATED_TABLE} WHERE yq_bo IS NOT NULL AND yq_bo <> '')
          ELSE EXISTS (SELECT 1 FROM {CONSOLIDATED_TABLE})
      END
"""


def count_orphan_yahoo_responses(db_path: Path = DEFAULT_DB_PATH) -> int:
    """Count the cached responses whose ticker no longer appears in consolidated."""
    if not db_path.exists():
        return 0

    with connect(db_path) as con:
        if not table_exists(con, YAHOO_RESPONSES_TABLE):
            return 0
        require_consolidated_table(con, db_path, ("yq_ns", "yq_bo"))
        return con.execute(f"SELECT COUNT(*) {_ORPHAN_YAHOO_RESPONSES}").fetchone()[0]


class OrphanVacuumError(Exception):
    """Raised when the orphaned responses were deleted and committed but VACUUM then failed."""

    def __init__(self, pruned: int, cause: sqlite3.Error) -> None:
        super().__init__(str(cause))
        self.pruned = pruned


def prune_orphan_yahoo_responses(db_path: Path = DEFAULT_DB_PATH) -> int:
    """Delete the cached responses whose ticker no longer appears in consolidated, and return how many.

    The deletion is committed first and the file is then vacuumed, on a second connection, to give the
    freed pages back, because VACUUM cannot run inside a transaction. Nothing is vacuumed when nothing was
    deleted. A failed VACUUM raises OrphanVacuumError, which carries the committed count.
    """
    with connect(db_path) as con:
        require_consolidated_table(con, db_path, ("yq_ns", "yq_bo"))
        pruned = con.execute(f"DELETE {_ORPHAN_YAHOO_RESPONSES}").rowcount
    if pruned:
        try:
            with connect(db_path) as con:
                con.execute("VACUUM")
        except sqlite3.Error as exc:
            raise OrphanVacuumError(pruned, exc) from exc
    return pruned


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
    # Check before initialize_database, so a mistyped directory fails without creating an empty database.
    if not cache_dir.is_dir():
        raise FileNotFoundError(f"Yahoo JSON cache directory not found: {cache_dir}")

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

            payload = response.get(yahoo_symbol) if isinstance(response, dict) else None
            if not is_usable_yahoo_payload(payload):
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
            if not imported:
                # After the first row, so the number commits or rolls back with the imported rows.
                write_schema_version(con)
            imported += 1

    return JsonImportResult(imported=imported, skipped=skipped)
