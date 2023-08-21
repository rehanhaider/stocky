"""
**DOCUMENTATION**
***https://towardsdatascience.com/the-unofficial-yahoo-finance-api-32dcf5d53df***
"""
import json
import sqlite3 as lite

import yahooquery as yq
import pandas as pd

from rich import print as rprint


class YahooDataManager:
    """
    Data manager class
    """

    def __init__(self, config):
        self.path = config["paths"]["input"]["yahoo"]
        db_con = lite.connect(f"{config['paths']['output']}/stocky.db")
        try:
            data = pd.read_sql_query("SELECT * FROM equities WHERE yq_symbol is not NULL", db_con)
        except pd.errors.DatabaseError as db_error:
            raise db_error
        self._symbols = data.to_dict("records")
        # print(self.symbols)
        db_con.commit()
        db_con.close()

    def get_symbol_keys(self):
        """
        Returns the keys of the symbols from the database that needs to be matched with the yahoo finance API
        """
        return self._symbols.key()  # type: ignore

    def update_data(self, key="zd_symbol") -> None:
        """
        Updates the data from the yahoo finance API and saves it on disk
        """
        exchange = "BSE"

        def get_api_data(symbol, exchange):
            ex = "NS" if exchange == "NSE" else ("BO" if exchange == "BSE" else "error")
            if ex == "error":
                print(f"{symbol}, {exchange}: Incorrect Exchange Provided")
                return ex
            rprint(f"INFO: {symbol}.{exchange} Processing")
            ticker = yq.Ticker(f"{symbol}.{ex}")
            data = ticker.all_modules
            if data[f"{symbol}.{ex}"] == f"Quote not found for ticker symbol: {symbol}.{ex}":
                print(f"{symbol}, {exchange}: No match found")
                return data[f"{symbol}.{ex}"]

            rprint("INFO: Found response")
            rprint("INFO: Writing to file")
            with open(f"{self.path}/{symbol}.{ex}.json", "w", encoding="utf-8") as file:
                file.write(json.dumps(data))

        for symbol in self._symbols:
            if symbol[key] is not None:
                try:
                    get_api_data(symbol[key], exchange)
                except KeyError:
                    continue
