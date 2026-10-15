Configuration
=============

zabgrab works without any configuration: it asks for whatever it needs. A config file
saves you from typing the same things each time: your servers, your preferred
settings, and :doc:`templates <guide/templates>` for the fetches you repeat.

Config files
------------

Config files are written in `TOML <https://toml.io/>`_. Open yours in your editor
(it's created if needed) with:

.. code-block:: bash

   zabgrab config edit

zabgrab checks the file when you close the editor, and offers to reopen it if
something's wrong.

zabgrab reads several files, each overriding the ones before. Most people only need
their user config:

1. **zabgrab's built-in defaults.**
2. **A system config,** shared by everyone on the machine:
   ``/etc/zabgrab/config.toml`` (``%PROGRAMDATA%\zabgrab\config.toml`` on Windows).
3. **Your user config:** ``~/.config/zabgrab/config.toml`` on Linux and macOS (or
   under ``$XDG_CONFIG_HOME``), ``%APPDATA%\zabgrab\config.toml`` on Windows.
4. **A project config:** the nearest ``zabgrab.toml`` in the current directory or its
   parents (up to your home directory), for settings and templates that belong with
   a project. ``zabgrab config edit --target project`` creates one in the current
   directory.
5. **A file given with** ``--config FILE`` (or ``$ZABGRAB_CONFIG``).

``--isolated`` ignores the system, user and project files.

To see what's in effect:

.. code-block:: bash

   zabgrab config paths            # which files are read, and which exist
   zabgrab config show             # the merged configuration
   zabgrab config show --sources   # ... with the file each value came from

Settings
--------

``[servers.NAME]``
   A Zabbix server, used by name with ``--server NAME``.

   ``url``
      The address of the Zabbix web interface.
   ``token_env``
      An environment variable holding an API token (see
      :doc:`guide/automation`). Without it, tokens are kept in your keyring.
   ``auth = "password"``
      Log in with a username and password instead of an API token.
   ``username``
      The username to suggest when logging in with a password.
   ``verify_ssl = false``
      Don't check the server's certificate, e.g. for a self-signed one. Only use this
      on networks you trust.
   ``timeout``
      Seconds to wait for each request (default 30).

``[defaults]``
   Settings for every fetch, unless a template or the command line says otherwise.
   Each matches a ``zabgrab fetch`` option: ``server``, ``from``, ``to``,
   ``resolution``, ``agg``, ``format``, ``output_dir``, ``output_pattern``,
   ``timezone``, ``jobs`` and ``max_items`` (see :doc:`guide/fetching`), plus
   ``mat_text_warn_entries``: how many text values a ``.mat`` file can hold before
   zabgrab warns that it'll be big and slow to load (default 200,000; 0 never warns).
   ``.h5`` files store text far more efficiently.

``[templates.NAME]``
   Named fetches: see :doc:`guide/templates`.

Example
-------

This example shows every kind of setting. It's also in the repository as
`config.example.toml
<https://github.com/SeanDS/zabgrab/blob/main/config.example.toml>`_.

.. literalinclude:: ../config.example.toml
   :language: toml
