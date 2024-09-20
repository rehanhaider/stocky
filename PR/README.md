# Modernization PR Sequence

This folder contains proposed PR descriptions for modernizing Stocky in small, reviewable steps.

The sequence is intentionally ordered so tooling lands first, the known Yahoo DB bug is fixed early, and larger data-pipeline refactors happen after tests exist.

## Proposed order

1. `01-uv-ruff-vscode-foundation.md`
2. `02-typer-rich-cli.md`
3. `03-yahoo-schema-fix.md`
4. `04-bhavcopy-source-resolution.md`
5. `05-pipeline-refactor-validation-tests.md`

## Deferred to roadmap

- A full TUI is deferred until the command-line API stabilizes.
- Quarterly refresh now targets the checked-in SQLite output, with raw market-data inputs kept local and ignored.
