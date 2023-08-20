"""Generates the ASCII art.
"""

from rich import print as rprint


ASCII_ART: str = """
 $$$$$$\    $$\                         $$\\
$$  __$$\   $$ |                        $$ |
$$ /  \__|$$$$$$\    $$$$$$\   $$$$$$$\ $$ |  $$\ $$\   $$\\
\$$$$$$\  \_$$  _|  $$  __$$\ $$  _____|$$ | $$  |$$ |  $$ |
 \____$$\   $$ |    $$ /  $$ |$$ /      $$$$$$  / $$ |  $$ |
$$\   $$ |  $$ |$$\ $$ |  $$ |$$ |      $$  _$$<  $$ |  $$ |
\$$$$$$  |  \$$$$  |\$$$$$$  |\$$$$$$$\ $$ | \$$\ \$$$$$$$ |
 \______/    \____/  \______/  \_______|\__|  \__| \____$$ |
                                                  $$\   $$ |
                                                  \$$$$$$  |
                                                   \______/
"""


def print_welcome_message() -> None:
    """Prints the welcome message."""
    rprint("*" * 100)
    for line in ASCII_ART.splitlines():
        rprint(" " * 15, end="")
        rprint(line)
    rprint()
    rprint("[bold]Stocky McStockface[/bold]: Stock mapper between Yahoo, Zerodha, BSE, & NSE for Indian stock markets")
    rprint("*" * 100)


def print_options() -> None:
    """Prints the options."""
    rprint("[bold blue]Options:[/bold blue]")
    rprint("[bold]1.[/bold] Merge and generate ISIN mapping")
    rprint("[bold]2.[/bold] Download & refresh data")
    rprint("[bold]3.[/bold] Exit")
    rprint()
