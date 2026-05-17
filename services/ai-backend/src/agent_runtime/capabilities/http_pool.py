"""Process-scoped pooled httpx client for ai-backend → backend HTTP calls.

Before this module existed, every backend round-trip (MCP server listing,
MCP JSON-RPC tool calls, Skill card fetches, Skill bundle loads) created a
fresh ``httpx.AsyncClient(...)`` inside an ``async with`` block. Each
construction paid a full TLS handshake (50–200 ms on a real network) since
no connection pool persisted between calls. On a run with five tool calls,
that's a quarter-second to a full second of pure handshake cost stacked on
top of the actual work.

One shared client per process collapses that handshake amortization to
"once per stale-keepalive window," and the existing per-call ``timeout=``
override means each method still controls its own deadline.

Why a class and not a module function: keeps the production helper inside
a class per ``services/ai-backend/CLAUDE.md``'s "Avoid module-level helper
functions" rule, and gives us a clean test seam — both production code
paths read through ``BackendHttpPool.get()`` and tests that don't care
about HTTP get the same shared instance, while tests that want to assert
on calls inject a ``FakeAsyncClient`` directly through the consumer's
``http_client`` field (the pool is the *default*, not the contract).
"""

from __future__ import annotations

from typing import ClassVar

import httpx


class BackendHttpPool:
    """Lazy-initialized, process-scoped shared ``httpx.AsyncClient``.

    Limits are sized for ai-backend's worst case: a moderate fan-out of
    concurrent MCP/Skill calls per run, multiplied by the worker's
    parallelism setting. ``keepalive_expiry`` is set to 30s to amortize
    TLS across typical tool-burst windows while not pinning connections
    long enough to cause upstream pool-exhaustion noise.

    Per-request ``timeout=`` overrides on each call still apply — the
    client-level default is only a backstop for callers that forget.
    """

    _LIMITS: ClassVar[httpx.Limits] = httpx.Limits(
        max_connections=100,
        max_keepalive_connections=20,
        keepalive_expiry=30.0,
    )
    _CLIENT_DEFAULT_TIMEOUT: ClassVar[float] = 30.0
    _client: ClassVar[httpx.AsyncClient | None] = None

    @classmethod
    def get(cls) -> httpx.AsyncClient:
        """Return the shared client, constructing it on first use.

        Callers should also pass a per-call ``timeout=`` to the request
        method — the client default is a guardrail, not a domain choice.
        """

        if cls._client is None:
            cls._client = httpx.AsyncClient(
                limits=cls._LIMITS,
                timeout=cls._CLIENT_DEFAULT_TIMEOUT,
            )
        return cls._client

    @classmethod
    async def aclose(cls) -> None:
        """Close the shared client and reset the slot.

        Wired into the runtime API + worker shutdown hooks so graceful
        SIGTERM lets in-flight connections drain. Idempotent: tests that
        rebuild the pool between cases can call this freely.
        """

        if cls._client is not None:
            client = cls._client
            cls._client = None
            await client.aclose()
