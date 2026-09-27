from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pandas as pd

from stocky import __version__
from stocky.config import DEFAULT_DB_PATH, DEFAULT_SNAPSHOT_DIR
from stocky.database import CONSOLIDATED_TABLE, connect, table_exists

EXPORT_FORMATS = ("csv", "json", "parquet")
EXPORT_COLUMNS = ("isin", "ins_type", "zd_symbol", "yq_symbol", "nse_symbol", "bse_sc_code", "bse_sc_name")
REQUIRABLE_COLUMNS = ("zd_symbol", "yq_symbol", "nse_symbol", "bse_sc_code")

SNAPSHOT_FORMATS = ("csv", "parquet")
SNAPSHOT_MANIFEST = "manifest.json"

_SUFFIX_FORMATS = {".csv": "csv", ".json": "json", ".parquet": "parquet"}
_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class ExportResult:
    rows: int
    columns: tuple[str, ...]
    format: str
    output: Path | None


def _resolve_columns(columns: Sequence[str] | None) -> tuple[str, ...]:
    if columns is None:
        return EXPORT_COLUMNS
    if len(columns) == 0:
        raise ValueError("--columns must name at least one column.")

    resolved: list[str] = []
    for column in columns:
        if column not in EXPORT_COLUMNS:
            raise ValueError(f"Unknown column '{column}'. Valid columns: {', '.join(EXPORT_COLUMNS)}")
        if column in resolved:
            raise ValueError(f"Duplicate column '{column}'. Each column may only be exported once.")
        resolved.append(column)
    return tuple(resolved)


def _resolve_require(require: Sequence[str] | None) -> tuple[str, ...]:
    if require is None:
        return ()
    if len(require) == 0:
        raise ValueError("--require must name at least one column.")

    resolved: list[str] = []
    for column in require:
        if column not in REQUIRABLE_COLUMNS:
            raise ValueError(f"Unknown require column '{column}'. Valid columns: {', '.join(REQUIRABLE_COLUMNS)}")
        if column not in resolved:
            resolved.append(column)
    return tuple(resolved)


def _resolve_format(output: Path | None, format: str | None) -> str:
    if format is not None:
        normalised = format.strip().lower()
        if normalised not in EXPORT_FORMATS:
            raise ValueError(f"Unknown format '{format}'. Valid formats: {', '.join(EXPORT_FORMATS)}")
        return normalised

    if output is None:
        raise ValueError("Pass --format when writing to stdout.")

    suffix = output.suffix.lower()
    if suffix not in _SUFFIX_FORMATS:
        raise ValueError(f"Cannot infer a format from '{output.name}'. Valid formats: {', '.join(EXPORT_FORMATS)}")
    return _SUFFIX_FORMATS[suffix]


def read_consolidated(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    columns: Sequence[str] | None = None,
    require: Sequence[str] | None = None,
) -> pd.DataFrame:
    selected = _resolve_columns(columns)
    required = _resolve_require(require)

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}. Run 'stocky rebuild' first.")

    query = f"SELECT {', '.join(selected)} FROM {CONSOLIDATED_TABLE}"
    if required:
        conditions = " AND ".join(f"{column} IS NOT NULL AND TRIM(CAST({column} AS TEXT)) != ''" for column in required)
        query += f" WHERE {conditions}"
    query += " ORDER BY isin"

    with connect(db_path) as con:
        if not table_exists(con, CONSOLIDATED_TABLE):
            raise RuntimeError(f"Database table '{CONSOLIDATED_TABLE}' does not exist in {db_path}")
        rows = con.execute(query, ()).fetchall()

    return pd.DataFrame(rows, columns=list(selected), dtype=object)


def _render_csv(frame: pd.DataFrame) -> str:
    return frame.to_csv(index=False, lineterminator="\n")


def _render_json(frame: pd.DataFrame) -> str:
    records = frame.astype(object).where(frame.notna(), None).to_dict(orient="records")
    return json.dumps(records, indent=2, ensure_ascii=False) + "\n"


def _render_parquet(frame: pd.DataFrame) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema((column, pa.string()) for column in frame.columns)
    table = pa.Table.from_pandas(frame.astype("string"), schema=schema, preserve_index=False)
    buffer = BytesIO()
    pq.write_table(table, buffer)
    return buffer.getvalue()


def _require_pyarrow() -> None:
    try:
        import pyarrow

        _ = pyarrow
    except ImportError as exc:
        raise RuntimeError(
            "Parquet export needs pyarrow. Install it with 'uv sync --extra parquet' or "
            "'pip install \"stocky[parquet]\"'."
        ) from exc


def _render(frame: pd.DataFrame, format: str) -> str | bytes:
    if format == "csv":
        return _render_csv(frame)
    if format == "json":
        return _render_json(frame)
    return _render_parquet(frame)


def _write(payload: str | bytes, output: Path | None) -> None:
    if output is None:
        if isinstance(payload, bytes):
            sys.stdout.buffer.write(payload)
        else:
            sys.stdout.write(payload)
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, bytes):
        output.write_bytes(payload)
    else:
        output.write_text(payload, encoding="utf-8")


def export_consolidated(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    output: Path | None,
    format: str | None,
    columns: Sequence[str] | None = None,
    require: Sequence[str] | None = None,
) -> ExportResult:
    resolved_format = _resolve_format(output, format)
    if resolved_format == "parquet":
        _require_pyarrow()
        if output is None and sys.stdout.isatty():
            raise ValueError("Parquet output is binary. Pass --output FILE or redirect stdout to a file.")

    frame = read_consolidated(db_path, columns=columns, require=require)
    _write(_render(frame, resolved_format), output)

    return ExportResult(
        rows=len(frame.index),
        columns=tuple(frame.columns),
        format=resolved_format,
        output=output,
    )


def export_tickers(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    output: Path | None,
    column: str,
    suffix: str = "",
    require: Sequence[str] | None = None,
) -> ExportResult:
    """Write one ticker per line from a symbol column, skipping unpopulated rows and repeats."""
    if column not in REQUIRABLE_COLUMNS:
        raise ValueError(f"Unknown tickers column '{column}'. Valid columns: {', '.join(REQUIRABLE_COLUMNS)}")

    required = [column, *(item for item in _resolve_require(require) if item != column)]
    frame = read_consolidated(db_path, columns=[column], require=required)

    tickers = list(dict.fromkeys(f"{str(value).strip()}{suffix}" for value in frame[column]))
    _write("".join(f"{ticker}\n" for ticker in tickers), output)

    return ExportResult(rows=len(tickers), columns=(column,), format="tickers", output=output)


@dataclass(frozen=True)
class SnapshotFile:
    name: str
    format: str
    bytes: int
    sha256: str


@dataclass(frozen=True)
class SnapshotResult:
    version: str
    directory: Path
    rows: int
    files: tuple[SnapshotFile, ...]


def _resolve_snapshot_formats(formats: Sequence[str] | None) -> tuple[str, ...]:
    if formats is None:
        return SNAPSHOT_FORMATS
    if len(formats) == 0:
        raise ValueError("--formats must name at least one format.")

    resolved: list[str] = []
    for format in formats:
        normalised = format.strip().lower()
        if normalised not in EXPORT_FORMATS:
            raise ValueError(f"Unknown format '{format}'. Valid formats: {', '.join(EXPORT_FORMATS)}")
        if normalised not in resolved:
            resolved.append(normalised)
    return tuple(resolved)


def _resolve_snapshot_version(version: str | None) -> str:
    if version is None:
        return datetime.now(timezone.utc).date().isoformat()
    if not _VERSION_PATTERN.fullmatch(version):
        raise ValueError(
            f"Invalid snapshot version '{version}'. Use letters, digits, '.', '_' or '-', starting with a letter or digit."
        )
    return version


def create_snapshot(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    output_dir: Path = DEFAULT_SNAPSHOT_DIR,
    version: str | None = None,
    formats: Sequence[str] | None = None,
) -> SnapshotResult:
    """Write every consolidated row to an immutable, versioned directory with a checksummed manifest."""
    resolved_version = _resolve_snapshot_version(version)
    resolved_formats = _resolve_snapshot_formats(formats)
    if "parquet" in resolved_formats:
        _require_pyarrow()

    directory = output_dir / f"consolidated-{resolved_version}"
    if directory.exists():
        raise FileExistsError(
            f"Snapshot {directory} already exists. Snapshots are never overwritten; pass a different --version."
        )

    frame = read_consolidated(db_path)

    files: list[SnapshotFile] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    staging = output_dir / f".{directory.name}-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        for format in resolved_formats:
            payload = _render(frame, format)
            data = payload if isinstance(payload, bytes) else payload.encode("utf-8")
            name = f"{CONSOLIDATED_TABLE}.{format}"
            (staging / name).write_bytes(data)
            files.append(
                SnapshotFile(name=name, format=format, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            )

        manifest = {
            "version": resolved_version,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "stocky_version": __version__,
            "table": CONSOLIDATED_TABLE,
            "rows": len(frame.index),
            "columns": list(frame.columns),
            "files": [asdict(file) for file in files],
        }
        (staging / SNAPSHOT_MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        staging.rename(directory)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return SnapshotResult(version=resolved_version, directory=directory, rows=len(frame.index), files=tuple(files))
