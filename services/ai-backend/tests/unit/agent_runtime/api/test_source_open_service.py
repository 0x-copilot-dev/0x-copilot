"""Owner-routed Sources v2 opener tests (E1 D4/D5).

The service is intentionally exercised below the HTTP layer so the tests can
prove the authorization order and its non-disclosure properties without an
implementation-specific router fixture.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from agent_runtime.api.source_open_service import (
    SourceOpenDispositionV2,
    SourceOpenNotFoundError,
    SourceOpenService,
)
from agent_runtime.artifacts import ArtifactNotFoundError
from agent_runtime.surfaces_v2.ledger_models import ArtifactKind, LedgerEventType

_ORG = "acme"
_USER = "sarah"
_RUN = "run-source-open-1"
_CONVERSATION = "conv-source-open-1"


@dataclass(frozen=True)
class _Event:
    event_type: str
    sequence_no: int
    payload: object


class _Persistence:
    def __init__(self, *, owner: str = _USER, visible: bool = True) -> None:
        self.run = SimpleNamespace(user_id=owner, conversation_id=_CONVERSATION)
        self.visible = visible
        self.get_run_calls = 0
        self.get_conversation_calls = 0

    async def get_run(self, *, org_id: str, run_id: str):  # noqa: ANN201
        self.get_run_calls += 1
        assert org_id == _ORG
        assert run_id == _RUN
        return self.run

    async def get_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ):  # noqa: ANN201
        self.get_conversation_calls += 1
        if not self.visible:
            return None
        return SimpleNamespace(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )


class _Events:
    def __init__(self, events: list[_Event]) -> None:
        self.events = events
        self.calls = 0

    async def list_events_after(
        self,
        *,
        org_id: str,
        run_id: str,
        after_sequence: int,
    ) -> list[_Event]:
        self.calls += 1
        assert (org_id, run_id, after_sequence) == (_ORG, _RUN, 0)
        return self.events


class _ArtifactOwner:
    def __init__(self, *, deny: bool = False) -> None:
        self.deny = deny
        self.calls: list[tuple[str, object]] = []

    async def get_metadata(
        self, *, org_id: str, user_id: str, artifact_id: str
    ) -> object:
        self.calls.append(("metadata", (org_id, user_id, artifact_id)))
        if self.deny:
            raise RuntimeError("/private/path and provider token must not leak")
        return SimpleNamespace(artifact=SimpleNamespace(kind=ArtifactKind.DOCUMENT))

    async def get_revision_metadata(
        self,
        *,
        org_id: str,
        user_id: str,
        artifact_id: str,
        revision: int,
    ) -> object:
        self.calls.append(("revision", (org_id, user_id, artifact_id, revision)))
        if self.deny:
            raise RuntimeError("cookie=session-secret must not leak")
        return SimpleNamespace(revision=revision)


def _artifact_event() -> _Event:
    # The deliberately hostile keys prove the source opener does not return a
    # ledger payload, raw source ref, path, request args, body, cookie, or token.
    return _Event(
        event_type=LedgerEventType.ARTIFACT_CREATED.value,
        sequence_no=4,
        payload={
            "artifact_id": "art_source_open_01",
            "revision": 2,
            "content_ref": "artifact://art_source_open_01/revisions/2",
            "physical_path": "/Users/sarah/private/report.md",
            "cookie": "session=super-secret",
            "args": {"provider_token": "sk-never-return-this"},
            "body": "full body must never return",
        },
    )


def _service(
    events: list[_Event],
    *,
    persistence: _Persistence | None = None,
    owner: _ArtifactOwner | None = None,
) -> tuple[SourceOpenService, _Persistence, _Events, _ArtifactOwner | None]:
    resolved_persistence = persistence or _Persistence()
    resolved_events = _Events(events)
    return (
        SourceOpenService(
            persistence=resolved_persistence,  # type: ignore[arg-type]
            event_store=resolved_events,  # type: ignore[arg-type]
            artifact_service=owner,
        ),
        resolved_persistence,
        resolved_events,
        owner,
    )


def test_owned_artifact_rechecks_owner_and_returns_only_logical_target() -> None:
    owner = _ArtifactOwner()
    service, _persistence, _events, _ = _service([_artifact_event()], owner=owner)

    result = asyncio.run(
        service.open_source(
            org_id=_ORG,
            user_id=_USER,
            run_id=_RUN,
            source_id="source:v2:004:artifact",
        )
    )

    assert result.disposition is SourceOpenDispositionV2.ARTIFACT
    assert result.artifact_id == "art_source_open_01"
    assert result.artifact_revision == 2
    assert result.artifact_kind is ArtifactKind.DOCUMENT
    assert owner.calls == [
        ("metadata", (_ORG, _USER, "art_source_open_01")),
        ("revision", (_ORG, _USER, "art_source_open_01", 2)),
    ]
    rendered = result.model_dump_json()
    for forbidden in (
        "/Users/sarah/private/report.md",
        "session=super-secret",
        "sk-never-return-this",
        "full body must never return",
        "physical_path",
        "cookie",
        "args",
    ):
        assert forbidden not in rendered


def test_unknown_or_foreign_run_is_indistinguishable_and_never_reads_events() -> None:
    persistence = _Persistence(owner="another-user")
    service, _persistence, events, _ = _service(
        [_artifact_event()], persistence=persistence, owner=_ArtifactOwner()
    )

    with pytest.raises(SourceOpenNotFoundError):
        asyncio.run(
            service.open_source(
                org_id=_ORG,
                user_id=_USER,
                run_id=_RUN,
                source_id="source:v2:004:artifact",
            )
        )

    assert events.calls == 0


def test_missing_parent_conversation_is_opaque_and_never_reads_events() -> None:
    """A caller cannot reopen a source from a run whose parent was revoked.

    ``run.user_id`` alone is intentionally insufficient: membership can change
    after a run was persisted.  The event ledger must not be touched until the
    parent conversation has been freshly resolved in the same scoped identity.
    """

    persistence = _Persistence(visible=False)
    service, resolved_persistence, events, _ = _service(
        [_artifact_event()], persistence=persistence, owner=_ArtifactOwner()
    )

    with pytest.raises(SourceOpenNotFoundError):
        asyncio.run(
            service.open_source(
                org_id=_ORG,
                user_id=_USER,
                run_id=_RUN,
                source_id="source:v2:004:artifact",
            )
        )

    assert resolved_persistence.get_run_calls == 1
    assert resolved_persistence.get_conversation_calls == 1
    assert events.calls == 0


def test_non_artifact_and_owner_failure_are_both_closed_unavailable() -> None:
    connector = _Event(
        event_type=LedgerEventType.READ_EXECUTED.value,
        sequence_no=3,
        payload={
            "call_id": "call_1",
            "connector": "linear",
            "op": "get_issue",
            "latency_ms": 12,
            "payload_ref": "call://call_1",
        },
    )
    owner = _ArtifactOwner(deny=True)
    service, _persistence, _events, _ = _service(
        [connector, _artifact_event()], owner=owner
    )

    connector_result = asyncio.run(
        service.open_source(
            org_id=_ORG,
            user_id=_USER,
            run_id=_RUN,
            source_id="source:v2:003:connector",
        )
    )
    artifact_result = asyncio.run(
        service.open_source(
            org_id=_ORG,
            user_id=_USER,
            run_id=_RUN,
            source_id="source:v2:004:artifact",
        )
    )

    assert connector_result.disposition is SourceOpenDispositionV2.UNAVAILABLE
    assert artifact_result.disposition is SourceOpenDispositionV2.UNAVAILABLE
    assert connector_result.artifact_id is None
    assert artifact_result.artifact_id is None
    # Only the artifact attempt can reach its owner; source kinds without an
    # authority do not fall back to unsafe ref dereferencing.
    assert owner.calls == [("metadata", (_ORG, _USER, "art_source_open_01"))]


def test_revoked_or_foreign_artifact_is_an_opaque_not_found() -> None:
    class _RevokedOwner(_ArtifactOwner):
        async def get_metadata(
            self, *, org_id: str, user_id: str, artifact_id: str
        ) -> object:
            self.calls.append(("metadata", (org_id, user_id, artifact_id)))
            raise ArtifactNotFoundError()

    owner = _RevokedOwner()
    service, _persistence, _events, _ = _service([_artifact_event()], owner=owner)

    with pytest.raises(SourceOpenNotFoundError):
        asyncio.run(
            service.open_source(
                org_id=_ORG,
                user_id=_USER,
                run_id=_RUN,
                source_id="source:v2:004:artifact",
            )
        )

    assert owner.calls == [("metadata", (_ORG, _USER, "art_source_open_01"))]
