Contributing
============

Setting up
----------

Clone the repository and install zabgrab in a virtual environment, in editable mode
with the development tools:

.. code-block:: bash

   git clone https://github.com/SeanDS/zabgrab.git
   cd zabgrab
   python -m venv .venv
   .venv/bin/pip install -e ".[dev,plot,docs]"
   .venv/bin/pre-commit install

The pre-commit hooks check formatting and linting on each commit.

Checks
------

Run these before pushing; CI runs the same checks on Linux (Python 3.11 to 3.14),
macOS and Windows:

.. code-block:: bash

   .venv/bin/ruff check .
   .venv/bin/ruff format --check .
   .venv/bin/mypy
   .venv/bin/pytest --cov=zabgrab

The tests need 100% coverage (branches included) to pass. Use ``# pragma: no cover``
only for lines that genuinely can't be tested.

CI's last job, *CI passed*, passes only if every other job did; it's the one check
``main``'s branch rules require.

The tests run in UK time, which their expected local times are written in. On Linux
and macOS they set it themselves; Windows can't change a running process's time
zone, so there the system time zone must be UK time (``tzutil /s "GMT Standard
Time"``).

Building the documentation
--------------------------

.. code-block:: bash

   .venv/bin/sphinx-build -b html docs docs/_build/html

Then open ``docs/_build/html/index.html``. CI builds the documentation too, and fails
on any warning (add ``--fail-on-warning`` to see the same). Each CI run keeps the
built documentation as an artifact named ``docs``, and once changes reach ``main``
they're published as the `development documentation
<https://seands.github.io/zabgrab/dev/>`_ (see :doc:`releasing`).

Code layout
-----------

The code is split into layers, and dependencies only point inwards:

``zabgrab.model``
   Data classes only: config, item specs, Zabbix objects, queries and fetched data.
``zabgrab.services``
   The logic: resolving settings, choosing servers, logging in, finding items,
   downloading, naming files, updating and merging. Services get everything they
   need (the Zabbix client, the UI, the clock, ...) as arguments, through the
   protocols in :mod:`zabgrab.services.ports`, and never print or prompt directly.
``zabgrab.adapters``
   The outside world: the Zabbix API, config files, file formats and the keyring.
``zabgrab.ui``
   Terminal output (rich) and plots (matplotlib).
``zabgrab.cli``
   The click commands, which only wire the rest together.

Data goes to stdout; messages, warnings, questions and progress go to stderr. Every
question carries a ``fix``: how to give the answer without being asked, shown when
zabgrab can't ask (see :doc:`/guide/automation`).

Conventions
-----------

- Comment code wherever it isn't trivial, explaining why rather than restating what.
- Every module, class, function and method has a NumPy-style docstring, which this
  documentation's :doc:`API reference <api>` is built from. ruff's docstring rules
  check the basics, and examples in docstrings run as tests.
- Add tests covering every branch of new code. Tests use an in-memory fake Zabbix
  server (``tests/fakezabbix.py``) and fake UI, clock and keyring
  (``tests/fakes.py``); command line tests use ``tests/cli_support.py``.
- Record design decisions in ``PLAN.md``, and user-visible changes in ``CHANGELOG.rst``
  under the next version's heading (``(unreleased)``). Raise that version if the
  change needs it (see :ref:`version-numbers`): a new feature makes a patch heading
  such as ``1.0.1`` the next minor version, ``1.1.0``.

MATLAB files
------------

The layout of ``.mat`` and ``.h5`` files is pinned by files verified in MATLAB, in
``tests/matlab/reference/``. If a change breaks the tests comparing against them, the
new layout must be checked in MATLAB before the reference files are replaced:
``tests/test_matlab.py`` runs ``tests/matlab/check_files.m`` in MATLAB when it's
installed (or listed in ``$ZABGRAB_MATLAB``), and is skipped otherwise.

Every group records the version of the layout it was written with
(``FORMAT_VERSION`` in ``zabgrab.model.series``, and ``supported_format`` in
``matlab/zabgrab_load.m``). Readers refuse newer versions, so increase both whenever
the layout changes in a way an older zabgrab or ``zabgrab_load`` would misread:
removing, renaming or changing the meaning of a group, dataset, attribute or
metadata key. Purely additive changes that older readers safely ignore don't need a
new version. Readers must keep reading every older version.
