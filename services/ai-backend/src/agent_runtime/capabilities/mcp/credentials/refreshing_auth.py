"""Rotating bearer ``httpx.Auth`` for direct-connect MCP (migration P2-2).

**Additive and unwired** — nothing in the running app constructs this yet
(P2-PLAN §3, P2-2). It is the piece that lets **one long-lived**
``MultiServerMCPClient`` outlive the credentials it authenticates with: token
refresh lives *inside* a small ``httpx.Auth`` handed to the client at
construction time, so an expiring bearer never forces the MCP subsystem to tear
down and rebuild sessions, and no subsystem above it has to know that tokens
expire at all.

The four properties it exists to guarantee:

* **Mint on first use.** Nothing is fetched at construction; the first request
  that needs a bearer triggers the injected fetch. Building an auth for a
  connector the run never calls costs one object and zero credential reads.
* **Cache until ``expires_at − skew``.** The skew is the safety margin against
  clock drift and in-flight latency, so a token is replaced *before* the server
  would reject it rather than after.
* **Exactly one refreshed retry on a 401 — never a loop.** A rejected bearer is
  re-minted once and the request is re-yielded once; the flow then ends, so a
  server that answers 401 unconditionally costs exactly two requests, not an
  infinite refresh storm against the keychain broker / mint endpoint.
* **The plaintext never renders.** The secret lives in
  :class:`~agent_runtime.capabilities.mcp.connection.MintedToken`'s
  ``SecretStr`` and is unwrapped in exactly one place — the moment the
  ``Authorization`` header is written. ``repr``, ``vars()``, structured-log dumps
  and tracebacks see the mask.

The token source is injected as a :class:`MintedTokenFetch` (a bound, per-server
read), so this module stays transport- and deployment-agnostic: P2-7a supplies
the desktop loopback-broker read, P2-7b the gated ``services/backend`` mint.
Failures from that seam are converted into typed domain errors carrying safe
public messages — an already-typed :class:`McpClientError` is preserved so the
error-map stage (P2-5) keeps the connector's own taxonomy.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx

from agent_runtime.capabilities.mcp.client import McpAuthError, McpClientError
from agent_runtime.capabilities.mcp.connection import MintedToken


class MintedTokenFetch(Protocol):
    """One credential round-trip for a single, already-bound server.

    The server identity is closed over by the provider that builds the auth
    (P2-7), so the auth itself never needs a ``server_id`` and cannot be pointed
    at a connector it was not minted for.
    """

    async def __call__(self) -> MintedToken: ...


class UtcClock(Protocol):
    """The injected "now" — must return a **timezone-aware** UTC datetime.

    Injected rather than read from the module so expiry behaviour is provable
    without sleeping in tests, and so a deployment can substitute a corrected
    clock without touching the refresh logic.
    """

    def __call__(self) -> datetime: ...


class McpCredentialFetchError(McpAuthError):
    """The credential read for a connector failed; the call cannot be authorized.

    Deliberately an :class:`McpAuthError`: a missing / unreadable bearer is the
    same class of failure as a rejected one — actionable by reconnecting, never
    retryable in place — so it lands on the existing taxonomy rung (P2-5 maps
    ``McpAuthError`` → AUTH_FAILURE, non-retryable) instead of inventing a new
    one. The originating exception is chained for logs; the public message is
    fixed and leaks no path, host, or vault detail.
    """

    SAFE_MESSAGE = (
        "Could not obtain credentials for this connector. Reconnect it and try again."
    )

    def __init__(self) -> None:
        """Raise with the fixed safe message (never a formatted internal detail)."""

        super().__init__(self.SAFE_MESSAGE)


class McpCredentialFlowError(McpClientError):
    """A synchronous client tried to use an auth whose refresh is async-only.

    Fail-closed: the base ``httpx.Auth.sync_auth_flow`` would silently fall back
    to ``auth_flow``, which yields the request **unauthenticated**. Refusing is
    the only safe answer.
    """

    SAFE_MESSAGE = "This connector's credentials require an asynchronous client."

    def __init__(self) -> None:
        """Raise with the fixed safe message."""

        super().__init__(self.SAFE_MESSAGE)


class McpCredentialConfigError(McpClientError):
    """The auth was constructed with an unusable refresh window.

    A negative skew would schedule the refresh *after* the stated expiry, i.e.
    guarantee that every token is first used while already dead — a 401 storm by
    construction. Rejected at construction, not at first request.
    """

    SAFE_MESSAGE = "Connector credential refresh is misconfigured."

    def __init__(self) -> None:
        """Raise with the fixed safe message."""

        super().__init__(self.SAFE_MESSAGE)


class RefreshingBearerAuth(httpx.Auth):
    """Mints, caches and rotates one server's bearer for a long-lived MCP client.

    Attach one instance per server to the ``langchain-mcp-adapters`` connection
    (P2-3). Concurrency is guarded by an :class:`asyncio.Lock` held **only**
    across the fetch — never across a ``yield`` — so N concurrent first calls
    cost one mint, not N (a token stampede against the broker), while the
    request/response handshake stays fully concurrent.
    """

    class Keys:
        """Wire keys this auth writes — declared once, never inlined."""

        AUTHORIZATION = "Authorization"
        BEARER = "Bearer"

    #: Refresh this far ahead of the stated expiry. Absorbs client/server clock
    #: drift plus the in-flight latency of the request the token is about to
    #: authenticate, so a token is replaced before the server would reject it.
    DEFAULT_SKEW = timedelta(seconds=60)

    #: The single status that means "the bearer we sent was rejected". Any other
    #: failure status is the *server's* answer to an authenticated request and is
    #: returned to the caller untouched — re-minting on a 500 would burn
    #: credentials on an outage.
    _REJECTED_STATUS = httpx.codes.UNAUTHORIZED

    def __init__(
        self,
        *,
        fetch: MintedTokenFetch,
        skew: timedelta | None = None,
        clock: UtcClock | None = None,
    ) -> None:
        """Bind the credential read and the refresh window; fetch nothing yet."""

        if skew is not None and skew < timedelta(0):
            raise McpCredentialConfigError()
        self._fetch = fetch
        self._skew = self.DEFAULT_SKEW if skew is None else skew
        self._clock: UtcClock = self._utc_now if clock is None else clock
        self._cached: MintedToken | None = None
        self._lock = asyncio.Lock()

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        """Attach a cached-or-minted bearer, retrying **once** on a single 401.

        The retry is expressed structurally rather than with a counter: there is
        exactly one statement that can yield a second time and the generator ends
        immediately after it, so a server answering 401 forever still sees
        exactly two requests and the caller receives that second 401 as the
        outcome. There is no code path back to the first yield.
        """

        token = await self._current_token()
        self._authorize(request, token)
        response = yield request
        if response.status_code != self._REJECTED_STATUS:
            return
        refreshed = await self._refresh_after_rejection(rejected=token)
        self._authorize(request, refreshed)
        yield request

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        """Refuse the synchronous flow instead of sending an unauthenticated request."""

        raise McpCredentialFlowError()

    def __repr__(self) -> str:
        """Redacted rendering: the class and whether a token is held, never which."""

        held = "cached" if self._cached is not None else "empty"
        return f"{type(self).__name__}(token={held})"

    def _authorize(self, request: httpx.Request, token: MintedToken) -> None:
        """Write the bearer header — the **only** place the plaintext is unwrapped."""

        request.headers[self.Keys.AUTHORIZATION] = (
            f"{self.Keys.BEARER} {token.value.get_secret_value()}"
        )

    async def _current_token(self) -> MintedToken:
        """Return the cached token while it is fresh, otherwise mint one.

        Double-checked under the lock: the cheap read outside covers the steady
        state (no lock on the hot path), and the re-read inside means a caller
        that queued behind someone else's mint takes that result instead of
        immediately minting a second time.
        """

        cached = self._cached
        if cached is not None and self._is_fresh(cached):
            return cached
        async with self._lock:
            cached = self._cached
            if cached is not None and self._is_fresh(cached):
                return cached
            return await self._mint()

    async def _refresh_after_rejection(self, *, rejected: MintedToken) -> MintedToken:
        """Mint a replacement for a bearer the server just refused.

        Freshness is deliberately **not** consulted — the server's 401 outranks
        our own expiry arithmetic. Identity is: if the cache already holds a
        different token, a concurrent flow replaced the rejected one and that
        replacement is used rather than minting again.
        """

        async with self._lock:
            current = self._cached
            if current is not None and current is not rejected:
                return current
            return await self._mint()

    async def _mint(self) -> MintedToken:
        """Run the injected fetch, validate it, and cache it. Caller holds the lock.

        The fetch is an untrusted boundary (it parses a broker or backend
        response), so its result is type-checked and any non-domain failure is
        converted to a typed error with a safe message. An already-typed
        :class:`McpClientError` is re-raised unchanged so a provider's own
        taxonomy (connection / timeout / auth) survives to the error-map stage.
        """

        try:
            token = await self._fetch()
        except McpClientError:
            raise
        except Exception as exc:  # noqa: BLE001 - narrowed into a typed domain error.
            raise McpCredentialFetchError() from exc
        if not isinstance(token, MintedToken):
            raise McpCredentialFetchError()
        self._cached = token
        return token

    def _is_fresh(self, token: MintedToken) -> bool:
        """True while ``now < expires_at − skew`` — the cache window."""

        return self._clock() < self._refresh_deadline(token)

    def _refresh_deadline(self, token: MintedToken) -> datetime:
        """``expires_at − skew``, reading a naive expiry as UTC.

        A mint that omits the offset is read as UTC rather than compared against
        an aware clock (which raises) or trusted as local time (which would move
        the deadline by the host's offset).
        """

        expires_at = token.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at - self._skew

    @staticmethod
    def _utc_now() -> datetime:
        """The default clock — timezone-aware UTC now (kept in-class, not module-level)."""

        return datetime.now(UTC)


__all__ = [
    "McpCredentialConfigError",
    "McpCredentialFetchError",
    "McpCredentialFlowError",
    "MintedTokenFetch",
    "RefreshingBearerAuth",
    "UtcClock",
]
