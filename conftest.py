"""Root conftest.

Its only job is to load the reporting plugin. ``pytest_plugins`` is only
honoured in the rootdir conftest, which is why this file exists at the top
level rather than the declaration living in ``tests/conftest.py``.

Being here also puts the repository root on ``sys.path``, so ``tests.*`` and
``app.*`` import the same way whether pytest is invoked from the root, from
``tests/``, or inside the container.
"""

pytest_plugins = ["tests.report_plugin"]
