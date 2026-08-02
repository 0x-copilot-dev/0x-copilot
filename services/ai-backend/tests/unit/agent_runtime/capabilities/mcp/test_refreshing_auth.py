"""Unit tests for the P2-2 rotating bearer auth (``RefreshingBearerAuth``).

The auth is additive and unwired, so these tests pin the four properties the
later phases (and the long-lived ``MultiServerMCPClient``) depend on:

* the token is cached until ``expires_at − skew`` and re-minted after it;
* a single ``401`` produces **exactly one** refreshed retry;
* a server that answers ``401`` forever costs exactly two requests — no loop;
* the ``SecretStr`` plaintext never renders into a ``repr`` or instance state.

Every case drives httpx's **real** auth machinery through an
``httpx.MockTransport`` (no network, no credentials, no sleeping — the clock is
injected), so the retry accounting is the transport's own, not a re-implementation
of it inside the test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from agent_runtime.capabilities.mcp.client import (
    McpAuthError,
    McpClientError,
    McpConnectionError,
)
from agent_runtime.capabilities.mcp.connection import MintedToken
from agent_runtime.capabilities.mcp.credentials.refreshing_auth import (
    McpCredentialConfigError,
    McpCredentialFetchError,
    McpCredentialFlowError,
    RefreshingBearerAuth,
)

_FIRST_SECRET = "lin_oauth_first-secret-value"
_ROTATED_SECRET = "lin_oauth_rotated-secret-value"
_INTERNAL_DETAIL = "/Users/someone/Library/Keychain/mcp.vault: locked"
_MINTED_AT = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
_LIFETIME = timedelta(hours=1)
_URL = "https://mcp.linear.app/mcp"


class FakeTokenMint:
    """A scripted credential read: one token per call, or a scripted failure.

    Yields control (``sleep(0)``) before answering so concurrent callers really
    interleave — otherwise a single-mint assertion would pass trivially.
    """

    def __init__(
        self,
        tokens: Sequence[MintedToken] = (),
        *,
        error: BaseException | None = None,
        result: object | None = None,
    ) -> None:
        self._tokens = list(tokens)
        self._error = error
        self._result = result
        self.calls = 0

    async def __call__(self) -> MintedToken:
        self.calls += 1
        await asyncio.sleep(0)
        if self._error is not None:
            raise self._error
        if self._result is not None:
            return self._result  # type: ignore[return-value]
        if not self._tokens:
            raise AssertionError("the mint was called more times than it was scripted")
        return self._tokens.pop(0)


class FakeClock:
    """A movable timezone-aware clock — expiry is proven without sleeping."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def __call__(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


class FakeMcpEndpoint:
    """Replays scripted statuses and records every bearer it was sent.

    The final scripted status repeats forever, so ``FakeMcpEndpoint(401)`` is the
    "always rejects" server that a looping auth would hammer.
    """

    def __init__(self, *statuses: int) -> None:
        self._statuses = list(statuses) or [httpx.codes.OK]
        self.seen_bearers: list[str] = []

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.seen_bearers.append(request.headers.get("Authorization", ""))
        status = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        return httpx.Response(status)

    def client(self, auth: httpx.Auth) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle), auth=auth)

    @property
    def request_count(self) -> int:
        return len(self.seen_bearers)


class RefreshingAuthFixtureMixin:
    """Builders shared by the concrete test classes."""

    def _token(
        self,
        secret: str = _FIRST_SECRET,
        *,
        expires_at: datetime | None = None,
    ) -> MintedToken:
        return MintedToken(
            value=SecretStr(secret),
            expires_at=_MINTED_AT + _LIFETIME if expires_at is None else expires_at,
        )

    def _bearer(self, secret: str) -> str:
        return f"Bearer {secret}"

    def _auth(
        self,
        mint: FakeTokenMint,
        *,
        clock: FakeClock | None = None,
        skew: timedelta | None = None,
    ) -> RefreshingBearerAuth:
        return RefreshingBearerAuth(
            fetch=mint,
            skew=skew,
            clock=FakeClock(_MINTED_AT) if clock is None else clock,
        )

    def _rotating_mint(self) -> FakeTokenMint:
        return FakeTokenMint(
            [
                self._token(_FIRST_SECRET),
                self._token(_ROTATED_SECRET, expires_at=_MINTED_AT + 2 * _LIFETIME),
            ]
        )


class TestTokenCaching(RefreshingAuthFixtureMixin):
    async def test_first_use_mints_once_and_attaches_the_bearer(self) -> None:
        mint = FakeTokenMint([self._token()])
        endpoint = FakeMcpEndpoint(httpx.codes.OK)
        async with endpoint.client(self._auth(mint)) as client:
            response = await client.get(_URL)
        assert response.status_code == httpx.codes.OK
        assert mint.calls == 1
        assert endpoint.seen_bearers == [self._bearer(_FIRST_SECRET)]

    async def test_cached_until_the_skew_window_opens(self) -> None:
        # One second before ``expires_at - skew``: still the original token, and
        # the credential source is not touched again.
        mint = self._rotating_mint()
        clock = FakeClock(_MINTED_AT)
        endpoint = FakeMcpEndpoint(httpx.codes.OK)
        async with endpoint.client(self._auth(mint, clock=clock)) as client:
            await client.get(_URL)
            clock.advance(
                _LIFETIME - RefreshingBearerAuth.DEFAULT_SKEW - timedelta(seconds=1)
            )
            await client.get(_URL)
        assert mint.calls == 1
        assert endpoint.seen_bearers == [self._bearer(_FIRST_SECRET)] * 2

    async def test_refreshes_once_the_skew_window_opens(self) -> None:
        # The point of the skew: the token is replaced BEFORE its stated expiry,
        # so the request is never authenticated with a bearer about to die.
        mint = self._rotating_mint()
        clock = FakeClock(_MINTED_AT)
        endpoint = FakeMcpEndpoint(httpx.codes.OK)
        async with endpoint.client(self._auth(mint, clock=clock)) as client:
            await client.get(_URL)
            clock.advance(_LIFETIME - RefreshingBearerAuth.DEFAULT_SKEW)
            await client.get(_URL)
        assert mint.calls == 2
        assert endpoint.seen_bearers == [
            self._bearer(_FIRST_SECRET),
            self._bearer(_ROTATED_SECRET),
        ]

    async def test_refreshes_after_expiry(self) -> None:
        mint = self._rotating_mint()
        clock = FakeClock(_MINTED_AT)
        endpoint = FakeMcpEndpoint(httpx.codes.OK)
        async with endpoint.client(self._auth(mint, clock=clock)) as client:
            await client.get(_URL)
            clock.advance(_LIFETIME + timedelta(minutes=5))
            await client.get(_URL)
        assert mint.calls == 2
        assert endpoint.seen_bearers[-1] == self._bearer(_ROTATED_SECRET)

    async def test_a_zero_skew_caches_right_up_to_expiry(self) -> None:
        mint = self._rotating_mint()
        clock = FakeClock(_MINTED_AT)
        endpoint = FakeMcpEndpoint(httpx.codes.OK)
        auth = self._auth(mint, clock=clock, skew=timedelta(0))
        async with endpoint.client(auth) as client:
            clock.advance(_LIFETIME - timedelta(seconds=1))
            await client.get(_URL)
            clock.advance(timedelta(seconds=1))
            await client.get(_URL)
        assert mint.calls == 2

    async def test_a_naive_expiry_is_read_as_utc(self) -> None:
        # A mint that omits the offset must not shift the deadline by the host's
        # local offset (nor raise on an aware/naive comparison).
        naive_expiry = (_MINTED_AT + _LIFETIME).replace(tzinfo=None)
        mint = FakeTokenMint(
            [
                self._token(_FIRST_SECRET, expires_at=naive_expiry),
                self._token(_ROTATED_SECRET),
            ]
        )
        clock = FakeClock(_MINTED_AT)
        endpoint = FakeMcpEndpoint(httpx.codes.OK)
        async with endpoint.client(self._auth(mint, clock=clock)) as client:
            await client.get(_URL)
            clock.advance(_LIFETIME - RefreshingBearerAuth.DEFAULT_SKEW)
            await client.get(_URL)
        assert mint.calls == 2

    async def test_concurrent_first_use_mints_exactly_once(self) -> None:
        # No token stampede: N concurrent cold calls cost one credential read.
        mint = FakeTokenMint([self._token()])
        endpoint = FakeMcpEndpoint(httpx.codes.OK)
        async with endpoint.client(self._auth(mint)) as client:
            await asyncio.gather(*(client.get(_URL) for _ in range(4)))
        assert mint.calls == 1
        assert endpoint.seen_bearers == [self._bearer(_FIRST_SECRET)] * 4


class TestUnauthorizedRetry(RefreshingAuthFixtureMixin):
    async def test_a_single_401_yields_exactly_one_refreshed_request(self) -> None:
        mint = self._rotating_mint()
        endpoint = FakeMcpEndpoint(httpx.codes.UNAUTHORIZED, httpx.codes.OK)
        async with endpoint.client(self._auth(mint)) as client:
            response = await client.get(_URL)
        assert response.status_code == httpx.codes.OK
        assert mint.calls == 2
        assert endpoint.seen_bearers == [
            self._bearer(_FIRST_SECRET),
            self._bearer(_ROTATED_SECRET),
        ]

    async def test_repeated_401_never_loops(self) -> None:
        # The server rejects every bearer. The flow must stop after one retry and
        # hand the second 401 back, rather than re-minting forever.
        mint = self._rotating_mint()
        endpoint = FakeMcpEndpoint(httpx.codes.UNAUTHORIZED)
        async with endpoint.client(self._auth(mint)) as client:
            response = await client.get(_URL)
        assert response.status_code == httpx.codes.UNAUTHORIZED
        assert endpoint.request_count == 2
        assert mint.calls == 2

    async def test_a_401_refreshes_even_while_the_cached_token_looks_fresh(
        self,
    ) -> None:
        # The server's rejection outranks our own expiry arithmetic: a bearer the
        # peer refuses is replaced, not re-sent because the clock says it is fine.
        mint = self._rotating_mint()
        endpoint = FakeMcpEndpoint(httpx.codes.UNAUTHORIZED, httpx.codes.OK)
        async with endpoint.client(self._auth(mint, clock=FakeClock(_MINTED_AT))) as (
            client
        ):
            await client.get(_URL)
        assert endpoint.seen_bearers[-1] == self._bearer(_ROTATED_SECRET)

    async def test_a_successful_response_never_refreshes(self) -> None:
        mint = FakeTokenMint([self._token()])
        endpoint = FakeMcpEndpoint(httpx.codes.OK)
        async with endpoint.client(self._auth(mint)) as client:
            await client.get(_URL)
            await client.get(_URL)
        assert mint.calls == 1
        assert endpoint.request_count == 2

    async def test_a_non_401_failure_is_returned_untouched(self) -> None:
        # Re-minting on a 500 would burn credentials on an outage; the server's
        # answer to an authenticated request belongs to the caller.
        mint = FakeTokenMint([self._token()])
        endpoint = FakeMcpEndpoint(httpx.codes.INTERNAL_SERVER_ERROR)
        async with endpoint.client(self._auth(mint)) as client:
            response = await client.get(_URL)
        assert response.status_code == httpx.codes.INTERNAL_SERVER_ERROR
        assert endpoint.request_count == 1
        assert mint.calls == 1


class TestSecretSafety(RefreshingAuthFixtureMixin):
    async def _primed_auth(self) -> RefreshingBearerAuth:
        auth = self._auth(FakeTokenMint([self._token()]))
        endpoint = FakeMcpEndpoint(httpx.codes.OK)
        async with endpoint.client(auth) as client:
            await client.get(_URL)
        return auth

    async def test_repr_never_renders_the_bearer(self) -> None:
        auth = await self._primed_auth()
        rendered = repr(auth)
        assert _FIRST_SECRET not in rendered
        assert "cached" in rendered

    async def test_instance_state_never_renders_the_bearer(self) -> None:
        # vars()/structured dumps of a live auth are a real leak path — the
        # cached MintedToken must mask itself there too.
        auth = await self._primed_auth()
        assert _FIRST_SECRET not in str(vars(auth))

    async def test_the_plaintext_still_reaches_the_wire(self) -> None:
        # The mask is a rendering property, not a functional one: the header the
        # server receives carries the real bearer.
        mint = FakeTokenMint([self._token()])
        endpoint = FakeMcpEndpoint(httpx.codes.OK)
        async with endpoint.client(self._auth(mint)) as client:
            await client.get(_URL)
        assert endpoint.seen_bearers == [f"Bearer {_FIRST_SECRET}"]


class TestCredentialFailures(RefreshingAuthFixtureMixin):
    async def test_a_fetch_failure_becomes_a_typed_error_with_a_safe_message(
        self,
    ) -> None:
        mint = FakeTokenMint(error=RuntimeError(_INTERNAL_DETAIL))
        endpoint = FakeMcpEndpoint(httpx.codes.OK)
        async with endpoint.client(self._auth(mint)) as client:
            with pytest.raises(McpCredentialFetchError) as caught:
                await client.get(_URL)
        assert isinstance(caught.value, McpAuthError)
        assert str(caught.value) == (
            "Could not obtain credentials for this connector. Reconnect it and try again."
        )
        assert _INTERNAL_DETAIL not in str(caught.value)
        assert endpoint.request_count == 0

    async def test_an_already_typed_mcp_error_is_preserved(self) -> None:
        # A provider's own taxonomy must survive to the error-map stage rather
        # than being flattened into an auth failure.
        mint = FakeTokenMint(error=McpConnectionError("broker unreachable"))
        endpoint = FakeMcpEndpoint(httpx.codes.OK)
        async with endpoint.client(self._auth(mint)) as client:
            with pytest.raises(McpConnectionError) as caught:
                await client.get(_URL)
        assert not isinstance(caught.value, McpCredentialFetchError)

    async def test_a_non_token_result_is_refused(self) -> None:
        # The fetch parses a broker/backend response: an unvalidated shape must
        # not reach the header writer.
        mint = FakeTokenMint(result={"access_token": _FIRST_SECRET})
        endpoint = FakeMcpEndpoint(httpx.codes.OK)
        async with endpoint.client(self._auth(mint)) as client:
            with pytest.raises(McpCredentialFetchError):
                await client.get(_URL)
        assert endpoint.request_count == 0

    async def test_a_failed_mint_is_not_cached(self) -> None:
        mint = FakeTokenMint(error=RuntimeError(_INTERNAL_DETAIL))
        auth = self._auth(mint)
        endpoint = FakeMcpEndpoint(httpx.codes.OK)
        async with endpoint.client(auth) as client:
            for _ in range(2):
                with pytest.raises(McpCredentialFetchError):
                    await client.get(_URL)
        assert mint.calls == 2
        assert repr(auth).endswith("(token=empty)")

    def test_the_synchronous_flow_is_refused(self) -> None:
        auth = self._auth(FakeTokenMint([self._token()]))
        with pytest.raises(McpCredentialFlowError) as caught:
            auth.sync_auth_flow(httpx.Request("GET", _URL))
        assert isinstance(caught.value, McpClientError)
        assert str(caught.value) == (
            "This connector's credentials require an asynchronous client."
        )

    def test_a_negative_skew_is_refused_at_construction(self) -> None:
        with pytest.raises(McpCredentialConfigError) as caught:
            RefreshingBearerAuth(
                fetch=FakeTokenMint([self._token()]), skew=-timedelta(seconds=1)
            )
        assert str(caught.value) == "Connector credential refresh is misconfigured."
