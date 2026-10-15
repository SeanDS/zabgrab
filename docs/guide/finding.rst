Finding hosts and items
=======================

Zabbix organises what it monitors into **hosts** (machines, devices, services), each
with **items** (the individual measurements, such as a temperature or a disk's free
space). Before fetching data, find the items you want with ``zabgrab hosts`` and
``zabgrab items``.

Patterns
--------

Every search pattern works like the filters in the Zabbix web interface:

- it ignores case: ``cpu`` finds ``CPU load``;
- it matches anywhere in the text: ``load`` finds ``CPU load average``;
- ``*`` matches any text: ``Particles *um`` finds ``Particles 0.3um`` and
  ``Particles 5um``.

Host patterns match either a host's technical name (``lab1``) or its visible
name (``Lab 1``).

Hosts
-----

.. code-block:: bash

   zabgrab hosts                     # every host you can see
   zabgrab hosts clean               # hosts whose name contains "clean"
   zabgrab hosts --group "Lab*"      # hosts in host groups matching "Lab*"

The table shows each host's ID, technical and visible names, groups, and whether
it's monitored (disabled hosts are dimmed).

Items
-----

.. code-block:: bash

   zabgrab items "Particles *"                     # by item name
   zabgrab items --host lab1                 # every item on a host
   zabgrab items --key "net.if.in[*]"              # by item key
   zabgrab items --tag area:lab                    # by tag ("name" or "name:value")
   zabgrab items temperature --host "rack*" --tag location:basement

A name pattern and the ``--host``, ``--key`` and ``--tag`` options can be combined:
items must match all of them. The table shows each item's ID, host, name, key, value
type, units, latest value and how long ago it was collected.

Results are sorted by item ID. Sort by any other column with ``--sort``, naming it
as in the table header, in any case:

.. code-block:: bash

   zabgrab items --host lab1 --sort "last value"
   zabgrab items --host lab1 --sort age          # most recently updated first

Searches return at most 500 results per server; ``--limit`` changes this. zabgrab
says when the limit cut the results short.

Searching several servers
-------------------------

Without ``--server``, searches use your default server (or ask which one). Give
``--server`` more than once to search several servers at once; the tables then gain a
*Server* column:

.. code-block:: bash

   zabgrab items "Particles *" --server lab --server office

.. _item-specs:

Item specs: choosing items to fetch
-----------------------------------

Commands that fetch data take items as **item specs**, with ``--item`` (``-i``).
``zabgrab items --item`` accepts them too, so you can check what a spec matches
before fetching it. An item spec is one of:

``12345``
   One item, by its ID (as shown by ``zabgrab items``).
``Particles *``
   Items whose name matches the pattern.
``host=lab1,name=Particles *``
   Comma-separated ``field=pattern`` pairs, which must all match. The fields are
   ``itemid``, ``host``, ``name``, ``key`` and ``tag``.

Item keys often contain commas (``net.if.in[eth0,bytes]``). That's fine: only a comma
followed by a field name and ``=`` starts a new pair, so
``host=router1,key=net.if.in[eth0,bytes]`` works as you'd expect.

Item IDs are exact and never change, but patterns pick up new items as they're
added (a new host, a new sensor), so they're usually better in templates. In config
files, item specs are written as tables (see :doc:`templates`):

.. code-block:: toml

   items = [
     { host = "lab*", name = "Particles *" },
     { itemid = 12345 },
     { key = "net.if.in[*]", tag = "area:lab" },
   ]

You only see the hosts and items your Zabbix account has permission to read.
