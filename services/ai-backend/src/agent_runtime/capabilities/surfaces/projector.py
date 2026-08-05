"""Pure-domain surface projection (generative-UI PRD-02, plan D3/D4).

:class:`SurfaceProjector` turns a connector tool's output into a
:class:`SurfaceEnvelope` — a ``surface_uri`` plus ``{spec?, source?, data}`` —
that rides inside the ``tool_result`` / ``draft_updated`` event payload. It is a
*pure* function of its inputs: no I/O, no transport, no env reads. Two injected
seams: an optional
:class:`~agent_runtime.capabilities.surfaces.store.SurfaceSpecReadPort`
(rung-2 cache read) and an optional :class:`SurfaceGenerationSchedulerPort`
(rung-3 async generation, PRD-07).

Spec-acquisition ladder (D4, floored by the generative-UI-floor PRD §3.3):

1. **builtin** curated spec (packaged JSON, :mod:`agent_runtime...surfaces.builtin`)
2. injected **store** (cached / previously generated — in-memory or file)
3. **shape match** — the two name-blind rungs, in order: an exact payload-shape
   hit in the learned cache (any tool, any connector, same install), then a
   nearest-neighbour match against the curated specs read as shape templates.
   Both retire the naming brittleness the audit measured, and both report
   ``SHAPE_MATCH`` so the ledger never calls a structural match a curated one.
4. **inferred** — rung 0,
   :class:`~agent_runtime.capabilities.surfaces.infer.SurfaceSpecInferrer`
   derives a real spec from the payload's own structure. Deterministic, free,
   and it cannot fail, so a mapping-shaped output now ALWAYS ships a spec. Async
   generation is still invited here (when a scheduler is wired): the model is a
   *refinement* of the inferred spec, no longer its only supplier, and its
   result arrives via ``surface_spec_generated`` and merges by URI (PRD-04).

That inversion is the point of the PRD: having a spec used to be the happy path
and everything else a failure state the user read as an apology ("No spec
matched …"). With rung 0 unconditional there is no failure state to word.

Every rung reads the payload **after**
:class:`~agent_runtime.capabilities.surfaces.infer.EnvelopeUnwrapper` has peeled
its wrapper envelopes, and the peeled value is what ships as ``state.data`` —
see :meth:`SurfaceProjector._unwrapped` for why the two must be the same value.

The URI grammar is ``<archetype>://<server-slug>/<tool-or-resource>/<id>``; the
id segment is derived from a common id field on the output, else a stable hash
of the call id, so the same logical resource yields the same URI across events.
"""

from __future__ import annotations

__operation_boundary__ = "presentation"

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agent_runtime.capabilities.surfaces import builtin
from agent_runtime.capabilities.surfaces.infer import (
    EnvelopeUnwrapper,
    SurfaceSpecInferrer,
)
from agent_runtime.capabilities.surfaces.spec_models import (
    SurfaceArchetype,
    SurfaceEnvelope,
    SurfaceSource,
    SurfaceSpec,
    SurfaceSpecRung,
    SurfaceState,
)
from agent_runtime.capabilities.surfaces.shape_hash import output_shape_hash
from agent_runtime.capabilities.surfaces.store import (
    InMemorySurfaceSpecStore,
    SurfaceSpecReadPort,
    SurfaceSpecShapeReadPort,
    SurfaceSpecStorePort,
)

# Ordered id-bearing keys probed to build a stable URI segment (plan D3).
_ID_FIELDS: tuple[str, ...] = ("id", "key", "identifier", "number")

# Characters allowed verbatim in a URI id segment; everything else collapses to
# a dash so an untrusted value can never inject a path separator or scheme.
_URI_SEGMENT_SAFE = re.compile(r"[^A-Za-z0-9._-]+")

# Length of the stable hash fallback used when no id field is present.
_HASH_LEN = 12


@runtime_checkable
class SurfaceGenerationSchedulerPort(Protocol):
    """Rung-3 seam: schedule async generation for a ladder miss (PRD-07).

    Injected so the pure projector never imports the generation machinery or
    touches an event loop. ``tool_descriptor`` is typed ``object`` (the projector
    only forwards it); the scheduler expects a ``GenToolDescriptor``. Fully
    best-effort: implementations must swallow their own errors.
    """

    def maybe_schedule(
        self,
        *,
        server: str,
        tool: str,
        tool_descriptor: object,
        output: object,
        surface_uri: str,
    ) -> None:
        """Schedule generation for ``(server, tool)`` unless capped/deduped."""
        ...


@dataclass(frozen=True)
class _LadderResult:
    """What the acquisition ladder produced, and which rung produced it.

    The rung travels with the spec rather than being recomputed downstream: the
    ledger's ``view.derived`` basis has to state how a surface was shaped, and a
    second party deriving that from the spec alone cannot tell a curated spec
    from an inferred one. One producer, one value — the rule
    :meth:`SurfaceProjector._state_source` already documents for ``source``.
    """

    spec: SurfaceSpec | None
    rung: SurfaceSpecRung | None

    @property
    def wants_refinement(self) -> bool:
        """Whether async model generation should still be invited.

        Rung 0 is a *floor*, not an answer. An inferred spec renders instantly
        and is precisely the artefact the model is now asked to improve (PRD
        §3.5), so — unlike a curated or previously-stored spec — it must not
        suppress the scheduler. Getting this wrong is the whole feature: the
        old condition was "no spec", which rung 0 makes permanently false.

        A shape match does NOT want refinement, and that is what makes AC14
        true: the second encounter with a known shape costs zero model calls.
        Both of its sources are already better than anything a nano model would
        author from the same payload — a spec a person hand-wrote, or one this
        install already generated and stored for exactly this shape — so paying
        for a refinement per novel tool *name* would spend the model on aliases
        of a shape that was solved once.
        """

        return self.rung is None or self.rung is SurfaceSpecRung.INFERRED


@dataclass(frozen=True)
class SurfaceProjector:
    """Resolves a tool output into a :class:`SurfaceEnvelope` (or ``None``).

    ``store`` is the optional rung-2 read seam. ``scheduler`` is the optional
    rung-3 seam: when the curated and stored rungs both miss, the projector
    attaches the *inferred* envelope (which renders instantly, unconditionally)
    AND schedules async spec generation to refine it — never blocking the
    tool-call path. ``enabled`` is a self-contained short-circuit:
    when ``False`` the projector returns ``None`` without resolving — the caller
    decides the toggle and passes it in; the projector never touches the
    environment. (PRD-E3 retired the standalone ``RUNTIME_SURFACE_EMISSION`` env
    gate; the projector is now driven on-demand by the ``SURFACES_V2`` Work Ledger
    emitter, so the runtime construction path leaves ``enabled`` at its ``True``
    default.)
    """

    store: SurfaceSpecReadPort | None = None
    enabled: bool = True
    scheduler: SurfaceGenerationSchedulerPort | None = None

    def resolve(
        self,
        server_name: str,
        tool_name: str,
        output: object,
        *,
        call_id: str | None = None,
        tool_descriptor: object | None = None,
    ) -> SurfaceEnvelope | None:
        """Return a surface envelope for a non-error tool output, or ``None``.

        ``None`` when emission is disabled or ``output`` is not a mapping
        (str/None/list scalars have no surface). A mapping ALWAYS yields an
        envelope carrying a spec: curated, stored, or — when neither knows this
        tool — inferred from the payload's own structure. When the curated and
        stored rungs both miss, an async generation is also scheduled (given a
        wired scheduler and an enabled model) so the surface upgrades in place
        once ``surface_spec_generated`` lands.
        """

        if not self.enabled:
            return None
        if not isinstance(output, Mapping):
            return None

        data = self._unwrapped(output)
        source = self._state_source(server_name, tool_name)
        ladder = self._resolve_ladder(server_name, tool_name, data, source)
        archetype = (
            ladder.spec.archetype
            if ladder.spec is not None
            else self._infer_archetype(data)
        )
        surface_uri = self._build_uri(
            archetype=archetype,
            server_name=server_name,
            tool_name=tool_name,
            output=data,
            call_id=call_id,
        )
        if ladder.wants_refinement and self.scheduler is not None:
            # The peeled payload, deliberately: a spec the model authors against
            # the wrapper binds paths that miss the data we ship.
            self.scheduler.maybe_schedule(
                server=server_name,
                tool=tool_name,
                tool_descriptor=tool_descriptor,
                output=data,
                surface_uri=surface_uri,
            )
        return SurfaceEnvelope(
            surface_uri=surface_uri,
            archetype=archetype,
            spec_rung=ladder.rung,
            state=SurfaceState(spec=ladder.spec, source=source, data=data),
        )

    # -- payload normalisation ------------------------------------------------

    @staticmethod
    def _unwrapped(output: Mapping[str, object]) -> Mapping[str, object]:
        """Peel wrapper envelopes off the payload, before anything reads it.

        This runs FIRST — ahead of spec lookup, archetype inference and URI
        construction — and its result is what ships as ``state.data``, because
        a spec's dot-paths and the data they bind against must be the same
        value. Shipping the wrapper while shaping paths against its contents
        (or the reverse) resolves every path to nothing.

        Which is exactly FINDINGS §3.4: ``langchain-mcp-adapters`` hands us
        ``{"structured_content": {...}}`` for every MCP server that returns
        ``structuredContent``, so the curated ``items_path: "issues"`` resolved
        against the wrapper and missed — a hand-authored spec that matched
        perfectly still rendered nothing.

        :class:`EnvelopeUnwrapper` is typed to return ``object`` because it is
        total over any input; given a ``Mapping`` it can only return that
        mapping or a mapping peeled out of it, so the guard is a narrowing for
        the type checker rather than a reachable branch.
        """

        peeled = EnvelopeUnwrapper.unwrap(output)
        return peeled if isinstance(peeled, Mapping) else output

    # -- provenance -----------------------------------------------------------

    @staticmethod
    def _state_source(server_name: str, tool_name: str) -> SurfaceSource | None:
        """The envelope's ``{server, tool}`` provenance, or ``None``.

        Carried on EVERY state, whichever rung answered: it is the pair the
        renderer reads to name the tool a surface came from, and on a curated
        hit it agrees with ``spec.source`` at no cost. Never read back out of
        ``data`` — a payload that could name its own provenance could claim to
        be any tool.

        **This is the one place the served name is decided.**
        ``WorkLedgerEmitter`` does not compute its own — it restates this pair
        off the envelope onto ``surface.created.source``, which is what the v2
        fold hands the renderer. They used to derive it separately, one through
        ``tool_slug`` and one not, which is how two names for one tool reached
        two different screens.

        It uses :func:`builtin.display_name`, deliberately not the lookup slugs.
        What the caller passes is what gets served, unchanged apart from
        whitespace: a name that reaches a person must not be re-spelled on the
        way. That the MCP path happens to hand it names already lowercased at
        the ``McpToolCallRequest`` boundary is that contract's business, not
        this function's — this one must not lowercase a name a second time, and
        must not lowercase a name that arrived intact from anywhere else.

        Returns ``None`` rather than raising when either name is blank.
        :class:`SurfaceSource` requires both members to be non-empty, and this
        projector is called outside any ``try`` (see
        ``SurfaceLedgerOperationOutcomePresenter``) — a ValidationError here
        would turn a nameless tool into a failed tool call. An unnamed source is
        exactly the "unknown tool" the note already has a sentence for.
        """

        server = builtin.display_name(server_name)
        tool = builtin.display_name(tool_name)
        if not server or not tool:
            return None
        return SurfaceSource(server=server, tool=tool)

    # -- ladder ---------------------------------------------------------------

    def _resolve_ladder(
        self,
        server_name: str,
        tool_name: str,
        output: Mapping[str, object],
        source: SurfaceSource | None,
    ) -> _LadderResult:
        """Climb the ladder over the already-unwrapped payload.

        Ordered strongest-first, and the order is the design (floor PRD §3.4)::

            exact (server, tool)  →  exact shape hash  →  nearest-neighbour
            skeleton match        →  rung 0 inference

        The first two rungs are keyed on ``(server, tool)``, which is precisely
        why they miss every tool nobody has met yet — the audit found Linear's
        real create tool is ``save_issue`` and not the catalogued
        ``create_issue``, ``list_my_issues`` misses ``list_issues``, and a
        server the user added by URL is named after its host and misses
        everything. The two shape rungs read the payload instead of its label,
        so a *name* nobody catalogued no longer costs a curated render.

        Both shape rungs are :data:`SurfaceSpecRung.SHAPE_MATCH`: a spec found
        by structure, never a spec authored for this connector.

        ``source`` is passed through so an inferred spec carries the real
        connector provenance the schema requires; the inferrer stamps a generic
        placeholder when a call is nameless, rather than refusing to infer.

        Returns an empty result — never raises — if inference ever declines,
        which today it does only for a non-mapping the caller already rejected.
        """

        curated = builtin.lookup(server_name, tool_name)
        if curated is not None:
            return _LadderResult(curated, SurfaceSpecRung.BUILTIN)
        if self.store is not None:
            stored = self.store.get(server=server_name, tool=tool_name)
            if stored is not None:
                return _LadderResult(stored, SurfaceSpecRung.STORE)
        learned = self._learned_for_shape(output)
        if learned is not None:
            return _LadderResult(learned, SurfaceSpecRung.SHAPE_MATCH)
        matched = builtin.match_by_shape(output)
        if matched is not None:
            return _LadderResult(matched, SurfaceSpecRung.SHAPE_MATCH)
        inferred = SurfaceSpecInferrer.infer(output, source=source)
        if inferred is not None:
            return _LadderResult(inferred, SurfaceSpecRung.INFERRED)
        return _LadderResult(None, None)

    def _learned_for_shape(self, output: Mapping[str, object]) -> SurfaceSpec | None:
        """Read the learned cache by payload shape alone (PRD §3.6, AC14).

        Probed with ``isinstance`` rather than required on
        :class:`SurfaceSpecReadPort`, because that seam is frozen from PRD-02
        and every store injected before this rung existed must keep satisfying
        it. A store that cannot answer by shape simply has no rung here.

        The hash is computed over the **unwrapped** payload — the same value
        ``SurfaceGenerationScheduler`` hashes when it writes — or the cache
        would be written under one key and read under another and never hit.
        """

        if not isinstance(self.store, SurfaceSpecShapeReadPort):
            return None
        return self.store.get_by_shape(output_shape_hash=output_shape_hash(output))

    # -- URI construction -----------------------------------------------------

    def _build_uri(
        self,
        *,
        archetype: SurfaceArchetype,
        server_name: str,
        tool_name: str,
        output: Mapping[str, object],
        call_id: str | None,
    ) -> str:
        slug = builtin.server_slug(server_name) or "unknown"
        tool = builtin.tool_slug(tool_name) or "tool"
        identifier = self._derive_id(output, call_id)
        return f"{archetype.value}://{slug}/{tool}/{identifier}"

    @classmethod
    def _derive_id(cls, output: Mapping[str, object], call_id: str | None) -> str:
        raw = cls._first_id_field(output)
        if raw is not None:
            segment = _URI_SEGMENT_SAFE.sub("-", str(raw)).strip("-")
            if segment:
                return segment
        return cls._stable_hash(output, call_id)

    @classmethod
    def _first_id_field(cls, output: Mapping[str, object]) -> object | None:
        """Return the first present id-bearing scalar, top-level or one wrapper deep.

        Handles both flat outputs (``{"id": ...}``) and the common single-object
        envelope (``{"issue": {"identifier": ...}}``) without guessing across
        multiple nested objects.
        """

        for field in _ID_FIELDS:
            value = output.get(field)
            if cls._is_scalar_id(value):
                return value
        nested = [value for value in output.values() if isinstance(value, Mapping)]
        if len(nested) == 1:
            for field in _ID_FIELDS:
                value = nested[0].get(field)
                if cls._is_scalar_id(value):
                    return value
        return None

    @staticmethod
    def _is_scalar_id(value: object) -> bool:
        return isinstance(value, (str, int)) and not isinstance(value, bool)

    @staticmethod
    def _stable_hash(output: Mapping[str, object], call_id: str | None) -> str:
        if call_id:
            basis = call_id
        else:
            try:
                basis = json.dumps(output, sort_keys=True, default=str)
            except (TypeError, ValueError):
                basis = repr(sorted(output.keys()))
        digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()
        return digest[:_HASH_LEN]

    # -- archetype inference (spec-less case) ---------------------------------

    @staticmethod
    def _infer_archetype(output: Mapping[str, object]) -> SurfaceArchetype:
        """Coarse archetype for an output no rung produced a spec for.

        A top-level array of objects reads as a ``table``; everything else as a
        ``record``. Kept, though rung 0 makes it unreachable for the mappings
        this projector accepts: ``SurfaceSpecInferrer.infer`` is *typed*
        ``SurfaceSpec | None``, and a projector that answered ``None.archetype``
        the day that changed would fail the tool call outright. A one-expression
        fallback is cheaper than that risk.
        """

        for value in output.values():
            if (
                isinstance(value, list)
                and value
                and all(isinstance(item, Mapping) for item in value)
            ):
                return SurfaceArchetype.TABLE
        return SurfaceArchetype.RECORD


__all__ = [
    "InMemorySurfaceSpecStore",
    "SurfaceGenerationSchedulerPort",
    "SurfaceProjector",
    "SurfaceSpecReadPort",
    "SurfaceSpecShapeReadPort",
    "SurfaceSpecStorePort",
]
