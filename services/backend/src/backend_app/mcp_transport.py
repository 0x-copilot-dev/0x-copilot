"""Backend-owned HTTP transport for pooled remote MCP sessions.

Endpoints, encrypted token envelopes, protocol negotiation, and remote session
identifiers remain process-local.  The registry exposes only pool leases.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import IO, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backend_app.contracts import McpConfiguredValue, McpStdioConfig
from backend_app.mcp_session_pool import (
    McpSessionDispatchFence,
    McpSessionTransport,
    VerifiedMcpSessionScopeKey,
)
from backend_app.token_vault import TokenVault

_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
# How long a local server gets to exit after its stdin closes, before it is
# killed. Short on purpose: this runs on the shutdown path.
_STDIO_SHUTDOWN_SECONDS = 5.0


class McpRemoteAuthError(ValueError):
    """The remote server rejected backend-held authentication material."""


class McpRemoteTransportError(ConnectionError):
    """A fenced request failed after dispatch became potentially observable."""


@runtime_checkable
class McpRemoteSessionTransport(McpSessionTransport, Protocol):
    """A pool transport capable of issuing one fenced JSON-RPC request."""

    def rpc(
        self,
        payload: dict[str, object],
        fence: McpSessionDispatchFence,
    ) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class _TransportBinding:
    """How to reach one MCP server: an endpoint, or a program to launch.

    Exactly one of ``endpoint`` / ``stdio`` is set, mirroring the registry
    record's own invariant. Keeping both on one binding lets the session pool
    stay transport-agnostic — it leases "a transport", and which kind it got is
    decided here, once, from the row.
    """

    endpoint: str | None = field(repr=False, default=None)
    encrypted_access_token: str | None = field(repr=False, default=None)
    headers: tuple[McpConfiguredValue, ...] = field(repr=False, default=())
    stdio: McpStdioConfig | None = field(repr=False, default=None)


def _resolve_configured_values(
    values: tuple[McpConfiguredValue, ...],
    token_vault: TokenVault,
) -> dict[str, str]:
    """Decrypt configured headers / env into a name → value mapping.

    A placeholder with no stored secret is DROPPED rather than sent empty: an
    ``Authorization: Bearer`` with nothing after it reads to the server as a
    malformed credential, and the resulting 401 would send the user hunting
    for a token problem instead of the "you haven't supplied this input yet"
    that is actually true.
    """

    resolved: dict[str, str] = {}
    for item in values:
        if item.value is not None:
            resolved[item.name] = item.value
        elif item.encrypted_value is not None:
            resolved[item.name] = token_vault.decrypt(item.encrypted_value)
    return resolved


class McpHttpTransportFactory:
    """Process-local binding registry used only by the session-pool factory."""

    def __init__(self, *, token_vault: TokenVault, max_bindings: int = 128) -> None:
        if max_bindings < 1:
            raise ValueError("MCP transport binding capacity must be positive")
        self._token_vault = token_vault
        self._bindings: OrderedDict[str, _TransportBinding] = OrderedDict()
        self._max_bindings = max_bindings
        self._lock = threading.RLock()

    def bind(
        self,
        *,
        scope: VerifiedMcpSessionScopeKey,
        endpoint: str | None,
        encrypted_access_token: str | None,
        headers: tuple[McpConfiguredValue, ...] = (),
        stdio: McpStdioConfig | None = None,
    ) -> None:
        if (endpoint is None) == (stdio is None):
            raise ValueError("a transport binding needs exactly one of endpoint/stdio")
        with self._lock:
            self._bindings.pop(scope.fingerprint, None)
            self._bindings[scope.fingerprint] = _TransportBinding(
                endpoint=endpoint,
                encrypted_access_token=encrypted_access_token,
                headers=headers,
                stdio=stdio,
            )
            while len(self._bindings) > self._max_bindings:
                self._bindings.popitem(last=False)

    def unbind(self, scope: VerifiedMcpSessionScopeKey) -> None:
        with self._lock:
            self._bindings.pop(scope.fingerprint, None)

    def connect(self, scope: VerifiedMcpSessionScopeKey) -> McpRemoteSessionTransport:
        with self._lock:
            binding = self._bindings.get(scope.fingerprint)
            if binding is not None:
                self._bindings.move_to_end(scope.fingerprint)
        if binding is None:
            raise RuntimeError("MCP transport binding is unavailable")
        if binding.stdio is not None:
            return McpStdioTransport(binding=binding, token_vault=self._token_vault)
        return McpHttpTransport(binding=binding, token_vault=self._token_vault)


class McpHttpTransport:
    """Synchronous streamable-HTTP MCP transport with sticky session headers."""

    def __init__(self, *, binding: _TransportBinding, token_vault: TokenVault) -> None:
        self._binding = binding
        self._token_vault = token_vault
        self._closed = False
        self._session_id: str | None = None
        self._protocol_version: str | None = None
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def keepalive(self) -> None:
        self._request(
            {"jsonrpc": "2.0", "id": "pool-keepalive", "method": "ping"},
            fence=None,
        )

    def rpc(
        self, payload: dict[str, object], fence: McpSessionDispatchFence
    ) -> dict[str, object]:
        return self._request(payload, fence=fence)

    def _request(
        self,
        payload: dict[str, object],
        *,
        fence: McpSessionDispatchFence | None,
    ) -> dict[str, object]:
        with self._lock:
            if self._closed:
                raise ConnectionError("MCP transport is closed")
            headers = {
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
            }
            # Revalidate the lease before *any* credential material is
            # decrypted. From this point on local failures are conservatively
            # non-retryable, which is safer than treating a partial request as
            # pre-dispatch.
            if fence is not None:
                fence.commit()
            try:
                if self._binding.encrypted_access_token is not None:
                    token = self._token_vault.decrypt(
                        self._binding.encrypted_access_token
                    )
                    headers["authorization"] = f"Bearer {token}"
                # Configured headers apply AFTER the OAuth bearer, so an
                # explicitly configured `Authorization` wins. That ordering is
                # the point: a server carrying a user-supplied API key has no
                # OAuth token to be overwritten by, and one carrying both was
                # configured by someone who meant the header they wrote. The
                # protocol headers below are set last and are unreachable from
                # configuration anyway (see `_RESERVED_HEADER_NAMES`).
                #
                # Lower-cased on the way in because THIS dict is lower-cased:
                # merging `Authorization` next to an existing `authorization`
                # would leave both in the mapping and send the header twice,
                # with the server free to honour whichever it saw first. HTTP
                # field names are case-insensitive, so folding them is
                # lossless; environment variable names are NOT, which is why
                # the shared resolver preserves case and only this call site
                # folds it.
                headers.update(
                    {
                        name.lower(): value
                        for name, value in _resolve_configured_values(
                            self._binding.headers, self._token_vault
                        ).items()
                    }
                )
                if self._session_id is not None:
                    headers["mcp-session-id"] = self._session_id
                if self._protocol_version is not None:
                    headers["mcp-protocol-version"] = self._protocol_version
                request = Request(
                    self._binding.endpoint,
                    data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urlopen(request, timeout=30) as response:
                    self._session_id = (
                        response.headers.get("mcp-session-id") or self._session_id
                    )
                    self._protocol_version = (
                        response.headers.get("mcp-protocol-version")
                        or self._protocol_version
                    )
                    raw_bytes = response.read(_MAX_RESPONSE_BYTES + 1)
                    if len(raw_bytes) > _MAX_RESPONSE_BYTES:
                        raise McpRemoteTransportError(
                            "MCP server response exceeds the safe limit"
                        )
                    raw = raw_bytes.decode("utf-8")
                    content_type = response.headers.get("content-type", "")
            except HTTPError as exc:
                if exc.code in {401, 403}:
                    raise McpRemoteAuthError(
                        "MCP server rejected the stored OAuth token"
                    ) from exc
                raise McpRemoteTransportError("MCP server request failed") from exc
            except (URLError, TimeoutError) as exc:
                raise McpRemoteTransportError("MCP server is unavailable") from exc
            except McpRemoteTransportError:
                raise
            except Exception as exc:
                raise McpRemoteTransportError("MCP server request failed") from exc
        try:
            decoded = self._decode(raw, content_type)
        except Exception as exc:
            raise McpRemoteTransportError(
                "MCP server returned an invalid JSON-RPC response"
            ) from exc
        if not isinstance(decoded, dict):
            raise McpRemoteTransportError(
                "MCP server returned an invalid JSON-RPC response"
            )
        return decoded

    @staticmethod
    def _decode(raw: str, content_type: str) -> object:
        if content_type.lower().startswith("text/event-stream"):
            for line in raw.splitlines():
                if line.startswith("data:"):
                    data = line.removeprefix("data:").strip()
                    if data and data != "[DONE]":
                        return json.loads(data)
            return {}
        return json.loads(raw or "{}")


class McpStdioTransport:
    """Local stdio MCP transport: a subprocess speaking JSON-RPC over pipes.

    The spec's other standard transport. The client launches the server as a
    child process and exchanges newline-delimited JSON-RPC over its stdin and
    stdout; stderr is the server's log stream and is explicitly NOT part of the
    protocol, so it is drained to a discard sink rather than parsed.

    Three deliberate choices, each load-bearing:

    * **No shell.** ``command`` and ``args`` go to ``Popen`` as a vector, so
      quoting, globs, pipes, and ``;`` carry no meaning. There is no
      shell-injection surface because there is no shell.
    * **A closed environment.** The child gets ONLY the configured variables
      plus the handful needed to find an interpreter (``PATH``, ``HOME``).
      Inheriting the backend's environment would hand every local MCP server
      the process's own database URL, auth secret, and vault key.
    * **Draining stderr.** A child that logs heavily fills the pipe buffer and
      blocks forever on write — a hang with no error anywhere, which is the
      worst failure this class could have. A reader thread keeps it empty.

    Reaching a local process needs no credential, so there is no token to
    decrypt here and ``McpRemoteAuthError`` cannot arise.
    """

    def __init__(self, *, binding: _TransportBinding, token_vault: TokenVault) -> None:
        if binding.stdio is None:  # pragma: no cover — the factory guarantees it
            raise ValueError("a stdio transport needs a stdio launch config")
        self._config = binding.stdio
        self._token_vault = token_vault
        self._closed = False
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._stderr_pump: threading.Thread | None = None

    def _environment(self) -> dict[str, str]:
        base = {
            name: os.environ[name]
            for name in ("PATH", "HOME", "LANG", "TMPDIR", "SystemRoot")
            if name in os.environ
        }
        base.update(_resolve_configured_values(self._config.env, self._token_vault))
        return base

    def _ensure_process(self) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        try:
            process = subprocess.Popen(  # noqa: S603 — vector, never a shell
                [self._config.command, *self._config.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self._config.cwd,
                env=self._environment(),
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except (OSError, ValueError) as exc:
            raise McpRemoteTransportError(
                f"could not start local MCP server {self._config.command!r}"
            ) from exc
        self._process = process
        stderr = process.stderr
        if stderr is not None:
            pump = threading.Thread(
                target=self._drain, args=(stderr,), daemon=True, name="mcp-stdio-stderr"
            )
            pump.start()
            self._stderr_pump = pump
        return process

    @staticmethod
    def _drain(stream: IO[str]) -> None:
        try:
            for _ in stream:
                pass
        except (ValueError, OSError):
            # The pipe closed under us while the process was being torn down.
            return

    def close(self) -> None:
        with self._lock:
            self._closed = True
            process = self._process
            self._process = None
        if process is None or process.poll() is not None:
            return
        # Closing stdin is how the spec says to shut a stdio server down; the
        # kill is the backstop for one that ignores it.
        try:
            if process.stdin is not None:
                process.stdin.close()
            process.wait(timeout=_STDIO_SHUTDOWN_SECONDS)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            process.kill()

    def keepalive(self) -> None:
        self._request({"jsonrpc": "2.0", "id": "pool-keepalive", "method": "ping"})

    def rpc(
        self, payload: dict[str, object], fence: McpSessionDispatchFence
    ) -> dict[str, object]:
        return self._request(payload, fence=fence)

    def _request(
        self,
        payload: dict[str, object],
        *,
        fence: McpSessionDispatchFence | None = None,
    ) -> dict[str, object]:
        with self._lock:
            if self._closed:
                raise ConnectionError("MCP transport is closed")
            process = self._ensure_process()
            stdin, stdout = process.stdin, process.stdout
            if stdin is None or stdout is None:  # pragma: no cover — PIPE is set
                raise McpRemoteTransportError("local MCP server has no pipes")
            # Same fence discipline as the HTTP transport: revalidate the lease
            # immediately before the write that makes dispatch observable.
            if fence is not None:
                fence.commit()
            try:
                stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
                stdin.flush()
                raw = stdout.readline()
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise McpRemoteTransportError(
                    "local MCP server is unavailable"
                ) from exc
            if raw == "":
                raise McpRemoteTransportError("local MCP server exited")
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise McpRemoteTransportError(
                    "MCP server response exceeds the safe limit"
                )
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise McpRemoteTransportError(
                "MCP server returned an invalid JSON-RPC response"
            ) from exc
        if not isinstance(decoded, dict):
            raise McpRemoteTransportError(
                "MCP server returned an invalid JSON-RPC response"
            )
        return decoded


__all__ = [
    "McpHttpTransportFactory",
    "McpRemoteAuthError",
    "McpRemoteSessionTransport",
    "McpRemoteTransportError",
    "McpStdioTransport",
]
