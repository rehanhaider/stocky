# Roadmap

## TUI

Build a terminal UI after the CLI has stabilized.

The TUI should be a thin interface over the same commands exposed by `stocky rebuild` and `stocky yahoo update`, not a separate execution path. Candidate libraries are Textual and prompt-toolkit. Textual is the stronger default if we want a full-screen interface with progress panels, tables, logs, and forms.

Initial TUI goals:

- Select BSE/NSE/Zerodha input files.
- Choose date-derived or latest-file source resolution.
- Preview selected source files before running.
- Show rebuild progress and validation failures.
- Show Yahoo update progress and skipped symbols.
- Export or open the generated SQLite database location.

## Quarterly Data Refresh

Raw market data should stay out of Git. Quarterly refreshes should update the SQLite output and commit that output together with a refresh summary.

Proposed workflow:

- Add a documented quarterly checklist.
- Manually download required BSE, NSE, and Zerodha source files into ignored local input folders.
- Run `uv run stocky rebuild --date YYYY-MM-DD` or `uv run stocky rebuild --latest`.
- Run Yahoo update with an explicit dry-run option first.
- Commit the updated `data/output/stocky.db` and refresh summary.
- Include a generated refresh summary showing source files, row counts, skipped symbols, and DB table counts.

Open questions:

- Whether quarterly refreshes should use calendar quarter-end dates or the first available market day after quarter close.
- Whether `stocky.db` should stay in Git long-term or move to release artifacts once it becomes too large.
