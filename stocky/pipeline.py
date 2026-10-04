from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from stocky import __version__
from stocky.config import DEFAULT_BACKUP_DIR, DEFAULT_DB_PATH
from stocky.database import (
    CONSOLIDATED_COLUMNS,
    CONSOLIDATED_TABLE,
    BuildProvenance,
    SourceRecord,
    backup_database,
    connect,
    initialize_database,
    read_latest_provenance,
    record_build_provenance,
    table_exists,
)
from stocky.sources import BhavcopyPaths, parse_bse_bhavcopy_date, parse_nse_bhavcopy_date, require_existing_files

NSE_LEGACY_COLUMNS = {"SERIES", "ISIN", "SYMBOL"}
NSE_UDIFF_COLUMNS = {"SctySrs", "ISIN", "TckrSymb", "FinInstrmId"}

# EQ = rolling-settlement equities; BE/BZ = trade-for-trade equities.
# SME platform (SM/ST) and debt/bond/ETF series stay excluded.
NSE_EQUITY_SERIES = {"EQ", "BE", "BZ"}

BSE_LEGACY_COLUMNS = {"SC_TYPE", "ISIN_CODE", "SC_CODE", "SC_NAME"}
BSE_UDIFF_COLUMNS = {"SctySrs", "ISIN", "FinInstrmId", "TckrSymb", "FinInstrmNm"}

# The legacy files' SC_TYPE "Q" filter kept every group except fixed income (F)
# and gilts/SGBs (G); the UDiFF file has no SC_TYPE, so exclude those series instead.
BSE_NON_EQUITY_UDIFF_SERIES = {"F", "G"}


@dataclass(frozen=True)
class SourcePreview:
    bse_bhavcopy: Path
    bse_rows: int
    nse_bhavcopy: Path
    nse_rows: int
    zerodha_instruments: Path
    zerodha_rows: int


DEFAULT_DIFF_SAMPLE_SIZE = 5


@dataclass(frozen=True)
class RowChange:
    isin: str
    # Column name to its (current, rebuilt) values.
    changes: dict[str, tuple[str | None, str | None]]


@dataclass(frozen=True)
class RebuildDiff:
    """How a rebuilt consolidated table differs from the one in the database, keyed by ISIN."""

    current_rows: int
    rebuilt_rows: int
    added: int
    removed: int
    changed: int
    unchanged: int
    # Rows beyond the first for an ISIN that appears more than once; the comparison uses the first row.
    duplicate_current: int
    duplicate_rebuilt: int
    compared_columns: list[str]
    column_changes: dict[str, int]
    sample_added: list[dict[str, str | None]]
    sample_removed: list[dict[str, str | None]]
    sample_changed: list[RowChange]
    sample_duplicates: list[str]
    current_provenance: BuildProvenance | None


@dataclass(frozen=True)
class RebuildResult:
    rows: int
    db_path: Path
    backup_path: Path | None
    dry_run: bool
    bse_bhavcopy: Path
    nse_bhavcopy: Path
    zerodha_instruments: Path
    built_at: str = ""
    build_id: int | None = None
    sources: list[SourceRecord] = field(default_factory=list)
    diff: RebuildDiff | None = None


def _strip_dataframe_strings(dataframe: pd.DataFrame) -> pd.DataFrame:
    return dataframe.replace({r"^\s*|\s*$": ""}, regex=True)


def _require_columns(dataframe: pd.DataFrame, required_columns: set[str], source_name: str) -> None:
    missing = sorted(required_columns.difference(dataframe.columns))
    if missing:
        raise ValueError(f"{source_name} is missing required columns: {', '.join(missing)}")


def load_bse_equities(path: Path) -> pd.DataFrame:
    bse_bhavcopy = _strip_dataframe_strings(pd.read_csv(path))

    if BSE_LEGACY_COLUMNS.issubset(bse_bhavcopy.columns):
        equities = bse_bhavcopy[bse_bhavcopy["SC_TYPE"] == "Q"][["ISIN_CODE", "SC_CODE", "SC_NAME"]].copy()
        equities.rename(
            columns={"ISIN_CODE": "isin", "SC_CODE": "bse_sc_code", "SC_NAME": "bse_sc_name"},
            inplace=True,
        )
        equities["bse_sc_code"] = equities["bse_sc_code"].astype(str)
        # Legacy files carry no BSE trading symbol.
        equities["bse_symbol"] = None
        return equities

    if BSE_UDIFF_COLUMNS.issubset(bse_bhavcopy.columns):
        equities = bse_bhavcopy[~bse_bhavcopy["SctySrs"].isin(BSE_NON_EQUITY_UDIFF_SERIES)][
            ["ISIN", "FinInstrmId", "TckrSymb", "FinInstrmNm"]
        ].copy()
        equities.rename(
            columns={
                "ISIN": "isin",
                "FinInstrmId": "bse_sc_code",
                "TckrSymb": "bse_symbol",
                "FinInstrmNm": "bse_sc_name",
            },
            inplace=True,
        )
        equities["bse_sc_code"] = equities["bse_sc_code"].astype(str)
        return equities

    raise ValueError(
        "BSE bhavcopy is missing required columns for supported formats: "
        f"legacy {sorted(BSE_LEGACY_COLUMNS)} or UDiFF {sorted(BSE_UDIFF_COLUMNS)} ({path})"
    )


def load_nse_equities(path: Path) -> pd.DataFrame:
    nse_bhavcopy = _strip_dataframe_strings(pd.read_csv(path))

    if NSE_LEGACY_COLUMNS.issubset(nse_bhavcopy.columns):
        equities = nse_bhavcopy[nse_bhavcopy["SERIES"].isin(NSE_EQUITY_SERIES)][["ISIN", "SYMBOL"]].copy()
        equities.rename(columns={"ISIN": "isin", "SYMBOL": "nse_symbol"}, inplace=True)
        # Legacy files carry no instrument token, so Zerodha's NSE symbol is matched by name only.
        equities["nse_token"] = None
        return equities

    if NSE_UDIFF_COLUMNS.issubset(nse_bhavcopy.columns):
        equities = nse_bhavcopy[nse_bhavcopy["SctySrs"].isin(NSE_EQUITY_SERIES)][
            ["ISIN", "TckrSymb", "FinInstrmId"]
        ].copy()
        equities.rename(columns={"ISIN": "isin", "TckrSymb": "nse_symbol", "FinInstrmId": "nse_token"}, inplace=True)
        equities["nse_token"] = equities["nse_token"].astype(str)
        return equities

    raise ValueError(
        "NSE bhavcopy is missing required columns for supported formats: "
        f"legacy {sorted(NSE_LEGACY_COLUMNS)} or UDiFF {sorted(NSE_UDIFF_COLUMNS)} ({path})"
    )


def load_zerodha_instruments(path: Path) -> pd.DataFrame:
    instruments = _strip_dataframe_strings(pd.read_csv(path))
    _require_columns(instruments, {"segment", "exchange_token", "tradingsymbol"}, f"Zerodha instruments ({path})")

    instruments = instruments.query("segment == 'BSE' or segment == 'NSE'")[
        ["segment", "exchange_token", "tradingsymbol"]
    ].copy()
    instruments["exchange_token"] = instruments["exchange_token"].astype(str)
    instruments["tradingsymbol"] = instruments["tradingsymbol"].astype(str)
    return instruments


def match_zerodha_symbols(tokens: pd.Series, zerodha_instruments: pd.DataFrame, segment: str) -> pd.Series:
    """Map exchange tokens to Zerodha trading symbols from one segment only.

    Zerodha's exchange_token is the exchange's own instrument number: the NSE token for the NSE
    segment and the BSE scrip code for the BSE segment. Matching by token rather than by symbol keeps
    an NSE share from picking up a different company's BSE symbol, and yields Zerodha's own spelling,
    such as AAREYDRUGS-BE for NSE trade-for-trade shares.
    """
    segment_rows = zerodha_instruments[zerodha_instruments["segment"] == segment]
    token_to_symbol = dict(zip(segment_rows["exchange_token"], segment_rows["tradingsymbol"], strict=False))
    return tokens.map(lambda token: token_to_symbol.get(str(token)) if pd.notna(token) else None)


def match_zerodha_symbols_by_name(symbols: pd.Series, zerodha_instruments: pd.DataFrame, segment: str) -> pd.Series:
    """Find an exchange symbol in one Zerodha segment, as listed or with Zerodha's -BE/-BZ series suffix.

    NSE gives a share a new token when it moves between the EQ and BE series, so a Zerodha file from a
    different day can hold the share under its other token. Searching only the named segment keeps the
    symbol from matching a different company on the other exchange.
    """
    listed = set(zerodha_instruments.loc[zerodha_instruments["segment"] == segment, "tradingsymbol"])

    def match(symbol: object) -> str | None:
        if pd.isna(symbol) or not str(symbol):
            return None
        return next(
            (candidate for candidate in (f"{symbol}", f"{symbol}-BE", f"{symbol}-BZ") if candidate in listed), None
        )

    return symbols.map(match)


def yahoo_tickers(symbols: pd.Series, suffix: str) -> pd.Series:
    """Append Yahoo's exchange suffix to an exchange's own symbol, leaving unlisted rows empty."""
    return symbols.map(lambda symbol: f"{symbol}{suffix}" if pd.notna(symbol) and str(symbol) else None)


def build_consolidated_dataframe(
    *,
    bse_equities: pd.DataFrame,
    nse_equities: pd.DataFrame,
    zerodha_instruments: pd.DataFrame,
) -> pd.DataFrame:
    equities = pd.merge(nse_equities, bse_equities, on="isin", how="outer")
    equities.set_index("isin", inplace=True)
    equities["ins_type"] = "equity"
    equities["zd_ns"] = match_zerodha_symbols(equities["nse_token"], zerodha_instruments, "NSE").fillna(
        match_zerodha_symbols_by_name(equities["nse_symbol"], zerodha_instruments, "NSE")
    )
    equities["zd_bo"] = match_zerodha_symbols(equities["bse_sc_code"], zerodha_instruments, "BSE")
    equities["yq_ns"] = yahoo_tickers(equities["nse_symbol"], ".NS")
    equities["yq_bo"] = yahoo_tickers(equities["bse_symbol"], ".BO")

    return equities[[column for column in CONSOLIDATED_COLUMNS if column != "isin"]]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def describe_source(path: Path, *, role: str, rows: int, trade_date: date | None = None) -> SourceRecord:
    """Identify one input file by name, trade date, and content hash.

    Only the file name is kept, not the local directory, because the database is committed and the
    path would only describe one machine. The hash and modification time identify the Zerodha
    instruments file, which carries no date of its own.
    """
    stat = path.stat()
    return SourceRecord(
        role=role,
        file_name=path.name,
        trade_date=trade_date.isoformat() if trade_date is not None else None,
        sha256=_file_sha256(path),
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        rows=rows,
    )


def _normalize(value: object) -> str | None:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    text = str(value)
    return text if text else None


def _records_by_isin(dataframe: pd.DataFrame, columns: list[str]) -> dict[str, dict[str, str | None]]:
    """Map each ISIN to its first row; a later row with the same ISIN is counted by ``_duplicate_isins``."""
    records: dict[str, dict[str, str | None]] = {}
    for isin, *values in dataframe[["isin", *columns]].itertuples(index=False, name=None):
        records.setdefault(
            str(isin), {column: _normalize(value) for column, value in zip(columns, values, strict=True)}
        )
    return records


def _duplicate_isins(dataframe: pd.DataFrame) -> set[str]:
    isins = dataframe["isin"].astype(str)
    return set(isins[isins.duplicated()])


def diff_consolidated(
    db_path: Path,
    rebuilt: pd.DataFrame,
    *,
    sample_size: int = DEFAULT_DIFF_SAMPLE_SIZE,
) -> RebuildDiff:
    """Compare a rebuilt consolidated table, indexed by ISIN, with the table currently in the database.

    Empty strings and NULLs count as the same empty value. A table with an older layout is compared on
    the columns both tables have, and ``compared_columns`` names them. When an ISIN appears more than
    once, its first row is compared and the others are counted as duplicates, so added, changed,
    unchanged, and duplicate rows add up to ``rebuilt_rows``, and kept, removed, and duplicate rows
    add up to ``current_rows``.
    """
    if sample_size < 0:
        raise ValueError("Sample size must not be negative.")

    current = pd.DataFrame(columns=list(CONSOLIDATED_COLUMNS))
    current_provenance = None
    # Check first: connect() would create an empty database file during a dry run.
    if db_path.exists():
        with connect(db_path) as con:
            current_provenance = read_latest_provenance(con)
            if table_exists(con, CONSOLIDATED_TABLE):
                current = pd.read_sql(f"SELECT * FROM {CONSOLIDATED_TABLE}", con, dtype=object)

    rebuilt_frame = rebuilt.reset_index()
    compared = [column for column in CONSOLIDATED_COLUMNS if column != "isin" and column in current.columns]
    before = _records_by_isin(current, compared)
    after = _records_by_isin(rebuilt_frame, compared)

    added = [isin for isin in after if isin not in before]
    removed = [isin for isin in before if isin not in after]
    changed: list[RowChange] = []
    column_changes = dict.fromkeys(compared, 0)
    for isin in after:
        if isin not in before:
            continue
        changes = {
            column: (before[isin][column], after[isin][column])
            for column in compared
            if before[isin][column] != after[isin][column]
        }
        if changes:
            changed.append(RowChange(isin=isin, changes=changes))
            for column in changes:
                column_changes[column] += 1

    rebuilt_rows = _records_by_isin(rebuilt_frame, [column for column in CONSOLIDATED_COLUMNS if column != "isin"])
    current_rows = _records_by_isin(current, [column for column in current.columns if column != "isin"])
    return RebuildDiff(
        current_rows=len(current),
        rebuilt_rows=len(rebuilt_frame),
        added=len(added),
        removed=len(removed),
        changed=len(changed),
        unchanged=len(after) - len(added) - len(changed),
        duplicate_current=len(current) - len(before),
        duplicate_rebuilt=len(rebuilt_frame) - len(after),
        compared_columns=compared,
        column_changes={column: count for column, count in column_changes.items() if count},
        sample_added=[{"isin": isin, **rebuilt_rows[isin]} for isin in sorted(added)[:sample_size]],
        sample_removed=[{"isin": isin, **current_rows[isin]} for isin in sorted(removed)[:sample_size]],
        sample_changed=sorted(changed, key=lambda change: change.isin)[:sample_size],
        sample_duplicates=sorted(_duplicate_isins(current) | _duplicate_isins(rebuilt_frame))[:sample_size],
        current_provenance=current_provenance,
    )


def preview_sources(paths: BhavcopyPaths) -> SourcePreview:
    """Load the three source files and report how many usable rows each holds."""
    require_existing_files(paths)

    return SourcePreview(
        bse_bhavcopy=paths.bse,
        bse_rows=len(load_bse_equities(paths.bse)),
        nse_bhavcopy=paths.nse,
        nse_rows=len(load_nse_equities(paths.nse)),
        zerodha_instruments=paths.zerodha,
        zerodha_rows=len(load_zerodha_instruments(paths.zerodha)),
    )


def write_consolidated_table(con: sqlite3.Connection, consolidated: pd.DataFrame) -> None:
    """Replace the consolidated table inside the caller's transaction: TEXT columns and an ISIN index."""
    frame = consolidated.reset_index()
    columns = list(frame.columns)
    column_definitions = ", ".join(f'"{column}" TEXT' for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    con.execute(f'DROP TABLE IF EXISTS "{CONSOLIDATED_TABLE}"')
    con.execute(f'CREATE TABLE "{CONSOLIDATED_TABLE}" ({column_definitions})')
    con.execute(f'CREATE INDEX "ix_{CONSOLIDATED_TABLE}_isin" ON "{CONSOLIDATED_TABLE}" ("isin")')
    con.executemany(
        f'INSERT INTO "{CONSOLIDATED_TABLE}" VALUES ({placeholders})',
        (
            tuple(
                None if value is None or (not isinstance(value, str) and pd.isna(value)) else str(value)
                for value in row
            )
            for row in frame.itertuples(index=False, name=None)
        ),
    )


def rebuild_database(
    paths: BhavcopyPaths,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    backup: bool = True,
    dry_run: bool = False,
    sample_size: int = DEFAULT_DIFF_SAMPLE_SIZE,
    progress: Callable[[str], None] | None = None,
) -> RebuildResult:
    def report(stage: str) -> None:
        if progress is not None:
            progress(stage)

    require_existing_files(paths)

    report("Loading BSE bhavcopy")
    bse_equities = load_bse_equities(paths.bse)
    report("Loading NSE bhavcopy")
    nse_equities = load_nse_equities(paths.nse)
    report("Loading Zerodha instruments")
    zerodha_instruments = load_zerodha_instruments(paths.zerodha)

    report("Consolidating")
    stocky = build_consolidated_dataframe(
        bse_equities=bse_equities,
        nse_equities=nse_equities,
        zerodha_instruments=zerodha_instruments,
    )

    report("Recording source files")
    built_at = datetime.now(UTC).isoformat()
    sources = [
        describe_source(
            paths.bse, role="bse_bhavcopy", rows=len(bse_equities), trade_date=parse_bse_bhavcopy_date(paths.bse)
        ),
        describe_source(
            paths.nse, role="nse_bhavcopy", rows=len(nse_equities), trade_date=parse_nse_bhavcopy_date(paths.nse)
        ),
        describe_source(paths.zerodha, role="zerodha_instruments", rows=len(zerodha_instruments)),
    ]

    report("Comparing with the current database")
    diff = diff_consolidated(db_path, stocky, sample_size=sample_size)

    def result(*, backup_path: Path | None = None, build_id: int | None = None) -> RebuildResult:
        return RebuildResult(
            rows=len(stocky),
            db_path=db_path,
            backup_path=backup_path,
            dry_run=dry_run,
            bse_bhavcopy=paths.bse,
            nse_bhavcopy=paths.nse,
            zerodha_instruments=paths.zerodha,
            built_at=built_at,
            build_id=build_id,
            sources=sources,
            diff=diff,
        )

    if dry_run:
        return result()

    initialize_database(db_path)
    backup_path = None
    if backup:
        report("Backing up database")
        backup_path = backup_database(db_path, backup_dir=backup_dir)

    report("Writing consolidated table")
    initialize_database(db_path)
    # One transaction, so the table and the record of what built it are written together or not at all.
    # pandas' to_sql commits on its own, so the table is written here with the same layout it produced.
    with connect(db_path) as con:
        con.execute("BEGIN")
        write_consolidated_table(con, stocky)
        build_id = record_build_provenance(
            con,
            built_at=built_at,
            stocky_version=__version__,
            consolidated_rows=len(stocky),
            sources=sources,
        )

    return result(backup_path=backup_path, build_id=build_id)
