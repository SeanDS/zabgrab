Making a release
================

zabgrab's version comes from git tags, through `setuptools_scm
<https://setuptools-scm.readthedocs.io/>`_: there's no version number to edit.
Building at the commit tagged ``1.2.0`` gives version ``1.2.0``, and builds between
releases get development versions such as ``1.2.1.dev3+g1234abc``.

Publishing a release on GitHub uploads it to PyPI (the ``Publish to PyPI``
workflow) and publishes its documentation to GitHub Pages (the ``Publish
documentation`` workflow).

Version numbers
---------------

Releases follow `semantic versioning <https://semver.org/>`_, ``MAJOR.MINOR.PATCH``:

- **PATCH** (``1.0.1``) for bug fixes only.
- **MINOR** (``1.1.0``) for new features that don't break anything: existing
  commands, options, config files and data files work as before.
- **MAJOR** (``2.0.0``) for breaking changes, such as removing or renaming options or
  config keys, or changing the layout of ``.mat`` and ``.h5`` files.

Tags are the bare version number, without a ``v`` prefix.

Preparing the release
---------------------

Each release is prepared on its own branch, which is tagged and then merged into
``main``:

1. Make sure ``main`` has everything for the release, and that CI passed on its
   latest commit (lint, tests on every platform, the documentation and the build
   smoke test).
2. If the ``.mat``/``.h5`` layout changed, check the new files in MATLAB
   (``tests/matlab/check_files.m``) before releasing; see :doc:`contributing`.
3. Create a release branch from ``main``, named after the version:

   .. code-block:: bash

      git switch main
      git pull
      git switch -c release/1.2.0

4. Update ``CHANGELOG.rst``: replace ``(unreleased)`` in the heading with the release
   date, e.g. ``1.2.0 (2027-03-01)`` (keeping the underline as long as the
   heading), and check the list of changes is complete. Make any other last
   changes for the release here too, such as documentation fixes.
5. Commit, push the branch and open a pull request into ``main``:

   .. code-block:: bash

      git commit -am "Release 1.2.0"
      git push -u origin release/1.2.0
      gh pr create --base main --title "Release 1.2.0" --body "Release 1.2.0."

   Wait for CI to pass on it. Fix anything that comes up on the same branch.

Tagging and publishing
----------------------

Tag the release branch's last commit, once CI passed on it, then merge the pull
request into ``main``. Use an annotated tag (``-a``), so the tag records who made
it and when:

.. code-block:: bash

   git switch release/1.2.0
   git pull
   git tag -a 1.2.0 -m "zabgrab 1.2.0"
   git push origin 1.2.0
   gh release create 1.2.0 --title 1.2.0 --notes "See CHANGELOG.rst for the changes."
   gh pr merge release/1.2.0 --merge --delete-branch

Merge with a merge commit (``--merge``, or *Create a merge commit* on GitHub), not
*Squash and merge* or *Rebase and merge*: those copy the changes into new commits,
leaving the tagged commit outside ``main``'s history. With a merge commit, ``main``
contains the tag, so later development builds are numbered after it (e.g.
``1.2.1.dev3+g1234abc``).

To publish the release on GitHub instead of with ``gh release create``: push the
tag, then open *Releases* → *Draft a new release*, choose the tag, use the version
as the title, paste the version's changelog entries as the description (*Generate
release notes* adds the merged pull requests too), and click *Publish release*.

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

Publishing the release also starts the ``Publish to PyPI`` workflow, which builds
the release from its tag and uploads it to https://pypi.org/project/zabgrab/. It
checks first that the files are named for the tag's version (e.g.
``zabgrab-1.2.0-py3-none-any.whl``), and stops if they aren't, since PyPI never
accepts the same version twice. Check it ran under *Actions*, then check the new
version installs:

.. code-block:: bash

   pip install --upgrade zabgrab==1.2.0

The workflow uploads with PyPI's `trusted publishing
<https://docs.pypi.org/trusted-publishers/>`_: PyPI trusts uploads from this
workflow in this repository, so no API token is stored anywhere.

Setting it up
~~~~~~~~~~~~~

This is done once, before the first automatic release:

1. On PyPI, open the zabgrab project's *Manage* → *Publishing* page (for a project
   not on PyPI yet, use *Your account* → *Publishing* to add a pending
   publisher), and add a GitHub trusted publisher:

   - Owner: ``SeanDS``
   - Repository name: ``zabgrab``
   - Workflow name: ``publish.yml``
   - Environment name: ``pypi``

2. On GitHub, open the repository's *Settings* → *Environments*, and create an
   environment named ``pypi``. Optionally, add yourself under *Required reviewers*:
   each upload then waits for you to approve it, in the workflow run's page.
3. To try the setup without a release, do the same on `TestPyPI
   <https://test.pypi.org/>`_ (a separate account), with the environment name
   ``testpypi``, and create a ``testpypi`` environment on GitHub. Then run the
   workflow by hand (*Actions* → *Publish to PyPI* → *Run workflow*): it uploads
   a development build of the chosen branch (e.g. ``1.2.1.dev3``) to TestPyPI.
   Install it from there to check it:

   .. code-block:: bash

      pip install --index-url https://test.pypi.org/simple/ \
          --extra-index-url https://pypi.org/simple/ zabgrab==1.2.1.dev3

Publishing by hand
~~~~~~~~~~~~~~~~~~

If the workflow can't be used, build the release from a clean checkout of the tag,
check it, and upload it with `twine <https://twine.readthedocs.io/>`_ and a PyPI
API token:

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

If the ``Publish to PyPI`` workflow failed before uploading (e.g. the trusted
publisher wasn't set up), fix the problem, then open the failed run under *Actions*
and click *Re-run failed jobs*.

A tag that hasn't been uploaded to PyPI (its release isn't published yet, or the
upload failed), before anyone could have installed it, can be deleted and made
again:

.. code-block:: bash

   gh release delete 1.2.0 --yes
   git push --delete origin 1.2.0
   git tag -d 1.2.0

Never move or reuse a version that has been uploaded to PyPI (PyPI refuses to take
the same version twice anyway): fix the problem and release the next patch version
instead.
