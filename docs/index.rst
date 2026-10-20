zabgrab
=======

zabgrab fetches data from `Zabbix <https://www.zabbix.com/>`_ servers into files you
can analyse: MATLAB (``.mat``), HDF5 (``.h5``), and tab- or comma-separated text.

Zabbix is good at collecting monitoring data and showing it on dashboards, but
getting the numbers out for your own analysis means clicking through the web
interface or scripting its API. zabgrab does that for you:

- **Find what's monitored:** search hosts and items like the Zabbix web interface's
  filters, from the command line.
- **Fetch data over any time range:** every raw value, or Zabbix's hourly trends,
  for as many items as you like, downloaded in parallel.
- **Save it in the format you work in:** MATLAB, HDF5, TSV or CSV, with metadata
  recording exactly what was fetched, from where and when.
- **Repeat fetches easily:** save them as templates, and keep files up to date by
  adding only the data that's new.
- **Look at it:** plot files straight away, list their values (sorted, filtered
  and paged), or merge and convert them.

.. code-block:: bash

   zabgrab items "Particles *" --host lab1
   zabgrab fetch -t particles --from "7 days ago" --to now -o particles.h5
   zabgrab update particles.h5
   zabgrab plot particles.h5
   zabgrab show particles.h5 --sort value --desc --limit 10

New to zabgrab? Start with :doc:`installation` and the :doc:`quickstart`.

.. toctree::
   :maxdepth: 1
   :caption: User guide

   installation
   quickstart
   guide/finding
   guide/fetching
   guide/templates
   guide/updating
   guide/showing
   guide/analysing
   guide/automation
   configuration
   cli

.. toctree::
   :maxdepth: 1
   :caption: Development

   developers/index
   changelog
