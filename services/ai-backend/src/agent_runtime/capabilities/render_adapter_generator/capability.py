"""MCP-style capability that emits tier-2 ``SaaSRendererAdapter`` source code."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, ClassVar

from pydantic import ValidationError

from agent_runtime.capabilities.render_adapter_generator.models import (
    AdapterCodegenRequest,
    AdapterCodegenResult,
    LayoutTemplate,
    SampleState,
)
from agent_runtime.capabilities.render_adapter_generator.templates import (
    AdapterSourceBuilder,
)
from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.schemas import RuntimeApiEventType

if TYPE_CHECKING:  # pragma: no cover - typing-only imports
    from agent_runtime.api.events import RuntimeEventProducer
    from runtime_api.schemas import RunRecord, RuntimeEventEnvelope


class _GeneratorIdentity:
    """Stable identifier embedded in metadata + emitted with every event."""

    MODEL_NAME: ClassVar[str] = "render-adapter-generator/v1"
    SCHEMA_VERSION: ClassVar[int] = 1


class _Messages:
    """Safe public messages exposed via ``AdapterCodegenError.safe_message``."""

    REQUEST_INVALID: ClassVar[str] = "Adapter codegen request failed validation"
    AUDIT_FAILED: ClassVar[str] = (
        "Generated adapter source failed the local allowlist audit"
    )


class _ForbiddenPattern:
    """Identifier-level patterns banned in any generated adapter source."""

    TOKENS: ClassVar[tuple[str, ...]] = (
        "window",
        "document",
        "localStorage",
        "sessionStorage",
        "XMLHttpRequest",
        "EventSource",
        "WebSocket",
        "navigator",
        "history",
        "fetch",
        "eval",
        "require",
        "process",
        "global",
        "globalThis",
        "child_process",
        "fs",
    )

    LITERAL: ClassVar[tuple[str, ...]] = (
        "new Function",
        "import(",
        "require(",
    )

    @classmethod
    def violations(cls, source: str) -> list[str]:
        found: list[str] = []
        for token in cls.TOKENS:
            pattern = re.compile(
                r"(?<![A-Za-z0-9_$])" + re.escape(token) + r"(?![A-Za-z0-9_$])"
            )
            if pattern.search(source):
                found.append(token)
        for literal in cls.LITERAL:
            if literal in source:
                found.append(literal)
        return found


class _ImportAllowlist:
    """Module specifiers a generated adapter is permitted to import."""

    ALLOWED: ClassVar[frozenset[str]] = frozenset(
        {
            "react",
            "@enterprise-search/design-system",
        }
    )

    IMPORT_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"^\s*import\s+[^;]*?from\s+[\"\']([^\"\']+)[\"\']\s*;?\s*$",
        re.MULTILINE,
    )

    @classmethod
    def disallowed_specifiers(cls, source: str) -> list[str]:
        disallowed: list[str] = []
        for match in cls.IMPORT_RE.finditer(source):
            specifier = match.group(1)
            if specifier not in cls.ALLOWED:
                disallowed.append(specifier)
        return disallowed


class AdapterCodegenError(Exception):
    """Typed domain error raised when codegen produces or accepts invalid input."""

    def __init__(self, safe_message: str) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


class AdapterAllowlistAuditor:
    """Defensive check that the generator never produces sandbox-unsafe source."""

    @classmethod
    def audit(cls, source: str) -> None:
        if not isinstance(source, str) or not source:
            raise AdapterCodegenError(_Messages.AUDIT_FAILED)
        disallowed = _ImportAllowlist.disallowed_specifiers(source)
        violations = _ForbiddenPattern.violations(source)
        if disallowed or violations:
            raise AdapterCodegenError(_Messages.AUDIT_FAILED)
        if "export const adapter" not in source:
            raise AdapterCodegenError(_Messages.AUDIT_FAILED)
        if "export const renderCurrent" not in source:
            raise AdapterCodegenError(_Messages.AUDIT_FAILED)
        if "export const renderDiff" not in source:
            raise AdapterCodegenError(_Messages.AUDIT_FAILED)


class RenderAdapterGenerator:
    """Capability that builds a tier-2 adapter source string from a template choice."""

    def __init__(
        self,
        *,
        producer: "RuntimeEventProducer | None" = None,
        run: "RunRecord | None" = None,
        clock: "callable" = lambda: datetime.now(timezone.utc),  # type: ignore[valid-type]
        source: StreamEventSource = StreamEventSource.RUNTIME,
    ) -> None:
        self._producer = producer
        self._run = run
        self._clock = clock
        self._source = source

    @property
    def name(self) -> str:
        return "generateRenderAdapter"

    async def generate(
        self,
        *,
        scheme: str,
        sample_state: Mapping[str, object] | SampleState | None,
        layout_template: LayoutTemplate | str,
    ) -> AdapterCodegenResult:
        """Produce an adapter source string and optionally emit an event for it."""

        try:
            if isinstance(sample_state, SampleState):
                sample = sample_state
            else:
                sample = SampleState.from_mapping(sample_state)
            request = AdapterCodegenRequest(
                scheme=scheme,
                layout=layout_template,
                sample_state=sample,
            )
        except ValidationError as exc:
            raise AdapterCodegenError(_first_safe_message(exc)) from exc

        generated_at = self._clock().isoformat()
        adapter_source = AdapterSourceBuilder.build(
            scheme=request.scheme,
            layout=request.layout,
            sample_state=request.sample_state,
            generated_at=generated_at,
            generator_model=_GeneratorIdentity.MODEL_NAME,
        )
        AdapterAllowlistAuditor.audit(adapter_source)

        result = AdapterCodegenResult(
            scheme=request.scheme,
            layout=request.layout,
            schema_version=_GeneratorIdentity.SCHEMA_VERSION,
            adapter_source=adapter_source,
            generated_at=generated_at,
            generator_model=_GeneratorIdentity.MODEL_NAME,
        )
        await self._maybe_emit(result)
        return result

    async def _maybe_emit(
        self, result: AdapterCodegenResult
    ) -> "RuntimeEventEnvelope | None":
        if self._producer is None or self._run is None:
            return None
        return await self._producer.append_api_event(
            run=self._run,
            source=self._source,
            event_type=RuntimeApiEventType.ADAPTER_GENERATED,
            payload=result.payload(),
        )


def _first_safe_message(error: ValidationError) -> str:
    for entry in error.errors():
        message = entry.get("msg")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return _Messages.REQUEST_INVALID


__all__ = [
    "AdapterAllowlistAuditor",
    "AdapterCodegenError",
    "RenderAdapterGenerator",
]
