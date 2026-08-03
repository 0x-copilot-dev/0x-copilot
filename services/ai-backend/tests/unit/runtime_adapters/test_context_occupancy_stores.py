"""Conformance tests for the context-occupancy store adapters.

Every backend has to agree on three things, because the read API and the
capture seam will not know which one they are talking to: identity is the
measured attempt (not the row id), a run's series comes back oldest-first, and
a scope filter narrows to one context window rather than mixing two.

Both remaining adapters run without any external service, so this is
``tests/integration``; its SQL contract is pinned statically in
``tests/unit/agent_runtime/persistence/test_context_occupancy_migration.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_runtime.api.ports import ContextOccupancyStorePort
from agent_runtime.persistence.records import (
    RuntimeContextGraphScope,
    RuntimeContextOccupancyRecord,
)
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_adapters.in_memory import InMemoryRuntimeApiStore

_CREATED_AT = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
_ORG_A = "org-a"
_ORG_B = "org-b"
_RUN_A = "run-a"
_RUN_B = "run-b"


class ContextOccupancyStoreMixin:
    """Builds occupancy rows that differ only where a test needs them to."""

    def occupancy(
        self,
        *,
        model_call_id: str,
        attempt_ordinal: int = 1,
        org_id: str = _ORG_A,
        run_id: str = _RUN_A,
        graph_scope: RuntimeContextGraphScope = RuntimeContextGraphScope.ROOT,
        estimated_input_tokens: int = 1_200,
        provider_input_tokens: int | None = 1_180,
        created_at: datetime = _CREATED_AT,
        record_id: str | None = None,
    ) -> RuntimeContextOccupancyRecord:
        return RuntimeContextOccupancyRecord.from_measurement(
            org_id=org_id,
            run_id=run_id,
            conversation_id="conversation-a",
            model_call_id=model_call_id,
            attempt_ordinal=attempt_ordinal,
            graph_scope=graph_scope,
            provider="anthropic",
            model_family="claude-opus-5",
            context_window_tokens=200_000,
            estimated_input_tokens=estimated_input_tokens,
            provider_input_tokens=provider_input_tokens,
            segments=(
                {
                    "segment_class": "tools",
                    "label": ("agent_runtime.capabilities.backends:publish_artifact"),
                    "lifecycle": "resident",
                    "detail": "publish_artifact",
                    "byte_count": 2_600,
                    "estimated_tokens": 650,
                    "item_count": 1,
                    "counter_source": "tokenizer",
                },
            ),
            record_id=record_id,
            created_at=created_at,
        )


class TestContextOccupancyPortSurface:
    def test_every_adapter_implements_the_port(self) -> None:
        for adapter in (
            InMemoryRuntimeApiStore,
            FileRuntimeApiStore,
        ):
            assert issubclass(adapter, ContextOccupancyStorePort), adapter

    def test_the_port_exposes_no_mutating_surface(self) -> None:
        # An occupancy measurement describes a request already sent, so the
        # store deliberately offers append + read and nothing else.
        declared = {
            name for name in vars(ContextOccupancyStorePort) if not name.startswith("_")
        }
        assert declared == {"append_context_occupancy", "list_context_occupancy"}


class TestInMemoryContextOccupancy(ContextOccupancyStoreMixin):
    async def test_append_is_idempotent_on_the_measured_attempt(self) -> None:
        store = InMemoryRuntimeApiStore()
        first = self.occupancy(model_call_id="call-a", record_id="row-1")

        assert await store.append_context_occupancy(first)
        # Same attempt redelivered under a fresh transport id: one row, and the
        # stored row is the one that was measured first.
        assert not await store.append_context_occupancy(
            self.occupancy(
                model_call_id="call-a",
                record_id="row-2",
                estimated_input_tokens=99,
                provider_input_tokens=99,
            )
        )
        # A model retry is a different context against a different window, so
        # it is a second row rather than an overwrite (§6.3).
        assert await store.append_context_occupancy(
            self.occupancy(model_call_id="call-a", attempt_ordinal=2)
        )

        rows = await store.list_context_occupancy(org_id=_ORG_A, run_id=_RUN_A)
        assert [row.attempt_ordinal for row in rows] == [1, 2]
        assert rows[0].id == "row-1"
        assert rows[0].estimated_input_tokens == 1_200

    async def test_list_filters_by_graph_scope_and_never_mixes_windows(
        self,
    ) -> None:
        store = InMemoryRuntimeApiStore()
        root = self.occupancy(model_call_id="call-root")
        child = self.occupancy(
            model_call_id="call-child",
            graph_scope=RuntimeContextGraphScope.SUBAGENT,
            estimated_input_tokens=400,
            provider_input_tokens=390,
        )
        await store.append_context_occupancy(root)
        await store.append_context_occupancy(child)

        both = await store.list_context_occupancy(org_id=_ORG_A, run_id=_RUN_A)
        assert {row.model_call_id for row in both} == {"call-root", "call-child"}

        root_only = await store.list_context_occupancy(
            org_id=_ORG_A,
            run_id=_RUN_A,
            graph_scope=RuntimeContextGraphScope.ROOT,
        )
        assert [row.model_call_id for row in root_only] == ["call-root"]
        # The parent's free space ignores what the child put in its own window.
        assert root_only[0].free_tokens == 200_000 - 1_180

        child_only = await store.list_context_occupancy(
            org_id=_ORG_A,
            run_id=_RUN_A,
            graph_scope=RuntimeContextGraphScope.SUBAGENT,
        )
        assert [row.model_call_id for row in child_only] == ["call-child"]

    async def test_list_is_scoped_to_one_tenant_and_one_run(self) -> None:
        store = InMemoryRuntimeApiStore()
        await store.append_context_occupancy(self.occupancy(model_call_id="mine"))
        await store.append_context_occupancy(
            self.occupancy(model_call_id="other-run", run_id=_RUN_B)
        )
        await store.append_context_occupancy(
            self.occupancy(model_call_id="other-tenant", org_id=_ORG_B)
        )

        rows = await store.list_context_occupancy(org_id=_ORG_A, run_id=_RUN_A)
        assert [row.model_call_id for row in rows] == ["mine"]
        assert await store.list_context_occupancy(org_id=_ORG_B, run_id=_RUN_B) == ()

    async def test_series_is_ordered_oldest_first(self) -> None:
        store = InMemoryRuntimeApiStore()
        for index in reversed(range(3)):
            await store.append_context_occupancy(
                self.occupancy(
                    model_call_id=f"turn-{index}",
                    created_at=_CREATED_AT + timedelta(seconds=index),
                )
            )

        rows = await store.list_context_occupancy(org_id=_ORG_A, run_id=_RUN_A)
        assert [row.model_call_id for row in rows] == ["turn-0", "turn-1", "turn-2"]


class TestFileContextOccupancy(ContextOccupancyStoreMixin):
    async def test_reopen_preserves_snapshots_and_folds_redeliveries(
        self, tmp_path
    ) -> None:
        root = tmp_path / "context-occupancy-store"
        store = FileRuntimeApiStore(root)
        await store.open()
        first = self.occupancy(model_call_id="call-a", record_id="row-1")
        retry = self.occupancy(model_call_id="call-a", attempt_ordinal=2)
        child = self.occupancy(
            model_call_id="call-child",
            graph_scope=RuntimeContextGraphScope.SUBAGENT,
            estimated_input_tokens=400,
            provider_input_tokens=390,
        )
        assert await store.append_context_occupancy(first)
        assert await store.append_context_occupancy(retry)
        assert await store.append_context_occupancy(child)
        await store.close()

        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        rows = await reopened.list_context_occupancy(org_id=_ORG_A, run_id=_RUN_A)
        assert rows == (first, retry, child)
        # The signed residual survives the JSONL round trip.
        assert rows[0].unattributed_delta == -20
        assert rows[2].unattributed_delta == -10

        # A redelivery after restart still folds to the durable attempt key.
        assert not await reopened.append_context_occupancy(
            self.occupancy(model_call_id="call-a", record_id="row-2")
        )
        scoped = await reopened.list_context_occupancy(
            org_id=_ORG_A,
            run_id=_RUN_A,
            graph_scope=RuntimeContextGraphScope.SUBAGENT,
        )
        assert scoped == (child,)
        await reopened.close()

    async def test_occupancy_never_writes_a_usage_row(self, tmp_path) -> None:
        # Single-tracker invariant (§6.1): the ledger records occupancy only.
        store = FileRuntimeApiStore(tmp_path / "single-tracker")
        await store.open()
        await store.append_context_occupancy(self.occupancy(model_call_id="call-a"))

        assert store.model_call_usage == []
        assert store.run_usage == {}
        await store.close()
