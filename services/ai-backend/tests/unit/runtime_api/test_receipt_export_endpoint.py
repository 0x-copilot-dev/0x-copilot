"""``GET /v1/agent/runs/{run_id}/receipt/export`` + its query service (PRD-E3 D2).

Real ``InMemoryRuntimeApiStore`` + ``RuntimeEventProducer``: scope mismatch ⇒
404; a non-terminal (still-running) run ⇒ 409; a terminal-but-FAILED / CANCELLED
run ⇒ a foldable bundle (E1 emits a receipt on every terminal path); the happy
path returns a bundle a ``ReceiptExportVerifier`` verifies; production without
``AUDIT_HMAC_KEY`` ⇒ 503 with a safe, key-free message.
"""

from __future__ import annotations

import httpx
import pytest
from copilot_audit_chain import AuditChainSigner

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.conversation_query_service import ConversationQueryService
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import AgentRuntimeContext, StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.receipt_export import (
    ReceiptExportUnavailable,
    ReceiptExportVerifier,
)
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    AgentRunStatus,
    CreateConversationRequest,
    CreateRunRequest,
    RunRecord,
    RuntimeApiEventType,
)


class ReceiptExportEndpointMixin:
    ORG = "org_e3"
    USER = "user_e3"

    async def _setup(self):
        store = InMemoryRuntimeApiStore()
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )
        model_resolver = ModelConfigResolver(settings)
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        run_coordinator = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=producer,
            settings=settings,
            model_resolver=model_resolver,
        )
        conv_coordinator = ConversationCoordinator(
            persistence=store,
            settings=settings,
            run_coordinator=run_coordinator,
        )
        conversation = await conv_coordinator.create_conversation(
            CreateConversationRequest(org_id=self.ORG, user_id=self.USER, title="E3")
        )
        run_response = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=self.ORG,
                user_id=self.USER,
                user_input="Read the issue.",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        cqs = ConversationQueryService(
            persistence=store,
            event_store=store,
            settings=settings,
            model_resolver=model_resolver,
        )
        return store, producer, cqs, store.runs[run_response.run_id]

    @staticmethod
    async def _append_ledger(producer: RuntimeEventProducer, run: RunRecord) -> None:
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.READ_EXECUTED,
            payload={
                "v": 1,
                "call_id": "call_1",
                "connector": "linear",
                "op": "get_issue",
                "latency_ms": 12,
                "payload_ref": "call:call_1",
            },
        )
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.SURFACE_CREATED,
            payload={
                "v": 1,
                "surface_id": "record://linear/get_issue/issue-1",
                "kind": "record",
                "source": {"connector": "linear", "op": "get_issue"},
                "title": "ENG-1 Fix",
                "payload_ref": "call:call_1",
            },
        )

    @staticmethod
    def _mark_terminal(
        store: InMemoryRuntimeApiStore, run: RunRecord, status: AgentRunStatus
    ) -> None:
        store.runs[run.run_id] = run.model_copy(update={"status": status})


class TestExportRunReceiptService(ReceiptExportEndpointMixin):
    async def test_scope_mismatch_is_404(self) -> None:
        store, producer, cqs, run = await self._setup()
        await self._append_ledger(producer, run)
        self._mark_terminal(store, run, AgentRunStatus.COMPLETED)
        with pytest.raises(RuntimeApiError) as exc:
            await cqs.export_run_receipt(
                org_id=self.ORG, user_id="someone_else", run_id=run.run_id
            )
        assert exc.value.http_status == 404

    async def test_unknown_run_is_404(self) -> None:
        _store, _producer, cqs, _run = await self._setup()
        with pytest.raises(RuntimeApiError) as exc:
            await cqs.export_run_receipt(
                org_id=self.ORG, user_id=self.USER, run_id="run_missing"
            )
        assert exc.value.http_status == 404

    async def test_non_terminal_run_is_409(self) -> None:
        # The freshly-created run is QUEUED (not terminal) ⇒ no sealed receipt.
        _store, producer, cqs, run = await self._setup()
        await self._append_ledger(producer, run)
        with pytest.raises(RuntimeApiError) as exc:
            await cqs.export_run_receipt(
                org_id=self.ORG, user_id=self.USER, run_id=run.run_id
            )
        assert exc.value.http_status == 409
        # Safe, leak-free message.
        assert "finished" in exc.value.envelope.safe_message

    async def test_completed_run_verifies(self) -> None:
        store, producer, cqs, run = await self._setup()
        await self._append_ledger(producer, run)
        self._mark_terminal(store, run, AgentRunStatus.COMPLETED)
        bundle = await cqs.export_run_receipt(
            org_id=self.ORG, user_id=self.USER, run_id=run.run_id
        )
        signer = AuditChainSigner.from_env(
            environment_env_var="RUNTIME_ENVIRONMENT", fail_closed=False
        )
        result = ReceiptExportVerifier(signer=signer).verify(
            bundle.model_dump(mode="json")
        )
        assert result.ok is True
        assert bundle.run_id == run.run_id
        # 2 ledger rows folded + 1 synthetic receipt row.
        assert len(bundle.rows) == 3
        assert bundle.rows[-1].event_type == "receipt.export"

    @pytest.mark.parametrize(
        "terminal",
        [AgentRunStatus.FAILED, AgentRunStatus.CANCELLED, AgentRunStatus.TIMED_OUT],
    )
    async def test_non_completed_terminal_still_exports(
        self, terminal: AgentRunStatus
    ) -> None:
        # E1 emits a receipt on EVERY terminal path — failed/cancelled/timed_out
        # runs have a foldable receipt too (mirror E1's rule, not COMPLETED-only).
        store, producer, cqs, run = await self._setup()
        await self._append_ledger(producer, run)
        self._mark_terminal(store, run, terminal)
        bundle = await cqs.export_run_receipt(
            org_id=self.ORG, user_id=self.USER, run_id=run.run_id
        )
        assert len(bundle.rows) == 3

    async def test_production_without_key_raises_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store, producer, cqs, run = await self._setup()
        await self._append_ledger(producer, run)
        self._mark_terminal(store, run, AgentRunStatus.COMPLETED)
        monkeypatch.setenv("RUNTIME_ENVIRONMENT", "production")
        monkeypatch.delenv("AUDIT_HMAC_KEY", raising=False)
        with pytest.raises(ReceiptExportUnavailable) as exc:
            await cqs.export_run_receipt(
                org_id=self.ORG, user_id=self.USER, run_id=run.run_id
            )
        # Safe message — no key/env detail leaks.
        message = str(exc.value)
        assert "AUDIT_HMAC_KEY" not in message
        assert "not available" in message


class TestExportRunReceiptHttp(ReceiptExportEndpointMixin):
    async def _app_and_run(self, status: AgentRunStatus):
        store, producer, _cqs, run = await self._setup()
        await self._append_ledger(producer, run)
        self._mark_terminal(store, run, status)
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )
        ports = RuntimeAdapterFactory.from_store(store)
        app = RuntimeApiAppFactory.create_app(ports=ports, settings=settings)
        return app, run

    async def test_http_happy_path_returns_verifiable_bundle(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        app, run = await self._app_and_run(AgentRunStatus.COMPLETED)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.get(
                f"/v1/agent/runs/{run.run_id}/receipt/export",
                params={"org_id": self.ORG, "user_id": self.USER},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["export_version"] == 1
        assert body["run_id"] == run.run_id
        signer = AuditChainSigner.from_env(
            environment_env_var="RUNTIME_ENVIRONMENT", fail_closed=False
        )
        assert ReceiptExportVerifier(signer=signer).verify(body).ok is True

    async def test_http_non_terminal_is_409(self) -> None:
        store, producer, _cqs, run = await self._setup()
        await self._append_ledger(producer, run)  # left QUEUED
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )
        ports = RuntimeAdapterFactory.from_store(store)
        app = RuntimeApiAppFactory.create_app(ports=ports, settings=settings)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.get(
                f"/v1/agent/runs/{run.run_id}/receipt/export",
                params={"org_id": self.ORG, "user_id": self.USER},
            )
        assert response.status_code == 409

    async def test_http_production_without_key_is_503(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, run = await self._app_and_run(AgentRunStatus.COMPLETED)
        monkeypatch.setenv("RUNTIME_ENVIRONMENT", "production")
        monkeypatch.delenv("AUDIT_HMAC_KEY", raising=False)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.get(
                f"/v1/agent/runs/{run.run_id}/receipt/export",
                params={"org_id": self.ORG, "user_id": self.USER},
            )
        assert response.status_code == 503
        assert "AUDIT_HMAC_KEY" not in response.text
