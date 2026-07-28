from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_runtime.capabilities.mcp.revision_feed import (
    ActiveMcpRevisionSubjectRegistry,
    InMemoryMcpRevisionCursorStore,
    McpCatalogGenerationAuthorityPort,
    McpDescriptorCacheInvalidationPort,
    McpRevisionCursorStoreError,
    McpRevisionFeedCoordinator,
    McpRevisionFeedRunner,
    McpRevisionFeedSubjectState,
    McpRevisionSubject,
    ProcessLocalMcpCatalogGenerationAuthority,
    ProcessLocalMcpDescriptorCacheInvalidator,
)
from agent_runtime.capabilities.mcp.revision_wire import (
    BackendMcpRevisionCursorExpired,
    BackendMcpRevisionFeed,
    BackendMcpRevisionNotice,
    BackendMcpRevisionUnavailable,
)
from runtime_adapters.file.mcp_revision_cursor import (
    DesktopFilesystemMcpRevisionCursorStore,
    McpRevisionCursorStoreUnsupported,
)


def _subject(suffix: str = "a") -> McpRevisionSubject:
    return McpRevisionSubject(org_id=f"org-{suffix}", user_id=f"user-{suffix}")


def _notice(
    *, notice_id: str = "notice-a", server_id: str = "server-a"
) -> BackendMcpRevisionNotice:
    return BackendMcpRevisionNotice.model_validate(
        {
            "cursor": "notice-cursor",
            "notice_id": notice_id,
            "sequence_no": 1,
            "server_id": server_id,
            "profile_id": "profile-a",
            "subject_scope_hash": "scope-a",
            "new_revision": "revision-b",
            "reason": "config_changed",
            "occurred_at": "2026-01-01T00:01:00Z",
        }
    )


class _Resolver:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.flushed: list[tuple[str, str]] = []

    async def apply_notice(self, *, org_id: str, user_id: str, notice: object) -> None:
        self.events.append(f"resolver:{org_id}:{user_id}")

    async def invalidate_subject(self, *, org_id: str, user_id: str) -> None:
        self.events.append(f"resolver-flush:{org_id}:{user_id}")
        self.flushed.append((org_id, user_id))


class _Descriptors(McpDescriptorCacheInvalidationPort):
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail
        self.flushed: list[McpRevisionSubject] = []

    async def invalidate_descriptor(
        self, *, subject: McpRevisionSubject, server_id: str, notice_id: str
    ) -> None:
        self.events.append(f"descriptor:{server_id}")
        if self.fail:
            raise RuntimeError("descriptor failure")

    async def flush_subject(self, *, subject: McpRevisionSubject) -> None:
        self.events.append(f"descriptor-flush:{subject.org_id}")
        self.flushed.append(subject)


class _Catalog(McpCatalogGenerationAuthorityPort):
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.flushed: list[McpRevisionSubject] = []

    async def advance(
        self, *, subject: McpRevisionSubject, server_id: str, notice_id: str
    ) -> int:
        self.events.append(f"catalog:{server_id}")
        return 1

    async def flush_subject(self, *, subject: McpRevisionSubject) -> None:
        self.events.append(f"catalog-flush:{subject.org_id}")
        self.flushed.append(subject)


class _FailOnceCursorStore(InMemoryMcpRevisionCursorStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail = True

    async def save(self, subject: McpRevisionSubject, cursor: str) -> None:
        if self.fail:
            self.fail = False
            raise RuntimeError("disk full")
        await super().save(subject, cursor)


@pytest.mark.asyncio
async def test_active_subjects_are_capped_expire_and_isolate() -> None:
    now = [0.0]
    registry = ActiveMcpRevisionSubjectRegistry(
        max_subjects=1, inactivity_ttl_seconds=10, clock=lambda: now[0]
    )
    assert await registry.touch_verified(_subject("a"))
    assert not await registry.touch_verified(_subject("b"))
    assert await registry.active_subjects() == (_subject("a"),)
    now[0] = 10
    assert await registry.active_subjects() == ()
    assert await registry.touch_verified(_subject("b"))


@pytest.mark.asyncio
async def test_filesystem_cursor_is_opaque_restrictive_and_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DesktopFilesystemMcpRevisionCursorStore(tmp_path)
    subject = _subject()
    await store.save(subject, "cursor-a")
    cursor_dir = tmp_path / "mcp-revision-cursors"
    path = next(cursor_dir.iterdir())
    assert subject.org_id not in path.name and subject.user_id not in path.name
    assert path.stat().st_mode & 0o077 == 0
    assert await store.load(subject) == "cursor-a"

    def fail_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("no replace")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(McpRevisionCursorStoreError):
        await store.save(subject, "cursor-b")
    assert await store.load(subject) == "cursor-a"
    assert not list(cursor_dir.glob(".*.tmp"))


@pytest.mark.asyncio
async def test_filesystem_cursor_rejects_symlink_oversize_and_corruption(
    tmp_path: Path,
) -> None:
    store = DesktopFilesystemMcpRevisionCursorStore(tmp_path)
    subject = _subject()
    await store.save(subject, "cursor-a")
    path = next((tmp_path / "mcp-revision-cursors").iterdir())
    path.unlink()
    path.symlink_to(tmp_path / "target")
    with pytest.raises(McpRevisionCursorStoreError):
        await store.load(subject)
    path.unlink()
    path.write_bytes(b"x" * 1025)
    path.chmod(0o600)
    with pytest.raises(McpRevisionCursorStoreError):
        await store.load(subject)
    path.write_bytes(b"not-json")
    path.chmod(0o600)
    with pytest.raises(McpRevisionCursorStoreError):
        await store.load(subject)


@pytest.mark.asyncio
async def test_filesystem_cursor_rejects_a_symlinked_root(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir()
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(real_root, target_is_directory=True)
    store = DesktopFilesystemMcpRevisionCursorStore(linked_root)
    with pytest.raises(McpRevisionCursorStoreError):
        await store.load(_subject())


def test_filesystem_cursor_fails_construction_without_dirfd_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        DesktopFilesystemMcpRevisionCursorStore,
        "_supports_descriptor_operations",
        staticmethod(lambda: False),
    )
    with pytest.raises(McpRevisionCursorStoreUnsupported):
        DesktopFilesystemMcpRevisionCursorStore(tmp_path)


@pytest.mark.asyncio
async def test_filesystem_cursor_clear_rejects_symlink_without_touching_target(
    tmp_path: Path,
) -> None:
    store = DesktopFilesystemMcpRevisionCursorStore(tmp_path)
    subject = _subject()
    await store.save(subject, "cursor-a")
    path = next((tmp_path / "mcp-revision-cursors").iterdir())
    external = tmp_path / "external"
    external.write_text("keep")
    path.unlink()
    path.symlink_to(external)
    with pytest.raises(McpRevisionCursorStoreError):
        await store.clear(subject)
    assert external.read_text() == "keep"
    assert path.is_symlink()


@pytest.mark.asyncio
async def test_coordinator_orders_work_and_does_not_advance_cursor_on_failure() -> None:
    events: list[str] = []
    cursors = InMemoryMcpRevisionCursorStore()
    coordinator = McpRevisionFeedCoordinator(
        resolver=_Resolver(events),
        descriptors=_Descriptors(events),
        catalog=_Catalog(events),
        cursors=cursors,
    )
    feed = BackendMcpRevisionFeed(notices=(_notice(),), next_cursor="page-cursor")
    await coordinator.apply_page(subject=_subject(), feed=feed)
    assert events == [
        "resolver:org-a:user-a",
        "descriptor:server-a",
        "catalog:server-a",
    ]
    assert await cursors.load(_subject()) == "page-cursor"

    failing = McpRevisionFeedCoordinator(
        resolver=_Resolver([]),
        descriptors=_Descriptors([], fail=True),
        catalog=_Catalog([]),
        cursors=cursors,
    )
    with pytest.raises(RuntimeError):
        await failing.apply_page(subject=_subject("b"), feed=feed)
    assert await cursors.load(_subject("b")) is None


@pytest.mark.asyncio
async def test_cursor_write_replay_is_idempotent_and_subject_flush_is_isolated() -> (
    None
):
    events: list[str] = []
    cursors = _FailOnceCursorStore()
    resolver = _Resolver(events)
    descriptors = _Descriptors(events)
    catalog = _Catalog(events)
    coordinator = McpRevisionFeedCoordinator(
        resolver=resolver, descriptors=descriptors, catalog=catalog, cursors=cursors
    )
    feed = BackendMcpRevisionFeed(notices=(_notice(),), next_cursor="page-cursor")
    with pytest.raises(RuntimeError):
        await coordinator.apply_page(subject=_subject(), feed=feed)
    await coordinator.apply_page(subject=_subject(), feed=feed)
    assert events.count("resolver:org-a:user-a") == 1
    assert await cursors.load(_subject()) == "page-cursor"
    await coordinator.reset_expired_subject(subject=_subject())
    assert resolver.flushed == [("org-a", "user-a")]
    assert descriptors.flushed == [_subject()]
    assert catalog.flushed == [_subject()]
    assert await cursors.load(_subject()) is None
    assert await cursors.load(_subject("b")) is None


@pytest.mark.asyncio
async def test_process_local_authorities_are_idempotent_and_subject_scoped() -> None:
    descriptors = ProcessLocalMcpDescriptorCacheInvalidator(max_notices=2)
    catalog = ProcessLocalMcpCatalogGenerationAuthority(max_notices=2)
    await descriptors.invalidate_descriptor(
        subject=_subject(), server_id="s", notice_id="n"
    )
    await descriptors.invalidate_descriptor(
        subject=_subject(), server_id="s", notice_id="n"
    )
    assert await descriptors.generation(subject=_subject(), server_id="s") == 1
    assert await catalog.advance(subject=_subject(), server_id="s", notice_id="n") == 1
    assert await catalog.advance(subject=_subject(), server_id="s", notice_id="n") == 1
    await descriptors.invalidate_descriptor(
        subject=_subject("b"), server_id="s", notice_id="n"
    )
    assert await descriptors.generation(subject=_subject("b"), server_id="s") == 1


class _FeedClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls = 0

    async def feed(self, **_kwargs: object) -> BackendMcpRevisionFeed:
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_runner_empty_offline_bounds_and_cursor_expiry() -> None:
    registry = ActiveMcpRevisionSubjectRegistry()
    cursors = InMemoryMcpRevisionCursorStore()
    coordinator = McpRevisionFeedCoordinator(
        resolver=_Resolver([]),
        descriptors=_Descriptors([]),
        catalog=_Catalog([]),
        cursors=cursors,
    )
    empty_client = _FeedClient([])
    runner = McpRevisionFeedRunner(
        client=empty_client,
        subjects=registry,
        cursors=cursors,
        coordinator=coordinator,  # type: ignore[arg-type]
    )
    assert (await runner.run_once()).http_calls == 0

    await registry.touch_verified(_subject())
    await cursors.save(_subject(), "old")
    offline = McpRevisionFeedRunner(
        client=_FeedClient([BackendMcpRevisionUnavailable("offline")]),  # type: ignore[arg-type]
        subjects=registry,
        cursors=cursors,
        coordinator=coordinator,
        random=lambda: 0,
    )
    result = await offline.run_once()
    assert result.results[0].state is McpRevisionFeedSubjectState.OFFLINE
    assert result.retry_after_seconds is not None
    assert await cursors.load(_subject()) == "old"

    expired = McpRevisionFeedRunner(
        client=_FeedClient([BackendMcpRevisionCursorExpired("old")]),  # type: ignore[arg-type]
        subjects=registry,
        cursors=cursors,
        coordinator=coordinator,
    )
    assert (await expired.run_once()).results[
        0
    ].state is McpRevisionFeedSubjectState.CURSOR_EXPIRED
    assert await cursors.load(_subject()) is None

    bounded = McpRevisionFeedRunner(
        client=_FeedClient(
            [
                BackendMcpRevisionFeed(
                    notices=(_notice(notice_id="n1"), _notice(notice_id="n2")),
                    next_cursor=None,
                )
            ]
        ),  # type: ignore[arg-type]
        subjects=registry,
        cursors=cursors,
        coordinator=coordinator,
        max_notices=1,
    )
    assert (await bounded.run_once()).results[
        0
    ].state is McpRevisionFeedSubjectState.BOUND_EXCEEDED


@pytest.mark.asyncio
async def test_runner_treats_empty_or_short_pages_as_quiescent() -> None:
    registry = ActiveMcpRevisionSubjectRegistry()
    await registry.touch_verified(_subject())
    cursors = InMemoryMcpRevisionCursorStore()
    await cursors.save(_subject(), "old")
    coordinator = McpRevisionFeedCoordinator(
        resolver=_Resolver([]),
        descriptors=_Descriptors([]),
        catalog=_Catalog([]),
        cursors=cursors,
    )
    empty = McpRevisionFeedRunner(
        client=_FeedClient([BackendMcpRevisionFeed(notices=(), next_cursor="old")]),  # type: ignore[arg-type]
        subjects=registry,
        cursors=cursors,
        coordinator=coordinator,
        page_limit=1,
    )
    empty_result = await empty.run_once()
    assert empty_result.results[0].state is McpRevisionFeedSubjectState.APPLIED
    assert empty_result.http_calls == 1
    assert await cursors.load(_subject()) == "old"

    short = McpRevisionFeedRunner(
        client=_FeedClient(
            [BackendMcpRevisionFeed(notices=(_notice(),), next_cursor="new")]
        ),  # type: ignore[arg-type]
        subjects=registry,
        cursors=cursors,
        coordinator=coordinator,
        page_limit=2,
    )
    assert (await short.run_once()).results[
        0
    ].state is McpRevisionFeedSubjectState.APPLIED
    assert await cursors.load(_subject()) == "new"


@pytest.mark.asyncio
async def test_runner_rejects_nonempty_stalled_or_cyclic_cursors_before_reapply() -> (
    None
):
    registry = ActiveMcpRevisionSubjectRegistry()
    await registry.touch_verified(_subject())
    cursors = InMemoryMcpRevisionCursorStore()
    await cursors.save(_subject(), "old")
    events: list[str] = []
    coordinator = McpRevisionFeedCoordinator(
        resolver=_Resolver(events),
        descriptors=_Descriptors(events),
        catalog=_Catalog(events),
        cursors=cursors,
    )
    stalled = McpRevisionFeedRunner(
        client=_FeedClient(
            [BackendMcpRevisionFeed(notices=(_notice(),), next_cursor="old")]
        ),  # type: ignore[arg-type]
        subjects=registry,
        cursors=cursors,
        coordinator=coordinator,
        page_limit=1,
    )
    assert (await stalled.run_once()).results[
        0
    ].state is McpRevisionFeedSubjectState.CURSOR_STALLED
    assert events == []
    assert await cursors.load(_subject()) == "old"

    short_stalled = McpRevisionFeedRunner(
        client=_FeedClient(
            [
                BackendMcpRevisionFeed(
                    notices=(_notice(notice_id="short"),), next_cursor="old"
                )
            ]
        ),  # type: ignore[arg-type]
        subjects=registry,
        cursors=cursors,
        coordinator=coordinator,
        page_limit=2,
    )
    assert (await short_stalled.run_once()).results[
        0
    ].state is McpRevisionFeedSubjectState.CURSOR_STALLED
    assert events == []
    assert await cursors.load(_subject()) == "old"

    terminal_stalled = McpRevisionFeedRunner(
        client=_FeedClient(
            [
                BackendMcpRevisionFeed(
                    notices=(_notice(notice_id="terminal"),), next_cursor=None
                )
            ]
        ),  # type: ignore[arg-type]
        subjects=registry,
        cursors=cursors,
        coordinator=coordinator,
        page_limit=2,
    )
    assert (await terminal_stalled.run_once()).results[
        0
    ].state is McpRevisionFeedSubjectState.CURSOR_STALLED
    assert events == []
    assert await cursors.load(_subject()) == "old"

    cycling = McpRevisionFeedRunner(
        client=_FeedClient(
            [
                BackendMcpRevisionFeed(
                    notices=(_notice(notice_id="one"),), next_cursor="cursor-one"
                ),
                BackendMcpRevisionFeed(
                    notices=(_notice(notice_id="two"),), next_cursor="old"
                ),
            ]
        ),  # type: ignore[arg-type]
        subjects=registry,
        cursors=cursors,
        coordinator=coordinator,
        page_limit=1,
    )
    cycle_result = await cycling.run_once()
    assert cycle_result.results[0].state is McpRevisionFeedSubjectState.CURSOR_STALLED
    assert events == [
        "resolver:org-a:user-a",
        "descriptor:server-a",
        "catalog:server-a",
    ]
    assert await cursors.load(_subject()) == "cursor-one"


@pytest.mark.asyncio
async def test_cursor_load_error_does_not_reset_or_advance_feed_state(
    tmp_path: Path,
) -> None:
    registry = ActiveMcpRevisionSubjectRegistry()
    await registry.touch_verified(_subject())
    cursors = DesktopFilesystemMcpRevisionCursorStore(tmp_path)
    await cursors.save(_subject(), "old")
    cursor_path = next((tmp_path / "mcp-revision-cursors").iterdir())
    cursor_path.write_text("corrupt")
    cursor_path.chmod(0o600)
    client = _FeedClient([])
    runner = McpRevisionFeedRunner(
        client=client,  # type: ignore[arg-type]
        subjects=registry,
        cursors=cursors,
        coordinator=McpRevisionFeedCoordinator(
            resolver=_Resolver([]),
            descriptors=_Descriptors([]),
            catalog=_Catalog([]),
            cursors=cursors,
        ),
    )
    assert (await runner.run_once()).results[
        0
    ].state is McpRevisionFeedSubjectState.FAILED
    assert client.calls == 0
    assert cursor_path.read_text() == "corrupt"
