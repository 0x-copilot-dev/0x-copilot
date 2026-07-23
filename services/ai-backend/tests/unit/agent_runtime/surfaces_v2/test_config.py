"""``SurfacesV2Flag`` semantics (PRD-A3 D2).

Default off; only ``true`` / ``1`` / ``yes`` / ``on`` (case-insensitive,
trimmed) enable. Everything else — unset, empty, ``false``, ``0``, garbage — is
off. The injectable ``environ`` lets both branches assert without touching
process state.
"""

from __future__ import annotations

import pytest

from agent_runtime.surfaces_v2.config import SurfacesV2Flag


class TestSurfacesV2Flag:
    @pytest.mark.parametrize(
        "value",
        ["true", "TRUE", "True", "1", "yes", "YES", "on", "ON", "  on  ", " TrUe "],
    )
    def test_truthy_values_enable(self, value: str) -> None:
        assert SurfacesV2Flag.enabled({"SURFACES_V2": value}) is True

    @pytest.mark.parametrize(
        "value",
        ["", "  ", "false", "FALSE", "0", "no", "off", "yep", "2", "enabled", "y"],
    )
    def test_non_truthy_values_disable(self, value: str) -> None:
        assert SurfacesV2Flag.enabled({"SURFACES_V2": value}) is False

    def test_unset_is_off(self) -> None:
        assert SurfacesV2Flag.enabled({}) is False

    def test_reads_process_environ_when_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SURFACES_V2", raising=False)
        assert SurfacesV2Flag.enabled() is False
        monkeypatch.setenv("SURFACES_V2", "on")
        assert SurfacesV2Flag.enabled() is True
