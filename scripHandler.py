from csv import DictReader as DictReader
from cs50 import SQL

scrip = SQL("sqlite:///data/scrip.db")

nseBhavPath = f"res/NSE-20210401.csv"
bseBhavPath = f"res/BSE-20210401.csv"
