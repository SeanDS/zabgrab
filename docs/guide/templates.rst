Templates
=========

A template is a named fetch kept in your config: which items, from which server, over
what time range, in which format. Once you have one, repeating the fetch is one short
command:

.. code-block:: bash

   zabgrab fetch -t particles -o

Saving a fetch as a template
----------------------------

The easiest way to make a template is to add ``--save-template NAME`` to a fetch:

.. code-block:: bash

   zabgrab fetch -s lab -i "host=lab*,name=Particles *" \
       --from "24 hours ago" --to now --save-template particles

This saves the settings given on the command line and your answers to any questions,
exactly as given: patterns rather than the item IDs they matched, and relative times
(``24 hours ago``) rather than dates, so the template fetches new items and the
latest data each time. Settings from your ``[defaults]`` aren't copied, so changing
them later still applies.

It works with ``--dry-run`` and ``--explain`` too, so you can save a template without
fetching anything. Templates go in your user config, unless ``--save-to`` says
``project``, ``system`` or a file path. zabgrab only replaces an existing template
with ``--force``, and otherwise leaves the file as it was, comments included.

Writing templates
-----------------

Templates are ``[templates.NAME]`` tables in your config (``zabgrab config edit``).
They take ``items``, an optional ``description``, and any setting of ``fetch``
(written as in ``[defaults]``, see :doc:`/configuration`):

.. code-block:: toml

   [templates.particles]
   description = "Particle counts in the labs"
   server = "lab"
   items = [
     { host = "lab*", name = "Particles *" },
     { itemid = 12345 },
   ]
   from = "24 hours ago"
   to = "now"
   format = "h5"

   [templates.network-weekly]
   items = [{ host = "router1", key = "net.if.in[*]" }]
   from = "7 days ago"
   to = "now"
   resolution = "1h"
   agg = ["avg", "max"]

Items are :ref:`item specs <item-specs>` written as tables. List your templates, with
everything else in your config, with ``zabgrab config show``.

Overriding a template
---------------------

Options on the command line override the template, which overrides ``[defaults]``.
zabgrab says when an option overrides a template's value:

.. code-block:: console

   $ zabgrab fetch -t particles --from "7 days ago" -o week.h5
   --from '7 days ago' overrides '24 hours ago' from template 'particles'.

``--item`` adds items to the template's. ``--explain`` shows where each setting came
from. A fetch uses one template at a time.

Choices
-------

Some settings can be a choice instead of a single value, written as ``{ choices =
[...], select = ... }``. ``select`` says how to choose:

``prompt`` (the default)
   Ask which one to use, each time. When zabgrab can't ask, it's an error (with the
   option that gives the answer).
``all``
   Use all of them.
``first``
   Use the first that works.

Choices are allowed for:

``server`` (``prompt`` only)
   Ask which server to fetch from. ``--server`` picks one without asking.

   .. code-block:: toml

      server = { choices = ["lab", "office"] }

``format``
   Ask for the format, or save every format with ``all``.

   .. code-block:: toml

      format = { choices = ["h5", "csv"], select = "all" }

``host`` (in item specs)
   Ask which host, fetch from every host (``all``), or use the first host that has
   matching items (``first``), e.g. for a spare that takes over from a failed
   machine.

   .. code-block:: toml

      items = [
        { host = { choices = ["switch-a", "switch-b"], select = "first" }, name = "Uptime" },
      ]
