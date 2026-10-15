Quickstart
==========

This page goes from nothing to a file of data in a few minutes. You need a Zabbix
account on the server you want data from.

1. Tell zabgrab about your server
---------------------------------

Open your config file in your editor (zabgrab creates it if needed):

.. code-block:: bash

   zabgrab config edit

and add your server, giving it a short name:

.. code-block:: toml

   [servers.lab]
   url = "https://zabbix.example.org"   # the address of the Zabbix web interface

   [defaults]
   server = "lab"                       # use it unless told otherwise

You can skip this step: zabgrab then asks for the server's address when it needs
one, and offers to save it for you.

.. _logging-in:

2. Log in with an API token
---------------------------

zabgrab logs in with a Zabbix API token. Create one in the Zabbix web interface under
*User settings* → *API tokens* → *Create API token*, copy it, and store it in your
system's keyring:

.. code-block:: bash

   zabgrab token add lab

zabgrab asks for the token, checks it works and saves it. You only do this once per
server. (If you don't, zabgrab asks for a token the first time it connects.)

.. tip::

   On a machine without a keyring, such as a server running scheduled jobs, put the
   token in an environment variable instead and name it in the server's config with
   ``token_env = "LAB_ZABBIX_TOKEN"``. To log in with your username and password
   rather than a token, set ``auth = "password"``.

3. Find the items you want
--------------------------

Items are the things Zabbix measures, each on a host. Search for them by name:

.. code-block:: console

   $ zabgrab items "particles" --host lab
    Item ID   Host         Name                Key               Type    Units   Last value   Age
      41207   lab1   Particles 0.3um     particles[0.3]    float   #/m3    1234.5        25s
      41208   lab1   Particles 0.5um     particles[0.5]    float   #/m3    201.0         25s
      41377   lab2   Particles 0.3um     particles[0.3]    float   #/m3    988.1         31s

Searches work like the filters in the Zabbix web interface: they ignore case, match
anywhere in the text, and ``*`` matches anything. See :doc:`guide/finding` for more.

4. Fetch the data
-----------------

Fetch the last day of those items into an HDF5 file:

.. code-block:: bash

   zabgrab fetch --item "host=lab*,name=Particles *" \
       --from "24 hours ago" --to now --output particles.h5

zabgrab downloads every value in that time range, with progress bars, and saves them
with metadata recording exactly what was fetched. Leave out ``--from``, ``--to`` or
the file name, and zabgrab asks for them.

.. code-block:: text

   Fetched 3 items (4,317 points) from 'lab', 2026-10-14 10:15:00 BST to
   2026-10-15 10:15:00 BST; saved to particles.h5 (group fetch_0001).

5. Use it
---------

Plot it straight away (with the ``plot`` extra installed):

.. code-block:: bash

   zabgrab plot particles.h5

or load it in MATLAB or Python (see :doc:`guide/analysing`):

.. code-block:: matlab

   S = zabgrab_load('particles.h5');
   plot(S(1).time, S(1).value)

Next steps
----------

- Save the fetch as a template so you can repeat it with one short command:
  :doc:`guide/templates`.
- Keep the file up to date with ``zabgrab update particles.h5``:
  :doc:`guide/updating`.
- Run zabgrab from scripts and scheduled jobs: :doc:`guide/automation`.
