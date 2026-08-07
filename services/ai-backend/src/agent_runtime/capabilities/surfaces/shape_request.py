"""Shaping — the read path's rung 5, and the user's "Suggest a shape" escape hatch.

Two callers of one model subsystem, kept in one module because they ask the same
question of the same generator and differ only in budget and trigger:

* :class:`ReadPathShaper` — **automatic**, rung 5 of the projector's ladder. It
  is consulted only for a payload the deterministic floor could not bind (an
  array at the root, prose, a markdown table, a CSV block, a multi-block result
  — five of the eight measured MCP shapes), and its answer is
  :meth:`SurfaceSpecGenerator.shape`'s three-way contract: decline, select, or
  generate. This is the module's read-path half; everything below the divider is
  the invited half.
* :class:`ShapeRequestRunner` — **user-invited** (PRD-B4, FR-D4), below.

Invited shaping — the user's "Suggest a shape" escape hatch (PRD-B4, FR-D4).

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

import asyncio
import logging
import time
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import ClassVar

from agent_runtime.capabilities.surfaces.generator import (
    GenFailure,
    GenToolDescriptor,
    ShapingCredentials,
    ShapingModelBuild,
    SurfaceSpecGenerator,
)
from agent_runtime.capabilities.surfaces.projector import ShapedSurface
from agent_runtime.capabilities.surfaces.shape_hash import output_shape_hash
from agent_runtime.capabilities.surfaces.shaping_answer import (
    GenerateBinding,
    SelectBinding,
    ShapingSurface,
    ShapingVerdict,
)
from agent_runtime.capabilities.surfaces.spec_models import (
    SurfaceArchetype,
    SurfaceSource,
    SurfaceSpec,
    SurfaceSpecError,
    SurfaceSpecRung,
    validate_surface_spec,
)
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

_LOGGER = logging.getLogger(__name__)

# The PRD names the outcome enum ``ShapeRequestOutcome``; it is exactly the
# contract-owned :class:`ShapeOutcome` (shaped/no_fit). Aliased so the domain
# reads with the PRD vocabulary without a second enum drifting from the ledger.
ShapeRequestOutcome = ShapeOutcome


class ShapedSurfaceBuilder:
    """Turn one rendering shaping answer into the two halves a renderer reads.

    The shaping contract deliberately stops one step short of a
    :class:`SurfaceSpec` — its answer carries a literal ``title`` and a spec is
    paths-only by construction — and says so: *"how a shaping answer reaches a
    renderer belongs to the caller that made the call."* This class is that
    caller's answer, and it makes exactly three decisions.

    **1. A payload with no keys is bound under one.** ``SurfaceDotPath`` needs at
    least one segment, so an array or a string at the ROOT is unaddressable: a
    ``select`` answer over it could name no ``items_path`` and no column path
    would resolve. Binding it under :data:`_ShapeKeys.ITEMS` gives the model
    something to point at, and — critically — the SAME value is what ships as
    ``state.data``, so the document the answer was linted against is the document
    the renderer resolves. ``EnvelopeUnwrapper`` leaves it intact (it refuses to
    peel to a non-mapping, which is what lets ``items_path: "data"`` bind an
    array), so the model sees the wrapper it is asked to address.

    **2. A generated answer's rows ARE its data.** ``generate`` mode exists for
    prose, where there is nothing to point at; its rows are model-authored and
    are recorded as such. They are shipped as the surface's data with the spec's
    paths addressing them, because a generated spec laid over the connector's
    payload binds every path to nothing.

    **3. No model-authored text enters the spec.** ``title_path`` is
    :data:`_ShapeKeys.TITLE_PROBE` — the same constant the inference floor's
    minimal spec uses — over the connector's own payload in ``select`` mode, so
    it resolves to a connector value or to nothing (the renderer then names the
    tool, which is its documented fallback). The model's literal title is used
    only in ``generate`` mode, where the data is already the model's and the
    title is one more model-authored value among them.
    """

    #: Archetypes whose renderer reads ``columns`` + ``items_path``. Every other
    #: implemented archetype reads ``fields`` over the payload root, so a
    #: shaping answer's columns become fields there — the answer names one
    #: vocabulary (``columns``) and the spec names two.
    _TABULAR: ClassVar[frozenset[SurfaceArchetype]] = frozenset(
        {SurfaceArchetype.TABLE, SurfaceArchetype.BOARD}
    )

    @classmethod
    def bindable(cls, payload: object) -> object:
        """The payload with at least one addressable key. Total over any input.

        A mapping is returned unchanged — it already has keys, and rewrapping it
        would move every path the model wrote by one segment.
        """

        if isinstance(payload, Mapping):
            return payload
        return {_ShapeKeys.ITEMS: payload}

    @classmethod
    def build(
        cls,
        *,
        surface: ShapingSurface,
        payload: object,
        source: SurfaceSource | None,
    ) -> ShapedSurface | None:
        """Build the renderable pair, or ``None`` if the spec will not validate.

        ``None`` rather than a raise: this runs on a read path where a display
        upgrade may not fail the call, and a spec this builder cannot assemble is
        the same outcome as a model that never answered — the floor stands.
        """

        binding = surface.binding
        if isinstance(binding, GenerateBinding):
            data, items_path, title_path = cls._generated(surface, binding)
            rung = SurfaceSpecRung.GENERATED
        elif isinstance(binding, SelectBinding):
            data = cls.bindable(payload)
            items_path = binding.items_path
            title_path = _ShapeKeys.TITLE_PROBE
            rung = SurfaceSpecRung.SELECTED
        else:  # pragma: no cover - the binding union has exactly two members
            return None
        spec = cls._spec(
            archetype=surface.archetype,
            source=source,
            title_path=title_path,
            items_path=items_path,
            binding=binding,
        )
        if spec is None:
            return None
        return ShapedSurface(spec=spec, data=data, rung=rung)

    @classmethod
    def _generated(
        cls, surface: ShapingSurface, binding: GenerateBinding
    ) -> tuple[object, str | None, str]:
        """``(data, items_path, title_path)`` for a model-authored answer.

        A tabular archetype draws every row, so the rows are bound under a key an
        ``items_path`` can name. A record-shaped one draws a single body, so the
        FIRST row is the body and the column paths are read through it — the
        generate contract guarantees every column path resolves in every row, so
        reading them through one row cannot miss.
        """

        rows = [dict(row) for row in binding.rows]
        if surface.archetype in cls._TABULAR:
            data: dict[str, object] = {
                _ShapeKeys.TITLE: surface.title,
                _ShapeKeys.ROWS: rows,
            }
            return (data, _ShapeKeys.ROWS, _ShapeKeys.TITLE)
        return (
            {_ShapeKeys.TITLE: surface.title, _ShapeKeys.ROW: rows[0]},
            None,
            _ShapeKeys.TITLE,
        )

    @classmethod
    def _spec(
        cls,
        *,
        archetype: SurfaceArchetype,
        source: SurfaceSource | None,
        title_path: str,
        items_path: str | None,
        binding: SelectBinding | GenerateBinding,
    ) -> SurfaceSpec | None:
        """Assemble + validate the spec, through the same gate every spec crosses.

        Deliberately built as a raw dict and passed to
        :func:`validate_surface_spec` rather than constructed directly: the model
        chose the archetype and the paths, so this is untrusted input and it
        earns the schema gate the generation path already applies.
        """

        raw: dict[str, object] = {
            _ShapeKeys.SPEC_VERSION: _ShapeKeys.SPEC_VERSION_VALUE,
            _ShapeKeys.ARCHETYPE: archetype.value,
            _ShapeKeys.SOURCE: cls._source_dict(source),
            _ShapeKeys.TITLE_PATH: title_path,
        }
        slots = [
            column.model_dump(mode="json", exclude_none=True)
            for column in binding.columns
        ]
        if archetype in cls._TABULAR:
            raw[_ShapeKeys.COLUMNS] = slots
            if items_path is not None:
                raw[_ShapeKeys.ITEMS_PATH] = items_path
        else:
            # ``align`` is a column-only member; a field has no column to align
            # within, so it is dropped rather than carried into a slot that has
            # no meaning for it.
            raw[_ShapeKeys.FIELDS] = [
                {key: value for key, value in slot.items() if key != _ShapeKeys.ALIGN}
                for slot in slots
            ]
        try:
            return validate_surface_spec(raw)
        except SurfaceSpecError:
            _LOGGER.warning(
                "%s shaped_spec_invalid archetype=%s: the surface keeps the "
                "deterministic floor",
                _SHAPER_PREFIX,
                archetype.value,
            )
            return None

    @staticmethod
    def _source_dict(source: SurfaceSource | None) -> dict[str, str]:
        """``SurfaceSpec.source`` is schema-required; a nameless call still shapes.

        The placeholder mirrors :data:`agent_runtime...infer.INFERRED_SOURCE`'s
        posture — a surface is never refused because its call had no name.
        """

        if source is None:
            return {
                _ShapeKeys.SERVER: _ShapeKeys.UNKNOWN,
                _ShapeKeys.TOOL: _ShapeKeys.UNKNOWN,
            }
        return {_ShapeKeys.SERVER: source.server, _ShapeKeys.TOOL: source.tool}


class _ShapeKeys:
    """Spellings the shaped spec and its data are assembled from."""

    # Where an unaddressable payload is bound so a dot-path exists. ``items`` is
    # an ``EnvelopeUnwrapper`` wrapper name, so a mapping under it would be
    # peeled — which is why only NON-mappings are ever bound here.
    ITEMS = "items"
    # Model-authored data slots. Namespaced away from the rows so a connector
    # field called ``title`` can never be mistaken for the surface's heading.
    TITLE = "surface_title"
    ROWS = "rows"
    ROW = "row"
    # The floor's own ``title_path`` convention (``infer._Keys.FALLBACK_TITLE``):
    # resolves to the connector's own title when it has one, and to nothing
    # otherwise, which the renderer already words as the tool's name.
    TITLE_PROBE = "title"
    UNKNOWN = "unknown"

    SPEC_VERSION = "spec_version"
    SPEC_VERSION_VALUE = 1
    ARCHETYPE = "archetype"
    SOURCE = "source"
    SERVER = "server"
    TOOL = "tool"
    TITLE_PATH = "title_path"
    ITEMS_PATH = "items_path"
    COLUMNS = "columns"
    FIELDS = "fields"
    ALIGN = "align"


#: Log prefix for the read-path shaper, matching the generator's metering line.
_SHAPER_PREFIX = "[surfaces.shaping]"


class ReadPathShaper:
    """Rung 5: one shaping call per novel payload shape, per run.

    Implements :class:`~.projector.SurfaceShapingPort`, so the projector reaches
    it without importing the model machinery. Three properties are the contract:

    **One call per surface, at most.** The projector consults this seam only when
    rungs 1–4 produced nothing, and the two are mutually exclusive there, so a
    read never pays for both a refinement and a shaping. Within a run, answers
    are memoised on the payload's *structural* shape, so one tool called five
    times costs one call — and a second, concurrent read of the same shape awaits
    the first rather than starting a second (the in-flight discipline
    ``ShapeRequestCoordinator`` already applies per surface, applied here per
    shape because the read path has no surface id yet when the question is
    asked). ``max_per_run`` bounds the rest.

    **It never raises.** Every failure mode — no shaping model, a provider
    error, an answer that will not validate, a bug here — returns ``None``, which
    the projector reads as "keep the deterministic floor". A user with no
    provider key is exactly as well off as before this rung existed.

    **It answers, it does not emit.** The surface record is written once, by the
    presenter, from the envelope the projector returns. That is what makes a
    decline mean *no surface* rather than a surface that is later retracted.
    """

    #: Distinct payload shapes one run will spend a shaping call on. Matches
    #: ``SurfaceGenerationScheduler``'s refinement cap: they bound the same cost
    #: on the same path, and two numbers would only drift.
    DEFAULT_MAX_PER_RUN = 5
    ENV_MAX_PER_RUN = "SURFACE_SHAPE_MAX_PER_RUN"

    def __init__(
        self,
        *,
        generator: SurfaceSpecGenerator,
        max_per_run: int = DEFAULT_MAX_PER_RUN,
    ) -> None:
        self._generator = generator
        self._max_per_run = max(max_per_run, 0)
        self._settled: dict[str, ShapedSurface | None] = {}
        self._inflight: dict[str, asyncio.Task[ShapedSurface | None]] = {}
        self._spent = 0

    async def shape(
        self,
        *,
        server: str,
        tool: str,
        tool_descriptor: object,
        payload: object,
        source: SurfaceSource | None,
    ) -> ShapedSurface | None:
        """Shape ``payload``, or return ``None`` when no answer is available."""

        try:
            return await self._shape(
                server=server,
                tool=tool,
                tool_descriptor=tool_descriptor,
                payload=payload,
                source=source,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a display upgrade may not fail a read
            _LOGGER.warning(
                "%s shape_raised server=%s tool=%s: the surface keeps the "
                "deterministic floor",
                _SHAPER_PREFIX,
                server,
                tool,
                exc_info=True,
            )
            return None

    async def _shape(
        self,
        *,
        server: str,
        tool: str,
        tool_descriptor: object,
        payload: object,
        source: SurfaceSource | None,
    ) -> ShapedSurface | None:
        bindable = ShapedSurfaceBuilder.bindable(payload)
        key = self._key(server=server, tool=tool, payload=bindable)
        if key in self._settled:
            return self._settled[key]
        existing = self._inflight.get(key)
        if existing is not None:
            # Shielded: one caller's cancellation must not cancel the answer the
            # other callers are waiting on.
            return await asyncio.shield(existing)
        if self._spent >= self._max_per_run:
            return None
        self._spent += 1
        task = asyncio.ensure_future(
            self._ask(
                server=server,
                tool=tool,
                tool_descriptor=tool_descriptor,
                payload=bindable,
                source=source,
            )
        )
        self._inflight[key] = task
        # Settled by the TASK, not by whoever happened to await it: an awaiter
        # can be cancelled (the run ends, the read is abandoned) while the call
        # it started is still in flight, and recording the answer on the
        # awaiter's path would then lose it and let the next read buy it again.
        task.add_done_callback(lambda done: self._settle(key, done))
        return await asyncio.shield(task)

    def _settle(self, key: str, task: "asyncio.Task[ShapedSurface | None]") -> None:
        """Memoise a finished call's answer and clear its in-flight slot."""

        self._inflight.pop(key, None)
        if task.cancelled() or task.exception() is not None:
            # Nothing was answered, so nothing is memoised — but the budget was
            # spent, which is the honest record of a call that was made.
            return
        self._settled[key] = task.result()

    async def _ask(
        self,
        *,
        server: str,
        tool: str,
        tool_descriptor: object,
        payload: object,
        source: SurfaceSource | None,
    ) -> ShapedSurface | None:
        """One shaping call, mapped onto the projector's seam vocabulary.

        The three-way answer maps onto three returns, and the mapping is the
        point of the contract: ``render`` becomes a renderable pair carrying the
        provenance :attr:`ShapingOutcome.view_basis` decided, ``declined``
        becomes :meth:`ShapedSurface.declined` (no surface at all — the model
        looked and said no), and ``invalid`` becomes ``None`` (nobody answered,
        so the floor stands). Folding the last two together would let a broken
        prompt silently delete surfaces.
        """

        descriptor = (
            tool_descriptor
            if isinstance(tool_descriptor, GenToolDescriptor)
            else GenToolDescriptor(name=tool)
        )
        outcome = await self._generator.shape(
            server=server, tool_descriptor=descriptor, sample_output=payload
        )
        if outcome.verdict is ShapingVerdict.DECLINED:
            return ShapedSurface.declined()
        if outcome.surface is None:
            return None
        return ShapedSurfaceBuilder.build(
            surface=outcome.surface, payload=payload, source=source
        )

    def _key(self, *, server: str, tool: str, payload: object) -> str:
        """The memo key: this tool's identity plus the payload's STRUCTURE.

        Structural rather than literal so a tool called five times with five
        different result sets costs one call, and the same
        ``SpecKey``/shape-hash pair the refinement scheduler dedupes on, so the
        two rungs agree on what "the same shape" means.
        """

        return SpecKey.build(
            server=server,
            tool=tool,
            output_shape_hash=output_shape_hash(payload),
            skill_version=self._generator.skill_version,
        ).digest()

    @classmethod
    def max_per_run_from_env(cls, environ: Mapping[str, str]) -> int:
        raw = environ.get(cls.ENV_MAX_PER_RUN, "").strip()
        if not raw:
            return cls.DEFAULT_MAX_PER_RUN
        try:
            value = int(raw)
        except ValueError:
            return cls.DEFAULT_MAX_PER_RUN
        return value if value >= 0 else cls.DEFAULT_MAX_PER_RUN

    # -- run-scoped binding (mirrors SurfaceGenerationScheduler) --------------

    @classmethod
    def bind_for_run(cls, shaper: "ReadPathShaper") -> object:
        """Set the active shaper; return the token for restoration."""

        return _SHAPER_CTX.set(shaper)

    @classmethod
    def unbind(cls, token: object) -> None:
        """Restore the previous shaper token."""

        _SHAPER_CTX.reset(token)  # type: ignore[arg-type]

    @classmethod
    def active(cls) -> "ReadPathShaper | None":
        """Return the currently bound shaper, or ``None`` when unbound."""

        return _SHAPER_CTX.get(None)


_SHAPER_CTX: ContextVar["ReadPathShaper | None"] = ContextVar(
    "read_path_shaper", default=None
)


def build_read_path_shaper(
    *,
    environ: Mapping[str, str],
    run_provider: str | None = None,
    credentials: ShapingCredentials | None = None,
    usage_meter: object | None = None,
    completion: object | None = None,
) -> ReadPathShaper | None:
    """Build a run-scoped shaper from env, or ``None`` when shaping is off.

    The gate is :class:`ShapingModelResolver` — the same seam every other shaping
    caller consults, so the BYOK/default-provider decision (SDR §13 #1) is stated
    once. No model resolves ⇒ ``None`` ⇒ the projector never gets a shaper and
    the ladder is byte-identical to before rung 5 existed.

    A model that cannot be CONSTRUCTED returns ``None`` here rather than raising,
    for the reason ``build_surface_generation_scheduler`` already gives: "we
    cannot build a shaping model" is precisely shaping being off, and keeping the
    degrade next to the thing that fails beats relying on every caller to wrap
    the call.

    ``credentials`` carries the run's BYOK material into that construction, and
    it is not optional in practice: on a packaged desktop install the process
    env holds no provider key by design, so a builder called without it builds
    a model with no credential, fails, and turns rung 5 off for every run. The
    caller that holds the run's hydrated context (the worker) must pass it.
    """

    model_id = ShapingModelResolver.resolve(environ=environ, run_provider=run_provider)
    if not model_id:
        return None
    if completion is None:
        from agent_runtime.capabilities.surfaces.generator import (  # noqa: PLC0415
            LangChainSpecCompletion,
        )

        build = ShapingModelBuild.attempt(model_id=model_id, credentials=credentials)
        if not build.ok:
            # No exc_info and no kwargs in the line — the kwargs hold key
            # material. ``describe()`` carries the failure CLASS and the
            # exception type name, which is what separates "nobody gave this
            # run a key" from "the model id is wrong".
            _LOGGER.warning(
                "%s shaping_model_unavailable model=%s %s: rung 5 is off for this "
                "run; surfaces still render from the deterministic floor",
                _SHAPER_PREFIX,
                model_id,
                build.describe(),
            )
            return None
        completion = LangChainSpecCompletion(model=build.model, model_id=model_id)
    generator = SurfaceSpecGenerator(
        completion=completion,  # type: ignore[arg-type]
        usage_meter=usage_meter,  # type: ignore[arg-type]
    )
    return ReadPathShaper(
        generator=generator,
        max_per_run=ReadPathShaper.max_per_run_from_env(environ),
    )


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
    "ReadPathShaper",
    "ShapeRequestError",
    "ShapeRequestOutcome",
    "ShapeRequestRunner",
    "ShapedSurfaceBuilder",
    "build_read_path_shaper",
]
