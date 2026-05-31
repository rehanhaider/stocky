# PR 04: Replace Single-Date Hardcoding With Exchange-Aware Source Resolution

## Summary

Remove the current hardcoded May 2021 bhavcopy filenames while preserving the reality that source files are manually downloaded from exchange websites and follow exchange-specific naming conventions.

## Decision

Do not implement programmatic bhavcopy downloading. The README already notes that downloading NSE bhavcopy data programmatically is not acceptable for this project.

Instead, encode exchange-specific filename conventions and provide explicit override paths.

## Implementation Plan

- Add a bhavcopy source resolver with three modes:
  - Explicit paths: `--bse-bhavcopy` and `--nse-bhavcopy`
  - Date-derived paths: `--date YYYY-MM-DD`
  - Auto-discovery: pick the newest valid BSE/NSE pair from the configured input directory
- Keep the source naming conventions in one module, not scattered through processing code.
- Validate required files before reading them.
- Print the exact files selected before processing.
- Keep Zerodha instruments as an explicit path or conventional default.

## Filename Strategy

For date-derived lookup, generate expected filenames from known source conventions:

- BSE: `BSE-EQ_ISINCODE_DDMMYY.CSV`
- NSE before 2024-07-08: `NSE-cmDDMONYYYYbhav.csv`
- NSE from 2024-07-08: `BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip`

For auto-discovery, parse matching filenames in `data/marketData/bhavCopies`, extract dates, and choose the latest compatible pair.

## Example Commands

```bash
uv run stocky rebuild --date 2021-05-03
uv run stocky rebuild --input-dir data/marketData/bhavCopies --latest
uv run stocky rebuild --bse-bhavcopy path/to/BSE.csv --nse-bhavcopy path/to/NSE.csv --zerodha-instruments path/to/instruments.csv
```

## Acceptance Criteria

- No production code hardcodes a single May 2021 bhavcopy date.
- Users can still rely on exchange filename conventions.
- Explicit file overrides work.
- Missing or mismatched source files fail before pandas tries to read them.
- README documents the manual download plus source-resolution workflow.

## Testing

- Unit tests for date-to-filename generation.
- Unit tests for auto-discovery using fixture filenames.
- CLI tests for explicit path and date-derived path modes.

## Out of Scope

- Programmatic exchange downloads.
- Quarterly checked-in data refresh automation. That belongs in `ROADMAP.md`.
