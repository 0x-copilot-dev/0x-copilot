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
import ipaddress
from typing import Literal

from pydantic import Field, model_validator

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
    SANDBOX_ISOLATION_UNVERIFIED = "sandbox_isolation_unverified"
    SANDBOX_SNAPSHOT_REQUIRED = "sandbox_snapshot_required"
    SANDBOX_MANIFEST_MISMATCH = "sandbox_manifest_mismatch"
    SANDBOX_EXECUTION_INDETERMINATE = "sandbox_execution_indeterminate"
    SANDBOX_LIFECYCLE_CONFLICT = "sandbox_lifecycle_conflict"


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
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
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

    @model_validator(mode="after")
    def _exact_policy(self) -> "SandboxEgressPolicy":
        """Reject policy shapes a provider could accidentally broaden.

        The launch posture is deny-all.  Allowlists are retained for a future
        provider that can prove enforcement, but are intentionally limited to
        exact host names: no wildcards, schemes, ports, paths, or raw IPs.
        """

        if self.mode == "deny_all":
            if self.destinations:
                raise ValueError("deny_all egress may not include destinations")
            return self
        if not self.destinations:
            raise ValueError("allowlist egress requires at least one destination")
        for destination in self.destinations:
            try:
                ipaddress.ip_address(destination)
            except ValueError:
                is_ip_address = False
            else:
                is_ip_address = True
            if (
                not destination
                or len(destination) > 253
                or destination != destination.lower()
                or destination.startswith(("http://", "https://", "."))
                or any(character in destination for character in "/*:@?#[]\\")
                or "." not in destination
                or any(
                    not (label.isalnum() or "-" in label)
                    for label in destination.split(".")
                )
                or any(
                    label.startswith("-") or label.endswith("-")
                    for label in destination.split(".")
                )
                or is_ip_address
            ):
                raise ValueError("egress destinations must be exact DNS host names")
        if len(set(self.destinations)) != len(self.destinations):
            raise ValueError("egress destinations must be unique")
        return self

    def is_deny_all(self) -> bool:
        """Whether this is the launch-safe no-egress policy."""

        return self.mode == "deny_all" and not self.destinations


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

    @model_validator(mode="after")
    def _content_ref_matches_declared_bytes(self) -> "WorkspaceTransferEntry":
        if (
            self.payload_ref.sha256 != self.sha256
            or self.payload_ref.size_bytes != self.size_bytes
        ):
            raise ValueError("snapshot content reference must match declared bytes")
        return self


class SandboxSnapshot(RuntimeContract):
    """Provider-safe immutable snapshot envelope.

    This is deliberately distinct from C3's private workspace materialisation
    record.  It contains virtual sandbox paths plus content-addressed artifact
    references only; it must never contain a host path, grant, broker handle,
    root identity, or credential.
    """

    format_version: Literal[1] = 1
    snapshot_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
    entries: tuple[WorkspaceTransferEntry, ...] = ()
    total_bytes: int = Field(ge=0)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _entries_are_immutable_and_bounded(self) -> "SandboxSnapshot":
        if sum(entry.size_bytes for entry in self.entries) != self.total_bytes:
            raise ValueError("sandbox snapshot byte total does not match entries")
        paths = [entry.path for entry in self.entries]
        if len(paths) != len(set(paths)):
            raise ValueError("sandbox snapshot paths must be unique")
        return self


class WorkspaceTransferManifest(RuntimeContract):
    """C3-private materialisation record, never passed to a sandbox provider.

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
    """One canonical, reviewable change from an immutable sandbox snapshot.

    A patch is not a sequence of imperative filesystem commands.  Each entry
    describes one exact before/after fact, so C1 can import it into an overlay
    and C3 can later stage it without ever granting the sandbox host authority.
    """

    operation: Literal["create", "replace", "delete", "move", "mkdir"]
    path: str = Field(min_length=1)
    source_path: str | None = None
    baseline_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    baseline_identity: str | None = Field(default=None, min_length=1, max_length=512)
    result_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    result_size_bytes: int | None = Field(default=None, ge=0)
    result_ref: ArtifactRef | None = None

    @model_validator(mode="after")
    def _operation_has_complete_evidence(self) -> "WorkspacePatchEntry":
        has_any_result = any(
            value is not None
            for value in (self.result_digest, self.result_size_bytes, self.result_ref)
        )
        has_result = (
            self.result_digest is not None
            and self.result_size_bytes is not None
            and self.result_ref is not None
        )
        if self.operation in {"create", "replace"}:
            if not has_result:
                raise ValueError(
                    "create and replace entries require verified result bytes"
                )
            if (
                self.result_ref is None
                or self.result_ref.sha256 != self.result_digest
                or self.result_ref.size_bytes != self.result_size_bytes
            ):
                raise ValueError("patch result reference must match declared bytes")
            if self.operation == "replace" and self.baseline_digest is None:
                raise ValueError("replace entries require a baseline digest")
        elif self.operation == "delete":
            if (
                self.baseline_digest is None
                or has_any_result
                or self.source_path is not None
            ):
                raise ValueError("delete entries require only a baseline digest")
        elif self.operation == "move":
            if (
                self.source_path is None
                or self.baseline_digest is None
                or has_any_result
            ):
                raise ValueError(
                    "move entries require a source path and baseline digest"
                )
        elif self.operation == "mkdir" and (
            self.source_path is not None
            or self.baseline_digest is not None
            or has_any_result
        ):
            raise ValueError("mkdir entries may not carry file evidence")
        return self


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


# D3's public name.  Keep the original class name during the migration so
# existing AC7 callers do not mistake a schema rename for a different patch.
SandboxPatchManifest = WorkspacePatchManifest


class SandboxPatchImportRequest(RuntimeContract):
    """Typed handoff from D3 to C3's overlay-only patch import port.

    This is deliberately not a workspace-commit request.  A complete patch is
    imported into C1's overlay and later goes through A4/A5 review and C3's
    native executor; the sandbox never receives any broker or Electron handle.
    """

    run_id: str = Field(min_length=1, max_length=255)
    operation_id: str = Field(min_length=1, max_length=255)
    patch: WorkspacePatchManifest

    @model_validator(mode="after")
    def _only_complete_patch_can_cross_boundary(self) -> "SandboxPatchImportRequest":
        if not self.patch.complete:
            raise ValueError("an incomplete sandbox patch cannot be imported")
        return self


class SandboxCreateRequest(RuntimeContract):
    """Immutable execution envelope the user approves. The model cannot mutate
    provider/region/egress/secret/limits after approval."""

    run_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1, max_length=255)
    snapshot: SandboxSnapshot
    egress: SandboxEgressPolicy = SandboxEgressPolicy()
    secret_refs: tuple[SandboxSecretLeaseRef, ...] = ()
    limit_profile: str = Field(min_length=1)
    approval_id: str = Field(min_length=1)
    owner_tag: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)


class SandboxIsolationAttestation(RuntimeContract):
    """Provider proof required before an untrusted command may launch.

    A request describes desired egress; this object describes controls the
    provider has actually verified.  Any missing control makes the provider
    unavailable rather than a best-effort isolation boundary.
    """

    provider: SandboxProviderId
    isolation: Literal["container", "microvm", "process"]
    process_isolated: bool
    filesystem_fresh: bool
    teardown_guaranteed: bool
    host_credentials_absent: bool
    cpu_quota_enforced: bool
    memory_quota_enforced: bool
    wall_clock_quota_enforced: bool
    process_quota_enforced: bool
    file_quota_enforced: bool
    egress_mode: Literal["deny_all", "allowlist"]
    attestation_ref: str = Field(min_length=1, max_length=2048)

    def satisfies(self, policy: SandboxEgressPolicy) -> bool:
        """Return whether every D3 launch invariant is proven effective."""

        return (
            self.isolation in {"container", "microvm"}
            and self.process_isolated
            and self.filesystem_fresh
            and self.teardown_guaranteed
            and self.host_credentials_absent
            and self.cpu_quota_enforced
            and self.memory_quota_enforced
            and self.wall_clock_quota_enforced
            and self.process_quota_enforced
            and self.file_quota_enforced
            and self.egress_mode == policy.mode
        )


class SandboxLifecycleState(StrEnum):
    """Durable, replay-safe states of one sandbox operation."""

    REQUESTED = "requested"
    PROVISIONED = "provisioned"
    UPLOADING = "uploading"
    RUNNING = "running"
    COLLECTING = "collecting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INDETERMINATE = "indeterminate"
    CLEANUP_PENDING = "cleanup_pending"
    CLEANED = "cleaned"


class SandboxLifecycleRecord(RuntimeContract):
    """Credential-free durable fact record for one idempotent operation.

    The record deliberately stores no command, provider client, token, host
    path, grant, or output bytes.  ``execution_started`` is the no-blind-retry
    boundary: after it becomes true a worker may reconcile or clean up, but it
    must not issue another execution call based on the same request.
    """

    operation_id: str = Field(min_length=1, max_length=255)
    run_id: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=1, max_length=255)
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: SandboxLifecycleState = SandboxLifecycleState.REQUESTED
    execution_started: bool = False
    provider_session_ref: str | None = Field(
        default=None,
        max_length=2048,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    cleanup_attempts: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _state_has_no_impossible_execution_fact(self) -> "SandboxLifecycleRecord":
        if self.execution_started and self.state is SandboxLifecycleState.REQUESTED:
            raise ValueError("started sandbox execution cannot remain requested")
        if (
            self.state
            in {
                SandboxLifecycleState.RUNNING,
                SandboxLifecycleState.COLLECTING,
                SandboxLifecycleState.INDETERMINATE,
            }
            and not self.execution_started
        ):
            raise ValueError(
                "active or indeterminate sandbox execution must be marked started"
            )
        return self

    def transition(
        self,
        *,
        state: SandboxLifecycleState,
        execution_started: bool | None = None,
        provider_session_ref: str | None = None,
        cleanup_attempts: int | None = None,
    ) -> "SandboxLifecycleRecord":
        """Return an immutable record after a monotonic transition."""

        return self.model_copy(
            update={
                "state": state,
                "execution_started": (
                    self.execution_started
                    if execution_started is None
                    else execution_started
                ),
                "provider_session_ref": (
                    self.provider_session_ref
                    if provider_session_ref is None
                    else provider_session_ref
                ),
                "cleanup_attempts": (
                    self.cleanup_attempts
                    if cleanup_attempts is None
                    else cleanup_attempts
                ),
                "updated_at": _utcnow(),
            }
        )


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


class SandboxDeliverable(RuntimeContract):
    """An explicit sandbox file requested as an exact-byte artifact.

    The model never receives an unrestricted ``download everything`` primitive.
    Deliverables are part of the approved sandbox operation and are only read
    from the virtual ``/workspace`` root by the runtime adapter.
    """

    path: str = Field(min_length=1, max_length=1024)
    media_type: str = Field(min_length=1, max_length=255)
    suggested_filename: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=240)


class SandboxArtifactPublication(RuntimeContract):
    """Trusted metadata for one exact-byte artifact published by D3."""

    run_id: str = Field(min_length=1, max_length=255)
    operation_id: str = Field(min_length=1, max_length=255)
    source_path: str = Field(min_length=1, max_length=1024)
    media_type: str = Field(min_length=1, max_length=255)
    suggested_filename: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=240)
    # The stream adapter calculates these values while the publisher consumes
    # the bytes. They are optional at call start and verified against the
    # returned artifact ref before a result becomes observable.
    content_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    byte_size: int | None = Field(default=None, ge=0)
    idempotency_key: str = Field(min_length=1, max_length=255)


class SandboxPublishedArtifact(RuntimeContract):
    """A completed artifact result retaining sandbox operation provenance."""

    source_path: str = Field(min_length=1, max_length=1024)
    media_type: str = Field(min_length=1, max_length=255)
    suggested_filename: str = Field(min_length=1, max_length=255)
    artifact_ref: ArtifactRef


class SandboxUsageAttribution(RuntimeContract):
    """Provider execution usage attributed exactly once to an operation."""

    operation_id: str = Field(min_length=1, max_length=255)
    run_id: str = Field(min_length=1, max_length=255)
    duration_ms: int = Field(ge=0)
    commands: int = Field(ge=0)
    uploaded_bytes: int = Field(ge=0)
    downloaded_bytes: int = Field(ge=0)
    provider_cost_microunits: int | None = Field(default=None, ge=0)


class SandboxRunRequest(RuntimeContract):
    """Approved, immutable input to D3's lifecycle coordinator."""

    create_request: SandboxCreateRequest
    command: str = Field(min_length=1, max_length=64 * 1024)
    deliverables: tuple[SandboxDeliverable, ...] = ()
    collect_patch: bool = False
    # The trusted policy layer supplies concrete secret values that must be
    # scrubbed from bounded output. They are transient and never written into
    # the lifecycle store or emitted events.
    redaction_terms: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _run_has_unique_workspace_paths(self) -> "SandboxRunRequest":
        paths = [item.path for item in self.deliverables]
        if len(paths) != len(set(paths)):
            raise ValueError("sandbox deliverable paths must be unique")
        if any(not term for term in self.redaction_terms):
            raise ValueError("sandbox redaction terms must be non-empty")
        return self


class SandboxRunResult(RuntimeContract):
    """Redaction-safe terminal projection returned from the coordinator."""

    run_id: str = Field(min_length=1, max_length=255)
    operation_id: str = Field(min_length=1, max_length=255)
    state: SandboxLifecycleState
    stdout: str = ""
    stderr: str = ""
    output_truncated: bool = False
    exit_code: int | None = None
    duration_ms: int = Field(default=0, ge=0)
    artifacts: tuple[SandboxPublishedArtifact, ...] = ()
    patch: WorkspacePatchManifest | None = None
