"""
Get preset configs from JSON file
"""

import json


def get_config() -> dict:
    """
    Get preset configs from JSON file
    """
    with open("config.json", "r", encoding="utf-8") as file:
        config = dict(json.load(file))

    return config
