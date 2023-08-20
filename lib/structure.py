"""
Creates folder structure for the project
"""


from pathlib import Path


def create_folders():
    """
    Creates the folder structure for the project
    """
    # Create a directory named data in the current directory
    Path("data/maket_data/zerodha").mkdir(parents=True, exist_ok=True)
    Path("data/maket_data/yahoo").mkdir(parents=True, exist_ok=True)
    Path("data/maket_data/bse").mkdir(parents=True, exist_ok=True)
    Path("data/maket_data/nse").mkdir(parents=True, exist_ok=True)
    Path("output").mkdir(parents=True, exist_ok=True)
