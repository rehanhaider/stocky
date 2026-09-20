[![FOSSA Status](https://app.fossa.com/api/projects/git%2Bgithub.com%2Fjustgoodin%2Fstocky.svg?type=shield)](https://app.fossa.com/projects/git%2Bgithub.com%2Fjustgoodin%2Fstocky?ref=badge_shield)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-46a4ff.svg)](https://docs.astral.sh/ruff/)
[![license](https://img.shields.io/github/license/justgoodin/stocky)](https://choosealicense.com/licenses/gpl-3.0/)

# Stocky McStockface
**Stocky Will help you generate a consolidated list of Instruments**
1. by mapping their ISIN codes to
    1. BSE Stock codes and symbols
    2. NSE symbols
    3. Zerodha symbols
    4. Yahoo symbols (Both .NS and .BO)

# Installation
Clone the repository.

```bash
git clone https://github.com/justgoodin/stocky.git
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
option asks for the exchange, the key column, an optional limit, and whether to fetch only uncached symbols, then shows
how many symbols it will fetch and a progress bar while it runs. Both options run exactly what `stocky rebuild` and
`stocky yahoo update` run.

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

Update Yahoo responses in SQLite:

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

Export a Zerodha plus Yahoo universe to JSON, keeping only rows where both symbols are populated:

```bash
uv run stocky export -o data/output/universe.json \
  --columns isin,zd_symbol,yq_symbol \
  --require zd_symbol,yq_symbol
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

For compatibility, `python app.py` still launches the CLI after dependencies are installed.

## UI Options
There are six options.

**1. Rebuild stocky.db from scratch:**
Lists the BSE/NSE bhavcopy pairs found in `data/marketData/bhavCopies`, takes a row number or a trade date, previews the
resolved files with their row counts, and on confirmation backs up the existing database and replaces the `consolidated`
table. Requires bhavcopies and Zerodha instruments in their respective locations.

**2. Update Yahoo data:**
Asks for the exchange, key column, optional limit, and whether to fetch only uncached symbols, reports how many symbols
it will fetch, then downloads Yahoo data using yahooquery and stores responses in `data/output/stocky.db`

**3. Import Yahoo JSON cache:**
Imports legacy files from `data/marketData/yahoo/apiResponse` into the `yahoo_responses` SQLite table.

**4. Show database status:**
Prints row counts, per-column coverage, and Yahoo cache freshness for `data/output/stocky.db`.

**5. Look up an instrument:**
Searches the `consolidated` table across all symbol namespaces (Zerodha, Yahoo, NSE, BSE, ISIN, name).

**6. Exit:**
Exit the program


## License
[![FOSSA Status](https://app.fossa.com/api/projects/git%2Bgithub.com%2Fjustgoodin%2Fstocky.svg?type=large)](https://app.fossa.com/projects/git%2Bgithub.com%2Fjustgoodin%2Fstocky?ref=badge_large)
