"""BYOK Phase-2 — run-create seals user provider keys without persisting them.

End-to-end over the in-memory store:

* A user key satisfies the model-resolver credential gate when the
  deployment has no env key for the provider.
* The sealed context carries the key in memory only — ``user_policies_json``
  is stripped, and the key value appears in NO persisted surface (run
  record dump, outbox/claim payload, events, audit rows) nor in logs.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeRunCommand,
)

_ORG_ID = "org_byok"
_USER_ID = "user_byok"
_SECRET_KEY = "sk-unit-test-byok-secret-000000000000"


class FakePoliciesResolver:
    """Returns a snapshot with (or without) BYOK provider keys."""

    def __init__(self, *, with_key: bool) -> None:
        self._with_key = with_key

    async def resolve(self, *, org_id: str, user_id: str) -> dict[str, object]:
        snapshot: dict[str, object] = {"privacy": {"training_opt_out": True}}
        if self._with_key:
            snapshot["provider_keys"] = {"openai": _SECRET_KEY}
        return snapshot


class ByokCoordinatorMixin:
    """Build a coordinator over the in-memory store WITHOUT any env keys."""

    @staticmethod
    def _settings_without_env_keys() -> RuntimeSettings:
        # env_file points at a nonexistent path: RuntimeSettings.load
        # otherwise layers the developer's local ``services/ai-backend/.env``
        # (which may hold real provider keys) under ``environ`` — making the
        # "no env key configured" branch untestable on a dev machine.
        return RuntimeSettings.load(
            env_file="/nonexistent/byok-test.env",
            environ={
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            },
        )

    async def _build(
        self, *, with_key: bool
    ) -> tuple[RunCoordinator, InMemoryRuntimeApiStore, str]:
        store = InMemoryRuntimeApiStore()
        settings = self._settings_without_env_keys()
        event_producer = RuntimeEventProducer(
            persistence=store, event_store=store, on_event_appended=None
        )
        run_coordinator = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=event_producer,
            settings=settings,
            model_resolver=ModelConfigResolver(settings=settings),
            user_policies_resolver=FakePoliciesResolver(with_key=with_key),
        )
        conv_coordinator = ConversationCoordinator(
            persistence=store,
            settings=settings,
            run_coordinator=run_coordinator,
        )
        conversation = await conv_coordinator.create_conversation(
            CreateConversationRequest(
                org_id=_ORG_ID, user_id=_USER_ID, assistant_id="assistant_byok"
            )
        )
        return run_coordinator, store, conversation.conversation_id

    @staticmethod
    def _run_request(conversation_id: str) -> CreateRunRequest:
        return CreateRunRequest(
            conversation_id=conversation_id,
            org_id=_ORG_ID,
            user_id=_USER_ID,
            user_input="hello",
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )


class TestCreateRunWithUserKey(ByokCoordinatorMixin):
    async def test_user_key_satisfies_credential_gate_without_env_key(self) -> None:
        run_coordinator, store, conversation_id = await self._build(with_key=True)

        response = await run_coordinator.create_run(self._run_request(conversation_id))

        command = store.run_commands[0]
        assert command.runtime_context.provider_keys == {"openai": _SECRET_KEY}
        assert "provider_keys" not in command.runtime_context.user_policies_json
        assert store.runs[response.run_id].status == "queued"

    async def test_missing_user_and_env_key_rejects_with_settings_hint(self) -> None:
        run_coordinator, _store, conversation_id = await self._build(with_key=False)

        with pytest.raises(RuntimeApiError) as exc_info:
            await run_coordinator.create_run(self._run_request(conversation_id))

        assert exc_info.value.envelope.safe_message == (
            "Missing API key for model provider 'openai'. "
            "Add one in Settings -> Provider keys."
        )

    async def test_key_never_reaches_a_persisted_surface_or_logs(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        run_coordinator, store, conversation_id = await self._build(with_key=True)

        with caplog.at_level(logging.DEBUG):
            response = await run_coordinator.create_run(
                self._run_request(conversation_id)
            )

        run = store.runs[response.run_id]
        # Persisted run record projection (what postgres writes as
        # ``runtime_context_json``) must not carry the key.
        assert _SECRET_KEY not in run.runtime_context.model_dump_json()
        assert _SECRET_KEY not in json.dumps(run.model_dump(mode="json"), default=str)

        # Queue/outbox payload — the durable wire the worker claims from.
        claim = await store.claim_next(
            worker_id="w1",
            lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
        )
        assert claim is not None
        assert _SECRET_KEY not in json.dumps(claim.payload, default=str)
        # The JSON round-trip drops the in-memory field entirely. Strip the
        # queue-internal metadata keys the same way ``RuntimeWorker`` does.
        command_payload = {
            key: value
            for key, value in claim.payload.items()
            if key != "command_type" and not (key == "approval_id" and value is None)
        }
        rebuilt = RuntimeRunCommand.model_validate(command_payload)
        assert rebuilt.runtime_context.provider_keys == {}
        # ...while the persisted policy snapshot itself survives.
        assert rebuilt.runtime_context.user_policies_json == {
            "privacy": {"training_opt_out": True}
        }

        # Runtime events.
        for event in store.events_by_run[response.run_id]:
            assert _SECRET_KEY not in event.model_dump_json()

        # Audit rows.
        assert _SECRET_KEY not in json.dumps(store.audit_log, default=str)

        # Logs emitted during run-create.
        assert _SECRET_KEY not in caplog.text
