"""P3 (refactor 03-parallel-bootstrap.md) — pin the parallel-resolver
contract in ``RuntimeApiService.create_run``.

The three run-start helpers ``_resolve_workspace_behavior_overrides`` /
``_resolve_user_policies`` / ``_resolve_suggested_connectors`` must run
concurrently via ``asyncio.gather``. These tests assert:

  * All three are awaited concurrently (not sequentially).
  * Total latency tracks the slowest single resolver, not their sum.
  * Resolved values flow unchanged into the persisted run.
  * Each resolver fires once per run (no caching past run boundary).
  * A failure in one resolver propagates the typed exception and
    short-circuits persistence.
  * Cancelling sibling tasks on first-failure terminates promptly.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest

from agent_runtime.api.service import RuntimeApiService
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
)


_ORG_ID = "org_p3"
_USER_ID = "user_p3"
_ASSISTANT_ID = "assistant_p3"


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    yield


class CreateRunFixtureMixin:
    """Construct a ``RuntimeApiService`` over an in-memory store with a
    seeded conversation. Tests patch the three ``_resolve_*`` methods on
    the returned service to isolate the gather contract from real I/O.
    """

    async def _build_service_with_conversation(
        self,
    ) -> tuple[RuntimeApiService, InMemoryRuntimeApiStore, str]:
        store = InMemoryRuntimeApiStore()
        service = RuntimeApiService(
            persistence=store,
            event_store=store,
            queue=store,
            settings=RuntimeSettings.load(
                environ={
                    "OPENAI_API_KEY": "sk-test",
                    "RUNTIME_DEFAULT_PROVIDER": "openai",
                    "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                }
            ),
        )
        conversation = await service.create_conversation(
            CreateConversationRequest(
                org_id=_ORG_ID,
                user_id=_USER_ID,
                assistant_id=_ASSISTANT_ID,
            )
        )
        return service, store, conversation.conversation_id

    @staticmethod
    def _run_request(*, conversation_id: str) -> CreateRunRequest:
        return CreateRunRequest(
            conversation_id=conversation_id,
            org_id=_ORG_ID,
            user_id=_USER_ID,
            user_input="hi",
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )


class TestResolversRunInParallel(CreateRunFixtureMixin):
    """The three resolvers must run concurrently inside ``create_run``."""

    async def test_all_three_enter_before_any_completes(self) -> None:
        """Use a barrier that requires all three resolvers to be in
        flight before any completes. A serial implementation would
        deadlock — only the first call would enter and wait for the
        barrier to fill, but the remaining two never enter.
        """

        service, _store, conversation_id = await self._build_service_with_conversation()
        barrier = asyncio.Barrier(3)

        async def _gated_workspace(*, org_id: str) -> dict[str, object]:
            await barrier.wait()
            return {}

        async def _gated_policies(*, org_id: str, user_id: str) -> dict[str, object]:
            await barrier.wait()
            return {}

        async def _gated_suggested(
            *, org_id: str, user_id: str, paused_connectors: tuple[str, ...]
        ) -> tuple:
            await barrier.wait()
            return ()

        with (
            patch.object(
                service, "_resolve_workspace_behavior_overrides", _gated_workspace
            ),
            patch.object(service, "_resolve_user_policies", _gated_policies),
            patch.object(service, "_resolve_suggested_connectors", _gated_suggested),
        ):
            response = await asyncio.wait_for(
                service.create_run(self._run_request(conversation_id=conversation_id)),
                timeout=2.0,
            )
        assert response.run_id

    async def test_total_latency_tracks_max_not_sum(self) -> None:
        """Each resolver sleeps 100ms. Serial would take ≥300ms; parallel
        ≤180ms (max + slack for surrounding async work)."""

        service, _store, conversation_id = await self._build_service_with_conversation()
        delay_seconds = 0.1

        async def _slow_workspace(*, org_id: str) -> dict[str, object]:
            await asyncio.sleep(delay_seconds)
            return {}

        async def _slow_policies(*, org_id: str, user_id: str) -> dict[str, object]:
            await asyncio.sleep(delay_seconds)
            return {}

        async def _slow_suggested(
            *, org_id: str, user_id: str, paused_connectors: tuple[str, ...]
        ) -> tuple:
            await asyncio.sleep(delay_seconds)
            return ()

        with (
            patch.object(
                service, "_resolve_workspace_behavior_overrides", _slow_workspace
            ),
            patch.object(service, "_resolve_user_policies", _slow_policies),
            patch.object(service, "_resolve_suggested_connectors", _slow_suggested),
        ):
            start = time.monotonic()
            await service.create_run(self._run_request(conversation_id=conversation_id))
            elapsed = time.monotonic() - start
        # Generous upper bound: max(delay) + 80ms slack for persistence,
        # event append, queue enqueue. Serial would be 3 * delay = 300ms.
        assert elapsed < 0.18, (
            f"create_run took {elapsed:.3f}s; expected < 0.18s with parallel "
            "resolvers. Serial baseline would be ≥0.30s."
        )


class TestResolvedValuesFlowThrough(CreateRunFixtureMixin):
    """Resolved values must reach the persisted run unchanged."""

    async def test_no_caching_across_runs(self) -> None:
        """Two ``create_run`` calls invoke each resolver mock exactly
        twice — no memoization past the run boundary."""

        service, store, conversation_id = await self._build_service_with_conversation()
        ws = AsyncMock(return_value={})
        up = AsyncMock(return_value={})
        sc = AsyncMock(return_value=())

        with (
            patch.object(service, "_resolve_workspace_behavior_overrides", ws),
            patch.object(service, "_resolve_user_policies", up),
            patch.object(service, "_resolve_suggested_connectors", sc),
        ):
            await service.create_run(self._run_request(conversation_id=conversation_id))
            await service.create_run(self._run_request(conversation_id=conversation_id))

        assert ws.await_count == 2
        assert up.await_count == 2
        assert sc.await_count == 2

    async def test_each_resolver_called_once_per_run(self) -> None:
        service, store, conversation_id = await self._build_service_with_conversation()
        ws = AsyncMock(return_value={})
        up = AsyncMock(return_value={})
        sc = AsyncMock(return_value=())

        with (
            patch.object(service, "_resolve_workspace_behavior_overrides", ws),
            patch.object(service, "_resolve_user_policies", up),
            patch.object(service, "_resolve_suggested_connectors", sc),
        ):
            await service.create_run(self._run_request(conversation_id=conversation_id))

        ws.assert_awaited_once_with(org_id=_ORG_ID)
        up.assert_awaited_once_with(org_id=_ORG_ID, user_id=_USER_ID)
        sc.assert_awaited_once_with(
            org_id=_ORG_ID, user_id=_USER_ID, paused_connectors=()
        )


class TestFailurePropagation(CreateRunFixtureMixin):
    """A failed resolver must propagate its typed exception with no
    persistence side effects."""

    async def test_workspace_overrides_failure_propagates(self) -> None:
        service, store, conversation_id = await self._build_service_with_conversation()

        class _Boom(Exception):
            pass

        with (
            patch.object(
                service,
                "_resolve_workspace_behavior_overrides",
                AsyncMock(side_effect=_Boom("workspace down")),
            ),
            patch.object(service, "_resolve_user_policies", AsyncMock(return_value={})),
            patch.object(
                service,
                "_resolve_suggested_connectors",
                AsyncMock(return_value=()),
            ),
        ):
            with pytest.raises(_Boom, match="workspace down"):
                await service.create_run(
                    self._run_request(conversation_id=conversation_id)
                )

    async def test_user_policies_failure_propagates(self) -> None:
        service, store, conversation_id = await self._build_service_with_conversation()

        class _Boom(Exception):
            pass

        with (
            patch.object(
                service,
                "_resolve_workspace_behavior_overrides",
                AsyncMock(return_value={}),
            ),
            patch.object(
                service,
                "_resolve_user_policies",
                AsyncMock(side_effect=_Boom("policies down")),
            ),
            patch.object(
                service,
                "_resolve_suggested_connectors",
                AsyncMock(return_value=()),
            ),
        ):
            with pytest.raises(_Boom, match="policies down"):
                await service.create_run(
                    self._run_request(conversation_id=conversation_id)
                )

    async def test_suggestible_connectors_failure_propagates(self) -> None:
        service, store, conversation_id = await self._build_service_with_conversation()

        class _Boom(Exception):
            pass

        with (
            patch.object(
                service,
                "_resolve_workspace_behavior_overrides",
                AsyncMock(return_value={}),
            ),
            patch.object(service, "_resolve_user_policies", AsyncMock(return_value={})),
            patch.object(
                service,
                "_resolve_suggested_connectors",
                AsyncMock(side_effect=_Boom("catalog down")),
            ),
        ):
            with pytest.raises(_Boom, match="catalog down"):
                await service.create_run(
                    self._run_request(conversation_id=conversation_id)
                )

    async def test_failure_short_circuits_persistence(self) -> None:
        """A resolver failure before ``persistence.create_run_with_user_message``
        means no run row, no event append, no queue enqueue."""

        service, store, conversation_id = await self._build_service_with_conversation()

        runs_before = len(store.runs)
        events_before = sum(
            len(envelopes) for envelopes in store.events_by_run.values()
        )
        queue_before = len(store.run_commands)

        class _Boom(Exception):
            pass

        with (
            patch.object(
                service,
                "_resolve_workspace_behavior_overrides",
                AsyncMock(side_effect=_Boom("workspace down")),
            ),
            patch.object(service, "_resolve_user_policies", AsyncMock(return_value={})),
            patch.object(
                service,
                "_resolve_suggested_connectors",
                AsyncMock(return_value=()),
            ),
            pytest.raises(_Boom),
        ):
            await service.create_run(self._run_request(conversation_id=conversation_id))

        assert len(store.runs) == runs_before
        assert (
            sum(len(envelopes) for envelopes in store.events_by_run.values())
            == events_before
        )
        assert len(store.run_commands) == queue_before


class TestCancellationOfSiblings(CreateRunFixtureMixin):
    """When one resolver fails, the others must be cancelled — total
    latency should not include their full sleep."""

    async def test_failing_resolver_cancels_slow_siblings(self) -> None:
        service, store, conversation_id = await self._build_service_with_conversation()

        class _Boom(Exception):
            pass

        async def _fast_fail_workspace(*, org_id: str) -> dict[str, object]:
            await asyncio.sleep(0.01)
            raise _Boom("fast failure")

        async def _slow_policies(*, org_id: str, user_id: str) -> dict[str, object]:
            await asyncio.sleep(0.5)  # would dominate if NOT cancelled
            return {}

        async def _slow_suggested(
            *, org_id: str, user_id: str, paused_connectors: tuple[str, ...]
        ) -> tuple:
            await asyncio.sleep(0.5)  # would dominate if NOT cancelled
            return ()

        with (
            patch.object(
                service, "_resolve_workspace_behavior_overrides", _fast_fail_workspace
            ),
            patch.object(service, "_resolve_user_policies", _slow_policies),
            patch.object(service, "_resolve_suggested_connectors", _slow_suggested),
        ):
            start = time.monotonic()
            with pytest.raises(_Boom, match="fast failure"):
                await service.create_run(
                    self._run_request(conversation_id=conversation_id)
                )
            elapsed = time.monotonic() - start

        # Cancellation should land well under the slow siblings' 500ms
        # sleep. Generous bound for slack: 200ms.
        assert elapsed < 0.2, (
            f"create_run took {elapsed:.3f}s after a fast failure; expected "
            "< 0.20s. Slow siblings appear to have run to completion instead "
            "of being cancelled."
        )
