# Stocky Feature Ideas

**Stocky** is a focused instrument master/consolidation tool: ISIN-centric cross-mapping of BSE/NSE/Zerodha/Yahoo symbols for Indian equities, plus storage of raw Yahoo Finance responses.

It has a clean modern CLI (typer + rich), solid pipeline with tests, supports legacy + UDiFF bhavcopies, and already has a basic roadmap (TUI + quarterly refresh workflow).

This document captures additional feature ideas beyond the current implementation and the existing [ROADMAP.md](../ROADMAP.md).

---

## Data Coverage Expansion

- **Mutual funds**: Zerodha already ships `mf_instruments.csv` in the repo (5k+ rows) but it's completely ignored. Add MF support with ISIN + scheme code mappings.
- **BSE debt / other segments**: `data/marketData/bhavCopies/bseDebt/` files exist but are unused. Support non-equity segments (or at least make the pipeline aware of them).
- **Derivatives / indices / ETFs / SGBs**: Current code hard-filters to EQ/Q. Add opt-in support for more instrument types with appropriate handling.
- **Additional brokers/exchanges**: Groww, Upstox, Alice Blue, etc. instrument lists (many publish CSVs similar to Zerodha).
- **Full Zerodha instrument support**: Currently only keeps BSE/NSE segments; the full file has many more (currencies, commodities, etc.).

## Query, Search & Inspection (big usability gap)

- `stocky query` / `stocky lookup` commands: fuzzy search by name, exact symbol in any namespace (zd/nse/bse/yq), reverse lookup (symbol → full row + ISIN).
- `stocky status` / `stocky info`: DB stats, coverage %, last refresh dates per source, unmatched counts, Yahoo freshness.
- Interactive symbol explorer (even before full TUI).
- Show "equivalents" for a given instrument (all four symbol systems + company name).

## Exports & Downstream Integration

- Export commands: `stocky export --format csv|parquet|json` (with column selection, only matched symbols, etc.).
- Generate ready-to-use "universe" files for popular libraries (pandas, vectorbt, backtrader, zipline-reloaded, etc.).
- Stable "master instruments" snapshot export that downstream projects can pin.
- Optional views/tables for common use cases (e.g. only symbols with both zd_symbol and yq_symbol).

## Automation & Freshness

- Explicit `--dry-run` + preview/diff mode for rebuilds (already partially supported; make it richer with row counts and sample diffs).
- Selective Yahoo refresh (by exchange, by list of symbols, or only missing/stale).
- Quarterly refresh checklist + auto-generated summary report (partially on ROADMAP).
- GitHub Action or script that can at least validate + produce the summary (manual bhavcopy download step is unavoidable).
- Timestamped "source provenance" metadata stored in the DB (which exact bhavcopy files + Zerodha version produced each row).

## Data Quality, Validation & Observability

- Better conflict reporting: name mismatches between BSE/NSE, multiple mappings for same symbol, ISIN collisions.
- Validation report as a command output (or artifact on rebuild).
- Coverage tracking over time (e.g. % of consolidated rows with zd_symbol, yq_symbol, etc.).
- Symbol change / corporate action awareness (hard problem, but even basic detection of previous symbols disappearing would help).

## Enrichment & Analytics (leveraging the Yahoo blob store)

- Commands to extract useful fields from stored `yahoo_responses` (market cap, sector/industry, quote, key statistics, earnings dates) without re-hitting the API.
- Simple screening: `stocky screen --market-cap-gt 10000 --exchange NSE` (using cached data).
- OHLCV / price history export from the cached modules (when present).
- "Enrich consolidated" table with selected fundamental fields from Yahoo (materialized columns or views).

## UX / CLI / TUI

- Make the interactive menu much richer (file selection with preview, progress, validation failures) — this is the start of the TUI on ROADMAP.
- JSON output mode for all commands (`--json`) for scripting.
- Progress bars + better error messages during long Yahoo updates.
- Full Textual TUI (already planned): file pickers, live progress panels, table views of results, log tailing.

## Ops, Distribution & Maintenance

- Docker image with the CLI + common workflow.
- Pre-built `stocky.db` releases (or clear guidance on when to check it into the repo vs treat as artifact).
- `stocky diff` between two DBs or two dates (useful for quarterly refresh reviews).
- Improved backup/restore story and DB migration handling if schema evolves.
- Optional read-only HTTP API (FastAPI + sqlite) so other tools can query the master list without copying the DB.

## Lower Priority / More Ambitious

- Historical mapping versions (track symbol changes over quarters).
- Corporate action feed integration or at least a place to annotate them.
- Multi-asset expansion beyond equities/debt/MFs if the project scope grows.
- Browser-based viewer (nice-to-have once TUI exists).

---

## Already on ROADMAP.md

These are captured in the existing roadmap and are worth prioritizing:

- TUI (thin layer over existing commands)
- Quarterly refresh process + generated summary

## Quick Wins (relatively small surface area)

- `stocky status` / info command
- CSV/Parquet export
- MF instrument support (the file is literally sitting there)
- Better dry-run + preview output on rebuild
- Selective Yahoo update

---

**How to use this doc**: Pick one or more areas above when planning work. Cross-reference with [ROADMAP.md](../ROADMAP.md) and open specific items in `ISSUES.md` (using the template) before implementing.
