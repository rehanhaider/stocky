from stocky.yahoo import YahooDataManager


class yahooDataManager(YahooDataManager):
    def updateData(self, exchange: str = "BSE") -> None:
        self.update_data(exchange=exchange)
