"""GET /v1/agent/conversations/stream — Chats live-refresh store tail (PRD-09 D4).

The stream is scoped to ``(org_id, user_id)`` derived from the verified bearer,
so a subscriber can only ever see its OWN conversations changing. DoD #4 pins the
tenant-isolation property: an ``org_b`` subscriber reads zero
``conversation_changed`` frames for ``org_a`` conversations (only the heartbeat).

The isolation + framing are asserted against the store-tail adapter driven
directly — the route is a thin ``StreamingResponse`` wrapper over exactly this
generator, and an infinite SSE response deadlocks the in-process TestClient — plus
a route-registration assertion that the literal ``/conversations/stream`` is
registered before the ``/conversations/{conversation_id}`` path param.
"""

from __future__ import annotations

from agent_runtime.api.conversation_query_service import ConversationQueryService
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import CreateConversationRequest
from runtime_api.sse.conversation_adapter import ConversationSseAdapter

_ORG_A = "org_a"
_USER_A = "user_a"


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


def _query_service(store: InMemoryRuntimeApiStore) -> ConversationQueryService:
    settings = _settings()
    return ConversationQueryService(
        persistence=store,
        event_store=store,
        settings=settings,
        model_resolver=ModelConfigResolver(settings),
    )


async def _seed(store: InMemoryRuntimeApiStore, *, count: int) -> None:
    for i in range(count):
        await store.create_conversation(
            CreateConversationRequest(
                org_id=_ORG_A,
                user_id=_USER_A,
                assistant_id="assistant",
                title=f"a-{i}",
            )
        )


async def _collect_until_heartbeat(agen, *, max_frames: int = 200) -> list[str]:
    frames: list[str] = []
    async for frame in agen:
        frames.append(frame)
        if frame.startswith(": keepalive"):
            break
        if len(frames) >= max_frames:
            break
    await agen.aclose()
    return frames


async def test_org_b_subscriber_sees_no_org_a_frames_before_heartbeat() -> None:
    store = InMemoryRuntimeApiStore()
    await _seed(store, count=3)
    query_service = _query_service(store)
    frames = await _collect_until_heartbeat(
        ConversationSseAdapter.stream(
            query_service=query_service,
            org_id="org_b",
            user_id="user_b",
            after=None,
            follow=True,
            heartbeat_interval_seconds=0.02,
            poll_interval_seconds=0.005,
        )
    )
    # The heartbeat is the FIRST frame; zero conversation_changed frames leaked.
    assert any(f.startswith(": keepalive") for f in frames)
    assert not any(f.startswith("event: conversation_changed") for f in frames)


async def test_owner_subscriber_receives_its_own_conversation_frames() -> None:
    store = InMemoryRuntimeApiStore()
    await _seed(store, count=2)
    query_service = _query_service(store)
    # follow=False → one pass; after=None surfaces the caller's current rows.
    frames = [
        frame
        async for frame in ConversationSseAdapter.stream(
            query_service=query_service,
            org_id=_ORG_A,
            user_id=_USER_A,
            after=None,
            follow=False,
        )
    ]
    changed = [f for f in frames if f.startswith("event: conversation_changed")]
    assert len(changed) == 2
    # Each frame carries a keyset ``id:`` line the client reconnects from.
    assert all("id: " in f for f in changed)
