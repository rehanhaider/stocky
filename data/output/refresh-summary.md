# Stocky refresh summary

Generated 2026-10-21 16:11:25 UTC by Stocky 2.0.0 from `data/output/stocky.db`.

**Validation: passed**

| Check | Result | Detail |
| --- | --- | --- |
| Consolidated table layout | pass | All columns present. |
| Consolidated rows | pass | 4920 rows. |
| ISINs present and unique | pass | 0 empty, 0 duplicated. |
| Yahoo tickers follow exchange symbols | pass | Every ticker is its exchange symbol plus the suffix. |
| Source provenance | pass | Build 3 recorded 4920 rows; the table has 4920. |
| Bhavcopy trade dates agree | pass | BSE bhavcopy 2026-09-30, NSE bhavcopy 2026-09-30. |

## Source files

Build 3 ran at 2026-10-21 14:27:36 UTC with Stocky 2.0.0 and wrote 4920 rows.

| Source | File | Trade date | Usable rows | Size (bytes) | Modified | SHA-256 |
| --- | --- | --- | ---: | ---: | --- | --- |
| BSE bhavcopy | `BhavCopy_BSE_CM_0_0_0_20260930_F_0000.CSV` | 2026-09-30 | 4615 | 868796 | 2026-10-21 14:18:23 UTC | `e2b7cea2672aed05df3ce55f3c531a153cb2b5215e2b493b2735cd9be116acce` |
| NSE bhavcopy | `BhavCopy_NSE_CM_0_0_0_20260930_F_0000.csv.zip` | 2026-09-30 | 2973 | 206815 | 2026-10-21 14:18:23 UTC | `0ece4833d287f504885258a40edc21fd6368632849b87add88d288f1ef0d4a59` |
| Zerodha instruments | `instruments.csv` | - | 23204 | 9487786 | 2026-10-21 14:18:25 UTC | `1306e2d89fed8d09a016ced676759ceabdd0e44c30d983a90a80443d6d7055c4` |

## Database tables

| Table | Rows |
| --- | ---: |
| `broker_symbols` | 51850 |
| `bse_debt` | 903 |
| `consolidated` | 4920 |
| `indices` | 236 |
| `mutual_funds` | 7600 |
| `provenance_builds` | 3 |
| `provenance_sources` | 9 |
| `yahoo_responses` | 8871 |

## Consolidated coverage

| Column | Populated | Missing | Coverage |
| --- | ---: | ---: | ---: |
| `isin` | 4920 | 0 | 100.0% |
| `nse_symbol` | 2973 | 1947 | 60.4% |
| `bse_symbol` | 4615 | 305 | 93.8% |
| `bse_sc_code` | 4615 | 305 | 93.8% |
| `bse_sc_name` | 4615 | 305 | 93.8% |
| `zd_ns` | 2970 | 1950 | 60.4% |
| `zd_bo` | 4603 | 317 | 93.6% |
| `yq_ns` | 2973 | 1947 | 60.4% |
| `yq_bo` | 4615 | 305 | 93.8% |

## Yahoo cache

Newest fetch 2026-10-21 16:11:20 UTC; oldest fetch 2026-05-31 03:58:52 UTC.

## Tickers without a Yahoo response

These tickers were never fetched, or Yahoo returned no listed security for them, so the update skipped them.

| Exchange | Column | Listed | Without response |
| --- | --- | ---: | ---: |
| NSE | `yq_ns` | 2973 | 44 |
| BSE | `yq_bo` | 4615 | 31 |

NSE (first 20 of 44): `NV20.NS`, `SGBAPR28I.NS`, `SGBAUG27.NS`, `SGBAUG28V.NS`, `SGBAUG29V.NS`, `SGBAUG30.NS`, `SGBD29VIII.NS`, `SGBDC27VII.NS`, `SGBDE30III.NS`, `SGBDE31III.NS`, `SGBFEB27.NS`, `SGBFEB28IX.NS`, `SGBFEB29XI.NS`, `SGBFEB32IV.NS`, `SGBJ28VIII.NS`, `SGBJAN27.NS`, `SGBJAN29IX.NS`, `SGBJAN29X.NS`, `SGBJAN30IX.NS`, `SGBJU29III.NS`

BSE (first 20 of 31): `ENERGY.BO`, `QDLDW.BO`, `SGBAPR28I.BO`, `SGBAUG27.BO`, `SGBAUG28V.BO`, `SGBAUG29V.BO`, `SGBAUG30.BO`, `SGBD29VIII.BO`, `SGBDE30III.BO`, `SGBDE31III.BO`, `SGBFEB27.BO`, `SGBFEB28IX.BO`, `SGBFEB29XI.BO`, `SGBFEB32IV.BO`, `SGBJAN29IX.BO`, `SGBJU29III.BO`, `SGBJUL28IV.BO`, `SGBJUL29IV.BO`, `SGBJUN27.BO`, `SGBJUN28.BO`
