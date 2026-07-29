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

from collections.abc import Callable, Mapping, Sequence
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
        CatalogDescriptorRevision,
        LiveCapabilityCatalogGeneration,
    )
    from agent_runtime.capabilities.mcp.cards import McpServerCard
    from agent_runtime.control_plane.revision_binding import (
        RevisionBoundScope,
        RevisionResolutionHandle,
    )

from agent_runtime.execution.contracts import AgentRuntimeContext


_LOGGER = logging.getLogger(__name__)


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
    """

    def __init__(
        self,
        *,
        subject_fingerprint: str,
        run_id: str,
        resolve_inputs: Callable[[], CapabilityGenerationInputs | None],
    ) -> None:
        self._subject_fingerprint = subject_fingerprint
        self._run_id = run_id
        self._resolve_inputs = resolve_inputs

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
    W1 defined.  ``generation_source`` is the live authority the invoke seam's
    revalidation needs; it is produced here because it must be keyed to the same
    reference key and the same trusted inputs as the catalog beside it.
    """

    activation: "CapabilityActivationDecision"
    catalog: "CapabilityCatalog"
    generation_source: RunScopedCapabilityCatalogGeneration
    subject_fingerprint: str


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
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._card_source = card_source
        self._descriptor_revision_source = descriptor_revision_source
        self._environ = environ
        self._clock = clock

    def compose(
        self,
        context: AgentRuntimeContext,
        *,
        mcp_server_cards: Sequence["McpServerCard"] | None = None,
    ) -> RunCapabilityDiscovery | None:
        """Return this run's composed discovery inputs, or ``None`` to stay dark.

        ``None`` is the correct and expected answer for every deployment that
        has configured nothing, and for every configured deployment whose run
        cannot produce a bindable catalog.  It is never an error.
        """

        try:
            return self._compose(context, mcp_server_cards=mcp_server_cards)
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
        descriptor_revisions = self._descriptor_revisions(context)

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

        return RunCapabilityDiscovery(
            activation=activation,
            catalog=catalog,
            generation_source=RunScopedCapabilityCatalogGeneration(
                subject_fingerprint=subject_fingerprint,
                run_id=context.run_id,
                resolve_inputs=lambda: self._live_inputs(
                    context,
                    subject_fingerprint=subject_fingerprint,
                ),
            ),
            subject_fingerprint=subject_fingerprint,
        )

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

    def _descriptor_revisions(
        self, context: AgentRuntimeContext
    ) -> tuple["CatalogDescriptorRevision", ...]:
        if self._descriptor_revision_source is None:
            return ()
        return tuple(self._descriptor_revision_source(context))

    def _live_inputs(
        self,
        context: AgentRuntimeContext,
        *,
        subject_fingerprint: str,
    ) -> CapabilityGenerationInputs | None:
        """Re-read every keyed input as it stands *now*.

        This is what the generation source calls, and it is deliberately not a
        captured value. The connector scope is frozen for the run by contract,
        and so is the run-control snapshot, but the F8 descriptor revisions are
        not -- so a descriptor that moved mid-run changes the derived identity
        here and the shared revalidator fails the reference closed.
        """

        control = self._run_control()
        if control is None:
            return None
        _, _, task_policy_selection_ref = control
        return CapabilityGenerationInputs(
            subject_fingerprint=subject_fingerprint,
            connector_scope_revision=self._connector_scope_revision(context),
            task_policy_selection_ref=task_policy_selection_ref,
            descriptor_revisions=self._descriptor_revisions(context),
        )


__all__ = (
    "CapabilityDiscoveryComposer",
    "CapabilityDiscoveryEnvironment",
    "CapabilityGenerationInputs",
    "CatalogDescriptorRevisionSourcePort",
    "McpServerCardSnapshotPort",
    "RunCapabilityDiscovery",
    "RunScopedCapabilityCatalogGeneration",
)
