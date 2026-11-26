from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from stocky import __version__
from stocky.brokers import BROKER_TABLE
from stocky.bse_debt import BSE_DEBT_TABLE
from stocky.config import DEFAULT_DB_PATH
from stocky.database import (
    CONSOLIDATED_COLUMNS,
    CONSOLIDATED_TABLE,
    YAHOO_RESPONSES_TABLE,
    BuildProvenance,
    DatabaseStatus,
    ImportRecord,
    connect,
    missing_database_message,
    read_import_provenance,
    read_latest_provenance,
    read_status,
    table_exists,
)
from stocky.indices import INDICES_TABLE
from stocky.mutual_funds import MUTUAL_FUNDS_TABLE

DEFAULT_MISSING_SAMPLE_SIZE = 20

# Each exchange's Yahoo ticker column and the symbol column it is derived from.
YAHOO_TICKER_SOURCES = (("NSE", "yq_ns", "nse_symbol", ".NS"), ("BSE", "yq_bo", "bse_symbol", ".BO"))

SOURCE_LABELS = {
    "bse_bhavcopy": "BSE bhavcopy",
    "nse_bhavcopy": "NSE bhavcopy",
    "zerodha_instruments": "Zerodha instruments",
    "zerodha_mf_instruments": "Zerodha MF instruments",
    "amfi_nav": "AMFI NAV",
    "bse_debt_bhavcopy": "BSE debt bhavcopy",
    "groww": "Groww instruments",
    "upstox": "Upstox instruments",
    "aliceblue_nse": "Alice Blue NSE",
    "aliceblue_bse": "Alice Blue BSE",
}

# Each mapping table and the command whose import fills it and records its source files.
MAPPING_TABLES = {
    MUTUAL_FUNDS_TABLE: "stocky mf import",
    BSE_DEBT_TABLE: "stocky debt import",
    INDICES_TABLE: "stocky indices import",
    BROKER_TABLE: "stocky brokers import",
}


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class MissingYahooTickers:
    """Listed tickers with no saved Yahoo response: never fetched, or skipped because Yahoo had no listed security."""

    exchange: str
    column: str
    listed: int
    missing: int
    sample: list[str]


@dataclass(frozen=True)
class RefreshSummary:
    db_path: Path
    generated_at: str
    stocky_version: str
    passed: bool
    checks: list[Check]
    provenance: BuildProvenance | None
    imports: list[ImportRecord]
    table_counts: dict[str, int]
    status: DatabaseStatus | None
    missing_yahoo: list[MissingYahooTickers]


def _populated(column: str) -> str:
    return f"({column} IS NOT NULL AND TRIM({column}) != '')"


def build_refresh_summary(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    sample_size: int = DEFAULT_MISSING_SAMPLE_SIZE,
) -> RefreshSummary:
    """Validate a rebuilt database and collect what a refresh review needs: sources, counts, and gaps."""
    if not db_path.exists():
        raise FileNotFoundError(missing_database_message(db_path, "stocky rebuild"))
    if sample_size < 0:
        raise ValueError("Sample size must not be negative.")

    checks: list[Check] = []
    missing_yahoo: list[MissingYahooTickers] = []
    with connect(db_path) as con:
        table_counts = {
            name: con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            for (name,) in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        }
        provenance = read_latest_provenance(con)
        imports = read_import_provenance(con)
        # Each broker is imported, and recorded, on its own, so every broker with rows needs its own record.
        brokers_with_rows = (
            [row[0] for row in con.execute(f"SELECT DISTINCT broker FROM {BROKER_TABLE} ORDER BY broker")]
            if table_exists(con, BROKER_TABLE)
            else []
        )

        present = (
            {row[1] for row in con.execute(f"PRAGMA table_info({CONSOLIDATED_TABLE})")}
            if table_exists(con, CONSOLIDATED_TABLE)
            else set()
        )
        missing_columns = [column for column in CONSOLIDATED_COLUMNS if column not in present]
        layout_ok = not missing_columns
        checks.append(
            Check(
                "Consolidated table layout",
                layout_ok,
                "All columns present."
                if layout_ok
                else f"Missing {', '.join(missing_columns)}. Run 'stocky rebuild' to recreate the table.",
            )
        )

        rows = table_counts.get(CONSOLIDATED_TABLE, 0)
        checks.append(Check("Consolidated rows", rows > 0, f"{rows} rows."))

        if layout_ok:
            empty, duplicates = con.execute(
                f"""
                SELECT
                    SUM(NOT {_populated("isin")}),
                    COUNT(isin) - COUNT(DISTINCT isin)
                FROM {CONSOLIDATED_TABLE}
                """
            ).fetchone()
            empty, duplicates = empty or 0, duplicates or 0
            checks.append(
                Check(
                    "ISINs present and unique",
                    empty == 0 and duplicates == 0,
                    f"{empty} empty, {duplicates} duplicated.",
                )
            )

            mismatches = []
            for exchange, ticker_column, symbol_column, suffix in YAHOO_TICKER_SOURCES:
                count = con.execute(
                    f"""
                    SELECT COUNT(*) FROM {CONSOLIDATED_TABLE}
                    WHERE CASE WHEN {_populated(symbol_column)} THEN {symbol_column} || ? ELSE '' END
                        != COALESCE({ticker_column}, '')
                    """,
                    (suffix,),
                ).fetchone()[0]
                if count:
                    mismatches.append(f"{count} {exchange} ({ticker_column})")
            checks.append(
                Check(
                    "Yahoo tickers follow exchange symbols",
                    not mismatches,
                    "Every ticker is its exchange symbol plus the suffix."
                    if not mismatches
                    else f"Rows that differ: {', '.join(mismatches)}.",
                )
            )

            has_yahoo = table_exists(con, YAHOO_RESPONSES_TABLE)
            for exchange, ticker_column, _, _ in YAHOO_TICKER_SOURCES:
                cached = f"{ticker_column} IN (SELECT yahoo_symbol FROM {YAHOO_RESPONSES_TABLE})" if has_yahoo else "0"
                listed = con.execute(
                    f"SELECT COUNT(DISTINCT {ticker_column}) FROM {CONSOLIDATED_TABLE} WHERE {_populated(ticker_column)}"
                ).fetchone()[0]
                missing_query = f"""
                    SELECT DISTINCT {ticker_column} FROM {CONSOLIDATED_TABLE}
                    WHERE {_populated(ticker_column)} AND NOT ({cached})
                    ORDER BY {ticker_column}
                """
                missing = [row[0] for row in con.execute(missing_query).fetchall()]
                missing_yahoo.append(
                    MissingYahooTickers(
                        exchange=exchange,
                        column=ticker_column,
                        listed=listed,
                        missing=len(missing),
                        sample=missing[:sample_size],
                    )
                )

    if provenance is None:
        checks.append(Check("Source provenance", False, "No build recorded. Run 'stocky rebuild' to record it."))
    else:
        checks.append(
            Check(
                "Source provenance",
                provenance.consolidated_rows == rows,
                f"Build {provenance.build_id} recorded {provenance.consolidated_rows} rows; the table has {rows}.",
            )
        )
        trade_dates = {
            source.role: source.trade_date for source in provenance.sources if source.role != "zerodha_instruments"
        }
        dates = set(trade_dates.values())
        checks.append(
            Check(
                "Bhavcopy trade dates agree",
                len(trade_dates) == 2 and len(dates) == 1 and None not in dates,
                ", ".join(
                    f"{SOURCE_LABELS.get(role, role)} {value or 'unknown'}" for role, value in trade_dates.items()
                )
                + "."
                if trade_dates
                else "No bhavcopies recorded.",
            )
        )

    for table_name, command in MAPPING_TABLES.items():
        table_rows = table_counts.get(table_name, 0)
        records = [record for record in imports if record.table_name == table_name]
        if not table_rows:
            detail = f"No rows. Run '{command}'."
        elif not records:
            detail = f"{table_rows} rows; no import recorded. Run '{command}' to record it."
        else:
            detail = (
                f"{table_rows} rows from "
                + ", ".join(f"`{record.file_name}` (modified {record.modified_at[:10]})" for record in records)
                + "."
            )
        unrecorded = []
        if table_name == BROKER_TABLE:
            # Alice Blue records its two files as aliceblue_nse and aliceblue_bse.
            recorded = {record.role.split("_")[0] for record in records}
            unrecorded = [broker for broker in brokers_with_rows if broker not in recorded]
        if records and unrecorded:
            detail += (
                f" No import recorded for {', '.join(unrecorded)}. Run '{command} "
                + " ".join(f"--broker {broker}" for broker in unrecorded)
                + "' to record it."
            )
        checks.append(Check(f"{table_name} import", bool(table_rows and records and not unrecorded), detail))

    return RefreshSummary(
        db_path=db_path,
        generated_at=datetime.now(UTC).isoformat(),
        stocky_version=__version__,
        passed=all(check.passed for check in checks),
        checks=checks,
        provenance=provenance,
        imports=imports,
        table_counts=table_counts,
        status=read_status(db_path) if layout_ok else None,
        missing_yahoo=missing_yahoo,
    )


def _timestamp(value: str | None) -> str:
    return value[:19].replace("T", " ") + " UTC" if value else "none"


def _percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def render_markdown(summary: RefreshSummary) -> str:
    lines = [
        "# Stocky refresh summary",
        "",
        f"Generated {_timestamp(summary.generated_at)} by Stocky {summary.stocky_version} "
        f"from `{summary.db_path.as_posix()}`.",
        "",
        f"**Validation: {'passed' if summary.passed else 'FAILED'}**",
        "",
        "| Check | Result | Detail |",
        "| --- | --- | --- |",
        *(f"| {check.name} | {'pass' if check.passed else 'FAIL'} | {check.detail} |" for check in summary.checks),
        "",
        "## Source files",
        "",
    ]

    provenance = summary.provenance
    if provenance is None:
        lines.append("No source provenance is recorded in this database.")
    else:
        lines += [
            f"Build {provenance.build_id} ran at {_timestamp(provenance.built_at)} with Stocky "
            f"{provenance.stocky_version} and wrote {provenance.consolidated_rows} rows.",
            "",
            "| Source | File | Trade date | Usable rows | Size (bytes) | Modified | SHA-256 |",
            "| --- | --- | --- | ---: | ---: | --- | --- |",
            *(
                f"| {SOURCE_LABELS.get(source.role, source.role)} | `{source.file_name}` | {source.trade_date or '-'} "
                f"| {source.rows} | {source.size_bytes} | {_timestamp(source.modified_at)} | `{source.sha256}` |"
                for source in provenance.sources
            ),
        ]

    lines += ["", "## Imported tables", ""]
    if not summary.imports:
        lines.append("No import provenance is recorded in this database.")
    else:
        lines += [
            "| Table | Source | File | Rows | Size (bytes) | Modified | Imported | SHA-256 |",
            "| --- | --- | --- | ---: | ---: | --- | --- | --- |",
            *(
                f"| `{record.table_name}` | {SOURCE_LABELS.get(record.role, record.role)} | `{record.file_name}` "
                f"| {record.rows} | {record.size_bytes} | {_timestamp(record.modified_at)} "
                f"| {_timestamp(record.imported_at)} by Stocky {record.stocky_version} | `{record.sha256}` |"
                for record in summary.imports
            ),
        ]

    lines += [
        "",
        "## Database tables",
        "",
        "| Table | Rows |",
        "| --- | ---: |",
        *(f"| `{name}` | {count} |" for name, count in summary.table_counts.items()),
    ]

    status = summary.status
    if status is not None:
        lines += [
            "",
            "## Consolidated coverage",
            "",
            "| Column | Populated | Missing | Coverage |",
            "| --- | ---: | ---: | ---: |",
            *(
                f"| `{entry.column}` | {entry.populated} | {status.consolidated_rows - entry.populated} "
                f"| {_percent(entry.populated, status.consolidated_rows)} |"
                for entry in status.coverage
            ),
            "",
            "## Yahoo cache",
            "",
            f"Newest fetch {_timestamp(status.yahoo_newest_fetch)}; oldest fetch {_timestamp(status.yahoo_oldest_fetch)}.",
        ]

    if summary.missing_yahoo:
        lines += [
            "",
            "## Tickers without a Yahoo response",
            "",
            "These tickers were never fetched, or Yahoo returned no listed security for them, so the update skipped them.",
            "",
            "| Exchange | Column | Listed | Without response |",
            "| --- | --- | ---: | ---: |",
            *(
                f"| {entry.exchange} | `{entry.column}` | {entry.listed} | {entry.missing} |"
                for entry in summary.missing_yahoo
            ),
        ]
        for entry in summary.missing_yahoo:
            if not entry.sample:
                continue
            shown = "" if len(entry.sample) == entry.missing else f" (first {len(entry.sample)} of {entry.missing})"
            lines += ["", f"{entry.exchange}{shown}: " + ", ".join(f"`{ticker}`" for ticker in entry.sample)]

    return "\n".join(lines) + "\n"
