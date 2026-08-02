"""Web / self-host credential plane for direct-connect MCP (migration P2-7b).

**Additive and unwired** — nothing in the running app constructs this yet
(P2-PLAN §3); P2-8 injects it behind the per-tool flag for every deployment that
is *not* ``ENTERPRISE_DEPLOYMENT_PROFILE=single_user_desktop``, where
:class:`~agent_runtime.capabilities.mcp.credentials.desktop.DesktopKeychainCredentialProvider`
answers instead.

The problem it solves is the mirror image of the desktop one. Under
direct-connect ai-backend opens the MCP server itself, so it needs the endpoint
**and** a bearer per server — but on a multi-tenant deployment the durable
credential lives in ``services/backend``'s token vault, encrypted at rest, behind
the tenant boundary, and that is exactly where it should stay. The product
decision (owner-approved) is that ai-backend stays credential-agnostic: it never
holds a refresh token, never decrypts a vault envelope, and never learns the
OAuth client secret.

So the backend grew one gated route —
``POST /internal/v1/mcp/servers/{server_id}/access-token`` — that runs the *same*
admission sequence as the JSON-RPC proxy (tenant scope → PRD-06 D3 access-mode
gate → liveness → refresh-on-expiry) and answers with ``{url, transport,
access_token, expires_at, scopes, access_mode}``. This module is its ai-backend
side.

Its gate is deliberately **stricter** than the proxy's, and a caller of this
module should know why rather than be surprised by a 403. The proxy sees the call
it authorizes, so a ``read`` connector still works there: reads pass and only a
non-read-only ``tools/call`` is refused. A minted bearer names no call and is
answerable to nothing once it leaves the backend, so ``read`` — the default for a
newly projected connector row — is refused outright and only ``read_act`` (or a
server no connector row joins) mints. The consequence for this lane is concrete:
direct-connect covers the connectors the user granted act on, and the rest stay
reachable through the proxy lane, which can police them per call.

What it guarantees:

* **One object satisfies both seams.** The P0
  :class:`~agent_runtime.capabilities.policy.contracts.CredentialProvider`
  (``auth_for``) and the P2-1
  :class:`~agent_runtime.capabilities.mcp.connection.McpConnectionDirectory`
  (``connection_for``) are answered from the *same* round-trip contract, because
  the mint returns the endpoint and the bearer together — the same shape the
  desktop provider has, so P2-3 calls one pair of methods regardless of profile.
* **The bearer rides the ``httpx.Auth`` only.** ``connection_for`` keeps the URL
  and the transport and drops the token on the floor; no header bag is carried
  over at all. A connection config that is logged, cached, or rendered is
  incapable of carrying the credential.
* **Mint on demand, never at construction.** ``auth_for`` builds a
  :class:`~agent_runtime.capabilities.mcp.credentials.refreshing_auth.RefreshingBearerAuth`
  over a fetch bound to one ``server_id``. The backend is called on first use and
  then only at ``expires_at − skew`` (or once on a 401), so an auth built for a
  connector the run never calls costs zero mints.
* **The lifetime is the backend's to state.** ``expires_at`` comes from the mint
  and is capped there; this side caches to it and no further. Re-minting is what
  re-runs the access-mode gate, so a connector turned ``off`` mid-run stops
  working within one token lifetime rather than at the end of the run.
* **No raw failure escapes.** Every mint outcome becomes a typed
  :class:`~agent_runtime.capabilities.mcp.client.McpClientError` with an authored
  safe message — never a backend URL, a service token, a response body, or a
  traceback.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Final

import httpx
from copilot_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)
from pydantic import Field, SecretStr

from agent_runtime.capabilities.http_pool import BackendHttpPool
from agent_runtime.capabilities.mcp.cards import McpTransport
from agent_runtime.capabilities.mcp.client import (
    McpAuthError,
    McpClientError,
    McpConnectionError,
    McpNotFoundError,
    McpRequestRejectedError,
)
from agent_runtime.capabilities.mcp.connection import (
    ConnectionContract,
    McpServerConnectionConfig,
    MintedToken,
)
from agent_runtime.capabilities.mcp.credentials.refreshing_auth import (
    RefreshingBearerAuth,
    UtcClock,
)
from agent_runtime.execution.contracts import ConnectorAccessMode

_LOGGER = logging.getLogger(__name__)


class BackendCredentialMessages:
    """Safe public copy for every backend-mint failure.

    Declared once, never inlined (``services/ai-backend/CLAUDE.md``). Each string
    is read by the model and paraphrased into user-facing copy, so — like
    ``Messages.Loader`` and the desktop provider's messages — each one states its
    own transience and the single action that resolves it. None of them names the
    backend host, the route, a tenant, a status body, or a secret.
    """

    NOT_CONNECTED: Final = (
        "This connector is not connected for this account. That is not temporary "
        "and a retry will not change it — ask the user to connect it in "
        "Settings, which signs in and stores the credential."
    )
    ACCESS_REFUSED: Final = (
        "Access to this connector was refused. The request was answered, so this "
        "is not an outage and not temporary: retrying will not change it. It "
        "usually means the connector is switched off for the agent, or is set to "
        "read-only and cannot be opened directly, or its sign-in has expired and "
        "needs to be renewed in Settings."
    )
    NO_BEARER: Final = (
        "This connector cannot be opened directly: it has no credential to "
        "issue, or it runs locally and has no address to connect to. That is a "
        "configuration fact, not an outage — a retry will not change it."
    )
    UNAVAILABLE: Final = (
        "The credential service could not be reached, so the connector could not "
        "be authenticated. This one is transient — trying again shortly may "
        "succeed."
    )
    REJECTED: Final = (
        "The request for this connector's credential was refused as invalid. The "
        "request was answered, so this is not an outage: retrying it unchanged "
        "will be refused again."
    )
    UNREADABLE: Final = (
        "The connector's credential could not be read. The request was answered, "
        "so this is not an outage: reconnecting the connector in Settings stores "
        "a fresh credential and resolves it."
    )


class BackendMcpConnectorNotFoundError(McpNotFoundError):
    """The mint has no such connector for this tenant (HTTP 404).

    Lands on the existing ``McpNotFoundError`` rung (⊂ ``McpRequestRejectedError``)
    so the P2-5 error map already classifies it as ``UNKNOWN_SERVER`` /
    ``CAPABILITY_NOT_FOUND``, non-retryable. Note the backend answers 404 for a
    server owned by *another* tenant as well — a mis-scoped caller must not be
    able to tell "not yours" from "not there", and neither must this class.
    """

    STATUS_CODE: Final = 404

    def __init__(self) -> None:
        """Raise with the fixed safe message (never the requested server id)."""

        super().__init__(
            BackendCredentialMessages.NOT_CONNECTED, status_code=self.STATUS_CODE
        )


class BackendMcpCredentialDeniedError(McpAuthError):
    """The mint refused to issue (HTTP 401 / 403).

    Covers every half of "answered, but no": the connector's access mode is
    ``off`` or ``read`` (the PRD-06 D3 gate, which is the authoritative
    permission boundary and lives on the backend by design) and the caller's own
    service identity being rejected. Deliberately one rung: from the run's point
    of view all of them are "answered, not retryable, resolved by a person",
    which is what ``McpAuthError`` means, and distinguishing them in
    model-visible copy would leak which one it was.
    """

    def __init__(self) -> None:
        """Raise with the fixed safe message (never the reason code or tenant)."""

        super().__init__(BackendCredentialMessages.ACCESS_REFUSED)


class BackendMcpNoBearerError(McpRequestRejectedError):
    """The server exists and is permitted, but admits no bearer (HTTP 409).

    The backend answers 409 for a connector that authenticates with nothing
    (``auth_mode=none``) or that is a local subprocess (stdio — its address is a
    command on the backend host, meaningless to this service). Kept distinct from
    the auth rung because it is not a refusal and reconnecting does not fix it:
    the connector's *shape* is the answer.
    """

    STATUS_CODE: Final = 409

    def __init__(self) -> None:
        """Raise with the fixed safe message (never the reason code)."""

        super().__init__(
            BackendCredentialMessages.NO_BEARER, status_code=self.STATUS_CODE
        )


class BackendMcpMintUnavailableError(McpConnectionError):
    """The mint could not be reached, or failed to answer usefully (5xx).

    An ``McpConnectionError``, so P2-5 marks it retryable — this is the one
    backend-mint failure where trying again can plausibly succeed. Minting is
    *not* a dispatch, so a server-side failure here is unambiguous: nothing could
    have reached the connector yet, and re-minting replays nothing.
    """

    def __init__(self) -> None:
        """Raise with the fixed safe message (never a URL, host, or token)."""

        super().__init__(BackendCredentialMessages.UNAVAILABLE)


class BackendMcpMintRejectedError(McpRequestRejectedError):
    """The mint refused the request itself (any other 4xx).

    Not a connection failure: the peer answered, so an identical retry is
    answered identically. This is the rung for our own malformed request — an
    internal defect the user cannot act on — which is why the copy does not ask
    them to do anything.
    """

    def __init__(self, status_code: int) -> None:
        """Record the rejecting status alongside the fixed safe message."""

        super().__init__(BackendCredentialMessages.REJECTED, status_code=status_code)


class BackendMcpMintUnreadableError(McpClientError):
    """The mint was answered but the answer was unusable.

    Covers a non-JSON body, a missing or extra field, an unparseable expiry, and
    an unknown transport. Deliberately the *base* ``McpClientError`` rung: P2-5's
    catch-all row describes it as a protocol error, non-retryable, which is
    exactly the honest answer for "recognised as ours, but not understood".
    """

    def __init__(self) -> None:
        """Raise with the fixed safe message (never the offending body)."""

        super().__init__(BackendCredentialMessages.UNREADABLE)


class BackendMintConfig(ConnectionContract):
    """Everything one provider needs to reach the mint, as one frozen value.

    A contract rather than four constructor arguments because it is a trust
    boundary: the tenant identity travels on it, and the service token is the
    thing that makes the backend believe that identity. Holding the token as a
    :class:`~pydantic.SecretStr` with ``repr`` suppressed means a config that
    reaches a log line, a traceback, or a ``model_dump_json`` shows the mask.
    """

    #: Origin of ``services/backend`` (``BACKEND_BASE_URL``). A trailing slash is
    #: tolerated; the route is joined against the stripped form.
    base_url: str = Field(min_length=1)
    #: The tenant this provider mints for. Supplied by the composing layer from
    #: the verified runtime context — never by anything the model can influence.
    org_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    #: The shared service token the backend's internal API requires. Masked in
    #: every rendering; unwrapped only when the request headers are built.
    service_token: SecretStr = Field(repr=False)
    #: Per-request deadline. A mint sits in front of the *first* call to a
    #: connector, so it is on the run's critical path and must fail fast.
    timeout_seconds: float = Field(default=10.0, gt=0)

    class Env:
        """Environment names read by :meth:`BackendScopedTokenCredentialProvider.from_env`.

        The same two the rest of the runtime's trusted-backend lane already reads
        (``runtime_api.app``, ``user_policies_resolver``); a third spelling of
        them here is precisely the drift that leaves a lane silently disabled.
        """

        BASE_URL: Final = "BACKEND_BASE_URL"
        SERVICE_TOKEN: Final = "ENTERPRISE_SERVICE_TOKEN"


class BackendMintedCredential(ConnectionContract):
    """The mint's response body, validated at the boundary.

    ``extra="forbid"`` is deliberate on a *response*: the producer is the
    backend's ``InternalMcpAccessToken`` in this same repository, which moves in
    lockstep with this file, so an unexpected field means the two have drifted —
    a thing to notice loudly, not to ignore quietly. It is also the property that
    makes "a refresh token cannot arrive here" structural rather than
    conventional. (This pair is not yet in
    ``copilot_service_contracts.mcp_cross_service_contract``'s golden fixture,
    which is where the drift check belongs once P2 wires the route; until then
    the strictness here is the check.)

    ``transport`` is validated straight into the domain ``StrEnum`` with no alias
    table, unlike the desktop broker's free-form echo: the producer is the
    backend's own ``McpTransport``, whose members are byte-identical to this
    boundary's. An unknown value therefore means drift, and drift must fail.
    """

    url: str = Field(min_length=1)
    transport: McpTransport
    #: The bearer plaintext. ``SecretStr`` masks it in every ``repr`` / ``str`` /
    #: JSON rendering; ``repr=False`` drops the field from the model ``repr`` so
    #: the mask does not even advertise it.
    access_token: SecretStr = Field(repr=False)
    expires_at: datetime
    scopes: tuple[str, ...] = ()
    #: The connector's durable access mode as the backend's gate evaluated it.
    #: Not a permission this side grants itself — the mint already refused every
    #: mode above it, so a credential that arrives here is ``read_act`` or
    #: ``None``. It is carried because the authority a credential was released
    #: under should travel with the credential: a holder that wants to be
    #: stricter than the backend (refusing to act on ``None``, say, where no
    #: connector row was configured and therefore nothing gated) needs the value
    #: to do it, and one that does not can no longer claim it was never told.
    #:
    #: Typed as the runtime's own mirror of the enum, so a value the backend
    #: invents fails validation here rather than being carried as an opaque
    #: string. ``None`` also covers a backend that predates the field: the two
    #: move in lockstep, but the tolerant read of "absent" and the honest read
    #: of "no connector row" are the same posture — nothing was configured.
    access_mode: ConnectorAccessMode | None = None


class BackendMintStatus:
    """Resolve a mint response status into the typed error it actually means.

    Mirrors the status-class reasoning of
    :class:`~agent_runtime.capabilities.mcp.backend_provider.McpProxyStatusClassifier`
    (a 4xx is an answer and is never transient; a 5xx is a failure to answer and
    is) but is not reused from it: that classifier cannot express this route's
    409 rung, and its messages describe the JSON-RPC proxy path rather than a
    credential mint. Same rule, different vocabulary — stated here rather than
    bent there.
    """

    _DENIED: Final[frozenset[int]] = frozenset(
        {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}
    )
    _NOT_FOUND: Final = httpx.codes.NOT_FOUND
    _NO_BEARER: Final = httpx.codes.CONFLICT
    _CLIENT_ERRORS: Final[range] = range(400, 500)

    @classmethod
    def error_for(cls, status_code: int) -> McpClientError | None:
        """Return the error ``status_code`` warrants, or ``None`` on success."""

        if httpx.codes.OK <= status_code < httpx.codes.MULTIPLE_CHOICES:
            return None
        if status_code in cls._DENIED:
            return BackendMcpCredentialDeniedError()
        if status_code == cls._NOT_FOUND:
            return BackendMcpConnectorNotFoundError()
        if status_code == cls._NO_BEARER:
            return BackendMcpNoBearerError()
        if status_code in cls._CLIENT_ERRORS:
            return BackendMcpMintRejectedError(status_code)
        return BackendMcpMintUnavailableError()


class BackendScopedTokenReader:
    """One connector's mint round-trip, bound to a single ``server_id``.

    Bound rather than parameterised for the same two reasons as its desktop
    sibling. It is the ``MintedTokenFetch`` handed to
    :class:`RefreshingBearerAuth` (the auth calls it with no arguments, so it
    cannot be pointed at a connector it was not built for), and it is where the
    untrusted response becomes domain values — one home for that conversion
    whether the caller wanted the endpoint, the token, or both.

    Every read is its own round-trip; nothing is cached here. Caching belongs to
    the auth, which already holds the token until ``expires_at − skew``. A second
    cache in front of it would keep a plaintext credential alive on a schedule
    nobody owns, and would also mean the access-mode gate stops being re-run.
    """

    #: The gated backend route. Declared once; ``server_id`` is the only variable.
    ROUTE: Final = "/internal/v1/mcp/servers/{server_id}/access-token"

    class Query:
        """Query keys the mint expects — declared once, never inlined."""

        ORG_ID: Final = "org_id"
        USER_ID: Final = "user_id"

    def __init__(
        self,
        *,
        config: BackendMintConfig,
        http_client: httpx.AsyncClient,
        server_id: str,
    ) -> None:
        """Bind the mint config, the HTTP client, and the connector identity."""

        self._config = config
        self._http_client = http_client
        self._server_id = server_id

    async def connection(self) -> McpServerConnectionConfig:
        """Return the endpoint half — URL and transport, and nothing else.

        The minted bearer in the same response is dropped here: it is not
        returned, not stored, not logged. No headers are carried over either —
        the backend keeps configured header values (which may themselves be
        secrets) on its own side of the line, so the honest answer for this
        lane is an empty bag rather than a filtered one.
        """

        minted = await self._mint()
        return McpServerConnectionConfig(url=minted.url, transport=minted.transport)

    async def __call__(self) -> MintedToken:
        """Return the credential half — the ``MintedTokenFetch`` the auth calls."""

        minted = await self._mint()
        return MintedToken(value=minted.access_token, expires_at=minted.expires_at)

    def __repr__(self) -> str:
        """Redacted rendering: the class only — never the tenant or the token."""

        return f"{type(self).__name__}()"

    async def _mint(self) -> BackendMintedCredential:
        """POST the mint, converting every outcome into the MCP taxonomy."""

        try:
            response = await self._http_client.post(
                f"{self._config.base_url.rstrip('/')}"
                f"{self.ROUTE.format(server_id=self._server_id)}",
                params={
                    self.Query.ORG_ID: self._config.org_id,
                    self.Query.USER_ID: self._config.user_id,
                },
                headers=self._headers(),
                timeout=self._config.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            # Includes timeouts. A mint is not a dispatch, so this is an
            # ordinary connection failure rather than an ambiguous one: nothing
            # could have reached the connector, and re-minting replays nothing.
            raise BackendMcpMintUnavailableError() from exc
        status_error = BackendMintStatus.error_for(response.status_code)
        if status_error is not None:
            raise status_error
        return self._parse(response)

    def _headers(self) -> dict[str, str]:
        """Build the service-auth headers — the only place the token is unwrapped.

        All three are required together: the backend's internal API verifies the
        service token and then reads the tenant from the headers, ignoring the
        query string. Sending the identity only in the query would work in a dev
        deployment with no token configured and fail closed in every real one.
        """

        return {
            SERVICE_TOKEN_HEADER: self._config.service_token.get_secret_value(),
            ORG_HEADER: self._config.org_id,
            USER_HEADER: self._config.user_id,
        }

    @staticmethod
    def _parse(response: httpx.Response) -> BackendMintedCredential:
        """Validate the untrusted body into the domain contract.

        ``from None`` on both branches: a JSON error echoes the body and a
        pydantic error echoes the offending value — which on this route is the
        credential. Neither belongs in a chained traceback that may be logged
        next to the same request. Only the error class is recorded.
        """

        try:
            body = response.json()
        except ValueError:
            _LOGGER.debug("mcp access-token mint returned a non-JSON body")
            raise BackendMcpMintUnreadableError() from None
        try:
            return BackendMintedCredential.model_validate(body)
        except Exception as exc:  # noqa: BLE001 - narrowed into a typed error.
            _LOGGER.debug(
                "mcp access-token mint response was unusable: error_class=%s",
                type(exc).__name__,
            )
            raise BackendMcpMintUnreadableError() from None


class BackendScopedTokenCredentialProvider:
    """The web / self-host credential plane: ``CredentialProvider`` + ``McpConnectionDirectory``.

    One object satisfies both P0/P2-1 seams because one gated backend route
    answers both halves. P2-3 asks the directory for ``(url, transport)`` at
    connection-build time and the provider for the ``httpx.Auth`` that
    authenticates every later request; P2-8 injects this instance for every
    deployment profile that is not the single-user desktop.

    The split of work between the two calls is the security-relevant part:
    ``connection_for`` performs a mint and keeps only the endpoint (the bearer in
    that response is never returned, stored, or logged), while ``auth_for``
    performs **no** call at all — it hands back an auth that mints on first use
    and then only when the token is about to expire.

    Tenant isolation and encryption at rest are not implemented here and are not
    meant to be: they stay inside ``services/backend``, on the authoritative side
    of the trust line. This side holds a short-lived bearer and nothing else.
    """

    def __init__(
        self,
        *,
        config: BackendMintConfig,
        http_client: httpx.AsyncClient | None = None,
        skew: timedelta | None = None,
        clock: UtcClock | None = None,
    ) -> None:
        """Bind the mint config and the auth's refresh window / clock.

        ``http_client`` defaults to the process-shared :class:`BackendHttpPool`
        so mints reuse the same TLS connection as every other backend call;
        tests inject a client over ``httpx.MockTransport`` through this argument.
        """

        self._config = config
        self._http_client = (
            BackendHttpPool.get() if http_client is None else http_client
        )
        self._skew = skew
        self._clock: UtcClock = self._utc_now if clock is None else clock

    @classmethod
    def from_env(
        cls,
        *,
        org_id: str,
        user_id: str,
        env: Mapping[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
        skew: timedelta | None = None,
        clock: UtcClock | None = None,
    ) -> BackendScopedTokenCredentialProvider | None:
        """Build the provider from the trusted-backend lane's env, or ``None``.

        ``None`` means "the trusted backend lane is not configured" — the same
        both-or-neither gate ``runtime_api.app`` applies before composing its
        backend-backed services — so a deployment missing either half selects a
        different provider at wiring time rather than failing at the first tool
        call. The tenant is passed in rather than read from the environment: it
        belongs to the run, not to the process.
        """

        source: Mapping[str, str] = os.environ if env is None else env
        base_url = source.get(BackendMintConfig.Env.BASE_URL, "").strip()
        service_token = source.get(BackendMintConfig.Env.SERVICE_TOKEN, "").strip()
        if not base_url or not service_token:
            _LOGGER.info(
                "mcp_credentials.backend_mint_config_absent url=%s token=%s",
                bool(base_url),
                bool(service_token),
            )
            return None
        return cls(
            config=BackendMintConfig(
                base_url=base_url,
                org_id=org_id,
                user_id=user_id,
                service_token=SecretStr(service_token),
            ),
            http_client=http_client,
            skew=skew,
            clock=clock,
        )

    async def auth_for(self, server_id: str) -> httpx.Auth:
        """Return the rotating bearer for ``server_id`` — no mint happens here.

        The returned auth mints on first use and caches to ``expires_at − skew``,
        so one long-lived MCP client survives token rotation without the MCP
        subsystem knowing that tokens expire (P2-2) — and every rotation is
        another pass through the backend's access-mode gate.
        """

        return RefreshingBearerAuth(
            fetch=self._reader(server_id), skew=self._skew, clock=self._clock
        )

    async def connection_for(self, server_id: str) -> McpServerConnectionConfig:
        """Return the endpoint config for ``server_id`` — never the bearer."""

        return await self._reader(server_id).connection()

    def __repr__(self) -> str:
        """Redacted rendering: the class only — the config holds a secret."""

        return f"{type(self).__name__}()"

    def _reader(self, server_id: str) -> BackendScopedTokenReader:
        """Bind one mint round-trip to one connector identity."""

        return BackendScopedTokenReader(
            config=self._config,
            http_client=self._http_client,
            server_id=server_id,
        )

    @staticmethod
    def _utc_now() -> datetime:
        """The default clock — aware UTC now (kept in-class, not module-level)."""

        return datetime.now(UTC)


__all__ = [
    "BackendCredentialMessages",
    "BackendMcpConnectorNotFoundError",
    "BackendMcpCredentialDeniedError",
    "BackendMcpMintRejectedError",
    "BackendMcpMintUnavailableError",
    "BackendMcpMintUnreadableError",
    "BackendMcpNoBearerError",
    "BackendMintConfig",
    "BackendMintStatus",
    "BackendMintedCredential",
    "BackendScopedTokenCredentialProvider",
    "BackendScopedTokenReader",
]
