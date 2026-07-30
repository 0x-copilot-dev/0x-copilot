"""The Context Occupancy Ledger on a REAL run of a DEFAULT deployment.

Every other occupancy test injects the thing being tested: a recorder, a sink, or
an ``ModelInvocationRuntimeBinding`` carrying ``context_occupancy_store``. That is
why the ledger shipped capturing nothing. ``FeatureModeSet.f10`` defaults to
``OFF``, so ``ModelInvocationWorkerComposer.compose`` returns ``None``, so no F10
binding is installed, so ``awrap_model_call`` returns at ``if binding is None``
before any measurement runs — on every model call of every default deployment.

This file closes that gap the only way it can be closed: drive a real queued run
through the real worker, the real Deep Agents graph and the real streaming
executor with **F10 left at its production default**, inject nothing, and ask the
store whether rows exist. It is deliberately built on the same harness as
``test_fake_model_run_stream`` — only the concrete chat model is the deterministic
fake — so what it proves is a property of the production wiring rather than of a
test double.
"""

from __future__ import annotations

from agent_runtime.control_plane.context import RunControlContext
from agent_runtime.control_plane.feature_modes import FeatureMode, FeatureModeSet
from agent_runtime.observability.context_origin import ContextSegmentClass
from agent_runtime.persistence.records import RuntimeContextGraphScope
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.loop import RuntimeWorker

from tests.unit.runtime_worker.test_fake_model_run_stream import FakeModelRunMixin


class RealRunOccupancyMixin(FakeModelRunMixin):
    """Execute one real run and hand back the store it wrote occupancy into."""

    ORG_ID = "org_123"

    @classmethod
    async def _execute_real_run(
        cls, monkeypatch
    ) -> tuple[InMemoryRuntimeApiStore, str]:
        monkeypatch.setenv("RUNTIME_FAKE_MODEL", "1")
        store = InMemoryRuntimeApiStore()
        settings = cls._settings()
        run_id = await cls._enqueue_run(store, settings)

        worker = RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            mcp_discovery_cache=(
                DefaultRuntimeDependenciesFactory.build_default_discovery_cache()
            ),
        )
        processed = await worker.run_until_idle()

        assert processed == 1
        names = [event.event_type for event in store.events_by_run[run_id]]
        # Guard the premise: a run that failed would make an empty ledger
        # unsurprising and this test meaningless.
        assert "run_failed" not in names, names
        assert "run_completed" in names, names
        return store, run_id


class TestOccupancyIsCapturedOnADefaultDeployment(RealRunOccupancyMixin):
    async def test_a_real_run_with_f10_off_writes_occupancy_rows(
        self, monkeypatch
    ) -> None:
        store, run_id = await self._execute_real_run(monkeypatch)

        rows = await store.list_context_occupancy(org_id=self.ORG_ID, run_id=run_id)

        assert rows, (
            "a real run wrote no context occupancy rows: the ledger captures "
            "nothing on a default deployment"
        )

    async def test_no_f10_binding_was_installed_on_the_run_that_wrote_them(
        self, monkeypatch
    ) -> None:
        """The premise, pinned: those rows were written with F10 absent.

        Without this, the test above would keep passing for the wrong reason the
        day a default flips F10 on — the rows would appear via the F10 binding
        and the regression this guards would silently reopen. Asserted by
        observing the install seam rather than by re-deriving a mode, because
        "no binding was installed" is the exact condition
        ``awrap_model_call`` branches on.
        """

        installs: list[object] = []
        original = RunControlContext.install_model_invocation_runtime
        monkeypatch.setattr(
            RunControlContext,
            "install_model_invocation_runtime",
            staticmethod(
                lambda binding: (installs.append(binding), original(binding))[1]
            ),
        )

        store, run_id = await self._execute_real_run(monkeypatch)

        assert installs == [], "F10 was installed; this run is not the OFF path"
        assert FeatureModeSet().f10 is FeatureMode.OFF
        assert await store.list_context_occupancy(org_id=self.ORG_ID, run_id=run_id)

    async def test_the_rows_describe_the_window_that_was_actually_sent(
        self, monkeypatch
    ) -> None:
        store, run_id = await self._execute_real_run(monkeypatch)

        rows = await store.list_context_occupancy(org_id=self.ORG_ID, run_id=run_id)
        assert rows

        first = rows[0]
        assert first.run_id == run_id
        assert first.org_id == self.ORG_ID
        assert first.conversation_id
        assert first.graph_scope is RuntimeContextGraphScope.ROOT
        assert first.attempt_ordinal == 1
        # The three facts the F10 route supplies when F10 is on, and that with
        # F10 off can only have come from the run's own model profile.
        assert first.provider == "openai"
        assert first.model_family == "gpt-5.4-mini"
        assert first.context_window_tokens == 128_000
        # The measurement is of a real materialized request, not an empty
        # fail-open shell: a real graph sends a system block and a tool surface.
        assert first.estimated_input_tokens > 1_000
        classes = {
            segment.get("segment_class")
            for segment in first.segments_json.get(
                first.Keys.SEGMENTS,  # type: ignore[union-attr]
                (),
            )
        }
        assert ContextSegmentClass.SYSTEM.value in classes, classes
        assert ContextSegmentClass.TOOLS.value in classes, classes

    async def test_the_fake_provider_reports_no_total_so_none_is_recorded(
        self, monkeypatch
    ) -> None:
        """``None`` is not zero (§6.1/§4.4), and this is the path that proves it.

        The deterministic fake returns no usage metadata, so reconciliation has
        nothing authoritative to copy. Recording ``0`` here would claim the
        provider billed nothing and make ``unattributed_delta`` a large negative
        number on every call of every fake-model run.
        """

        store, run_id = await self._execute_real_run(monkeypatch)

        rows = await store.list_context_occupancy(org_id=self.ORG_ID, run_id=run_id)
        assert rows
        assert rows[0].provider_input_tokens is None
        assert rows[0].unattributed_delta == 0
