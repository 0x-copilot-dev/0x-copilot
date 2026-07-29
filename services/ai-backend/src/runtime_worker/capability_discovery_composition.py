"""The single worker-owned F3 capability-discovery assembly.

This module answers one question for one run: *may this run expose the bounded
discovery bridge, and over which authorized catalog*.  It composes pieces that
already exist -- F3.1's :class:`CapabilityActivationResolver`, F3.2's
:class:`AuthorizedCatalogBuilder`, and RB.3's
:class:`CapabilityCatalogGenerationPort` -- and decides nothing about
authorization itself.  Identity, connector scope, and permissions are still
revalidated at their existing call-time boundaries; every capability the
catalog can name is one the caller was already authorized to use, and every
inner operation still enters the Operation Gateway.

Three properties are load-bearing and are enforced structurally rather than by
convention:

*Fail dark, never fail open.*  Every unresolved input -- absent configuration,
an unresolvable run posture, no reference key, no authorized cards, a builder
refusal -- returns ``None`` from :meth:`CapabilityDiscoveryComposer.compose`.
The caller then leaves both ``RuntimeDependencies`` fields ``None``, W1's
``_capability_bridge_tools`` registers nothing, and the run stays on the
untouched pre-F3 disclosure path.  Nothing here can raise into a healthy run.

*A catalog with no entries is not worth a bridge.*  Registering the bridge is
not free: F3.9 suppresses the direct MCP card block whenever a bridge tool is
registered, so a bridge over an empty catalog would leave the model with no
route to MCP at all.  An empty projection is therefore treated exactly like a
catalog that could not be built.

*Nothing accumulates across runs.*  The reference key is derived per run, the
catalog is projected per run, and the generation source holds no catalog --
only a callable that re-reads the trusted inputs.  Two runs never share a
reference, and a ref minted in one run is meaningless in another.

The activation posture is read through the one F3 ladder, which is itself a
narrowing dial inside the shared ``FeatureMode`` vocabulary.  This module adds
no second mode vocabulary: the only value it interprets on its own is
*presence*, and only to keep the dark path from importing the discovery package
at all.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import logging
import os
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime.
    from agent_runtime.capabilities.discovery import (
        CapabilityActivationDecision,
        CapabilityCatalog,
        CapabilityCatalogGeneration,
        CapabilityDiscoveryObserver,
        CapabilityExpansionLimits,
        CapabilityReferenceMinter,
        CatalogDescriptorRevision,
        LiveCapabilityCatalogGeneration,
        RunScopedSchemaArtifactPublisher,
    )
    from agent_runtime.capabilities.mcp.cards import McpServerCard
    from agent_runtime.context.memory.summarization import OffloadWriter
    from agent_runtime.control_plane.context import RunControlBinding
    from agent_runtime.control_plane.ports import RunControlDecisionStorePort
    from agent_runtime.control_plane.revision_binding import (
        RevisionBoundScope,
        RevisionResolutionHandle,
    )

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    CapabilityBridgeComposition,
)
from runtime_worker.capability_descriptor_revisions import (
    McpRevisionResolverPort,
    RefreshableDescriptorRevisionSource,
    RunScopedDescriptorRevisions,
)


_LOGGER = logging.getLogger(__name__)

#: One meter set per worker process, created on first activated run and never
#: on the dark path.  Instruments are per-instance, so building a set per run
#: would register a duplicate instrument per run for the life of the process;
#: the meters themselves are stateless and run-agnostic, which is exactly why
#: they can be shared and why no run identity may ever be labelled onto them.
_METRICS: object | None = None


def _discovery_metrics() -> object | None:
    """Return this process's discovery meters, or ``None`` if unavailable.

    Failing to build meters is never a reason to fail a run or to change what
    the bridge registers, so every failure resolves to an unmeasured run.
    """

    global _METRICS  # noqa: PLW0603 - one process-wide meter set, by design.
    if _METRICS is not None:
        return _METRICS
    try:
        from agent_runtime.capabilities.discovery.telemetry import (  # noqa: PLC0415
            CapabilityDiscoveryMetrics,
        )

        _METRICS = CapabilityDiscoveryMetrics()
    except Exception:
        _LOGGER.debug("capability_discovery.metrics_unavailable", exc_info=True)
        return None
    return _METRICS


class CapabilityDiscoveryEnvironment:
    """Bounded, conservative environment parsing for the worker-only F3 lane.

    Unlike the F8 assembly next door, nothing here raises on a malformed value.
    F3's own rule is that an absent, unknown, or unparseable control resolves to
    the *narrowest* posture, so a typo can only remove the new discovery path.
    Raising would turn a configuration typo into a failed run, which is the one
    thing a dark feature must never do.
    """

    ACTIVATION = "F3_CAPABILITY_ACTIVATION"
    CATALOG_TTL_SECONDS = "F3_CAPABILITY_CATALOG_TTL_SECONDS"
    REFERENCE_SECRET = "ENTERPRISE_AUTH_SECRET"
    ENVIRONMENT = "RUNTIME_ENVIRONMENT"

    DEFAULT_TTL_SECONDS = 900.0
    MAX_TTL_SECONDS = 3600.0
    MIN_SECRET_BYTES = 16

    # Domain separation keeps a key derived here from colliding with any other
    # use of the same deployment secret.
    REFERENCE_KEY_PURPOSE = "f3-capability-reference-key-v1"
    CONNECTOR_SCOPE_PURPOSE = "f3-connector-scope-v1"
    _DEVELOPMENT_SECRET = b"dev-f3-capability-reference-key-v1"

    @classmethod
    def raw_activation(cls, environ: Mapping[str, str] | None = None) -> str | None:
        """Return the operator's configured activation string, or ``None``.

        The value is handed to the F3 resolver unparsed.  Reading it here is
        transport, not policy: this module never decides what a given string
        means.
        """

        source = environ if environ is not None else os.environ
        raw = source.get(cls.ACTIVATION, "").strip()
        return raw or None

    @classmethod
    def is_configured(cls, environ: Mapping[str, str] | None = None) -> bool:
        """Return whether an operator has configured F3 activation at all.

        This is the one cheap pre-gate the dependency factory reads before it
        imports anything F3-owned.  It tests *presence*, never meaning, so it
        cannot drift into a second activation vocabulary: any value that is
        present -- including a misspelling -- still goes through the real
        resolver and still resolves conservatively.
        """

        return cls.raw_activation(environ) is not None

    @classmethod
    def catalog_ttl_seconds(cls, environ: Mapping[str, str] | None = None) -> float:
        """Return the catalog lifetime, defaulting on anything invalid.

        A missing, blank, non-numeric, or out-of-range value resolves to the
        conservative default rather than to the ceiling, matching how every
        other F3 bound is read.
        """

        source = environ if environ is not None else os.environ
        raw = source.get(cls.CATALOG_TTL_SECONDS, "").strip()
        if not raw:
            return cls.DEFAULT_TTL_SECONDS
        try:
            value = float(raw)
        except ValueError:
            return cls.DEFAULT_TTL_SECONDS
        if value <= 0 or value > cls.MAX_TTL_SECONDS:
            return cls.DEFAULT_TTL_SECONDS
        return value

    @classmethod
    def reference_key(
        cls,
        *,
        run_id: str,
        environ: Mapping[str, str] | None = None,
    ) -> bytes | None:
        """Derive this run's 32-byte F3 reference key, or ``None`` to stay dark.

        The key is derived per run from the deployment secret the service
        already requires, so two independent compositions of the *same* run mint
        byte-identical references -- which is what lets the generation source
        recompute an identity rather than replay one -- while a reference minted
        in one run is meaningless in every other run.

        Production with no configured secret fails dark rather than falling back
        to a shared development key.
        """

        source = environ if environ is not None else os.environ
        raw = (source.get(cls.REFERENCE_SECRET) or "").strip()
        environment = (source.get(cls.ENVIRONMENT) or "development").strip().lower()
        if not raw:
            if environment == "production":
                return None
            secret = cls._DEVELOPMENT_SECRET
        else:
            # Desktop provisions this value as hex; hosted deployments may use
            # an arbitrary high-entropy string. Both stay stable across restarts.
            try:
                secret = bytes.fromhex(raw)
            except ValueError:
                secret = raw.encode("utf-8")
        if len(secret) < cls.MIN_SECRET_BYTES:
            return None
        payload = f"{cls.REFERENCE_KEY_PURPOSE}:{run_id}".encode()
        return hmac.new(secret, payload, hashlib.sha256).digest()


@runtime_checkable
class McpServerCardSnapshotPort(Protocol):
    """A synchronous snapshot of the MCP server cards this run may see.

    The catalog body is authorized MCP server cards, and the live registry lists
    them asynchronously, but ``RuntimeDependenciesFactory`` is synchronous by
    contract.  This port is the seam through which an already-awaited, already
    permission-filtered card snapshot reaches the composition.  It is
    deliberately not a loader: nothing here performs I/O, and an implementation
    that blocked would block the worker's event loop.
    """

    def __call__(self, context: AgentRuntimeContext) -> Sequence["McpServerCard"]: ...


@runtime_checkable
class CatalogDescriptorRevisionSourcePort(Protocol):
    """The F8 descriptor revisions currently folded into a catalog generation.

    Read *every* time a generation is derived, never captured once: recomputing
    is the whole reason a stale reference can be detected mid-run.
    """

    def __call__(
        self, context: AgentRuntimeContext
    ) -> Sequence["CatalogDescriptorRevision"]: ...


@dataclass(frozen=True, slots=True)
class CapabilityGenerationInputs:
    """The four trusted inputs one catalog generation is keyed to.

    Grouping them makes the ordering requirement explicit and checkable: a
    generation cannot be derived before the verified subject, the connector
    scope revision, the F4 task-policy selection, and the F8 descriptor
    revisions are all known.
    """

    subject_fingerprint: str
    connector_scope_revision: str
    task_policy_selection_ref: str
    descriptor_revisions: tuple["CatalogDescriptorRevision", ...] = ()

    def generation(self) -> "CapabilityCatalogGeneration":
        """Derive the one reproducible identity for these inputs."""

        from agent_runtime.capabilities.discovery import (  # noqa: PLC0415
            CapabilityCatalogGeneration,
        )

        return CapabilityCatalogGeneration.create(
            subject_fingerprint=self.subject_fingerprint,
            connector_scope_revision=self.connector_scope_revision,
            task_policy_selection_ref=self.task_policy_selection_ref,
            descriptor_revisions=self.descriptor_revisions,
        )


class RunScopedCapabilityCatalogGeneration:
    """The production ``CapabilityCatalogGenerationPort`` for one bound run.

    It answers exactly one question -- what catalog generation is authoritative
    *now* for this scope -- and it answers it by **recomputing** the identity
    from freshly re-read trusted inputs.  It deliberately holds no catalog and
    no previously derived generation: RB.3 recorded why, and it is the whole
    point of the adapter.  An authority that answered from the snapshot under
    test would be validating that snapshot against itself, and every reference
    would look current forever.

    It never compares generations, never inspects the generation a reference was
    minted against, and never widens a scope.  Comparison belongs to the shared
    revalidator.  The one check performed here is a *narrowing* one: a scope this
    source does not own is answered ``unknown`` rather than resolved.

    ``refresh`` is what makes "recompute" mean something.  Three of the four
    keyed inputs are frozen for the run by contract, so the F8 descriptor
    revisions are the only one that can move -- and they can only move if
    something re-reads them.  Awaiting the refresh here rather than at the
    composition root is the whole difference between an authority that can
    report a reference stale mid-run and one that re-derives the same answer
    forever.
    """

    def __init__(
        self,
        *,
        subject_fingerprint: str,
        run_id: str,
        resolve_inputs: Callable[[], CapabilityGenerationInputs | None],
        refresh: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._subject_fingerprint = subject_fingerprint
        self._run_id = run_id
        self._resolve_inputs = resolve_inputs
        self._refresh = refresh

    async def live_generation(
        self,
        *,
        scope: "RevisionBoundScope",
        resolution_handle: "RevisionResolutionHandle | None" = None,
    ) -> "LiveCapabilityCatalogGeneration":
        """Return the generation that is authoritative for ``scope`` right now."""

        from agent_runtime.capabilities.discovery import (  # noqa: PLC0415
            CapabilityCatalogIdentityError,
            LiveCapabilityCatalogGeneration,
        )
        from agent_runtime.control_plane.revision_binding import (  # noqa: PLC0415
            RevisionAuthorityState,
        )

        # The handle is a resolution *key* for sources that must ask a backend
        # keyed by an identity the fingerprint destroyed. This source rebuilds
        # from its own bound run, so it never reads freshness out of it.
        del resolution_handle

        if scope.subject_fingerprint != self._subject_fingerprint:
            return LiveCapabilityCatalogGeneration.for_state(
                RevisionAuthorityState.UNKNOWN
            )
        if scope.run_id is not None and scope.run_id != self._run_id:
            return LiveCapabilityCatalogGeneration.for_state(
                RevisionAuthorityState.UNKNOWN
            )
        if self._refresh is not None:
            try:
                await self._refresh()
            except Exception:
                # The refresh itself is total, so reaching here means the
                # authority behind it is in a state this source cannot describe.
                # Answering from the stale snapshot would report a catalog that
                # may already have moved as current, so refuse instead.
                _LOGGER.warning(
                    "Catalog descriptor revisions could not be re-read; "
                    "reporting the scope unavailable.",
                    exc_info=True,
                )
                return LiveCapabilityCatalogGeneration.for_state(
                    RevisionAuthorityState.UNAVAILABLE
                )
        try:
            current = self._resolve_inputs()
        except Exception:
            _LOGGER.warning(
                "Capability catalog generation inputs are unresolvable; "
                "reporting the scope unavailable.",
                exc_info=True,
            )
            return LiveCapabilityCatalogGeneration.for_state(
                RevisionAuthorityState.UNAVAILABLE
            )
        if current is None:
            return LiveCapabilityCatalogGeneration.for_state(
                RevisionAuthorityState.UNAVAILABLE
            )
        if current.subject_fingerprint != self._subject_fingerprint:
            # The bound subject no longer projects this catalog. Nothing about
            # the reference is knowable from here; refuse rather than resolve.
            return LiveCapabilityCatalogGeneration.for_state(
                RevisionAuthorityState.UNKNOWN
            )
        try:
            return LiveCapabilityCatalogGeneration.active(current.generation())
        except (CapabilityCatalogIdentityError, ValueError):
            return LiveCapabilityCatalogGeneration.for_state(
                RevisionAuthorityState.UNAVAILABLE
            )


@dataclass(frozen=True, slots=True)
class RunCapabilityDiscovery:
    """Everything one run's F3 composition produced, or nothing at all.

    ``activation`` and ``catalog`` are the two ``RuntimeDependencies`` fields
    W1 defined, and ``bridge`` is the third: the invocation-seam inputs only a
    composition root can supply.  ``generation_source`` is the live authority
    the invoke seam's revalidation needs; it is produced here because it must be
    keyed to the same reference key and the same trusted inputs as the catalog
    beside it.

    ``bridge`` is built here rather than by the caller for the same reason.  Its
    minter, its revalidation, and its schema-artifact publisher are all keyed to
    the one ``reference_key`` this run derived, and that key never leaves
    :meth:`CapabilityDiscoveryComposer._compose`.  Handing the caller the key so
    it could build them would make "the bridge and the catalog agree on a key"
    an instruction; deriving both from one local makes it a fact.
    """

    activation: "CapabilityActivationDecision"
    catalog: "CapabilityCatalog"
    generation_source: RunScopedCapabilityCatalogGeneration
    subject_fingerprint: str
    bridge: CapabilityBridgeComposition = CapabilityBridgeComposition()


class CapabilityDiscoveryComposer:
    """Compose one run's F3 activation, authorized catalog, and live authority.

    The composition is pure and cheap: it performs no I/O, opens no connection,
    and starts no task.  Everything it needs that *would* require I/O -- the
    authorized MCP server cards, the F8 descriptor revisions -- arrives through
    an injected synchronous snapshot port, because the runtime dependency
    factory is synchronous by contract.
    """

    def __init__(
        self,
        *,
        card_source: McpServerCardSnapshotPort | None = None,
        descriptor_revision_source: CatalogDescriptorRevisionSourcePort | None = None,
        descriptor_revision_resolver: McpRevisionResolverPort | None = None,
        decision_store: "RunControlDecisionStorePort | None" = None,
        schema_artifact_writer: "OffloadWriter | None" = None,
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._card_source = card_source
        self._descriptor_revision_source = descriptor_revision_source
        # The F8 revision authority the worker process already owns. It is the
        # same resolver the MCP loader consults, so this reads an answer the
        # deployment already maintains rather than introducing a second one.
        # ``None`` -- every deployment with F8 unconfigured -- folds zero
        # revisions, exactly as this module did before it was threaded.
        self._descriptor_revision_resolver = descriptor_revision_resolver
        # Both are optional in the way every other input here is. Without a
        # decision store the run is unmeasured; without a writer an over-bound
        # schema is reported unavailable. Neither changes what the bridge does,
        # and neither can fail a run.
        self._decision_store = decision_store
        self._schema_artifact_writer = schema_artifact_writer
        self._environ = environ
        self._clock = clock

    async def acompose(
        self,
        context: AgentRuntimeContext,
        *,
        mcp_server_cards: Sequence["McpServerCard"] | None = None,
    ) -> RunCapabilityDiscovery | None:
        """Compose with this run's F8 descriptor revisions resolved first.

        This is the entry point the worker's async composition root uses, and
        the ordering it enforces is the point: the revisions are read *before*
        the catalog is projected, so the generation the catalog is stamped with
        and the generation the live authority recomputes are keyed to the same
        four inputs.  Reading them afterwards would stamp a catalog with a
        generation nothing could ever reproduce.

        The synchronous :meth:`compose` remains exactly what it was for callers
        that have no revision authority to await.
        """

        source = self._revision_source(context, mcp_server_cards)
        if source is not None:
            try:
                await source.refresh()
            except Exception:  # pragma: no cover - refresh is total.
                _LOGGER.warning(
                    "Catalog descriptor revisions are unavailable; "
                    "composing without them.",
                    exc_info=True,
                )
        return self.compose(
            context,
            mcp_server_cards=mcp_server_cards,
            descriptor_revision_source=source,
        )

    def compose(
        self,
        context: AgentRuntimeContext,
        *,
        mcp_server_cards: Sequence["McpServerCard"] | None = None,
        descriptor_revision_source: CatalogDescriptorRevisionSourcePort | None = None,
    ) -> RunCapabilityDiscovery | None:
        """Return this run's composed discovery inputs, or ``None`` to stay dark.

        ``None`` is the correct and expected answer for every deployment that
        has configured nothing, and for every configured deployment whose run
        cannot produce a bindable catalog.  It is never an error.

        ``descriptor_revision_source`` is the run-scoped view :meth:`acompose`
        already awaited.  It is passed rather than stored because the composer
        is process-scoped and the view is not: a source cached on the instance
        would leak one run's server set into the next run's generation.
        """

        try:
            return self._compose(
                context,
                mcp_server_cards=mcp_server_cards,
                descriptor_revision_source=descriptor_revision_source,
            )
        except Exception:
            # A dark feature must never fail an otherwise healthy run, and it
            # must never widen a tool surface on the way down.
            _LOGGER.warning(
                "Capability discovery composition failed; "
                "keeping the pre-F3 disclosure path.",
                exc_info=True,
            )
            return None

    def _compose(
        self,
        context: AgentRuntimeContext,
        *,
        mcp_server_cards: Sequence["McpServerCard"] | None,
        descriptor_revision_source: CatalogDescriptorRevisionSourcePort | None = None,
    ) -> RunCapabilityDiscovery | None:
        from agent_runtime.capabilities.discovery import (  # noqa: PLC0415
            AuthorizedCatalogBuilder,
            CapabilityCatalogScope,
        )

        activation = self._activation()
        if not activation.registers_bridge:
            # ``direct``, ``server``, and ``shadow`` all keep the pre-F3
            # disclosure path. Supplying inputs the registrar would discard
            # would only make the dark path's shape harder to reason about.
            return None

        # Order is the point, not a convenience: the generation is keyed to the
        # verified subject, the connector scope, the F4 selection, and the F8
        # revisions, so none of them may be read after the catalog is projected.
        control = self._run_control()
        if control is None:
            return None
        snapshot_profile, policy_revision, task_policy_selection_ref = control

        reference_key = CapabilityDiscoveryEnvironment.reference_key(
            run_id=context.run_id,
            environ=self._environ,
        )
        if reference_key is None:
            return None

        builder = AuthorizedCatalogBuilder(reference_key=reference_key)
        subject_fingerprint = builder.subject_fingerprint(context)
        connector_scope_revision = self._connector_scope_revision(context)
        revision_source = descriptor_revision_source or self._descriptor_revision_source
        descriptor_revisions = self._descriptor_revisions(context, revision_source)

        cards = (
            tuple(mcp_server_cards)
            if mcp_server_cards is not None
            else self._cards(context)
        )
        if not cards:
            # Nothing authorized to index. Registering the bridge anyway would
            # suppress the direct MCP card block (F3.9) and leave the model with
            # no route to MCP at all, so this narrows to the pre-F3 path.
            return None

        ttl = CapabilityDiscoveryEnvironment.catalog_ttl_seconds(self._environ)
        catalog = builder.build(
            context=context,
            scope=CapabilityCatalogScope.from_context(
                context,
                profile_id=snapshot_profile,
                policy_revision=policy_revision,
                connector_scope_revision=connector_scope_revision,
            ),
            task_policy_selection_ref=task_policy_selection_ref,
            mcp_server_cards=cards,
            descriptor_revisions=descriptor_revisions,
            expires_at=self._clock() + timedelta(seconds=ttl),
        )
        if not catalog.entries:
            # Every card was filtered by the builder's defensive permission
            # recheck. Same reasoning as an empty card snapshot.
            return None
        if catalog.generation is None:  # pragma: no cover - builder always stamps.
            return None

        generation_source = RunScopedCapabilityCatalogGeneration(
            subject_fingerprint=subject_fingerprint,
            run_id=context.run_id,
            resolve_inputs=lambda: self._live_inputs(
                context,
                subject_fingerprint=subject_fingerprint,
                revision_source=revision_source,
            ),
            # Only a source that can be re-read makes "live" mean anything. One
            # that cannot is left alone rather than wrapped, so a deployment
            # with no F8 authority behaves exactly as it did.
            refresh=(
                revision_source.refresh
                if isinstance(revision_source, RefreshableDescriptorRevisionSource)
                else None
            ),
        )
        return RunCapabilityDiscovery(
            activation=activation,
            catalog=catalog,
            generation_source=generation_source,
            subject_fingerprint=subject_fingerprint,
            bridge=self._bridge(
                context,
                reference_key=reference_key,
                subject_fingerprint=subject_fingerprint,
                generation_source=generation_source,
            ),
        )

    def _bridge(
        self,
        context: AgentRuntimeContext,
        *,
        reference_key: bytes,
        subject_fingerprint: str,
        generation_source: RunScopedCapabilityCatalogGeneration,
    ) -> CapabilityBridgeComposition:
        """Assemble the invocation-seam inputs keyed to this run's own key.

        Every part is built from the single ``reference_key`` the catalog beside
        it was projected under, and that key exists in exactly one place — the
        local in :meth:`_compose`.  There is no second derivation to keep in
        step, so an expanded reference is byte-identical to a catalog member's
        by construction rather than by agreement.

        The whole assembly degrades one part at a time.  A missing decision
        store costs the run its journal lineage, a missing writer costs it the
        protected-schema answer, and an unbuildable revalidation costs it
        ``invoke_capability`` entirely — none of them costs it the bridge, and
        none of them raises.
        """

        from agent_runtime.capabilities.discovery import (  # noqa: PLC0415
            HmacCapabilityReferenceMinter,
        )

        minter: CapabilityReferenceMinter = HmacCapabilityReferenceMinter(
            reference_key=reference_key
        )
        metrics = _discovery_metrics()
        return CapabilityBridgeComposition(
            minter=minter,
            revalidation=self._revalidation(
                subject_fingerprint=subject_fingerprint,
                generation_source=generation_source,
            ),
            observer=self._observer(
                context,
                subject_fingerprint=subject_fingerprint,
                metrics=metrics,
            ),
            expansion_observer=metrics,
            schema_artifacts=self._schema_artifacts(minter),
            expansion_limits=self._expansion_limits(),
        )

    def _expansion_limits(self) -> "CapabilityExpansionLimits | None":
        """Resolve the operator's second-tier bounds, or ``None`` on any doubt.

        The knobs are documented on
        :class:`~agent_runtime.capabilities.discovery.activation.CapabilityExpansionLimits`
        and were readable there from the start; what was missing was a caller.
        This is it, and it is deliberately the only one — a second reader would
        be a second effective ``K``.

        Resolution is delegated whole rather than reimplemented, so the contract's
        own rule stands: a missing, blank, non-numeric, or out-of-range value
        resolves to the conservative default and never to the ceiling.  A typo
        therefore cannot raise fan-out or the deadline, and ``None`` -- returned
        only if the contract itself could not be built -- leaves the expander on
        the same hard defaults it used before this was threaded.
        """

        from agent_runtime.capabilities.discovery import (  # noqa: PLC0415
            CapabilityExpansionLimits,
        )

        try:
            return CapabilityExpansionLimits.from_environment(self._environ)
        except Exception:  # pragma: no cover - resolution defaults, never raises.
            _LOGGER.warning(
                "Capability expansion limits could not be resolved; "
                "keeping the bounded defaults.",
                exc_info=True,
            )
            return None

    @staticmethod
    def _revalidation(
        *,
        subject_fingerprint: str,
        generation_source: RunScopedCapabilityCatalogGeneration,
    ) -> object | None:
        """Bind the shared Step RB revalidator to this run's live authority.

        The authority recomputes a generation from freshly re-read trusted
        inputs, so this is what makes a reference minted before a descriptor
        moved fail closed at use time.  Without it the registrar refuses to
        offer ``invoke_capability`` at all, which is the correct narrowing: an
        unrevalidatable reference is never usable.
        """

        from agent_runtime.capabilities.discovery import (  # noqa: PLC0415
            CapabilityCatalogRevisionAuthority,
            CapabilityRefRevalidation,
        )
        from agent_runtime.control_plane.revision_binding import (  # noqa: PLC0415
            RevisionBindingRevalidator,
        )

        try:
            return CapabilityRefRevalidation(
                revalidator=RevisionBindingRevalidator(
                    CapabilityCatalogRevisionAuthority(generation_source)
                ),
                subject_fingerprint=subject_fingerprint,
            )
        except Exception:
            _LOGGER.warning(
                "Capability ref revalidation could not be composed; "
                "invoke_capability stays unregistered.",
                exc_info=True,
            )
            return None

    def _observer(
        self,
        context: AgentRuntimeContext,
        *,
        subject_fingerprint: str,
        metrics: object | None,
    ) -> object | None:
        """Fan discovery decisions out to the run journal and to the meters.

        The recorder's binding is read from the verified run-control snapshot
        rather than from this composition: the journal is partitioned by the
        *control* subject fingerprint, and F3's catalog-keyed fingerprint is a
        different derivation for a different purpose.  Using the wrong one would
        write rows no reader of that run could ever list.

        ``subject_fingerprint`` is still taken as a parameter so the caller
        cannot silently pass F3's; it is used only when the snapshot carries
        none, which no verified binding ever does.
        """

        from agent_runtime.capabilities.discovery.telemetry import (  # noqa: PLC0415
            CapabilityDiscoveryObserverGroup,
            RunJournalDiscoveryDecisionRecorder,
        )

        observers: list[CapabilityDiscoveryObserver] = []
        binding = self._control_binding()
        store = self._decision_store
        if store is not None and binding is not None:
            snapshot = binding.snapshot
            observers.append(
                RunJournalDiscoveryDecisionRecorder(
                    store=store,
                    org_id=context.org_id,
                    run_id=context.run_id,
                    trace_id=context.trace_id,
                    subject_fingerprint=(
                        snapshot.subject_fingerprint or subject_fingerprint
                    ),
                    snapshot_id=snapshot.snapshot_id,
                    policy_revision=snapshot.policy_revisions.capability,
                    clock=self._clock,
                )
            )
        if metrics is not None:
            observers.append(metrics)  # type: ignore[arg-type]
        if not observers:
            return None
        return CapabilityDiscoveryObserverGroup(observers=tuple(observers))

    def _schema_artifacts(
        self,
        minter: "CapabilityReferenceMinter",
    ) -> "RunScopedSchemaArtifactPublisher | None":
        """Publish over-bound schemas through the runtime's existing offload seam.

        The publisher takes the *same* minter object the catalog's references
        came from, not a second one built from the same bytes.  Its derivation
        folds in the binding digest, so an artifact reference is a function of
        the run, subject, and catalog generation — and a run handed a foreign
        key would mint references its own resolver re-derives differently and
        therefore refuses.
        """

        writer = self._schema_artifact_writer
        if writer is None:
            return None
        from agent_runtime.capabilities.discovery import (  # noqa: PLC0415
            RunScopedSchemaArtifactPublisher,
        )

        return RunScopedSchemaArtifactPublisher(
            writer=writer,
            minter=minter,  # type: ignore[arg-type]
        )

    @staticmethod
    def _control_binding() -> "RunControlBinding | None":
        from agent_runtime.control_plane.context import (  # noqa: PLC0415
            RunControlContext,
        )

        return RunControlContext.current()

    def _activation(self) -> "CapabilityActivationDecision":
        """Resolve the posture from deployment config and the run's own mode.

        The feature mode is read off the verified run-control binding rather
        than from configuration, so the immutable snapshot (already narrowed by
        any live constraint or kill switch) remains the hard ceiling. With no
        bound run there is no posture to read, the mode defaults ``off``, and
        the ceiling forbids anything but the pre-F3 path.
        """

        from agent_runtime.capabilities.discovery import (  # noqa: PLC0415
            CapabilityActivationResolver,
        )
        from agent_runtime.control_plane.context import (  # noqa: PLC0415
            RunControlContext,
        )
        from agent_runtime.control_plane.feature_modes import (  # noqa: PLC0415
            AgentQualityFeature,
        )

        binding = RunControlContext.current()
        raw_mode = (
            binding.mode_for(AgentQualityFeature.F3_CAPABILITY_DISCOVERY).value
            if binding is not None
            else None
        )
        return CapabilityActivationResolver().resolve_configured(
            raw_mode=raw_mode,
            raw_activation=CapabilityDiscoveryEnvironment.raw_activation(self._environ),
        )

    @staticmethod
    def _run_control() -> tuple[str, str, str] | None:
        """Return ``(profile_id, policy_revision, task_policy_selection_ref)``.

        All three come from the verified, immutable run-control snapshot. The
        capability policy revision and the F4 task-policy selection are read
        from where they are already authoritative; nothing is invented here.
        """

        from agent_runtime.control_plane.context import (  # noqa: PLC0415
            RunControlContext,
        )

        binding = RunControlContext.current()
        if binding is None:
            return None
        snapshot = binding.snapshot
        return (
            snapshot.deployment_profile,
            snapshot.policy_revisions.capability,
            snapshot.task_policy_selection_ref,
        )

    @staticmethod
    def _connector_scope_revision(context: AgentRuntimeContext) -> str:
        """Digest the run's frozen connector scope into one comparable revision.

        Every input is already-verified run context: the granted per-connector
        scopes, the connectors the user paused for this chat, and the durable
        per-connector access modes. The value is compared for equality only --
        it asserts *which* scope a catalog was projected under, never that one
        scope is newer than another.
        """

        from agent_runtime.surfaces_v2.canonical_json import (  # noqa: PLC0415
            canonical_json_sha256,
        )

        digest = canonical_json_sha256(
            {
                "purpose": CapabilityDiscoveryEnvironment.CONNECTOR_SCOPE_PURPOSE,
                "connector_scopes": {
                    connector: sorted(scopes)
                    for connector, scopes in sorted(context.connector_scopes.items())
                },
                "paused_connectors": sorted(context.paused_connectors),
                "connector_access_modes": {
                    connector: mode.value
                    for connector, mode in sorted(
                        context.connector_access_modes.items()
                    )
                },
            }
        )
        return f"connector-scope://sha256/{digest}"

    def _cards(self, context: AgentRuntimeContext) -> tuple["McpServerCard", ...]:
        if self._card_source is None:
            return ()
        return tuple(self._card_source(context))

    def _revision_source(
        self,
        context: AgentRuntimeContext,
        mcp_server_cards: Sequence["McpServerCard"] | None,
    ) -> RunScopedDescriptorRevisions | None:
        """Build this run's re-readable F8 revision view, or ``None``.

        The view is keyed to the cards the run was already authorized to see, so
        it can only ever report revisions for servers the catalog itself indexes.
        Building it performs no I/O; :meth:`acompose` awaits the first read.
        """

        if mcp_server_cards is None:
            return None
        return RunScopedDescriptorRevisions.for_cards(
            resolver=self._descriptor_revision_resolver,
            context=context,
            cards=tuple(mcp_server_cards),
        )

    @staticmethod
    def _descriptor_revisions(
        context: AgentRuntimeContext,
        source: CatalogDescriptorRevisionSourcePort | None,
    ) -> tuple["CatalogDescriptorRevision", ...]:
        if source is None:
            return ()
        return tuple(source(context))

    def _live_inputs(
        self,
        context: AgentRuntimeContext,
        *,
        subject_fingerprint: str,
        revision_source: CatalogDescriptorRevisionSourcePort | None = None,
    ) -> CapabilityGenerationInputs | None:
        """Re-read every keyed input as it stands *now*.

        This is what the generation source calls, and it is deliberately not a
        captured value. The connector scope is frozen for the run by contract,
        and so is the run-control snapshot, but the F8 descriptor revisions are
        not -- so a descriptor that moved mid-run changes the derived identity
        here and the shared revalidator fails the reference closed.

        ``revision_source`` is the run's own view rather than the composer's
        instance field for the same reason :meth:`compose` takes it as an
        argument: the composer outlives the run, and a view that outlived it with
        it would key one run's generation to another run's server set.
        """

        control = self._run_control()
        if control is None:
            return None
        _, _, task_policy_selection_ref = control
        return CapabilityGenerationInputs(
            subject_fingerprint=subject_fingerprint,
            connector_scope_revision=self._connector_scope_revision(context),
            task_policy_selection_ref=task_policy_selection_ref,
            descriptor_revisions=self._descriptor_revisions(
                context,
                revision_source or self._descriptor_revision_source,
            ),
        )


def build_capability_discovery_composer(
    *,
    decision_store: object | None = None,
    schema_artifact_writer: object | None = None,
    descriptor_revision_resolver: object | None = None,
) -> CapabilityDiscoveryComposer | None:
    """Build the worker's fully-wired composer, or ``None`` to stay dark.

    The presence gate is read *here*, before the composer is constructed, and
    that placement is the whole point.  A handler that always held a composer
    would run the real composition on every run of every deployment — importing
    the discovery package, resolving a posture, deriving a key — only to discard
    the result, and the dark path's import graph would no longer be the pre-F3
    one.  Returning ``None`` keeps the unconfigured deployment on the branch
    that has always existed.

    Testing *presence* rather than meaning is deliberate and is not a second
    activation vocabulary: any value that is present, including a misspelling,
    still goes through the one F3 resolver and still resolves conservatively.
    Both handlers call this rather than constructing a composer of their own, so
    the run and approval-resume paths cannot drift into wiring F3 differently.

    ``descriptor_revision_resolver`` is the worker process's one F8 revision
    authority, threaded exactly as ``mcp_discovery_cache`` already is and for the
    same reason: it is per-process state that only ``__main__`` can build.  It is
    ``None`` on every deployment with F8 unconfigured, and the composer then
    folds no revisions -- the behaviour that shipped.
    """

    if not CapabilityDiscoveryEnvironment.is_configured():
        return None
    return CapabilityDiscoveryComposer(
        decision_store=decision_store,  # type: ignore[arg-type]
        schema_artifact_writer=schema_artifact_writer,  # type: ignore[arg-type]
        descriptor_revision_resolver=descriptor_revision_resolver,  # type: ignore[arg-type]
    )


__all__ = (
    "CapabilityDiscoveryComposer",
    "CapabilityDiscoveryEnvironment",
    "CapabilityGenerationInputs",
    "CatalogDescriptorRevisionSourcePort",
    "McpServerCardSnapshotPort",
    "RunCapabilityDiscovery",
    "RunScopedCapabilityCatalogGeneration",
    "build_capability_discovery_composer",
)
