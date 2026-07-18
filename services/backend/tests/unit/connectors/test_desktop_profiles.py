"""AC9 desktop profile catalog — validation + slug↔server reconciliation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend_app.connectors.profile_catalog import (
    ConnectorAvailability,
    DesktopConnectorProfile,
    DesktopProfileCatalog,
    ProfileCatalogError,
)
from backend_app.connectors.service import ConnectorCatalogEntry


def _base_profile(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "profile_id": "p1",
        "connector_slug": "gmail",
        "server_id": "desktop:google:gmail",
        "display_group": "Google Workspace",
        "endpoint_template": "https://gmailmcp.googleapis.com/mcp/v1",
        "transport": "http",
        "release_stage": "stable",
        "verified_at": "2026-07-18",
        "requires_pre_registered_client": True,
        "callback_modes": ["loopback_pkce"],
        "tools": [
            {
                "tool_name": "search_threads",
                "product_scope": "read",
                "risk": "low",
                "approval": "session",
            }
        ],
    }
    payload.update(overrides)
    return payload


class TestProfileValidation:
    def test_shipped_catalog_loads_and_reconciles(self) -> None:
        catalog = DesktopProfileCatalog.load()
        resolved = catalog.reconcile()

        slugs = {row.profile.connector_slug for row in resolved}
        assert {"gmail", "gdrive", "outlook", "atlassian"} <= slugs

    def test_atlassian_reuses_existing_seed_id(self) -> None:
        catalog = DesktopProfileCatalog.load()
        atlassian = catalog.get("atlassian")
        assert atlassian.server_id == "seed:atlassian"
        assert atlassian.reuses_existing_seed is True

    def test_https_endpoint_required(self) -> None:
        with pytest.raises(ValidationError):
            DesktopConnectorProfile.model_validate(
                _base_profile(endpoint_template="http://insecure.example.com/mcp")
            )

    def test_write_tool_requires_per_call_approval(self) -> None:
        with pytest.raises(ValidationError):
            DesktopConnectorProfile.model_validate(
                _base_profile(
                    tools=[
                        {
                            "tool_name": "create_file",
                            "product_scope": "write",
                            "risk": "high",
                            "approval": "session",
                        }
                    ]
                )
            )

    def test_preview_profile_requires_gate(self) -> None:
        with pytest.raises(ValidationError):
            DesktopConnectorProfile.model_validate(
                _base_profile(release_stage="preview", requires_preview_gate=False)
            )

    def test_duplicate_slug_rejected(self) -> None:
        p1 = DesktopConnectorProfile.model_validate(_base_profile())
        p2 = DesktopConnectorProfile.model_validate(
            _base_profile(profile_id="p2", server_id="desktop:other")
        )
        with pytest.raises(ProfileCatalogError):
            DesktopProfileCatalog((p1, p2))


class TestReconciliation:
    def test_unknown_marketing_slug_is_orphan(self) -> None:
        profile = DesktopConnectorProfile.model_validate(
            _base_profile(connector_slug="gmail")
        )
        catalog = DesktopProfileCatalog((profile,))
        marketing = (ConnectorCatalogEntry(slug="slack", display_name="Slack"),)
        with pytest.raises(ProfileCatalogError):
            catalog.reconcile(marketing=marketing)

    def test_profile_owned_seed_must_configure_client(self) -> None:
        profile = DesktopConnectorProfile.model_validate(
            _base_profile(
                server_id="desktop:google:gmail",
                requires_pre_registered_client=False,
            )
        )
        catalog = DesktopProfileCatalog((profile,))
        marketing = (ConnectorCatalogEntry(slug="gmail", display_name="Gmail"),)
        with pytest.raises(ProfileCatalogError):
            catalog.reconcile(marketing=marketing)

    def test_reuse_seed_must_reference_real_seed(self) -> None:
        profile = DesktopConnectorProfile.model_validate(
            _base_profile(
                connector_slug="atlassian",
                server_id="seed:does-not-exist",
                reuses_existing_seed=True,
            )
        )
        catalog = DesktopProfileCatalog((profile,))
        marketing = (ConnectorCatalogEntry(slug="atlassian", display_name="Atlassian"),)
        with pytest.raises(ProfileCatalogError):
            catalog.reconcile(marketing=marketing)

    def test_preview_availability_gated_by_deployment(self) -> None:
        catalog = DesktopProfileCatalog.load()

        default = {r.profile.connector_slug: r for r in catalog.reconcile()}
        enabled = {
            r.profile.connector_slug: r for r in catalog.reconcile(preview_enabled=True)
        }

        # Gmail is preview: hidden as preview until the deployment enables it.
        assert default["gmail"].availability is ConnectorAvailability.PREVIEW
        # Atlassian is stable: available regardless.
        assert default["atlassian"].availability is ConnectorAvailability.AVAILABLE
        # Outlook has a tenant template → admin setup even when preview enabled.
        assert (
            enabled["outlook"].availability
            is ConnectorAvailability.ADMIN_SETUP_REQUIRED
        )
        # Gmail becomes available once preview is enabled.
        assert enabled["gmail"].availability is ConnectorAvailability.AVAILABLE

    def test_no_orphan_cards_in_shipped_catalog(self) -> None:
        """Every shipped profile resolves to a marketing card + installable server."""

        catalog = DesktopProfileCatalog.load()
        resolved = catalog.reconcile()
        assert len(resolved) == len(catalog.profiles)
        for row in resolved:
            assert row.display_name
            assert row.profile.server_id
