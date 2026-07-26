"""B1 worker wiring keeps one authoritative ``/drafts`` backend per flag state."""

from __future__ import annotations

from types import SimpleNamespace

from agent_runtime.capabilities.backends import ArtifactDraftBackend, DraftBackend
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
from runtime_worker.handlers.approval import RuntimeApprovalHandler
from runtime_worker.handlers.run import RuntimeRunHandler


async def _emit(_event: object) -> None:
    return None


def _handler(
    *, settings: RuntimeSettings, artifact_service: object | None
) -> RuntimeRunHandler:
    store = InMemoryRuntimeApiStore()
    return RuntimeRunHandler(
        persistence=store,
        event_store=store,
        settings=settings,
        draft_store=InMemoryDraftStore(),
        artifact_service=artifact_service,
    )


def test_artifact_drafts_flag_selects_the_canonical_adapter() -> None:
    settings = RuntimeSettings.load(
        environ={
            "ARTIFACT_EFFECTS_V2": "true",
            "ARTIFACT_DRAFTS_V2": "true",
        }
    )
    handler = _handler(settings=settings, artifact_service=object())

    backend = handler._drafts_backend(
        org_id="org_1",
        user_id="user_1",
        conversation_id="conv_1",
        run_id="run_1",
        emit_event=_emit,
    )

    assert isinstance(backend, ArtifactDraftBackend)


def test_artifact_drafts_flag_off_preserves_legacy_backend() -> None:
    settings = RuntimeSettings.load(environ={"ARTIFACT_EFFECTS_V2": "true"})
    handler = _handler(settings=settings, artifact_service=object())

    backend = handler._drafts_backend(
        org_id="org_1",
        user_id="user_1",
        conversation_id="conv_1",
        run_id="run_1",
        emit_event=_emit,
    )

    assert isinstance(backend, DraftBackend)


def test_approval_resume_uses_the_same_flag_selected_authority() -> None:
    store = InMemoryRuntimeApiStore()
    handler = RuntimeApprovalHandler(
        persistence=store,
        event_store=store,
        settings=RuntimeSettings.load(
            environ={
                "ARTIFACT_EFFECTS_V2": "true",
                "ARTIFACT_DRAFTS_V2": "true",
            }
        ),
        draft_store=InMemoryDraftStore(),
        artifact_service=object(),
    )
    run = SimpleNamespace(
        org_id="org_1",
        user_id="user_1",
        conversation_id="conv_1",
        run_id="run_1",
        runtime_context=SimpleNamespace(user_id="user_1"),
    )

    backend = handler._drafts_backend(run)

    assert isinstance(backend, ArtifactDraftBackend)
