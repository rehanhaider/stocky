[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-46a4ff.svg)](https://docs.astral.sh/ruff/)
[![license](https://img.shields.io/github/license/rehanhaider/stocky)](https://choosealicense.com/licenses/gpl-3.0/)

# Stocky McStockface
**Stocky Will help you generate a consolidated list of Instruments**
1. by mapping their ISIN codes to
    1. BSE Stock codes and symbols
    2. NSE symbols
    3. Zerodha symbols
    4. Yahoo symbols (Both .NS and .BO)

## The consolidated table

`data/output/stocky.db` holds one `consolidated` row per ISIN. A share keeps the same ISIN on NSE and BSE, but its
symbol can differ, so every symbol column belongs to one exchange. A column stays empty when that exchange does not list
the ISIN in the bhavcopy used for the rebuild.

| Column | NSE | BSE | Source |
| --- | --- | --- | --- |
| `nse_symbol` / `bse_symbol` | yes | yes | `TckrSymb` in each exchange's bhavcopy |
| `bse_sc_code`, `bse_sc_name` | | yes | BSE bhavcopy |
| `zd_ns` / `zd_bo` | yes | yes | Zerodha instruments, matched by the exchange's instrument token, such as `AAREYDRUGS-BE`. When an NSE token has changed, the NSE symbol is looked up in Zerodha's NSE list only |
| `yq_ns` / `yq_bo` | yes | yes | The exchange symbol plus `.NS` or `.BO`, such as `RELIANCE.NS` |

For example, Globe Textiles trades only on NSE, so it has `yq_ns = GLOBE.NS` and an empty `yq_bo`. Yahoo's `GLOBE.BO`
is a different company, and nothing links it to Globe Textiles. Legacy-format bhavcopies (`NSE-cm*bhav.csv`,
`EQ_ISINCODE_*.CSV`) carry no BSE trading symbol, so a rebuild from them leaves `bse_symbol` and `yq_bo` empty.

### Release data

Release `v2.0.0` replaces the shared Zerodha and Yahoo symbol columns with exchange-specific columns. See the
[release notes](docs/releases/v2.0.0.md) for the column changes, source trade date, and database row counts.
`data/output/stocky.db` is versioned with the repository; pin the release tag to use that database version.

## The mutual_funds table

`mutual_funds` holds one row per mutual fund scheme option from Zerodha's mutual fund instruments file. It is separate
from `consolidated`, and `stocky rebuild` does not change it.

| Column | Source |
| --- | --- |
| `isin` | The scheme option's ISIN, such as `INF179K01KG8`. Each plan (regular or direct) and option (growth, payout, reinvest) has its own ISIN |
| `zd_mf` | Zerodha's `tradingsymbol`, the identifier Kite's mutual fund orders take. Zerodha uses the ISIN here |
| `amfi_code` | AMFI scheme code from `NAVAll.txt`, stored as TEXT. NULL when the ISIN is unmatched or has more than one scheme code |
| `name`, `amc` | Scheme name and Zerodha's AMC label, such as `HDFCMutualFund_MF` |
| `scheme_type`, `plan`, `dividend_type` | Category (`Debt`, `Equity`, ...), `regular` or `direct`, and `growth`, `payout`, or `reinvest` |

Zerodha's file has no AMFI scheme code. The import joins AMFI's NAV file by ISIN, using both ISIN fields and keeping
only valid ISINs. Codes shared by multiple ISINs are allowed; an ISIN with multiple codes gets NULL.

## The bse_debt table

`bse_debt` holds identifier mappings from the three CSV members in a BSE debt bhavcopy archive. It is separate
from `consolidated`, and `stocky rebuild` does not change it. The same ISIN can appear in more than one family.

| Column | Source |
| --- | --- |
| `family` | `fgroup`, `icdm`, or `wdm`, from the member filename |
| `trade_date` | The date in the member filename, stored as `YYYY-MM-DD` |
| `scrip_code` | `fgroup` `Security_cd` or `wdm` `Scrip Code`; empty for `icdm` |
| `symbol` | `fgroup` `sc_name` or `wdm` `Security Code`; empty for `icdm` |
| `isin` | `fgroup` or `icdm` `ISIN No.`; empty for `wdm` |
| `name` | `icdm` `Issuer Name` or `wdm` `Security Description`; empty for `fgroup` |
| `coupon_pct` | `fgroup` `COUP0N (%)`, `icdm` `Coupon (%)`, or `wdm` `Coupon Rate (%)` |
| `maturity_date` | `Maturity Date` or `wdm` `MaturityDate`, stored as `YYYY-MM-DD` |
| `face_value` | `fgroup` `FACE VALUE` or `icdm` `Face Value`; empty for `wdm` |

Numbers keep the source's trimmed text, and empty cells are SQL NULL. Each family's identifier must be unique:
`scrip_code` for `fgroup` and `wdm`, and `isin` for `icdm`. Prices, volumes, and turnover are not stored.

## The indices table

`indices` holds market index identifiers from rows whose `segment` is `INDICES` in Zerodha's instruments file.
It is separate from `consolidated`, and `stocky rebuild` does not change it.

| Column | Source |
| --- | --- |
| `exchange` | Zerodha's `exchange`, such as `NSE`, `BSE`, `GLOBAL`, `MCX`, or `NSEIX` |
| `zd_symbol` | Zerodha's `tradingsymbol`, such as `NIFTY 50` |
| `name` | Zerodha's index `name` |
| `zd_instrument_token` | Zerodha's `instrument_token`, the primary key, such as `256265` |
| `exchange_token` | Zerodha's `exchange_token`, such as `1001` |

All columns are TEXT, values keep the source's trimmed text, and empty cells are SQL NULL. Instrument tokens
must be present and unique. The symbol has an index. Price fields are not stored. Only the `INDICES` segment
is imported; NCO commodity spot rows are excluded. The local `instruments.csv` snapshot is dated 2026-07-19.
Yahoo index tickers are not mapped.

# Installation
Clone the repository.

```bash
git clone https://github.com/rehanhaider/stocky.git
cd stocky
```

Install dependencies with `uv`.

```bash
uv sync
```

# Instructions

## Inputs needed
### You need to download 3 files
1. BSE Bhavcopy: Download from `https://www.bseindia.com/markets/MarketInfo/BhavCopy.aspx`.
2. NSE Bhavcopy:
    - Current files: Download `CM-UDiFF Common Bhavcopy Final (zip)` from `https://www.nseindia.com/all-reports`, or open the date-specific archive URL:
      `https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip`
    - Example direct file:
      `https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20260522_F_0000.csv.zip`
    - Legacy files before 2024-07-08 use the older `NSE-cmDDMONYYYYbhav.csv` filename convention.
    - This project treats exchange bhavcopies as manually downloaded local inputs; do not add automated NSE downloads here.
3. Zerodha Instruments: Download from `https://api.kite.trade/instruments`
4. Optional, for mutual funds: Zerodha mutual fund instruments from `https://api.kite.trade/mf/instruments`
   and AMFI scheme codes from `https://www.amfiindia.com/spages/NAVAll.txt`. Both files are required for `mf import`.
5. Optional, for BSE debt: download `https://www.bseindia.com/download/BhavCopy/Debt/DEBTBHAVCOPY<DDMMYYYY>.zip`,
   replacing `<DDMMYYYY>` with the trade date, such as `16102026`.

### Then place these files in the following locations
1. BSE Bhavcopy: `data/marketData/bhavCopies`
2. NSE Bhavcopy: `data/marketData/bhavCopies`. Current NSE UDiFF `.csv.zip` files can be used directly.
3. Zerodha Instruments: `data/marketData/zerodha`. The filename should be `instruments.csv`
4. Zerodha mutual fund instruments: `data/marketData/zerodha`. The filename should be `mf_instruments.csv`
   AMFI scheme codes: `data/marketData/amfi/NAVAll.txt`.
5. BSE debt bhavcopy archives: `data/marketData/bhavCopies/bseDebt`. Keep the `DEBTBHAVCOPY<DDMMYYYY>.zip` filename;
   the loader reads `fgroup<DDMMYYYY>.csv`, `icdm<DDMMYYYY>.csv`, and `wdm<DDMMYYYY>.csv` directly from the zip.

Raw market data files are local inputs and are ignored by Git. The durable output is `data/output/stocky.db`.

## Running the app

Run the interactive menu:

```bash
uv run stocky
```

The rebuild option lists the BSE/NSE bhavcopy pairs it found in `data/marketData/bhavCopies`, lets you pick one by row
number or trade date, and previews the resolved files, their row counts, and the row changes before you confirm the
rebuild. The Yahoo
option asks for the exchange, an optional limit, and whether to fetch only uncached tickers, then shows how many tickers
it will fetch and a progress bar while it runs. Both options run exactly what `stocky rebuild` and
`stocky yahoo update` run.

Open the full-screen terminal UI:

```bash
uv sync --extra tui
uv run stocky tui
```

The TUI needs the optional Textual extra; install it with `uv sync --extra tui` or `pip install "stocky[tui]"`. It has four tabs
over the same code as the commands:

- **Rebuild** resolves the bhavcopies from the latest pair, a discovered trade date, or files you pick in the file tree,
  previews each file's row count, and shows each stage and any validation failure while `stocky rebuild` runs.
- **Yahoo update** takes the exchange, limit, and missing-only choices, counts the tickers to fetch, and shows a live
  progress bar while `stocky yahoo update` runs.
- **Status** shows the `stocky status` tables, opens the database folder, and exports the consolidated table to CSV, JSON,
  or Parquet.
- **Search** lists matches for a symbol, ISIN, BSE scrip code, or name; select a row to see its equivalents.

A log panel under the tabs records every run's progress and errors. `stocky tui` accepts `--db-path`, `--input-dir`, and
`--zerodha-instruments`. Press `q` to quit; a running rebuild or Yahoo update stops at its next step first.

Rebuild the SQLite database using the latest matching BSE/NSE bhavcopy pair:

```bash
uv run stocky rebuild --latest
```

Preview a rebuild without writing anything. The dry run compares the rebuilt table with the current one by ISIN and
shows the source row counts, the added, removed, changed, and unchanged rows, per-column change counts, and up to
`--sample` example rows of each kind (default 5):

```bash
uv run stocky rebuild --latest --dry-run
```

Every rebuild records its source files in the database: the BSE and NSE bhavcopy names with their trade dates and the
Zerodha instruments file, each with its SHA-256 hash, size, modification time, and usable row count, plus the build
time and Stocky version. The `provenance_builds` and `provenance_sources` tables keep every build; `stocky status`
shows the latest one. Only file names are stored, not local paths.

Rebuild for a specific source date:

```bash
uv run stocky rebuild --date 2021-05-03
```

Use explicit source files:

```bash
uv run stocky rebuild \
  --bse-bhavcopy data/marketData/bhavCopies/BSE-EQ_ISINCODE_030521.CSV \
  --nse-bhavcopy data/marketData/bhavCopies/NSE-cm03MAY2021bhav.csv \
  --zerodha-instruments data/marketData/zerodha/instruments.csv
```

Import the legacy Yahoo JSON cache into SQLite:

```bash
uv run stocky yahoo import-cache
```

Update Yahoo responses in SQLite. `--exchange NSE` fetches the `yq_ns` tickers and `--exchange BSE` fetches the `yq_bo`
tickers, so a run only asks Yahoo for tickers that exchange lists. Only answers that name a listed security are saved.
An error, an empty answer, or an index that Yahoo files under the same ticker (such as `ENERGY.BO`, the S&P BSE Energy
index) is skipped, and any older saved answer for that ticker is removed. `stocky yahoo import-cache` applies the same
rule. A run without `--missing-only` refreshes every saved answer:

```bash
uv run stocky yahoo update --exchange BSE
```

Refresh part of the cache instead of every ticker. `--symbols` takes tickers or exchange symbols listed for the
exchange, `--stale-days N` keeps tickers with no response or one fetched more than N days ago, and `--missing-only`
keeps tickers with no response. The options combine, and `--limit` caps the selection after them:

```bash
uv run stocky yahoo update --exchange NSE --symbols RELIANCE,INFY.NS
uv run stocky yahoo update --exchange BSE --stale-days 90 --limit 500
```

Show database statistics, per-column coverage, Yahoo cache freshness, and the source files of the latest rebuild:

```bash
uv run stocky status
```

Validate the database and write the refresh summary. It lists the source files, every table's row count, column
coverage, Yahoo cache dates, and the tickers without a Yahoo response, and exits with status 1 when a check fails. See
the [quarterly refresh checklist](docs/refresh-checklist.md) for the full refresh steps:

```bash
uv run stocky summary --output data/output/refresh-summary.md
```

Look up an instrument by symbol, ISIN, BSE scrip code, or name fragment. When nothing matches directly, `query` shows the closest names:

```bash
uv run stocky query RELIANCE
uv run stocky query "hdfc bank"
uv run stocky query 500325 --exact
```

Show every known identifier for one exact match:

```bash
uv run stocky lookup 500325
```

Load the Zerodha mutual fund file and AMFI scheme codes into the `mutual_funds` table. Download
`https://www.amfiindia.com/spages/NAVAll.txt` to `data/marketData/amfi/NAVAll.txt` first. Each run requires both files,
replaces that table, and leaves the others as they are. `--mf-instruments` and `--amfi` read different source files.
The output reports total rows, rows with an AMFI code, and ambiguous ISINs in Zerodha's list, listing up to 10:

```bash
uv run stocky mf import
uv run stocky mf import --amfi data/marketData/amfi/NAVAll.txt
```

Look up a mutual fund by ISIN, Zerodha identifier, AMFI code, scheme name, or AMC fragment. AMFI codes match exactly.
`--exact` matches the ISIN, Zerodha identifier, AMFI code, or full scheme name only:

```bash
uv run stocky mf query "parag parikh"
uv run stocky mf query INF179K01KG8 --exact
uv run stocky mf query 119091 --exact
```

Load the newest BSE debt archive by the date in its filename into the `bse_debt` table. Each run replaces that table
in one transaction and leaves the others as they are; `--file` reads a different archive and `--db` selects a database.
The 2021 `BSE-DE-*.csv` files are not used:

```bash
uv run stocky debt import
uv run stocky debt import --file data/marketData/bhavCopies/bseDebt/DEBTBHAVCOPY16102026.zip --db data/output/stocky.db
```

Look up BSE debt by ISIN, scrip code, symbol, or name fragment. `--exact` matches the ISIN, scrip code, or symbol only.
`--limit` sets the maximum number of results:

```bash
uv run stocky debt query "GOI" --limit 10
uv run stocky debt query INE0K0Y08060 --exact
```

Load market indices from `data/marketData/zerodha/instruments.csv` into the `indices` table. Each run replaces
that table in one transaction and leaves the others as they are. A failed import keeps the previous table.
`--file` reads a different instruments file and `--db` selects a database:

```bash
uv run stocky indices import
uv run stocky indices import --file data/marketData/zerodha/instruments.csv --db data/output/stocky.db
```

Look up an index by exact symbol, instrument token, or exchange token, or by symbol or name fragment.
`--exact` matches only the three identifiers. `--exchange` filters by exchange without regard to case;
`--limit` sets the maximum number of results:

```bash
uv run stocky indices query "NIFTY" --exchange nse --limit 10
uv run stocky indices query 256265 --exact
uv run stocky indices query "NIFTY BANK" --json
```

Search and inspect equivalent identifiers in a standalone explorer:

```bash
uv run stocky explore
```

### JSON output

```bash
uv run stocky status --json
```

The `status`, `summary`, `query`, `lookup`, `explore`, `rebuild`, `screen`, `yahoo update`, `yahoo import-cache`, `yahoo fields`, `mf import`, `mf query`, `debt import`, `debt query`, `indices import`, and `indices query` commands accept `--json` and print JSON on stdout. With `--json`, `yahoo update` also writes its progress and error events to stderr as JSON lines, one per line.

### Exporting the consolidated table

Export every row and column to CSV:

```bash
uv run stocky export -o data/output/consolidated.csv
```

Export an NSE universe with Zerodha and Yahoo tickers to JSON, keeping only rows where both are populated:

```bash
uv run stocky export -o data/output/universe.json \
  --columns isin,zd_ns,yq_ns \
  --require zd_ns,yq_ns
```

Export every row and column to Parquet:

```bash
uv run stocky export -o data/output/consolidated.parquet
```

Parquet needs the optional extra; install it with `uv sync --extra parquet` or `pip install "stocky[parquet]"`.

Stream CSV to stdout so it can be piped into another command:

```bash
uv run stocky export --format csv
```

### Ticker lists

`--tickers COLUMN` writes one ticker per line from `nse_symbol`, `bse_symbol`, `bse_sc_code`, `zd_ns`, `zd_bo`, `yq_ns`, or `yq_bo`, skipping rows where that column is empty and dropping repeats. `--suffix` appends text to every ticker, and `--require` still filters rows. Most backtesting setups can read the result directly: pandas, vectorbt, backtrader, and zipline-reloaded loaders take a plain list of tickers. Stocky has no price data, so building data feeds or bundles is left to those tools.

Yahoo tickers for NSE-listed instruments, ready for `yfinance` or vectorbt's `YFData`:

```bash
uv run stocky export --tickers yq_ns -o data/output/yahoo_nse.txt
```

### Yahoo fields and screening

These commands read the Yahoo responses already cached in `stocky.db`. They never call Yahoo Finance and never write to the database.

Each exchange's data comes only from the response cached under that exchange's ticker column: `yq_ns` for NSE and `yq_bo` for BSE. A bare symbol is never used to match, because the same symbol can name different companies on NSE and BSE.

`yahoo fields` shows one instrument's fields, with one table per exchange that lists it. It takes the same exact identifiers as `lookup`:

```bash
uv run stocky yahoo fields RELIANCE
uv run stocky yahoo fields INE002A01018 --json
```

The fields are:

- name and quote type;
- currency, market cap, and sector and industry;
- price, previous close, volume, quote time, and 52-week high and low;
- trailing P/E, trailing EPS, book value, price to book, dividend yield, beta, shares outstanding, and enterprise value;
- the first and last expected earnings date.

Some responses do not have every module, so a field can be empty. More than 40% of the cached responses have no sector, industry, or key statistics.

`yahoo enriched` writes the enriched consolidated view as CSV, JSON, or Parquet. The view has one row for each instrument on each exchange where it has a Yahoo ticker. It shows the ticker and its identifiers, then `yahoo_status`, `fetched_at`, and the fields above. `yahoo_status` is one of three values:

- `ok`: the response names a listed security;
- `unusable`: the response is an error or names something else, such as an index;
- `missing`: no response is cached.

```bash
uv run stocky yahoo enriched -o data/output/enriched.parquet
uv run stocky yahoo enriched --exchange NSE --format csv
```

`screen` filters the listings that have usable data. It shows the largest market cap first.

- `--market-cap-gt` and `--market-cap-lt` are in crore. Both bounds are exclusive.
- `--sector` and `--industry` match any part of the value, in any case.
- `--exchange` limits the result to NSE or BSE.

```bash
uv run stocky screen --market-cap-gt 10000 --exchange NSE
uv run stocky screen --sector technology --market-cap-lt 500 --limit 50 --json
```

The values are what Yahoo returned when the response was fetched. Check the quote date: many responses hold quotes from years before `fetched_at`. A few hold impossible placeholder values that sort to the top of a market cap screen. The cached modules have no price history, so Stocky has no OHLCV export.

### Pinnable snapshots

`stocky snapshot` writes every consolidated row to `data/output/snapshots/consolidated-<version>/` as CSV and Parquet, along with a `manifest.json` that records the version, row count, columns, Stocky version, and each file's size and SHA-256. The version defaults to today's UTC date. An existing snapshot is never overwritten, so a downstream project can pin a version and check its files against the manifest. Parquet needs the `parquet` extra; without it, pass `--formats csv`.

```bash
uv run stocky snapshot --version 2026-q3
uv run stocky snapshot --version 2026-q3-csv --formats csv
```

For compatibility, `python app.py` still launches the CLI after dependencies are installed.

## UI Options
There are six options.

**1. Rebuild stocky.db from scratch:**
Lists the BSE/NSE bhavcopy pairs found in `data/marketData/bhavCopies`, takes a row number or a trade date, previews the
resolved files with their row counts and how the rebuilt table differs from the current one, and on confirmation backs
up the existing database and replaces the `consolidated` table. Requires bhavcopies and Zerodha instruments in their respective locations.

**2. Update Yahoo data:**
Asks for the exchange, optional limit, and whether to fetch only uncached tickers, reports how many tickers it will
fetch, then downloads Yahoo data using yahooquery and stores responses in `data/output/stocky.db`

**3. Import Yahoo JSON cache:**
Imports legacy files from `data/marketData/yahoo/apiResponse` into the `yahoo_responses` SQLite table.

**4. Show database status:**
Prints row counts, per-column coverage, Yahoo cache freshness, and the source files of the latest rebuild for
`data/output/stocky.db`.

**5. Look up an instrument:**
Searches the `consolidated` table across every symbol column (NSE, BSE, Zerodha, Yahoo, ISIN, name).

**6. Exit:**
Exit the program


## License

[Read the license](LICENSE.md).
