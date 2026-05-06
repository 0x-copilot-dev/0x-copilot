"""Privacy-settings snapshot consumed by the AI runtime (PR B2 / 8.0.3f).

The backend's ``/internal/v1/policies/privacy`` endpoint composes a
workspace default + per-user override into a hydrated full shape. The
AI runtime fetches that shape once at run start and caches it on
``AgentRuntimeContext`` so the retention sweeper, memory consumer,
provider-call layer, and audit redactor can each read the same
snapshot without a second round-trip.

This module is the read-side companion to
:class:`backend_app.privacy.store.PrivacySettingsRow`. It deliberately
holds no I/O — adapters land in
:mod:`agent_runtime.api.privacy_fetcher` (out of scope here; the
snapshot is the contract).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class DataResidencyRegion(StrEnum):
    """Mirror of the backend's region enum."""

    US_EAST_1 = "us-east-1"
    EU_WEST_1 = "eu-west-1"
    AP_NORTHEAST_1 = "ap-northeast-1"


@dataclass(frozen=True)
class PrivacySettingsSnapshot:
    """Hydrated privacy settings for one (org, user) at run start.

    Field semantics mirror the backend's ``PrivacySettingsResponse``
    shape verbatim. The runtime treats every field as a *read-only*
    constraint:

    * ``training_opt_out`` is forwarded into the provider-call layer
      as a do-not-train signal on every request.
    * ``region`` is the desired data residency. ``None`` means "use
      the deployment default" — the routing layer decides.
    * ``retention_days`` is consumed by the existing C8 retention
      sweeper. ``None`` means "retain forever" (subject to the
      workspace's master TTL).
    * ``share_metadata`` opts the user in to admin-visible thread
      metadata; message content stays private regardless.
    * ``memory_enabled`` toggles cross-chat memory writes + reads.
    """

    org_id: str
    user_id: str | None
    training_opt_out: bool
    region: DataResidencyRegion | None
    retention_days: int | None
    share_metadata: bool
    memory_enabled: bool

    @classmethod
    def from_response(
        cls,
        body: Mapping[str, object],
    ) -> "PrivacySettingsSnapshot":
        """Build a snapshot from the wire-format ``PrivacySettingsResponse``.

        Unknown / malformed fields fall through to deployment defaults
        rather than raising — the AI runtime should never refuse a run
        on a privacy-fetch parse error.
        """

        org_id = str(body.get("org_id") or "")
        raw_user = body.get("user_id")
        user_id = str(raw_user) if isinstance(raw_user, str) and raw_user else None
        region_value = body.get("region")
        region: DataResidencyRegion | None
        if isinstance(region_value, str):
            try:
                region = DataResidencyRegion(region_value)
            except ValueError:
                region = None
        else:
            region = None
        retention = body.get("retention_days")
        retention_days = (
            int(retention)
            if isinstance(retention, int)
            and not isinstance(retention, bool)
            and retention > 0
            else None
        )
        return cls(
            org_id=org_id,
            user_id=user_id,
            training_opt_out=_bool(body.get("training_opt_out"), default=True),
            region=region,
            retention_days=retention_days,
            share_metadata=_bool(body.get("share_metadata"), default=True),
            memory_enabled=_bool(body.get("memory_enabled"), default=True),
        )

    @classmethod
    def deployment_default(
        cls, *, org_id: str, user_id: str | None = None
    ) -> "PrivacySettingsSnapshot":
        """Return the snapshot the runtime caches when the backend
        reports no stored row for the (org, user) scope. Mirrors the
        deployment defaults the backend hydrates on the GET path."""

        return cls(
            org_id=org_id,
            user_id=user_id,
            training_opt_out=True,
            region=None,
            retention_days=None,
            share_metadata=True,
            memory_enabled=True,
        )

    def memory_writes_allowed(self) -> bool:
        """Convenience for the memory consumer."""

        return self.memory_enabled

    def admin_visible_metadata_allowed(self) -> bool:
        """Convenience for the audit / share-metadata layer."""

        return self.share_metadata

    def provider_do_not_train(self) -> bool:
        """Convenience for the provider-call layer."""

        return self.training_opt_out


def _bool(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


__all__ = ["DataResidencyRegion", "PrivacySettingsSnapshot"]
