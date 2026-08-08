"""Connector write-back — Save on an edited surface becomes a STAGED proposal.

The design's Save table has two rows. An **artifact** origin splices the deltas
into the source document and makes a new revision: local, reversible, no model,
no approval, and entirely client-side. A **connector** origin cannot be local —
the deltas have to become a real request to a vendor — so it goes:

    deltas → model maps op + arg names → staged → diff → approve → commit

This module owns the middle three arrows and, just as importantly, owns *not*
owning the last one.

**Nothing this module can reach executes.** ``WriteStager`` has no MCP client by
construction, and the copy this lane stages through is narrowed twice more
before it is used: :class:`~agent_runtime.surfaces_v2.rowset_policy.NeverBypassPolicy`
closes the FR-C8 allow-always branch, and ``commit_queue=None`` removes the only
seam that can enqueue a commit at all. So the Save path holds no execution
capability of any kind, rather than holding one and declining to use it. The
rows land ``STAGED``; the user then reviews the per-row diff and approves
through the already-wired ``POST /v1/agent/stages/{stage_id}/apply``, whose
stager (composed separately, in the app) *does* carry the queue. That endpoint
is the single door to execution and this lane does not go near it.

Closing the auto-apply branch here is not belt-and-braces, it is the WYSIWYG
property: at staging time the user has seen a *cell diff*, not the composed
``target_args``. Applying an argument set nobody reviewed is exactly what the
staging engine exists to prevent, and an allow-always connector policy — a
reasonable thing for an agent's own proposals — must not reach through a
different lane's gesture.

**Two things are read server-side rather than believed.** The surface's origin
(which connector, which read op) is folded out of the run's own ledger, so a
client cannot name a different connector than the one it is looking at. The
run's identity is scope-checked against the caller before anything else. The
rows *as read* do come from the client, and that is deliberate: they are one
half of the provenance set in
:class:`~.write_mapping.ArgProvenanceAudit`, whose job is to prove no value was
authored by the MODEL. The user is not the adversary there — they are the
author, and the values they typed are the ones they are approving.

That client-supplied ``row`` is a value source and never an *authorisation* to
send a field: :class:`~.write_mapping.WriteArgScope` confines the composed args
to the columns the diff shows plus the keys the CONNECTOR's own op schema
requires.

**"Padding ``row`` widens nothing" needed a stronger argument than it had.** The
old claim was true of the arg NAME set and false of the value: a ``ROW`` binding
could name a declared scope arg and fill it from ANY field of the posted row, so
``issue_id ← row['parent_id']`` addressed a different record and
``cc ← row['secret_recipient']`` sent a recipient the surface never rendered.
Three things now hold it up, and none of them is a snapshot read on the save
path:

* a ``ROW`` binding is IDENTITY-mapped — it may only read ``edit.row[arg]`` for
  an ``arg`` the connector declared required — so a padded field is unreachable
  by name, not merely unusual;
* :class:`~.write_mapping.ArgProvenanceAudit` admits only TOP-LEVEL row values,
  matching what a binding can reach rather than walking to any depth; and
* every composed arg, carried ones included, appears in ``StagedRow.sends`` and
  is read at the approval gate — so a value taken from the row is displayed as
  ``issue_id: PAR-1 (sending unchanged)`` rather than riding along invisibly.

The audit's own remediation was to resolve the row from the server-held surface
snapshot instead. That is deliberately NOT built: ``SurfaceSnapshot`` carries a
``payload_ref`` and no hydrated content, so it would mean a blob read on the save
path plus a new equality semantics for "the row as rendered" that nothing
defines — to defend against a client that lies about its own record id, when the
client here is the user, who is the author and not the adversary. Revisit it only
if the threat model ever admits a hostile client.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

from agent_runtime.capabilities.surfaces.generator import (
    ShapingCredentials,
    SpecCompletionPort,
)
from agent_runtime.capabilities.surfaces.write_mapping import (
    SurfaceRowEdit,
    WriteMapping,
    WriteMappingError,
    WriteOpCandidate,
    build_surface_write_mapper,
    edited_columns_of,
)
from agent_runtime.capabilities.surfaces.write_ops_capture import (
    CapturedSurfaceWriteOps,
    CapturedWriteOpsProjection,
)
from agent_runtime.surfaces_v2.config import SurfacesV2Flag
from agent_runtime.surfaces_v2.constants import Titles, Values
from agent_runtime.surfaces_v2.projection import SurfaceSnapshot, SurfaceStoreProjection
from agent_runtime.surfaces_v2.rowset_policy import NeverBypassPolicy
from agent_runtime.surfaces_v2.staging import StagedWriteState, WriteStager

_LOGGER = logging.getLogger(__name__)

_PREFIX = "[surfaces.writeback]"


class _Messages:
    """Safe public messages. Constants only — never model or provider output."""

    SURFACE_NOT_FOUND = "surface_not_found"
    RUN_NOT_FOUND = "run_not_found"
    CATALOG_UNAVAILABLE = (
        "The connector's write operations are not available, so this save "
        "cannot be prepared. Nothing was staged."
    )
    NOT_A_CONNECTOR_SURFACE = (
        "This surface did not come from a connector read, so it has no write target."
    )


class SurfaceWriteBackError(Exception):
    """A save could not be staged. Carries only a safe public message."""

    safe_message: str = "The save could not be prepared."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.safe_message)
        if message is not None:
            self.safe_message = message


class SurfaceNotFound(SurfaceWriteBackError):
    """Unknown surface, or the flag is off so no v2 surface exists. → 404."""

    safe_message: str = _Messages.SURFACE_NOT_FOUND


class RunNotFound(SurfaceWriteBackError):
    """Unknown run, or a run belonging to another user. → 404 (never 403).

    A run id is not an authorization capability, and separating "absent" from
    "someone else's" would let a caller enumerate a peer's runs — the same
    reasoning ``StageRoutes`` applies to a stage id.
    """

    safe_message: str = _Messages.RUN_NOT_FOUND


class WriteOpsUnavailable(SurfaceWriteBackError):
    """No write-op catalogue is wired for this deployment. → 503.

    Distinct from "this connector has no write op" (a mapping rejection): this
    says the question could not be asked at all. It is LOUD for the same reason
    every other failure on this path is — a save that quietly stages nothing is
    a save the user believes happened.
    """

    safe_message: str = _Messages.CATALOG_UNAVAILABLE


@runtime_checkable
class ConnectorWriteOpsPort(Protocol):
    """The write ops a connector offers, as candidates the model may choose from.

    Injected rather than imported so this module never reaches an MCP client:
    it needs the *descriptors*, and holding the thing that lists them is one
    short step from holding the thing that calls them. The port is also where a
    deployment decides which ops are even proposable — filtering to writes, and
    to writes this user is permitted, belongs to the adapter, not here.

    **The ops are supplied to the port, not fetched by it.** ``captured`` is
    what the READ that produced this surface recorded — see
    :mod:`.write_ops_capture` — and it is the reason this lane can exist at all.
    The alternative shape, a port that goes and looks, has no source to look in:
    the curated action catalogue carries ``READ|WRITE`` and no schema, op
    descriptors are persisted nowhere else, and ``input_schema`` lives only on a
    LIVE loaded server. Giving the port the ability to reach one would put MCP
    client construction on an HTTP request path and a network hop between a user
    pressing Save and anything happening.

    So the default adapter is a lookup (:class:`~.write_ops_capture.CapturedConnectorWriteOps`)
    and the port's remaining job is NARROWING: a deployment with real per-user
    write permissions binds an adapter that drops the ops this user may not
    propose. Returning fewer is always safe; returning MORE than was captured
    means composing against a schema nobody recorded, and the lane treats an
    empty answer as *"no write is proposable"* rather than as a licence.
    """

    async def write_ops(
        self,
        *,
        org_id: str,
        user_id: str,
        connector: str,
        captured: Sequence[WriteOpCandidate],
        captured_connector: str,
    ) -> Sequence[WriteOpCandidate]:
        """Return the connector's proposable write ops (may be empty)."""
        ...


@runtime_checkable
class _UserPoliciesResolverLike(Protocol):
    """The API's per-user policy/BYOK snapshot seam (duck-typed, not imported)."""

    async def resolve(self, *, org_id: str, user_id: str) -> Mapping[str, object]: ...


class ApiShapingCredentials:
    """Build :class:`ShapingCredentials` in the API process, from the policy snapshot.

    The worker builds them from a hydrated ``AgentRuntimeContext``. There is no
    such context here — a save can arrive long after its run completed — but the
    same three values are reachable through the resolver the API already
    composes: the backend's aggregate snapshot carries ``provider_keys`` and
    ``provider_endpoints`` as top-level fields, and the parsers that split them
    out are the ones run-start already uses, so the key a save is signed with is
    the key the run itself would have used.

    ``workspace_behavior_overrides`` is deliberately absent: it lives on the
    runtime context only and has no API-side source. Omitting it degrades to the
    user-policy kwargs alone, which is the honest posture — never a guess at a
    workspace's region or privacy pin.

    The returned object holds plaintext key material. It is per-request and
    in-memory; never log it, never put it on an event.
    """

    @classmethod
    async def resolve(
        cls,
        *,
        resolver: _UserPoliciesResolverLike | None,
        org_id: str,
        user_id: str,
    ) -> ShapingCredentials | None:
        """Return the run's BYOK material, or ``None`` when none is reachable."""

        if resolver is None:
            return None
        try:
            snapshot = await resolver.resolve(org_id=org_id, user_id=user_id)
        except Exception:  # noqa: BLE001 - a resolver failure is "no credential"
            _LOGGER.warning("%s credentials_unresolved", _PREFIX)
            return None
        if not isinstance(snapshot, Mapping):
            return None

        from agent_runtime.api.user_policies_resolver import (  # noqa: PLC0415
            ProviderEndpointsParser,
            ProviderKeysParser,
        )

        keys, without_keys = ProviderKeysParser.split(dict(snapshot))
        endpoints, policies = ProviderEndpointsParser.split(dict(without_keys))
        return ShapingCredentials(
            provider_keys=keys,
            provider_endpoints=endpoints,
            user_policies_json=policies or None,
        )


@dataclass(frozen=True)
class SurfaceWriteBackCoordinator:
    """Save on a connector-origin surface: map, stage, stop.

    ``completion`` is the test seam (inject a fake and no model is built);
    production leaves it ``None`` and :func:`build_surface_write_mapper` resolves
    the model through the shaping ladder and RAISES when it cannot.
    """

    persistence: object
    event_store: object
    stager: WriteStager
    environ: Mapping[str, str]
    write_ops: ConnectorWriteOpsPort | None = None
    user_policies: _UserPoliciesResolverLike | None = None
    completion: SpecCompletionPort | None = None

    async def save(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        surface_id: str,
        edits: Sequence[SurfaceRowEdit],
    ) -> StagedWriteState:
        """Stage the user's batched cell edits as a reviewable row-set write.

        Order matters and is fail-closed throughout: scope, then origin, then
        catalogue, then model, then staging. Every step before staging raises a
        typed error and appends NOTHING to the ledger, so a refused save leaves
        the run exactly as it was.
        """

        run = await self._run_for_scope(org_id=org_id, user_id=user_id, run_id=run_id)
        snapshot, captured = await self._origin(
            org_id=org_id, run_id=run_id, surface_id=surface_id
        )
        candidates = await self._candidates(
            org_id=org_id,
            user_id=user_id,
            connector=snapshot.connector,
            captured=captured,
        )
        mapping = await self._map(
            run=run, snapshot=snapshot, candidates=candidates, edits=edits
        )
        return await self._stage(
            run=run,
            org_id=org_id,
            run_id=run_id,
            surface=snapshot,
            mapping=mapping,
            edits=edits,
        )

    # -- steps ---------------------------------------------------------------

    async def _map(
        self,
        *,
        run: object,
        snapshot: SurfaceSnapshot,
        candidates: Sequence[WriteOpCandidate],
        edits: Sequence[SurfaceRowEdit],
    ) -> WriteMapping:
        """Resolve the model and map. Both halves raise rather than degrade."""

        credentials = await ApiShapingCredentials.resolve(
            resolver=self.user_policies,
            org_id=str(getattr(run, "org_id", "") or ""),
            user_id=str(getattr(run, "user_id", "") or ""),
        )
        mapper = build_surface_write_mapper(
            environ=self.environ,
            run_provider=getattr(run, "model_provider", None),
            credentials=credentials,
            completion=self.completion,
        )
        return await mapper.map_edits(
            connector=snapshot.connector,
            read_op=snapshot.op,
            candidates=candidates,
            edits=edits,
        )

    async def _stage(
        self,
        *,
        run: object,
        org_id: str,
        run_id: str,
        surface: SurfaceSnapshot,
        mapping: WriteMapping,
        edits: Sequence[SurfaceRowEdit],
    ) -> StagedWriteState:
        """Emit the staged row-set through a stager with NO execution capability."""

        parked = replace(
            self.stager, policy_resolver=NeverBypassPolicy(), commit_queue=None
        )
        return await parked.stage_rowset(
            run=run,
            org_id=org_id,
            run_id=run_id,
            target_connector=surface.connector,
            target_op=mapping.op,
            rows=mapping.rows,
            agent_holds=(),
            title=self._title(surface=surface, mapping=mapping, edits=edits),
        )

    async def _candidates(
        self,
        *,
        org_id: str,
        user_id: str,
        connector: str,
        captured: CapturedSurfaceWriteOps | None,
    ) -> Sequence[WriteOpCandidate]:
        if self.write_ops is None:
            _LOGGER.warning(
                "%s write_ops_port_unwired connector=%s", _PREFIX, connector
            )
            raise WriteOpsUnavailable()
        try:
            return await self.write_ops.write_ops(
                org_id=org_id,
                user_id=user_id,
                connector=connector,
                captured=captured.ops if captured is not None else (),
                captured_connector=captured.connector if captured is not None else "",
            )
        except Exception as exc:  # noqa: BLE001 - never leak catalogue internals
            _LOGGER.warning(
                "%s write_ops_failed connector=%s error=%s",
                _PREFIX,
                connector,
                type(exc).__name__,
            )
            raise WriteOpsUnavailable() from exc

    async def _origin(
        self, *, org_id: str, run_id: str, surface_id: str
    ) -> tuple[SurfaceSnapshot, CapturedSurfaceWriteOps | None]:
        """Fold the run's ledger: THIS surface's connector + read op, and its ops.

        Server-side rather than client-declared: the connector a save writes to
        is decided by the surface the user was looking at, not by the body of
        the request. Flag off ⇒ no v2 surfaces exist ⇒ 404, nothing appended.

        Both answers come out of ONE pass over the events. The captured write
        ops were written onto the same ``surface.created`` row the origin is
        folded from, so reading them costs this path nothing — no second query,
        no MCP client, no network. Two folds rather than one projection because
        the SurfaceStore fold is served verbatim by the surfaces endpoint and
        connector op declarations have no business on that response.

        ``None`` for the capture is not an error here: it is a surface whose
        read recorded no write ops, and the refusal belongs one step later,
        where the mapper says which connector had nothing to offer.
        """

        if not SurfacesV2Flag.enabled(self.environ):
            raise SurfaceNotFound()
        list_events = getattr(self.event_store, "list_events_after", None)
        if list_events is None:
            raise SurfaceNotFound()
        events = list(await list_events(org_id=org_id, run_id=run_id, after_sequence=0))
        state = SurfaceStoreProjection.fold(run_id, events)
        captured = CapturedWriteOpsProjection.fold(events)
        for snapshot in state.surfaces:
            if snapshot.surface_id != surface_id:
                continue
            if not snapshot.connector or not snapshot.op:
                raise SurfaceWriteBackError(_Messages.NOT_A_CONNECTOR_SURFACE)
            return snapshot, captured.get(surface_id)
        raise SurfaceNotFound()

    async def _run_for_scope(self, *, org_id: str, user_id: str, run_id: str) -> object:
        get_run = getattr(self.persistence, "get_run", None)
        if get_run is None:
            raise RunNotFound()
        run = await get_run(org_id=org_id, run_id=run_id)
        if run is None or getattr(run, "user_id", None) != user_id:
            raise RunNotFound()
        return run

    @staticmethod
    def _title(
        *,
        surface: SurfaceSnapshot,
        mapping: WriteMapping,
        edits: Sequence[SurfaceRowEdit],
    ) -> str:
        """A human title for the staged surface: what is being written, where.

        Built from the connector, the chosen op and the edited column names —
        all of which are either connector facts or the user's own column
        labels. No cell value reaches a title, which is a ledger-visible string.
        """

        columns = edited_columns_of(edits)
        summary = ", ".join(columns[:3]) or Values.KIND_TABLE
        return (
            f"{surface.connector}{Titles.SEPARATOR}{mapping.op}"
            f"{Titles.SEPARATOR}{summary}"
        )[: Values.TITLE_MAX_LEN]


__all__ = [
    "ApiShapingCredentials",
    "ConnectorWriteOpsPort",
    "RunNotFound",
    "SurfaceNotFound",
    "SurfaceWriteBackCoordinator",
    "SurfaceWriteBackError",
    "WriteMappingError",
    "WriteOpsUnavailable",
]
