from datetime import date
from pathlib import Path

from stocky.sources import (
    bse_filename_for_date,
    discover_latest_bhavcopy_pair,
    nse_filename_for_date,
    parse_bse_bhavcopy_date,
    parse_nse_bhavcopy_date,
)


def test_bhavcopy_filename_generation() -> None:
    legacy_trade_date = date(2021, 5, 3)
    udiff_trade_date = date(2026, 5, 22)

    assert bse_filename_for_date(legacy_trade_date) == "BSE-EQ_ISINCODE_030521.CSV"
    assert bse_filename_for_date(udiff_trade_date) == "BhavCopy_BSE_CM_0_0_0_20260522_F_0000.CSV"
    assert nse_filename_for_date(legacy_trade_date) == "NSE-cm03MAY2021bhav.csv"
    assert nse_filename_for_date(udiff_trade_date) == "BhavCopy_NSE_CM_0_0_0_20260522_F_0000.csv.zip"


def test_bhavcopy_filename_parsing() -> None:
    assert parse_bse_bhavcopy_date(Path("BSE-EQ_ISINCODE_030521.CSV")) == date(2021, 5, 3)
    assert parse_bse_bhavcopy_date(Path("BhavCopy_BSE_CM_0_0_0_20260717_F_0000.CSV")) == date(2026, 7, 17)
    assert parse_nse_bhavcopy_date(Path("NSE-cm03MAY2021bhav.csv")) == date(2021, 5, 3)
    assert parse_nse_bhavcopy_date(Path("BhavCopy_NSE_CM_0_0_0_20260522_F_0000.csv.zip")) == date(2026, 5, 22)
    assert parse_nse_bhavcopy_date(Path("BhavCopy_NSE_CM_0_0_0_20260522_F_0000.csv")) == date(2026, 5, 22)


def test_discover_latest_bhavcopy_pair(tmp_path) -> None:
    (tmp_path / "BSE-EQ_ISINCODE_030521.CSV").write_text("", encoding="utf-8")
    (tmp_path / "NSE-cm03MAY2021bhav.csv").write_text("", encoding="utf-8")
    (tmp_path / "BSE-EQ_ISINCODE_300921.CSV").write_text("", encoding="utf-8")
    (tmp_path / "NSE-cm30SEP2021bhav.csv").write_text("", encoding="utf-8")
    zerodha = tmp_path / "instruments.csv"
    zerodha.write_text("", encoding="utf-8")

    paths = discover_latest_bhavcopy_pair(tmp_path, zerodha)

    assert paths.bse.name == "BSE-EQ_ISINCODE_300921.CSV"
    assert paths.nse.name == "NSE-cm30SEP2021bhav.csv"


def test_discover_latest_bhavcopy_pair_prefers_newer_udiff_files(tmp_path) -> None:
    (tmp_path / "BSE-EQ_ISINCODE_030521.CSV").write_text("", encoding="utf-8")
    (tmp_path / "NSE-cm03MAY2021bhav.csv").write_text("", encoding="utf-8")
    (tmp_path / "BhavCopy_BSE_CM_0_0_0_20260717_F_0000.CSV").write_text("", encoding="utf-8")
    (tmp_path / "BhavCopy_NSE_CM_0_0_0_20260717_F_0000.csv.zip").write_text("", encoding="utf-8")
    zerodha = tmp_path / "instruments.csv"
    zerodha.write_text("", encoding="utf-8")

    paths = discover_latest_bhavcopy_pair(tmp_path, zerodha)

    assert paths.bse.name == "BhavCopy_BSE_CM_0_0_0_20260717_F_0000.CSV"
    assert paths.nse.name == "BhavCopy_NSE_CM_0_0_0_20260717_F_0000.csv.zip"
