Scripts and scheduled jobs
==========================

zabgrab asks questions when something's missing, which is convenient at a terminal
but no use in a script or a scheduled job. This page covers running it unattended.

When zabgrab asks, and when it doesn't
--------------------------------------

zabgrab only asks questions when it's running in a terminal. It never asks when:

- its input or error output isn't a terminal (in a pipe, a cron job, a CI job, ...);
- ``--no-input`` is given;
- ``$ZABGRAB_NO_INPUT`` is set (to anything but ``0``);
- ``$CI`` is set (as CI services do).

Then, instead of asking:

- a question that needs an answer (which server, what time range, which format) stops
  zabgrab with an error that says exactly how to give the answer, such as the
  option to pass or the config setting to add;
- a yes/no question takes its default answer, with a warning (e.g. a fetch matching
  more than ``max_items`` items fetches them all);
- an existing output file is kept, and the data saved next to it under a new name
  (pass ``--overwrite`` to replace it);
- without ``--output``, the data is fetched but not saved (with a warning).

So the reliable way to script zabgrab is to give everything on the command line or in
a template, and to try the command once by hand with ``--no-input``:

.. code-block:: bash

   zabgrab --no-input fetch -t particles --from "1 hour ago" --to now -o data/

After any fetch where you answered questions, zabgrab prints the equivalent command
(``Next time: ...``), which is a good starting point for a script.

Logging in without a keyring
----------------------------

Scheduled jobs often run where there's no keyring, or a locked one. Give them the
API token in an environment variable instead, named in the server's config:

.. code-block:: toml

   [servers.lab]
   url = "https://zabbix.example.org"
   token_env = "LAB_ZABBIX_TOKEN"

zabgrab uses the variable when it's set, and the keyring otherwise. ``zabgrab token
list`` shows where each server's token comes from, and ``zabgrab token list --check``
checks they work.

.. warning::

   Treat API tokens like passwords: keep them out of scripts, version control and
   command lines. Load them from a file only you can read, or from your scheduler's
   secret store. Give tokens an expiry date in Zabbix, and use an account that can
   only read what the job needs.

A scheduled update
------------------

For example, to add the latest data to a file every morning with cron:

.. code-block:: bash

   # crontab -e
   # m  h  dom mon dow  command
   15   6  *   *   *    . $HOME/.zabbix-token && $HOME/.local/bin/zabgrab update $HOME/data/particles.h5 >> $HOME/data/update.log 2>&1

where ``~/.zabbix-token`` (readable only by you: ``chmod 600``) contains
``export LAB_ZABBIX_TOKEN=...``. Use the full path to ``zabgrab``, since cron's
``PATH`` is minimal; ``which zabgrab`` shows it.

Exit status
-----------

zabgrab's exit status tells a script how it went:

====== =============================================================================
Status Meaning
====== =============================================================================
0      Success.
1      An error: nothing was done (or saved).
2      A mistake on the command line, such as an unknown option.
3      Some items couldn't be fetched, but the others were saved.
130    Cancelled (Ctrl+C, or cancelled at a question).
====== =============================================================================

Output
------

zabgrab writes data (tables, TOML) to standard output, and everything else (progress,
messages, warnings, errors) to standard error. So you can pipe or redirect tables
without the messages getting mixed in:

.. code-block:: bash

   zabgrab items --host lab1 > items.txt

Progress bars only show in a terminal. ``-q`` hides messages, ``-qq`` hides warnings
too (errors always show), and ``-v`` shows more detail. Colour is off when output
isn't a terminal or ``$NO_COLOR`` is set; ``--color`` and ``--no-color`` force it.
Add ``--debug`` to see full tracebacks for errors.

Global options such as these can go before or after the command:
``zabgrab -q update file.h5`` and ``zabgrab update file.h5 -q`` are the same.
