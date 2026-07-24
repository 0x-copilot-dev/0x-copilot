"""``make_event_emitter`` DRAFT_UPDATED emission after the v1 surface retirement
(PRD-E3 D4).

The v1 ``message`` surface attach (``DraftSurfaceProjector``) was retired: a
``DRAFT_UPDATED`` payload no longer carries ``surface`` / ``surface_uri``. Draft
surfaces render from the D1-wave ``write.staged`` / ``revision.added`` ledger
events instead. This file ports the still-relevant invariant from the old
``TestDraftSurfaceEmission``: the in-package emitter still emits a well-formed
``DRAFT_UPDATED`` event (draft_id / version / sections / status) through
``append_api_event`` — now proven to carry **no** surface keys, in every branch.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_runtime.capabilities.backends.draft_backend import (
    DraftBackend,
    make_event_emitter,
)
from agent_runtime.persistence.records import DraftPath
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


_DRAFT_ID = "deadbeefcafe1234deadbeefcafe1234"


def _path() -> str:
    return f"/{_DRAFT_ID}.md"


class _CaptureProducer:
    """Fake RuntimeEventProducer capturing every ``append_api_event`` call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def append_api_event(self, **kwargs: object) -> None:
        self.calls.append(kwargs)

    def payloads(self) -> list[dict[str, object]]:
        return [call["payload"] for call in self.calls]  # type: ignore[return-value]


def _run() -> SimpleNamespace:
    return SimpleNamespace(run_id="run_1", conversation_id="conv_1")


def _backend(
    *,
    store: InMemoryDraftStore,
    producer: _CaptureProducer,
    with_store: bool = True,
) -> DraftBackend:
    emitter = make_event_emitter(
        event_producer=producer,
        run=_run(),
        store=store if with_store else None,
    )
    return DraftBackend(
        store=store,
        org_id="org_acme",
        conversation_id="conv_1",
        run_id="run_1",
        user_id="user_sarah",
        emit_event=emitter,
    )


class TestDraftEmissionIsSurfaceFree:
    async def test_first_write_emits_draft_updated_without_surface(self) -> None:
        store = InMemoryDraftStore()
        producer = _CaptureProducer()
        backend = _backend(store=store, producer=producer)

        await backend.awrite(_path(), "# Greeting\nHello.")

        payload = producer.payloads()[0]
        # Retirement invariant: no v1 surface decoration on DRAFT_UPDATED.
        assert "surface" not in payload
        assert "surface_uri" not in payload
        # The draft event itself is intact (ported non-surface invariant).
        assert payload["draft_id"] == _DRAFT_ID
        assert payload["version"] == 1
        assert {s["heading"] for s in payload["sections"]} == {"Greeting"}
        assert DraftPath.for_draft_id(_DRAFT_ID).endswith(f"{_DRAFT_ID}.md")

    async def test_second_write_still_surface_free(self) -> None:
        store = InMemoryDraftStore()
        producer = _CaptureProducer()
        backend = _backend(store=store, producer=producer)

        await backend.awrite(_path(), "# Greeting\nHello.")
        await backend.awrite(_path(), "# Greeting\nHello there.")

        v2_payload = producer.payloads()[1]
        assert "surface" not in v2_payload
        assert "surface_uri" not in v2_payload
        assert v2_payload["version"] == 2

    async def test_no_store_still_surface_free(self) -> None:
        store = InMemoryDraftStore()
        producer = _CaptureProducer()
        backend = _backend(store=store, producer=producer, with_store=False)

        await backend.awrite(_path(), "# Greeting\nHello.")

        payload = producer.payloads()[0]
        assert "surface" not in payload
        assert "surface_uri" not in payload
        assert payload["draft_id"] == _DRAFT_ID
