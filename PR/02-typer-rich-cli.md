# PR 02: Add a Modern CLI With Typer and Rich

## Summary

Replace the interactive-only launcher with a scriptable CLI while preserving an interactive command for users who prefer menus. Use Typer for command parsing and Rich for readable terminal output.

## Why Typer and Rich

Three reasonable paths are available:

- `argparse` plus Rich: minimal dependencies, but more boilerplate as commands grow.
- Click plus Rich: mature, but less type-driven.
- Typer plus Rich: clean command structure, type hints, good help output, and a natural path to future command expansion.

Typer plus Rich is the best fit because this project needs clear subcommands, typed options, and user-friendly output without building a full TUI yet.

## Implementation Plan

- Add a package entry point, for example `stocky`.
- Add subcommands:
  - `stocky interactive`
  - `stocky rebuild`
  - `stocky yahoo update`
  - `stocky yahoo import-cache`
  - `stocky version`
- Keep the current menu available under `stocky interactive` during the transition.
- Use Rich for status messages, warnings, errors, progress, and summary tables.
- Return non-zero exit codes for invalid input, missing files, and failed processing.
- Update `README.md` with CLI examples.

## Example Commands

```bash
uv run stocky interactive
uv run stocky rebuild --date 2021-05-03
uv run stocky rebuild --bse-bhavcopy data/marketData/bhavCopies/BSE-EQ_ISINCODE_030521.CSV --nse-bhavcopy data/marketData/bhavCopies/NSE-cm03MAY2021bhav.csv
uv run stocky yahoo update --exchange BSE
```

## Acceptance Criteria

- The project can be run with `uv run stocky`.
- The existing interactive behavior remains available.
- Rebuild and Yahoo update can be invoked non-interactively.
- Legacy Yahoo JSON cache files can be imported into SQLite.
- CLI validation errors are clear and produce non-zero exit codes.
- Rich output replaces ad hoc colored print calls in the CLI path.

## Testing

- `uv run stocky --help`
- `uv run stocky interactive`
- `uv run stocky rebuild --help`
- `uv run stocky yahoo update --help`
- `uv run stocky yahoo import-cache --help`
- CLI smoke tests using pytest and Typer's test runner.

## Out of Scope

- A full-screen TUI. That belongs in `ROADMAP.md` after the CLI API settles.
- Changing how bhavcopy input files are resolved. That is handled in PR 04.
