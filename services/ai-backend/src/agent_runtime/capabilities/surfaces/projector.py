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
   and it cannot fail, so a mapping-shaped payload now ALWAYS ships a spec. Async
   generation is still invited here (when a scheduler is wired): the model is a
   *refinement* of the inferred spec, no longer its only supplier, and its
   result arrives via ``surface_spec_generated`` and merges by URI (PRD-04).
5. **shaped** — the model, asked the *shaping* question rather than the
   spec-refinement one (:mod:`.shaping_answer`), and reached only through
   :meth:`SurfaceProjector.project`. It exists because rungs 1–4 all assume the
   payload has addressable structure and a measured matrix of eight real MCP
   shapes says five do not: an array at the root, prose, a markdown table, a CSV
   block, a multi-block result. For those the floor is not *worse* than the
   model — it has nothing at all, which is precisely when one model call is
   worth making. Its answer may also be **no surface**, which no spec can say.

That inversion is the point of the PRD: having a spec used to be the happy path
and everything else a failure state the user read as an apology ("No spec
matched …"). With rung 0 unconditional there is no failure state to word.

Rungs 4 and 5 are mutually exclusive by construction — rung 4 answers for a
mapping and rung 5 is only consulted when nothing answered — so a read costs **at
most one** model call whichever way it goes.

Every rung reads the payload **after**
:class:`~agent_runtime.capabilities.surfaces.infer.EnvelopeUnwrapper` has peeled
its wrapper envelopes and decoded its MCP content blocks, and that decoded value
is what ships as ``state.data`` — see :meth:`SurfaceProjector._decoded` for why
the two must be the same value, and why the projector no longer falls back to
the wrapper when the decode lands on something that is not a mapping.

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
class ShapedSurface:
    """What one model shaping answer became: a renderable pair, or a refusal.

    The shaping contract (:mod:`.shaping_answer`) deliberately stops short of a
    :class:`SurfaceSpec` — its answer carries a literal ``title`` and a spec is
    paths-only by construction — so *something* has to turn an answer into the
    two halves a renderer reads. That translation belongs to the seam's
    implementation, not to the projector, which is why this type is the seam's
    return value rather than the answer itself.

    ``spec is None`` is a **decline**: the model looked and said nothing here is
    worth drawing. It is a first-class answer, not a failure, and it is the one
    outcome that ends with no surface at all — which is exactly what no spec can
    express and why the shaping question was asked in the first place.

    ``data`` travels beside the spec because the two are one resolution: a
    ``generate`` answer's rows ARE the data, and shipping the spec while leaving
    the connector's payload underneath it would bind every path to nothing.
    """

    spec: SurfaceSpec | None = None
    data: object = None
    rung: SurfaceSpecRung | None = None

    @classmethod
    def declined(cls) -> "ShapedSurface":
        """The model's honest "nothing here" — no surface, not an empty one."""

        return cls()

    @property
    def renders(self) -> bool:
        return self.spec is not None


@runtime_checkable
class SurfaceShapingPort(Protocol):
    """Rung-5 seam: ask the model to shape a payload the floor cannot bind.

    Awaited, unlike :class:`SurfaceGenerationSchedulerPort`, and the difference
    is the point. A refinement improves a surface the user is already reading,
    so it fires and forgets. A shaping answer decides whether there is a surface
    *at all* and what it binds — emitting first and correcting later would put
    the wrong table on screen, which is the defect this rung exists to remove.

    ``None`` means the seam had no answer to give (no shaping model configured,
    a provider error, an unusable answer). It is distinct from
    :meth:`ShapedSurface.declined`, which is the model answering "no": one is an
    outage, the other is a judgement, and only the second suppresses the surface.
    Implementations must never raise — a display upgrade may not fail a read.
    """

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
        """Whether async model *refinement* should still be invited.

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

        "No spec at all" used to be included here and no longer is. It is not a
        spec worth refining; it is a payload with nothing to refine, and asking
        for a *refinement* of nothing is what produced specs whose paths bound
        the adapter's own envelope. That case is :attr:`wants_shaping` now, and
        the two are mutually exclusive so a read still costs at most one call.
        """

        return self.rung is SurfaceSpecRung.INFERRED

    @property
    def wants_shaping(self) -> bool:
        """Whether the shaping question is the one worth asking (rung 5).

        True only when the deterministic ladder produced nothing at all. That
        happens for exactly the payloads rungs 1–4 cannot address — the decoded
        value is not a mapping, so there is no key to point a dot-path at — and
        it is the one state where a model call buys a surface rather than a
        nicer version of one.
        """

        return self.spec is None


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
    shaper: SurfaceShapingPort | None = None

    def resolve(
        self,
        server_name: str,
        tool_name: str,
        output: object,
        *,
        call_id: str | None = None,
        tool_descriptor: object | None = None,
    ) -> SurfaceEnvelope | None:
        """The deterministic ladder only: rungs 1–4, no model, no awaiting.

        ``None`` when emission is disabled or ``output`` is not a mapping
        (str/None/list scalars have no surface). A payload that DECODES to a
        mapping always yields an envelope carrying a spec: curated, stored, or —
        when neither knows this tool — inferred from its own structure. When the
        curated and stored rungs both miss, an async refinement is also
        scheduled (given a wired scheduler and an enabled model) so the surface
        upgrades in place once ``surface_spec_generated`` lands.

        A payload that decodes to something that is NOT a mapping yields an
        envelope with **no spec** — the honest generic view over the decoded
        value. :meth:`project` is the entry point that can do better than that,
        by asking the model; this one is what a caller with no shaping seam
        gets, and it is byte-identical to what shipped before rung 5 existed for
        every payload the floor could already bind.
        """

        if not self.enabled:
            return None
        if not isinstance(output, Mapping):
            return None
        data = self._decoded(output)
        source = self._state_source(server_name, tool_name)
        ladder = self._resolve_ladder(server_name, tool_name, data, source)
        return self._floor_envelope(
            server_name=server_name,
            tool_name=tool_name,
            data=data,
            source=source,
            ladder=ladder,
            call_id=call_id,
            tool_descriptor=tool_descriptor,
        )

    async def project(
        self,
        server_name: str,
        tool_name: str,
        output: object,
        *,
        call_id: str | None = None,
        tool_descriptor: object | None = None,
    ) -> SurfaceEnvelope | None:
        """The whole ladder, rung 5 included. The production read-path entry.

        Identical to :meth:`resolve` whenever the deterministic floor produced a
        spec — same envelope, same timing, same scheduled refinement, and no
        model call is made here at all. The only payloads that reach the shaper
        are the ones the floor had nothing for, and for those the answer is
        awaited rather than merged in later, because it decides *whether* there
        is a surface. Emitting the floor's non-answer first and correcting it
        afterwards is how a canvas tab opens showing the adapter's own envelope
        and then, maybe, changes its mind.

        Three ways it ends:

        * the model renders ⇒ its spec and data, under a rung that records
          whether the values are the connector's (``selected``) or the model's
          (``generated``);
        * the model declines ⇒ ``None``. **No surface at all**, which is the
          honest outcome for an acknowledgement or an empty result and the one
          thing rungs 1–4 structurally cannot say;
        * no answer — no shaping model, a provider error, an unusable answer ⇒
          exactly what :meth:`resolve` would have returned. A user with no
          provider key is never worse off than before this rung existed.
        """

        if not self.enabled:
            return None
        if not isinstance(output, Mapping):
            return None
        data = self._decoded(output)
        source = self._state_source(server_name, tool_name)
        ladder = self._resolve_ladder(server_name, tool_name, data, source)
        floor = self._floor_envelope(
            server_name=server_name,
            tool_name=tool_name,
            data=data,
            source=source,
            ladder=ladder,
            call_id=call_id,
            tool_descriptor=tool_descriptor,
        )
        if not ladder.wants_shaping or self.shaper is None:
            return floor
        shaped = await self.shaper.shape(
            server=server_name,
            tool=tool_name,
            tool_descriptor=tool_descriptor,
            payload=data,
            source=source,
        )
        if shaped is None:
            return floor
        if not shaped.renders:
            return None
        return self._shaped_envelope(
            server_name=server_name,
            tool_name=tool_name,
            source=source,
            shaped=shaped,
            call_id=call_id,
        )

    # -- envelope assembly ----------------------------------------------------

    def _floor_envelope(
        self,
        *,
        server_name: str,
        tool_name: str,
        data: object,
        source: SurfaceSource | None,
        ladder: _LadderResult,
        call_id: str | None,
        tool_descriptor: object | None,
    ) -> SurfaceEnvelope:
        """The envelope the deterministic ladder produced, plus its refinement."""

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
            # The decoded payload, deliberately: a spec the model authors
            # against the wrapper binds paths that miss the data we ship.
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

    def _shaped_envelope(
        self,
        *,
        server_name: str,
        tool_name: str,
        source: SurfaceSource | None,
        shaped: ShapedSurface,
        call_id: str | None,
    ) -> SurfaceEnvelope:
        """The envelope a rendering shaping answer produced.

        The URI is derived from the SHAPED data and archetype rather than the
        floor's, because this is the only surface record that will be written
        for this call — there is no earlier one to stay compatible with, and an
        id built from a payload the surface does not draw is a join key pointing
        at the wrong thing.
        """

        assert shaped.spec is not None  # narrowed by ``ShapedSurface.renders``
        archetype = shaped.spec.archetype
        return SurfaceEnvelope(
            surface_uri=self._build_uri(
                archetype=archetype,
                server_name=server_name,
                tool_name=tool_name,
                output=shaped.data,
                call_id=call_id,
            ),
            archetype=archetype,
            spec_rung=shaped.rung,
            state=SurfaceState(spec=shaped.spec, source=source, data=shaped.data),
        )

    # -- payload normalisation ------------------------------------------------

    @staticmethod
    def _decoded(output: Mapping[str, object]) -> object:
        """Decode the payload once, before anything reads it.

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

        **It returns whatever the decode produced, including a non-mapping, and
        that is the fix rung 5 is built on.** This used to fall back to the
        original wrapper when the decode landed on a list or a string, on the
        reasoning that a mapping is what the rest of the ladder needs. What that
        produced was measured: a connector whose real answer is a JSON array
        arrives as ``{"result": [{"type": "text", "text": "[…]"}]}``, the decode
        correctly yields the array, the fallback threw it away, and rung 0 then
        inferred a perfectly valid table of ``ID`` / ``Type`` / ``Text`` over
        ``items_path: "result"`` — the *adapter's* fields, with the entire
        connector payload in one cell. It looks like it worked. Five of eight
        measured payload shapes did this.

        Returning the decoded value means the floor honestly has no spec for
        those payloads (:meth:`SurfaceSpecInferrer.infer` answers only for a
        mapping), which is both the truth and the trigger for asking the model.
        """

        return EnvelopeUnwrapper.unwrap(output)

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
        output: object,
        source: SurfaceSource | None,
    ) -> _LadderResult:
        """Climb the ladder over the already-decoded payload.

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

        Returns an empty result — never raises — when the decoded payload is
        not a mapping. Every rung below reads keys: a curated spec is keyed on
        the pair but still binds paths, both shape rungs hash a mapping, and
        rung 0 answers ``None`` by contract. An empty result is therefore the
        honest report that the deterministic ladder has nothing here, and it is
        what invites rung 5 (:attr:`_LadderResult.wants_shaping`).
        """

        if not isinstance(output, Mapping):
            return _LadderResult(None, None)
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
        output: object,
        call_id: str | None,
    ) -> str:
        slug = builtin.server_slug(server_name) or "unknown"
        tool = builtin.tool_slug(tool_name) or "tool"
        identifier = self._derive_id(output, call_id)
        return f"{archetype.value}://{slug}/{tool}/{identifier}"

    @classmethod
    def _derive_id(cls, output: object, call_id: str | None) -> str:
        raw = cls._first_id_field(output)
        if raw is not None:
            segment = _URI_SEGMENT_SAFE.sub("-", str(raw)).strip("-")
            if segment:
                return segment
        return cls._stable_hash(output, call_id)

    @classmethod
    def _first_id_field(cls, output: object) -> object | None:
        """Return the first present id-bearing scalar, top-level or one wrapper deep.

        Handles both flat outputs (``{"id": ...}``) and the common single-object
        envelope (``{"issue": {"identifier": ...}}``) without guessing across
        multiple nested objects. A payload that decoded to a list or a string
        names no id field, so it falls through to the stable hash.
        """

        if not isinstance(output, Mapping):
            return None
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
    def _stable_hash(output: object, call_id: str | None) -> str:
        if call_id:
            basis = call_id
        else:
            try:
                basis = json.dumps(output, sort_keys=True, default=str)
            except (TypeError, ValueError):
                basis = repr(output)
        digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()
        return digest[:_HASH_LEN]

    # -- archetype inference (spec-less case) ---------------------------------

    @staticmethod
    def _infer_archetype(output: object) -> SurfaceArchetype:
        """Coarse archetype for a payload no rung produced a spec for.

        An array of objects — at the root, or under any top-level key — reads as
        a ``table``; everything else as a ``record``. Rung 0 makes it
        unreachable for a payload that DECODES to a mapping, but a payload that
        decodes to something else has no rung 0 by contract, so this is the live
        answer for exactly the shapes rung 5 was built for. It also stays as the
        narrowing for ``SurfaceSpecInferrer.infer``'s ``SurfaceSpec | None``
        return: a projector that answered ``None.archetype`` the day that
        changed would fail the tool call outright.
        """

        candidates: tuple[object, ...] = (
            (output,) if not isinstance(output, Mapping) else tuple(output.values())
        )
        for value in candidates:
            if (
                isinstance(value, list)
                and value
                and all(isinstance(item, Mapping) for item in value)
            ):
                return SurfaceArchetype.TABLE
        return SurfaceArchetype.RECORD


__all__ = [
    "InMemorySurfaceSpecStore",
    "ShapedSurface",
    "SurfaceGenerationSchedulerPort",
    "SurfaceProjector",
    "SurfaceShapingPort",
    "SurfaceSpecReadPort",
    "SurfaceSpecShapeReadPort",
    "SurfaceSpecStorePort",
]
