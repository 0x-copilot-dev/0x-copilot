"""C10 ``@reader`` decorator marking methods that may target a read replica.

The decorator stamps an attribute the store consults when picking a pool
(primary vs. replica). The CI static check
``tools/check_reader_methods.py`` walks the AST of any method tagged
``@reader`` and refuses to merge if it contains an ``INSERT`` /
``UPDATE`` / ``DELETE`` SQL keyword — keeping accidental writes off the
read-only path.

Usage:

    from agent_runtime.persistence._reader import reader

    @reader
    async def query_user_daily(self, ...): ...
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar


_F = TypeVar("_F", bound=Callable[..., object])


READER_ATTR = "__reader__"


def reader(method: _F) -> _F:
    """Mark a method as eligible for read-replica routing.

    No runtime behavior change — the store inspects the attribute when
    it picks a pool. The static check is what enforces "no writes here".
    """

    setattr(method, READER_ATTR, True)
    return method
