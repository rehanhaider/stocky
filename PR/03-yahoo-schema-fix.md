# PR 03: Fix Yahoo Update DB Table Contract

## Summary

Fix the Yahoo update path so it reads from the table that the rebuild process actually writes.

## Current Problem

`lib/bin/bhavCopy.py` writes the merged instrument data to a SQLite table named `consolidated`.

`lib/bin/yahoo.py` currently reads from `equities`:

```sql
SELECT * FROM equities WHERE yq_symbol is not NULL
```

The checked-in `stocky.db` contains `consolidated`, not `equities`, so the Yahoo update flow is not aligned with the database contract.

## Implementation Plan

- Introduce a shared table-name constant, defaulting to `consolidated`.
- Update the Yahoo manager query to read from `consolidated`.
- Make the query select only required columns rather than `SELECT *`.
- Store Yahoo API responses in a `yahoo_responses` table instead of writing one JSON file per symbol.
- Add a one-time import path for the legacy JSON cache.
- Add a clear error if the expected table does not exist.
- Add a small regression test using a temporary SQLite database.

## Acceptance Criteria

- `stocky yahoo update` reads symbols from `consolidated`.
- The code no longer references the stale `equities` table.
- Yahoo responses are persisted in SQLite.
- A missing DB table produces a clear actionable error.
- A regression test fails against the old `equities` query and passes with the fix.

## Testing

- `uv run pytest`
- `uv run stocky yahoo update --dry-run`
- `uv run stocky yahoo import-cache`

## Out of Scope

- Downloading Yahoo responses in tests.
- Changing the generated data retention policy.
- Refactoring all SQLite access.
