import sqlite3 as lite
import pandas as pd
from termcolor import colored
from os import path


def bhavUpdate() -> None:
    # #Process NSE & BSE Bhav copies
    # **Step 1:** ***Load NSE & BSE bhav copies into dataframe***
    bseBhavPath = f"data/marketData/bhavCopies/BSE-EQ_ISINCODE_030521.CSV"
    nseBhavPath = f"data/marketData/bhavCopies/NSE-cm03MAY2021bhav.csv"

    if path.isfile(bseBhavPath) and path.isfile(nseBhavPath):
        print(colored("Found NSE & BSE Bhav Copies", "yellow"))
    elif path.isfile(bseBhavPath) and not path.isfile(nseBhavPath):
        print(colored("NSE Bhav Copy is missing", "red"))
    elif not path.isfile(bseBhavPath) and path.isfile(nseBhavPath):
        print(colored("BSE Bhav Copy is missing", "red"))
    else:
        print(colored("Both Bhav Copies are missing", "red"))

    # Read both files
    print(colored("Processing Bhav Copies", "yellow"))
    bseBhav = pd.read_csv(bseBhavPath)
    nseBhav = pd.read_csv(nseBhavPath)

    # Remove Trailing spaces
    bseBhav = bseBhav.replace({r"^\s*|\s*$": ""}, regex=True)
    nseBhav = nseBhav.replace({r"^\s*|\s*$": ""}, regex=True)

    # **Step 2:** ***Slice BSEBHAV to have only equity data & relevant columns***
    # Select only equities
    bseEquities = bseBhav[bseBhav["SC_TYPE"] == "Q"]

    # Slice only required data
    bseEquities = bseEquities[["ISIN_CODE", "SC_CODE", "SC_NAME"]]
    bseEquities.rename(columns={"ISIN_CODE": "isin", "SC_CODE": "bse_sc_code", "SC_NAME": "bse_sc_name"}, inplace=True)

    # Values are loaded as integers, convert to string for better handling
    bseEquities["bse_sc_code"] = bseEquities["bse_sc_code"].astype(str)

    # **Step 3:** ***Slice NSEBHAV to have only equity data & relevant columns***
    # Applicable only for Sovereign Gold Bonds, kept for future use
    # nseEquities["SYMBOL"] = np.where(nseEquities["SERIES"] == "GB", nseEquities["SYMBOL"] +"-" + nseEquities["SERIES"], nseEquities["SYMBOL"])

    # Select only equities
    nseEquities = nseBhav[nseBhav["SERIES"] == "EQ"]

    # Slice only required data
    nseEquities = nseEquities[["ISIN", "SYMBOL"]]

    # Prepare data with matching columns to Zerodha
    nseEquities.rename(columns={"ISIN": "isin", "SYMBOL": "nse_symbol", "SERIES": "nse_series"}, inplace=True)

    # **Step 4:** ***Merge both lists using outer join***
    equities = pd.merge(nseEquities, bseEquities, on="isin", how="outer")
    equities.set_index("isin", inplace=True)

    # **Step 5:** ***Create a new DataFrame that lists instrument type***
    equities["ins_type"] = "equity"

    # #Match zerodha symbols
    print(colored("Merging Zerodha instruments", "yellow"))
    # **Step 6:** ***Read Zerodha instruments and extract only equity and relevant fields***
    zerodha_instruments = pd.read_csv("data/marketData/zerodha/instruments.csv")
    zerodha_instruments = zerodha_instruments.query("segment=='BSE' or segment=='NSE'")
    zerodha_instruments = zerodha_instruments[["exchange_token", "tradingsymbol"]]
    zerodha_instruments["exchange_token"] = zerodha_instruments["exchange_token"].astype(str)
    # zerodha_instruments[zerodha_instruments["exchange_token"]=="890145"]["tradingsymbol"].item()

    # **Step 7:** **Create a list of all unique zerodha equity instruments***
    # listObjects = list(equities["nse_symbol"]) + list(equities["bse_sc_code"])
    setZerodha = list(zerodha_instruments["tradingsymbol"]) + list(zerodha_instruments["exchange_token"])
    setZerodha = [item for item in setZerodha if str(item) != "nan"]
    setZerodha = set(setZerodha)

    # **Step 8:** ***Match zerodha symbols to isin***
    def f(row):
        if row["nse_symbol"] in setZerodha:
            val = row["nse_symbol"]
        elif row["bse_sc_code"] in setZerodha:
            try:
                val = zerodha_instruments[zerodha_instruments["exchange_token"] == row["bse_sc_code"]][
                    "tradingsymbol"
                ].item()
            except Exception as e:
                val = None
        else:
            val = None
        return val

    equities["zd_symbol"] = equities.apply(f, axis=1)

    # # Match with yahoo names for stocks that have been found already
    print(colored("Merging yahoo symbols", "yellow"))

    import glob
    import os

    print("Here")
    yahooList = glob.glob("data/marketData/yahoo/apiResponse/*.json")
    for row in range(len(yahooList)):
        # print("Here")
        # print(yahooList)
        # yahooList[row] = yahooList[row][29:-5]
        yahooList[row] = os.path.basename(yahooList[row])[:-5]

    equities["yq_symbol"] = [
        nse_symbol
        if f"{nse_symbol}.NS" in yahooList or f"{nse_symbol}.BO" in yahooList
        else zd_symbol
        if f"{zd_symbol}.NS" in yahooList or f"{zd_symbol}.BO" in yahooList
        else None
        for nse_symbol, zd_symbol in zip(equities["nse_symbol"], equities["zd_symbol"])
    ]

    # # Save data to new file
    stocky = equities[["ins_type", "zd_symbol", "yq_symbol", "nse_symbol", "bse_sc_code", "bse_sc_name"]]

    # # Create a copy of the stocky.db
    print(colored("Backing up existing db", "yellow"))
    from shutil import copyfile
    from datetime import datetime

    timestamp = str(datetime.now()).replace(":", "-").split(".")[0]
    copyfile("data/output/stocky.db", f"data/output/backup/{timestamp}-stocky.db")

    # **Step 9:** ***Open db, delete previous data, update new data***
    print(colored("Saving to stocky.db", "yellow"))
    con = lite.connect("data/output/stocky.db")
    cur = con.cursor()
    try:
        cur.execute("DROP TABLE consolidated")
    except Exception as e:
        print(e)

    stocky.to_sql("consolidated", con)
    con.commit()
    con.close()
    print(colored("Completed updating stocky.db", "yellow"))
