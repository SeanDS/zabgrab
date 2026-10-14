# zabgrab

A command line tool for fetching data from Zabbix, being rewritten as 1.0.0 in
phases. [PLAN.md](PLAN.md) holds the design, the phases and the decisions log: read
it before starting work, and keep it in sync when the design changes. Development
happens on the `rewrite` branch (or branches merged into it).

## Setup and checks

Use a virtual environment in the repo:

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev,plot]"
.venv/bin/pre-commit install
```

Run all of these before committing:

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest --cov=zabgrab
```

CI (`.github/workflows/ci.yml`) runs the same checks on Linux (Python 3.11 to 3.14),
macOS and Windows, and fails below 100% coverage.

## Working conventions

- **Commit as you go:** commit each self-contained change as soon as it passes the
  checks, without asking first, with a descriptive message.
- **100% test coverage:** add tests that cover every branch of new code. Use
  `# pragma: no cover` only for lines that genuinely can't be tested.
- **Comments:** comment code wherever it isn't trivial, explaining why rather than
  restating what.
- **Docstrings:** every module, class, function and method gets a NumPy-style
  docstring that Sphinx (napoleon) can render: a one-line summary, then the sections
  that apply (`Parameters`, `Returns`, `Yields`, `Raises`, `Attributes`, `Examples`)
  with `-----` underlines. Trivial one-liners may have just the summary. Older code
  that doesn't follow this yet is a follow-up task in PLAN.md; don't widen unrelated
  changes to retrofit it.

## Architecture

- `model/`: data classes only. `services/`: the logic. `adapters/`: Zabbix, config
  files, file formats, keyring. `ui/`: rendering. `cli/`: click wiring only.
- Services get their dependencies (Zabbix client, UI, clock, ...) as arguments and
  never print or prompt directly; they go through the `UI` port, whose questions
  always carry a `fix` for non-interactive runs.
- Data goes to stdout; messages, warnings and progress go to stderr.

## Tests

- Tests run against an in-memory fake Zabbix (`tests/fakezabbix.py`) and fake UI,
  clock and keyring (`tests/fakes.py`); CLI tests use `tests/cli_support.py`.
- Tests run in UK time (expected local times are written in it). On Windows, which
  can't change a running process's time zone, the system time zone must be UK time.
- `tests/test_matlab.py` runs `tests/matlab/check_files.m` in MATLAB when it's
  installed (or listed in `ZABGRAB_MATLAB`), and is skipped otherwise. The `.mat`/`.h5`
  layout is also pinned by MATLAB-verified files in `tests/matlab/reference/`; if a
  change breaks those tests, the new layout must be re-checked in MATLAB first.
