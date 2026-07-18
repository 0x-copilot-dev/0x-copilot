"""AC9 desktop connector profile catalog — the slug↔server reconciliation overlay.

`desktop_profiles.yaml` is the single source of truth for the desktop overlay.
This module loads it into validated :class:`DesktopConnectorProfile` records and
**reconciles** each profile against two existing sources of truth:

* the marketing catalog (`connectors/catalog.yaml`) — a profile's
  ``connector_slug`` must be a real marketing slug, so a profile can never
  invent a card the product doesn't advertise; and
* the MCP server seeds (`mcp_catalog.DEFAULT_CATALOG`) — a profile either
  reuses an existing ``seed:<slug>`` server id (Atlassian) or carries its own
  profile-owned seed definition (Gmail/Drive/Outlook) with a verified endpoint.

The loader fails closed on any of: duplicate profile/slug/server id, unknown
marketing slug (orphan card), non-HTTPS endpoint, a write/draft tool missing
risk or per-call approval metadata, or a ``preview`` profile without a preview
gate. :meth:`DesktopProfileCatalog.reconcile` returns the resolved rows and is
the only way a caller learns which slugs are installable.

No tokens or client secrets appear here; those live only in the backend
``TokenVault``.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Iterable, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from backend_app.connectors.service import ConnectorCatalogEntry, load_catalog
from backend_app.mcp_catalog import DEFAULT_CATALOG


class ProfileCatalogError(ValueError):
    """Raised when the desktop profile catalog fails validation/reconciliation."""


class ConnectorReleaseStage(StrEnum):
    STABLE = "stable"
    PREVIEW = "preview"


class ConnectorAvailability(StrEnum):
    AVAILABLE = "available"
    PREVIEW = "preview"
    ADMIN_SETUP_REQUIRED = "admin_setup_required"
    TENANT_DISABLED = "tenant_disabled"
    UNSUPPORTED_BY_POLICY = "unsupported_by_policy"
    TOOL_CONTRACT_MISMATCH = "tool_contract_mismatch"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"


class _ProfileContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderPermission(_ProfileContract):
    identifier: str
    kind: Literal["oauth_scope", "admin_permission", "provider_policy"]
    required_for: Literal["read", "draft", "write"]
    admin_consent_required: bool = False


class ConnectorToolPolicy(_ProfileContract):
    tool_name: str
    product_scope: Literal["read", "draft", "write"]
    risk: Literal["low", "medium", "high", "critical"]
    approval: Literal["session", "per_call", "disabled"]

    @model_validator(mode="after")
    def _mutating_tools_require_per_call_approval(self) -> "ConnectorToolPolicy":
        # A tool that can change provider data must be gated behind explicit
        # per-call approval; a session-scoped grant is not enough.
        if self.product_scope in {"draft", "write"} and self.approval != "per_call":
            raise ProfileCatalogError(
                f"tool {self.tool_name!r} has product_scope={self.product_scope} "
                "but does not require per_call approval"
            )
        return self


class DesktopConnectorProfile(_ProfileContract):
    profile_id: str
    connector_slug: str
    server_id: str
    display_group: str
    endpoint_template: str
    transport: Literal["http"] = "http"
    release_stage: ConnectorReleaseStage
    requires_preview_gate: bool = False
    verified_at: date
    requires_pre_registered_client: bool = False
    requires_admin_setup: bool = False
    reuses_existing_seed: bool = False
    reference_urls: tuple[str, ...] = ()
    callback_modes: tuple[Literal["loopback_pkce", "deep_link_pkce"], ...]
    permissions: tuple[ProviderPermission, ...] = ()
    tools: tuple[ConnectorToolPolicy, ...] = ()
    unsupported_capabilities: tuple[str, ...] = ()

    @field_validator("endpoint_template")
    @classmethod
    def _validate_https_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "https":
            raise ProfileCatalogError(f"endpoint_template must be https, got {value!r}")
        if not parsed.hostname:
            raise ProfileCatalogError(f"endpoint_template must have a host: {value!r}")
        return value

    @field_validator("callback_modes")
    @classmethod
    def _require_a_callback_mode(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ProfileCatalogError("at least one callback mode is required")
        return value

    @model_validator(mode="after")
    def _preview_requires_gate(self) -> "DesktopConnectorProfile":
        if (
            self.release_stage is ConnectorReleaseStage.PREVIEW
            and not self.requires_preview_gate
        ):
            raise ProfileCatalogError(
                f"preview profile {self.profile_id!r} must set requires_preview_gate"
            )
        return self

    @property
    def has_tenant_template(self) -> bool:
        """True when the endpoint carries an unresolved ``{placeholder}``."""

        return "{" in self.endpoint_template

    def default_availability(self, *, preview_enabled: bool) -> ConnectorAvailability:
        """Availability before any live probe — the stable, honest default.

        Preview profiles stay unavailable-as-preview unless the deployment
        turned preview connectors on; admin-setup profiles report that; a
        stable profile with a concrete endpoint is available.
        """

        if self.release_stage is ConnectorReleaseStage.PREVIEW and not preview_enabled:
            return ConnectorAvailability.PREVIEW
        if self.requires_admin_setup or self.has_tenant_template:
            return ConnectorAvailability.ADMIN_SETUP_REQUIRED
        return ConnectorAvailability.AVAILABLE


class ResolvedConnectorProfile(_ProfileContract):
    """A profile reconciled to its marketing card + MCP server seed."""

    profile: DesktopConnectorProfile
    display_name: str
    description: str
    availability: ConnectorAvailability


class DesktopProfileCatalog:
    """Loads + validates + reconciles the desktop profile overlay."""

    def __init__(self, profiles: tuple[DesktopConnectorProfile, ...]) -> None:
        self._profiles = profiles
        self._assert_unique()

    @property
    def profiles(self) -> tuple[DesktopConnectorProfile, ...]:
        return self._profiles

    @classmethod
    def load(cls, path: Path | None = None) -> "DesktopProfileCatalog":
        """Load the overlay from YAML (defaults to the package-local file)."""

        resolved = path or Path(__file__).resolve().parent / "desktop_profiles.yaml"
        with resolved.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        rows = raw.get("profiles") or []
        try:
            profiles = tuple(
                DesktopConnectorProfile.model_validate(row) for row in rows
            )
        except ProfileCatalogError:
            raise
        except Exception as exc:  # pydantic ValidationError → typed error
            raise ProfileCatalogError(f"invalid desktop profile: {exc}") from exc
        return cls(profiles)

    def get(self, slug: str) -> DesktopConnectorProfile:
        """Return the profile for a marketing slug or raise."""

        for profile in self._profiles:
            if profile.connector_slug == slug:
                return profile
        raise ProfileCatalogError(f"no desktop profile for slug {slug!r}")

    def reconcile(
        self,
        *,
        marketing: Iterable[ConnectorCatalogEntry] | None = None,
        preview_enabled: bool = False,
    ) -> tuple[ResolvedConnectorProfile, ...]:
        """Join every profile to its marketing card + installable MCP server.

        Fails closed if any profile references a slug the marketing catalog
        does not advertise (an orphan card), or a server id that is neither an
        existing ``seed:*`` nor a profile-owned seed definition. Returns
        resolved rows sorted by display group + display name.
        """

        marketing_by_slug = {
            entry.slug: entry
            for entry in (marketing if marketing is not None else load_catalog())
        }
        seed_ids = {entry.server_id for entry in DEFAULT_CATALOG}

        resolved: list[ResolvedConnectorProfile] = []
        for profile in self._profiles:
            card = marketing_by_slug.get(profile.connector_slug)
            if card is None:
                raise ProfileCatalogError(
                    f"profile {profile.profile_id!r} references unknown marketing "
                    f"slug {profile.connector_slug!r} — orphan card"
                )
            self._assert_installable_server(profile, seed_ids)
            resolved.append(
                ResolvedConnectorProfile(
                    profile=profile,
                    display_name=card.display_name,
                    description=card.description,
                    availability=profile.default_availability(
                        preview_enabled=preview_enabled
                    ),
                )
            )
        resolved.sort(key=lambda row: (row.profile.display_group, row.display_name))
        return tuple(resolved)

    @staticmethod
    def _assert_installable_server(
        profile: DesktopConnectorProfile, seed_ids: set[str]
    ) -> None:
        """A card cannot appear without an installable MCP server behind it."""

        if profile.reuses_existing_seed:
            if profile.server_id not in seed_ids:
                raise ProfileCatalogError(
                    f"profile {profile.profile_id!r} claims to reuse seed "
                    f"{profile.server_id!r} but no such seed exists"
                )
            return
        # A profile-owned seed must carry its own concrete auth/endpoint. It
        # must also not collide with an existing seed id.
        if profile.server_id in seed_ids:
            raise ProfileCatalogError(
                f"profile {profile.profile_id!r} server_id {profile.server_id!r} "
                "collides with an existing seed; set reuses_existing_seed"
            )
        if not profile.requires_pre_registered_client:
            raise ProfileCatalogError(
                f"profile-owned seed {profile.profile_id!r} must configure a "
                "pre-registered OAuth client"
            )

    def _assert_unique(self) -> None:
        self._reject_duplicates(
            (profile.profile_id for profile in self._profiles), "profile_id"
        )
        self._reject_duplicates(
            (profile.connector_slug for profile in self._profiles), "connector_slug"
        )
        self._reject_duplicates(
            (profile.server_id for profile in self._profiles), "server_id"
        )

    @staticmethod
    def _reject_duplicates(values: Iterable[str], label: str) -> None:
        seen: set[str] = set()
        for value in values:
            if value in seen:
                raise ProfileCatalogError(f"duplicate {label}: {value!r}")
            seen.add(value)


__all__ = [
    "ConnectorAvailability",
    "ConnectorReleaseStage",
    "ConnectorToolPolicy",
    "DesktopConnectorProfile",
    "DesktopProfileCatalog",
    "ProfileCatalogError",
    "ProviderPermission",
    "ResolvedConnectorProfile",
]
