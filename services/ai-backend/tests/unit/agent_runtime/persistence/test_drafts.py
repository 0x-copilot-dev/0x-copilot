"""Unit tests for the PR 1.3 draft records, port, in-memory adapter, and DraftService."""

from __future__ import annotations

import pytest

from agent_runtime.api.draft_service import DraftService
from agent_runtime.persistence.ports import OptimisticConflict
from agent_runtime.persistence.records import (
    DraftPath,
    DraftRecord,
    DraftStatus,
)
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    DraftDiscardRequest,
    DraftPatchRequest,
    DraftSendRequest,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _draft_id() -> str:
    return "abcdef0123456789abcdef0123456789"


def _record(
    *,
    org_id: str = "org_acme",
    conversation_id: str = "conv_1",
    user_id: str = "user_sarah",
    run_id: str | None = "run_1",
    version: int = 1,
    status: DraftStatus = DraftStatus.DRAFT,
    content_text: str = "# Aurora 4.0\n\nLaunch announcement body.",
    target_connector: str | None = None,
    target_metadata: dict | None = None,
    citation_ids: tuple[str, ...] = (),
) -> DraftRecord:
    return DraftRecord(
        draft_id=_draft_id(),
        version=version,
        org_id=org_id,
        conversation_id=conversation_id,
        run_id=run_id,
        user_id=user_id,
        title="Aurora 4.0",
        content_text=content_text,
        target_connector=target_connector,
        target_metadata=target_metadata or {},
        citation_ids=citation_ids,
        status=status,
    )


class TestDraftRecord:
    def test_normalizes_dashed_uuid_to_hex(self) -> None:
        dashed = "abcdef01-2345-6789-abcd-ef0123456789"
        record = DraftRecord(
            draft_id=dashed,
            version=1,
            org_id="o",
            conversation_id="c",
            user_id="u",
        )
        assert record.draft_id == "abcdef0123456789abcdef0123456789"

    def test_rejects_non_uuid_draft_id(self) -> None:
        with pytest.raises(ValueError):
            DraftRecord(
                draft_id="not-a-uuid",
                version=1,
                org_id="o",
                conversation_id="c",
                user_id="u",
            )

    def test_path_round_trip(self) -> None:
        path = DraftPath.for_draft_id(_draft_id())
        assert path == "/drafts/abcdef0123456789abcdef0123456789.md"
        assert DraftPath.parse_draft_id(path) == _draft_id()

    def test_path_rejects_invalid_shape(self) -> None:
        assert DraftPath.parse_draft_id("/drafts/foo.md") is None
        assert DraftPath.parse_draft_id("/drafts/abc.md") is None
        assert DraftPath.parse_draft_id("/memories/abcdef.md") is None


class TestInMemoryDraftStore:
    async def test_insert_and_latest(self) -> None:
        store = InMemoryDraftStore()
        record = _record(version=1)
        store.insert_version(record)
        assert store.latest(org_id=record.org_id, draft_id=record.draft_id) == record

    async def test_unique_per_org_draft_version(self) -> None:
        store = InMemoryDraftStore()
        store.insert_version(_record(version=1))
        with pytest.raises(OptimisticConflict) as exc:
            store.insert_version(_record(version=1))
        assert exc.value.expected_version == 1
        assert exc.value.actual_version == 1

    async def test_versions_monotone(self) -> None:
        store = InMemoryDraftStore()
        store.insert_version(_record(version=1))
        store.insert_version(_record(version=2, content_text="edited"))
        latest = store.latest(org_id="org_acme", draft_id=_draft_id())
        assert latest is not None
        assert latest.version == 2
        assert latest.content_text == "edited"

    async def test_latest_for_conversation_scoped_to_org(self) -> None:
        store = InMemoryDraftStore()
        store.insert_version(_record(org_id="org_a", conversation_id="conv_a"))
        store.insert_version(_record(org_id="org_b", conversation_id="conv_a"))
        a_drafts = store.latest_for_conversation(
            org_id="org_a", conversation_id="conv_a"
        )
        assert len(a_drafts) == 1
        assert a_drafts[0].org_id == "org_a"

    async def test_expect_status_raises_on_version_drift(self) -> None:
        store = InMemoryDraftStore()
        store.insert_version(_record(version=1))
        store.insert_version(_record(version=2))
        with pytest.raises(OptimisticConflict):
            store.expect_status(
                org_id="org_acme", draft_id=_draft_id(), expected_version=1
            )

    async def test_expect_status_unknown_raises_keyerror(self) -> None:
        store = InMemoryDraftStore()
        with pytest.raises(KeyError):
            store.expect_status(
                org_id="org_acme", draft_id=_draft_id(), expected_version=1
            )


class TestDraftService:
    async def test_list_returns_latest_per_draft_id(self) -> None:
        store = InMemoryDraftStore()
        store.insert_version(_record(version=1))
        store.insert_version(_record(version=2, content_text="v2"))
        service = DraftService(store=store)

        result = await service.list_for_conversation(
            org_id="org_acme", conversation_id="conv_1"
        )

        assert len(result.drafts) == 1
        assert result.drafts[0].version == 2

    async def test_get_returns_latest_when_version_omitted(self) -> None:
        store = InMemoryDraftStore()
        store.insert_version(_record(version=1))
        store.insert_version(_record(version=2, content_text="v2"))
        service = DraftService(store=store)

        draft = await service.get(org_id="org_acme", draft_id=_draft_id())
        assert draft.version == 2

    async def test_get_specific_version(self) -> None:
        store = InMemoryDraftStore()
        store.insert_version(_record(version=1, content_text="v1"))
        store.insert_version(_record(version=2, content_text="v2"))
        service = DraftService(store=store)

        draft = await service.get(org_id="org_acme", draft_id=_draft_id(), version=1)
        assert draft.version == 1

    async def test_get_unknown_raises_404(self) -> None:
        service = DraftService(store=InMemoryDraftStore())
        with pytest.raises(RuntimeApiError) as exc:
            await service.get(org_id="org_acme", draft_id=_draft_id())
        assert exc.value.http_status == 404

    async def test_patch_inserts_new_version(self) -> None:
        store = InMemoryDraftStore()
        store.insert_version(_record(version=1, content_text="v1"))
        service = DraftService(store=store)

        result = await service.patch(
            org_id="org_acme",
            user_id="user_sarah",
            draft_id=_draft_id(),
            request=DraftPatchRequest(expected_version=1, content_text="patched body"),
        )
        assert result.version == 2
        assert result.content_text == "patched body"
        assert result.run_id is None

    async def test_patch_version_conflict_raises_409(self) -> None:
        store = InMemoryDraftStore()
        store.insert_version(_record(version=1))
        store.insert_version(_record(version=2))
        service = DraftService(store=store)

        with pytest.raises(RuntimeApiError) as exc:
            await service.patch(
                org_id="org_acme",
                user_id="user_sarah",
                draft_id=_draft_id(),
                request=DraftPatchRequest(expected_version=1, content_text="x"),
            )
        assert exc.value.http_status == 409

    async def test_patch_refuses_sent_drafts(self) -> None:
        store = InMemoryDraftStore()
        store.insert_version(_record(version=1, status=DraftStatus.SENT))
        service = DraftService(store=store)

        with pytest.raises(RuntimeApiError) as exc:
            await service.patch(
                org_id="org_acme",
                user_id="user_sarah",
                draft_id=_draft_id(),
                request=DraftPatchRequest(expected_version=1, content_text="x"),
            )
        assert exc.value.http_status == 409

    async def test_send_transitions_to_pending_approval(self) -> None:
        store = InMemoryDraftStore()
        store.insert_version(_record(version=1))
        service = DraftService(store=store)

        result = await service.send(
            org_id="org_acme",
            user_id="user_sarah",
            draft_id=_draft_id(),
            request=DraftSendRequest(
                expected_version=1,
                target_connector="slack",
                target_metadata={"channel": "#announcements"},
            ),
        )
        assert result.draft.status == DraftStatus.SEND_PENDING_APPROVAL
        assert result.draft.target_connector == "slack"
        assert result.draft.target_metadata == {"channel": "#announcements"}
        assert result.approval_id is not None
        assert result.approval_id.startswith("draft_send:")

    async def test_send_audits_proposed(self) -> None:
        audit_calls: list[tuple[str, dict]] = []

        class StubPersistence:
            def write_audit_log(self, *, event_type: str, record: dict) -> None:
                audit_calls.append((event_type, record))

        store = InMemoryDraftStore()
        store.insert_version(_record(version=1))
        service = DraftService(store=store, persistence=StubPersistence())

        await service.send(
            org_id="org_acme",
            user_id="user_sarah",
            draft_id=_draft_id(),
            request=DraftSendRequest(
                expected_version=1,
                target_connector="slack",
                target_metadata={},
            ),
        )

        assert any(call[0] == "draft.send.proposed" for call in audit_calls)

    async def test_discard_transitions_to_discarded(self) -> None:
        store = InMemoryDraftStore()
        store.insert_version(_record(version=1))
        service = DraftService(store=store)

        result = await service.discard(
            org_id="org_acme",
            user_id="user_sarah",
            draft_id=_draft_id(),
            request=DraftDiscardRequest(expected_version=1),
        )
        assert result.status == DraftStatus.DISCARDED

    async def test_cross_org_returns_404(self) -> None:
        store = InMemoryDraftStore()
        store.insert_version(_record(org_id="org_a"))
        service = DraftService(store=store)

        with pytest.raises(RuntimeApiError) as exc:
            await service.get(org_id="org_b", draft_id=_draft_id())
        assert exc.value.http_status == 404
