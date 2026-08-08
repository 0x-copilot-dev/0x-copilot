"""The worker hands BOTH shaping builders the run's real provider credential.

``ShapingCredentials`` shipped with a seam and no producer: nothing in ``src/``
ever constructed it with real material, so ``build_read_path_shaper`` and
``build_surface_generation_scheduler`` both composed their model kwargs from an
empty default. On a packaged BYOK desktop the process env holds no provider key
by design, so the shaping model was built with no credential, construction
raised, and both rungs degraded to off — on every single run, silently:

    [surfaces.shaping] shaping_model_unavailable model=gpt-5.4-mini:
      rung 5 is off for this run; ...
    [surfaces.specgen] shaping_model_unavailable model=gpt-5.4-mini:
      refinement is off for this run

These tests drive a REAL queued run through ``RuntimeWorker`` (the same harness
``test_worker_byok_hydration`` uses) rather than calling the builders directly:
the defect was never in a builder, it was in the caller not passing the argument,
and a test that calls the builder itself cannot see that.

The assertion is on the VALUE that reached the model factory — the composed
``extra_kwargs`` carrying the user's key — and on the credential object
``model_kwargs_for`` was invoked on. "A builder was called" would have passed
throughout the entire dark period.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.capabilities.surfaces.generator import ShapingCredentials
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
)
from agent_runtime.execution.factory import RuntimeHarness
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import CreateConversationRequest, CreateRunRequest
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.loop import RuntimeWorker

_ORG_ID = "org_shaping_credential"
_USER_ID = "user_shaping_credential"
_SECRET_KEY = "sk-unit-test-shaping-credential-000000000"
#: A SERVICE-env key, deliberately distinct from the BYOK one. It exists to let
#: ``create_run`` past the credential gate; if it ever reached the shaping model
#: the assertion on ``reason=no_run_credential`` would fail, which is the point.
_OPERATOR_KEY = "sk-unit-test-operator-env-000000000000000"
#: What ``ShapingModelResolver`` picks for an ``openai`` run with no explicit
#: ``SURFACE_SPEC_MODEL`` — the exact id the live log named.
_SHAPING_MODEL_ID = "gpt-5.4-mini"


class _PoliciesResolver:
    """Backend policy snapshot: the run's BYOK keys, no other policy."""

    def __init__(self, provider_keys: Mapping[str, str] | None = None) -> None:
        self._provider_keys = dict(
            provider_keys if provider_keys is not None else {"openai": _SECRET_KEY}
        )

    async def resolve(self, *, org_id: str, user_id: str) -> dict[str, object]:
        return {"privacy": {}, "provider_keys": dict(self._provider_keys)}


class _RecordingModelFactory:
    """Stands in for ``build_chat_model_from_id`` and records what it was given."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, object] | None]] = []

    def __call__(
        self, model_id: str, *, extra_kwargs: Mapping[str, object] | None = None
    ) -> object:
        self.calls.append((model_id, extra_kwargs))
        return object()


class ShapingCredentialRunMixin:
    """Drives one queued run to completion with shaping wired to recorders."""

    @staticmethod
    def _settings(*, environ: Mapping[str, str] | None = None) -> RuntimeSettings:
        """Settings that cannot inherit the developer's ``services/ai-backend/.env``.

        ``load`` merges that file BEFORE ``environ``, and it is gitignored — so a
        checkout that has one resolves ``provider_settings.is_configured`` True and
        a checkout that does not (every CI runner) resolves it False. Passing
        ``environ`` alone does not close that: it replaces ``os.environ``, not the
        file layer. Pinning ``env_file`` at an empty path is what makes the two
        environments the same test.
        """

        return RuntimeSettings.load(
            env_file=os.devnull,
            environ={
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                **(environ or {}),
            },
        )

    @staticmethod
    def _shaping_env(monkeypatch: pytest.MonkeyPatch) -> None:
        """The packaged-desktop posture: SURFACES_V2 on, no operator override.

        Deliberately NOT ``SURFACE_SPEC_MODEL``: the measured failure was on the
        B3 shaping-on default, where the model id comes from the run's provider.
        No provider key in the process env either — BYOK is the only source, so
        a builder that drops the credential has nothing to fall back on.
        """

        monkeypatch.delenv("SURFACE_SPEC_MODEL", raising=False)
        monkeypatch.delenv("SURFACES_V2", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    @staticmethod
    def _record_shaping(
        monkeypatch: pytest.MonkeyPatch,
    ) -> tuple[_RecordingModelFactory, list[ShapingCredentials]]:
        """Record the credential at BOTH ends of the composition.

        ``seen`` is every ``ShapingCredentials`` a builder actually invoked
        ``model_kwargs_for`` on; ``factory`` is the composed kwargs that reached
        model construction. Asserting only the first would pass over a
        composition that dropped the key; only the second would not name where.
        """

        factory = _RecordingModelFactory()
        monkeypatch.setattr(
            "agent_runtime.execution.deep_agent_builder.build_chat_model_from_id",
            factory,
        )
        seen: list[ShapingCredentials] = []
        original = ShapingCredentials.model_kwargs_for

        def recording_model_kwargs_for(
            self: ShapingCredentials, model_id: str
        ) -> dict[str, object]:
            seen.append(self)
            return original(self, model_id)

        monkeypatch.setattr(
            ShapingCredentials, "model_kwargs_for", recording_model_kwargs_for
        )
        return (factory, seen)

    async def _run_once(
        self,
        store: InMemoryRuntimeApiStore,
        settings: RuntimeSettings,
        *,
        provider_keys: Mapping[str, str] | None = None,
    ) -> str:
        """Create a queued run with a BYOK key and drive it to completion."""

        resolver = _PoliciesResolver(provider_keys)
        event_producer = RuntimeEventProducer(
            persistence=store, event_store=store, on_event_appended=None
        )
        run_coordinator = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=event_producer,
            settings=settings,
            model_resolver=ModelConfigResolver(settings=settings),
            user_policies_resolver=resolver,
        )
        conversation = await ConversationCoordinator(
            persistence=store,
            settings=settings,
            run_coordinator=run_coordinator,
        ).create_conversation(
            CreateConversationRequest(
                org_id=_ORG_ID, user_id=_USER_ID, assistant_id="assistant_shaping"
            )
        )
        response = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=_ORG_ID,
                user_id=_USER_ID,
                user_input="list the rows",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )

        def agent_factory(
            *, context: AgentRuntimeContext, dependencies: RuntimeDependencies
        ) -> RuntimeHarness:
            return RuntimeHarness(
                agent=object(),
                context=context,
                dependencies=dependencies,
                tools=(),
                mcp_servers=(),
                subagents=(),
                memory_backend=None,
                skill_directories=(),
            )

        async def invoker(
            _harness: RuntimeHarness, _messages: Sequence[object]
        ) -> object:
            return {"messages": [{"role": "assistant", "content": "done"}]}

        worker = RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            run_handler=RuntimeRunHandler(
                persistence=store,
                event_store=store,
                settings=settings,
                agent_factory=agent_factory,
                runtime_invoker=invoker,
                user_policies_resolver=resolver,
            ),
        )
        processed = await worker.run_until_idle()
        assert processed == 1
        assert store.runs[response.run_id].status == "completed"
        return response.run_id


class TestBothShapingBuildersGetTheRunsCredential(ShapingCredentialRunMixin):
    async def test_the_byok_key_reaches_both_shaping_models(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._shaping_env(monkeypatch)
        factory, _ = self._record_shaping(monkeypatch)
        store = InMemoryRuntimeApiStore()

        await self._run_once(store, self._settings())

        # Refinement (specgen) and rung 5 (read-path shaping) are built once
        # each per run, and BOTH must carry the user's key. Only one did before
        # this fix, and neither carried it before the one before that.
        assert factory.calls == [
            (_SHAPING_MODEL_ID, {"api_key": _SECRET_KEY}),
            (_SHAPING_MODEL_ID, {"api_key": _SECRET_KEY}),
        ]

    async def test_the_credential_object_reaches_model_kwargs_for(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The seam the audit found unproduced. Asserting on the composed kwargs
        # alone would still pass if a future caller rebuilt them by hand.
        self._shaping_env(monkeypatch)
        _, seen = self._record_shaping(monkeypatch)
        store = InMemoryRuntimeApiStore()

        await self._run_once(store, self._settings())

        assert len(seen) == 2
        assert all(
            credentials.provider_keys == {"openai": _SECRET_KEY} for credentials in seen
        )

    async def test_both_builders_share_one_credential_instance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # One producer, two consumers. Two lookups is how one of them goes
        # stale against the run's real policy without anyone noticing.
        self._shaping_env(monkeypatch)
        _, seen = self._record_shaping(monkeypatch)
        store = InMemoryRuntimeApiStore()

        await self._run_once(store, self._settings())

        assert seen[0] is seen[1]

    async def test_the_key_never_lands_on_an_event_record_or_log(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The kwargs are plaintext key material. Threading them further must not
        # widen where they can be observed.
        self._shaping_env(monkeypatch)
        self._record_shaping(monkeypatch)
        store = InMemoryRuntimeApiStore()

        with caplog.at_level(logging.DEBUG):
            run_id = await self._run_once(store, self._settings())

        for event in store.events_by_run[run_id]:
            assert _SECRET_KEY not in event.model_dump_json()
        assert _SECRET_KEY not in store.runs[run_id].runtime_context.model_dump_json()
        assert _SECRET_KEY not in json.dumps(store.audit_log, default=str)
        assert _SECRET_KEY not in caplog.text

    async def test_a_run_without_a_byok_key_still_completes_with_shaping_off(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The honest degrade must survive the wiring: no run credential ⇒ no
        # shaping model ⇒ both rungs off, run still completes, floor still
        # renders. The real factory is left in place so construction genuinely
        # fails.
        #
        # The posture is an OPERATOR deployment, and it has to be: shaping reads
        # the run's BYOK credential, but ``create_run`` refuses outright when
        # neither BYOK nor the service env can satisfy the provider. So "no key
        # at all" cannot reach the degrade — the run never starts, which is the
        # fail-loud BYOK lane working. The gap this covers is the deployment
        # that HAS a service key (run allowed) and no per-user key (shaping
        # credential empty).
        #
        # This test used to pass ``provider_keys={}`` with no service key and
        # relied, unknowingly, on a developer's gitignored ``.env`` to make the
        # run creatable at all. It was green on every laptop and red on the
        # first CI runner that ran it.
        self._shaping_env(monkeypatch)
        store = InMemoryRuntimeApiStore()
        operator_env = self._settings(environ={"OPENAI_API_KEY": _OPERATOR_KEY})

        with caplog.at_level(logging.WARNING):
            await self._run_once(store, operator_env, provider_keys={})

        unavailable = [
            record.getMessage()
            for record in caplog.records
            if "shaping_model_unavailable" in record.getMessage()
        ]
        assert len(unavailable) == 2
        # F3: the line names the CLASS of failure, not just the model id. The
        # old line read as a bad model id and cost a wrong diagnosis.
        assert all("reason=no_run_credential" in line for line in unavailable)
        assert all("api_key" not in line for line in unavailable)
