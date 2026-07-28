"""Backend-owned HTTP transport for pooled remote MCP sessions.

Endpoints, encrypted token envelopes, protocol negotiation, and remote session
identifiers remain process-local.  The registry exposes only pool leases.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backend_app.mcp_session_pool import (
    McpSessionDispatchFence,
    McpSessionTransport,
    VerifiedMcpSessionScopeKey,
)
from backend_app.token_vault import TokenVault

_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


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
    endpoint: str = field(repr=False)
    encrypted_access_token: str | None = field(repr=False)


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
        endpoint: str,
        encrypted_access_token: str | None,
    ) -> None:
        with self._lock:
            self._bindings.pop(scope.fingerprint, None)
            self._bindings[scope.fingerprint] = _TransportBinding(
                endpoint=endpoint,
                encrypted_access_token=encrypted_access_token,
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


__all__ = [
    "McpHttpTransportFactory",
    "McpRemoteAuthError",
    "McpRemoteSessionTransport",
    "McpRemoteTransportError",
]
