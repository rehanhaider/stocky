from cs50 import SQL
import sqlite3 as lite
import pandas as pd


nseBhavPath = f"bhavData/NSE-20210401.csv"
bseBhavPath = f"bhavData/BSE-20210401.csv"

nseBhav = pd.read_csv(nseBhavPath)
bseBhav = pd.read_csv(bseBhavPath)

db = lite.connect("scrip.db")

nseBhav.to_sql("nseBhav", db)
bseBhav.to_sql("bseBhav", db)

db.execute("""CREATE VIEW bhavList AS
                SELECT bse.isin, bse.bse_symbol, nse.nse_symbol, bse.name
                FROM 
                    (SELECT SC_CODE as bse_symbol, SC_NAME as name, ISIN_CODE as isin 
                    FROM bseBhav) bse
                LEFT OUTER JOIN
                    (SELECT SYMBOL as nse_symbol, ISIN as isin
                    FROM nseBhav) nse
                    on bse.isin = nse.isin""")

db.commit()
db.close()

