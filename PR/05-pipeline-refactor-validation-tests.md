# PR 05: Refactor Pipeline Logic and Add Regression Tests

## Summary

Split the bhavcopy pipeline into testable units, add validation around input data, and cover the core merge behavior with small fixtures.

## Context

The current rebuild flow mixes file paths, pandas loading, data cleanup, merge logic, Yahoo symbol matching, SQLite backup, SQLite writes, and terminal output in one function. That makes it difficult to test safely before changing behavior.

## Implementation Plan

- Create a package layout for application logic, for example:
  - `stocky/config.py`
  - `stocky/sources.py`
  - `stocky/pipeline.py`
  - `stocky/database.py`
  - `stocky/yahoo.py`
  - `stocky/cli.py`
- Split the current rebuild flow into focused functions:
  - load BSE bhavcopy
  - load NSE bhavcopy
  - load Zerodha instruments
  - normalize source columns
  - merge BSE and NSE instruments
  - match Zerodha symbols
  - match available Yahoo symbols
  - write SQLite output
- Add validation for:
  - file existence
  - required columns
  - output directory existence
  - expected SQLite table names
  - duplicate or missing ISIN values where they affect output
- Add pytest fixtures with tiny BSE, NSE, Zerodha, and Yahoo response filename samples.
- Replace broad `except Exception` blocks with targeted exceptions and actionable messages.
- Remove dead placeholder code and debugging output.

## Acceptance Criteria

- Core merge logic is covered by unit tests.
- Rebuild validation fails before destructive DB writes.
- SQLite backup directory handling is reliable.
- Dead code such as unused placeholder screen functions is removed or justified.
- Raw market-data inputs are ignored by Git, while the SQLite output remains the durable artifact.

## Testing

- `uv run pytest`
- `uv run ruff check .`
- `uv run stocky rebuild --dry-run --date 2021-05-03`

## Out of Scope

- Building a TUI.
- Adding automated exchange downloads.
