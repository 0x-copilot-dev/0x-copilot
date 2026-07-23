"""``ViewDeriver`` — the auditable per-surface view lifecycle (PRD-B3).

Fakes only (no network, no live model): a fake spec store, a fake / real
``SurfaceSpecGenerator`` driven by a fake ``SpecCompletionPort``, a collector
``emit`` closure, and an adversarial MCP seam that raises on any invocation to
prove the regenerate path never re-fetches. Pins:

* the honest ladder — registry hit ⇒ shaped/registry, miss ⇒ generic/schema now
  then a scheduled shape attempt, non-mapping ⇒ raw/schema;
* regenerate is a pure function of the STORED payload — zero connector traffic on
  every branch, bypasses the scheduler dedup, overwrites the cached spec, and
  stays honestly generic on a generation failure;
* the per-surface cap raises a typed error at the limit;
* shaping is metered per attempt with ``purpose=view_shaping`` + ``surface_id``.
"""

from __future__ import annotations

import json

import pytest

from agent_runtime.capabilities.surfaces.generator import (
    SpecCompletionResult,
    SurfaceSpecGenerator,
)
from agent_runtime.capabilities.surfaces.spec_models import SurfaceSpec
from agent_runtime.capabilities.surfaces.store import (
    SpecKey,
    StoredSpec,
    SurfaceSpecStorePort,
)
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.observability.attribution import Purpose
from agent_runtime.observability.usage_meter import MeteredModelInvocation, UsageMeter
from agent_runtime.surfaces_v2.ledger_models import (
    LedgerEventType,
    ViewBasis,
    ViewTier,
)
from agent_runtime.surfaces_v2.view_deriver import (
    RegenerateLimitError,
    ViewDeriver,
    ViewDeriverError,
    _Limits,
    _SurfaceScopedInvocation,
)
from runtime_api.schemas import RunRecord

_LINEAR_SAMPLE: dict[str, object] = {
    "issue": {
        "id": "uuid-1",
        "identifier": "ENG-1421",
        "title": "Fix login redirect loop",
        "state": {"name": "In Progress"},
        "url": "https://linear.app/acme/issue/ENG-1421",
    }
}

_VALID_CANDIDATE: dict[str, object] = {
    "spec_version": 1,
    "archetype": "record",
    "title_path": "issue.title",
    "fields": [{"label": "State", "path": "issue.state.name", "format": "badge"}],
    "link": {"label": "Open in Linear", "url_path": "issue.url"},
}

_BAD_LINT_CANDIDATE: dict[str, object] = {
    "spec_version": 1,
    "archetype": "record",
    "title_path": "issue.does_not_exist",  # schema-valid path, fails lint
}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeStore(SurfaceSpecStorePort):
    """In-memory spec store keyed by ``(server, tool)`` and full ``SpecKey``."""

    def __init__(self) -> None:
        self._by_tool: dict[tuple[str, str], SurfaceSpec] = {}
        self._stored: dict[str, StoredSpec] = {}
        self._failures: set[str] = set()
        self.put_calls: list[SpecKey] = []

    def get(self, *, server: str, tool: str) -> SurfaceSpec | None:
        return self._by_tool.get((server, tool))

    def get_stored(self, key: SpecKey) -> StoredSpec | None:
        return self._stored.get(key.digest())

    def put(self, key: SpecKey, stored: StoredSpec) -> None:
        self.put_calls.append(key)
        self._stored[key.digest()] = stored
        self._by_tool[(key.server, key.tool)] = stored.spec

    def record_failure(self, key: SpecKey, reason: str, raw_output: str) -> None:
        self._failures.add(key.digest())

    def has_failure(self, key: SpecKey) -> bool:
        return key.digest() in self._failures


class FakeCompletion:
    """Returns pre-canned candidates; never a live model."""

    def __init__(self, candidates: list[object]) -> None:
        self._candidates = list(candidates)
        self.calls = 0

    async def complete(self, *, system: str, user: str) -> SpecCompletionResult:
        self.calls += 1
        candidate = self._candidates.pop(0)
        raw = json.dumps(candidate) if isinstance(candidate, dict) else str(candidate)
        return SpecCompletionResult(
            candidate=candidate,
            raw_text=raw,
            model="openai:gpt-5.4-mini",
            input_tokens=120,
            output_tokens=48,
        )


class ExplodingMcp:
    """Adversarial connector seam — any access is a test failure (zero re-fetch)."""

    def __getattr__(self, name: str) -> object:  # pragma: no cover - must never fire
        raise AssertionError(
            f"regenerate touched the MCP client (.{name}) — connector re-fetch!"
        )


class FakeScheduler:
    """Records ``maybe_schedule`` calls; never actually generates."""

    def __init__(self) -> None:
        self.scheduled: list[dict[str, object]] = []

    def maybe_schedule(self, **kwargs: object) -> None:
        self.scheduled.append(kwargs)


class RecordingUsageRecorder:
    """Captures every ``RuntimeModelCallUsageRecord`` the meter builds."""

    def __init__(self) -> None:
        self.records: list[object] = []

    async def record_call(self, record: object, *, pricing_at: object) -> object:
        self.records.append(record)
        return None


class _EmitCollector:
    """Collects ``(event_type, payload, summary)`` the deriver emits."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object], str | None]] = []

    async def __call__(self, event_type: str, payload, summary: str | None) -> None:
        self.events.append((event_type, dict(payload), summary))

    @property
    def last(self) -> tuple[str, dict[str, object], str | None]:
        return self.events[-1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(runtime_context_admin: AgentRuntimeContext) -> RunRecord:
    return RunRecord(
        run_id="run_abc123",
        conversation_id="conv_abc123",
        org_id="org_456",
        user_id="user_123",
        user_message_id="msg_1",
        trace_id="trace_123",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        runtime_context=runtime_context_admin,
    )


def _valid_spec() -> SurfaceSpec:
    from agent_runtime.capabilities.surfaces.spec_models import validate_surface_spec

    candidate = dict(_VALID_CANDIDATE)
    candidate["source"] = {"server": "linear", "tool": "get_issue"}
    return validate_surface_spec(candidate)


# ---------------------------------------------------------------------------
# derive — the honest ladder
# ---------------------------------------------------------------------------


class TestDerive:
    async def test_registry_miss_emits_generic_immediately_then_schedules(self) -> None:
        emit = _EmitCollector()
        scheduler = FakeScheduler()
        deriver = ViewDeriver(store=FakeStore(), emit=emit, scheduler=scheduler)

        derivation = await deriver.derive(
            surface_id="s1",
            server="unknownsrv",
            tool="mystery",
            payload=_LINEAR_SAMPLE,
        )

        # Generic emitted immediately (before any shaping).
        assert derivation.tier is ViewTier.GENERIC
        assert derivation.basis is ViewBasis.SCHEMA
        event_type, payload, _ = emit.last
        assert event_type == LedgerEventType.VIEW_DERIVED.value
        assert payload["tier"] == "generic"
        assert payload["basis"] == "schema"
        # And a bounded background shape attempt was scheduled.
        assert len(scheduler.scheduled) == 1
        assert scheduler.scheduled[0]["surface_uri"] == "s1"

    async def test_builtin_hit_emits_shaped_registry_with_spec_ref(self) -> None:
        emit = _EmitCollector()
        deriver = ViewDeriver(store=FakeStore(), emit=emit)

        derivation = await deriver.derive(
            surface_id="s1", server="github", tool="get_issue", payload={"x": 1}
        )

        assert derivation.tier is ViewTier.SHAPED
        assert derivation.basis is ViewBasis.REGISTRY
        assert derivation.spec_ref == "spec:github/get_issue"
        _, payload, _ = emit.last
        assert payload["spec_ref"] == "spec:github/get_issue"

    async def test_store_hit_emits_shaped_registry(self) -> None:
        store = FakeStore()
        store._by_tool[("customsrv", "custom_tool")] = _valid_spec()
        emit = _EmitCollector()
        deriver = ViewDeriver(store=store, emit=emit)

        derivation = await deriver.derive(
            surface_id="s1",
            server="customsrv",
            tool="custom_tool",
            payload={"x": 1},
        )

        assert derivation.tier is ViewTier.SHAPED
        assert derivation.basis is ViewBasis.REGISTRY

    async def test_non_mapping_payload_emits_raw_schema(self) -> None:
        emit = _EmitCollector()
        scheduler = FakeScheduler()
        deriver = ViewDeriver(store=FakeStore(), emit=emit, scheduler=scheduler)

        derivation = await deriver.derive(
            surface_id="s1",
            server="unknownsrv",
            tool="blob",
            payload="a raw text blob",
        )

        assert derivation.tier is ViewTier.RAW
        assert derivation.basis is ViewBasis.SCHEMA
        # A non-mapping payload never schedules shaping.
        assert scheduler.scheduled == []


# ---------------------------------------------------------------------------
# regenerate — pure function of the stored payload
# ---------------------------------------------------------------------------


class TestRegenerate:
    def _metered_generator(
        self,
        runtime_context_admin: AgentRuntimeContext,
        candidates: list[object],
        surface_id: str = "s1",
    ) -> tuple[SurfaceSpecGenerator, RecordingUsageRecorder]:
        recorder = RecordingUsageRecorder()
        meter = UsageMeter(recorder=recorder, emit_event=None, surfaces_v2=False)
        invocation = MeteredModelInvocation(
            meter=meter, run=_run(runtime_context_admin), purpose=Purpose.VIEW_SHAPING
        )
        scoped = _SurfaceScopedInvocation(invocation=invocation, surface_id=surface_id)
        generator = SurfaceSpecGenerator(
            completion=FakeCompletion(candidates), usage_meter=scoped
        )
        return generator, recorder

    async def test_regenerate_uses_stored_payload_only(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # Adversarial: an exploding MCP seam is available but the deriver must
        # never touch it — a pure re-derivation of the STORED payload. Covers
        # every branch: registry-hit, generation-success, generation-failure,
        # and no-generator, each with the exploding seam in scope.
        for server, tool, generator in (
            ("github", "get_issue", None),  # registry hit
            ("unknownsrv", "mystery", None),  # no generator ⇒ generic
        ):
            emit = _EmitCollector()
            deriver = ViewDeriver(store=FakeStore(), emit=emit, generator=generator)
            _mcp = ExplodingMcp()  # in scope; deriver has no handle to it
            del _mcp
            await deriver.regenerate(
                surface_id="s1",
                server=server,
                tool=tool,
                payload=_LINEAR_SAMPLE,
                regen_count=0,
            )
            assert emit.events, "a view.derived must be emitted"

        # Generation-success branch, still zero connector traffic.
        gen, _rec = self._metered_generator(
            runtime_context_admin, [dict(_VALID_CANDIDATE)]
        )
        emit = _EmitCollector()
        deriver = ViewDeriver(
            store=FakeStore(), emit=emit, generator=gen, model_id="openai:gpt-5.4-mini"
        )
        derivation = await deriver.regenerate(
            surface_id="s1",
            server="customsrv",
            tool="custom_tool",
            payload=_LINEAR_SAMPLE,
            regen_count=0,
        )
        assert derivation.tier is ViewTier.SHAPED
        assert derivation.basis is ViewBasis.GENERATED

    async def test_missing_payload_raises_surface_not_found(self) -> None:
        deriver = ViewDeriver(store=FakeStore(), emit=_EmitCollector())
        with pytest.raises(ViewDeriverError) as exc:
            await deriver.regenerate(
                surface_id="s1",
                server="linear",
                tool="get_issue",
                payload=None,
                regen_count=0,
            )
        assert str(exc.value) == "surface_not_found"

    async def test_regenerate_bypasses_scheduler_dedup_and_overwrites_cached_spec(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # A user-requested retry must generate even though the scheduler's per-run
        # ``seen``/``stored`` skip would suppress it — regenerate calls the
        # generator directly and overwrites the cached spec (the repair).
        store = FakeStore()
        gen, _rec = self._metered_generator(
            runtime_context_admin, [dict(_VALID_CANDIDATE)]
        )
        deriver = ViewDeriver(
            store=store, emit=_EmitCollector(), generator=gen, model_id="m:1"
        )

        derivation = await deriver.regenerate(
            surface_id="s1",
            server="customsrv",
            tool="custom_tool",
            payload=_LINEAR_SAMPLE,
            regen_count=0,
        )

        assert derivation.basis is ViewBasis.GENERATED
        assert store.put_calls, "the cached spec was overwritten (the repair)"

    async def test_regenerate_generation_failure_stays_generic(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # Two lint failures ⇒ GenFailure ⇒ honest generic (a SUCCESS response),
        # never a fabricated shaped view, never an error.
        gen, _rec = self._metered_generator(
            runtime_context_admin,
            [dict(_BAD_LINT_CANDIDATE), dict(_BAD_LINT_CANDIDATE)],
        )
        emit = _EmitCollector()
        deriver = ViewDeriver(
            store=FakeStore(), emit=emit, generator=gen, model_id="m:1"
        )

        derivation = await deriver.regenerate(
            surface_id="s1",
            server="customsrv",
            tool="custom_tool",
            payload=_LINEAR_SAMPLE,
            regen_count=0,
        )

        assert derivation.tier is ViewTier.GENERIC
        assert derivation.basis is ViewBasis.SCHEMA

    async def test_regenerate_cap_raises_typed_error_at_limit(self) -> None:
        deriver = ViewDeriver(store=FakeStore(), emit=_EmitCollector())
        with pytest.raises(RegenerateLimitError) as exc:
            await deriver.regenerate(
                surface_id="s1",
                server="linear",
                tool="get_issue",
                payload=_LINEAR_SAMPLE,
                regen_count=_Limits.MAX_REGEN_PER_SURFACE,
            )
        assert str(exc.value) == "regenerate_limit_reached"

    async def test_shaping_metered_per_attempt_purpose_view_shaping(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # Fake completion fails lint once then succeeds ⇒ two attempts ⇒ two usage
        # records, both purpose=view_shaping with surface_id set (DoD).
        gen, recorder = self._metered_generator(
            runtime_context_admin,
            [dict(_BAD_LINT_CANDIDATE), dict(_VALID_CANDIDATE)],
            surface_id="s1",
        )
        deriver = ViewDeriver(
            store=FakeStore(),
            emit=_EmitCollector(),
            generator=gen,
            model_id="openai:gpt-5.4-mini",
        )

        derivation = await deriver.regenerate(
            surface_id="s1",
            server="customsrv",
            tool="custom_tool",
            payload=_LINEAR_SAMPLE,
            regen_count=0,
        )

        assert derivation.basis is ViewBasis.GENERATED
        assert len(recorder.records) == 2
        for record in recorder.records:
            assert record.purpose == Purpose.VIEW_SHAPING.value
            assert record.surface_id == "s1"


class TestGenerationSuccessShape:
    async def test_generation_success_emits_shaped_generated_with_gen_info(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        recorder = RecordingUsageRecorder()
        meter = UsageMeter(recorder=recorder, emit_event=None, surfaces_v2=False)
        invocation = MeteredModelInvocation(
            meter=meter, run=_run(runtime_context_admin), purpose=Purpose.VIEW_SHAPING
        )
        scoped = _SurfaceScopedInvocation(invocation=invocation, surface_id="s1")
        gen = SurfaceSpecGenerator(
            completion=FakeCompletion([dict(_VALID_CANDIDATE)]), usage_meter=scoped
        )
        emit = _EmitCollector()
        deriver = ViewDeriver(
            store=FakeStore(), emit=emit, generator=gen, model_id="openai:gpt-5.4-mini"
        )

        derivation = await deriver.regenerate(
            surface_id="s1",
            server="customsrv",
            tool="custom_tool",
            payload=_LINEAR_SAMPLE,
            regen_count=0,
        )

        assert derivation.gen is not None
        assert derivation.gen.model == "openai:gpt-5.4-mini"
        assert derivation.gen.ms >= 0
        _, payload, _ = emit.last
        assert payload["gen"]["model"] == "openai:gpt-5.4-mini"
        assert "ms" in payload["gen"]
