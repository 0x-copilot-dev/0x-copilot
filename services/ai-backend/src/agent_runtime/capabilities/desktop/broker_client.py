"""Async client for desktop broker reads and v2 workspace effects.

AC5 slice 3a/3b — the ai-backend side of user-granted host-folder access.

The Electron main process runs an authenticated, loopback-only capability
broker (``apps/desktop/main/capabilities/broker.ts``). It exposes the
filesystem READ operations (``stat``/``list``/``read``/``glob``/``grep``) and
the private v2 prepared workspace-effect protocol. Direct filesystem mutation
was retired under PRD-E2 D7: it has no client dispatch method and cannot be
re-enabled through a rollout setting. Every JSON request carries:

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
import re
from collections.abc import AsyncIterable, Mapping
from dataclasses import dataclass
from typing import Final, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from agent_runtime.capabilities.http_pool import BackendHttpPool

logger = logging.getLogger(__name__)

_OPAQUE_COMPONENT: Final = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
_HOST_SESSION_REF: Final = re.compile(r"^whs_[A-Za-z0-9_-]{32,160}$")


def _workspace_prepared_id(prepared_ref: str) -> str:
    """Decode only the safe opaque tail accepted by Electron's v2 routes."""

    prefix = "workspace-prepared://"
    if not prepared_ref.startswith(prefix):
        raise BrokerProtocolError("workspace prepared reference is invalid")
    identifier = prepared_ref[len(prefix) :]
    if _OPAQUE_COMPONENT.fullmatch(identifier) is None:
        raise BrokerProtocolError("workspace prepared reference is invalid")
    return identifier


def _assert_host_session_wire_is_private(body: Mapping[str, object]) -> None:
    """Reject any host-session field outside the narrow opaque contract.

    This is intentionally stricter than the generic broker models.  A future
    Electron change must not accidentally put a read capability, permit,
    prepared reference, device identity, root, or path into the worker's
    private bootstrap response and have Pydantic silently ignore it.

    The grant field names are the ones that actually travel — Electron's
    ``toHostSessionGrant`` emits ``grantId``, and only the Pydantic ALIAS is
    ``grant_id``. Written in field spelling, this allowlist matched nothing a
    real broker sends: every live host session would have failed closed, while
    every test fed it snake_case and stayed green. Both spellings are admitted
    so the assertion is about WHICH fields cross, never about which caller
    spelled them how — that is the model's job.
    """

    expected = {"host_session_ref", "expires_at", "grants"}
    if set(body) != expected or not isinstance(body["grants"], list):
        raise BrokerProtocolError("workspace host session response is invalid")
    allowed_grant_fields = {
        "grantId",
        "grant_id",
        "mount",
        "mode",
        "label",
        "status",
    }
    for grant in body["grants"]:
        if not isinstance(grant, dict) or set(grant) - allowed_grant_fields:
            raise BrokerProtocolError("workspace host session response is invalid")


class Routes:
    """Broker route paths (mirrors ``broker.ts`` ``ROUTES``)."""

    STAT: Final = "/v1/fs/stat"
    LIST: Final = "/v1/fs/list"
    READ: Final = "/v1/fs/read"
    GLOB: Final = "/v1/fs/glob"
    GREP: Final = "/v1/fs/grep"
    #: Grant-management read — the CURRENT active grant snapshot (path-free).
    GRANTS_SNAPSHOT: Final = "/v1/grants/snapshot"
    #: MCP direct-connect credential read (migration P2-7a). Resolves one
    #: connector's endpoint + short-lived bearer out of ``SecretStorage`` under
    #: the main process's ACTIVE-WORKSPACE gate. Kept here so ai-backend has one
    #: HTTP path to the broker rather than a second, MCP-only client.
    MCP_SECRET: Final = "/mcp/secret"
    #: C2 staged workspace mutation protocol. These are private loopback
    #: routes, never facade/public API routes.
    WORKSPACE_PREPARE: Final = "/internal/workspace/v2/prepare"
    WORKSPACE_HOST_SESSIONS: Final = "/internal/workspace/v2/host-sessions"
    WORKSPACE_PREPARED: Final = "/internal/workspace/v2/prepared"
    WORKSPACE_CLAIMS: Final = "/internal/workspace/v2/claims"


class Header:
    """Request header names the broker requires on every call."""

    AUTHORIZATION: Final = "Authorization"
    PROTOCOL: Final = "X-Capability-Protocol"
    CONTENT_TYPE: Final = "content-type"
    SERVICE_IDENTITY: Final = "x-desktop-local-service"
    SERVICE_AUDIENCE: Final = "x-desktop-local-audience"


class Field_:  # noqa: N801 — a small constants namespace, not a runtime type
    """Request/response JSON field names shared with the broker wire contract."""

    GRANT_ID: Final = "grant_id"
    #: The MCP connector identity ``/mcp/secret`` is keyed on — the same
    #: ``server_id`` ``SecretStorage.get(activeWorkspace, "mcp", server_id)``
    #: uses, so one connector has one id across the whole credential plane.
    SERVER_ID: Final = "server_id"
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
    CAPABILITY_RETIRED: Final = "capability_retired"
    TOO_LARGE: Final = "too_large"

    # Envelope / protocol / config failures (broker.ts ``#handle``).
    UNAUTHORIZED: Final = "unauthorized"
    FORBIDDEN: Final = "forbidden"
    UNSUPPORTED_PROTOCOL_VERSION: Final = "unsupported_protocol_version"
    PAYLOAD_TOO_LARGE: Final = "payload_too_large"
    INVALID_JSON: Final = "invalid_json"
    METHOD_NOT_ALLOWED: Final = "method_not_allowed"
    INTERNAL: Final = "internal"
    WORKSPACE_WRITE_UNSUPPORTED: Final = "workspace_write_unsupported"
    WORKSPACE_CAPABILITY_DENIED: Final = "workspace_capability_denied"
    WORKSPACE_PERMIT_DENIED: Final = "workspace_permit_denied"
    WORKSPACE_PREPARED_NOT_FOUND: Final = "workspace_prepared_not_found"
    WORKSPACE_PRECONDITION_DRIFT: Final = "workspace_precondition_drift"
    WORKSPACE_CONFLICT: Final = "workspace_conflict"


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


class BrokerCapabilityRetiredError(BrokerError):
    """A permanently retired legacy capability was requested."""

    code = ErrorCode.CAPABILITY_RETIRED


class WorkspaceAuthorityDeniedError(BrokerError):
    """A main-issued workspace capability or exact permit was rejected."""

    code = ErrorCode.WORKSPACE_CAPABILITY_DENIED


class WorkspacePreconditionDriftError(BrokerError):
    """The checked workspace baseline changed before commit."""

    code = ErrorCode.WORKSPACE_PRECONDITION_DRIFT


class WorkspaceConflictError(BrokerError):
    """The prepared workspace reservation cannot be committed safely."""

    code = ErrorCode.WORKSPACE_CONFLICT


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
    ErrorCode.CAPABILITY_RETIRED: BrokerCapabilityRetiredError,
    ErrorCode.WORKSPACE_WRITE_UNSUPPORTED: BrokerUnsupportedError,
    ErrorCode.WORKSPACE_CAPABILITY_DENIED: WorkspaceAuthorityDeniedError,
    ErrorCode.WORKSPACE_PERMIT_DENIED: WorkspaceAuthorityDeniedError,
    ErrorCode.WORKSPACE_PREPARED_NOT_FOUND: BrokerNotFoundError,
    ErrorCode.WORKSPACE_PRECONDITION_DRIFT: WorkspacePreconditionDriftError,
    ErrorCode.WORKSPACE_CONFLICT: WorkspaceConflictError,
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
    """One host-folder grant from ``/v1/grants/snapshot``.

    ``mount`` is an OPAQUE, per-boot id the broker derives from the grant's host
    root (an HMAC under a per-boot salt): stable within a boot (two grants on
    one tree share a mount) yet non-reversible. ``label`` is the broker's
    sanitized display name (folder basename or a renderer hint). Every op still
    keys off ``grant_id`` — ``mount`` is presentation-only.

    ``root`` — the DELIBERATE REVERSAL of this model's original path-free rule
    ---------------------------------------------------------------------------
    This projection used to carry no host-absolute path at all, so that a
    compromised ai-backend could not learn where a grant pointed. That property
    stopped buying anything the moment host reads moved to deepagents'
    ``FilesystemBackend``: this process now resolves and reads real host paths
    itself, so it necessarily holds them. Withholding the root only hid it from
    the one component that needs it, with this consequence — a granted folder
    could not be turned into an ``allow`` rule, so EVERY read of a folder the
    user had explicitly attached still interrupted and asked again. "Attach a
    folder" bought the user nothing.

    ``root`` is therefore the grant's real host root, and it is what
    ``HostFilesystemRules`` turns into the ``allow`` rule that stops the asking.
    It stays OPTIONAL: an older broker that does not send it yields ``None``,
    and such a grant simply keeps prompting rather than failing — degraded, not
    broken.

    What did NOT change: the RENDERER projection is still path-free
    (``WorkspaceGrantPort`` / ``type PathFree<T>``), because the UI has no need
    for a host path and is a far likelier exfiltration surface. The reversal is
    scoped to the service that performs the read.
    """

    grant_id: str = Field(alias="grantId")
    mode: Literal["read_only", "read_write_no_delete", "read_write"]
    label: str = ""
    status: Literal["active", "revoked"] = "active"
    mount: str
    root: str | None = None


class BrokerGrantSnapshot(_BrokerModel):
    """``/v1/grants/snapshot`` — the CURRENT active grant set.

    The broker excludes revoked grants from the active snapshot, so every
    ``grants`` entry is safe to bind as a workspace mount.
    """

    snapshot_id: str = Field(alias="snapshotId", default="")
    captured_at: float = Field(alias="capturedAt", default=0.0)
    grants: tuple[BrokerGrant, ...] = ()


class McpSecretResult(_BrokerModel):
    """``/mcp/secret`` — one MCP connector's endpoint plus a short-lived bearer.

    The desktop half of the direct-connect credential plane (migration P2-7a).
    Electron main resolves it from ``SecretStorage`` under the **active-workspace
    gate**, so a run can only ever read the credential of the workspace it is
    running in; this model is the untrusted-JSON boundary on the ai-backend side.

    ``token`` is a :class:`~pydantic.SecretStr` with ``repr`` additionally
    suppressed at the field level, so neither ``repr()`` nor ``str()`` nor
    ``model_dump_json()`` (structured logs) can render the plaintext. It is
    unwrapped in exactly one place downstream — the moment the ``Authorization``
    header is written by ``RefreshingBearerAuth``.

    ``expires_at`` is deliberately permissive at the wire (``str | int | float |
    None``): Electron speaks epoch milliseconds elsewhere on this broker while an
    OAuth store commonly speaks epoch seconds or ISO-8601, and guessing wrong in
    either direction is a live failure (a token cached past its death, or a
    re-mint on every single request). The one place that normalization happens is
    the MCP credential provider, which owns the rule and the clock.

    ``headers`` carries only NON-SECRET request headers. The bearer never rides
    here — the provider strips any authorization-shaped header before the config
    reaches the connection.
    """

    url: str = Field(min_length=1, max_length=2_048)
    transport: str = Field(min_length=1, max_length=64)
    token: SecretStr = Field(repr=False, min_length=1)
    expires_at: str | int | float | None = Field(default=None, alias="expiresAt")
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("expires_at", mode="before")
    @classmethod
    def _reject_boolean_expiry(cls, value: object) -> object:
        """Refuse a ``bool`` before the union's lax ``int`` member accepts it.

        ``bool`` is an ``int`` subclass, so a JSON ``true`` would otherwise be
        coerced to ``1`` and read as 1970-01-01T00:00:01Z — an expiry in the past
        means the auth re-mints on *every* request, a keychain-read storm
        sustained by one malformed field. Rejected at the boundary instead.
        """

        if isinstance(value, bool):
            raise ValueError("expires_at must be a timestamp, not a boolean")
        return value


class WorkspaceUploadSlot(_BrokerModel):
    """One private staged-content slot returned by workspace prepare."""

    slot: str
    digest: str = Field(min_length=64, max_length=64)
    size: int = Field(ge=0)


class WorkspacePreparedEffect(_BrokerModel):
    """Opaque broker prepare result; it carries no physical path."""

    prepared_ref: str = Field(min_length=1, max_length=2048)
    expires_at: int = Field(ge=0)
    observed_target_digest: str = Field(min_length=64, max_length=64)
    upload_slots: tuple[WorkspaceUploadSlot, ...] = ()


class WorkspaceHostSessionGrant(_BrokerModel):
    """Path-free grant projection bound to one private host session."""

    grant_id: str = Field(alias="grantId")
    mode: Literal["read_only", "read_write_no_delete", "read_write"]
    label: str = ""
    status: Literal["active", "revoked"] = "active"
    mount: str


class WorkspaceHostSession(_BrokerModel):
    """Opaque main-issued session reference; never a read capability or permit."""

    host_session_ref: str = Field(min_length=36, max_length=192)
    expires_at: int = Field(ge=0)
    grants: tuple[WorkspaceHostSessionGrant, ...] = ()


class WorkspaceCommitResult(_BrokerModel):
    """Stable result for a one-use workspace commit or reconcile."""

    outcome: Literal[
        "applied",
        "already_applied",
        "precondition_drift",
        "failed",
        "indeterminate",
    ]
    receipt_ref: str = Field(min_length=1, max_length=2048)
    result_digest: str | None = Field(default=None, min_length=64, max_length=64)
    safe_message: str | None = Field(default=None, max_length=512)


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
    # The supervised child identity bound by Electron main, when configured.
    service_identity: str | None = None
    # The fixed Electron-main broker audience for this private channel.
    broker_audience: str | None = None
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

    async def mcp_secret(self, server_id: str) -> McpSecretResult:
        """Read one MCP connector's endpoint + short-lived bearer (P2-7a).

        MCP tokens are added at connect time, so they cannot ride the supervisor's
        boot secrets and must be read live. The read happens in Electron main
        under the active-workspace gate — a compromised run cannot reach another
        workspace's connector — and returns over this same authenticated loopback
        channel, so direct-connect adds no new trust boundary.

        Raises a typed :class:`BrokerError` on any transport, protocol, or
        broker-signalled failure, exactly like every other route here. The
        response's token is a ``SecretStr``: nothing on this path logs it, and
        :meth:`_raise_for_error` only ever records route + status + machine code.
        """
        body = await self._post(Routes.MCP_SECRET, {Field_.SERVER_ID: server_id})
        return McpSecretResult.model_validate(body)

    # --- v2 workspace authority (C2) --------------------------------------

    async def workspace_host_session(
        self,
        *,
        run_id: str,
        user_id: str,
    ) -> WorkspaceHostSession:
        """Bootstrap one opaque, run-bound host session over the private broker.

        The response deliberately omits the C2 read capability, device id,
        root, prepared ref, and permit.  The worker retains only the opaque
        ``host_session_ref`` it must echo for prepare/commit.
        """

        body = await self._post(
            Routes.WORKSPACE_HOST_SESSIONS,
            {"run_id": run_id, "user_id": user_id},
            accepted_statuses=frozenset({200, 201}),
        )
        _assert_host_session_wire_is_private(body)
        session = WorkspaceHostSession.model_validate(body)
        if _HOST_SESSION_REF.fullmatch(session.host_session_ref) is None:
            raise BrokerProtocolError("workspace host session reference is invalid")
        return session

    async def workspace_prepare(
        self,
        *,
        host_session_ref: str,
        change_set: Mapping[str, object],
    ) -> WorkspacePreparedEffect:
        """Reserve a CAS-checked workspace change set without mutation.

        ``host_session_ref`` is an opaque Electron-main binding for one exact
        run/user/device. The transport bearer alone is insufficient, and the
        main-issued read capability never leaves the desktop process.
        """

        if _HOST_SESSION_REF.fullmatch(host_session_ref) is None:
            raise BrokerProtocolError("workspace host session reference is invalid")
        payload = {"host_session_ref": host_session_ref, **dict(change_set)}
        body = await self._post(
            Routes.WORKSPACE_PREPARE,
            payload,
            accepted_statuses=frozenset({200, 201}),
        )
        return WorkspacePreparedEffect.model_validate(body)

    async def workspace_upload(
        self,
        *,
        prepared_ref: str,
        slot: str,
        content: AsyncIterable[bytes],
        final: bool,
    ) -> None:
        """Stream immutable artifact bytes into a private prepared slot.

        The payload is deliberately raw octets rather than base64 JSON. The
        Electron authority validates exact accumulated size/digest before it
        will make the slot commit-eligible.
        """

        prepared_id = _workspace_prepared_id(prepared_ref)
        if _OPAQUE_COMPONENT.fullmatch(slot) is None:
            raise BrokerProtocolError("workspace slot reference is invalid")
        await self._put_stream(
            f"{Routes.WORKSPACE_PREPARED}/{prepared_id}/content/{slot}",
            content=content,
            final=final,
        )

    async def workspace_commit(
        self, *, prepared_ref: str, host_session_ref: str
    ) -> WorkspaceCommitResult:
        """Commit using only an opaque host-session reference.

        Electron main derives the exact prepared binding, consumes the verified
        approval reservation, and keeps the raw one-use C2 permit in-process.
        """

        prepared_id = _workspace_prepared_id(prepared_ref)
        if _HOST_SESSION_REF.fullmatch(host_session_ref) is None:
            raise BrokerProtocolError("workspace host session reference is invalid")
        body = await self._post(
            f"{Routes.WORKSPACE_PREPARED}/{prepared_id}/commit",
            {"host_session_ref": host_session_ref},
        )
        return WorkspaceCommitResult.model_validate(body)

    async def workspace_reconcile(self, *, claim_id: str) -> WorkspaceCommitResult:
        """Ask the native journal to reconcile a prior non-terminal claim."""

        if _OPAQUE_COMPONENT.fullmatch(claim_id) is None:
            raise BrokerProtocolError("workspace claim reference is invalid")
        body = await self._post(
            f"{Routes.WORKSPACE_CLAIMS}/{claim_id}/reconcile",
            {},
        )
        return WorkspaceCommitResult.model_validate(body)

    async def workspace_abort(self, *, prepared_ref: str) -> None:
        """Release an uncommitted native reservation, without host mutation."""

        prepared_id = _workspace_prepared_id(prepared_ref)
        await self._post(f"{Routes.WORKSPACE_PREPARED}/{prepared_id}/abort", {})

    # --- transport ----------------------------------------------------------

    async def _post(
        self,
        route: str,
        payload: Mapping[str, object],
        *,
        accepted_statuses: frozenset[int] = frozenset({200}),
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
        if self._config.service_identity:
            headers[Header.SERVICE_IDENTITY] = self._config.service_identity
        if self._config.broker_audience:
            headers[Header.SERVICE_AUDIENCE] = self._config.broker_audience
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

        if response.status_code in accepted_statuses:
            return self._parse_success(route, response)
        self._raise_for_error(route, response)

    async def _put_stream(
        self,
        route: str,
        *,
        content: AsyncIterable[bytes],
        final: bool,
    ) -> None:
        """PUT raw staged bytes with the same private broker authentication."""

        url = f"{self._config.base_url.rstrip('/')}{route}"
        headers = {
            Header.AUTHORIZATION: f"Bearer {self._config.token}",
            Header.PROTOCOL: self._config.protocol_version,
            Header.CONTENT_TYPE: "application/octet-stream",
            "x-workspace-upload-final": "true" if final else "false",
        }
        if self._config.service_identity:
            headers[Header.SERVICE_IDENTITY] = self._config.service_identity
        if self._config.broker_audience:
            headers[Header.SERVICE_AUDIENCE] = self._config.broker_audience
        try:
            response = await self._http.put(
                url,
                content=content,
                headers=headers,
                timeout=self._config.timeout_seconds,
            )
        except httpx.TimeoutException:
            raise BrokerUnavailableError(
                "capability broker request timed out"
            ) from None
        except httpx.HTTPError:
            raise BrokerUnavailableError("capability broker is unreachable") from None
        if len(response.content) > self._config.max_response_bytes:
            raise BrokerProtocolError("capability broker response too large")
        if response.status_code != 200:
            self._raise_for_error(route, response)
        self._parse_success(route, response)

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
