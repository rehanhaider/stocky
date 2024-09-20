# PR 01: Modernize Python Tooling With uv, Ruff, and VS Code Defaults

## Summary

Introduce a modern Python project setup centered on `uv`, `pyproject.toml`, Ruff, and committed VS Code workspace settings. This PR makes the project reproducible before changing application behavior.

## Context

The current `requirements.txt` is a 2021-era full environment freeze with many transitive, development, notebook, platform-specific, and likely unused dependencies. The project also has no `pyproject.toml`, no lockfile workflow, and no shared editor/lint defaults.

## Implementation Plan

- Add `pyproject.toml` with project metadata, Python version support, dependencies, and script entry points.
- Use `uv` as the documented runner and dependency manager:
  - `uv sync`
  - `uv run stocky`
  - `uv run ruff check .`
  - `uv run ruff format .`
- Replace the broad `requirements.txt` workflow with explicit runtime and dev dependencies in `pyproject.toml`.
- Add Ruff configuration for linting and formatting.
- Add `.vscode/settings.json` with Ruff-backed format-on-save and linting defaults.
- Update `README.md` installation and run instructions to use `uv`.

## Dependency Direction

Start with the smallest confirmed dependency set:

- Runtime: `pandas`, `yahooquery`, `typer`, `rich`
- CLI dependencies land in PR 02
- Dev: `ruff`, `pytest`

Do not carry forward unused dependencies from the old full freeze unless the code proves they are needed.

## Acceptance Criteria

- A fresh checkout can run `uv sync`.
- `uv run stocky` starts the existing application entry point.
- `uv run ruff check .` runs.
- VS Code picks Ruff as the formatter/linter for Python files.
- `README.md` no longer recommends `pip install --upgrade requirements.txt`.

## Testing

- `uv sync`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run python -m py_compile app.py lib/bin/bhavCopy.py lib/bin/yahoo.py stocky/*.py tests/*.py`

## Out of Scope

- Rewriting the interactive app flow.
- Changing generated data policy.
- Refactoring the bhavcopy merge logic.
