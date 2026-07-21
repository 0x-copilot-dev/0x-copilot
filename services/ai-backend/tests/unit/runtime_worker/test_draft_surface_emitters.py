"""Surface-emission tests for the two *production* DRAFT_UPDATED emitters (PRD-02b).

PRD-02 attached the ``message`` surface only inside
``draft_backend.make_event_emitter``. The worker's own draft closures —
``RuntimeRunHandler._draft_event_emitter`` (drafts written during a live run) and
``RuntimeApprovalHandler._emit_draft_updated`` (drafts mutated across an approval)
— built their own payloads and bypassed it, so their events shipped without a
surface. These tests pin that both now carry a top-level ``surface_uri`` +
``surface`` envelope (with a section-level diff on v2), share the single builder,
and honour ``RUNTIME_SURFACE_EMISSION=false``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
from runtime_api.schemas import RuntimeApiEventType
from runtime_worker.handlers.approval import RuntimeApprovalHandler
from runtime_worker.handlers.run import RuntimeRunHandler

from tests.unit.agent_runtime.persistence.test_drafts import _draft_id, _record

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


_RUN = SimpleNamespace(
    run_id="run_1",
    conversation_id="conv_1",
    org_id="org_acme",
    user_id="user_sarah",
)
_COMMAND = SimpleNamespace(
    run_id="run_1",
    conversation_id="conv_1",
    org_id="org_acme",
)


class _CapturePersistence:
    """Persistence stub: ``get_run`` returns a non-None run so the emitter proceeds."""

    async def get_run(self, **_kwargs: object) -> object:
        return _RUN


class _EventStore:
    pass


def _capture(handler: object) -> list[dict[str, object]]:
    """Replace ``append_api_event`` with a capturing sink; return the captured calls."""
    calls: list[dict[str, object]] = []

    async def _sink(**kwargs: object) -> None:
        calls.append(dict(kwargs))

    handler.event_producer.append_api_event = _sink  # type: ignore[attr-defined]
    return calls


def _draft_updated_payload(calls: list[dict[str, object]]) -> dict[str, object]:
    payload = next(
        call["payload"]
        for call in calls
        if call["event_type"] is RuntimeApiEventType.DRAFT_UPDATED
    )
    assert isinstance(payload, dict)
    return payload


class TestRunHandlerDraftEmitter:
    """``RuntimeRunHandler._draft_event_emitter`` — drafts written during a run."""

    def _handler(self, store: InMemoryDraftStore) -> RuntimeRunHandler:
        return RuntimeRunHandler(
            persistence=_CapturePersistence(),
            event_store=_EventStore(),
            draft_store=store,
        )

    async def test_v1_carries_message_surface_without_diff(self) -> None:
        store = InMemoryDraftStore()
        handler = self._handler(store)
        calls = _capture(handler)

        emit = handler._draft_event_emitter(_COMMAND)
        await emit(_record(version=1, content_text="# Greeting\nHello."))

        payload = _draft_updated_payload(calls)
        assert payload["surface_uri"] == f"message://draft/{_draft_id()}"
        surface = payload["surface"]
        assert isinstance(surface, dict)
        assert surface["archetype"] == "message"
        assert surface["state"]["data"]["body"] == "# Greeting\nHello."
        # v1: no prior version ⇒ exclude_none drops the diff.
        assert "diff" not in surface

    async def test_v2_carries_top_level_surface_with_section_diff(self) -> None:
        store = InMemoryDraftStore()
        await store.insert_version(
            _record(version=1, content_text="# Greeting\nHello.")
        )
        handler = self._handler(store)
        calls = _capture(handler)

        emit = handler._draft_event_emitter(_COMMAND)
        await emit(_record(version=2, content_text="# Greeting\nHello there."))

        payload = _draft_updated_payload(calls)
        assert payload["surface_uri"] == f"message://draft/{_draft_id()}"
        diff = payload["surface"]["diff"]
        assert diff is not None
        changes = diff["changes"]
        assert {c["field"] for c in changes} == {"Greeting"}
        assert changes[0]["old"] == "Hello."
        assert changes[0]["new"] == "Hello there."

    async def test_disabled_flag_omits_surface(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RUNTIME_SURFACE_EMISSION", "false")
        store = InMemoryDraftStore()
        await store.insert_version(
            _record(version=1, content_text="# Greeting\nHello.")
        )
        handler = self._handler(store)
        calls = _capture(handler)

        emit = handler._draft_event_emitter(_COMMAND)
        await emit(_record(version=2, content_text="# Greeting\nHello there."))

        payload = _draft_updated_payload(calls)
        assert "surface" not in payload
        assert "surface_uri" not in payload
        # The pre-surface payload is otherwise intact.
        assert payload["draft_id"] == _draft_id()
        assert payload["version"] == 2


class TestApprovalHandlerDraftEmitter:
    """``RuntimeApprovalHandler._emit_draft_updated`` — drafts mutated across approval."""

    def _handler(self, store: InMemoryDraftStore) -> RuntimeApprovalHandler:
        return RuntimeApprovalHandler(
            persistence=_CapturePersistence(),
            event_store=_EventStore(),
            draft_store=store,
        )

    async def test_v2_carries_top_level_surface_with_section_diff(self) -> None:
        store = InMemoryDraftStore()
        await store.insert_version(
            _record(version=1, content_text="# Greeting\nHello.")
        )
        handler = self._handler(store)
        calls = _capture(handler)

        await handler._emit_draft_updated(
            run=_RUN,
            record=_record(version=2, content_text="# Greeting\nHello there."),
        )

        payload = _draft_updated_payload(calls)
        assert payload["surface_uri"] == f"message://draft/{_draft_id()}"
        surface = payload["surface"]
        assert surface["archetype"] == "message"
        diff = surface["diff"]
        assert diff is not None
        assert {c["field"] for c in diff["changes"]} == {"Greeting"}
        assert diff["changes"][0]["new"] == "Hello there."

    async def test_disabled_flag_omits_surface(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RUNTIME_SURFACE_EMISSION", "false")
        store = InMemoryDraftStore()
        await store.insert_version(
            _record(version=1, content_text="# Greeting\nHello.")
        )
        handler = self._handler(store)
        calls = _capture(handler)

        await handler._emit_draft_updated(
            run=_RUN,
            record=_record(version=2, content_text="# Greeting\nHello there."),
        )

        payload = _draft_updated_payload(calls)
        assert "surface" not in payload
        assert "surface_uri" not in payload
        assert payload["version"] == 2
