# Stocky refresh summary

Generated 2026-11-26 20:06:53 UTC by Stocky 3.0.0 from `data/output/stocky.db`.

Database schema: 1.

**Validation: passed**

| Check | Result | Detail |
| --- | --- | --- |
| Consolidated table layout | pass | Schema 1. |
| Consolidated rows | pass | 4920 rows. |
| ISINs present and unique | pass | 0 empty, 0 duplicated. |
| Yahoo tickers follow exchange symbols | pass | Every ticker is its exchange symbol plus the suffix. |
| Source provenance | pass | Build 4 recorded 4920 rows; the table has 4920. |
| Bhavcopy trade dates agree | pass | BSE bhavcopy 2026-09-30, NSE bhavcopy 2026-09-30. |
| mutual_funds import | pass | 7600 rows from `NAVAll.txt` (modified 2026-11-26), `mf_instruments.csv` (modified 2026-11-26). |
| bse_debt import | pass | 903 rows from `DEBTBHAVCOPY30092026.zip` (modified 2026-11-26). |
| indices import | pass | 254 rows from `instruments.csv` (modified 2026-11-26). |
| broker_symbols import | pass | 52037 rows from `BSE.csv` (modified 2026-11-26), `NSE.csv` (modified 2026-11-26), `instrument.csv` (modified 2026-11-26), `complete.json.gz` (modified 2026-11-26). |

## Source files

Build 4 ran at 2026-11-26 20:06:42 UTC with Stocky 3.0.0 and wrote 4920 rows.

| Source | File | Trade date | Usable rows | Size (bytes) | Modified | SHA-256 |
| --- | --- | --- | ---: | ---: | --- | --- |
| BSE bhavcopy | `BhavCopy_BSE_CM_0_0_0_20260930_F_0000.CSV` | 2026-09-30 | 4615 | 868796 | 2026-11-26 20:04:54 UTC | `e2b7cea2672aed05df3ce55f3c531a153cb2b5215e2b493b2735cd9be116acce` |
| NSE bhavcopy | `BhavCopy_NSE_CM_0_0_0_20260930_F_0000.csv.zip` | 2026-09-30 | 2973 | 206815 | 2026-11-26 20:04:54 UTC | `0ece4833d287f504885258a40edc21fd6368632849b87add88d288f1ef0d4a59` |
| Zerodha instruments | `instruments.csv` | - | 23287 | 10322752 | 2026-11-26 20:04:55 UTC | `bcca221ee8239fba693cfce88e57198ba013e94cdb4c3a1ea75ecdc8802c669c` |

## Imported tables

| Table | Source | File | Rows | Size (bytes) | Modified | Imported | SHA-256 |
| --- | --- | --- | ---: | ---: | --- | --- | --- |
| `broker_symbols` | Alice Blue BSE | `BSE.csv` | 6393 | 493834 | 2026-11-26 20:04:58 UTC | 2026-11-26 20:06:44 UTC by Stocky 3.0.0 | `a7379f01eaa4ea48ad5942dbe39bf3cce16ce09cc6f55b35b35c94bbdadc95da` |
| `broker_symbols` | Alice Blue NSE | `NSE.csv` | 9989 | 819664 | 2026-11-26 20:04:58 UTC | 2026-11-26 20:06:44 UTC by Stocky 3.0.0 | `8ba6f6e64cb1ba2d7dfb7ff7947d3b1640c033e16737c6a25ba19e9d2bf255c1` |
| `broker_symbols` | Groww instruments | `instrument.csv` | 12797 | 21688138 | 2026-11-26 20:04:57 UTC | 2026-11-26 20:06:43 UTC by Stocky 3.0.0 | `560dbd3e6355eb090351987b04d9881b9b7b595f37c92dbe2d0c1a76695c458d` |
| `broker_symbols` | Upstox instruments | `complete.json.gz` | 22858 | 3817079 | 2026-11-26 20:04:57 UTC | 2026-11-26 20:06:44 UTC by Stocky 3.0.0 | `93c1302c5d5b5ec55a8f7072d80f96ab8d4ff697e3b0217604fde08bcade0555` |
| `bse_debt` | BSE debt bhavcopy | `DEBTBHAVCOPY30092026.zip` | 903 | 29250 | 2026-11-26 20:04:54 UTC | 2026-11-26 20:06:42 UTC by Stocky 3.0.0 | `a33bf759753f01087f1f64753ef76262d7a5934a7f530e11ddab46c95e3387cd` |
| `indices` | Zerodha instruments | `instruments.csv` | 254 | 10322752 | 2026-11-26 20:04:55 UTC | 2026-11-26 20:06:42 UTC by Stocky 3.0.0 | `bcca221ee8239fba693cfce88e57198ba013e94cdb4c3a1ea75ecdc8802c669c` |
| `mutual_funds` | AMFI NAV | `NAVAll.txt` | 7401 | 1534065 | 2026-11-26 20:04:56 UTC | 2026-11-26 20:06:42 UTC by Stocky 3.0.0 | `b53ce9f91d962694586c3001277ab8d61b846832aaba60dd1388193f16178498` |
| `mutual_funds` | Zerodha MF instruments | `mf_instruments.csv` | 7600 | 1177751 | 2026-11-26 20:04:55 UTC | 2026-11-26 20:06:42 UTC by Stocky 3.0.0 | `5e99ebd37876a1748b82e16369360dc885e87e0c82d31fd6165d786ed3551662` |

## Database tables

| Table | Rows |
| --- | ---: |
| `broker_symbols` | 52037 |
| `bse_debt` | 903 |
| `consolidated` | 4920 |
| `indices` | 254 |
| `mutual_funds` | 7600 |
| `provenance_builds` | 4 |
| `provenance_imports` | 8 |
| `provenance_sources` | 12 |
| `yahoo_responses` | 7513 |

## Consolidated coverage

| Column | Populated | Missing | Coverage |
| --- | ---: | ---: | ---: |
| `isin` | 4920 | 0 | 100.0% |
| `nse_symbol` | 2973 | 1947 | 60.4% |
| `bse_symbol` | 4615 | 305 | 93.8% |
| `bse_sc_code` | 4615 | 305 | 93.8% |
| `bse_sc_name` | 4615 | 305 | 93.8% |
| `zd_ns` | 2964 | 1956 | 60.2% |
| `zd_bo` | 4583 | 337 | 93.2% |
| `yq_ns` | 2973 | 1947 | 60.4% |
| `yq_bo` | 4615 | 305 | 93.8% |

## Yahoo cache

Newest fetch 2026-11-26 20:06:49 UTC; oldest fetch 2026-05-31 03:58:52 UTC.

## Tickers without a Yahoo response

These tickers were never fetched, or Yahoo returned no listed security for them, so the update skipped them.

| Exchange | Column | Listed | Without response |
| --- | --- | ---: | ---: |
| NSE | `yq_ns` | 2973 | 44 |
| BSE | `yq_bo` | 4615 | 31 |

NSE (first 20 of 44): `NV20.NS`, `SGBAPR28I.NS`, `SGBAUG27.NS`, `SGBAUG28V.NS`, `SGBAUG29V.NS`, `SGBAUG30.NS`, `SGBD29VIII.NS`, `SGBDC27VII.NS`, `SGBDE30III.NS`, `SGBDE31III.NS`, `SGBFEB27.NS`, `SGBFEB28IX.NS`, `SGBFEB29XI.NS`, `SGBFEB32IV.NS`, `SGBJ28VIII.NS`, `SGBJAN27.NS`, `SGBJAN29IX.NS`, `SGBJAN29X.NS`, `SGBJAN30IX.NS`, `SGBJU29III.NS`

BSE (first 20 of 31): `ENERGY.BO`, `QDLDW.BO`, `SGBAPR28I.BO`, `SGBAUG27.BO`, `SGBAUG28V.BO`, `SGBAUG29V.BO`, `SGBAUG30.BO`, `SGBD29VIII.BO`, `SGBDE30III.BO`, `SGBDE31III.BO`, `SGBFEB27.BO`, `SGBFEB28IX.BO`, `SGBFEB29XI.BO`, `SGBFEB32IV.BO`, `SGBJAN29IX.BO`, `SGBJU29III.BO`, `SGBJUL28IV.BO`, `SGBJUL29IV.BO`, `SGBJUN27.BO`, `SGBJUN28.BO`
