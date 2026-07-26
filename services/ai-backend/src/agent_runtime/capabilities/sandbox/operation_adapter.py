"""Operation-Gateway adapter for immutable remote sandbox execution.

The adapter is intentionally one layer above provider lifecycle code.  A model
tool creates a canonical :class:`OperationRequest`; this adapter validates its
reference-only snapshot arguments and sends one deterministic launch request to
an injected gateway port.  It has no provider-lifecycle dependency and cannot
open a provider session, a local file, or a live workspace mount.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from pydantic import Field, field_validator, model_validator

from agent_runtime.capabilities.operations.context import OperationContext
from agent_runtime.capabilities.operations.contracts import (
    OperationRawResult,
    ProposedEffect,
)
from agent_runtime.capabilities.operations.descriptors import OperationDescriptorEntry
from agent_runtime.capabilities.sandbox.snapshot import (
    SandboxSnapshotBuilder,
    SandboxSnapshotFileStorePort,
    SandboxSnapshotLimits,
    SandboxSnapshotManifest,
    SandboxSnapshotPlan,
)
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes, sha256_hex
from agent_runtime.surfaces_v2.entities import OperationDescriptor, OperationRequest
from agent_runtime.surfaces_v2.ledger_ids import ArtifactContentRefCodec
from agent_runtime.surfaces_v2.ledger_models import (
    EffectClass,
    EffectExecutorKind,
    OperationResultKind,
)

SANDBOX_CAPABILITY = "sandbox"
SANDBOX_EXECUTE_OPERATION = "run_in_sandbox"


def _logical_ref(value: str, *, label: str, prefix: str | None = None) -> str:
    lowered = value.lower() if isinstance(value, str) else ""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or value != value.strip()
        or value.startswith(("/", "~", "\\"))
        or lowered.startswith(("file://", "filesystem://"))
        or (len(value) >= 3 and value[1:3] in {":/", ":\\"})
        or any(part in {".", ".."} for part in value.split("/"))
        or (prefix is not None and not value.startswith(prefix))
    ):
        raise ValueError(f"{label} must be a logical reference")
    return value


class SandboxOperationAvailability(RuntimeContract):
    """A truthful non-provisioning availability response for the model tool."""

    available: bool
    reason: str | None = Field(default=None, min_length=1, max_length=96)

    @field_validator("reason")
    @classmethod
    def _reason_is_safe_code(cls, value: str | None) -> str | None:
        if value is not None and not all(
            character.islower() or character.isdigit() or character in "._-"
            for character in value
        ):
            raise ValueError("sandbox availability reason must be a bounded code")
        return value

    @model_validator(mode="after")
    def _reason_matches_state(self) -> "SandboxOperationAvailability":
        if self.available and self.reason is not None:
            raise ValueError("available sandbox operations cannot include a reason")
        if not self.available and self.reason is None:
            raise ValueError("unavailable sandbox operations require a reason")
        return self


class SandboxOperationArguments(RuntimeContract):
    """Canonical operation arguments; no provider/session/local-path fields."""

    command: str = Field(min_length=1, max_length=64 * 1024)
    snapshot: SandboxSnapshotPlan


class SandboxRunArtifact(RuntimeContract):
    """A completed sandbox artifact reference with no byte or local-path leak."""

    artifact_ref: str = Field(min_length=1, max_length=2048)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1, max_length=255)
    suggested_filename: str = Field(min_length=1, max_length=255)

    @field_validator("artifact_ref")
    @classmethod
    def _immutable_artifact_ref(cls, value: str) -> str:
        _logical_ref(value, label="sandbox artifact_ref", prefix="artifact://")
        try:
            ArtifactContentRefCodec.parse(value)
        except Exception as exc:
            raise ValueError(
                "sandbox artifact_ref must be an immutable artifact revision"
            ) from exc
        return value


class SandboxPatchManifestRef(RuntimeContract):
    """An immutable, review-required patch handoff for later C1/UI apply.

    Its presence records a complete sandbox diff, but grants no overlay or
    host-workspace write authority.  An explicit user-approved apply operation
    outside this gateway must consume this artifact-backed contract.
    """

    patch_ref: str = Field(min_length=1, max_length=2048)
    baseline_snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    complete: bool = True

    @field_validator("patch_ref")
    @classmethod
    def _immutable_patch_ref(cls, value: str) -> str:
        _logical_ref(value, label="sandbox patch_ref", prefix="artifact://")
        try:
            ArtifactContentRefCodec.parse(value)
        except Exception as exc:
            raise ValueError(
                "sandbox patch_ref must be an immutable artifact revision"
            ) from exc
        return value

    @model_validator(mode="after")
    def _complete_patch_only(self) -> "SandboxPatchManifestRef":
        if not self.complete:
            raise ValueError("incomplete sandbox patches cannot enter the gateway")
        return self


class SandboxOperationLaunch(RuntimeContract):
    """Exactly one canonical, deny-all execution request for the D3 lifecycle seam."""

    run_id: str = Field(min_length=1, max_length=255)
    operation_id: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(pattern=r"^sandbox:[0-9a-f]{64}$")
    command: str = Field(min_length=1, max_length=64 * 1024)
    snapshot: SandboxSnapshotManifest
    egress_mode: str = Field(default="deny_all", frozen=True)
    secret_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _initial_launch_is_deny_all_and_secret_free(self) -> "SandboxOperationLaunch":
        if self.egress_mode != "deny_all" or self.secret_refs:
            raise ValueError("initial sandbox execution permits no egress or secrets")
        return self


class SandboxOperationRunResult(RuntimeContract):
    """Stored, redaction-safe outcome returned by the lifecycle gateway.

    ``patch`` is a pending artifact-backed proposal.  Command completion never
    assigns an activity ref or applies that proposal to an overlay/workspace.
    """

    run_id: str = Field(min_length=1, max_length=255)
    operation_id: str = Field(min_length=1, max_length=255)
    result_ref: str = Field(min_length=1, max_length=2048)
    safe_summary: str = Field(min_length=1, max_length=512)
    activity_ref: str | None = Field(default=None, max_length=2048)
    artifacts: tuple[SandboxRunArtifact, ...] = ()
    patch: SandboxPatchManifestRef | None = None

    @field_validator("result_ref")
    @classmethod
    def _stored_result_ref(cls, value: str) -> str:
        _logical_ref(value, label="sandbox result reference", prefix="artifact://")
        try:
            ArtifactContentRefCodec.parse(value)
        except Exception as exc:
            raise ValueError(
                "sandbox result reference must be an immutable artifact revision"
            ) from exc
        return value

    @field_validator("activity_ref")
    @classmethod
    def _stored_activity_ref(cls, value: str | None) -> str | None:
        if value is not None:
            return _logical_ref(value, label="sandbox activity reference")
        return value


@runtime_checkable
class SandboxOperationRunnerPort(Protocol):
    """Worker-owned lifecycle gateway; provider sessions stay behind this port."""

    @property
    def availability(self) -> SandboxOperationAvailability:
        """Report whether the provider can currently verify the required posture."""
        ...

    async def run(
        self, *, request: SandboxOperationLaunch
    ) -> SandboxOperationRunResult:
        """Persist and execute one immutable launch without exposing provider handles."""
        ...


def sandbox_operation_descriptor() -> OperationDescriptorEntry:
    """Return the exact D3 descriptor for composition-root registration."""

    return OperationDescriptorEntry(
        descriptor=OperationDescriptor(
            capability=SANDBOX_CAPABILITY,
            op=SANDBOX_EXECUTE_OPERATION,
            executor=EffectExecutorKind.SANDBOX,
            effect_class=EffectClass.NONE,
            result_kind=OperationResultKind.ACTIVITY,
            supports_prepare=False,
            supports_reconcile=False,
            required_gate_kinds=(),
            max_inline_result_bytes=0,
        ),
        descriptor_version="sandbox-gateway-v1",
        display_name="sandbox command",
        timeout_ms=600_000,
    )


@dataclass
class SandboxOperationAdapter:
    """Gateway-only D3 adapter; it cannot directly create or execute a sandbox."""

    runner: SandboxOperationRunnerPort
    snapshot_store: SandboxSnapshotFileStorePort
    snapshot_limits: SandboxSnapshotLimits = field(
        default_factory=SandboxSnapshotLimits
    )
    _results: dict[str, SandboxOperationRunResult] = field(
        default_factory=dict, init=False, repr=False
    )

    @property
    def availability(self) -> SandboxOperationAvailability:
        """Expose the runner's honest provider posture to registration code."""

        return self.runner.availability

    async def execute_read(self, request: OperationRequest) -> OperationRawResult:
        """Run one descriptor-classified sandbox operation through the lifecycle port."""

        if (
            request.capability != SANDBOX_CAPABILITY
            or request.op != SANDBOX_EXECUTE_OPERATION
        ):
            raise ValueError("sandbox adapter received an unsupported operation")
        if not self.availability.available:
            raise SandboxOperationUnavailable(self.availability.reason or "unavailable")
        arguments = self._arguments(request)
        snapshot = await SandboxSnapshotBuilder.materialize(
            plan=arguments.snapshot,
            store=self.snapshot_store,
            limits=self.snapshot_limits,
        )
        result = await self.runner.run(
            request=SandboxOperationLaunch(
                run_id=request.run_id,
                operation_id=request.operation_id,
                idempotency_key=self.idempotency_key(
                    request=request, snapshot=snapshot
                ),
                command=arguments.command,
                snapshot=snapshot,
            )
        )
        if (
            result.run_id != request.run_id
            or result.operation_id != request.operation_id
        ):
            raise ValueError(
                "sandbox lifecycle result identity did not match operation"
            )
        self._results[request.operation_id] = result
        return OperationRawResult(
            result_ref=result.result_ref,
            safe_summary=result.safe_summary,
            activity_ref=result.activity_ref,
        )

    async def build_proposal(self, request: OperationRequest) -> ProposedEffect:
        """Refuse an accidental descriptor change from creating a direct effect path."""

        del request
        raise ValueError("sandbox execution never builds a direct effect proposal")

    def result_for(self, operation_id: str) -> SandboxOperationRunResult | None:
        """Return the immutable stored result for the just-completed operation."""

        return self._results.get(operation_id)

    @staticmethod
    def idempotency_key(
        *, request: OperationRequest, snapshot: SandboxSnapshotManifest
    ) -> str:
        """Bind provider retry identity to canonical operation and snapshot facts."""

        return "sandbox:" + sha256_hex(
            canonical_json_bytes(
                {
                    "operation_id": request.operation_id,
                    "run_id": request.run_id,
                    "args_digest": request.args_digest,
                    "snapshot_digest": snapshot.manifest_digest,
                }
            )
        )

    @staticmethod
    def _arguments(request: OperationRequest) -> SandboxOperationArguments:
        context = OperationContext.require()
        stored = context.arguments.get(request.canonical_args_ref)
        if stored is None:
            raise ValueError("sandbox operation arguments are unavailable")
        digest, raw = stored
        if digest != request.args_digest or sha256_hex(raw) != request.args_digest:
            raise ValueError("sandbox operation argument digest does not match")
        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("sandbox operation arguments are invalid") from exc
        if not isinstance(decoded, dict) or canonical_json_bytes(decoded) != raw:
            raise ValueError("sandbox operation arguments are not canonical")
        return SandboxOperationArguments.model_validate(decoded)


class SandboxOperationUnavailable(RuntimeError):
    """Safe stale-tool result: no command was sent because the provider is absent."""


__all__ = (
    "SANDBOX_CAPABILITY",
    "SANDBOX_EXECUTE_OPERATION",
    "SandboxOperationAdapter",
    "SandboxOperationArguments",
    "SandboxOperationAvailability",
    "SandboxOperationLaunch",
    "SandboxOperationRunResult",
    "SandboxOperationRunnerPort",
    "SandboxOperationUnavailable",
    "SandboxPatchManifestRef",
    "SandboxRunArtifact",
    "sandbox_operation_descriptor",
)
