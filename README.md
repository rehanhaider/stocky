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

### Then place these files in the following locations
1. BSE Bhavcopy: `data/marketData/bhavCopies`
2. NSE Bhavcopy: `data/marketData/bhavCopies`. Current NSE UDiFF `.csv.zip` files can be used directly.
3. Zerodha Instruments: `data/marketData/zerodha`. The filename should be `instruments.csv`

Raw market data files are local inputs and are ignored by Git. The durable output is `data/output/stocky.db`.

## Running the app

Run the interactive menu:

```bash
uv run stocky
```

The rebuild option lists the BSE/NSE bhavcopy pairs it found in `data/marketData/bhavCopies`, lets you pick one by row
number or trade date, and previews the resolved files and their row counts before you confirm the rebuild. The Yahoo
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

Show database statistics, per-column coverage, and Yahoo cache freshness:

```bash
uv run stocky status
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

Search and inspect equivalent identifiers in a standalone explorer:

```bash
uv run stocky explore
```

### JSON output

```bash
uv run stocky status --json
```

The `status`, `query`, `lookup`, `explore`, `rebuild`, `yahoo update`, and `yahoo import-cache` commands accept `--json` and print JSON on stdout. With `--json`, `yahoo update` also writes its progress and error events to stderr as JSON lines, one per line.

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
resolved files with their row counts, and on confirmation backs up the existing database and replaces the `consolidated`
table. Requires bhavcopies and Zerodha instruments in their respective locations.

**2. Update Yahoo data:**
Asks for the exchange, optional limit, and whether to fetch only uncached tickers, reports how many tickers it will
fetch, then downloads Yahoo data using yahooquery and stores responses in `data/output/stocky.db`

**3. Import Yahoo JSON cache:**
Imports legacy files from `data/marketData/yahoo/apiResponse` into the `yahoo_responses` SQLite table.

**4. Show database status:**
Prints row counts, per-column coverage, and Yahoo cache freshness for `data/output/stocky.db`.

**5. Look up an instrument:**
Searches the `consolidated` table across every symbol column (NSE, BSE, Zerodha, Yahoo, ISIN, name).

**6. Exit:**
Exit the program


## License

[Read the license](LICENSE.md).
