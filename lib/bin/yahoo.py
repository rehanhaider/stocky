"""
**DOCUMENTATION**  
***https://towardsdatascience.com/the-unofficial-yahoo-finance-api-32dcf5d53df***
"""

import yahooquery as yq
import json
import sqlite3 as lite
import pandas as pd
from termcolor import colored


class yahooDataManager:
    def __init__(self):
        self.path = f"data/marketData/yahoo/apiResponse"
        dbCon = lite.connect("data/output/stocky.db")
        data = pd.read_sql_query("SELECT * FROM equities WHERE yq_symbol is not NULL", dbCon)
        self.symbols = data.to_dict("records")
        # print(self.symbols)
        dbCon.commit()
        dbCon.close()

    def getSymbolKeys(self):
        return self.symbols.key()

    def updateData(self, key="zd_symbol") -> None:
        exchange = "BSE"

        def getAPIData(symbol, exchange):
            ex = "NS" if exchange == "NSE" else ("BO" if exchange == "BSE" else "error")
            if ex == "error":
                print(f"{symbol}, {exchange}: Incorrect Exchange Provided")
                return ex
            print(colored(f"INFO: {symbol}.{exchange} Processing", "yellow"))
            ticker = yq.Ticker(f"{symbol}.{ex}")
            data = ticker.all_modules
            if data[f"{symbol}.{ex}"] == f"Quote not found for ticker symbol: {symbol}.{ex}":
                print(f"{symbol}, {exchange}: No match found")
                return data[f"{symbol}.{ex}"]
            else:
                print(colored(f"INFO: Found response", "yellow"))
                print(colored(f"INFO: Writing to file", "yellow"))
                with open(f"{self.path}/{symbol}.{ex}.json", "w") as file:
                    file.write(json.dumps(data))

        for symbol in self.symbols:
            if symbol[key] != None:
                try:
                    getAPIData(symbol[key], exchange)
                except KeyError:
                    continue
