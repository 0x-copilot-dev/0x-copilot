"""Make ``tools/`` importable for its own tests, whatever the pytest rootdir.

Five tests in this directory import a sibling checker by bare name
(``from check_service_boundaries import ...``). That works when pytest resolves
rootdir to the repo root, because the ``prepend`` import mode puts this directory
on ``sys.path``.

It stops working the moment a caller passes these files alongside tests from a
*different* project in one invocation — pytest then resolves rootdir to that
project, adopts its ``pyproject.toml`` as the config authority, imports this file
under a different module name, and never puts this directory on ``sys.path``:

    ModuleNotFoundError: No module named 'check_service_boundaries'

That is exactly what `ci-e2-final-conformance.yml` did, and it left `main` red
for days while every PR inherited a failure that had nothing to do with its diff.
The workflow now runs each project under its own rootdir, which is the real fix.
This conftest is the guard: it makes the failure mode impossible for the other
four sibling-importing tests, so the next caller who combines paths gets a
working run instead of a collection error.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TOOLS_DIR = str(Path(__file__).resolve().parent)

if _TOOLS_DIR not in sys.path:
    # Appended, not prepended: these are repo-local scripts and must never
    # shadow a real dependency that happens to share a name.
    sys.path.append(_TOOLS_DIR)
