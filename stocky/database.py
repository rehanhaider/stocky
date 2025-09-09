from __future__ import annotations

import gzip
import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from stocky.config import DEFAULT_BACKUP_DIR, DEFAULT_DB_PATH

CONSOLIDATED_TABLE = "consolidated"
YAHOO_RESPONSES_TABLE = "yahoo_responses"


@dataclass(frozen=True)
class JsonImportResult:
    imported: int
    skipped: int


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

    initialize_database(db_path)
    with connect(db_path) as con:
        rows = con.execute(f"SELECT yahoo_symbol FROM {YAHOO_RESPONSES_TABLE}").fetchall()
    return {row[0] for row in rows}


def fetch_consolidated_symbols(
    db_path: Path = DEFAULT_DB_PATH,
    key: str = "zd_symbol",
    limit: int | None = None,
) -> list[str]:
    allowed_keys = {"zd_symbol", "yq_symbol", "nse_symbol", "bse_sc_code"}
    if key not in allowed_keys:
        raise ValueError(f"Unsupported symbol key: {key}")

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    with connect(db_path) as con:
        if not table_exists(con, CONSOLIDATED_TABLE):
            raise RuntimeError(f"Database table '{CONSOLIDATED_TABLE}' does not exist in {db_path}")

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
