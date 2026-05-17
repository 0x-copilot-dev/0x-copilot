"""Lifespan-owned shared ``httpx.AsyncClient`` for outbound facade calls.

Before this module, every facade route opened a fresh ``httpx.AsyncClient(...)``
inside an ``async with`` block. That meant a full TLS handshake (50–200 ms
against a real backend) on every request — including the auth-touch hop
that happens on most requests. A single chat-page load could pay this
handshake cost a dozen times.

A single client per worker process amortizes TLS across calls (httpx
keeps the connection alive in its pool), so the same handshake is paid
once per upstream every ``keepalive_expiry`` seconds, not per request.

Why a class with classmethods: keeps construction + teardown reachable
from the FastAPI lifespan without polluting that file with a module-level
mutable singleton. ``attach(app)`` is the single point at which we couple
the pool to a FastAPI instance — tests can patch ``httpx.AsyncClient``
at this path and the patched class flows through every consumer.

How call sites consume it: every route handler reads
``request.app.state.http_client`` (or ``settings_for(app)``-style
helpers that take ``app`` accept a client through ``app.state``). Per-call
timeouts move from client construction to the request method's
``timeout=`` kwarg, so each call site keeps its own deadline.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
import httpx


class HttpClientPool:
    """Owns the lifespan of the shared ``httpx.AsyncClient`` on ``app.state``."""

    # Sized for the facade's worst case: many concurrent verify-with-touch
    # hops plus the regular request fan-out to backend + ai-backend. Per-
    # worker pool — multiple uvicorn workers each get their own.
    _LIMITS = httpx.Limits(
        max_connections=100,
        max_keepalive_connections=20,
        keepalive_expiry=30.0,
    )

    # Backstop only — per-call ``timeout=`` overrides win when present, and
    # the streaming SSE route passes ``timeout=None`` per request.
    _DEFAULT_TIMEOUT = 30.0

    @classmethod
    def attach(cls, app: FastAPI) -> None:
        """Build the client at app construction and stash it on ``app.state``.

        Done synchronously inside :func:`create_app` (not inside lifespan)
        because ``TestClient`` is normally used without the ``with``
        context manager, so the lifespan never fires in tests. ``httpx``
        defers actual socket work to first use, so this stays cheap.
        """

        app.state.http_client = httpx.AsyncClient(
            limits=cls._LIMITS,
            timeout=cls._DEFAULT_TIMEOUT,
        )

    @classmethod
    @asynccontextmanager
    async def lifespan(cls, app: FastAPI) -> AsyncIterator[None]:
        """FastAPI lifespan context that closes the pool on shutdown.

        Used in production (``uvicorn`` / ``gunicorn`` invoke the lifespan);
        ``TestClient(create_app(...))`` skips it unless the test uses the
        ``with`` form, which is fine — short-lived test apps don't need a
        connection-pool aclose.
        """

        try:
            yield
        finally:
            await app.state.http_client.aclose()


def http_client(app: FastAPI) -> httpx.AsyncClient:
    """Read the shared client from ``app.state``.

    Tiny helper so call sites don't repeat ``app.state.http_client``
    everywhere and so a future move (e.g. multi-tenant pool with per-org
    routing) only touches one accessor.
    """

    return app.state.http_client
