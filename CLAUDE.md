# Tracking: notes for AI sessions

A PySide6 desktop library for anime, movies, TV and games across drives, Android devices and
Steam. Start with [docs/README.md](docs/README.md); the pages it lists describe the modules,
the scanner and lookup rules (with their real examples), the data and the workflow.

Before changing anything:

- **Scanner rules** (`tracking/scan/`): record a replay baseline first
  (`.venv\Scripts\python -m tracking.scan.replay --save`), compare after, and read every line
  of the diff. See docs/WORKFLOW.md.
- **Lookup rules** (`tracking/providers/variants.py`, `base.py`): compare `search_variants`
  for every title in a library before and after.
- **Each rule** gets a name, its real example in the docstring, a test, and a line in
  docs/SCANNING.md or docs/MATCHING.md. Thresholds are named constants.
- **Checks**: `ruff check tracking tests`, `pytest -q` (no network).

Owner's standing preferences:

- Commit and push only when asked ("Commit and push to main").
- The owner runs the built exe (`run.bat`); rebuild only via `build.bat`, never while it runs.
- README screenshots use the demo library only (no personal paths).
- Don't use IMDb's internal GraphQL with spoofed headers.
- The real library is at `%LOCALAPPDATA%\Tracking`; read it (or a copy), never write to it
  from tools.
