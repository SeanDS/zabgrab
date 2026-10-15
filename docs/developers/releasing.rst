Making a release
================

zabgrab's version comes from git tags, through `setuptools_scm
<https://setuptools-scm.readthedocs.io/>`_: there's no version number to edit.
Building at the commit tagged ``1.2.0`` gives version ``1.2.0``, and builds between
releases get development versions such as ``1.2.1.dev3+g1234abc``.

Publishing a release on GitHub also publishes its documentation to GitHub Pages
(the ``Publish documentation`` workflow).

Version numbers
---------------

Releases follow `semantic versioning <https://semver.org/>`_, ``MAJOR.MINOR.PATCH``:

- **PATCH** (``1.0.1``) for bug fixes only.
- **MINOR** (``1.1.0``) for new features that don't break anything: existing
  commands, options, config files and data files work as before.
- **MAJOR** (``2.0.0``) for breaking changes, such as removing or renaming options or
  config keys, or changing the layout of ``.mat`` and ``.h5`` files.

Tags are the bare version number, without a ``v`` prefix.

Before releasing
----------------

1. Make sure the default branch has everything for the release, and that CI passed
   on its latest commit (lint, tests on every platform, the documentation and the
   build smoke test).
2. If the ``.mat``/``.h5`` layout changed, check the new files in MATLAB
   (``tests/matlab/check_files.m``) before releasing; see :doc:`contributing`.
3. Update ``CHANGELOG.rst``: replace ``(unreleased)`` in the heading with the release
   date, e.g. ``1.2.0 (2027-03-01)`` (keeping the underline as long as the
   heading), and check the list of changes is complete.
   Commit this to the default branch (through a pull request, as usual).

Tagging and publishing
----------------------

You can do this on GitHub or from the command line. Either way, the tag must be on
the commit that updated the changelog (or a later one on the default branch).

**On GitHub:**

1. Open *Releases* → *Draft a new release*.
2. Under *Choose a tag*, type the new version (e.g. ``1.2.0``) and choose *Create new
   tag on publish*, with the default branch as the target.
3. Use the version as the title, and paste the version's changelog entries as the
   description (*Generate release notes* adds the merged pull requests too).
4. Click *Publish release*.

**From the command line** (with the `GitHub CLI <https://cli.github.com/>`_):

.. code-block:: bash

   git switch main
   git pull
   git tag -a 1.2.0 -m "zabgrab 1.2.0"
   git push origin 1.2.0
   gh release create 1.2.0 --title 1.2.0 --notes "See CHANGELOG.rst for the changes."

Use an annotated tag (``-a``), so the tag records who made it and when.

Publishing the release starts the ``Publish documentation`` workflow, which builds
the documentation for the tagged version and deploys it to
https://seands.github.io/zabgrab/. Check it ran under the repository's *Actions*
tab (the release's own run only starts another on ``main``, which does the work).

The documentation site
----------------------

GitHub Pages serves two versions of the documentation:

https://seands.github.io/zabgrab/
   The latest release, built from its tag. Until the first release, this redirects
   to the development documentation.
https://seands.github.io/zabgrab/dev/
   The development version, built from ``main`` after every push, with a banner
   saying it's unreleased. Use it to check documentation changes before releasing
   them.

Each deployment replaces the whole site, so the ``Publish documentation`` workflow
always builds both. It runs on pushes to ``main``, when a release is published, and
by hand (*Actions* → *Publish documentation* → *Run workflow*), e.g. to republish
the site after a failed run. Pages must be set to deploy from GitHub Actions (the
repository's *Settings* → *Pages* → *Source*).

Publishing to PyPI
------------------

Releases aren't uploaded to PyPI automatically. To publish one, build it from a
clean checkout of the tag, check it, and upload it with `twine
<https://twine.readthedocs.io/>`_:

.. code-block:: bash

   git switch --detach 1.2.0
   git status            # must be clean, or the version gets a local suffix
   python -m pip install --upgrade build twine
   rm -rf dist
   python -m build
   python -m twine check dist/*
   python -m twine upload dist/*

The files in ``dist/`` must be named after the release version (e.g.
``zabgrab-1.2.0-py3-none-any.whl``); a ``dev`` or ``+g...`` version means the build
wasn't run at the tag or the checkout wasn't clean.

If something goes wrong
-----------------------

A tag that has only just been pushed, before anyone could have installed it, can be
deleted and made again:

.. code-block:: bash

   gh release delete 1.2.0 --yes
   git push --delete origin 1.2.0
   git tag -d 1.2.0

Never move or reuse a version that has been uploaded to PyPI (PyPI refuses to take
the same version twice anyway): fix the problem and release the next patch version
instead.
