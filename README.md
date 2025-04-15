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
2. NSE Bhavcopy: Download from `https://www1.nseindia.com/products/content/equities/equities/archieve_eq.htm`. \
Note: Downloading BHAV Copy using programmatic methods is illegal.
3. Zerodha Instruments: Download from `https://api.kite.trade/instruments`

### Then place these files in the following locations
1. BSE Bhavcopy: `data/marketData/bhavCopies`
2. NSE Bhavcopy: `data/marketData/bhavCopies`
3. Zerodha Instruments: `data/marketData/zerodha`. The filename should be `instruments.csv`

Raw market data files are local inputs and are ignored by Git. The durable output is `data/output/stocky.db`.

## Running the app

Run the interactive menu:

```bash
uv run stocky
```

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

For compatibility, `python app.py` still launches the CLI after dependencies are installed.

## UI Options
There are four options.

**1. Rebuild stocky.db from scratch:**
This backs up the existing database and replaces the `consolidated` table. Requires bhavcopies and Zerodha instruments in their respective locations.

**2. Update all Yahoo data:**
Downloads Yahoo data using yahooquery and stores responses in `data/output/stocky.db`

**3. Import Yahoo JSON cache:**
Imports legacy files from `data/marketData/yahoo/apiResponse` into the `yahoo_responses` SQLite table.

**4. Exit:**
Exit the program


## License
[![FOSSA Status](https://app.fossa.com/api/projects/git%2Bgithub.com%2Fjustgoodin%2Fstocky.svg?type=large)](https://app.fossa.com/projects/git%2Bgithub.com%2Fjustgoodin%2Fstocky?ref=badge_large)
