"""
Refresh data from all sources
"""
import sys

from rich import print as rprint
import pandas as pd

from .lib.yahoo import YahooDataManager
from .lib.zerodha import download_zerodha_data
from .config import get_config


def _refresh_yahoo_data():
    """Refresh from yahoo finance"""
    try:
        ydm = YahooDataManager(get_config())
    except pd.errors.DatabaseError:
        rprint("[bold red]Error[/bold red]: Please run the merging of bhavcopy data first")
        return

    ydm.update_data()


def _refresh_bse_data():
    """Refresh from BSE"""


def _refresh_nse_data():
    """Refresh from NSE"""


def _refresh_zerodha_data():
    """Refresh from Zerodha"""
    download_zerodha_data()


def _refresh_all_data():
    """Refresh from all sources"""
    _refresh_zerodha_data()
    _refresh_bse_data()
    _refresh_nse_data()
    _refresh_yahoo_data()


def print_options():
    """Print options"""
    rprint("[bold]1. [/bold]Refresh from all sources")
    rprint("[bold]2. [/bold]Refresh stock data from Zerodha")
    rprint("[bold]3. [/bold]Refresh bhavcopy from BSE")
    rprint("[bold]4. [/bold]Refresh bhavcopy from NSE")
    rprint("[bold]5. [/bold]Refresh stock data from Yahoo Finance")
    rprint("[bold]6. [/bold]Go back to main menu")
    rprint("[bold]7. [/bold]Exit")


def refresh_data():
    """Refresh data"""

    run_screen = True
    while run_screen:
        # print options
        print_options()

        # get user input
        user_input = input("Enter your choice: ")

        # process user input
        match user_input:
            case "1":
                _refresh_all_data()
            case "2":
                _refresh_zerodha_data()
            case "3":
                _refresh_bse_data()
            case "4":
                _refresh_nse_data()
            case "5":
                _refresh_yahoo_data()
            case "6":
                rprint("Going back to main menu...")
                run_screen = False
            case "7":
                rprint("Exiting...")
                sys.exit()
