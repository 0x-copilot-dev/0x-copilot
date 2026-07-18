"""Provider-neutral typed contracts for remote sandbox execution (AC7).

This module is the single source of truth for the sandbox capability's IO and
domain shapes. Every value that crosses a boundary — provider adapter, policy
backend, execution service, events — is a frozen Pydantic model built on
``RuntimeContract`` so external input is coerced and validated at the edge.

Scope (AC7 FOUNDATION): the contracts here describe the create/execute/teardown
lifecycle, the workspace snapshot/patch envelope, egress policy, and secret
lease references. Full egress-policy compilation to a provider network control
and host patch-apply are deliberately *not* implemented here — they are called
out as seams in :mod:`agent_runtime.capabilities.sandbox.workspace_transfer` and
:mod:`agent_runtime.capabilities.sandbox.remote_execution_service`.

The model may never name a provider, region, image, credential, or provider
session id — those originate only from trusted deployment settings (see
:mod:`agent_runtime.capabilities.sandbox.config`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import Field

from agent_runtime.execution.contracts import RuntimeContract


def _utcnow() -> datetime:
    """Timezone-aware current time (UTC). Kept local so contracts have no clock dep."""

    return datetime.now(timezone.utc)


class SandboxProviderId(StrEnum):
    """Providers the registry can select. Exactly one ships in AC7."""

    LANGSMITH = "langsmith"


class SandboxErrorCode(StrEnum):
    """Stable, model- and API-safe error classes for the sandbox capability.

    Mirrors the ``Stable errors`` catalogue in the AC7 PRD. These strings are
    part of the product contract; never rename one in place — add a new member.
    """

    SANDBOX_DISABLED = "sandbox_disabled"
    SANDBOX_PROVIDER_UNCONFIGURED = "sandbox_provider_unconfigured"
    SANDBOX_POLICY_UNSUPPORTED = "sandbox_policy_unsupported"
    SNAPSHOT_INVALID = "snapshot_invalid"
    SNAPSHOT_QUOTA_EXCEEDED = "snapshot_quota_exceeded"
    SANDBOX_PROVISION_FAILED = "sandbox_provision_failed"
    SANDBOX_UPLOAD_FAILED = "sandbox_upload_failed"
    SANDBOX_COMMAND_TIMEOUT = "sandbox_command_timeout"
    SANDBOX_SESSION_EXPIRED = "sandbox_session_expired"
    SANDBOX_EGRESS_DENIED = "sandbox_egress_denied"
    SANDBOX_SECRET_EXPIRED = "sandbox_secret_expired"
    SANDBOX_CANCELLED = "sandbox_cancelled"
    SANDBOX_DOWNLOAD_FAILED = "sandbox_download_failed"
    SANDBOX_PATCH_INCOMPLETE = "sandbox_patch_incomplete"
    SANDBOX_CLEANUP_PENDING = "sandbox_cleanup_pending"
    SANDBOX_COMMAND_BUDGET_EXCEEDED = "sandbox_command_budget_exceeded"
    SANDBOX_PATH_NOT_ALLOWED = "sandbox_path_not_allowed"


class SandboxError(Exception):
    """Typed domain error carrying a stable code and a redaction-safe message.

    The ``message`` is safe to surface to the model and HTTP clients; it must
    never contain host absolute paths, provider credentials, URL query strings,
    or secret material. Internal detail belongs in logs, not here.
    """

    def __init__(self, code: SandboxErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code.value}: {message}")


class ArtifactRef(RuntimeContract):
    """Opaque reference to bytes stored by the artifact store (AC4).

    AC4 owns the real payload store; AC7 only holds references. This local
    contract is the seam: when AC4 lands, ``ArtifactRef`` becomes the shared
    ``PayloadRef`` type and this definition is removed. Until then the sandbox
    capability never inlines file bytes into events or contracts — it carries
    a ref.
    """

    artifact_id: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)


class SandboxEgressPolicy(RuntimeContract):
    """Immutable egress envelope. Default-deny; allowlist is exact HTTPS hosts.

    AC7 FOUNDATION validates the *shape* (deny-all default, no wildcards/raw
    IPs) but does NOT compile the policy to a provider network control — that
    provider-side enforcement is deferred (see PRD "Egress policy"). Callers
    must treat a shape-valid policy as *proposed*, not *enforced*, until the
    provider compilation lands.
    """

    mode: Literal["deny_all", "allowlist"] = "deny_all"
    destinations: tuple[str, ...] = ()


class SandboxSecretLeaseRef(RuntimeContract):
    """Reference to a provider-/deployment-side secret — never secret material.

    Lifetime is bounded (<=15 min and never beyond the session) and audience
    is an exact host set. AC7 FOUNDATION carries the reference; injecting it via
    a provider proxy is deferred to the credential-handling review (PRD
    "Short-lived secret references").
    """

    lease_id: str = Field(min_length=1)
    audience_hosts: tuple[str, ...]
    expires_at: datetime
    capability: Literal["read", "write"] = "read"


class WorkspaceTransferEntry(RuntimeContract):
    """One regular file in an upload snapshot, addressed by a normalized path."""

    path: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)
    executable: bool = False
    payload_ref: ArtifactRef


class WorkspaceTransferManifest(RuntimeContract):
    """Deterministic description of the bytes uploaded to ``/workspace``.

    The manifest hash is order-independent (see ``workspace_transfer``) so two
    hosts enumerating the same tree in different orders produce the same hash.
    """

    format_version: Literal[1] = 1
    workspace_id: str = Field(min_length=1)
    root_grant_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=_utcnow)
    entries: tuple[WorkspaceTransferEntry, ...] = ()
    total_bytes: int = Field(ge=0)
    manifest_sha256: str = Field(min_length=64, max_length=64)


class WorkspacePatchEntry(RuntimeContract):
    """One host-relative change produced by comparing ``/workspace`` to baseline."""

    operation: Literal["add", "modify", "delete"]
    path: str = Field(min_length=1)
    baseline_sha256: str | None = None
    result_sha256: str | None = None
    result_size_bytes: int | None = None
    payload_ref: ArtifactRef | None = None


class WorkspacePatchManifest(RuntimeContract):
    """Typed patch returned from a session. Applying it to the host is a SEPARATE
    broker operation (AC5) and is out of scope for AC7 FOUNDATION.

    ``complete=False`` marks a partial download; a partial patch must never be
    applied to the host.
    """

    format_version: Literal[1] = 1
    session_id: str = Field(min_length=1)
    baseline_manifest_sha256: str = Field(min_length=64, max_length=64)
    entries: tuple[WorkspacePatchEntry, ...] = ()
    complete: bool = True
    manifest_sha256: str = Field(min_length=64, max_length=64)


class SandboxCreateRequest(RuntimeContract):
    """Immutable execution envelope the user approves. The model cannot mutate
    provider/region/egress/secret/limits after approval."""

    run_id: str = Field(min_length=1)
    workspace_snapshot: WorkspaceTransferManifest
    egress: SandboxEgressPolicy = SandboxEgressPolicy()
    secret_refs: tuple[SandboxSecretLeaseRef, ...] = ()
    limit_profile: str = Field(min_length=1)
    approval_id: str = Field(min_length=1)
    owner_tag: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)


CleanupState = Literal["active", "terminating", "deleted", "cleanup_pending"]


class ManagedSandboxSession(RuntimeContract):
    """Durable, credential-free projection of one provider session.

    Persisted so a reaper can sweep leaks after worker death. Contains provider
    id and an opaque provider session ref, never a token.
    """

    session_id: str = Field(min_length=1)
    provider: SandboxProviderId
    provider_session_ref: str = Field(min_length=1)
    owner_tag: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime
    cleanup_state: CleanupState = "active"

    def with_state(self, state: CleanupState) -> ManagedSandboxSession:
        """Return a copy transitioned to ``state`` (models are frozen)."""

        return self.model_copy(update={"cleanup_state": state})

    def is_expired(self, *, now: datetime | None = None) -> bool:
        """Whether the session's TTL has elapsed."""

        return (now or _utcnow()) >= self.expires_at


class SandboxCommandResult(RuntimeContract):
    """Policy-shaped result of one ``execute`` call surfaced to the runtime."""

    output: str
    exit_code: int | None
    truncated: bool = False
    duration_ms: int = Field(ge=0)
