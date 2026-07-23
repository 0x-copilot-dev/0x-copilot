"""ViewDeriver — explicit, auditable per-surface view state (PRD-B3, SDR §3/§7 S5).

v1 encoded view tier implicitly ("spec absent ⇒ tier-3"). v2 makes it explicit
ledger state: every surface's view is a ``view.derived`` event carrying a
``tier`` (raw / generic / shaped) and a ``basis`` (schema / registry / generated).
This module owns the two transitions that produce those events as a pure function
of the **stored** tool response — never a re-fetch:

* :meth:`ViewDeriver.derive` runs the honest ladder on the read path — registry
  hit ⇒ ``shaped`` / ``registry`` immediately; miss on a structured payload ⇒
  ``generic`` / ``schema`` *now* (never blocking on shaping) then a bounded
  background shape attempt via the reused ``SurfaceGenerationScheduler``; a
  non-mapping payload ⇒ ``raw`` / ``schema`` (B2's lossless fallback).
* :meth:`ViewDeriver.regenerate` is the user-invited "Looks wrong? Regenerate"
  (FR-A6) — it re-derives from the same stored payload with **zero** new
  connector traffic (it never touches the MCP client), bounded by a per-surface
  cap, metered per attempt (``purpose: view_shaping``, ``surface_id`` set).

The v1 generation subsystem (redaction, lint, injection kill-switch, retry) is
reused underneath unchanged (``capabilities/surfaces/generator``). This module is
a clean sibling of the A1/A3 contracts: it emits through the injected
:data:`EmitFn` closure and never imports ``runtime_api``.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar

from agent_runtime.capabilities.surfaces import builtin
from agent_runtime.capabilities.surfaces.generator import (
    GenFailure,
    GenToolDescriptor,
    SurfaceGenerationScheduler,
    SurfaceSpecGenerator,
)
from agent_runtime.capabilities.surfaces.shape_hash import output_shape_hash
from agent_runtime.capabilities.surfaces.spec_models import SurfaceSpec
from agent_runtime.capabilities.surfaces.store import (
    SpecKey,
    StoredSpec,
    SurfaceSpecStorePort,
)
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.observability.usage_meter import MeteredModelInvocation
from agent_runtime.surfaces_v2.constants import Keys, Messages, Values
from agent_runtime.surfaces_v2.emitter import EmitFn
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType, ViewBasis, ViewTier


class _Limits:
    """Bounds the deriver enforces (auditable constants, not magic numbers)."""

    # Max user-invited regenerations per surface — a cost + abuse bound. The
    # ledger fold (not mutable state) counts prior non-first, non-registry
    # ``view.derived`` events for a surface; at the cap the endpoint 409s.
    MAX_REGEN_PER_SURFACE: ClassVar[int] = 3


class ViewGenInfo(RuntimeContract):
    """The ``gen`` block of a shaped/generated ``view.derived`` (SDR §5)."""

    model: str
    ms: int


class ViewDerivation(RuntimeContract):
    """The derived view returned to callers and shipped as the event payload.

    A pure description of a surface's view state — no side effects. The deriver
    emits the matching ``view.derived`` event before returning this.
    """

    surface_id: str
    tier: ViewTier
    basis: ViewBasis
    spec_ref: str | None = None
    gen: ViewGenInfo | None = None


class ViewDeriverError(Exception):
    """Base for typed deriver errors. Carries only a safe public message."""


class RegenerateLimitError(ViewDeriverError):
    """The per-surface regenerate cap has been reached (maps to HTTP 409)."""


class _Messages:
    """Safe, actionable messages surfaced through the typed errors above."""

    SURFACE_NOT_FOUND = "surface_not_found"
    REGENERATE_LIMIT_REACHED = "regenerate_limit_reached"


class _SurfaceScopedInvocation:
    """A :class:`MeteredModelInvocation` bound to one ``surface_id``.

    The reused generator meters per attempt via ``record_attempt(...)`` but never
    passes ``surface_id`` (it shapes an output shape, not a concrete surface). On
    the regenerate path the surface is known, and the DoD requires every shaping
    usage row to carry it. This thin adapter forwards to the real invocation with
    the bound ``surface_id`` injected — duck-typed to the generator's
    ``usage_meter`` seam so the shared generator is untouched.
    """

    def __init__(self, *, invocation: MeteredModelInvocation, surface_id: str) -> None:
        self._invocation = invocation
        self._surface_id = surface_id

    async def record_attempt(
        self,
        *,
        model_id: str,
        input_tokens: int | None,
        output_tokens: int | None,
        duration_ms: int,
        surface_id: str | None = None,
    ) -> None:
        await self._invocation.record_attempt(
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
            surface_id=surface_id or self._surface_id,
        )


@dataclass(frozen=True)
class ViewDeriver:
    """Derive + regenerate per-surface view state (see the module docstring)."""

    store: SurfaceSpecStorePort
    emit: EmitFn
    generator: SurfaceSpecGenerator | None = None
    scheduler: SurfaceGenerationScheduler | None = None
    # The shaping model id backing ``generator`` — used for ``StoredSpec`` and the
    # ``gen.model`` provenance block. ``None`` when no generator is configured.
    model_id: str | None = None

    # -- derive (read path) -------------------------------------------------

    async def derive(
        self,
        *,
        surface_id: str,
        server: str,
        tool: str,
        payload: object,
        tool_descriptor: GenToolDescriptor | None = None,
    ) -> ViewDerivation:
        """Run the honest ladder for a freshly-created surface.

        Registry hit (builtin or store) ⇒ ``shaped`` / ``registry``. Miss on a
        structured Mapping payload ⇒ ``generic`` / ``schema`` emitted immediately
        (never blocking on shaping — NFR-1), then a bounded background attempt is
        scheduled. A non-mapping payload ⇒ ``raw`` / ``schema`` (B2 fallback).
        """

        spec_ref = self._registry_spec_ref(server=server, tool=tool)
        if spec_ref is not None:
            return await self._emit_shaped_registry(
                surface_id=surface_id, spec_ref=spec_ref
            )
        if not isinstance(payload, Mapping):
            return await self._emit_tier(
                surface_id=surface_id,
                tier=ViewTier.RAW,
                basis=ViewBasis.SCHEMA,
            )
        derivation = await self._emit_tier(
            surface_id=surface_id,
            tier=ViewTier.GENERIC,
            basis=ViewBasis.SCHEMA,
        )
        self._maybe_schedule(
            server=server,
            tool=tool,
            payload=payload,
            surface_id=surface_id,
            tool_descriptor=tool_descriptor,
        )
        return derivation

    # -- regenerate (user-invited, out-of-run; zero connector traffic) ------

    async def regenerate(
        self,
        *,
        surface_id: str,
        server: str,
        tool: str,
        payload: object,
        regen_count: int,
    ) -> ViewDerivation:
        """Re-derive a surface's view from the STORED ``payload``.

        A pure function of the stored tool response — this method never touches
        the MCP client, connector sessions, or ``CallMcpTool`` (asserted
        adversarially in tests). ``payload is None`` ⇒ ``ViewDeriverError`` (404).
        At the per-surface cap ⇒ ``RegenerateLimitError`` (409). Otherwise it
        re-runs the ladder; a miss with a configured generator calls the reused
        generation subsystem directly (bypassing the scheduler's per-run dedup,
        which would wrongly suppress a user-requested retry), metered per attempt.
        """

        if payload is None:
            raise ViewDeriverError(_Messages.SURFACE_NOT_FOUND)
        if regen_count >= _Limits.MAX_REGEN_PER_SURFACE:
            raise RegenerateLimitError(_Messages.REGENERATE_LIMIT_REACHED)

        spec_ref = self._registry_spec_ref(server=server, tool=tool)
        if spec_ref is not None:
            # A curated/team spec landed since first render — honour it.
            return await self._emit_shaped_registry(
                surface_id=surface_id, spec_ref=spec_ref
            )
        if self.generator is None or self.model_id is None:
            # No shaping available (no BYOK key) — honest re-affirmation.
            return await self._emit_tier(
                surface_id=surface_id,
                tier=ViewTier.GENERIC,
                basis=ViewBasis.SCHEMA,
            )
        return await self._regenerate_via_generation(
            surface_id=surface_id, server=server, tool=tool, payload=payload
        )

    async def _regenerate_via_generation(
        self,
        *,
        surface_id: str,
        server: str,
        tool: str,
        payload: object,
    ) -> ViewDerivation:
        assert self.generator is not None  # narrowed by caller
        assert self.model_id is not None
        descriptor = GenToolDescriptor(name=tool)
        started = time.perf_counter()
        result = await self.generator.generate(
            server=server,
            tool_descriptor=descriptor,
            sample_output=payload,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        if isinstance(result, GenFailure):
            # Honest re-affirmation — never fabricate a shaped view.
            return await self._emit_tier(
                surface_id=surface_id,
                tier=ViewTier.GENERIC,
                basis=ViewBasis.SCHEMA,
            )
        self._store_spec(server=server, tool=tool, payload=payload, spec=result)
        return await self._emit_shaped_generated(
            surface_id=surface_id,
            gen=ViewGenInfo(model=self.model_id, ms=duration_ms),
        )

    # -- ladder helpers -----------------------------------------------------

    def _registry_spec_ref(self, *, server: str, tool: str) -> str | None:
        """Return the registry ``spec_ref`` for a hit (builtin or store), else None.

        ``builtin.lookup`` first, then the org-scoped spec store. A hit means the
        view is ``shaped`` / ``registry``; the ref is the same ``spec:<...>``
        convention regenerate emits, keyed on ``server`` / ``tool``.
        """

        try:
            if builtin.lookup(server, tool) is not None:
                return self._spec_ref(server=server, tool=tool)
            if self.store.get(server=server, tool=tool) is not None:
                return self._spec_ref(server=server, tool=tool)
        except Exception:  # noqa: BLE001 - a store miss must never fail derivation
            return None
        return None

    def _store_spec(
        self,
        *,
        server: str,
        tool: str,
        payload: object,
        spec: SurfaceSpec,
    ) -> None:
        """Overwrite the cached spec for this shape (the regenerate repair)."""

        assert self.model_id is not None
        key = SpecKey.build(
            server=server,
            tool=tool,
            output_shape_hash=output_shape_hash(payload),
            skill_version=self.generator.skill_version
            if self.generator is not None
            else 1,
        )
        self.store.put(
            key,
            StoredSpec.from_generation(
                key=key, spec=spec, generator_model=self.model_id
            ),
        )

    def _maybe_schedule(
        self,
        *,
        server: str,
        tool: str,
        payload: Mapping[str, object],
        surface_id: str,
        tool_descriptor: GenToolDescriptor | None,
    ) -> None:
        """Schedule a bounded background shape attempt (best-effort, non-blocking)."""

        if self.scheduler is None:
            return
        descriptor = tool_descriptor or GenToolDescriptor(name=tool)
        self.scheduler.maybe_schedule(
            server=server,
            tool=tool,
            tool_descriptor=descriptor,
            output=payload,
            surface_uri=surface_id,
        )

    @staticmethod
    def _spec_ref(*, server: str, tool: str) -> str:
        """The stable spec ref for a ``(server, tool)`` shape (SDR ref convention)."""

        return f"{Values.SPEC_REF_PREFIX}{builtin.server_slug(server)}/{builtin.tool_slug(tool)}"

    # -- emit helpers -------------------------------------------------------

    async def _emit_shaped_registry(
        self, *, surface_id: str, spec_ref: str
    ) -> ViewDerivation:
        derivation = ViewDerivation(
            surface_id=surface_id,
            tier=ViewTier.SHAPED,
            basis=ViewBasis.REGISTRY,
            spec_ref=spec_ref,
        )
        await self._emit_derivation(derivation)
        return derivation

    async def _emit_shaped_generated(
        self, *, surface_id: str, gen: ViewGenInfo
    ) -> ViewDerivation:
        derivation = ViewDerivation(
            surface_id=surface_id,
            tier=ViewTier.SHAPED,
            basis=ViewBasis.GENERATED,
            gen=gen,
        )
        await self._emit_derivation(derivation)
        return derivation

    async def _emit_tier(
        self, *, surface_id: str, tier: ViewTier, basis: ViewBasis
    ) -> ViewDerivation:
        derivation = ViewDerivation(surface_id=surface_id, tier=tier, basis=basis)
        await self._emit_derivation(derivation)
        return derivation

    async def _emit_derivation(self, derivation: ViewDerivation) -> None:
        payload: dict[str, object] = {
            Keys.Field.V: Values.PAYLOAD_V,
            Keys.Field.SURFACE_ID: derivation.surface_id,
            Keys.Field.TIER: derivation.tier.value,
            Keys.Field.BASIS: derivation.basis.value,
        }
        if derivation.spec_ref is not None:
            payload[Keys.Field.SPEC_REF] = derivation.spec_ref
        if derivation.gen is not None:
            payload[Keys.Field.GEN] = {
                Keys.Field.MODEL: derivation.gen.model,
                Keys.Field.MS: derivation.gen.ms,
            }
        await self.emit(
            LedgerEventType.VIEW_DERIVED.value, payload, Messages.VIEW_DERIVED
        )


__all__ = [
    "RegenerateLimitError",
    "ViewDerivation",
    "ViewDeriver",
    "ViewDeriverError",
    "ViewGenInfo",
    "_Limits",
]
