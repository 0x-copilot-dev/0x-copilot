"""Authoritative, bounded lifecycle-reference snapshots (PRD-E1 D9).

The existing :mod:`lifecycle_refs` module is deliberately the *pure* closed
registry and Work Ledger enumerator.  This module adds the composition layer
that a lifecycle/retention coordinator needs before it can make a destructive
decision:

* fetch one tenant-scoped, bounded run-event window from a capable store;
* fold the existing registry/enumerator and the artifact projection without
  reading a body, host path, credential, or provider response;
* report a closed coverage outcome for every registered lifecycle owner; and
* make missing inventories visible as ``unavailable`` / ``withheld`` rather
  than silently treating a partial scan as complete.

It intentionally exposes no HTTP route and performs no deletion.  A future
retention executor must call :meth:`LifecycleReferenceSnapshot.assert_complete`
and :meth:`LifecycleReferenceSnapshot.assert_no_active_legal_holds` before it
can mutate anything.

The storage-facing page protocol is intentionally narrower than the broad
``EventStorePort``.  Older/fake event stores therefore remain valid for normal
runtime work while this safety-sensitive collector refuses to claim coverage
until a backend implements the bounded query.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import Field, model_validator

from agent_runtime.artifacts.projection import ArtifactProjection
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.lifecycle_refs import (
    LifecycleGraphNode,
    LifecycleReference,
    LifecycleReferenceEnumerator,
    LifecycleReferenceGraph,
    LifecycleReferenceGraphError,
    LifecycleReferenceOwner,
    LifecycleReferenceRegistry,
    LifecycleReferenceRegistration,
    LifecycleReferenceScheme,
)
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType

_SAFE_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$"
_MAX_EVENT_WINDOW = 500
_MAX_LEGAL_HOLD_WINDOW = 200


class LifecycleReferenceCoverageState(StrEnum):
    """Whether an owner/family has an authoritative complete enumeration."""

    COVERED = "covered"
    WITHHELD = "withheld"
    UNAVAILABLE = "unavailable"


class LifecycleReferenceCoverageReason(StrEnum):
    """Closed, redacted reason vocabulary for incomplete lifecycle coverage."""

    EVENT_WINDOW_UNAVAILABLE = "event_window_unavailable"
    EVENT_WINDOW_TRUNCATED = "event_window_truncated"
    PARTIAL_EVENT_RANGE = "partial_event_range"
    EVENT_WINDOW_INVALID = "event_window_invalid"
    LEDGER_REFERENCE_REJECTED = "ledger_reference_rejected"
    ARTIFACT_FOLD_INCOMPLETE = "artifact_fold_incomplete"
    LEGAL_HOLD_INVENTORY_UNAVAILABLE = "legal_hold_inventory_unavailable"
    LEGAL_HOLD_WINDOW_TRUNCATED = "legal_hold_window_truncated"
    OWNER_NOT_MATERIALIZED = "owner_not_materialized"
    CLAIM_INVENTORY_UNAVAILABLE = "claim_inventory_unavailable"
    USAGE_INVENTORY_UNAVAILABLE = "usage_inventory_unavailable"
    AUDIT_EXPORT_INVENTORY_UNAVAILABLE = "audit_export_inventory_unavailable"


class LifecycleReferenceOwnerStrategy(StrEnum):
    """The reviewed source strategy assigned to one registry owner."""

    LEDGER = "ledger"
    ARTIFACT_FOLD = "artifact_fold"
    UNAVAILABLE = "unavailable"


class LifecycleReferenceFamily(StrEnum):
    """Lifecycle areas a later retention operation must explicitly account for."""

    RUN_CONVERSATION_MESSAGE_EVENTS = "run_conversation_message_events"
    OPERATIONS = "operations"
    ARTIFACTS_REVISIONS_BLOBS = "artifacts_revisions_blobs"
    SURFACES_VIEWS_SPECS = "surfaces_views_specs"
    STAGES_PROPOSALS = "stages_proposals"
    EFFECT_CLAIMS = "effect_claims"
    RECEIPTS = "receipts"
    WORKSPACE_OVERLAY_PREIMAGE_PREPARED_JOURNAL_RECOVERY = (
        "workspace_overlay_preimage_prepared_journal_recovery"
    )
    SANDBOX_SNAPSHOTS_PATCHES_RESOURCES = "sandbox_snapshots_patches_resources"
    BROWSER_DOWNLOADS_UPLOADS_RECEIPTS = "browser_downloads_uploads_receipts"
    USAGE_ATTRIBUTION = "usage_attribution"
    AUDIT_EXPORTS = "audit_exports"
    LEGAL_HOLDS = "legal_holds"


class LifecycleReferenceSnapshotDiagnosticCode(StrEnum):
    """Safe failure classes for a refused snapshot/precondition check."""

    RUN_NOT_FOUND_OR_NOT_AUTHORIZED = "run_not_found_or_not_authorized"
    RUN_SCOPE_UNAVAILABLE = "run_scope_unavailable"
    LEDGER_REFERENCE_REJECTED = "ledger_reference_rejected"
    SNAPSHOT_INCOMPLETE = "snapshot_incomplete"
    ACTIVE_LEGAL_HOLD = "active_legal_hold"


class LifecycleReferenceSnapshotDiagnostic(RuntimeContract):
    """Redacted snapshot diagnostic; it never carries source exception text."""

    code: LifecycleReferenceSnapshotDiagnosticCode
    owner: LifecycleReferenceOwner | None = None
    family: LifecycleReferenceFamily | None = None


class LifecycleReferenceSnapshotError(RuntimeError):
    """Fail-closed snapshot error with typed, non-sensitive diagnostics."""

    def __init__(
        self, diagnostics: tuple[LifecycleReferenceSnapshotDiagnostic, ...]
    ) -> None:
        self.diagnostics = diagnostics
        super().__init__("lifecycle reference snapshot refused")


class LifecycleReferenceConformanceError(RuntimeError):
    """The static owner/registry inventory is not safe to launch with."""


class LifecycleReferenceSnapshotScope(RuntimeContract):
    """Trusted, bounded tenant scope for one lifecycle-reference snapshot."""

    org_id: str = Field(pattern=_SAFE_IDENTIFIER_PATTERN, max_length=256)
    run_id: str = Field(pattern=_SAFE_IDENTIFIER_PATTERN, max_length=256)
    user_id: str | None = Field(
        default=None, pattern=_SAFE_IDENTIFIER_PATTERN, max_length=256
    )
    conversation_id: str | None = Field(
        default=None, pattern=_SAFE_IDENTIFIER_PATTERN, max_length=256
    )
    after_sequence: int = Field(default=0, ge=0)
    event_limit: int = Field(default=_MAX_EVENT_WINDOW, ge=1, le=_MAX_EVENT_WINDOW)
    legal_hold_limit: int = Field(
        default=_MAX_LEGAL_HOLD_WINDOW,
        ge=1,
        le=_MAX_LEGAL_HOLD_WINDOW,
    )


@dataclass(frozen=True)
class LifecycleReferenceEventWindow:
    """Internal bounded page returned by a storage adapter.

    ``events`` intentionally keeps envelope objects private to collection.  It
    is never included in a public/runtime contract, so event payload bodies
    cannot leak through a lifecycle snapshot.
    """

    events: tuple[object, ...]
    has_more: bool
    next_after_sequence: int | None


@runtime_checkable
class LifecycleReferenceEventWindowPort(Protocol):
    """Backend capability for a bounded tenant-scoped run event query."""

    async def list_lifecycle_reference_events_window(
        self,
        *,
        org_id: str,
        run_id: str,
        after_sequence: int,
        limit: int,
    ) -> LifecycleReferenceEventWindow:
        """Return at most ``limit`` events plus a truthful continuation bit."""


@runtime_checkable
class LifecycleReferenceRunScopePort(Protocol):
    """Minimal trusted run lookup needed to bind a snapshot to its tenant."""

    async def get_run(self, *, org_id: str, run_id: str) -> object | None:
        """Return only a run already scoped by the trusted organization."""


@runtime_checkable
class LifecycleReferenceLegalHoldPort(Protocol):
    """Bounded, tenant-scoped legal-hold inventory capability."""

    async def list_legal_holds(
        self,
        *,
        org_id: str,
        include_released: bool,
        limit: int,
    ) -> Sequence[object]:
        """Return at most the requested holds, ordered by the adapter."""


class LifecycleReferenceOwnerCapability(RuntimeContract):
    """One explicit source strategy for every lifecycle owner."""

    owner: LifecycleReferenceOwner
    strategy: LifecycleReferenceOwnerStrategy


class LifecycleReferenceOwnerSnapshot(RuntimeContract):
    """Safe per-owner enumeration outcome, including every registry owner."""

    owner: LifecycleReferenceOwner
    schemes: tuple[LifecycleReferenceScheme, ...]
    state: LifecycleReferenceCoverageState
    reason: LifecycleReferenceCoverageReason | None = None
    nodes: tuple[LifecycleGraphNode, ...] = ()
    artifact_revisions: tuple["LifecycleArtifactRevisionReference", ...] = ()

    @model_validator(mode="after")
    def _state_shape_is_honest(self) -> "LifecycleReferenceOwnerSnapshot":
        if self.state is LifecycleReferenceCoverageState.COVERED:
            if self.reason is not None:
                raise ValueError("covered lifecycle owner cannot carry a reason")
        elif self.reason is None:
            raise ValueError("incomplete lifecycle owner must carry a reason")
        if self.state is not LifecycleReferenceCoverageState.COVERED and (
            self.nodes or self.artifact_revisions
        ):
            raise ValueError("incomplete lifecycle owner cannot expose partial refs")
        return self


class LifecycleArtifactRevisionReference(RuntimeContract):
    """Safe logical artifact/revision/blob identity, never artifact content."""

    artifact_id: str = Field(pattern=_SAFE_IDENTIFIER_PATTERN, max_length=256)
    revision: int = Field(ge=1)
    content_ref: LifecycleReference
    blob_ref: LifecycleReference


class LifecycleReferenceRunContext(RuntimeContract):
    """Safe identity links for the conversation/message/run side of a snapshot."""

    conversation_id: str = Field(pattern=_SAFE_IDENTIFIER_PATTERN, max_length=256)
    run_id: str = Field(pattern=_SAFE_IDENTIFIER_PATTERN, max_length=256)
    user_message_ref: LifecycleReference


class LifecycleReferenceFamilySnapshot(RuntimeContract):
    """Coverage outcome for an explicit retention/deletion family."""

    family: LifecycleReferenceFamily
    owner: LifecycleReferenceOwner | None = None
    state: LifecycleReferenceCoverageState
    reason: LifecycleReferenceCoverageReason | None = None
    reference_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _family_state_is_honest(self) -> "LifecycleReferenceFamilySnapshot":
        if self.state is LifecycleReferenceCoverageState.COVERED:
            if self.reason is not None:
                raise ValueError("covered lifecycle family cannot carry a reason")
        elif self.reason is None:
            raise ValueError("incomplete lifecycle family must carry a reason")
        if self.state is not LifecycleReferenceCoverageState.COVERED and (
            self.reference_count != 0
        ):
            raise ValueError("incomplete lifecycle family cannot claim a ref count")
        return self


class LifecycleLegalHoldSnapshot(RuntimeContract):
    """Aggregate-only legal-hold state; hold/resource identifiers stay private."""

    state: LifecycleReferenceCoverageState
    reason: LifecycleReferenceCoverageReason | None = None
    active_count: int = Field(default=0, ge=0)
    released_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _hold_state_is_honest(self) -> "LifecycleLegalHoldSnapshot":
        if self.state is LifecycleReferenceCoverageState.COVERED:
            if self.reason is not None:
                raise ValueError("covered legal-hold inventory cannot carry a reason")
        elif self.reason is None:
            raise ValueError("incomplete legal-hold inventory must carry a reason")
        if self.state is not LifecycleReferenceCoverageState.COVERED and (
            self.active_count or self.released_count
        ):
            raise ValueError("incomplete legal-hold inventory cannot claim counts")
        return self


class LifecycleReferenceSnapshot(RuntimeContract):
    """One authoritative-or-explicitly-incomplete lifecycle snapshot."""

    scope: LifecycleReferenceSnapshotScope
    run_context: LifecycleReferenceRunContext
    graph: LifecycleReferenceGraph | None = None
    owners: tuple[LifecycleReferenceOwnerSnapshot, ...]
    families: tuple[LifecycleReferenceFamilySnapshot, ...]
    legal_holds: LifecycleLegalHoldSnapshot
    event_count: int = Field(ge=0)
    non_ledger_event_count: int = Field(ge=0)
    next_after_sequence: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _snapshot_inventory_is_closed(self) -> "LifecycleReferenceSnapshot":
        if tuple(owner.owner for owner in self.owners) != tuple(
            sorted(LifecycleReferenceOwner, key=lambda value: value.value)
        ):
            raise ValueError("lifecycle owner snapshot inventory must be closed")
        if tuple(family.family for family in self.families) != tuple(
            LifecycleReferenceFamily
        ):
            raise ValueError("lifecycle family snapshot inventory must be closed")
        if self.complete and self.graph is None:
            raise ValueError("complete lifecycle snapshot requires a graph")
        if self.run_context.run_id != self.scope.run_id:
            raise ValueError("lifecycle run context must match its snapshot scope")
        if (
            self.scope.conversation_id is not None
            and self.run_context.conversation_id != self.scope.conversation_id
        ):
            raise ValueError("lifecycle conversation context must match its scope")
        return self

    @property
    def complete(self) -> bool:
        """Whether every owner/family has a complete authoritative inventory."""

        return (
            self.graph is not None
            and self.legal_holds.state is LifecycleReferenceCoverageState.COVERED
            and all(
                owner.state is LifecycleReferenceCoverageState.COVERED
                for owner in self.owners
            )
            and all(
                family.state is LifecycleReferenceCoverageState.COVERED
                for family in self.families
            )
        )

    def assert_complete(self) -> None:
        """Fail closed before any future retention/deletion implementation."""

        if self.complete:
            return
        raise LifecycleReferenceSnapshotError(
            (
                LifecycleReferenceSnapshotDiagnostic(
                    code=LifecycleReferenceSnapshotDiagnosticCode.SNAPSHOT_INCOMPLETE
                ),
            )
        )

    def assert_no_active_legal_holds(self) -> None:
        """Fail closed if the bounded legal-hold inventory is incomplete/active."""

        if self.legal_holds.state is not LifecycleReferenceCoverageState.COVERED:
            raise LifecycleReferenceSnapshotError(
                (
                    LifecycleReferenceSnapshotDiagnostic(
                        code=LifecycleReferenceSnapshotDiagnosticCode.SNAPSHOT_INCOMPLETE,
                        family=LifecycleReferenceFamily.LEGAL_HOLDS,
                    ),
                )
            )
        if self.legal_holds.active_count:
            raise LifecycleReferenceSnapshotError(
                (
                    LifecycleReferenceSnapshotDiagnostic(
                        code=LifecycleReferenceSnapshotDiagnosticCode.ACTIVE_LEGAL_HOLD,
                        family=LifecycleReferenceFamily.LEGAL_HOLDS,
                    ),
                )
            )


def default_lifecycle_reference_owner_capabilities() -> tuple[
    LifecycleReferenceOwnerCapability, ...
]:
    """Return the reviewed source plan for every existing registry owner.

    Browser, sandbox, and workspace objects intentionally do not have a
    cross-owner inventory in ai-backend yet.  Claim, usage, and export gaps are
    represented as family-level outcomes below; no owner is silently omitted.
    """

    unavailable = {
        LifecycleReferenceOwner.BROWSER_AUTHORITY,
        LifecycleReferenceOwner.SANDBOX_RUNTIME,
        LifecycleReferenceOwner.WORKSPACE_AUTHORITY,
    }
    return tuple(
        LifecycleReferenceOwnerCapability(
            owner=owner,
            strategy=(
                LifecycleReferenceOwnerStrategy.UNAVAILABLE
                if owner in unavailable
                else LifecycleReferenceOwnerStrategy.ARTIFACT_FOLD
                if owner is LifecycleReferenceOwner.ARTIFACT_REPOSITORY
                else LifecycleReferenceOwnerStrategy.LEDGER
            ),
        )
        for owner in sorted(LifecycleReferenceOwner, key=lambda value: value.value)
    )


class LifecycleReferenceConformanceGate:
    """Static launch gate over the existing closed registry and owner plan."""

    @classmethod
    def validate(
        cls,
        *,
        registry: LifecycleReferenceRegistry,
        capabilities: Sequence[LifecycleReferenceOwnerCapability],
    ) -> None:
        """Reject missing/duplicate/unowned schemes without inspecting data."""

        try:
            registry.assert_contract_coverage()
            registry.assert_registered_examples()
        except LifecycleReferenceGraphError as error:
            raise LifecycleReferenceConformanceError(
                "lifecycle reference registry contract is not conformant"
            ) from error

        registrations = registry.registrations
        if {item.scheme for item in registrations} != set(LifecycleReferenceScheme):
            raise LifecycleReferenceConformanceError(
                "lifecycle reference registry does not cover every scheme"
            )

        capability_by_owner: dict[
            LifecycleReferenceOwner, LifecycleReferenceOwnerCapability
        ] = {}
        for capability in capabilities:
            if capability.owner in capability_by_owner:
                raise LifecycleReferenceConformanceError(
                    "duplicate lifecycle owner capability"
                )
            capability_by_owner[capability.owner] = capability
        if set(capability_by_owner) != set(LifecycleReferenceOwner):
            raise LifecycleReferenceConformanceError(
                "lifecycle owner capability inventory is incomplete"
            )
        if any(
            registration.owner not in capability_by_owner
            for registration in registrations
        ):
            raise LifecycleReferenceConformanceError(
                "lifecycle reference scheme has no owner capability"
            )

    @classmethod
    def validate_current(cls) -> None:
        """Validate the shipped registry/strategy inventory during app launch."""

        cls.validate(
            registry=LifecycleReferenceRegistry.default(),
            capabilities=default_lifecycle_reference_owner_capabilities(),
        )


class LifecycleReferenceSnapshotCollector:
    """Collect a bounded cross-owner lifecycle snapshot without side effects."""

    def __init__(
        self,
        *,
        event_store: object | None,
        persistence: object | None,
        registry: LifecycleReferenceRegistry | None = None,
        capabilities: Sequence[LifecycleReferenceOwnerCapability] | None = None,
    ) -> None:
        self._registry = registry or LifecycleReferenceRegistry.default()
        self._capabilities = tuple(
            capabilities
            if capabilities is not None
            else default_lifecycle_reference_owner_capabilities()
        )
        LifecycleReferenceConformanceGate.validate(
            registry=self._registry,
            capabilities=self._capabilities,
        )
        self._event_store = event_store
        self._persistence = persistence
        self._enumerator = LifecycleReferenceEnumerator(self._registry)

    async def collect(
        self,
        *,
        scope: LifecycleReferenceSnapshotScope,
    ) -> LifecycleReferenceSnapshot:
        """Return complete coverage only when every authoritative source agrees.

        An unavailable inventory is a first-class *result* (not a fake empty
        list).  An unknown/malformed ledger reference instead refuses the
        snapshot, because returning any graph would be an unsafe partial claim.
        """

        run = await self._get_scoped_run(scope)
        run_context = self._run_context(scope=scope, run=run)
        window = await self._get_event_window(scope)
        legal_holds = await self._collect_legal_holds(scope)
        owner_schemes = self._schemes_by_owner()

        if window is None:
            owners = self._owners_without_event_window(owner_schemes)
            return LifecycleReferenceSnapshot(
                scope=scope,
                run_context=run_context,
                owners=owners,
                families=self._families_for_unavailable_window(legal_holds),
                legal_holds=legal_holds,
                event_count=0,
                non_ledger_event_count=0,
            )

        event_status = self._window_state(scope=scope, window=window)
        if event_status is not None:
            state, reason = event_status
            owners = self._owners_for_incomplete_window(
                owner_schemes=owner_schemes,
                state=state,
                reason=reason,
            )
            return LifecycleReferenceSnapshot(
                scope=scope,
                run_context=run_context,
                owners=owners,
                families=self._families_for_incomplete_window(
                    owners=owners,
                    legal_holds=legal_holds,
                ),
                legal_holds=legal_holds,
                event_count=len(window.events),
                non_ledger_event_count=0,
                next_after_sequence=window.next_after_sequence,
            )

        ledger_events, non_ledger_event_count = self._ledger_events(
            scope=scope,
            window=window,
            run=run,
        )
        try:
            graph = self._enumerator.enumerate(
                run_id=scope.run_id,
                events=ledger_events,
            )
        except LifecycleReferenceGraphError as error:
            raise LifecycleReferenceSnapshotError(
                (
                    LifecycleReferenceSnapshotDiagnostic(
                        code=LifecycleReferenceSnapshotDiagnosticCode.LEDGER_REFERENCE_REJECTED
                    ),
                )
            ) from error

        artifacts, artifact_state = self._collect_artifact_revisions(ledger_events)
        owners = self._owners_for_complete_window(
            owner_schemes=owner_schemes,
            graph=graph,
            artifacts=artifacts,
            artifact_state=artifact_state,
        )
        return LifecycleReferenceSnapshot(
            scope=scope,
            run_context=run_context,
            graph=graph,
            owners=owners,
            families=self._families_for_complete_window(
                owners=owners,
                legal_holds=legal_holds,
            ),
            legal_holds=legal_holds,
            event_count=len(window.events),
            non_ledger_event_count=non_ledger_event_count,
        )

    async def _get_scoped_run(self, scope: LifecycleReferenceSnapshotScope) -> object:
        if not isinstance(self._persistence, LifecycleReferenceRunScopePort):
            raise LifecycleReferenceSnapshotError(
                (
                    LifecycleReferenceSnapshotDiagnostic(
                        code=LifecycleReferenceSnapshotDiagnosticCode.RUN_SCOPE_UNAVAILABLE
                    ),
                )
            )
        try:
            run = await self._persistence.get_run(
                org_id=scope.org_id,
                run_id=scope.run_id,
            )
        except Exception as error:
            raise LifecycleReferenceSnapshotError(
                (
                    LifecycleReferenceSnapshotDiagnostic(
                        code=LifecycleReferenceSnapshotDiagnosticCode.RUN_SCOPE_UNAVAILABLE
                    ),
                )
            ) from error
        if run is None or not self._run_matches_scope(run, scope):
            raise LifecycleReferenceSnapshotError(
                (
                    LifecycleReferenceSnapshotDiagnostic(
                        code=LifecycleReferenceSnapshotDiagnosticCode.RUN_NOT_FOUND_OR_NOT_AUTHORIZED
                    ),
                )
            )
        return run

    @staticmethod
    def _run_matches_scope(run: object, scope: LifecycleReferenceSnapshotScope) -> bool:
        if getattr(run, "org_id", None) != scope.org_id:
            return False
        if getattr(run, "run_id", None) != scope.run_id:
            return False
        if scope.user_id is not None and getattr(run, "user_id", None) != scope.user_id:
            return False
        return not (
            scope.conversation_id is not None
            and getattr(run, "conversation_id", None) != scope.conversation_id
        )

    def _run_context(
        self,
        *,
        scope: LifecycleReferenceSnapshotScope,
        run: object,
    ) -> LifecycleReferenceRunContext:
        conversation_id = getattr(run, "conversation_id", None)
        user_message_id = getattr(run, "user_message_id", None)
        if not isinstance(conversation_id, str) or not isinstance(user_message_id, str):
            raise LifecycleReferenceSnapshotError(
                (
                    LifecycleReferenceSnapshotDiagnostic(
                        code=LifecycleReferenceSnapshotDiagnosticCode.RUN_SCOPE_UNAVAILABLE
                    ),
                )
            )
        try:
            user_message_ref = self._registry.parse(f"message://{user_message_id}")
        except LifecycleReferenceGraphError as error:
            raise LifecycleReferenceSnapshotError(
                (
                    LifecycleReferenceSnapshotDiagnostic(
                        code=LifecycleReferenceSnapshotDiagnosticCode.RUN_SCOPE_UNAVAILABLE
                    ),
                )
            ) from error
        try:
            return LifecycleReferenceRunContext(
                conversation_id=conversation_id,
                run_id=scope.run_id,
                user_message_ref=user_message_ref,
            )
        except ValueError as error:
            raise LifecycleReferenceSnapshotError(
                (
                    LifecycleReferenceSnapshotDiagnostic(
                        code=LifecycleReferenceSnapshotDiagnosticCode.RUN_SCOPE_UNAVAILABLE
                    ),
                )
            ) from error

    async def _get_event_window(
        self, scope: LifecycleReferenceSnapshotScope
    ) -> LifecycleReferenceEventWindow | None:
        if not isinstance(self._event_store, LifecycleReferenceEventWindowPort):
            return None
        try:
            window = await self._event_store.list_lifecycle_reference_events_window(
                org_id=scope.org_id,
                run_id=scope.run_id,
                after_sequence=scope.after_sequence,
                limit=scope.event_limit,
            )
        except Exception:
            return None
        return window if isinstance(window, LifecycleReferenceEventWindow) else None

    async def _collect_legal_holds(
        self, scope: LifecycleReferenceSnapshotScope
    ) -> LifecycleLegalHoldSnapshot:
        if not isinstance(self._persistence, LifecycleReferenceLegalHoldPort):
            return LifecycleLegalHoldSnapshot(
                state=LifecycleReferenceCoverageState.UNAVAILABLE,
                reason=LifecycleReferenceCoverageReason.LEGAL_HOLD_INVENTORY_UNAVAILABLE,
            )
        try:
            rows = tuple(
                await self._persistence.list_legal_holds(
                    org_id=scope.org_id,
                    include_released=True,
                    limit=scope.legal_hold_limit + 1,
                )
            )
        except Exception:
            return LifecycleLegalHoldSnapshot(
                state=LifecycleReferenceCoverageState.UNAVAILABLE,
                reason=LifecycleReferenceCoverageReason.LEGAL_HOLD_INVENTORY_UNAVAILABLE,
            )
        if len(rows) > scope.legal_hold_limit:
            return LifecycleLegalHoldSnapshot(
                state=LifecycleReferenceCoverageState.WITHHELD,
                reason=LifecycleReferenceCoverageReason.LEGAL_HOLD_WINDOW_TRUNCATED,
            )
        if any(getattr(row, "org_id", None) != scope.org_id for row in rows):
            return LifecycleLegalHoldSnapshot(
                state=LifecycleReferenceCoverageState.WITHHELD,
                reason=LifecycleReferenceCoverageReason.LEGAL_HOLD_INVENTORY_UNAVAILABLE,
            )
        return LifecycleLegalHoldSnapshot(
            state=LifecycleReferenceCoverageState.COVERED,
            active_count=sum(getattr(row, "released_at", None) is None for row in rows),
            released_count=sum(
                getattr(row, "released_at", None) is not None for row in rows
            ),
        )

    @staticmethod
    def _window_state(
        *,
        scope: LifecycleReferenceSnapshotScope,
        window: LifecycleReferenceEventWindow,
    ) -> (
        tuple[LifecycleReferenceCoverageState, LifecycleReferenceCoverageReason] | None
    ):
        if not isinstance(window.has_more, bool):
            return (
                LifecycleReferenceCoverageState.WITHHELD,
                LifecycleReferenceCoverageReason.EVENT_WINDOW_INVALID,
            )
        if scope.after_sequence:
            return (
                LifecycleReferenceCoverageState.WITHHELD,
                LifecycleReferenceCoverageReason.PARTIAL_EVENT_RANGE,
            )
        if len(window.events) > scope.event_limit:
            return (
                LifecycleReferenceCoverageState.WITHHELD,
                LifecycleReferenceCoverageReason.EVENT_WINDOW_INVALID,
            )
        if window.has_more:
            return (
                LifecycleReferenceCoverageState.WITHHELD,
                LifecycleReferenceCoverageReason.EVENT_WINDOW_TRUNCATED,
            )
        if window.next_after_sequence is not None:
            return (
                LifecycleReferenceCoverageState.WITHHELD,
                LifecycleReferenceCoverageReason.EVENT_WINDOW_INVALID,
            )
        return None

    def _ledger_events(
        self,
        *,
        scope: LifecycleReferenceSnapshotScope,
        window: LifecycleReferenceEventWindow,
        run: object,
    ) -> tuple[list[dict[str, object]], int]:
        expected_sequence = scope.after_sequence + 1
        ledger_events: list[dict[str, object]] = []
        non_ledger_event_count = 0
        for envelope in window.events:
            if getattr(envelope, "run_id", None) != scope.run_id or getattr(
                envelope, "conversation_id", None
            ) != getattr(run, "conversation_id", None):
                raise LifecycleReferenceSnapshotError(
                    (
                        LifecycleReferenceSnapshotDiagnostic(
                            code=LifecycleReferenceSnapshotDiagnosticCode.LEDGER_REFERENCE_REJECTED
                        ),
                    )
                )
            sequence_no = getattr(envelope, "sequence_no", None)
            if (
                not isinstance(sequence_no, int)
                or isinstance(sequence_no, bool)
                or sequence_no != expected_sequence
            ):
                raise LifecycleReferenceSnapshotError(
                    (
                        LifecycleReferenceSnapshotDiagnostic(
                            code=LifecycleReferenceSnapshotDiagnosticCode.LEDGER_REFERENCE_REJECTED
                        ),
                    )
                )
            expected_sequence += 1
            event_value = self._event_type_value(getattr(envelope, "event_type", None))
            try:
                event_type = LedgerEventType(event_value)
            except ValueError:
                non_ledger_event_count += 1
                continue
            payload = getattr(envelope, "payload", None)
            if not isinstance(payload, Mapping):
                raise LifecycleReferenceSnapshotError(
                    (
                        LifecycleReferenceSnapshotDiagnostic(
                            code=LifecycleReferenceSnapshotDiagnosticCode.LEDGER_REFERENCE_REJECTED
                        ),
                    )
                )
            ledger_events.append(
                {
                    "event_type": event_type.value,
                    "sequence_no": sequence_no,
                    "payload": dict(payload),
                }
            )
        return ledger_events, non_ledger_event_count

    @staticmethod
    def _event_type_value(value: object) -> str:
        raw = getattr(value, "value", value)
        return raw if isinstance(raw, str) else ""

    def _collect_artifact_revisions(
        self,
        events: Sequence[Mapping[str, object]],
    ) -> tuple[
        tuple[LifecycleArtifactRevisionReference, ...],
        tuple[LifecycleReferenceCoverageState, LifecycleReferenceCoverageReason | None],
    ]:
        projection = ArtifactProjection.fold(events)
        if projection.ignored_malformed_events:
            return (
                (),
                (
                    LifecycleReferenceCoverageState.WITHHELD,
                    LifecycleReferenceCoverageReason.ARTIFACT_FOLD_INCOMPLETE,
                ),
            )
        revisions: list[LifecycleArtifactRevisionReference] = []
        try:
            for artifact in projection.artifacts:
                for revision in artifact.revisions:
                    content_ref = self._registry.parse(revision.content_ref)
                    blob_ref = self._registry.parse(
                        f"artifact-blob://sha256/{revision.content_digest}"
                    )
                    revisions.append(
                        LifecycleArtifactRevisionReference(
                            artifact_id=artifact.artifact_id,
                            revision=revision.revision,
                            content_ref=content_ref,
                            blob_ref=blob_ref,
                        )
                    )
        except (LifecycleReferenceGraphError, ValueError):
            return (
                (),
                (
                    LifecycleReferenceCoverageState.WITHHELD,
                    LifecycleReferenceCoverageReason.ARTIFACT_FOLD_INCOMPLETE,
                ),
            )
        return (
            tuple(
                sorted(
                    revisions,
                    key=lambda item: (item.artifact_id, item.revision),
                )
            ),
            (LifecycleReferenceCoverageState.COVERED, None),
        )

    def _schemes_by_owner(
        self,
    ) -> dict[LifecycleReferenceOwner, tuple[LifecycleReferenceScheme, ...]]:
        registrations: dict[
            LifecycleReferenceOwner, list[LifecycleReferenceRegistration]
        ] = {owner: [] for owner in LifecycleReferenceOwner}
        for registration in self._registry.registrations:
            registrations[registration.owner].append(registration)
        return {
            owner: tuple(
                registration.scheme
                for registration in sorted(rows, key=lambda row: row.scheme.value)
            )
            for owner, rows in registrations.items()
        }

    def _owners_without_event_window(
        self,
        owner_schemes: Mapping[
            LifecycleReferenceOwner, tuple[LifecycleReferenceScheme, ...]
        ],
    ) -> tuple[LifecycleReferenceOwnerSnapshot, ...]:
        return tuple(
            LifecycleReferenceOwnerSnapshot(
                owner=owner,
                schemes=owner_schemes[owner],
                state=LifecycleReferenceCoverageState.UNAVAILABLE,
                reason=(
                    LifecycleReferenceCoverageReason.OWNER_NOT_MATERIALIZED
                    if self._strategy_for(owner)
                    is LifecycleReferenceOwnerStrategy.UNAVAILABLE
                    else LifecycleReferenceCoverageReason.EVENT_WINDOW_UNAVAILABLE
                ),
            )
            for owner in sorted(LifecycleReferenceOwner, key=lambda value: value.value)
        )

    def _owners_for_incomplete_window(
        self,
        *,
        owner_schemes: Mapping[
            LifecycleReferenceOwner, tuple[LifecycleReferenceScheme, ...]
        ],
        state: LifecycleReferenceCoverageState,
        reason: LifecycleReferenceCoverageReason,
    ) -> tuple[LifecycleReferenceOwnerSnapshot, ...]:
        return tuple(
            LifecycleReferenceOwnerSnapshot(
                owner=owner,
                schemes=owner_schemes[owner],
                state=(
                    LifecycleReferenceCoverageState.UNAVAILABLE
                    if self._strategy_for(owner)
                    is LifecycleReferenceOwnerStrategy.UNAVAILABLE
                    else state
                ),
                reason=(
                    LifecycleReferenceCoverageReason.OWNER_NOT_MATERIALIZED
                    if self._strategy_for(owner)
                    is LifecycleReferenceOwnerStrategy.UNAVAILABLE
                    else reason
                ),
            )
            for owner in sorted(LifecycleReferenceOwner, key=lambda value: value.value)
        )

    def _owners_for_complete_window(
        self,
        *,
        owner_schemes: Mapping[
            LifecycleReferenceOwner, tuple[LifecycleReferenceScheme, ...]
        ],
        graph: LifecycleReferenceGraph,
        artifacts: tuple[LifecycleArtifactRevisionReference, ...],
        artifact_state: tuple[
            LifecycleReferenceCoverageState, LifecycleReferenceCoverageReason | None
        ],
    ) -> tuple[LifecycleReferenceOwnerSnapshot, ...]:
        nodes_by_owner: dict[
            LifecycleReferenceOwner, tuple[LifecycleGraphNode, ...]
        ] = {
            owner: tuple(node for node in graph.nodes if node.owner is owner)
            for owner in LifecycleReferenceOwner
        }
        snapshots: list[LifecycleReferenceOwnerSnapshot] = []
        for owner in sorted(LifecycleReferenceOwner, key=lambda value: value.value):
            strategy = self._strategy_for(owner)
            if strategy is LifecycleReferenceOwnerStrategy.UNAVAILABLE:
                snapshots.append(
                    LifecycleReferenceOwnerSnapshot(
                        owner=owner,
                        schemes=owner_schemes[owner],
                        state=LifecycleReferenceCoverageState.UNAVAILABLE,
                        reason=LifecycleReferenceCoverageReason.OWNER_NOT_MATERIALIZED,
                    )
                )
                continue
            if owner is LifecycleReferenceOwner.ARTIFACT_REPOSITORY:
                state, reason = artifact_state
                snapshots.append(
                    LifecycleReferenceOwnerSnapshot(
                        owner=owner,
                        schemes=owner_schemes[owner],
                        state=state,
                        reason=reason,
                        nodes=(
                            nodes_by_owner[owner]
                            if state is LifecycleReferenceCoverageState.COVERED
                            else ()
                        ),
                        artifact_revisions=(
                            artifacts
                            if state is LifecycleReferenceCoverageState.COVERED
                            else ()
                        ),
                    )
                )
                continue
            snapshots.append(
                LifecycleReferenceOwnerSnapshot(
                    owner=owner,
                    schemes=owner_schemes[owner],
                    state=LifecycleReferenceCoverageState.COVERED,
                    nodes=nodes_by_owner[owner],
                )
            )
        return tuple(snapshots)

    def _strategy_for(
        self, owner: LifecycleReferenceOwner
    ) -> LifecycleReferenceOwnerStrategy:
        return next(
            capability.strategy
            for capability in self._capabilities
            if capability.owner is owner
        )

    @staticmethod
    def _families_for_unavailable_window(
        legal_holds: LifecycleLegalHoldSnapshot,
    ) -> tuple[LifecycleReferenceFamilySnapshot, ...]:
        return LifecycleReferenceSnapshotCollector._families(
            owner_states={},
            legal_holds=legal_holds,
            event_reason=LifecycleReferenceCoverageReason.EVENT_WINDOW_UNAVAILABLE,
        )

    @staticmethod
    def _families_for_incomplete_window(
        *,
        owners: Sequence[LifecycleReferenceOwnerSnapshot],
        legal_holds: LifecycleLegalHoldSnapshot,
    ) -> tuple[LifecycleReferenceFamilySnapshot, ...]:
        return LifecycleReferenceSnapshotCollector._families(
            owner_states={item.owner: item for item in owners},
            legal_holds=legal_holds,
            event_reason=next(
                (
                    item.reason
                    for item in owners
                    if item.owner is LifecycleReferenceOwner.RUNTIME_EVENT_STORE
                ),
                LifecycleReferenceCoverageReason.EVENT_WINDOW_TRUNCATED,
            ),
        )

    @staticmethod
    def _families_for_complete_window(
        *,
        owners: Sequence[LifecycleReferenceOwnerSnapshot],
        legal_holds: LifecycleLegalHoldSnapshot,
    ) -> tuple[LifecycleReferenceFamilySnapshot, ...]:
        return LifecycleReferenceSnapshotCollector._families(
            owner_states={item.owner: item for item in owners},
            legal_holds=legal_holds,
            event_reason=None,
        )

    @staticmethod
    def _families(
        *,
        owner_states: Mapping[LifecycleReferenceOwner, LifecycleReferenceOwnerSnapshot],
        legal_holds: LifecycleLegalHoldSnapshot,
        event_reason: LifecycleReferenceCoverageReason | None,
    ) -> tuple[LifecycleReferenceFamilySnapshot, ...]:
        def from_owner(
            family: LifecycleReferenceFamily,
            owner: LifecycleReferenceOwner,
        ) -> LifecycleReferenceFamilySnapshot:
            owner_snapshot = owner_states.get(owner)
            if owner_snapshot is None:
                return LifecycleReferenceFamilySnapshot(
                    family=family,
                    owner=owner,
                    state=LifecycleReferenceCoverageState.UNAVAILABLE,
                    reason=event_reason
                    or LifecycleReferenceCoverageReason.EVENT_WINDOW_UNAVAILABLE,
                )
            return LifecycleReferenceFamilySnapshot(
                family=family,
                owner=owner,
                state=owner_snapshot.state,
                reason=owner_snapshot.reason,
                reference_count=(
                    len(owner_snapshot.nodes)
                    + len(owner_snapshot.artifact_revisions) * 2
                    if owner_snapshot.state is LifecycleReferenceCoverageState.COVERED
                    else 0
                ),
            )

        artifact_family = from_owner(
            LifecycleReferenceFamily.ARTIFACTS_REVISIONS_BLOBS,
            LifecycleReferenceOwner.ARTIFACT_REPOSITORY,
        )
        workspace_owner = owner_states.get(LifecycleReferenceOwner.WORKSPACE_AUTHORITY)
        if (
            workspace_owner is not None
            and workspace_owner.state is not LifecycleReferenceCoverageState.COVERED
        ):
            # Artifact metadata/revisions are folded from the ledger above, but
            # the registered ``artifact-blob`` scheme belongs to the workspace
            # authority. Do not claim the combined family is complete until
            # that owner supplies a real bounded blob inventory.
            artifact_family = LifecycleReferenceFamilySnapshot(
                family=LifecycleReferenceFamily.ARTIFACTS_REVISIONS_BLOBS,
                owner=LifecycleReferenceOwner.WORKSPACE_AUTHORITY,
                state=workspace_owner.state,
                reason=workspace_owner.reason,
            )

        return (
            from_owner(
                LifecycleReferenceFamily.RUN_CONVERSATION_MESSAGE_EVENTS,
                LifecycleReferenceOwner.RUNTIME_EVENT_STORE,
            ),
            from_owner(
                LifecycleReferenceFamily.OPERATIONS,
                LifecycleReferenceOwner.OPERATION_GATEWAY,
            ),
            artifact_family,
            from_owner(
                LifecycleReferenceFamily.SURFACES_VIEWS_SPECS,
                LifecycleReferenceOwner.SURFACE_PRESENTATION,
            ),
            from_owner(
                LifecycleReferenceFamily.STAGES_PROPOSALS,
                LifecycleReferenceOwner.EFFECT_STAGE,
            ),
            LifecycleReferenceFamilySnapshot(
                family=LifecycleReferenceFamily.EFFECT_CLAIMS,
                owner=LifecycleReferenceOwner.EFFECT_STAGE,
                state=LifecycleReferenceCoverageState.UNAVAILABLE,
                reason=LifecycleReferenceCoverageReason.CLAIM_INVENTORY_UNAVAILABLE,
            ),
            from_owner(
                LifecycleReferenceFamily.RECEIPTS,
                LifecycleReferenceOwner.RECEIPT_LIFECYCLE,
            ),
            from_owner(
                LifecycleReferenceFamily.WORKSPACE_OVERLAY_PREIMAGE_PREPARED_JOURNAL_RECOVERY,
                LifecycleReferenceOwner.WORKSPACE_AUTHORITY,
            ),
            from_owner(
                LifecycleReferenceFamily.SANDBOX_SNAPSHOTS_PATCHES_RESOURCES,
                LifecycleReferenceOwner.SANDBOX_RUNTIME,
            ),
            from_owner(
                LifecycleReferenceFamily.BROWSER_DOWNLOADS_UPLOADS_RECEIPTS,
                LifecycleReferenceOwner.BROWSER_AUTHORITY,
            ),
            LifecycleReferenceFamilySnapshot(
                family=LifecycleReferenceFamily.USAGE_ATTRIBUTION,
                owner=LifecycleReferenceOwner.RUNTIME_EVENT_STORE,
                state=LifecycleReferenceCoverageState.UNAVAILABLE,
                reason=LifecycleReferenceCoverageReason.USAGE_INVENTORY_UNAVAILABLE,
            ),
            LifecycleReferenceFamilySnapshot(
                family=LifecycleReferenceFamily.AUDIT_EXPORTS,
                owner=LifecycleReferenceOwner.RECEIPT_LIFECYCLE,
                state=LifecycleReferenceCoverageState.UNAVAILABLE,
                reason=LifecycleReferenceCoverageReason.AUDIT_EXPORT_INVENTORY_UNAVAILABLE,
            ),
            LifecycleReferenceFamilySnapshot(
                family=LifecycleReferenceFamily.LEGAL_HOLDS,
                owner=LifecycleReferenceOwner.POLICY_ENGINE,
                state=legal_holds.state,
                reason=legal_holds.reason,
            ),
        )


__all__ = (
    "LifecycleArtifactRevisionReference",
    "LifecycleLegalHoldSnapshot",
    "LifecycleReferenceConformanceError",
    "LifecycleReferenceConformanceGate",
    "LifecycleReferenceCoverageReason",
    "LifecycleReferenceCoverageState",
    "LifecycleReferenceEventWindow",
    "LifecycleReferenceEventWindowPort",
    "LifecycleReferenceFamily",
    "LifecycleReferenceFamilySnapshot",
    "LifecycleReferenceLegalHoldPort",
    "LifecycleReferenceOwnerCapability",
    "LifecycleReferenceOwnerSnapshot",
    "LifecycleReferenceOwnerStrategy",
    "LifecycleReferenceRunScopePort",
    "LifecycleReferenceRunContext",
    "LifecycleReferenceSnapshot",
    "LifecycleReferenceSnapshotCollector",
    "LifecycleReferenceSnapshotDiagnostic",
    "LifecycleReferenceSnapshotDiagnosticCode",
    "LifecycleReferenceSnapshotError",
    "LifecycleReferenceSnapshotScope",
    "default_lifecycle_reference_owner_capabilities",
)
