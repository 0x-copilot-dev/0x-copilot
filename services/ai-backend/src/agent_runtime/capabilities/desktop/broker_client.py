"""Async HTTP client for the desktop Electron capability broker's ``/v1/fs/*`` routes.

AC5 slice 3a — the ai-backend side of user-granted host-folder READ access.

The Electron main process runs an authenticated, loopback-only capability
broker (``apps/desktop/main/capabilities/broker.ts``). It exposes the
filesystem READ operations (``stat``/``list``/``read``/``glob``/``grep``) at
``/v1/fs/{stat,list,read,glob,grep}``. Every request must be a JSON ``POST``
carrying:

* ``Authorization: Bearer <token>`` — a per-boot 256-bit secret delivered to
  this process **out of band** (env var), never over renderer IPC.
* ``X-Capability-Protocol: <version>`` — the wire protocol version (``"1"``).

Every request body names a ``grant_id`` plus a **virtual** path (or pattern)
that is *always* interpreted relative to the grant's host root — the broker
never accepts and this client never sends a host-absolute path. Responses are
path-free / root-relative by construction, so nothing here can become a
host-path oracle.

Security posture of THIS client:

* The bearer token and any path are treated as sensitive: they are **never**
  logged. The only diagnostic we emit is ``route`` + HTTP status + machine
  error ``code`` (all non-sensitive).
* Broker error codes are mapped to typed :class:`BrokerError` subclasses with
  generic, safe messages — no broker internals, host paths, or stack detail
  ever reach the caller (and thus never reach model output).
* Transport failures (connect/timeout) collapse to
  :class:`BrokerUnavailableError`; malformed / unexpected responses collapse to
  :class:`BrokerProtocolError`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.capabilities.http_pool import BackendHttpPool

logger = logging.getLogger(__name__)


class Routes:
    """Broker READ route paths (mirrors ``broker.ts`` ``ROUTES``)."""

    STAT: Final = "/v1/fs/stat"
    LIST: Final = "/v1/fs/list"
    READ: Final = "/v1/fs/read"
    GLOB: Final = "/v1/fs/glob"
    GREP: Final = "/v1/fs/grep"
    #: Grant-management read — the CURRENT active grant snapshot (path-free).
    GRANTS_SNAPSHOT: Final = "/v1/grants/snapshot"


class Header:
    """Request header names the broker requires on every call."""

    AUTHORIZATION: Final = "Authorization"
    PROTOCOL: Final = "X-Capability-Protocol"
    CONTENT_TYPE: Final = "content-type"


class Field_:  # noqa: N801 — a small constants namespace, not a runtime type
    """Request/response JSON field names shared with the broker wire contract."""

    GRANT_ID: Final = "grant_id"
    PATH: Final = "path"
    OFFSET: Final = "offset"
    MAX_BYTES: Final = "max_bytes"
    PATTERN: Final = "pattern"
    MAX_RESULTS: Final = "max_results"
    PATH_GLOB: Final = "path_glob"
    IS_REGEX: Final = "is_regex"
    FLAGS: Final = "flags"
    MAX_MATCHES: Final = "max_matches"
    ERROR: Final = "error"


class ErrorCode:
    """Machine error codes the broker returns in ``{"error": <code>}`` bodies.

    The first group are per-op filesystem conditions the model can reason about
    (they map to dedicated :class:`BrokerError` subclasses). The second group
    are envelope / protocol / config failures that indicate a client or wiring
    bug rather than a recoverable filesystem state; they all collapse to
    :class:`BrokerProtocolError`.
    """

    # Per-op filesystem conditions (see path-validation.ts ``FsErrorCode``).
    INVALID_PATH: Final = "invalid_path"
    INVALID_REQUEST: Final = "invalid_request"
    NOT_A_DIRECTORY: Final = "not_a_directory"
    NOT_A_FILE: Final = "not_a_file"
    GRANT_REQUIRED: Final = "grant_required"
    PERMISSION_DENIED: Final = "permission_denied"
    NOT_FOUND: Final = "not_found"
    UNSUPPORTED: Final = "unsupported"
    TOO_LARGE: Final = "too_large"

    # Envelope / protocol / config failures (broker.ts ``#handle``).
    UNAUTHORIZED: Final = "unauthorized"
    FORBIDDEN: Final = "forbidden"
    UNSUPPORTED_PROTOCOL_VERSION: Final = "unsupported_protocol_version"
    PAYLOAD_TOO_LARGE: Final = "payload_too_large"
    INVALID_JSON: Final = "invalid_json"
    METHOD_NOT_ALLOWED: Final = "method_not_allowed"
    INTERNAL: Final = "internal"


# --- typed exceptions --------------------------------------------------------


class BrokerError(Exception):
    """Base for every capability-broker failure.

    ``code`` is a stable machine code; ``message`` is always safe to surface —
    it never contains a host path, a token, or broker internals.
    """

    code: str = "broker_error"

    def __init__(self, message: str | None = None) -> None:
        """Store a safe, generic message (defaults to the class ``code``)."""
        super().__init__(message or self.code)


class BrokerUnavailableError(BrokerError):
    """The broker could not be reached (connect refused, timeout, transport)."""

    code = "broker_unavailable"


class BrokerProtocolError(BrokerError):
    """The broker replied in an unexpected way (bad status, malformed JSON, auth/protocol reject, oversized body)."""

    code = "broker_protocol_error"


class BrokerGrantRequiredError(BrokerError):
    """No active grant for the supplied ``grant_id`` (unknown or revoked)."""

    code = ErrorCode.GRANT_REQUIRED


class BrokerInvalidPathError(BrokerError):
    """The virtual path was syntactically rejected (traversal, reserved, encoding)."""

    code = ErrorCode.INVALID_PATH


class BrokerInvalidRequestError(BrokerError):
    """Malformed op params (bad pattern, bad range, missing field)."""

    code = ErrorCode.INVALID_REQUEST


class BrokerPermissionDeniedError(BrokerError):
    """Resolved outside the root, symlink/TOCTOU escape, or insufficient grant mode."""

    code = ErrorCode.PERMISSION_DENIED


class BrokerNotFoundError(BrokerError):
    """The path does not exist under the grant root."""

    code = ErrorCode.NOT_FOUND


class BrokerNotADirectoryError(BrokerError):
    """A list/glob/grep target is not a directory."""

    code = ErrorCode.NOT_A_DIRECTORY


class BrokerNotAFileError(BrokerError):
    """A read target is not a regular file."""

    code = ErrorCode.NOT_A_FILE


class BrokerTooLargeError(BrokerError):
    """A read target exceeds the broker's hard byte ceiling."""

    code = ErrorCode.TOO_LARGE


class BrokerUnsupportedError(BrokerError):
    """The op is not enabled on the broker (e.g. no host-fs executor wired)."""

    code = ErrorCode.UNSUPPORTED


# Per-op filesystem codes → dedicated exception. Any code NOT in this map
# (envelope/protocol/config codes, or an unknown code) is a protocol error.
_CODE_TO_EXCEPTION: Final[Mapping[str, type[BrokerError]]] = {
    ErrorCode.GRANT_REQUIRED: BrokerGrantRequiredError,
    ErrorCode.INVALID_PATH: BrokerInvalidPathError,
    ErrorCode.INVALID_REQUEST: BrokerInvalidRequestError,
    ErrorCode.PERMISSION_DENIED: BrokerPermissionDeniedError,
    ErrorCode.NOT_FOUND: BrokerNotFoundError,
    ErrorCode.NOT_A_DIRECTORY: BrokerNotADirectoryError,
    ErrorCode.NOT_A_FILE: BrokerNotAFileError,
    ErrorCode.TOO_LARGE: BrokerTooLargeError,
    ErrorCode.UNSUPPORTED: BrokerUnsupportedError,
}


# --- typed result models (parsed from untrusted broker JSON) -----------------


class _BrokerModel(BaseModel):
    """Base config for broker response models: accept camelCase, ignore extras, freeze."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore", frozen=True)


class FsStatResult(_BrokerModel):
    """``/v1/fs/stat`` — leaf metadata (never a host path)."""

    type: Literal["file", "dir"]
    size: int
    mtime_ms: float = Field(alias="mtimeMs")
    name: str


class FsDirEntry(_BrokerModel):
    """One child from ``/v1/fs/list`` (symlinks are reported, never followed)."""

    name: str
    type: Literal["file", "dir", "symlink", "other"]


class FsListResult(_BrokerModel):
    """``/v1/fs/list`` — immediate children of a directory under the grant root."""

    entries: tuple[FsDirEntry, ...] = ()
    truncated: bool = False


class FsReadResult(_BrokerModel):
    """``/v1/fs/read`` — a bounded, base64-encoded byte window of a regular file."""

    base64: str
    size: int
    offset: int = 0
    bytes_read: int = Field(alias="bytesRead", default=0)
    truncated: bool = False


class FsGlobResult(_BrokerModel):
    """``/v1/fs/glob`` — root-relative (POSIX) paths that matched."""

    paths: tuple[str, ...] = ()
    truncated: bool = False
    scanned: int = 0


class FsGrepHit(_BrokerModel):
    """One content match from ``/v1/fs/grep``."""

    path: str
    line: int
    column: int
    preview: str


class FsGrepResult(_BrokerModel):
    """``/v1/fs/grep`` — content matches under the grant root."""

    hits: tuple[FsGrepHit, ...] = ()
    truncated: bool = False
    files_scanned: int = Field(alias="filesScanned", default=0)


class BrokerGrant(_BrokerModel):
    """One host-folder grant from ``/v1/grants/snapshot`` (path-free projection).

    ``mount`` is an OPAQUE, per-boot id the broker derives from the grant's host
    root (an HMAC under a per-boot salt): stable within a boot (two grants on
    one tree share a mount) yet non-reversible, so it never becomes a host-path
    oracle. ``label`` is the broker's sanitized display name (folder basename or
    a renderer hint). No host-absolute path is ever present. Every op still keys
    off ``grant_id`` — ``mount`` is presentation-only.
    """

    grant_id: str = Field(alias="grantId")
    mode: Literal["read_only", "read_write_no_delete", "read_write"]
    label: str = ""
    status: Literal["active", "revoked"] = "active"
    mount: str


class BrokerGrantSnapshot(_BrokerModel):
    """``/v1/grants/snapshot`` — the CURRENT active grant set.

    The broker excludes revoked grants from the active snapshot, so every
    ``grants`` entry is safe to bind as a workspace mount.
    """

    snapshot_id: str = Field(alias="snapshotId", default="")
    captured_at: float = Field(alias="capturedAt", default=0.0)
    grants: tuple[BrokerGrant, ...] = ()


# --- client ------------------------------------------------------------------


@dataclass(frozen=True)
class BrokerClientConfig:
    """Connection config for the loopback capability broker.

    ``base_url`` is non-secret (``http://127.0.0.1:<port>``); ``token`` is the
    per-boot bearer secret and must never be logged. ``max_response_bytes`` is a
    defensive backstop — the broker already caps its own response sizes.
    """

    base_url: str
    token: str
    protocol_version: str = "1"
    timeout_seconds: float = 10.0
    max_response_bytes: int = 16 * 1024 * 1024


class DesktopBrokerClient:
    """Thin async client over the broker's ``/v1/fs/*`` routes.

    Each public method maps 1:1 to a broker route, sends the authenticated POST,
    and returns a typed result model — or raises a typed :class:`BrokerError`.

    ``http_client`` defaults to the process-shared :class:`BackendHttpPool`
    instance (TLS/keepalive amortization). Tests inject a fake client — an
    ``httpx.AsyncClient(transport=httpx.MockTransport(...))`` — through the
    constructor; the pool is the *default*, not the contract.
    """

    def __init__(
        self,
        config: BrokerClientConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Bind the client to broker connection config and an httpx transport."""
        self._config = config
        self._http = http_client if http_client is not None else BackendHttpPool.get()

    async def stat(self, grant_id: str, path: str) -> FsStatResult:
        """stat a file or directory at ``path`` under ``grant_id``'s root."""
        body = await self._post(
            Routes.STAT,
            {Field_.GRANT_ID: grant_id, Field_.PATH: path},
        )
        return FsStatResult.model_validate(body)

    async def list(self, grant_id: str, path: str) -> FsListResult:
        """List the immediate children of directory ``path`` under ``grant_id``."""
        body = await self._post(
            Routes.LIST,
            {Field_.GRANT_ID: grant_id, Field_.PATH: path},
        )
        return FsListResult.model_validate(body)

    async def read(
        self,
        grant_id: str,
        path: str,
        *,
        offset: int | None = None,
        max_bytes: int | None = None,
    ) -> FsReadResult:
        """Read a bounded byte window of file ``path`` under ``grant_id``."""
        payload: dict[str, object] = {Field_.GRANT_ID: grant_id, Field_.PATH: path}
        if offset is not None:
            payload[Field_.OFFSET] = offset
        if max_bytes is not None:
            payload[Field_.MAX_BYTES] = max_bytes
        body = await self._post(Routes.READ, payload)
        return FsReadResult.model_validate(body)

    async def glob(
        self,
        grant_id: str,
        pattern: str,
        *,
        max_results: int | None = None,
    ) -> FsGlobResult:
        """Match ``pattern`` against the file tree under ``grant_id``'s root."""
        payload: dict[str, object] = {
            Field_.GRANT_ID: grant_id,
            Field_.PATTERN: pattern,
        }
        if max_results is not None:
            payload[Field_.MAX_RESULTS] = max_results
        body = await self._post(Routes.GLOB, payload)
        return FsGlobResult.model_validate(body)

    async def grep(
        self,
        grant_id: str,
        pattern: str,
        *,
        path_glob: str | None = None,
        is_regex: bool | None = None,
        flags: str | None = None,
        max_matches: int | None = None,
    ) -> FsGrepResult:
        """Search file contents under ``grant_id`` for ``pattern``.

        Defaults to a literal (fixed-string) substring search — matching the
        Deep Agents grep contract — unless ``is_regex`` is set.
        """
        payload: dict[str, object] = {
            Field_.GRANT_ID: grant_id,
            Field_.PATTERN: pattern,
        }
        if path_glob is not None:
            payload[Field_.PATH_GLOB] = path_glob
        if is_regex is not None:
            payload[Field_.IS_REGEX] = is_regex
        if flags is not None:
            payload[Field_.FLAGS] = flags
        if max_matches is not None:
            payload[Field_.MAX_MATCHES] = max_matches
        body = await self._post(Routes.GREP, payload)
        return FsGrepResult.model_validate(body)

    async def grants_snapshot(self) -> BrokerGrantSnapshot:
        """Fetch the broker's CURRENT active grant snapshot (path-free).

        Returns the set of active host-folder grants (revoked entries are
        excluded by the broker), each carrying a ``grant_id`` + opaque ``mount``
        id + sanitized ``label`` — never a host path. Raises a typed
        :class:`BrokerError` on any transport, protocol, or broker-signalled
        failure. The request body is empty; auth + protocol headers are applied
        by :meth:`_post` exactly as for the ``/v1/fs/*`` ops.
        """
        body = await self._post(Routes.GRANTS_SNAPSHOT, {})
        return BrokerGrantSnapshot.model_validate(body)

    # --- transport ----------------------------------------------------------

    async def _post(
        self, route: str, payload: Mapping[str, object]
    ) -> dict[str, object]:
        """POST ``payload`` to ``route`` with broker auth; return the JSON body dict.

        Raises a typed :class:`BrokerError` on any transport, protocol, or
        broker-signalled failure. Never logs the token or any path.
        """
        url = f"{self._config.base_url.rstrip('/')}{route}"
        headers = {
            Header.AUTHORIZATION: f"Bearer {self._config.token}",
            Header.PROTOCOL: self._config.protocol_version,
            Header.CONTENT_TYPE: "application/json",
        }
        try:
            response = await self._http.post(
                url,
                json=dict(payload),
                headers=headers,
                timeout=self._config.timeout_seconds,
            )
        except httpx.TimeoutException:
            # `from None`: never chain the httpx error — its repr can carry the
            # request URL, and we keep broker diagnostics minimal on purpose.
            raise BrokerUnavailableError(
                "capability broker request timed out"
            ) from None
        except httpx.HTTPError:
            raise BrokerUnavailableError("capability broker is unreachable") from None

        # Defensive size backstop — the broker caps its own responses, but never
        # trust an upstream to be well-behaved.
        if len(response.content) > self._config.max_response_bytes:
            logger.debug(
                "capability broker response too large: route=%s status=%s",
                route,
                response.status_code,
            )
            raise BrokerProtocolError("capability broker response too large")

        if response.status_code == 200:
            return self._parse_success(route, response)
        self._raise_for_error(route, response)

    @staticmethod
    def _parse_success(route: str, response: httpx.Response) -> dict[str, object]:
        """Decode a 200 body into a JSON object, or raise a protocol error."""
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
            logger.debug("capability broker returned non-JSON body: route=%s", route)
            raise BrokerProtocolError(
                "capability broker returned malformed JSON"
            ) from None
        if not isinstance(body, dict):
            raise BrokerProtocolError(
                "capability broker returned an unexpected payload"
            )
        return body

    @staticmethod
    def _raise_for_error(route: str, response: httpx.Response) -> None:
        """Map a non-200 broker response to a typed exception and raise it."""
        code: object = None
        try:
            body = response.json()
            if isinstance(body, dict):
                code = body.get(Field_.ERROR)
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
            code = None

        # Diagnostics carry only non-sensitive values: route, status, code.
        logger.debug(
            "capability broker op failed: route=%s status=%s code=%s",
            route,
            response.status_code,
            code,
        )

        exc_cls = _CODE_TO_EXCEPTION.get(code) if isinstance(code, str) else None
        if exc_cls is not None:
            raise exc_cls()
        raise BrokerProtocolError(
            f"capability broker error (status={response.status_code})"
        )
