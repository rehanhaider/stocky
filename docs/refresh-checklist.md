# Quarterly refresh checklist

Use this checklist to refresh `data/output/stocky.db`. Stocky treats exchange bhavcopies as manually downloaded local
inputs, so step 1 stays manual.

1. Download the inputs into the ignored local folders:
   - the BSE and NSE bhavcopies for one trade date into `data/marketData/bhavCopies`;
   - the Zerodha instruments file from `https://api.kite.trade/instruments` to `data/marketData/zerodha/instruments.csv`.
2. Preview the rebuild. Nothing is written:

   ```bash
   uv run stocky rebuild --date YYYY-MM-DD --dry-run
   ```

   Check the source row counts, the added, removed, and changed rows, and the sample differences. A large number of
   removed rows usually means a wrong or partial bhavcopy.
3. Rebuild. This backs up the database, replaces the `consolidated` table, and records the source files:

   ```bash
   uv run stocky rebuild --date YYYY-MM-DD
   ```

4. Preview the Yahoo update, then run it for each exchange. `--stale-days` fetches only tickers with no response or
   an old one; add `--symbols` to refresh named tickers only:

   ```bash
   uv run stocky yahoo update --exchange NSE --stale-days 90 --dry-run
   uv run stocky yahoo update --exchange NSE --stale-days 90
   uv run stocky yahoo update --exchange BSE --stale-days 90
   ```

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

The summary also lists the source files with their SHA-256 hashes, the row count of every table, column coverage,
Yahoo cache dates, and the tickers that have no Yahoo response.
