Fetching data
=============

``zabgrab fetch`` downloads items' data over a time range and saves it to a file. It
takes the items from a :doc:`template <templates>`, from ``--item`` options, or
both:

.. code-block:: bash

   zabgrab fetch -i "host=lab*,name=Particles *" \
       --from "7 days ago" --to now -o data.h5
   zabgrab fetch -t particles -o data.h5
   zabgrab fetch -t particles -i 12345 -o data.h5     # a template plus another item

Each fetch uses one server: ``--server`` (``-s``), else the template's, else your
default server, else zabgrab asks. ``--server`` takes a configured server's name or a
URL.

Anything a fetch needs that isn't given (the times, the output format, which server)
is asked for. Afterwards, zabgrab shows the command that would have avoided the
questions (``Next time: zabgrab fetch ...``), which you can copy into a script.

Time ranges
-----------

``--from`` and ``--to`` take times in many forms:

- relative: ``now``, ``24 hours ago``, ``3 days ago``, ``yesterday``;
- ISO 8601: ``2026-10-14``, ``2026-10-14 09:00``, ``2026-10-14T09:00:00Z``,
  ``2026-10-14T09:00+02:00``;
- day-first dates: ``14/10/2026``, ``14 Oct 2026 09:00``.

Times without a time zone are in your local time, or in the ``timezone`` setting
(``--timezone Europe/London``, or ``timezone = "UTC"`` in your config). Files always
store times in UTC.

Zabbix only keeps data for a while (each item's *storage period*, often 7 to 90 days
for raw values and a year or more for hourly trends). If the range starts before the
item's storage period, zabgrab warns that older data may have been deleted, and for
raw values, suggests hourly trends where they're kept longer.

Raw values or hourly trends
---------------------------

By default zabgrab fetches every **raw** value Zabbix collected. Over long ranges,
that can be a lot of data. Zabbix also keeps **hourly trends** for numeric items:
each hour's minimum, average, maximum and number of values. Fetch them with
``--resolution 1h``:

.. code-block:: bash

   zabgrab fetch -t particles --from "1 year ago" --to now --resolution 1h -o year.h5

``--agg`` picks which of the hourly statistics to save (by default ``min``, ``avg``
and ``max``; ``count`` is also available):

.. code-block:: bash

   zabgrab fetch -t particles --resolution 1h --agg avg --agg max -o year.h5

``--agg`` only applies to hourly trends; with raw data it's ignored, with a warning.
Text items (see below) have no trends, so they're skipped with ``--resolution 1h``.

Zabbix computes trends itself, and only hourly. For other averages, such as five
minutes, add a calculated item in Zabbix (e.g. with the formula
``avg(//particles[0.3],5m)``) and fetch that.

Item types
----------

zabgrab fetches every Zabbix value type:

========================= ====================================================
Value type                Saved as
========================= ====================================================
Numeric (float)           64-bit floating point numbers
Numeric (unsigned)        64-bit unsigned integers (no loss of precision)
Character, text           text
Log                       text, plus each entry's log time, source, severity and
                          event ID
========================= ====================================================

Binary items aren't supported, and are skipped with a warning.

Saving
------

``--output`` (``-o``) says where to save the data:

``-o data.h5``
   That file. The extension picks the format, unless ``--format`` is given.
``-o results/``
   An automatic name in that directory (a name ending in ``/``, or an existing
   directory).
``-o``
   An automatic name in the ``output_dir`` setting (by default the current
   directory).

Repeat ``--output`` to save to several files at once, e.g. in several formats:

.. code-block:: bash

   zabgrab fetch -t particles -o data.h5 -o data.csv
   zabgrab fetch -t particles -o -o results/     # automatic names in both places

If a format is asked for (see below), it's asked once and used for every output
without an extension.

Without ``--output``, zabgrab asks whether to save the data under an automatic name:
answer ``y`` to save it there, ``n`` not to save it, or ``o`` to type another path
(a file, or a directory ending in ``/``), which works like giving it with
``--output``. When it can't ask (see :doc:`automation`), it fetches the data but
doesn't save it, and says so.

The formats are:

======================= ======================================================
Format                  Use it for
======================= ======================================================
``h5`` (HDF5)           Python (h5py, pandas), MATLAB (``h5read``), and most
                        analysis tools. Compact, and can be updated.
``mat`` (MATLAB v7.3)   MATLAB's ``load``. The same layout as ``.h5``, and can be
                        updated too.
``tsv``, ``csv``        Spreadsheets and text tools. Can't be updated.
======================= ======================================================

zabgrab asks for the format if it can't tell from the file name, ``--format`` or the
``format`` setting. Every file starts with metadata describing exactly what was
fetched: from which server, by which command, the items, the time range, and where
each setting came from. See :doc:`analysing` for the files' layouts.

Automatic file names
~~~~~~~~~~~~~~~~~~~~

Automatic names follow the ``output_pattern`` setting, by default
``{hosts}_{template}_{from}--{to}``, giving e.g.
``lab1+lab2_particles_20261014T091500Z--20261015T091500Z.h5``. The
placeholders are:

``{hosts}``
   The hosts of the items, sorted and joined with ``+`` (more than three are
   shortened, e.g. ``a+b+5more``).
``{template}``
   The template's name, plus ``__custom`` if the command line added items or changed
   which data is fetched (only ``__custom`` without a template).
``{from}``, ``{to}``
   The time range, in UTC (e.g. ``20261014T091500Z``).
``{server}``, ``{resolution}``
   The server's name and the resolution (``raw`` or ``1h``).

Automatic names never overwrite an existing file: zabgrab adds ``-2``, ``-3``, ...
instead.

Existing files
~~~~~~~~~~~~~~

If the output file exists, zabgrab asks whether to overwrite it, save next to it
under a new name, or (for ``.h5`` and ``.mat`` files) add the data to it as a new
group. ``--overwrite`` replaces it without asking, and ``--append`` adds to it (see
:doc:`updating`; with several outputs, it adds to each, so they must all be ``.h5``
or ``.mat`` files). When zabgrab can't ask, it saves next to the existing file.

Large numbers of items
----------------------

A pattern can match more items than you meant. If a fetch matches more than
``max_items`` items (by default 50), zabgrab asks whether to fetch them all, choose
some, or cancel; ``--yes`` (``-y``) fetches them all without asking, as do runs where
zabgrab can't ask (with a warning).

``--jobs`` sets how many items download at once (by default 6). Each item's data is
downloaded in chunks (a day of raw values, or 30 days of trends, at a time), and
failed requests are retried. If some items fail, the others are still saved, and
zabgrab exits with status 3.

Checking before fetching
------------------------

Two options show what a fetch would do, without fetching:

``--explain``
   Shows every setting (server, times, resolution, format, ...) and where each came
   from: the command line, the template, your config, or zabgrab's defaults.
``--dry-run``
   Looks up the items and lists the ones that would be fetched (and any that would be
   skipped, and why).

.. code-block:: bash

   zabgrab fetch -t particles --explain
   zabgrab fetch -t particles --from "2 days ago" --to now --dry-run

Plotting or listing straight away
---------------------------------

``--plot`` plots the data when it's fetched (with the ``plot`` extra installed);
``--log`` uses log scales. ``--show`` lists it, in time order (see
:doc:`showing`). Either way, the data is then only saved if ``--output`` is given
too.

.. code-block:: bash

   zabgrab fetch -t particles --from "6 hours ago" --to now --plot
   zabgrab fetch -t particles --from "6 hours ago" --to now --show -o recent.h5

To sort or filter the values, use ``zabgrab show``, which can also fetch them
first.
