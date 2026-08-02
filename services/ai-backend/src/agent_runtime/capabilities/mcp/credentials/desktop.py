"""Desktop credential plane for direct-connect MCP (migration P2-7a).

**Additive and unwired** — nothing in the running app constructs this yet
(P2-PLAN §3, P2-7a); P2-8 injects it behind the per-tool flag, selected by
``ENTERPRISE_DEPLOYMENT_PROFILE=single_user_desktop``.

The problem it solves (P2-PLAN §1): under direct-connect, ai-backend opens the
MCP server itself, so it needs the endpoint **and** a bearer per server. On
desktop both live in Electron ``safeStorage`` behind ``SecretStorage``'s
**active-workspace gate**, in the main process, which a Python child cannot read.
The seam that already crosses that boundary is the authenticated loopback
**capability broker** the host-filesystem capability uses
(``apps/desktop/main/capabilities/broker.ts``; 127.0.0.1, per-boot 256-bit
bearer). P2-7a adds one route to it — ``POST /mcp/secret`` →
``SecretStorage.get(activeWorkspace, "mcp", server_id)`` → ``{url, transport,
token, expires_at}`` — and this module is its ai-backend side. It reuses
:class:`~agent_runtime.capabilities.desktop.broker_client.DesktopBrokerClient`
rather than opening a second HTTP path, so there is one authenticated channel to
the broker, with one header contract and one error taxonomy.

What this module guarantees:

* **One object satisfies both seams.** The P0 ``CredentialProvider``
  (``auth_for``) and the P2-1 :class:`McpConnectionDirectory` (``connection_for``)
  are answered from the *same* broker round-trip contract, because on desktop the
  endpoint and the token come out of one keychain record. P2-3 calls both.
* **The bearer rides the ``httpx.Auth`` only.** ``connection_for`` discards the
  token, and any authorization-shaped header the broker sends is stripped before
  it can reach :class:`McpServerConnectionConfig` — so the secret has exactly one
  route to the wire (``RefreshingBearerAuth``), and a connection config that gets
  logged, cached, or rendered cannot leak it.
* **Mint on demand, never at construction.** ``auth_for`` builds a
  :class:`~agent_runtime.capabilities.mcp.credentials.refreshing_auth.RefreshingBearerAuth`
  over a fetch bound to one ``server_id``; the broker is read on first use and
  then only at ``expires_at − skew`` (or on a single 401). Building an auth for a
  connector the run never calls costs zero keychain reads.
* **Isolation is preserved end-to-end.** The read happens in the main process
  under the active-workspace gate, so a compromised run cannot pull another
  workspace's credential; a refusal comes back as a typed, safe domain error.
* **No raw failure escapes.** Every broker outcome is converted into the existing
  :class:`~agent_runtime.capabilities.mcp.client.McpClientError` taxonomy with an
  authored safe message — never a broker internal, a host path, the loopback
  bearer, the connector token, or a traceback.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol

import httpx

from agent_runtime.capabilities.desktop.broker_client import (
    BrokerError,
    BrokerGrantRequiredError,
    BrokerNotFoundError,
    BrokerPermissionDeniedError,
    BrokerUnavailableError,
    McpSecretResult,
)
from agent_runtime.capabilities.mcp.cards import McpTransport
from agent_runtime.capabilities.mcp.client import (
    McpAuthError,
    McpClientError,
    McpConnectionError,
    McpNotFoundError,
)
from agent_runtime.capabilities.mcp.connection import (
    McpServerConnectionConfig,
    MintedToken,
)
from agent_runtime.capabilities.mcp.credentials.refreshing_auth import (
    RefreshingBearerAuth,
    UtcClock,
)

_LOGGER = logging.getLogger(__name__)


class DesktopCredentialMessages:
    """Safe public copy for every desktop credential failure.

    Declared once, never inlined (``services/ai-backend/CLAUDE.md``). Each string
    is read by the model and paraphrased into user-facing copy, so — like
    ``Messages.Loader`` — each one states its own transience and the single action
    that resolves it. None of them names the broker, a host path, a workspace, a
    port, or a secret.
    """

    NOT_CONNECTED: Final = (
        "This connector is not connected on this device. That is not temporary "
        "and a retry will not change it — ask the user to connect it in "
        "Settings, which signs in and stores the credential."
    )
    ACCESS_REFUSED: Final = (
        "This device refused access to the connector's credential. The request "
        "was answered, so this is not an outage and not temporary: retrying will "
        "not change it. It usually means the connector belongs to a different "
        "workspace than the one this chat is running in."
    )
    UNAVAILABLE: Final = (
        "The credential service on this device could not be reached, so the "
        "connector could not be authenticated. This one is transient — trying "
        "again shortly may succeed."
    )
    UNREADABLE: Final = (
        "The connector's stored credential could not be read on this device. "
        "The request was answered, so this is not an outage: reconnecting the "
        "connector in Settings stores a fresh credential and resolves it."
    )
    UNSUPPORTED_TRANSPORT: Final = (
        "This connector is configured with a transport this app cannot open. "
        "That is a configuration problem, not an outage: a retry will not "
        "change it."
    )


class DesktopMcpConnectorNotFoundError(McpNotFoundError):
    """No credential is stored for this connector on this device.

    Lands on the existing ``McpNotFoundError`` rung (⊂ ``McpRequestRejectedError``)
    so the P2-5 error map already classifies it as ``UNKNOWN_SERVER`` /
    ``CAPABILITY_NOT_FOUND``, non-retryable — no new taxonomy branch, no new
    behaviour to keep in sync. ``status_code`` records the answer the broker gave
    without carrying its body.
    """

    STATUS_CODE: Final = 404

    def __init__(self) -> None:
        """Raise with the fixed safe message (never the requested server id)."""

        super().__init__(
            DesktopCredentialMessages.NOT_CONNECTED, status_code=self.STATUS_CODE
        )


class DesktopMcpCredentialDeniedError(McpAuthError):
    """The device refused the credential read — the active-workspace gate held.

    An ``McpAuthError``: like an expired or rejected bearer, it is answered,
    never retryable in place, and resolved by user action. P2-5 maps it to
    AUTH_FAILURE, non-retryable.
    """

    def __init__(self) -> None:
        """Raise with the fixed safe message (never the workspace or server id)."""

        super().__init__(DesktopCredentialMessages.ACCESS_REFUSED)


class DesktopMcpBrokerUnavailableError(McpConnectionError):
    """The loopback credential service could not be reached (connect/timeout).

    An ``McpConnectionError``, so P2-5 marks it retryable — this is the one
    desktop credential failure where trying again can plausibly succeed.
    """

    def __init__(self) -> None:
        """Raise with the fixed safe message (never a URL, port, or token)."""

        super().__init__(DesktopCredentialMessages.UNAVAILABLE)


class DesktopMcpSecretUnreadableError(McpClientError):
    """The credential read was answered but the answer was unusable.

    Covers a malformed body, a missing field, an unparseable expiry, and every
    broker error code that is a wiring/protocol failure rather than a recoverable
    state. Deliberately the *base* ``McpClientError`` rung: P2-5's catch-all row
    describes it as a protocol error, non-retryable, which is exactly the honest
    answer for "recognised as ours, but not understood".
    """

    def __init__(self, message: str | None = None) -> None:
        """Raise with an authored safe message (defaults to the unreadable copy)."""

        super().__init__(message or DesktopCredentialMessages.UNREADABLE)


class DesktopMcpSecretBroker(Protocol):
    """The one broker capability this module needs (dependency inversion).

    Structurally satisfied by
    :class:`~agent_runtime.capabilities.desktop.broker_client.DesktopBrokerClient`
    — the client the host-filesystem capability already uses — and by a test
    fake. Declaring the narrow surface rather than depending on the concrete
    client keeps this module testable without a loopback server and keeps the
    broker free to grow routes this provider must not reach.
    """

    async def mcp_secret(self, server_id: str) -> McpSecretResult: ...


class DesktopSecretTransport:
    """Resolve the broker's transport string into the domain ``StrEnum``.

    The broker echoes whatever the connector was registered with, so this is an
    untrusted string, not a domain value. Mapped explicitly — never coerced —
    with one alias admitted: ``streamable_http`` is what the MCP SDK and
    ``langchain-mcp-adapters`` call the transport this boundary spells ``http``,
    and a connector registered through either vocabulary must resolve to the same
    rung rather than failing on spelling.

    ``stdio`` resolves normally and is *not* refused here: reporting what the
    device actually holds is this layer's job, and P2-3's ``McpConnectionBuilder``
    owns the decision that a local command spec cannot yet be opened (P2-PLAN
    §6.4). Refusing in two places would mean two answers to one question.
    """

    _ALIASES: Final[Mapping[str, McpTransport]] = {
        "streamable_http": McpTransport.HTTP,
        "streamable-http": McpTransport.HTTP,
        "http_stream": McpTransport.HTTP,
    }

    @classmethod
    def resolve(cls, raw: str) -> McpTransport:
        """Return the ``McpTransport`` for ``raw``, or raise a typed error."""

        normalized = raw.strip().lower()
        alias = cls._ALIASES.get(normalized)
        if alias is not None:
            return alias
        try:
            return McpTransport(normalized)
        except ValueError:
            # `from None`: the offending value is connector config, but nothing
            # is gained by chaining a ValueError whose text repeats it into a
            # traceback that may be logged next to the same record's token.
            raise DesktopMcpSecretUnreadableError(
                DesktopCredentialMessages.UNSUPPORTED_TRANSPORT
            ) from None


class DesktopSecretExpiry:
    """Normalize the broker's ``expires_at`` into an aware UTC ``datetime``.

    The wire type is deliberately loose (``str | int | float | None``) and the
    rule lives here, in one place, because both ways of guessing wrong are live
    failures: read milliseconds as seconds and the token is cached for centuries
    past its death (every call 401s, then re-mints); read seconds as
    milliseconds and it is born expired (a re-mint on every single request, i.e.
    a keychain-read storm).

    The rule, stated: an ISO-8601 string is parsed as such (a bare ``Z`` accepted,
    a naive value read as UTC); a number is epoch, in **milliseconds** at or above
    :attr:`MILLISECONDS_FLOOR` and in **seconds** below it. That threshold is
    1970-01-01 in milliseconds and year 5138 in seconds, so no real token expiry
    is ambiguous.

    A missing expiry is not an error — a non-expiring stored credential is a
    legitimate shape — but it is not trusted either: it yields a short
    :attr:`UNKNOWN_TTL`, so the auth re-reads soon over a loopback socket rather
    than caching an unknown lifetime forever.
    """

    #: Below this, a number is epoch seconds; at or above it, milliseconds.
    MILLISECONDS_FLOOR: Final = 100_000_000_000
    #: Assumed lifetime when the broker states none. Short on purpose: the
    #: re-read is a loopback round-trip, and being wrong the other way means
    #: authenticating with a bearer nothing has vouched for.
    UNKNOWN_TTL: Final = timedelta(minutes=5)
    _UTC_SUFFIX: Final = "Z"
    _UTC_OFFSET: Final = "+00:00"

    @classmethod
    def resolve(cls, raw: str | int | float | None, *, now: datetime) -> datetime:
        """Return the aware UTC expiry for ``raw``, or raise a typed error."""

        if raw is None:
            return now + cls.UNKNOWN_TTL
        # `bool` is an `int` subclass: `expires_at: true` must be a defect, not
        # 1970-01-01T00:00:01Z.
        if isinstance(raw, bool):
            raise DesktopMcpSecretUnreadableError()
        if isinstance(raw, (int, float)):
            return cls._from_epoch(float(raw))
        return cls._from_text(raw)

    @classmethod
    def _from_epoch(cls, value: float) -> datetime:
        """Read a number as epoch seconds or milliseconds, by magnitude."""

        seconds = value / 1000 if abs(value) >= cls.MILLISECONDS_FLOOR else value
        try:
            return datetime.fromtimestamp(seconds, UTC)
        except (OverflowError, OSError, ValueError):
            raise DesktopMcpSecretUnreadableError() from None

    @classmethod
    def _from_text(cls, value: str) -> datetime:
        """Read a string as ISO-8601, falling back to a numeric epoch."""

        text = value.strip()
        if text.endswith(cls._UTC_SUFFIX):
            text = f"{text[:-1]}{cls._UTC_OFFSET}"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            try:
                return cls._from_epoch(float(text))
            except ValueError:
                raise DesktopMcpSecretUnreadableError() from None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class DesktopMcpSecretReader:
    """One connector's credential read, bound to a single ``server_id``.

    Bound rather than parameterised for two reasons. It is the
    ``MintedTokenFetch`` handed to :class:`RefreshingBearerAuth` (the auth calls
    it with no arguments, so it cannot be pointed at a connector it was not built
    for), and it is where the untrusted broker response becomes domain values —
    so the conversion has exactly one home whether the caller wanted the
    endpoint, the token, or both.

    Every read is its own round-trip: nothing is cached here. Caching belongs to
    the auth, which already holds the token until ``expires_at − skew``; a second
    cache in front of it would keep a plaintext credential alive on a schedule
    nobody owns, for the sake of a loopback request.
    """

    #: Headers that must never survive from the broker into a connection config.
    #: The bearer has exactly one route to the wire — ``RefreshingBearerAuth`` —
    #: and a config that gets logged or cached must be incapable of carrying it.
    _SECRET_HEADERS: Final[frozenset[str]] = frozenset(
        {"authorization", "proxy-authorization", "cookie"}
    )

    def __init__(
        self,
        *,
        broker: DesktopMcpSecretBroker,
        server_id: str,
        clock: UtcClock,
    ) -> None:
        """Bind the broker capability, the connector identity, and the clock."""

        self._broker = broker
        self._server_id = server_id
        self._clock = clock

    async def connection(self) -> McpServerConnectionConfig:
        """Return the endpoint half — URL, transport, non-secret headers only."""

        secret = await self._read()
        return McpServerConnectionConfig(
            url=secret.url,
            transport=DesktopSecretTransport.resolve(secret.transport),
            headers=self._public_headers(secret.headers),
        )

    async def __call__(self) -> MintedToken:
        """Return the credential half — the ``MintedTokenFetch`` the auth calls."""

        secret = await self._read()
        return MintedToken(
            value=secret.token,
            expires_at=DesktopSecretExpiry.resolve(
                secret.expires_at, now=self._clock()
            ),
        )

    async def _read(self) -> McpSecretResult:
        """Run the broker read, converting every failure into the MCP taxonomy."""

        try:
            return await self._broker.mcp_secret(self._server_id)
        except BrokerNotFoundError as exc:
            raise DesktopMcpConnectorNotFoundError() from exc
        except (BrokerPermissionDeniedError, BrokerGrantRequiredError) as exc:
            raise DesktopMcpCredentialDeniedError() from exc
        except BrokerUnavailableError as exc:
            raise DesktopMcpBrokerUnavailableError() from exc
        except BrokerError as exc:
            # Every remaining broker code is a protocol/config failure. Its
            # message is safe by the client's own construction, but it is still
            # an internal detail, so only the class name is recorded.
            _LOGGER.debug(
                "mcp credential read failed: error_class=%s", type(exc).__name__
            )
            raise DesktopMcpSecretUnreadableError() from exc
        except Exception as exc:  # noqa: BLE001 - narrowed into a typed error.
            # A validation failure on the response lands here, and a pydantic
            # error can echo the offending value — which on this route includes
            # the token. `from None` keeps that out of the chained traceback, and
            # only the class name is logged.
            _LOGGER.debug(
                "mcp credential response was unusable: error_class=%s",
                type(exc).__name__,
            )
            raise DesktopMcpSecretUnreadableError() from None

    @classmethod
    def _public_headers(cls, headers: Mapping[str, str]) -> dict[str, str]:
        """Drop every authorization-shaped header before it reaches the config."""

        return {
            key: value
            for key, value in headers.items()
            if key.strip().lower() not in cls._SECRET_HEADERS
        }


class DesktopKeychainCredentialProvider:
    """The desktop credential plane: ``CredentialProvider`` + ``McpConnectionDirectory``.

    One object satisfies both P0/P2-1 seams because on desktop both halves come
    out of one keychain record, read over one broker route. P2-3 asks the
    directory for ``(url, transport, headers)`` at connection-build time and the
    provider for the ``httpx.Auth`` that authenticates every later request; P2-8
    injects this instance for
    ``ENTERPRISE_DEPLOYMENT_PROFILE=single_user_desktop``.

    The split of work between the two calls is the security-relevant part:
    ``connection_for`` performs a read and keeps only the endpoint (the token in
    that response is never returned, never stored, never logged), while
    ``auth_for`` performs **no** read at all — it hands back an auth that will
    read on first use and then only when the token is about to expire.
    """

    def __init__(
        self,
        *,
        broker: DesktopMcpSecretBroker,
        skew: timedelta | None = None,
        clock: UtcClock | None = None,
    ) -> None:
        """Bind the broker capability and the auth's refresh window / clock."""

        self._broker = broker
        self._skew = skew
        self._clock: UtcClock = self._utc_now if clock is None else clock

    @classmethod
    def from_env(
        cls,
        *,
        env: Mapping[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
        skew: timedelta | None = None,
        clock: UtcClock | None = None,
    ) -> DesktopKeychainCredentialProvider | None:
        """Build the provider from the supervisor's broker env, or ``None``.

        ``None`` means "this is not a desktop image, or the capability broker is
        not configured" — the same gate ``WorkspaceBackendWorkerWiring`` applies
        before composing the ``/workspace/`` route — so a non-desktop deployment
        selects a different provider rather than failing at first tool call. The
        env names are read through ``WorkspaceBackendConfig.from_env`` on purpose:
        a second copy of them is exactly the drift that once left the desktop's
        host reads silently disabled for a whole release.
        """

        # Lazy imports: the desktop capability package must not load on the
        # web / postgres / in-memory images.
        from agent_runtime.capabilities.desktop.broker_client import (  # noqa: PLC0415
            BrokerClientConfig,
            DesktopBrokerClient,
        )
        from agent_runtime.capabilities.desktop.workspace_backend import (  # noqa: PLC0415
            WorkspaceBackendConfig,
        )

        config = WorkspaceBackendConfig.from_env(env=env)
        if not config.broker_base_url or not config.broker_token:
            _LOGGER.info(
                "mcp_credentials.broker_config_absent url=%s token=%s",
                bool(config.broker_base_url),
                bool(config.broker_token),
            )
            return None
        broker = DesktopBrokerClient(
            BrokerClientConfig(
                base_url=config.broker_base_url,
                token=config.broker_token,
                service_identity=config.service_identity,
                broker_audience=config.broker_audience,
                protocol_version=config.protocol_version,
                timeout_seconds=config.timeout_seconds,
            ),
            http_client=http_client,
        )
        return cls(broker=broker, skew=skew, clock=clock)

    async def auth_for(self, server_id: str) -> httpx.Auth:
        """Return the rotating bearer for ``server_id`` — no read happens here.

        The returned auth mints on first use and caches to ``expires_at − skew``,
        so one long-lived MCP client survives token rotation without the MCP
        subsystem knowing that tokens expire (P2-2).
        """

        return RefreshingBearerAuth(
            fetch=self._reader(server_id), skew=self._skew, clock=self._clock
        )

    async def connection_for(self, server_id: str) -> McpServerConnectionConfig:
        """Return the endpoint config for ``server_id`` — never the bearer."""

        return await self._reader(server_id).connection()

    def _reader(self, server_id: str) -> DesktopMcpSecretReader:
        """Bind one credential read to one connector identity."""

        return DesktopMcpSecretReader(
            broker=self._broker, server_id=server_id, clock=self._clock
        )

    @staticmethod
    def _utc_now() -> datetime:
        """The default clock — aware UTC now (kept in-class, not module-level)."""

        return datetime.now(UTC)


__all__ = [
    "DesktopCredentialMessages",
    "DesktopKeychainCredentialProvider",
    "DesktopMcpBrokerUnavailableError",
    "DesktopMcpConnectorNotFoundError",
    "DesktopMcpCredentialDeniedError",
    "DesktopMcpSecretBroker",
    "DesktopMcpSecretReader",
    "DesktopMcpSecretUnreadableError",
    "DesktopSecretExpiry",
    "DesktopSecretTransport",
]
