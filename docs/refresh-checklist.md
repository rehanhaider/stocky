# Quarterly refresh checklist

Use this checklist to refresh `data/output/stocky.db`. Stocky treats exchange bhavcopies as manually downloaded local
inputs, so step 1 stays manual.

1. Download the inputs into the ignored local folders:
   - the BSE and NSE bhavcopies for one trade date into `data/marketData/bhavCopies`;
   - the Zerodha instruments file from `https://api.kite.trade/instruments` to `data/marketData/zerodha/instruments.csv`.
   - for mutual fund imports, the Zerodha mutual fund instruments file from `https://api.kite.trade/mf/instruments` to
     `data/marketData/zerodha/mf_instruments.csv`, and AMFI scheme codes from
     `https://www.amfiindia.com/spages/NAVAll.txt` to `data/marketData/amfi/NAVAll.txt`.
   - broker cash-market files:
     - `https://growwapi-assets.groww.in/instruments/instrument.csv` to `data/marketData/brokers/groww/instrument.csv`;
     - `https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz` to `data/marketData/brokers/upstox/complete.json.gz`;
     - `https://v2api.aliceblueonline.com/restpy/static/contract_master/NSE.csv` to `data/marketData/brokers/aliceblue/NSE.csv`;
     - `https://v2api.aliceblueonline.com/restpy/static/contract_master/BSE.csv` to `data/marketData/brokers/aliceblue/BSE.csv`.
   Steps 2 to 5 can also run as one command. `stocky refresh` runs the rebuild, `indices import`, `debt import`,
   `mf import`, `brokers import`, `yahoo update` for NSE and then BSE, and `summary`, in that order, with each command's
   default input files. The rebuild keeps the SGB rows when the current table has them. `--dry-run` shows every step's
   preview and writes nothing. The refresh stops at the first step that fails and names it and the steps that
   completed. It prints the summary on stdout, so run step 5 afterwards to write `data/output/refresh-summary.md`:

   ```bash
   uv run stocky refresh --date YYYY-MM-DD --stale-days 90 --dry-run
   uv run stocky refresh --date YYYY-MM-DD --stale-days 90
   ```

2. Preview the rebuild. Nothing is written:

   ```bash
   uv run stocky rebuild --date YYYY-MM-DD --include-sgb --dry-run
   ```

   Check the source row counts, the added, removed, and changed rows, and the sample differences. A large number of
   removed rows usually means a wrong or partial bhavcopy.
3. Rebuild. This backs up the database, replaces the `consolidated` table, and records the source files:

   ```bash
   uv run stocky rebuild --date YYYY-MM-DD --include-sgb
   ```

   Keep `--include-sgb`: a rebuild without the flag drops the SGB rows. It needs UDiFF bhavcopies.

4. Preview the Yahoo update, then run it for each exchange. `--stale-days` fetches only tickers with no response or
   an old one; add `--symbols` to refresh named tickers only:

   ```bash
   uv run stocky yahoo update --exchange NSE --stale-days 90 --dry-run
   uv run stocky yahoo update --exchange NSE --stale-days 90
   uv run stocky yahoo update --exchange BSE --stale-days 90
   ```

   For mutual funds, run `uv run stocky mf import` using the Zerodha mutual fund instruments and AMFI files from step 1.

   For market indices, run `uv run stocky indices import` using the Zerodha instruments file from step 1.

   For BSE debt, download the archive into `data/marketData/bhavCopies/bseDebt` and run `uv run stocky debt import`.

   For broker cash instruments, run `uv run stocky brokers import` after the rebuild. This imports all three
   brokers, excludes derivatives and indices, and refreshes Alice Blue's ISIN lookups against `consolidated`.
   Each broker replaces its own rows in one transaction; a later failure does not undo earlier brokers.

   Each of these imports records its source files in `provenance_imports`, which `stocky status` shows.

5. Validate the database and write the summary. The command exits with status 1 when a check fails:

   ```bash
   uv run stocky summary --output data/output/refresh-summary.md
   ```

6. Read the summary. Every check must pass. Compare the trade dates and row counts with the previous summary.
7. Commit `data/output/stocky.db` and `data/output/refresh-summary.md` together.

## What the summary checks

| Check | Fails when |
| --- | --- |
| Consolidated table layout | A column of the current layout is missing |
| Consolidated rows | The table is empty |
| ISINs present and unique | An ISIN is empty or appears twice |
| Yahoo tickers follow exchange symbols | `yq_ns` or `yq_bo` is not the exchange symbol plus `.NS` or `.BO` |
| Source provenance | No rebuild is recorded, or the recorded row count differs from the table |
| Bhavcopy trade dates agree | The BSE and NSE bhavcopies have different or unknown trade dates |
| `mutual_funds` import, `bse_debt` import, `indices` import, `broker_symbols` import | The table is empty or missing, or no import recorded its source files |

The summary also lists the rebuild's and the imports' source files with their SHA-256 hashes, the row count of every
table, column coverage, Yahoo cache dates, and the tickers that have no Yahoo response.
