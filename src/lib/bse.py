"""
# -*- coding: utf-8 -*-
# download bhavcopy from bse
"""
import os
import sys
from logging import Logger
from datetime import date
from zipfile import ZipFile
import requests


# initialize logger
logger = Logger(__name__)


def check_bhav_bse(file_name: str) -> bool:
    """
    Check if bhavcopy from BSE exists
    """
    # check if the file exists
    logger.info("Checking if the file exists")
    return os.path.exists(file_name)


def get_bhav_bse(file_date: date = date.today(), force_download: bool = False):
    """
    file_date: Bhav copy's date when it was generated. Default is today but will not work on a holiday.
    force_download: Download a fresh version of Bhav copy even if it exists
    """
    # get the filename
    file_name = "EQ_ISINCODE_" + file_date.strftime("%d%m%y") + "_CSV.ZIP"
    # check if the file exists
    if not check_bhav_bse(file_name):
        # get the url
        url = "https://www.bseindia.com/download/BhavCopy/Equity/" + file_name
        # get the file

        response = requests.get(url, allow_redirects=True, timeout=100)
        if response.status_code != 200:
            print(f"No Bhavcopy available for {file_date.strftime('%d-%m-%Y')}")
            sys.exit()

        print(response.status_code)
        # write the file
        with open(file_name, "wb") as file:
            file.write(response.content)
        # unzip the file
        with ZipFile(file_name, "r") as zip_obj:
            # Extract all the contents of zip file in current directory
            zip_obj.extractall()
        # delete the zip file
        # os.remove(file_name)

    print("Bhav copy exists: User -f to force download a new version")
