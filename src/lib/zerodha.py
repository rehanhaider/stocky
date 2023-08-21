"""
Downloads zerodha data
"""

import json
import requests

from rich import print as rprint


def download_zerodha_data():
    """Download Zerodha data"""

    with open("config.json", "r", encoding="utf-8") as file:
        config = json.load(file)

    instrument_url = config["data_urls"]["zerodha"]
    download_path = config["paths"]["input"]["zerodha"]

    rprint("[bold green]Downloading Zerodha data...[/bold green]")

    try:
        response = requests.get(url=instrument_url, allow_redirects=True, timeout=100)
    except requests.exceptions.SSLError:
        rprint("[bold red]SSL Error[/bold red]")
        while True:
            choice = input("Attempt to download without verifying SSL Certificate? (Y/N): ")
            if choice.lower() == "y":
                response = requests.get(url=instrument_url, allow_redirects=True, timeout=100, verify=False)
                break

            if choice.lower() == "n":
                rprint("[bold red]SSL Error: Please check if you are behind a VPN[/bold red]")
                return

            rprint("[bold red]Invalid choice[/bold red]")
            continue

    if response.status_code == 200:
        with open(f"{download_path}/zerodha.csv", "wb") as file:
            file.write(response.content)
        rprint("[bold green]Download complete![/bold green]")
