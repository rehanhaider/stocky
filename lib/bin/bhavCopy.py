from stocky.config import DEFAULT_BHAVCOPY_DIR, DEFAULT_ZERODHA_INSTRUMENTS
from stocky.pipeline import rebuild_database
from stocky.sources import discover_latest_bhavcopy_pair


def bhavUpdate() -> None:
    paths = discover_latest_bhavcopy_pair(DEFAULT_BHAVCOPY_DIR, DEFAULT_ZERODHA_INSTRUMENTS)
    rebuild_database(paths)
