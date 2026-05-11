"""Strip-internals / extract-actionable helpers for tool errors.

The LLM gets enough to fix its call (validation field names, retry-after,
HTTP status, "all engines failed"); the prompt does not become an
exfiltration channel for file paths, IDs, connection strings, or
multi-frame tracebacks.

Two responsibilities:

* :class:`ErrorSanitizer` rewrites the exception's user-visible message
  string. Strips paths, hex IDs (run / org / conversation), connection
  strings, ``Bearer`` headers, and stack-frame lines. Caps the result to
  a hard byte budget so a runaway traceback never bloats agent context.
* :class:`ErrorHintExtractor` produces a structured, machine-readable
  hint dict for known exception families (pydantic ValidationError,
  HTTP errors, DDGS errors, etc.). The LLM gets these alongside the
  sanitized message so it can act on them programmatically.

Both are pure utilities — no I/O, no global state.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


_MAX_SANITIZED_LENGTH = 2048

# Patterns that should never reach the LLM. Order matters — broader
# patterns last so narrower ones (e.g. ``Bearer <token>``) catch first.
_PATH_PATTERN = re.compile(
    r"(?:/Users/|/opt/|/var/|/private/|/tmp/|/Library/|/System/)\S+",
)
_REPO_PATH_PATTERN = re.compile(
    r"(?:enterprise[-_]search|services/(?:ai-backend|backend|backend-facade))[^\s\"']*",
)
_HEX_ID_PATTERN = re.compile(r"\b[0-9a-fA-F]{16,}\b")
# Permissive UUID-with-dashes form too.
_UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_CONN_STRING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"postgres(?:ql)?://[^\s\"']+"),
    re.compile(r"mysql://[^\s\"']+"),
    re.compile(r"redis://[^\s\"']+"),
)
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"password\s*=\s*[^\s,;\"']+", re.IGNORECASE),
    re.compile(r"token\s*=\s*[^\s,;\"']+", re.IGNORECASE),
    re.compile(r"api[_-]?key\s*[=:]\s*[^\s,;\"']+", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),  # OpenAI-style keys
)
# Stack-frame lines: ``File "…", line N, in func`` and the indented
# source-snippet line that follows. We drop both together.
_STACK_FRAME_PATTERN = re.compile(r'^\s*File "[^"]*", line \d+, in .+$', re.MULTILINE)
_TRACEBACK_HEADER_PATTERN = re.compile(
    r"^\s*Traceback \(most recent call last\):\s*$", re.MULTILINE
)


class ErrorSanitizer:
    """Strip-internals utility for an exception's user-visible message."""

    _REDACTED = "[redacted]"

    @classmethod
    def sanitize(cls, exc: BaseException) -> str:
        """Return a model-safe message for ``exc``.

        The class name is preserved; the message is rewritten to drop
        paths / IDs / connection strings / secrets / stack frames. The
        first line is preferred over the full ``str(exc)`` so multi-line
        chained exceptions don't bleed traceback frames into the
        sanitized output.
        """

        raw = str(exc) or exc.__class__.__name__
        message = cls._strip(raw)
        # Prefer the first meaningful line — multi-line exception strings
        # often include a traceback we've already pruned via _strip, but
        # capping to the first non-empty line keeps things tight.
        first_line = next(
            (line.strip() for line in message.splitlines() if line.strip()),
            message.strip(),
        )
        return cls._truncate(first_line)

    @classmethod
    def _strip(cls, text: str) -> str:
        scrubbed = _TRACEBACK_HEADER_PATTERN.sub("", text)
        scrubbed = _STACK_FRAME_PATTERN.sub("", scrubbed)
        for pattern in _SECRET_PATTERNS:
            scrubbed = pattern.sub(cls._REDACTED, scrubbed)
        for pattern in _CONN_STRING_PATTERNS:
            scrubbed = pattern.sub(cls._REDACTED, scrubbed)
        scrubbed = _PATH_PATTERN.sub(cls._REDACTED, scrubbed)
        scrubbed = _REPO_PATH_PATTERN.sub(cls._REDACTED, scrubbed)
        scrubbed = _UUID_PATTERN.sub(cls._REDACTED, scrubbed)
        scrubbed = _HEX_ID_PATTERN.sub(cls._REDACTED, scrubbed)
        return scrubbed

    @classmethod
    def _truncate(cls, text: str) -> str:
        if len(text) <= _MAX_SANITIZED_LENGTH:
            return text
        # Trim with an explicit marker so the LLM knows truncation
        # happened — it can ask for a tighter call instead of guessing.
        cutoff = _MAX_SANITIZED_LENGTH - len("…[truncated]")
        return text[:cutoff] + "…[truncated]"


class ErrorHintExtractor:
    """Pull structured, actionable hints out of well-known exceptions.

    Each extractor returns either a dict of hints or ``None``. Unknown
    exception types return ``{}`` — the LLM still gets the sanitized
    message and class name; it just doesn't get structured hints.
    """

    @classmethod
    def extract(cls, exc: BaseException) -> Mapping[str, Any]:
        for extractor in (
            cls._pydantic_validation,
            cls._httpx_status,
            cls._httpx_transport,
            cls._ddgs,
        ):
            hints = extractor(exc)
            if hints is not None:
                return hints
        return {}

    @classmethod
    def _pydantic_validation(cls, exc: BaseException) -> Mapping[str, Any] | None:
        try:
            from pydantic import ValidationError
        except Exception:  # pragma: no cover — pydantic always available
            return None
        if not isinstance(exc, ValidationError):
            return None
        invalid_args: list[str] = []
        details: list[dict[str, Any]] = []
        for err in exc.errors():
            loc = err.get("loc") or ()
            field_path = ".".join(str(part) for part in loc) if loc else "(root)"
            invalid_args.append(field_path)
            details.append(
                {
                    "field": field_path,
                    "type": err.get("type"),
                    "msg": err.get("msg"),
                }
            )
        return {
            "category": "validation_error",
            "invalid_args": invalid_args,
            "details": details,
        }

    @classmethod
    def _httpx_status(cls, exc: BaseException) -> Mapping[str, Any] | None:
        try:
            import httpx
        except Exception:  # pragma: no cover — httpx always available
            return None
        if not isinstance(exc, httpx.HTTPStatusError):
            return None
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        retry_after: int | None = None
        if response is not None:
            header_value = (
                response.headers.get("Retry-After") if response.headers else None
            )
            if header_value is not None:
                try:
                    retry_after = int(header_value)
                except (TypeError, ValueError):
                    retry_after = None
        return {
            "category": "http_status",
            "status_code": status_code,
            "retry_after_seconds": retry_after,
            "transient": status_code in {429, 502, 503, 504},
        }

    @classmethod
    def _httpx_transport(cls, exc: BaseException) -> Mapping[str, Any] | None:
        try:
            import httpx
        except Exception:  # pragma: no cover
            return None
        if isinstance(
            exc,
            (
                httpx.ConnectError,
                httpx.ReadTimeout,
                httpx.ConnectTimeout,
                httpx.NetworkError,
            ),
        ):
            return {
                "category": "transport",
                "transient": True,
            }
        return None

    @classmethod
    def _ddgs(cls, exc: BaseException) -> Mapping[str, Any] | None:
        # ``ddgs.DDGSException`` is the public alias; we don't want to
        # hard-import the dep just to reflect on its name, so match by
        # qualified class name. Same path the runtime uses elsewhere.
        qualname = f"{type(exc).__module__}.{type(exc).__name__}"
        if "ddgs" not in qualname and type(exc).__name__ != "DDGSException":
            return None
        message = str(exc).lower()
        return {
            "category": "search_provider",
            "all_engines_failed": "all engines" in message or "no results" in message,
            "rate_limited": "rate" in message or "429" in message,
        }


__all__ = (
    "ErrorHintExtractor",
    "ErrorSanitizer",
)
