# Database schema

This page describes the columns of the `consolidated`, `mutual_funds`, `bse_debt`, `indices`, `broker_symbols`, and
`yahoo_fields` tables in `data/output/stocky.db`. The [README](../README.md) covers installation and the commands that
build them.

## The consolidated table

`data/output/stocky.db` holds one `consolidated` row per ISIN. A share keeps the same ISIN on NSE and BSE, but its
symbol can differ, so every symbol column belongs to one exchange. A column stays empty when that exchange does not list
the ISIN in the bhavcopy used for the rebuild.

`ins_type` is `equity` for shares, `etf` for exchange-traded funds whose ISIN starts with `INF`, and `sgb` for
sovereign gold bonds. Every rebuild labels ETFs, so filtering ins_type = 'equity' no longer returns ETFs.
SGBs are included only with `stocky rebuild --include-sgb`. This needs UDiFF bhavcopies and includes NSE series `GB`
and BSE series `G` symbols starting with `SGB`. Other government securities stay excluded.

| Column | NSE | BSE | Source |
| --- | --- | --- | --- |
| `nse_symbol` / `bse_symbol` | yes | yes | `TckrSymb` in each exchange's bhavcopy |
| `bse_sc_code`, `bse_sc_name` | | yes | BSE bhavcopy |
| `zd_ns` / `zd_bo` | yes | yes | Zerodha instruments, matched by the exchange's instrument token, such as `AAREYDRUGS-BE`. When an NSE token has changed, the NSE symbol is looked up in Zerodha's NSE list only |
| `yq_ns` / `yq_bo` | yes | yes | The exchange symbol plus `.NS` or `.BO`, such as `RELIANCE.NS` |

For example, Globe Textiles trades only on NSE, so it has `yq_ns = GLOBE.NS` and an empty `yq_bo`. Yahoo's `GLOBE.BO`
is a different company, and nothing links it to Globe Textiles. Legacy-format bhavcopies (`NSE-cm*bhav.csv`,
`EQ_ISINCODE_*.CSV`) carry no BSE trading symbol, so a rebuild from them leaves `bse_symbol` and `yq_bo` empty.

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
is imported; NCO commodity spot rows are excluded. `stocky status` and `stocky summary` show the instruments file
and its modification time recorded by the last import. Yahoo index tickers are not mapped.

## The broker_symbols table

`broker_symbols` holds cash-market identifiers for Groww, Upstox, and Alice Blue on NSE and BSE. It is separate
from `consolidated`, and `stocky rebuild` keeps it. Each import replaces only the selected broker's rows in one
transaction. A failed broker import keeps its previous rows. Imports of earlier brokers remain committed if a
later broker fails.

| Column | Source |
| --- | --- |
| `broker` | `groww`, `upstox`, or `aliceblue` |
| `exchange` | `NSE` or `BSE`, from the source exchange or Upstox segment |
| `exchange_token` | Groww/Upstox `exchange_token`; Alice Blue `Token` |
| `isin` | Groww/Upstox `isin`; Alice Blue lookup in `consolidated` at import time |
| `symbol` | Groww/Upstox `trading_symbol`; Alice Blue `Trading Symbol` |
| `broker_key` | Groww `groww_symbol`; Upstox `instrument_key`; Alice Blue `Token` |
| `name` | Groww/Upstox `name`; Alice Blue `Instrument Name` |

All seven columns are TEXT. Values are trimmed, and empty cells become SQL NULL. The primary key is
`broker, exchange, exchange_token`; tokens must be present and unique within a broker and exchange. ISIN and
symbol have indexes. Prices, lot sizes, and tick sizes are not stored.

Only these rows are imported:

- Groww: `exchange` is `NSE` or `BSE`, `segment` is `CASH`, and `instrument_type` is `EQ`. CASH `IDX` rows are excluded.
- Upstox: `segment` is `NSE_EQ` or `BSE_EQ`, and `instrument_type` is not `INDEX`, `IDX`, `FUT`, `CE`, `PE`, or `W1`.
  `W1` is a warrant series. All derivative, commodity, currency, and index segments are excluded.
- Alice Blue: `Exch` is `NSE`, `Exchange Segment` is `nse_cm`, and `Instrument Type` is `0`, `2`, or `4`;
  or `Exch` is `BSE`, `Exchange Segment` is `bse_cm`, and `Instrument Type` is `E`. Index segments and
  NSE type `3` warrants are excluded.

Cash rows include shares, funds, and bonds. No futures, options, warrants, or index rows are imported. Indices
belong in `indices`. Alice Blue has no ISIN column. Its BSE Token matches `consolidated.bse_sc_code`; its NSE
Symbol matches `consolidated.nse_symbol` only for Group Name `EQ`, `BE`, `BZ`, or `GB`. For example, an NSE
`RELIANCE-EQ` row uses Symbol `RELIANCE` to obtain `INE002A01018`. An NSE row in series `N0` stays unmatched
even if its Symbol also exists in `consolidated`. Unmatched ISINs are NULL. Run `stocky rebuild` before importing
Alice Blue, and reimport it after rebuilding to refresh its ISIN mappings.

The source files do not provide a whole-file snapshot date. Each import records every file's modification time, which
`stocky status` and `stocky summary` show; that time does not prove when the brokers generated their lists.

## The yahoo_fields table

`yahoo_fields` holds one row per Yahoo ticker whose response named a listed security, with the fields Stocky reads
from that response. `stocky yahoo update` and `stocky yahoo import-cache` write it; `screen`, `yahoo fields`,
`yahoo enriched`, `status`, and `summary` read it. A ticker that Yahoo answers with an error, an empty answer, or an
index has no row, and an update that gets such an answer removes the ticker's earlier row.

| Column | Source |
| --- | --- |
| `yahoo_symbol` | The ticker, the primary key, such as `RELIANCE.NS`. It matches `consolidated.yq_ns` or `yq_bo` |
| `symbol`, `exchange` | The ticker without its suffix, and `NSE` or `BSE` |
| `fetched_at` | When the response was fetched, as an ISO 8601 UTC time. `--stale-days` compares against it |
| `name`, `quote_type` | `quoteType` `longName` (or `shortName`) and `quoteType` |
| `currency`, `market_cap` | `price`, then `summaryDetail` |
| `sector`, `industry` | `assetProfile`, then `summaryProfile` |
| `price`, `volume`, `market_time` | `price` `regularMarketPrice`, `regularMarketVolume`, and `regularMarketTime` |
| `previous_close` | `summaryDetail` `previousClose`, then `price` `regularMarketPreviousClose` |
| `fifty_two_week_high`, `fifty_two_week_low`, `trailing_pe`, `dividend_yield` | `summaryDetail` |
| `trailing_eps`, `book_value`, `price_to_book`, `beta`, `shares_outstanding`, `enterprise_value` | `defaultKeyStatistics` |
| `earnings_date`, `earnings_date_end` | The first and last date in `calendarEvents` `earnings.earningsDate`, as `YYYY-MM-DD` |

The numbers are REAL and the rest TEXT. A field the response lacks is NULL, as is a number Yahoo sent as text, such as
`Infinity`. Market cap is in the quoted currency, not crore.

The raw responses are not in `stocky.db`. They are kept in `yahoo_responses` in the cache file beside the database,
`data/output/stocky_yahoo_cache.db`, which Git ignores: one gzip-compressed `all_modules` JSON answer per ticker, with
its fetch time and source. A run that finishes removes the rows of tickers no longer in `consolidated` from both files.
