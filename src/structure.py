"""
Creates folder structure for the project
"""


from pathlib import Path
from .config import get_config


def create_folders():
    """
    Creates the folder structure for the project
    """
    # Create a directory named data in the current directory

    input_paths: dict = get_config()["paths"]["input"]
    output_path: str = get_config()["paths"]["output"]

    # for key, value in input_paths.items():
    #    Path(value).mkdir(parents=True, exist_ok=True)

    # for key, value in output_paths.items():
    #    Path(value).mkdir(parents=True, exist_ok=True)

    for item in input_paths:
        Path(input_paths[item]).mkdir(parents=True, exist_ok=True)

    Path(output_path).mkdir(parents=True, exist_ok=True)
