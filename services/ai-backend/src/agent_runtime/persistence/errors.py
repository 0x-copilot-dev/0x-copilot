"""Typed persistence-layer errors for the agent runtime.

These exceptions surface concurrency conflicts in a form the worker's retry
helper can recognize without inspecting SQL state codes. Keep public messages
generic — never include row data, predicate values, or other org-scoped
content.
"""

from __future__ import annotations


class PersistenceError(Exception):
    """Base class for typed persistence-layer errors."""


class ConcurrentRunUpdateError(PersistenceError):
    """Raised when an ``agent_runs`` UPDATE misses on its ``row_version`` CAS.

    The caller's read of ``row_version`` was stale by the time the UPDATE
    fired — another writer committed first. The runtime worker catches this
    via ``with_optimistic_retry`` and retries from a fresh read; if retries
    are exhausted the error propagates.
    """

    def __init__(self, *, run_id: str, expected_version: int) -> None:
        super().__init__("concurrent agent_runs update detected")
        self.run_id = run_id
        self.expected_version = expected_version


class ConcurrentMemoryItemUpdateError(PersistenceError):
    """Raised when a ``runtime_memory_items`` UPDATE misses on ``version``.

    Same semantics as ConcurrentRunUpdateError but scoped to memory items.
    """

    def __init__(self, *, item_id: str, expected_version: int) -> None:
        super().__init__("concurrent runtime_memory_items update detected")
        self.item_id = item_id
        self.expected_version = expected_version
