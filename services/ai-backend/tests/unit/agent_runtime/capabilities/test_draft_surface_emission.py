"""Draft surface-emission tests for ``make_event_emitter`` (generative-UI PRD-02, AC2).

Drives ``DraftBackend`` through the emitter to assert every ``DRAFT_UPDATED``
payload carries a ``message://draft/<id>`` surface, and that v2 carries a
section-level diff. Also pins the ``RUNTIME_SURFACE_EMISSION=false`` byte-compat
and the no-store (no-diff) path.
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


class TestDraftSurfaceEmission:
    async def test_v1_carries_message_surface_without_diff(self) -> None:
        store = InMemoryDraftStore()
        producer = _CaptureProducer()
        backend = _backend(store=store, producer=producer)

        await backend.awrite(_path(), "# Greeting\nHello.")

        payload = producer.payloads()[0]
        assert payload["surface_uri"] == f"message://draft/{_DRAFT_ID}"
        surface = payload["surface"]
        assert surface["archetype"] == "message"
        assert "spec" not in surface["state"]
        assert surface["state"]["data"]["subject"] == "Greeting"
        # No prior version ⇒ no diff (excluded by exclude_none).
        assert "diff" not in surface

    async def test_v2_carries_section_level_diff(self) -> None:
        store = InMemoryDraftStore()
        producer = _CaptureProducer()
        backend = _backend(store=store, producer=producer)

        await backend.awrite(_path(), "# Greeting\nHello.")
        await backend.awrite(_path(), "# Greeting\nHello there.")

        v2_payload = producer.payloads()[1]
        assert v2_payload["surface_uri"] == f"message://draft/{_DRAFT_ID}"
        diff = v2_payload["surface"]["diff"]
        assert diff is not None
        changes = diff["changes"]
        assert {c["field"] for c in changes} == {"Greeting"}
        change = changes[0]
        assert change["old"] == "Hello."
        assert change["new"] == "Hello there."

    async def test_added_section_appears_as_diff_addition(self) -> None:
        store = InMemoryDraftStore()
        producer = _CaptureProducer()
        backend = _backend(store=store, producer=producer)

        await backend.awrite(_path(), "# Greeting\nHello.")
        await backend.awrite(_path(), "# Greeting\nHello.\n\n# Ask\nCan we meet?")

        diff = producer.payloads()[1]["surface"]["diff"]
        added = [c for c in diff["changes"] if c["field"] == "Ask"]
        assert len(added) == 1
        # exclude_none omits a null ``old`` ⇒ new-only entry marks an addition.
        assert added[0].get("old") is None
        assert added[0]["new"] == "Can we meet?"

    async def test_no_store_still_emits_surface_without_diff(self) -> None:
        store = InMemoryDraftStore()
        producer = _CaptureProducer()
        backend = _backend(store=store, producer=producer, with_store=False)

        await backend.awrite(_path(), "# Greeting\nHello.")
        await backend.awrite(_path(), "# Greeting\nHello there.")

        # Surface still attaches, but with no store the emitter cannot read the
        # prior version, so no diff is produced on v2.
        v2 = producer.payloads()[1]
        assert v2["surface_uri"] == f"message://draft/{_DRAFT_ID}"
        assert "diff" not in v2["surface"]

    async def test_emission_disabled_omits_surface(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RUNTIME_SURFACE_EMISSION", "false")
        store = InMemoryDraftStore()
        producer = _CaptureProducer()
        backend = _backend(store=store, producer=producer)

        await backend.awrite(_path(), "# Greeting\nHello.")

        payload = producer.payloads()[0]
        assert "surface" not in payload
        assert "surface_uri" not in payload
        # The draft_id path is unchanged.
        assert payload["draft_id"] == _DRAFT_ID
        assert DraftPath.for_draft_id(_DRAFT_ID).endswith(f"{_DRAFT_ID}.md")
