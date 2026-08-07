"""Connector write-back — the lane that stages a Save and CANNOT send it.

Drives the real :class:`SurfaceWriteBackCoordinator` over the real
``WriteStager`` → ``RuntimeStageLedger`` → ``RuntimeEventProducer`` →
in-memory store, so the projector allow-list runs and the ledger round trip is
the one production takes. The only fakes are the two seams the design already
declares: the completion port and the connector write-op catalogue.

Three properties are asserted here, each by making it fail if the code stops
holding it:

* **Nothing dispatches.** The stager handed to the coordinator carries a SPY
  commit queue AND an allow-always policy — the pair that auto-applies a row-set
  everywhere else in the system. After a completed ``save`` the spy must still be
  empty, the stage must read ``STAGED``, and no decision may have been recorded.
* **The approved diff equals the sent payload.** The user's values are read back
  out of the LEDGER (not out of the object the composer returned) and compared to
  what they typed, because the worker dispatches from the fold, not from memory.
* **A save with no model fails loudly**, and leaves the ledger byte-identical.
"""

from __future__ import annotations

import json

import pytest

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.stage_ledger import RuntimeStageLedger
from agent_runtime.capabilities.surfaces.generator import SpecCompletionResult
from agent_runtime.capabilities.surfaces.write_back import (
    ApiShapingCredentials,
    RunNotFound,
    SurfaceNotFound,
    SurfaceWriteBackCoordinator,
    SurfaceWriteBackError,
    WriteOpsUnavailable,
)
from agent_runtime.capabilities.surfaces.write_mapping import (
    SurfaceRowEdit,
    WriteMappingRejected,
    WriteMappingUnavailable,
    WriteOpCandidate,
)
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.surfaces_v2.constants import Keys, Values
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType
from agent_runtime.surfaces_v2.rowset import RowFieldChange, RowStance
from agent_runtime.surfaces_v2.staging import StagedWriteStatus, WriteStager
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus, RunRecord
from tests.unit.rollout_testkit import legacy_staged_write_gate

pytestmark = pytest.mark.anyio

_ORG = "org_acme"
_USER = "user_sarah"
_OTHER_USER = "user_marcus"
_RUN = "run_writeback"
_CONV = "conv_writeback"
_SURFACE = "surface_issues"
_CONNECTOR = "linear"
_READ_OP = "list_issues"
_WRITE_OP = "update_issue"

# The three values the user typed. Chosen so a paraphrase, a coercion and a trim
# would each be visible in the assertion.
_NEW_PRIORITY = 3
_NEW_TITLE = "Ship the thing "
_NEW_BLOCKED = True


class _SpyQueue:
    """Records every enqueue. On the write-back lane it MUST stay empty."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def enqueue_stage_commit(self, **kwargs) -> None:  # noqa: ANN003
        self.calls.append(kwargs)


class _AllowAlwaysPolicy:
    """The FR-C8 allow-always resolver, i.e. the branch that must not fire here."""

    def bypass_for(self, *, connector: str, op: str) -> bool:  # noqa: ARG002
        return True


class _FakeWriteOps:
    """The injected connector catalogue. Records the scope it was asked about."""

    def __init__(
        self,
        *,
        candidates: tuple[WriteOpCandidate, ...] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._candidates = candidates
        self._error = error
        self.asked: list[dict[str, str]] = []

    async def write_ops(
        self, *, org_id: str, user_id: str, connector: str
    ) -> tuple[WriteOpCandidate, ...]:
        self.asked.append(
            {"org_id": org_id, "user_id": user_id, "connector": connector}
        )
        if self._error is not None:
            raise self._error
        return self._candidates or ()


class _FakeCompletion:
    """Answers with whatever the test hands it; records the prompts it saw."""

    def __init__(self, candidate: object) -> None:
        self.candidate = candidate
        self.prompts: list[tuple[str, str]] = []

    async def complete(self, *, system: str, user: str) -> SpecCompletionResult:
        self.prompts.append((system, user))
        return SpecCompletionResult(candidate=self.candidate, raw_text="")


class WriteBackHarnessMixin:
    """Real ledger + real stager (spy queue, allow-always policy) + fake seams."""

    HONEST_ANSWER: dict[str, object] = {
        "op": _WRITE_OP,
        "args": [
            {"arg": "id", "source": "row", "key": "id"},
            {"arg": "priority", "source": "edited", "key": "priority"},
            {"arg": "title", "source": "edited", "key": "title"},
            {"arg": "blocked", "source": "edited", "key": "blocked"},
        ],
    }

    def make_store(self) -> InMemoryRuntimeApiStore:
        store = InMemoryRuntimeApiStore()
        store.runs[_RUN] = self.run_record()
        store.events_by_run.setdefault(_RUN, [])
        return store

    @staticmethod
    def run_record() -> RunRecord:
        return RunRecord(
            run_id=_RUN,
            conversation_id=_CONV,
            org_id=_ORG,
            user_id=_USER,
            user_message_id="msg_1",
            trace_id="trace_1",
            model_provider="openai",
            model_name="gpt-5.4-mini",
            status=AgentRunStatus.RUNNING,
            runtime_context=AgentRuntimeContext(
                user_id=_USER,
                org_id=_ORG,
                roles=["employee"],
                run_id=_RUN,
                trace_id="trace_1",
                model_profile={
                    "provider": "openai",
                    "model_name": "gpt-5.4-mini",
                    "max_input_tokens": 128000,
                    "timeout_seconds": 30,
                    "temperature": 0,
                    "supports_streaming": True,
                },
            ),
        )

    def make_stager(self, store: InMemoryRuntimeApiStore, queue: _SpyQueue):  # noqa: ANN201
        """A stager wired exactly as the app wires the /stages/apply one.

        Both execution seams are LIVE: the commit queue is present and the
        policy auto-applies everything. If the lane ever loses its narrowing,
        this stager will enqueue.
        """

        return WriteStager(
            draft_store=None,  # type: ignore[arg-type] — rowsets never touch drafts
            ledger=RuntimeStageLedger(
                event_producer=RuntimeEventProducer(
                    persistence=store, event_store=store
                )
            ),
            rollout_gate=legacy_staged_write_gate(),
            commit_queue=queue,
            policy_resolver=_AllowAlwaysPolicy(),
        )

    async def seed_read_surface(
        self,
        store: InMemoryRuntimeApiStore,
        *,
        surface_id: str = _SURFACE,
        connector: str = _CONNECTOR,
        op: str = _READ_OP,
    ) -> None:
        """Emit the ``surface.created`` a connector READ leaves on the ledger."""

        ledger = RuntimeStageLedger(
            event_producer=RuntimeEventProducer(persistence=store, event_store=store)
        )
        await ledger.emit(
            run=store.runs[_RUN],
            event_type_value=LedgerEventType.SURFACE_CREATED.value,
            payload={
                Keys.Field.V: Values.PAYLOAD_V,
                Keys.Field.SURFACE_ID: surface_id,
                Keys.Field.KIND: Values.KIND_TABLE,
                Keys.Field.SOURCE: {
                    Keys.Field.CONNECTOR: connector,
                    Keys.Field.OP: op,
                },
                Keys.Field.TITLE: "Issues",
                Keys.Field.PAYLOAD_REF: "call:abc",
            },
            summary=None,
        )

    def edits(self) -> tuple[SurfaceRowEdit, ...]:
        return (
            SurfaceRowEdit(
                row_key="ISS-1",
                title="Ship the thing",
                row={"id": "ISS-1", "team": "core", "priority": 1},
                changes=(
                    RowFieldChange(field="priority", old=1, new=_NEW_PRIORITY),
                    RowFieldChange(field="title", old="Ship it", new=_NEW_TITLE),
                    RowFieldChange(field="blocked", old=None, new=_NEW_BLOCKED),
                ),
            ),
        )

    def candidates(self) -> tuple[WriteOpCandidate, ...]:
        return (
            WriteOpCandidate(name=_WRITE_OP, description="Update one issue."),
            WriteOpCandidate(name="create_issue", description="Create an issue."),
        )

    def coordinator(
        self,
        store: InMemoryRuntimeApiStore,
        queue: _SpyQueue,
        *,
        write_ops: object | None = None,
        completion: object | None = None,
        environ: dict[str, str] | None = None,
    ) -> SurfaceWriteBackCoordinator:
        return SurfaceWriteBackCoordinator(
            persistence=store,
            event_store=store,
            stager=self.make_stager(store, queue),
            environ=environ if environ is not None else {"SURFACES_V2": "true"},
            write_ops=(
                write_ops
                if write_ops is not None
                else _FakeWriteOps(candidates=self.candidates())
            ),
            completion=(
                completion
                if completion is not None
                else _FakeCompletion(self.HONEST_ANSWER)
            ),
        )

    async def saved(
        self, **kwargs
    ) -> tuple[object, InMemoryRuntimeApiStore, _SpyQueue]:  # noqa: ANN003
        """Seed a read surface, save three cell edits, return (state, store, spy)."""

        store = self.make_store()
        queue = _SpyQueue()
        await self.seed_read_surface(store)
        coordinator = self.coordinator(store, queue, **kwargs)
        state = await coordinator.save(
            org_id=_ORG,
            user_id=_USER,
            run_id=_RUN,
            surface_id=_SURFACE,
            edits=self.edits(),
        )
        return state, store, queue

    @staticmethod
    def event_types(store: InMemoryRuntimeApiStore) -> list[str]:
        return [
            getattr(event.event_type, "value", str(event.event_type))
            for event in store.events_by_run.get(_RUN, [])
        ]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _reachable_bearing(root: object, names: tuple[str, ...]) -> list[str]:
    """Paths, under ``root``, to any object exposing one of ``names``.

    Walks dataclass fields transitively — which is exactly how these lanes are
    composed — so "this object cannot reach a sender" becomes a check a future
    field addition has to pass rather than a claim in a docstring.
    """

    import dataclasses

    seen: set[int] = set()
    found: list[str] = []

    def walk(node: object, path: str, depth: int) -> None:
        if node is None or depth > 6 or id(node) in seen:
            return
        seen.add(id(node))
        for name in names:
            if callable(getattr(node, name, None)):
                found.append(f"{path}.{name}")
        if dataclasses.is_dataclass(node) and not isinstance(node, type):
            for field in dataclasses.fields(node):
                walk(getattr(node, field.name, None), f"{path}.{field.name}", depth + 1)

    walk(root, "root", 0)
    return found


async def _stager_used_by(
    coordinator: SurfaceWriteBackCoordinator,
    edits: tuple[SurfaceRowEdit, ...],
) -> WriteStager:
    """Run one save and return the stager instance ``stage_rowset`` ran on."""

    captured: list[WriteStager] = []
    original = WriteStager.stage_rowset

    async def _spy(self, **kwargs):  # noqa: ANN001, ANN003, ANN202
        captured.append(self)
        return await original(self, **kwargs)

    WriteStager.stage_rowset = _spy  # type: ignore[method-assign]
    try:
        await coordinator.save(
            org_id=_ORG,
            user_id=_USER,
            run_id=_RUN,
            surface_id=_SURFACE,
            edits=edits,
        )
    finally:
        WriteStager.stage_rowset = original  # type: ignore[method-assign]
    assert len(captured) == 1
    return captured[0]


# ---------------------------------------------------------------------------
# Property 2 — a completed save leaves the write STAGED and un-applied
# ---------------------------------------------------------------------------


class TestNothingDispatchesWithoutAnApproval(WriteBackHarnessMixin):
    async def test_save_never_enqueues_a_commit(self) -> None:
        _state, _store, queue = await self.saved()

        assert queue.calls == []

    async def test_save_leaves_the_stage_staged(self) -> None:
        state, _store, _queue = await self.saved()

        assert state.status is StagedWriteStatus.STAGED
        assert state.approved_rev is None
        assert state.apply_result is None

    async def test_save_records_no_decision(self) -> None:
        # The FR-C8 auto-apply branch emits ``decision.recorded{actor: policy}``
        # before it enqueues. Neither may appear.
        state, store, _queue = await self.saved()

        assert state.decisions == ()
        assert LedgerEventType.DECISION_RECORDED.value not in self.event_types(store)

    async def test_save_emits_exactly_the_three_staging_events(self) -> None:
        _state, store, _queue = await self.saved()

        assert self.event_types(store) == [
            LedgerEventType.SURFACE_CREATED.value,  # the seeded read surface
            LedgerEventType.SURFACE_CREATED.value,  # the staged table surface
            LedgerEventType.WRITE_STAGED.value,
            LedgerEventType.REVISION_ADDED.value,
        ]

    async def test_every_staged_row_awaits_a_decision(self) -> None:
        state, _store, _queue = await self.saved()

        assert state.rows is not None
        assert all(row.decided_by is None for row in state.rows)
        assert all(row.apply_outcome is None for row in state.rows)
        assert all(row.stance is RowStance.WILL_APPLY for row in state.rows)

    async def test_the_lane_stager_holds_neither_queue_nor_bypass(self) -> None:
        """The narrowing is on the copy the lane stages through, by construction.

        Asserted on the object graph rather than on behaviour alone so that a
        future composition root which starts injecting a resolver by default
        cannot silently re-open the branch.
        """

        store = self.make_store()
        queue = _SpyQueue()
        await self.seed_read_surface(store)
        coordinator = self.coordinator(store, queue)

        narrowed = await _stager_used_by(coordinator, self.edits())

        assert narrowed.commit_queue is None
        assert narrowed.policy_resolver.bypass_for(connector="x", op="y") is False

    async def test_no_object_the_coordinator_can_reach_calls_a_tool(self) -> None:
        """Prove "no MCP client by construction" rather than trusting it.

        ``call_tool`` is the one method on ``McpClient`` that sends. Walking the
        coordinator's whole dataclass graph for it turns a docstring claim into
        a check that a future field addition has to pass.
        """

        store = self.make_store()
        coordinator = self.coordinator(store, _SpyQueue())

        assert _reachable_bearing(coordinator, ("call_tool", "invoke_tool")) == []

    async def test_the_narrowed_stager_cannot_reach_the_commit_queue(self) -> None:
        """The lane's own stager holds no path to ``enqueue_stage_commit``.

        The coordinator HOLDS the app's stager, which does carry the queue —
        that is the shared one the ``/stages/{id}/apply`` service uses. What
        must be true is that the copy this lane stages through has no such path
        at all, rather than having one and declining to use it.
        """

        store = self.make_store()
        queue = _SpyQueue()
        await self.seed_read_surface(store)
        coordinator = self.coordinator(store, queue)
        narrowed = await _stager_used_by(coordinator, self.edits())

        assert _reachable_bearing(coordinator.stager, ("enqueue_stage_commit",)) == [
            "root.commit_queue.enqueue_stage_commit"
        ]
        assert _reachable_bearing(narrowed, ("enqueue_stage_commit",)) == []


# ---------------------------------------------------------------------------
# Property 3 — what the user approves is what would be sent
# ---------------------------------------------------------------------------


class TestApprovedDiffEqualsSentPayload(WriteBackHarnessMixin):
    async def test_target_args_read_back_from_the_ledger_are_verbatim(self) -> None:
        # The worker dispatches from ``state.staged_row(key)``, which is folded
        # out of the ledger — so this is the value that would leave the machine.
        state, _store, _queue = await self.saved()

        row = state.staged_row("ISS-1")
        assert row is not None
        assert row.target_args == {
            "id": "ISS-1",
            "priority": _NEW_PRIORITY,
            "title": _NEW_TITLE,
            "blocked": _NEW_BLOCKED,
        }

    async def test_scalar_types_survive_the_ledger_round_trip(self) -> None:
        state, _store, _queue = await self.saved()

        args = state.staged_row("ISS-1").target_args
        assert type(args["priority"]) is int
        assert type(args["blocked"]) is bool
        assert type(args["title"]) is str

    async def test_displayed_diff_and_sent_payload_carry_the_same_values(
        self,
    ) -> None:
        # ``changes`` is what the client renders; ``target_args`` is what the
        # dispatcher sends. The WYSIWYG claim is that they never disagree.
        state, _store, _queue = await self.saved()

        row = state.staged_row("ISS-1")
        by_field = {change.field: change.new for change in row.changes}
        assert by_field["priority"] == row.target_args["priority"]
        assert by_field["title"] == row.target_args["title"]
        assert by_field["blocked"] == row.target_args["blocked"]

    async def test_a_json_round_trip_of_the_ledger_payload_is_lossless(self) -> None:
        # The desktop's file adapter persists the ledger as JSONL, so the
        # in-memory store alone would not prove the fold is stable on disk.
        _state, store, _queue = await self.saved()
        revision = next(
            event
            for event in store.events_by_run[_RUN]
            if getattr(event.event_type, "value", "")
            == LedgerEventType.REVISION_ADDED.value
        )

        replayed = json.loads(json.dumps(dict(revision.payload)))

        rows = replayed[Keys.Field.ROWSET][Keys.Field.ROWS]
        assert rows[0][Keys.Field.TARGET_ARGS] == {
            "id": "ISS-1",
            "priority": _NEW_PRIORITY,
            "title": _NEW_TITLE,
            "blocked": _NEW_BLOCKED,
        }
        assert list(rows[0][Keys.Field.TARGET_ARGS]) == [
            "id",
            "priority",
            "title",
            "blocked",
        ]

    async def test_model_never_sees_a_cell_value(self) -> None:
        store = self.make_store()
        await self.seed_read_surface(store)
        completion = _FakeCompletion(self.HONEST_ANSWER)
        coordinator = self.coordinator(store, _SpyQueue(), completion=completion)

        await coordinator.save(
            org_id=_ORG,
            user_id=_USER,
            run_id=_RUN,
            surface_id=_SURFACE,
            edits=self.edits(),
        )

        _system, user = completion.prompts[0]
        assert "ISS-1" not in user
        assert _NEW_TITLE.strip() not in user
        assert "core" not in user

    async def test_stage_title_carries_no_cell_value(self) -> None:
        # The title is a ledger-visible string, so it may name columns but
        # never their contents.
        _state, store, _queue = await self.saved()
        staged = next(
            event
            for event in store.events_by_run[_RUN]
            if getattr(event.event_type, "value", "")
            == LedgerEventType.WRITE_STAGED.value
        )
        surfaces = [
            event
            for event in store.events_by_run[_RUN]
            if getattr(event.event_type, "value", "")
            == LedgerEventType.SURFACE_CREATED.value
        ]
        title = surfaces[-1].payload[Keys.Field.TITLE]

        assert title == f"{_CONNECTOR} · {_WRITE_OP} · priority, title, blocked"
        assert staged.payload[Keys.Field.TARGET] == {
            Keys.Field.CONNECTOR: _CONNECTOR,
            Keys.Field.OP: _WRITE_OP,
        }


# ---------------------------------------------------------------------------
# Property 4 — a connector Save with no model fails LOUDLY
# ---------------------------------------------------------------------------


class TestSaveWithNoModelFailsLoud(WriteBackHarnessMixin):
    async def test_no_resolvable_model_raises_rather_than_staging_nothing(
        self,
    ) -> None:
        store = self.make_store()
        queue = _SpyQueue()
        await self.seed_read_surface(store)
        # No BYOK default shaping model exists for this provider, and the run
        # env names no override — the packaged-desktop posture exactly.
        store.runs[_RUN] = store.runs[_RUN].model_copy(
            update={"model_provider": "ollama"}
        )
        # ``completion`` omitted ⇒ the production path: the shaping ladder
        # resolves the model, and ``build_surface_write_mapper`` RAISES.
        coordinator = SurfaceWriteBackCoordinator(
            persistence=store,
            event_store=store,
            stager=self.make_stager(store, queue),
            environ={"SURFACES_V2": "true"},
            write_ops=_FakeWriteOps(candidates=self.candidates()),
        )

        with pytest.raises(WriteMappingUnavailable) as caught:
            await coordinator.save(
                org_id=_ORG,
                user_id=_USER,
                run_id=_RUN,
                surface_id=_SURFACE,
                edits=self.edits(),
            )

        assert caught.value.safe_message == (
            "Saving to this connector needs a configured model provider, and "
            "none is available for this run. Nothing was staged and nothing "
            "was sent."
        )

    async def test_a_refused_save_appends_nothing_to_the_ledger(self) -> None:
        store = self.make_store()
        queue = _SpyQueue()
        await self.seed_read_surface(store)
        before = len(store.events_by_run[_RUN])
        store.runs[_RUN] = store.runs[_RUN].model_copy(
            update={"model_provider": "ollama"}
        )
        coordinator = SurfaceWriteBackCoordinator(
            persistence=store,
            event_store=store,
            stager=self.make_stager(store, queue),
            environ={"SURFACES_V2": "true"},
            write_ops=_FakeWriteOps(candidates=self.candidates()),
        )

        with pytest.raises(WriteMappingUnavailable):
            await coordinator.save(
                org_id=_ORG,
                user_id=_USER,
                run_id=_RUN,
                surface_id=_SURFACE,
                edits=self.edits(),
            )

        assert len(store.events_by_run[_RUN]) == before
        assert queue.calls == []

    async def test_an_invented_value_refuses_the_save_and_stages_nothing(
        self,
    ) -> None:
        store = self.make_store()
        queue = _SpyQueue()
        await self.seed_read_surface(store)
        before = len(store.events_by_run[_RUN])
        coordinator = self.coordinator(
            store,
            queue,
            completion=_FakeCompletion(
                {
                    "op": _WRITE_OP,
                    "args": [
                        *self.HONEST_ANSWER["args"],
                        {
                            "arg": "description",
                            "source": "literal",
                            "value": "Ship the thing, urgently",
                        },
                    ],
                }
            ),
        )

        with pytest.raises(WriteMappingRejected) as caught:
            await coordinator.save(
                org_id=_ORG,
                user_id=_USER,
                run_id=_RUN,
                surface_id=_SURFACE,
                edits=self.edits(),
            )

        assert caught.value.safe_message == (
            "The proposed write contains a value that you did not enter and "
            "that was not read from the connector. Nothing was staged."
        )
        assert len(store.events_by_run[_RUN]) == before
        assert queue.calls == []

    async def test_unwired_catalogue_is_a_loud_503_not_a_quiet_success(self) -> None:
        store = self.make_store()
        queue = _SpyQueue()
        await self.seed_read_surface(store)
        before = len(store.events_by_run[_RUN])
        coordinator = SurfaceWriteBackCoordinator(
            persistence=store,
            event_store=store,
            stager=self.make_stager(store, queue),
            environ={"SURFACES_V2": "true"},
            completion=_FakeCompletion(self.HONEST_ANSWER),
        )

        with pytest.raises(WriteOpsUnavailable) as caught:
            await coordinator.save(
                org_id=_ORG,
                user_id=_USER,
                run_id=_RUN,
                surface_id=_SURFACE,
                edits=self.edits(),
            )

        assert caught.value.safe_message == (
            "The connector's write operations are not available, so this save "
            "cannot be prepared. Nothing was staged."
        )
        assert len(store.events_by_run[_RUN]) == before

    async def test_catalogue_failure_leaks_no_internal_detail(self) -> None:
        store = self.make_store()
        await self.seed_read_surface(store)
        coordinator = self.coordinator(
            store,
            _SpyQueue(),
            write_ops=_FakeWriteOps(
                error=RuntimeError("postgres://user:hunter2@10.0.0.1/mcp down")
            ),
        )

        with pytest.raises(WriteOpsUnavailable) as caught:
            await coordinator.save(
                org_id=_ORG,
                user_id=_USER,
                run_id=_RUN,
                surface_id=_SURFACE,
                edits=self.edits(),
            )

        assert "hunter2" not in str(caught.value)
        assert "10.0.0.1" not in str(caught.value)

    async def test_connector_with_no_write_op_is_refused(self) -> None:
        store = self.make_store()
        queue = _SpyQueue()
        await self.seed_read_surface(store)
        before = len(store.events_by_run[_RUN])
        coordinator = self.coordinator(
            store, queue, write_ops=_FakeWriteOps(candidates=())
        )

        with pytest.raises(WriteMappingRejected) as caught:
            await coordinator.save(
                org_id=_ORG,
                user_id=_USER,
                run_id=_RUN,
                surface_id=_SURFACE,
                edits=self.edits(),
            )

        assert caught.value.safe_message == (
            "This connector exposes no write operation to save into."
        )
        assert len(store.events_by_run[_RUN]) == before


# ---------------------------------------------------------------------------
# Scope + origin — read from the ledger, never believed from the client
# ---------------------------------------------------------------------------


class TestScopeAndOrigin(WriteBackHarnessMixin):
    async def test_another_users_run_is_not_found(self) -> None:
        store = self.make_store()
        await self.seed_read_surface(store)
        coordinator = self.coordinator(store, _SpyQueue())

        with pytest.raises(RunNotFound) as caught:
            await coordinator.save(
                org_id=_ORG,
                user_id=_OTHER_USER,
                run_id=_RUN,
                surface_id=_SURFACE,
                edits=self.edits(),
            )

        # 404, never 403 — a run id is not an authorization capability.
        assert caught.value.safe_message == "run_not_found"

    async def test_unknown_run_is_not_found(self) -> None:
        store = self.make_store()
        coordinator = self.coordinator(store, _SpyQueue())

        with pytest.raises(RunNotFound):
            await coordinator.save(
                org_id=_ORG,
                user_id=_USER,
                run_id="run_ghost",
                surface_id=_SURFACE,
                edits=self.edits(),
            )

    async def test_unknown_surface_is_not_found(self) -> None:
        store = self.make_store()
        await self.seed_read_surface(store)
        coordinator = self.coordinator(store, _SpyQueue())

        with pytest.raises(SurfaceNotFound):
            await coordinator.save(
                org_id=_ORG,
                user_id=_USER,
                run_id=_RUN,
                surface_id="surface_ghost",
                edits=self.edits(),
            )

    async def test_flag_off_means_no_v2_surface_exists(self) -> None:
        store = self.make_store()
        await self.seed_read_surface(store)
        coordinator = self.coordinator(
            store, _SpyQueue(), environ={"SURFACES_V2": "false"}
        )

        with pytest.raises(SurfaceNotFound):
            await coordinator.save(
                org_id=_ORG,
                user_id=_USER,
                run_id=_RUN,
                surface_id=_SURFACE,
                edits=self.edits(),
            )

    async def test_a_surface_with_no_connector_origin_has_no_write_target(
        self,
    ) -> None:
        store = self.make_store()
        await self.seed_read_surface(store, connector="", op="")
        coordinator = self.coordinator(store, _SpyQueue())

        with pytest.raises(SurfaceWriteBackError) as caught:
            await coordinator.save(
                org_id=_ORG,
                user_id=_USER,
                run_id=_RUN,
                surface_id=_SURFACE,
                edits=self.edits(),
            )

        assert caught.value.safe_message == (
            "This surface did not come from a connector read, so it has no "
            "write target."
        )

    async def test_the_connector_asked_about_is_the_one_on_the_ledger(self) -> None:
        # The client names a surface id, never a connector. Two surfaces exist;
        # the catalogue must be asked about the one the user was looking at.
        store = self.make_store()
        queue = _SpyQueue()
        await self.seed_read_surface(
            store, surface_id="surface_other", connector="jira"
        )
        await self.seed_read_surface(store)
        write_ops = _FakeWriteOps(candidates=self.candidates())
        coordinator = self.coordinator(store, queue, write_ops=write_ops)

        await coordinator.save(
            org_id=_ORG,
            user_id=_USER,
            run_id=_RUN,
            surface_id=_SURFACE,
            edits=self.edits(),
        )

        assert write_ops.asked == [
            {"org_id": _ORG, "user_id": _USER, "connector": _CONNECTOR}
        ]

    async def test_an_event_store_without_replay_is_not_found(self) -> None:
        class _NoReplay:
            pass

        store = self.make_store()
        coordinator = SurfaceWriteBackCoordinator(
            persistence=store,
            event_store=_NoReplay(),
            stager=self.make_stager(store, _SpyQueue()),
            environ={"SURFACES_V2": "true"},
            write_ops=_FakeWriteOps(candidates=self.candidates()),
            completion=_FakeCompletion(self.HONEST_ANSWER),
        )

        with pytest.raises(SurfaceNotFound):
            await coordinator.save(
                org_id=_ORG,
                user_id=_USER,
                run_id=_RUN,
                surface_id=_SURFACE,
                edits=self.edits(),
            )


class TestApiShapingCredentials:
    async def test_absent_resolver_is_no_credential_not_an_error(self) -> None:
        assert (
            await ApiShapingCredentials.resolve(
                resolver=None, org_id=_ORG, user_id=_USER
            )
            is None
        )

    async def test_resolver_failure_degrades_to_no_credential(self) -> None:
        class _Broken:
            async def resolve(self, *, org_id: str, user_id: str) -> dict:
                raise RuntimeError("sk-live-should-not-appear")

        assert (
            await ApiShapingCredentials.resolve(
                resolver=_Broken(), org_id=_ORG, user_id=_USER
            )
            is None
        )

    async def test_provider_keys_are_split_out_of_the_policy_snapshot(self) -> None:
        class _Resolver:
            async def resolve(self, *, org_id: str, user_id: str) -> dict:
                return {
                    "provider_keys": {"openai": "sk-test"},
                    "provider_endpoints": {"openai": "https://example.invalid"},
                    "privacy_mode": "strict",
                }

        credentials = await ApiShapingCredentials.resolve(
            resolver=_Resolver(), org_id=_ORG, user_id=_USER
        )

        assert credentials is not None
        assert credentials.provider_keys == {"openai": "sk-test"}
        assert credentials.provider_endpoints == {"openai": "https://example.invalid"}
        assert credentials.workspace_behavior_overrides is None
