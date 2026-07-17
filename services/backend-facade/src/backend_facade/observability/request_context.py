"""Request-scoped correlation context bound to a ``ContextVar``.

Pure-ASGI middleware: SSE streams flow through the facade and must not be
buffered (Starlette ``BaseHTTPMiddleware`` would). One access log line is
emitted per request with method, route template, status, and duration -- no
path, no query, no body, no headers.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
import time
import uuid

from copilot_service_contracts.headers import (
    ORG_HEADER,
    REQUEST_ID_HEADER,
    USER_HEADER,
)


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    org_id: str | None
    user_id: str | None
    method: str | None
    route: str | None


_REQUEST_CONTEXT: ContextVar[RequestContext | None] = ContextVar(
    "backend_facade_request_context", default=None
)


def current_context() -> RequestContext | None:
    return _REQUEST_CONTEXT.get()


class RequestContextMiddleware:
    _ID_PREFIX = "req_"

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        access_log_emitter: Callable[[RequestContext, int, int, str | None], None]
        | None = None,
    ) -> None:
        self._app = app
        self._access_log_emitter = access_log_emitter

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        headers = self._decode_headers(scope.get("headers") or [])
        request_id = headers.get(REQUEST_ID_HEADER) or self._new_request_id()
        org_id = headers.get(ORG_HEADER) or None
        user_id = headers.get(USER_HEADER) or None
        method = scope.get("method")

        ctx = RequestContext(
            request_id=request_id,
            org_id=org_id,
            user_id=user_id,
            method=method,
            route=None,
        )
        token = _REQUEST_CONTEXT.set(ctx)
        started = time.perf_counter()
        status_holder = _StatusHolder()

        async def send_wrapper(message):  # type: ignore[no-untyped-def]
            if message.get("type") == "http.response.start":
                status_holder.code = int(message.get("status", 0))
                message = self._inject_request_id_header(message, request_id)
            await send(message)

        try:
            await self._app(scope, receive, send_wrapper)
        except Exception as exc:
            duration_ms = self._duration_ms(started)
            self._emit_access_log(
                ctx,
                scope,
                status=500,
                duration_ms=duration_ms,
                error_class=type(exc).__name__,
            )
            raise
        else:
            duration_ms = self._duration_ms(started)
            self._emit_access_log(
                ctx,
                scope,
                status=status_holder.code,
                duration_ms=duration_ms,
                error_class=None,
            )
        finally:
            _REQUEST_CONTEXT.reset(token)

    @classmethod
    def _new_request_id(cls) -> str:
        return f"{cls._ID_PREFIX}{uuid.uuid4().hex}"

    @staticmethod
    def _decode_headers(raw: list) -> dict[str, str]:
        decoded: dict[str, str] = {}
        for key, value in raw:
            try:
                k = key.decode("latin-1").lower()
                v = value.decode("latin-1")
            except (AttributeError, UnicodeDecodeError):
                continue
            decoded[k] = v
        return decoded

    @staticmethod
    def _inject_request_id_header(message, request_id: str):  # type: ignore[no-untyped-def]
        headers = list(message.get("headers") or [])
        headers.append(
            (REQUEST_ID_HEADER.encode("latin-1"), request_id.encode("latin-1"))
        )
        return {**message, "headers": headers}

    @staticmethod
    def _duration_ms(started: float) -> int:
        return max(0, int((time.perf_counter() - started) * 1000))

    def _emit_access_log(
        self,
        ctx: RequestContext,
        scope,  # type: ignore[no-untyped-def]
        *,
        status: int,
        duration_ms: int,
        error_class: str | None,
    ) -> None:
        if self._access_log_emitter is None:
            return
        route = self._route_template(scope)
        ctx_with_route = RequestContext(
            request_id=ctx.request_id,
            org_id=ctx.org_id,
            user_id=ctx.user_id,
            method=ctx.method,
            route=route,
        )
        self._access_log_emitter(ctx_with_route, status, duration_ms, error_class)

    @staticmethod
    def _route_template(scope) -> str | None:  # type: ignore[no-untyped-def]
        route = scope.get("route")
        if route is None:
            return scope.get("path") or None
        path = getattr(route, "path", None)
        if isinstance(path, str) and path:
            return path
        return scope.get("path") or None


class _StatusHolder:
    __slots__ = ("code",)

    def __init__(self) -> None:
        self.code = 0
