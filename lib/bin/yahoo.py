from stocky.yahoo import YahooDataManager


class yahooDataManager(YahooDataManager):
    def updateData(self, key: str = "zd_symbol") -> None:
        self.update_data(key=key, exchange="BSE")
