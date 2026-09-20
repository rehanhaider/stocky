from datetime import date
from pathlib import Path

import pytest

from stocky.sources import (
    BhavcopyPaths,
    bhavcopy_paths_for_date,
    bse_filename_for_date,
    discover_latest_bhavcopy_pair,
    nse_filename_for_date,
    parse_bse_bhavcopy_date,
    parse_nse_bhavcopy_date,
    require_existing_files,
    resolve_bhavcopy_paths,
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


def test_discover_latest_bhavcopy_pair_rejects_empty_directory(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="No matching BSE/NSE bhavcopy pair"):
        discover_latest_bhavcopy_pair(tmp_path)


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


def test_resolve_bhavcopy_paths_latest_overrides_other_selectors(tmp_path, market_csv_builder) -> None:
    older = market_csv_builder(tmp_path, trade_date=date(2021, 5, 3))
    newer = market_csv_builder(tmp_path, trade_date=date(2021, 9, 30))

    result = resolve_bhavcopy_paths(
        bse_bhavcopy=older.bse,
        nse_bhavcopy=older.nse,
        trade_date=date(2021, 5, 3),
        input_dir=tmp_path,
        zerodha=newer.zerodha,
        latest=True,
    )

    assert result.bse == newer.bse
    assert result.nse == newer.nse


def test_resolve_bhavcopy_paths_uses_explicit_pair(tmp_path) -> None:
    bse = tmp_path / "chosen-bse.csv"
    nse = tmp_path / "chosen-nse.csv"
    zerodha = tmp_path / "chosen-zerodha.csv"

    result = resolve_bhavcopy_paths(bse_bhavcopy=bse, nse_bhavcopy=nse, zerodha=zerodha)

    assert result == BhavcopyPaths(bse=bse, nse=nse, zerodha=zerodha)


def test_resolve_bhavcopy_paths_rejects_incomplete_explicit_pair(tmp_path) -> None:
    with pytest.raises(ValueError, match="Provide both"):
        resolve_bhavcopy_paths(bse_bhavcopy=tmp_path / "bse.csv")


def test_resolve_bhavcopy_paths_uses_date_derived_names(tmp_path) -> None:
    zerodha = tmp_path / "instruments.csv"

    result = resolve_bhavcopy_paths(
        trade_date=date(2021, 5, 3),
        input_dir=tmp_path,
        zerodha=zerodha,
    )

    assert result == BhavcopyPaths(
        bse=tmp_path / "BSE-EQ_ISINCODE_030521.CSV",
        nse=tmp_path / "NSE-cm03MAY2021bhav.csv",
        zerodha=zerodha,
    )


def test_resolve_bhavcopy_paths_defaults_to_latest_pair(tmp_path, market_csv_builder) -> None:
    older = market_csv_builder(tmp_path, trade_date=date(2021, 5, 3))
    newer = market_csv_builder(tmp_path, trade_date=date(2021, 9, 30))

    result = resolve_bhavcopy_paths(input_dir=tmp_path, zerodha=older.zerodha)

    assert result.bse == newer.bse
    assert result.nse == newer.nse


def test_bhavcopy_paths_for_date_uses_input_and_zerodha_paths(tmp_path) -> None:
    zerodha = tmp_path / "zerodha.csv"

    result = bhavcopy_paths_for_date(date(2026, 5, 22), input_dir=tmp_path, zerodha=zerodha)

    assert result == BhavcopyPaths(
        bse=tmp_path / "BhavCopy_BSE_CM_0_0_0_20260522_F_0000.CSV",
        nse=tmp_path / "BhavCopy_NSE_CM_0_0_0_20260522_F_0000.csv.zip",
        zerodha=zerodha,
    )


def test_require_existing_files_accepts_present_files(tmp_path, market_csv_builder) -> None:
    require_existing_files(market_csv_builder(tmp_path))


def test_require_existing_files_lists_all_missing_paths(tmp_path) -> None:
    paths = BhavcopyPaths(
        bse=tmp_path / "missing-bse.csv",
        nse=tmp_path / "missing-nse.csv",
        zerodha=tmp_path / "missing-zerodha.csv",
    )

    with pytest.raises(FileNotFoundError) as error:
        require_existing_files(paths)

    assert str(paths.bse) in str(error.value)
    assert str(paths.nse) in str(error.value)
    assert str(paths.zerodha) in str(error.value)
