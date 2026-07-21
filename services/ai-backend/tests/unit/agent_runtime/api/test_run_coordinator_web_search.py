"""Per-run web-search toggle threads from the request onto the sealed context.

Closes the full loop for the FTUE web-search toggle (README §7.4):
``CreateRunRequest.web_search_enabled`` -> ``AgentRuntimeContext.web_search_enabled``
-> ``WebSearchToolRegistry`` capability filter. Reuses the BYOK coordinator
harness (in-memory store, no env keys, a user key satisfies the credential gate)
so a real run seals and enqueues.
"""

from __future__ import annotations

from runtime_api.schemas import CreateRunRequest
from tests.unit.agent_runtime.api.test_run_coordinator_byok import (
    _ORG_ID,
    _USER_ID,
    ByokCoordinatorMixin,
)


def _run_request(
    conversation_id: str, *, web_search_enabled: bool | None
) -> CreateRunRequest:
    kwargs: dict[str, object] = {
        "conversation_id": conversation_id,
        "org_id": _ORG_ID,
        "user_id": _USER_ID,
        "user_input": "hello",
        "model": {"provider": "openai", "model_name": "gpt-5.4-mini"},
    }
    if web_search_enabled is not None:
        kwargs["web_search_enabled"] = web_search_enabled
    return CreateRunRequest(**kwargs)


def test_create_run_request_web_search_defaults_true() -> None:
    request = CreateRunRequest(
        conversation_id="conv_1",
        org_id="org_1",
        user_id="user_1",
        user_input="hi",
    )
    assert request.web_search_enabled is True


class TestWebSearchToggleThreadsToContext(ByokCoordinatorMixin):
    async def test_defaults_to_enabled_when_omitted(self) -> None:
        run_coordinator, store, conversation_id = await self._build(with_key=True)

        await run_coordinator.create_run(
            _run_request(conversation_id, web_search_enabled=None)
        )

        assert store.run_commands[0].runtime_context.web_search_enabled is True

    async def test_disabled_flag_flows_onto_sealed_context(self) -> None:
        run_coordinator, store, conversation_id = await self._build(with_key=True)

        await run_coordinator.create_run(
            _run_request(conversation_id, web_search_enabled=False)
        )

        assert store.run_commands[0].runtime_context.web_search_enabled is False
