"""Unit tests for :class:`DraftBackend` (PR 1.3).

Verifies the deepagents BackendProtocol surface: path validation,
``awrite`` / ``aedit`` / ``aread`` / ``als`` / ``agrep``, version monotony,
event emission contract, and the cross-org / non-existent / ambiguous-edit
edge cases the spec requires.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_runtime.capabilities.backends.draft_backend import DraftBackend
from agent_runtime.persistence.records import DraftPath, DraftRecord
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _draft_id() -> str:
    return "deadbeefcafe1234deadbeefcafe1234"


def _path() -> str:
    # Composite-stripped: only the inner /<uuid>.md
    return f"/{_draft_id()}.md"


def _full_path() -> str:
    return DraftPath.for_draft_id(_draft_id())


class _CaptureEmit:
    """Test double for the ``emit_event`` callback."""

    def __init__(self) -> None:
        self.records: list[DraftRecord] = []

    async def __call__(self, record: DraftRecord) -> None:
        self.records.append(record)


def _backend(
    *, store: InMemoryDraftStore | None = None, emit: Any = None
) -> DraftBackend:
    return DraftBackend(
        store=store or InMemoryDraftStore(),
        org_id="org_acme",
        conversation_id="conv_1",
        run_id="run_1",
        user_id="user_sarah",
        emit_event=emit,
    )


class TestDraftBackendPathValidation:
    async def test_invalid_path_returns_invalid_path_error(self) -> None:
        backend = _backend()
        result = await backend.awrite("/drafts/foo.md", "x")
        assert result.error == "invalid_path"

    async def test_invalid_short_uuid_path_returns_invalid_path_error(self) -> None:
        backend = _backend()
        result = await backend.awrite("/abc.md", "x")
        assert result.error == "invalid_path"

    async def test_full_drafts_path_also_accepted(self) -> None:
        backend = _backend()
        result = await backend.awrite(_full_path(), "# Hello\n")
        assert result.error is None
        assert result.path == _full_path()

    async def test_inner_path_accepted_after_composite_strip(self) -> None:
        backend = _backend()
        result = await backend.awrite(_path(), "# Hello\n")
        assert result.error is None


class TestDraftBackendWrite:
    async def test_first_write_inserts_version_one(self) -> None:
        store = InMemoryDraftStore()
        backend = _backend(store=store)

        await backend.awrite(_path(), "# Aurora\n\nLaunch announcement.")

        record = store.latest(org_id="org_acme", draft_id=_draft_id())
        assert record is not None
        assert record.version == 1
        assert record.title == "Aurora"
        assert record.run_id == "run_1"
        assert record.user_id == "user_sarah"

    async def test_repeat_writes_increment_version(self) -> None:
        store = InMemoryDraftStore()
        backend = _backend(store=store)

        await backend.awrite(_path(), "# Aurora\nv1")
        await backend.awrite(_path(), "# Aurora\nv2")

        record = store.latest(org_id="org_acme", draft_id=_draft_id())
        assert record is not None
        assert record.version == 2

    async def test_emits_event_on_each_write(self) -> None:
        emit = _CaptureEmit()
        backend = _backend(emit=emit)

        await backend.awrite(_path(), "# Aurora\nv1")
        await backend.awrite(_path(), "# Aurora\nv2")

        assert len(emit.records) == 2
        assert [r.version for r in emit.records] == [1, 2]


class TestDraftBackendEdit:
    async def test_edit_on_missing_file_returns_file_not_found(self) -> None:
        backend = _backend()
        result = await backend.aedit(_path(), old_string="x", new_string="y")
        assert result.error == "file_not_found"

    async def test_edit_substitutes_and_inserts_new_version(self) -> None:
        store = InMemoryDraftStore()
        backend = _backend(store=store)
        await backend.awrite(_path(), "# Aurora\n\nLaunch announcement.")

        result = await backend.aedit(
            _path(),
            old_string="Launch announcement.",
            new_string="Launch announcement v2.",
        )

        assert result.error is None
        assert result.occurrences == 1
        latest = store.latest(org_id="org_acme", draft_id=_draft_id())
        assert latest is not None
        assert "Launch announcement v2." in latest.content_text
        assert latest.version == 2

    async def test_edit_missing_substring_returns_no_match(self) -> None:
        backend = _backend()
        await backend.awrite(_path(), "# Aurora\nbody")

        result = await backend.aedit(_path(), old_string="ZZZ", new_string="x")
        # Distinct error string but not the standard literal — the spec
        # surfaces a clear message rather than the deepagents `not_found`.
        assert result.error is not None
        assert (
            "old_string" in (result.error or "").lower()
            or "not found" in (result.error or "").lower()
        )

    async def test_edit_ambiguous_match_requires_replace_all(self) -> None:
        backend = _backend()
        await backend.awrite(_path(), "# Aurora\n\nfoo foo foo")

        result = await backend.aedit(_path(), old_string="foo", new_string="bar")
        assert result.error is not None
        assert "ambiguous" in (result.error or "").lower()

    async def test_edit_replace_all_succeeds(self) -> None:
        store = InMemoryDraftStore()
        backend = _backend(store=store)
        await backend.awrite(_path(), "# Aurora\n\nfoo foo foo")

        result = await backend.aedit(
            _path(), old_string="foo", new_string="bar", replace_all=True
        )

        assert result.error is None
        assert result.occurrences == 3
        latest = store.latest(org_id="org_acme", draft_id=_draft_id())
        assert latest is not None
        assert latest.content_text.count("bar") == 3


class TestDraftBackendRead:
    async def test_read_returns_latest_content(self) -> None:
        backend = _backend()
        await backend.awrite(_path(), "# Aurora\nv1")
        await backend.awrite(_path(), "# Aurora\nv2")

        result = await backend.aread(_path())

        assert result.error is None
        assert result.file_data is not None
        assert "v2" in result.file_data["content"]

    async def test_read_unknown_returns_file_not_found(self) -> None:
        backend = _backend()
        result = await backend.aread(_path())
        assert result.error == "file_not_found"


class TestDraftBackendList:
    async def test_ls_returns_one_entry_per_draft(self) -> None:
        store = InMemoryDraftStore()
        backend = _backend(store=store)
        await backend.awrite(_path(), "# Aurora\nbody")

        result = await backend.als("/")

        assert result.error is None
        assert result.entries is not None
        assert len(result.entries) == 1
        assert result.entries[0]["path"] == f"/{_draft_id()}.md"


class TestDraftBackendCrossOrg:
    async def test_other_org_cannot_read_draft(self) -> None:
        store = InMemoryDraftStore()
        # Org A writes
        a_backend = _backend(store=store)
        await a_backend.awrite(_path(), "# Aurora\nbody")

        # Org B reads — gets file_not_found because org_id is bound at construction.
        b_backend = DraftBackend(
            store=store,
            org_id="org_other",
            conversation_id="conv_1",
            run_id="run_2",
            user_id="user_other",
        )
        result = await b_backend.aread(_path())
        assert result.error == "file_not_found"


class TestDraftBackendConcurrency:
    async def test_serializes_writes_to_same_draft(self) -> None:
        store = InMemoryDraftStore()
        backend = _backend(store=store)

        # Two writes scheduled concurrently in the same event loop must
        # produce versions 1 and 2 (no version-collision OptimisticConflict)
        # because the per-draft lock serializes the latest-then-+1 read.
        await asyncio.gather(
            backend.awrite(_path(), "v1"),
            backend.awrite(_path(), "v2"),
        )
        history = store.versions[("org_acme", _draft_id())]
        assert [record.version for record in history] == [1, 2]
