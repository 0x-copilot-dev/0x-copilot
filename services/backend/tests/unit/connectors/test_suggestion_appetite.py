"""How forward the agent may be about connectors the user does not have.

A suggestion is the one connector surface the user did not go looking for,
so it carries the highest bar. The per-slug mute already existed server-side
and had no way to be set; this adds the global appetite that sits over it:

    off           nothing, ever
    unblock_only  the curated `discoverable` set (default)
    always        the whole catalog

The precedence that matters: a per-slug mute outranks `always`, because
"show me everything" is a default and "never this one" is a decision.
"""

from __future__ import annotations

from backend_app.contracts import InstallMcpServerRequest
from backend_app.mcp_catalog import DEFAULT_CATALOG
from backend_app.routes.me_preferences import (
    ConnectorSuggestionMode,
    DiscoverableConnectorsPreferences,
)
from backend_app.service import McpRegistryService
from backend_app.store import InMemoryMcpStore

ORG = "org_a"
USER = "usr_1"


def _service() -> McpRegistryService:
    return McpRegistryService(store=InMemoryMcpStore())


def _slugs(service: McpRegistryService, **kwargs: object) -> set[str]:
    response = service.list_suggestible_connectors(org_id=ORG, user_id=USER, **kwargs)  # type: ignore[arg-type]
    return {entry.slug for entry in response.entries}


class TestAppetite:
    def test_off_suggests_nothing_at_all(self) -> None:
        assert _slugs(_service(), mode="off") == set()

    def test_unblock_only_is_the_curated_set(self) -> None:
        curated = {e.slug for e in DEFAULT_CATALOG if e.discoverable}
        assert _slugs(_service(), mode="unblock_only") == curated

    def test_absent_mode_behaves_as_the_default(self) -> None:
        """An older caller that passes no mode must not change behaviour."""

        assert _slugs(_service()) == _slugs(_service(), mode="unblock_only")

    def test_always_widens_to_the_whole_catalog(self) -> None:
        every = {e.slug for e in DEFAULT_CATALOG}
        assert _slugs(_service(), mode="always") == every

    def test_always_reaches_an_entry_the_curated_set_hides(self, monkeypatch) -> None:
        """The widening branch, exercised for real.

        Every shipped entry is currently `discoverable=True`, so `unblock_only`
        and `always` happen to return the same set and the branch would ship
        untested. Substituting one non-discoverable entry is what makes the
        difference observable.
        """

        import dataclasses

        from backend_app import service as service_module

        hidden = dataclasses.replace(DEFAULT_CATALOG[0], discoverable=False)
        monkeypatch.setattr(service_module, "DEFAULT_CATALOG", (hidden,))
        service = _service()

        assert _slugs(service, mode="unblock_only") == set()
        assert _slugs(service, mode="always") == {hidden.slug}

    def test_always_is_a_widening_not_a_reset(self) -> None:
        """It only adds the non-curated entries; nothing curated is lost."""

        curated = _slugs(_service(), mode="unblock_only")
        assert curated <= _slugs(_service(), mode="always")


class TestPrecedence:
    def test_a_mute_outranks_always(self) -> None:
        """ "Show me everything" is a default; "never this one" is a decision."""

        muted = next(e.slug for e in DEFAULT_CATALOG)
        assert muted not in _slugs(
            _service(), mode="always", user_overrides={muted: False}
        )

    def test_off_outranks_an_explicit_unmute(self) -> None:
        """Off means off — no per-entry rule talks its way past it."""

        unmuted = next(e.slug for e in DEFAULT_CATALOG)
        assert _slugs(_service(), mode="off", user_overrides={unmuted: True}) == set()

    def test_an_installed_connector_is_never_suggested(self) -> None:
        """The complement rule: suggestions and the composer are disjoint."""

        service = _service()
        installed = next(
            e
            for e in DEFAULT_CATALOG
            if e.discoverable and not e.requires_pre_registered_client
        )
        service.install_from_catalog(
            InstallMcpServerRequest(org_id=ORG, user_id=USER, slug=installed.slug)
        )
        assert installed.slug not in _slugs(service, mode="always")


class TestPreferenceContract:
    def test_the_shipped_default_is_unblock_only(self) -> None:
        assert (
            DiscoverableConnectorsPreferences().mode
            is ConnectorSuggestionMode.UNBLOCK_ONLY
        )

    def test_the_service_and_the_preference_agree_on_the_wire_values(self) -> None:
        """The service compares plain strings to avoid importing a routes
        module; this is the test that keeps the two sides honest."""

        from backend_app.service import _SUGGESTIONS_ALWAYS, _SUGGESTIONS_OFF

        assert _SUGGESTIONS_OFF == ConnectorSuggestionMode.OFF.value
        assert _SUGGESTIONS_ALWAYS == ConnectorSuggestionMode.ALWAYS.value

    def test_an_unknown_mode_is_rejected(self) -> None:
        import pytest

        with pytest.raises(Exception):
            DiscoverableConnectorsPreferences.model_validate({"mode": "sometimes"})
