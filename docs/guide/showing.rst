Listing values
==============

``zabgrab show`` lists data points as a table, from a saved file or straight from
Zabbix. Use it to read text and log items, which can't be plotted, to find the
highest or lowest values and when they happened, or to look through the data
before analysing it.

.. code-block:: bash

   zabgrab show particles.h5                    # a saved .h5 or .mat file
   zabgrab show -t particles                    # fetched from Zabbix, not saved

Where the data comes from
-------------------------

From a file
   ``zabgrab show FILE`` lists a ``.h5`` or ``.mat`` file's data, merged across its
   fetch groups (points fetched twice are listed once). ``--item``, ``--match``,
   ``--from``, ``--to`` and ``--group`` choose what to list, as for ``plot`` and
   ``export`` (see :ref:`choosing-items`).
From Zabbix
   Without a file, ``zabgrab show`` fetches the data first, as ``zabgrab fetch``
   does, with ``--template``, ``--item``, ``--server``, ``--from``, ``--to``,
   ``--resolution`` and ``--agg``, but doesn't save it.

.. code-block:: bash

   zabgrab show particles.h5 --item "*0.3um" --from "2 days ago"
   zabgrab show -s lab -i "host=lab1,key=log[*]" --from "1 day ago" --to now

``zabgrab fetch --show`` lists the data it fetched in time order (see
:doc:`fetching`); use ``zabgrab show`` to sort or filter it.

Reading the table
-----------------

Points are listed in time order, with each item's name and units when there's
more than one item:

.. code-block:: text

   $ zabgrab show particles.h5
   Time                     Item                          Value
   2026-10-14 09:00:00 BST  lab1: Particles 0.3um [#/m3]  1234.5
   2026-10-14 09:00:30 BST  lab1: Particles 0.5um [#/m3]  201.0
   2026-10-14 09:01:00 BST  lab1: Particles 0.3um [#/m3]  1180.0
   2026-10-14 09:01:30 BST  lab1: Particles 0.5um [#/m3]  188.5
   2026-10-14 09:02:00 BST  lab1: Particles 0.3um [#/m3]  5210.0
   ...

Times are in your local time, or the ``timezone`` setting, with fractions of a
second where Zabbix recorded them. Hourly trends have *Min*, *Avg*, *Max* and
*Count* columns instead of *Value*. Log items also show their log time, severity,
source and event ID, where Zabbix recorded them.

Text values are kept to one line, with ``⏎`` where a line breaks. ``--wrap`` shows
them in full:

.. code-block:: text

   $ zabgrab show lab1-syslog.h5 --wrap
   Time                     Value
   2026-10-14 09:02:10 BST  particle counter: sensor error E12
                            retrying in 5 s
   2026-10-14 09:05:42 BST  particle counter: reconnected

Ordering
--------

``--sort`` chooses the order, and ``--desc`` reverses it:

``--sort time``
   Oldest first (the default); with ``--desc``, newest first.
``--sort value``
   Each item's points by value, smallest first; with ``--desc``, largest first.
   Text sorts alphabetically. Values of different items can't be compared (they're
   often in different units), so each item gets a section of its own.
``--sort min``, ``avg``, ``max``, ``count``
   For hourly trends: by that statistic. ``--sort value`` sorts trends by their
   average.

.. code-block:: bash

   zabgrab show particles.h5 --desc                      # newest first
   zabgrab show particles.h5 --sort value                # smallest values first
   zabgrab show year.h5 --sort max --desc                # hours with the highest peaks

Highest and lowest values
~~~~~~~~~~~~~~~~~~~~~~~~~

Sort by value and limit the list to find, say, each item's two highest values and
when they happened:

.. code-block:: text

   $ zabgrab show particles.h5 --sort value --desc --limit 2
   lab1: Particles 0.3um [#/m3]: 1-2 of 4 values
   Time                     Value [#/m3]
   2026-10-14 09:02:00 BST  5210.0
   2026-10-14 09:03:00 BST  1302.5

   lab1: Particles 0.5um [#/m3]: 1-2 of 3 values
   Time                     Value [#/m3]
   2026-10-14 09:02:30 BST  940.0
   2026-10-14 09:00:30 BST  201.0

Without ``--desc``, the same command lists the lowest values. Finding the top few
values is quick even in a large file, since the rest needn't be sorted.

Paging
------

In a terminal, the list opens in a pager: ``$PAGER`` if it's set, else ``less``.
In ``less``, scroll with the arrow keys or the space bar, scroll sideways for long
text, search with ``/``, and quit with ``q``. A list that fits on one screen is
just printed (unless you've set ``less``'s options yourself, in ``$LESS``). ``--no-pager`` prints the whole list, and setting ``PAGER`` to
nothing turns the pager off for good:

.. code-block:: bash

   zabgrab show particles.h5 --no-pager
   export PAGER=                                # in your shell's profile

Piped into another program or redirected to a file, the list is plain text,
without a pager, so tools such as ``grep``, ``head`` and ``sort`` work on it:

.. code-block:: bash

   zabgrab show lab1-syslog.h5 | grep -c "sensor error"
   zabgrab show particles.h5 > particles.txt

``--limit`` (``-n``) and ``--offset`` show one page at a time, e.g. in scripts:

.. code-block:: bash

   zabgrab show particles.h5 --limit 50                  # values 1-50
   zabgrab show particles.h5 --limit 50 --offset 50      # values 51-100
   zabgrab show particles.h5 --desc --limit 20           # the 20 newest values

In time order, they count values across all items, and zabgrab says which it
showed (e.g. ``Showed values 51-100 of 2,345.``). Sorted by value, they apply to
each item's section.

Filtering
---------

``--grep``
   Only values containing a match for a regular expression, in `Python's syntax
   <https://docs.python.org/3/library/re.html#regular-expression-syntax>`_, which
   is close to ``grep -E``'s. Like ``grep``, it matches anywhere in the value
   unless anchored with ``^`` (start) or ``$`` (end), and is case-sensitive:
   start the expression with ``(?i)`` to ignore case. Numbers are matched as
   they're shown, e.g. ``1234.5``.
``--above``, ``--below``
   Only numeric values greater than, or less than, a number (the number itself is
   left out). For hourly trends, they compare the average, or the statistic named
   by ``--sort``. Text values are left out.

Filters apply before sorting and limiting, so ``--limit`` counts matching values.

.. code-block:: text

   $ zabgrab show lab1-syslog.h5 --grep "(?i)error|reconnect"
   Time                     Value
   2026-10-14 09:02:10 BST  particle counter: sensor error E12 ⏎ retrying in 5 s
   2026-10-14 09:05:42 BST  particle counter: reconnected

.. code-block:: bash

   zabgrab show syslog.h5 --grep "^usb"                  # values starting with "usb"
   zabgrab show syslog.h5 --grep "E1[0-9]"               # error codes E10 to E19
   zabgrab show particles.h5 --above 1000                # values over 1000
   zabgrab show particles.h5 --above 100 --below 500     # between 100 and 500
   zabgrab show year.h5 --sort max --above 5000          # hours peaking over 5000

Combining them
--------------

The options work together. For example, the ten latest sensor errors in the last
week, fetched from Zabbix, in full:

.. code-block:: bash

   zabgrab show -s lab -i "host=lab1,key=log[*]" --from "7 days ago" --to now \
       --grep "(?i)sensor error" --desc --limit 10 --wrap
