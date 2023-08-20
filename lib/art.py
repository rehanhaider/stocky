"""Generates the ASCII art.
"""

from rich import print as rprint


def generate_ascii_art() -> str:
    """Generates the ASCII art."""

    art = """
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

    return art


def print_welcome_message() -> None:
    """Prints the welcome message."""
    rprint("*" * 100)
    for line in generate_ascii_art().splitlines():
        rprint(" " * 15, end="")
        rprint(line)
    rprint()
    rprint("[bold]Stocky McStockface[/bold]: Stock mapper between Yahoo, Zerodha, BSE, & NSE for Indian stock markets")
    rprint("*" * 100)
