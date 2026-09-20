from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from stocky.config import DEFAULT_DB_PATH
from stocky.database import CONSOLIDATED_TABLE, connect, table_exists

EXPORT_FORMATS = ("csv", "json")
EXPORT_COLUMNS = ("isin", "ins_type", "zd_symbol", "yq_symbol", "nse_symbol", "bse_sc_code", "bse_sc_name")
REQUIRABLE_COLUMNS = ("zd_symbol", "yq_symbol", "nse_symbol", "bse_sc_code")

_SUFFIX_FORMATS = {".csv": "csv", ".json": "json"}


@dataclass(frozen=True)
class ExportResult:
    rows: int
    columns: tuple[str, ...]
    format: str
    output: Path | None


def _resolve_columns(columns: Sequence[str] | None) -> tuple[str, ...]:
    if not columns:
        return EXPORT_COLUMNS

    resolved: list[str] = []
    for column in columns:
        if column not in EXPORT_COLUMNS:
            raise ValueError(f"Unknown column '{column}'. Valid columns: {', '.join(EXPORT_COLUMNS)}")
        if column in resolved:
            raise ValueError(f"Duplicate column '{column}'. Each column may only be exported once.")
        resolved.append(column)
    return tuple(resolved)


def _resolve_require(require: Sequence[str] | None) -> tuple[str, ...]:
    if not require:
        return ()

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


def export_consolidated(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    output: Path | None,
    format: str | None,
    columns: Sequence[str] | None = None,
    require: Sequence[str] | None = None,
) -> ExportResult:
    resolved_format = _resolve_format(output, format)
    frame = read_consolidated(db_path, columns=columns, require=require)

    payload = _render_csv(frame) if resolved_format == "csv" else _render_json(frame)

    if output is None:
        sys.stdout.write(payload)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")

    return ExportResult(
        rows=len(frame.index),
        columns=tuple(frame.columns),
        format=resolved_format,
        output=output,
    )
