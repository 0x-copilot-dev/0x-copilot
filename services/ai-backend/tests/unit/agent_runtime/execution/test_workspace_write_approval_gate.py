"""Approval-gate proof for host ``/workspace/`` writes (AC5 slice 3b).

Exercises the SAME ``FilesystemPermission(mode="interrupt")`` the runtime factory
installs (:func:`agent_runtime.execution.factory._workspace_write_permissions`)
through a REAL Deep Agents graph + the REAL ``HumanInTheLoopMiddleware`` (no
mocks of the interrupt machinery). It proves the security-critical contract:

* a ``write_file`` under ``/workspace/`` PAUSES the graph (a LangGraph interrupt)
  and does NOT reach the broker or snapshot a pre-image while un-approved;
* resuming with ``approve`` runs the backend mutation — the pre-image is
  snapshotted + emitted BEFORE the broker write, then the write lands;
* resuming with ``reject`` runs NOTHING — no snapshot, no broker mutation, the
  host file is untouched.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.state import StateBackend
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent_runtime.capabilities.desktop.workspace_backend import (
    BrokeredWorkspaceBackend,
    WorkspaceMount,
    WorkspaceMutationSnapshot,
)
from agent_runtime.execution.factory import _workspace_write_permissions
from tests.unit.agent_runtime.capabilities.desktop.fakes import (
    FakeBrokerFs,
    RecordingBroker,
)

RCX = "rcx_gate_pinned"
_MUTATION_ROUTES = ("/v1/fs/write", "/v1/fs/edit")


class _ScriptedModel(BaseChatModel):
    """A minimal chat model that replays canned assistant messages in order.

    Ignores bound tools (returns the next scripted message) so a test can drive a
    real Deep Agents graph to emit a specific ``write_file`` tool call without a
    live LLM.
    """

    responses: list[BaseMessage]
    i: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted-approval-gate"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = self.responses[min(self.i, len(self.responses) - 1)]
        self.i += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_ScriptedModel":
        return self


@dataclass(frozen=True)
class _Ref:
    sha256: str
    size: int


@dataclass
class _FakeSnapshotStore:
    """In-memory content-addressed store satisfying ``WorkspaceSnapshotStore``."""

    puts: list[bytes] = field(default_factory=list)

    def put(
        self, data: bytes, *, media_type: str = "", preview: str | None = None
    ) -> _Ref:
        self.puts.append(data)
        return _Ref(sha256=hashlib.sha256(data).hexdigest(), size=len(data))


@dataclass
class _RecordingEmitter:
    records: list[WorkspaceMutationSnapshot] = field(default_factory=list)

    async def __call__(self, record: WorkspaceMutationSnapshot) -> None:
        self.records.append(record)


class WorkspaceWriteGateMixin:
    """Build a real Deep Agents graph whose ``/workspace/`` writes are approval-gated."""

    GRANT_RW = "grant-rw"
    THREAD = {"configurable": {"thread_id": "gate-thread"}}

    @classmethod
    def _broker(cls, files: dict[str, bytes]) -> RecordingBroker:
        broker = RecordingBroker(
            grants={cls.GRANT_RW: FakeBrokerFs(files=dict(files))},
            grant_meta={cls.GRANT_RW: {"mode": "read_write", "label": "proj"}},
        )
        broker.run_contexts[RCX] = {cls.GRANT_RW: "read_write"}
        return broker

    @classmethod
    def _agent(
        cls,
        broker: RecordingBroker,
        store: _FakeSnapshotStore,
        emitter: _RecordingEmitter,
    ) -> Any:
        backend = BrokeredWorkspaceBackend(
            client=broker.client(),
            mounts=[
                WorkspaceMount(name="proj", grant_id=cls.GRANT_RW, mode="read_write")
            ],
            run_capability_context=RCX,
            snapshot_store=store,
            snapshot_emitter=emitter,
        )
        composite = CompositeBackend(
            default=StateBackend(), routes={"/workspace/": backend}
        )
        model = _ScriptedModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {
                                "file_path": "/workspace/proj/a.txt",
                                "content": "NEW",
                            },
                            "id": "call_write_1",
                        }
                    ],
                ),
                AIMessage(content="done"),
            ]
        )
        # The SAME permission the factory installs for a writable run.
        return create_deep_agent(
            model=model,
            tools=[],
            permissions=list(_workspace_write_permissions(True)),
            backend=composite,
            checkpointer=InMemorySaver(),
        )

    @classmethod
    def _wired(
        cls, files: dict[str, bytes] | None = None
    ) -> tuple[Any, RecordingBroker, _FakeSnapshotStore, _RecordingEmitter]:
        broker = cls._broker(files or {"a.txt": b"OLD"})
        store = _FakeSnapshotStore()
        emitter = _RecordingEmitter()
        return cls._agent(broker, store, emitter), broker, store, emitter

    @staticmethod
    def _mutations(broker: RecordingBroker) -> list[str]:
        return [route for route, _h, _b in broker.requests if route in _MUTATION_ROUTES]


class TestWorkspaceWriteApprovalGate(WorkspaceWriteGateMixin):
    """A host write cannot run un-approved; it runs on approve and never on reject."""

    def test_unapproved_write_is_interrupted_and_never_reaches_the_host(self) -> None:
        agent, broker, store, emitter = self._wired()

        result = agent.invoke(
            {"messages": [("user", "overwrite the file")]}, config=self.THREAD
        )

        # The graph PAUSED on a real LangGraph interrupt instead of writing.
        assert "__interrupt__" in result
        # Nothing crossed to the broker, and no pre-image was snapshotted.
        assert self._mutations(broker) == []
        assert store.puts == []
        assert emitter.records == []
        # The user's real file is untouched.
        assert broker.grants[self.GRANT_RW].files["a.txt"] == b"OLD"

    def test_approved_write_snapshots_then_applies(self) -> None:
        agent, broker, store, emitter = self._wired()

        agent.invoke({"messages": [("user", "overwrite")]}, config=self.THREAD)
        # No write yet — still paused.
        assert self._mutations(broker) == []

        agent.invoke(
            Command(resume={"decisions": [{"type": "approve"}]}), config=self.THREAD
        )

        # Pre-image durably captured + referenced BEFORE the broker overwrite.
        assert store.puts == [b"OLD"]
        assert len(emitter.records) == 1
        record = emitter.records[0]
        assert record.op == "overwrite"
        assert record.path == "/proj/a.txt"
        assert record.object_sha256 == hashlib.sha256(b"OLD").hexdigest()
        # …and only then did the approved overwrite land on the host.
        assert self._mutations(broker) == ["/v1/fs/write"]
        assert broker.grants[self.GRANT_RW].files["a.txt"] == b"NEW"

    def test_rejected_write_runs_nothing(self) -> None:
        agent, broker, store, emitter = self._wired()

        agent.invoke({"messages": [("user", "overwrite")]}, config=self.THREAD)
        agent.invoke(
            Command(resume={"decisions": [{"type": "reject"}]}), config=self.THREAD
        )

        # A rejected tool call never executes: no snapshot, no broker mutation,
        # the host file is left exactly as it was.
        assert store.puts == []
        assert emitter.records == []
        assert self._mutations(broker) == []
        assert broker.grants[self.GRANT_RW].files["a.txt"] == b"OLD"
