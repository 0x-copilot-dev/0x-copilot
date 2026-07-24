"""``SurfacesV2Flag`` semantics (PRD-A3 D2 / E3 D5 flip).

**Default on** (E3): an unset ``SURFACES_V2`` resolves *on*. Only ``true`` / ``1``
/ ``yes`` / ``on`` (case-insensitive, trimmed) enable when the var is explicitly
set; an explicitly-empty, ``false`` / ``0`` / ``no`` / ``off`` / garbage value is
the kill switch and resolves off. The injectable ``environ`` lets both branches
assert without touching process state.
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
    def test_explicit_non_truthy_values_disable(self, value: str) -> None:
        # An explicitly-set non-truthy value is the kill switch — off even though
        # the *default* (unset) is on.
        assert SurfacesV2Flag.enabled({"SURFACES_V2": value}) is False

    def test_unset_is_on(self) -> None:
        # E3 flip: absent env var defaults on (v2 owns surface emission).
        assert SurfacesV2Flag.enabled({}) is True

    def test_reads_process_environ_when_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Unset process env ⇒ default-on; explicit "false" ⇒ kill switch.
        monkeypatch.delenv("SURFACES_V2", raising=False)
        assert SurfacesV2Flag.enabled() is True
        monkeypatch.setenv("SURFACES_V2", "false")
        assert SurfacesV2Flag.enabled() is False
        monkeypatch.setenv("SURFACES_V2", "on")
        assert SurfacesV2Flag.enabled() is True
