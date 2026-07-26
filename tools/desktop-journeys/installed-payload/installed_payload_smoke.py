#!/usr/bin/env python3
"""Keyless smoke of the globally installed @0x-copilot/cli npm payload.

Prerequisite: ``make desktop-install``. This launches the installed package's
``payload/desktop`` with its own Electron dependency; it never accepts the
checkout's APP_DIR as a substitute.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib import DriverSession  # noqa: E402


def main() -> None:
    with DriverSession(
        name="installed-payload-smoke", installed_payload=True
    ) as session:
        status = session.rpc("status")
        assert status["target"] == "installed-payload", status
        assert status["cliPackageRoot"], status
        assert status["appDir"].endswith("payload/desktop"), status
        assert session.wait_for("[data-testid=sign-in-gate]"), (
            "installed payload did not reach the production sign-in gate"
        )
        session.shot("installed-payload-sign-in")


if __name__ == "__main__":
    main()
