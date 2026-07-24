"""Invited shaping — the user's "Suggest a shape" escape hatch (PRD-B4, FR-D4).

When the automatic honest ladder ends at a raw or generic view, the user can
explicitly invite a shaping attempt that is allowed a **bigger budget** than the
automatic pass (more retries, optionally a stronger model). On success the
generated ``SurfaceSpec`` is persisted to the org shape registry (this surface
upgrades now; every future render of the tool is shaped); on failure the honest
fallback is left byte-identical and the ledger records ``shape.resolved
{outcome: no_fit}``.

The generation machinery survives from v1 (redaction, ``_force_source``, schema
validation, the injection kill-switch, retry-with-correction) — this module adds
only the invited-attempt orchestration:

* :class:`InvitedShapeAttempt` — the budget profile (retry count + model id),
  layered over B3's :class:`ShapingModelResolver` so the BYOK/default-provider
  decision (SDR §13 #1) is never bypassed.
* :class:`ShapeRequestRunner` — one awaited attempt that persists on success,
  records the failure on a miss, and emits ``view.derived`` + ``shape.resolved``.

The runner never touches write policy, approvals, or commit paths (SDR §10): a
shape request can only change *how* data is displayed. It emits through the
injected :data:`EmitFn` closure and never imports ``runtime_api``.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass

from agent_runtime.capabilities.surfaces.generator import (
    GenFailure,
    GenToolDescriptor,
    SurfaceSpecGenerator,
)
from agent_runtime.capabilities.surfaces.shape_hash import output_shape_hash
from agent_runtime.capabilities.surfaces.spec_models import SurfaceSpec
from agent_runtime.capabilities.surfaces.store import (
    SpecKey,
    StoredSpec,
    SurfaceSpecStorePort,
)
from agent_runtime.surfaces_v2.constants import Keys, Messages, Values
from agent_runtime.surfaces_v2.emitter import EmitFn
from agent_runtime.surfaces_v2.ledger_models import (
    LedgerEventType,
    ShapeOutcome,
    ViewBasis,
    ViewTier,
)
from agent_runtime.surfaces_v2.shaping_policy import ShapingModelResolver

# The PRD names the outcome enum ``ShapeRequestOutcome``; it is exactly the
# contract-owned :class:`ShapeOutcome` (shaped/no_fit). Aliased so the domain
# reads with the PRD vocabulary without a second enum drifting from the ledger.
ShapeRequestOutcome = ShapeOutcome


class InvitedShapeAttempt:
    """Budget profile for a user-invited attempt (bigger than the automatic pass).

    The automatic pass spends ``skill.json``'s ``max_retries`` (1 ⇒ 2 attempts);
    the invited attempt spends ``SURFACE_SHAPE_REQUEST_MAX_RETRIES`` (default 3 ⇒
    4 attempts). The model id is ``SURFACE_SHAPE_REQUEST_MODEL`` verbatim when set
    (the "stronger model" knob), else B3's :class:`ShapingModelResolver` — never
    a bare ``SURFACE_SPEC_MODEL`` read, which would bypass SDR §13 #1's
    BYOK/default-provider logic.
    """

    ENV_MODEL = "SURFACE_SHAPE_REQUEST_MODEL"
    ENV_MAX_RETRIES = "SURFACE_SHAPE_REQUEST_MAX_RETRIES"
    DEFAULT_MAX_RETRIES = 3

    @classmethod
    def max_retries(cls, environ: Mapping[str, str]) -> int:
        """Resolve the invited retry budget from env, clamped at 0."""

        raw = environ.get(cls.ENV_MAX_RETRIES, "").strip()
        if not raw:
            return cls.DEFAULT_MAX_RETRIES
        try:
            value = int(raw)
        except ValueError:
            return cls.DEFAULT_MAX_RETRIES
        return value if value >= 0 else cls.DEFAULT_MAX_RETRIES

    @classmethod
    def resolve_model_id(
        cls, *, environ: Mapping[str, str], run_provider: str | None
    ) -> str | None:
        """Resolve the invited model id, or ``None`` when shaping is unavailable.

        ``SURFACE_SHAPE_REQUEST_MODEL`` wins verbatim; otherwise defer to B3's
        resolver (which encodes the BYOK/default-provider decision). ``None`` ⇒
        the coordinator raises ``shaping_unavailable`` (422) before ledgering.
        """

        override = environ.get(cls.ENV_MODEL, "").strip()
        if override:
            return override
        return ShapingModelResolver.resolve(environ=environ, run_provider=run_provider)


class ShapeRequestError(Exception):
    """Typed domain error for an invited shape request. Carries a safe message."""


@dataclass(frozen=True)
class ShapeRequestRunner:
    """Run one user-invited shaping attempt (bigger budget) + ledger its outcome.

    ``generator`` arrives already budget-raised AND meter-wired: the coordinator
    builds ``SurfaceSpecGenerator(completion=…, skill=…with_max_retries(n),
    usage_meter=MeteredModelInvocation(purpose=SHAPE_REQUEST))`` and hands it in,
    so metering happens per attempt inside ``generate()`` (A2 seam) and the runner
    takes no separate meter. ``model_id`` is the resolved shaping model, used for
    the ``StoredSpec`` provenance and the ``view.derived.gen.model`` block.

    Unlike the automatic scheduler, the runner ignores any recorded failure for
    the key — an invited request may retry a shape the automatic pass gave up on.
    """

    generator: SurfaceSpecGenerator
    store: SurfaceSpecStorePort
    emit: EmitFn
    model_id: str

    async def run(
        self,
        *,
        server: str,
        tool: str,
        sample_output: object,
        surface_id: str,
    ) -> ShapeOutcome:
        """Generate with the invited budget; persist + ledger the outcome.

        Success ⇒ ``store.put`` + ``view.derived {tier: shaped, basis: generated}``
        + ``shape.resolved {outcome: shaped}``. Failure ⇒ ``store.record_failure``
        + ``shape.resolved {outcome: no_fit, reason}`` (a CONSTANT safe reason —
        never raw model output). The surface's view state does not change on
        failure.
        """

        descriptor = GenToolDescriptor(name=tool)
        started = time.perf_counter()
        result = await self.generator.generate(
            server=server,
            tool_descriptor=descriptor,
            sample_output=sample_output,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        key = SpecKey.build(
            server=server,
            tool=tool,
            output_shape_hash=output_shape_hash(sample_output),
            skill_version=self.generator.skill_version,
        )
        if isinstance(result, GenFailure):
            # Persist the failure for skill iteration; never render it. The
            # ledgered reason is a CONSTANT safe summary, not GenFailure.reason
            # (which can echo model-derived label/path text).
            self.store.record_failure(key, result.reason, result.raw_output)
            await self._emit_resolved(
                surface_id=surface_id,
                outcome=ShapeOutcome.NO_FIT,
                reason=Messages.SHAPE_NO_FIT_REASON,
            )
            return ShapeOutcome.NO_FIT

        await self._persist(key=key, spec=result)
        await self._emit_view_derived(surface_id=surface_id, duration_ms=duration_ms)
        await self._emit_resolved(
            surface_id=surface_id, outcome=ShapeOutcome.SHAPED, reason=None
        )
        return ShapeOutcome.SHAPED

    async def _persist(self, *, key: SpecKey, spec: SurfaceSpec) -> None:
        self.store.put(
            key,
            StoredSpec.from_generation(
                key=key, spec=spec, generator_model=self.model_id
            ),
        )

    async def _emit_view_derived(self, *, surface_id: str, duration_ms: int) -> None:
        """Emit the B3 shaped/generated ``view.derived`` (same payload shape).

        Byte-compatible with ``ViewDeriver._emit_derivation`` so the SurfaceStore
        + client canvas merge the invited upgrade exactly as an automatic one.
        """

        payload: dict[str, object] = {
            Keys.Field.V: Values.PAYLOAD_V,
            Keys.Field.SURFACE_ID: surface_id,
            Keys.Field.TIER: ViewTier.SHAPED.value,
            Keys.Field.BASIS: ViewBasis.GENERATED.value,
            Keys.Field.GEN: {
                Keys.Field.MODEL: self.model_id,
                Keys.Field.MS: duration_ms,
            },
        }
        await self.emit(
            LedgerEventType.VIEW_DERIVED.value, payload, Messages.VIEW_DERIVED
        )

    async def _emit_resolved(
        self, *, surface_id: str, outcome: ShapeOutcome, reason: str | None
    ) -> None:
        payload: dict[str, object] = {
            Keys.Field.V: Values.PAYLOAD_V,
            Keys.Field.SURFACE_ID: surface_id,
            Keys.Field.OUTCOME: outcome.value,
        }
        if reason is not None:
            payload[Keys.Field.REASON] = reason
        await self.emit(
            LedgerEventType.SHAPE_RESOLVED.value, payload, Messages.SHAPE_RESOLVED
        )


__all__ = [
    "InvitedShapeAttempt",
    "ShapeRequestError",
    "ShapeRequestOutcome",
    "ShapeRequestRunner",
]
