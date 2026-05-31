from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from stocky.config import DEFAULT_BHAVCOPY_DIR, DEFAULT_ZERODHA_INSTRUMENTS

MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

MONTH_NAMES = {value: key for key, value in MONTHS.items()}
NSE_UDIFF_START_DATE = date(2024, 7, 8)

BSE_BHAVCOPY_RE = re.compile(r"^BSE-EQ_ISINCODE_(?P<day>\d{2})(?P<month>\d{2})(?P<year>\d{2})\.CSV$", re.IGNORECASE)
NSE_LEGACY_BHAVCOPY_RE = re.compile(
    r"^NSE-cm(?P<day>\d{2})(?P<month>[A-Z]{3})(?P<year>\d{4})bhav\.csv$",
    re.IGNORECASE,
)
NSE_UDIFF_BHAVCOPY_RE = re.compile(
    r"^BhavCopy_NSE_CM_0_0_0_(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})_F_0000\.csv(?:\.zip)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BhavcopyPaths:
    bse: Path
    nse: Path
    zerodha: Path


def bse_filename_for_date(trade_date: date) -> str:
    return f"BSE-EQ_ISINCODE_{trade_date:%d%m%y}.CSV"


def nse_filename_for_date(trade_date: date) -> str:
    if trade_date >= NSE_UDIFF_START_DATE:
        return f"BhavCopy_NSE_CM_0_0_0_{trade_date:%Y%m%d}_F_0000.csv.zip"

    return f"NSE-cm{trade_date:%d}{MONTH_NAMES[trade_date.month]}{trade_date:%Y}bhav.csv"


def bhavcopy_paths_for_date(
    trade_date: date,
    input_dir: Path = DEFAULT_BHAVCOPY_DIR,
    zerodha: Path = DEFAULT_ZERODHA_INSTRUMENTS,
) -> BhavcopyPaths:
    return BhavcopyPaths(
        bse=input_dir / bse_filename_for_date(trade_date),
        nse=input_dir / nse_filename_for_date(trade_date),
        zerodha=zerodha,
    )


def parse_bse_bhavcopy_date(path: Path) -> date | None:
    match = BSE_BHAVCOPY_RE.match(path.name)
    if not match:
        return None

    year = 2000 + int(match.group("year"))
    return date(year, int(match.group("month")), int(match.group("day")))


def parse_nse_bhavcopy_date(path: Path) -> date | None:
    legacy_match = NSE_LEGACY_BHAVCOPY_RE.match(path.name)
    if legacy_match:
        month = MONTHS.get(legacy_match.group("month").upper())
        if month is None:
            return None

        return date(int(legacy_match.group("year")), month, int(legacy_match.group("day")))

    udiff_match = NSE_UDIFF_BHAVCOPY_RE.match(path.name)
    if udiff_match:
        return date(
            int(udiff_match.group("year")),
            int(udiff_match.group("month")),
            int(udiff_match.group("day")),
        )

    return None


def discover_latest_bhavcopy_pair(
    input_dir: Path = DEFAULT_BHAVCOPY_DIR,
    zerodha: Path = DEFAULT_ZERODHA_INSTRUMENTS,
) -> BhavcopyPaths:
    bse_files: dict[date, Path] = {}
    nse_files: dict[date, Path] = {}

    for candidate in input_dir.glob("*"):
        if not candidate.is_file():
            continue

        bse_date = parse_bse_bhavcopy_date(candidate)
        if bse_date is not None:
            bse_files[bse_date] = candidate
            continue

        nse_date = parse_nse_bhavcopy_date(candidate)
        if nse_date is not None:
            nse_files[nse_date] = candidate

    available_dates = sorted(set(bse_files).intersection(nse_files), reverse=True)
    if not available_dates:
        raise FileNotFoundError(f"No matching BSE/NSE bhavcopy pair found in {input_dir}")

    latest = available_dates[0]
    return BhavcopyPaths(bse=bse_files[latest], nse=nse_files[latest], zerodha=zerodha)


def resolve_bhavcopy_paths(
    *,
    bse_bhavcopy: Path | None = None,
    nse_bhavcopy: Path | None = None,
    trade_date: date | None = None,
    input_dir: Path = DEFAULT_BHAVCOPY_DIR,
    zerodha: Path = DEFAULT_ZERODHA_INSTRUMENTS,
    latest: bool = False,
) -> BhavcopyPaths:
    if bse_bhavcopy or nse_bhavcopy:
        if not bse_bhavcopy or not nse_bhavcopy:
            raise ValueError("Provide both --bse-bhavcopy and --nse-bhavcopy, or neither.")
        return BhavcopyPaths(bse=bse_bhavcopy, nse=nse_bhavcopy, zerodha=zerodha)

    if trade_date is not None:
        return bhavcopy_paths_for_date(trade_date, input_dir=input_dir, zerodha=zerodha)

    if latest:
        return discover_latest_bhavcopy_pair(input_dir=input_dir, zerodha=zerodha)

    return discover_latest_bhavcopy_pair(input_dir=input_dir, zerodha=zerodha)


def require_existing_files(paths: BhavcopyPaths) -> None:
    missing = [str(file_path) for file_path in (paths.bse, paths.nse, paths.zerodha) if not file_path.is_file()]
    if missing:
        missing_list = "\n".join(f"- {item}" for item in missing)
        raise FileNotFoundError(f"Required input files are missing:\n{missing_list}")
