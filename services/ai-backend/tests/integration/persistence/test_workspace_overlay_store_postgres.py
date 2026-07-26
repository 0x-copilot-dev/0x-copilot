"""Live PostgreSQL conformance tests for C1's workspace-overlay adapter.

The fast unit tests exercise row decoding and the transaction shape with a
fake connection. This module verifies the database constraints that cannot be
honestly emulated: a durable parent-run scope, restart persistence, and
cross-worker compare-and-swap.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from agent_runtime.capabilities.workspace.contracts import (
    BaseExistence,
    BasePrecondition,
    OverlayEntry,
    OverlayMutation,
    OverlayMutationKind,
    WorkspaceEntryKind,
    WorkspaceOperation,
)
from agent_runtime.capabilities.workspace.errors import WorkspaceOverlayConflictError
from agent_runtime.execution.contracts import AgentRuntimeContext
from runtime_adapters.postgres import (
    PostgresRuntimeApiStore,
    PostgresWorkspaceOverlayStore,
)
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeRequestContext,
)

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("WORKSPACE_OVERLAY_LIVE_TEST_DATABASE_URL"),
        reason=(
            "Set WORKSPACE_OVERLAY_LIVE_TEST_DATABASE_URL to a disposable Postgres "
            "database to exercise the durable workspace-overlay adapter."
        ),
    ),
]


@pytest.fixture
async def runtime_store() -> AsyncIterator[PostgresRuntimeApiStore]:
    store = PostgresRuntimeApiStore(
        os.environ["WORKSPACE_OVERLAY_LIVE_TEST_DATABASE_URL"],
        pool_min_size=2,
        pool_max_size=16,
        pool_acquire_timeout_seconds=10.0,
    )
    await store.open()
    try:
        await store.migrate()
        yield store
    finally:
        await store.close()


async def _seed_run(
    store: PostgresRuntimeApiStore, *, suffix: str | None = None
) -> tuple[str, str]:
    suffix = suffix or uuid4().hex
    org_id = f"org_workspace_overlay_{suffix}"
    user_id = f"user_workspace_overlay_{suffix}"
    conversation = await store.create_conversation(
        CreateConversationRequest(
            org_id=org_id,
            user_id=user_id,
            assistant_id=f"assistant_{suffix}",
        )
    )
    context = AgentRuntimeContext(
        user_id=user_id,
        org_id=org_id,
        roles=("Admin",),
        model_profile={
            "provider": "openai",
            "model_name": "gpt-5.4-mini",
            "max_input_tokens": 128000,
            "timeout_seconds": 30,
            "temperature": 0,
            "supports_streaming": True,
        },
        run_id=f"run_workspace_overlay_{suffix}",
        trace_id=f"trace_workspace_overlay_{suffix}",
    )
    client_request = CreateRunRequest(
        conversation_id=conversation.conversation_id,
        org_id=org_id,
        user_id=user_id,
        user_input="stage a workspace change",
        model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        request_context=RuntimeRequestContext(
            roles=("Admin",), permission_scopes=("Search:Read",)
        ),
    )
    run, _message, _created = await store.create_run_with_user_message(
        request=client_request.model_copy(update={"runtime_context": context}),
        conversation=conversation,
    )
    return org_id, run.run_id


def _mutation(path: str = "/workspace/project/report.csv") -> OverlayMutation:
    return OverlayMutation(
        kind=OverlayMutationKind.UPSERT,
        virtual_path=path,
        entry=OverlayEntry(
            virtual_path=path,
            entry_kind=WorkspaceEntryKind.FILE,
            operation=WorkspaceOperation.CREATE,
            content_ref=f"artifact-blob://sha256/{'a' * 64}",
            content_digest="a" * 64,
            byte_size=7,
            baseline=BasePrecondition(existence=BaseExistence.MUST_NOT_EXIST),
            stage_id="stg_00000000-0000-4000-8000-000000000123",
            stage_revision=2,
            author="agent",
        ),
    )


class TestPostgresWorkspaceOverlayStore:
    async def test_restart_round_trip_derives_parent_tenant_scope(
        self, runtime_store: PostgresRuntimeApiStore
    ) -> None:
        org_id, run_id = await _seed_run(runtime_store)
        first = PostgresWorkspaceOverlayStore(runtime_store)
        manifest = await first.append_revision(
            run_id=run_id, expected_version=0, mutations=(_mutation(),)
        )

        restored = await PostgresWorkspaceOverlayStore(runtime_store).get_manifest(
            run_id=run_id
        )

        assert restored == manifest
        async with runtime_store._role_connection("worker") as conn:
            cursor = await conn.execute(
                "SELECT org_id FROM runtime_workspace_overlay_manifests WHERE run_id = %s",
                (run_id,),
            )
            row = await cursor.fetchone()
        assert row == {"org_id": org_id}

    async def test_concurrent_compare_and_swap_has_exactly_one_winner(
        self, runtime_store: PostgresRuntimeApiStore
    ) -> None:
        _org_id, run_id = await _seed_run(runtime_store)
        overlays = PostgresWorkspaceOverlayStore(runtime_store)

        results = await asyncio.gather(
            *(
                overlays.append_revision(
                    run_id=run_id,
                    expected_version=0,
                    mutations=(_mutation(f"/workspace/project/report-{index}.csv"),),
                )
                for index in range(12)
            ),
            return_exceptions=True,
        )

        assert sum(not isinstance(result, Exception) for result in results) == 1
        assert (
            sum(isinstance(result, WorkspaceOverlayConflictError) for result in results)
            == 11
        )
        assert (await overlays.get_manifest(run_id=run_id)).version == 1

    async def test_runs_are_isolated_and_unknown_runs_cannot_materialise_state(
        self, runtime_store: PostgresRuntimeApiStore
    ) -> None:
        _first_org, first_run = await _seed_run(
            runtime_store, suffix=f"first_{uuid4().hex}"
        )
        _second_org, second_run = await _seed_run(
            runtime_store, suffix=f"second_{uuid4().hex}"
        )
        overlays = PostgresWorkspaceOverlayStore(runtime_store)
        first = await overlays.append_revision(
            run_id=first_run, expected_version=0, mutations=(_mutation(),)
        )

        second = await overlays.get_manifest(run_id=second_run)
        assert second.run_id == second_run
        assert second.version == 0
        assert second.entries == ()
        assert (await overlays.get_manifest(run_id=first_run)) == first

        with pytest.raises(WorkspaceOverlayConflictError):
            await overlays.append_revision(
                run_id="run_unknown_workspace_overlay",
                expected_version=0,
                mutations=(_mutation(),),
            )
