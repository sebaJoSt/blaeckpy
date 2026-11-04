# blaeckpy

Python library for signals, Home Assistant entities and commands over serial or TCP, using the
blaeck protocol. Version 1.0.0 is the first release (November 4, 2026). The user tags releases
and uploads them to PyPI; never tag or publish.

- No builds, uploads, commits or pushes without user authorization.
- Describe blaeckpy on its own terms. Docs, docstrings and examples never compare it with the
  Arduino library ("as in blaeck", "like blaeck").
- Python 3.12 or newer, no runtime dependencies. The public API is exported from
  `src/blaeckpy/__init__.py`; modules starting with `_` are private.
- The package is typed (`py.typed`). `python -m mypy` must stay clean over `src`, and
  `tests/test_typing.py` checks `tests/typing_usage.py` and the examples with mypy and basedpyright.
- Run `python -m pytest -q` before proposing a commit. CI runs pytest and mypy on Python
  3.12, 3.13 and 3.14.
- Git stores LF; there is no `.gitattributes`, so a checkout with `core.autocrlf` is CRLF.
  Preserve each file's existing line endings and BOM when editing.
- The golden scenarios and frames in `tests/golden` belong to blaeck. Never edit them; refresh
  them with `python scripts/copy_golden.py <path to blaeck>`, which records the blaeck commit
  in `tests/golden/SOURCE.txt`.
- A new example also goes in the README examples table. README links are absolute GitHub URLs
  so they work on PyPI. `tests/test_examples.py` runs every script in `examples/`.
- `MANIFEST.in` keeps the tests, examples and changelog in the sdist; new test file types must
  be added there.
- The status page (`src/blaeckpy/static/status.html`) uses no em dashes in visible text.
