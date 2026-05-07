"""PR 4.4.7 Phase 2 (Slice B) — catalog suggestion filter.

The ai-backend reads this endpoint at run-create and stuffs the
response into ``AgentRuntimeContext.suggested_connectors``. The filter
rules:

  1. Drop slugs whose ``seed:<slug>`` is already installed.
  2. Drop slugs in ``exclude_paused`` (accepts both ``slug`` and
     ``seed:slug`` forms).
  3. Drop entries with ``discoverable=False`` unless the user override
     forces ``True``.
  4. Drop entries the user explicitly muted.

These tests pin the rules without booting the FastAPI app — the
service layer is the right unit under test.
"""

from __future__ import annotations

from backend_app.contracts import (
    InstallMcpServerRequest,
    McpAuthMode,
)
from backend_app.mcp_catalog import CatalogEntry
from backend_app.service import McpRegistryService


_ORG = "org_acme"
_USER = "user_sarah"


class TestSuggestibleConnectors:
    def _service(self) -> McpRegistryService:
        return McpRegistryService()

    def test_returns_full_catalog_when_no_installs_no_paused_no_overrides(self) -> None:
        # The default catalog ships ~13 verified-by-default entries; we
        # only assert that suggestions is non-empty and excludes nothing
        # we didn't ask to exclude. Counting exact length would couple
        # this test to vendor list edits.
        service = self._service()
        response = service.list_suggestible_connectors(org_id=_ORG, user_id=_USER)
        assert len(response.entries) > 0
        slugs = {entry.slug for entry in response.entries}
        # Picks at least the well-known verified vendors.
        assert {"linear", "notion", "asana"}.issubset(slugs)

    def test_drops_installed_servers(self) -> None:
        service = self._service()
        # Install Linear so the user already knows about it.
        service.install_from_catalog(
            InstallMcpServerRequest(org_id=_ORG, user_id=_USER, slug="linear")
        )
        response = service.list_suggestible_connectors(org_id=_ORG, user_id=_USER)
        slugs = {entry.slug for entry in response.entries}
        assert "linear" not in slugs

    def test_drops_paused_slugs_via_bare_slug(self) -> None:
        service = self._service()
        response = service.list_suggestible_connectors(
            org_id=_ORG, user_id=_USER, exclude_paused=("notion",)
        )
        slugs = {entry.slug for entry in response.entries}
        assert "notion" not in slugs

    def test_drops_paused_slugs_via_seed_prefix(self) -> None:
        # Conversation column stores ``seed:<slug>`` form; the
        # endpoint must accept that as-is so callers don't have to
        # translate.
        service = self._service()
        response = service.list_suggestible_connectors(
            org_id=_ORG, user_id=_USER, exclude_paused=("seed:linear",)
        )
        slugs = {entry.slug for entry in response.entries}
        assert "linear" not in slugs

    def test_user_override_false_mutes_an_otherwise_suggestable_entry(self) -> None:
        service = self._service()
        response = service.list_suggestible_connectors(
            org_id=_ORG, user_id=_USER, user_overrides={"linear": False}
        )
        slugs = {entry.slug for entry in response.entries}
        assert "linear" not in slugs

    def test_user_override_true_unmutes_a_discoverable_false_entry(self) -> None:
        # Build a small fake catalog where one entry is shipped with
        # discoverable=False; it must be hidden by default and shown
        # when the user opts in.
        service = self._service()
        custom_entry = CatalogEntry(
            slug="hidden-vendor",
            display_name="Hidden Vendor",
            url="https://hidden.example/mcp",
            description="Stealth integration.",
            auth_mode=McpAuthMode.OAUTH2,
            discoverable=False,
        )
        # Patch the catalog locally for this test only.
        from backend_app import service as service_module

        original = service_module.DEFAULT_CATALOG
        service_module.DEFAULT_CATALOG = original + (custom_entry,)
        try:
            without_override = service.list_suggestible_connectors(
                org_id=_ORG, user_id=_USER
            )
            assert "hidden-vendor" not in {
                entry.slug for entry in without_override.entries
            }

            with_override = service.list_suggestible_connectors(
                org_id=_ORG,
                user_id=_USER,
                user_overrides={"hidden-vendor": True},
            )
            assert "hidden-vendor" in {entry.slug for entry in with_override.entries}
        finally:
            service_module.DEFAULT_CATALOG = original

    def test_per_user_isolation(self) -> None:
        service = self._service()
        # User A installs Linear — that should NOT remove Linear from
        # User B's suggestions.
        service.install_from_catalog(
            InstallMcpServerRequest(org_id=_ORG, user_id="user_a", slug="linear")
        )
        response = service.list_suggestible_connectors(org_id=_ORG, user_id="user_b")
        slugs = {entry.slug for entry in response.entries}
        assert "linear" in slugs

    def test_combined_filters(self) -> None:
        # Install one, pause another, mute a third — none should appear
        # in the suggestions.
        service = self._service()
        service.install_from_catalog(
            InstallMcpServerRequest(org_id=_ORG, user_id=_USER, slug="linear")
        )
        response = service.list_suggestible_connectors(
            org_id=_ORG,
            user_id=_USER,
            exclude_paused=("seed:notion",),
            user_overrides={"asana": False},
        )
        slugs = {entry.slug for entry in response.entries}
        assert "linear" not in slugs
        assert "notion" not in slugs
        assert "asana" not in slugs
