"""The two *production* DRAFT_UPDATED emitters after the v1 surface retirement
(PRD-E3 D4).

``RuntimeRunHandler._draft_event_emitter`` (drafts written during a live run) and
``RuntimeApprovalHandler._emit_draft_updated`` (drafts mutated across an approval)
used to attach a ``message`` surface via ``DraftSurfaceProjector``; E3 retired
that. These tests port the still-relevant invariant — both emitters still emit a
well-formed ``DRAFT_UPDATED`` event (draft_id / version) — and pin the retirement:
the payload carries **no** ``surface`` / ``surface_uri`` key. Draft surfaces now
render from D1-wave ``write.staged`` / ``revision.added`` ledger events.
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

    async def test_emits_draft_updated_without_surface(self) -> None:
        store = InMemoryDraftStore()
        handler = self._handler(store)
        calls = _capture(handler)

        emit = handler._draft_event_emitter(_COMMAND)
        await emit(_record(version=1, content_text="# Greeting\nHello."))

        payload = _draft_updated_payload(calls)
        assert "surface" not in payload
        assert "surface_uri" not in payload
        assert payload["draft_id"] == _draft_id()
        assert payload["version"] == 1

    async def test_v2_write_still_surface_free(self) -> None:
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
        assert payload["version"] == 2


class TestApprovalHandlerDraftEmitter:
    """``RuntimeApprovalHandler._emit_draft_updated`` — drafts mutated across approval."""

    def _handler(self, store: InMemoryDraftStore) -> RuntimeApprovalHandler:
        return RuntimeApprovalHandler(
            persistence=_CapturePersistence(),
            event_store=_EventStore(),
            draft_store=store,
        )

    async def test_emits_draft_updated_without_surface(self) -> None:
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
        assert payload["draft_id"] == _draft_id()
        assert payload["version"] == 2
