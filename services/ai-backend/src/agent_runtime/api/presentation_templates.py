"""Deterministic templates that render UI event-card presentation without an LLM call.

``DeterministicTemplates`` covers events derivable entirely from payload (approval,
auth, error, tool_call_delta). ``ToolTemplateRenderer`` fills title/summary
placeholders for tools that registered a display template. ``PayloadProjector``
synthesises ``result_preview`` rows for result events. Agent-supplied
``_display_*`` fields override synthetic templates and fill the minimal envelope.
The minimal envelope fallback always returns something so no card renders empty.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from string import Formatter

from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate
from runtime_api.schemas import RuntimeApiEventType

JsonObject = dict[str, object]


class _StatusLabel:
    RUNNING = "Running"
    WAITING = "Waiting for permission"
    DONE = "Done"
    FAILED = "Failed"


class _Kind:
    PROGRESS = "progress"
    RESULT = "result"
    APPROVAL = "approval"
    AUTH = "auth"
    ERROR = "error"


class _ErrorMessage:
    """Static title/summary tuples mapped from typed error codes.

    Add codes here as new failure modes show up in payload["error_code"].
    Keys are matched case-insensitively. ``DEFAULT`` is the catch-all.
    """

    TIMEOUT = ("Step timed out", "This step took too long and was stopped.")
    PERMISSION_DENIED = (
        "Not allowed",
        "0xCopilot isn't allowed to do this.",
    )
    EXTERNAL_SERVICE_ERROR = (
        "Service unavailable",
        "The connected app didn't respond.",
    )
    TOOL_EXCEPTION = (
        "Step failed",
        "The tool reported an error and didn't return a result.",
    )
    TOOL_TIMEOUT = (
        "Step timed out",
        "The tool took too long to respond and was stopped.",
    )
    TOOL_RUN_TIMEOUT = (
        "Step timed out",
        "0xCopilot ran out of time before this tool finished.",
    )
    TOOL_RUN_ABANDONED = (
        "Step interrupted",
        "0xCopilot lost track of this step and stopped it.",
    )
    TOOL_CANCELLED = (
        "Step cancelled",
        "This step was cancelled before it could finish.",
    )
    RUN_WORKER_LOST = (
        "Run interrupted",
        "0xCopilot stopped this run because the worker became unresponsive.",
    )
    DEFAULT = ("Step failed", "0xCopilot couldn't complete this step.")

    @classmethod
    def for_code(cls, code: str | None) -> tuple[str, str]:
        if not isinstance(code, str):
            return cls.DEFAULT
        upper = code.strip().upper().replace("-", "_")
        return getattr(cls, upper, cls.DEFAULT)


class _Identifier:
    """Humanizer for snake/slug identifiers used as display fallbacks."""

    _STRIP_PREFIXES = ("mcp_",)
    _STRIP_SUFFIXES = ("_com", "_io", "_app")

    @classmethod
    def humanize(cls, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text:
            return None
        lowered = text.lower()
        for prefix in cls._STRIP_PREFIXES:
            if lowered.startswith(prefix):
                text = text[len(prefix) :]
                lowered = text.lower()
                break
        for suffix in cls._STRIP_SUFFIXES:
            if lowered.endswith(suffix):
                text = text[: -len(suffix)]
                break
        words = [word for word in text.replace("-", "_").split("_") if word]
        if not words:
            return value.strip()
        return " ".join(word.capitalize() for word in words)


class DeterministicTemplates:
    """Render activity-card presentations from event payloads without an LLM.

    All builders return a dict that satisfies ``RuntimeEventPresentation``.
    They are pure: same inputs → same outputs, no I/O.
    """

    HANDLED = frozenset(
        {
            RuntimeApiEventType.APPROVAL_RESOLVED,
            RuntimeApiEventType.APPROVAL_REQUESTED,
            RuntimeApiEventType.MCP_AUTH_REQUIRED,
            RuntimeApiEventType.ERROR,
            RuntimeApiEventType.RUN_FAILED,
            RuntimeApiEventType.TOOL_CALL_DELTA,
        }
    )

    @classmethod
    def render(
        cls,
        *,
        event_type: RuntimeApiEventType,
        payload: Mapping[str, object],
        timeline_fields: Mapping[str, object],
        group_key: str | None,
    ) -> JsonObject | None:
        if event_type is RuntimeApiEventType.APPROVAL_RESOLVED:
            return cls._approval_resolved(payload, group_key)
        if event_type is RuntimeApiEventType.APPROVAL_REQUESTED:
            return cls._approval_requested(payload, group_key)
        if event_type is RuntimeApiEventType.MCP_AUTH_REQUIRED:
            return cls._mcp_auth_required(payload, group_key)
        if event_type is RuntimeApiEventType.RUN_FAILED:
            return cls._run_failed(payload, group_key)
        if event_type is RuntimeApiEventType.ERROR:
            return cls._error(payload, group_key)
        if event_type is RuntimeApiEventType.TOOL_CALL_DELTA:
            return cls._tool_call_delta(payload, timeline_fields, group_key)
        return None

    @classmethod
    def _approval_resolved(
        cls, payload: Mapping[str, object], group_key: str | None
    ) -> JsonObject:
        status = cls._lower_text(payload.get("status"))
        tool = _Identifier.humanize(payload.get("tool_name")) or "this action"
        if status in {"approved", "granted", "allowed"}:
            return cls._envelope(
                title="Permission granted",
                summary=f"You allowed {tool}.",
                status_label=_StatusLabel.DONE,
                kind=_Kind.APPROVAL,
                group_key=group_key,
            )
        return cls._envelope(
            title="Permission denied",
            summary=f"You blocked {tool}.",
            status_label=_StatusLabel.FAILED,
            kind=_Kind.APPROVAL,
            group_key=group_key,
        )

    @classmethod
    def _approval_requested(
        cls, payload: Mapping[str, object], group_key: str | None
    ) -> JsonObject:
        tool = _Identifier.humanize(payload.get("tool_name")) or "an action"
        entity = _Identifier.humanize(
            payload.get("display_name")
        ) or _Identifier.humanize(payload.get("server_name"))
        if entity:
            summary = (
                f"0xCopilot wants to run {tool} on {entity}. "
                "Approve or deny to continue."
            )
        else:
            summary = f"0xCopilot wants to run {tool}. Approve or deny to continue."
        return cls._envelope(
            title=f"Allow {tool}?",
            summary=summary,
            status_label=_StatusLabel.WAITING,
            kind=_Kind.APPROVAL,
            group_key=group_key,
            primary_entity=entity,
        )

    @classmethod
    def _mcp_auth_required(
        cls, payload: Mapping[str, object], group_key: str | None
    ) -> JsonObject:
        entity = (
            _Identifier.humanize(payload.get("display_name"))
            or _Identifier.humanize(payload.get("server_name"))
            or "this app"
        )
        return cls._envelope(
            title=f"Connect {entity}",
            summary=f"Sign in to {entity} so 0xCopilot can continue.",
            status_label=_StatusLabel.WAITING,
            kind=_Kind.AUTH,
            group_key=group_key,
            primary_entity=entity if entity != "this app" else None,
        )

    @classmethod
    def _run_failed(
        cls, payload: Mapping[str, object], group_key: str | None
    ) -> JsonObject:
        title, summary = _ErrorMessage.for_code(
            cls._first_text(payload, ("error_code", "code"))
        )
        return cls._envelope(
            title=title,
            summary=summary,
            status_label=_StatusLabel.FAILED,
            kind=_Kind.ERROR,
            group_key=group_key,
        )

    @classmethod
    def _error(cls, payload: Mapping[str, object], group_key: str | None) -> JsonObject:
        title, summary = _ErrorMessage.for_code(
            cls._first_text(payload, ("error_code", "code"))
        )
        return cls._envelope(
            title=title,
            summary=summary,
            status_label=_StatusLabel.FAILED,
            kind=_Kind.ERROR,
            group_key=group_key,
        )

    @classmethod
    def _tool_call_delta(
        cls,
        payload: Mapping[str, object],
        timeline_fields: Mapping[str, object],
        group_key: str | None,
    ) -> JsonObject:
        tool = _Identifier.humanize(payload.get("tool_name"))
        # Prefer the projector's display_title if present, fall back to humanized tool name.
        title_hint = cls._text(timeline_fields.get("display_title"))
        title = title_hint or (f"Working on {tool}" if tool else "Working on step")
        # Only use payload.message — payload.delta is the raw streaming JSON-arg
        # token (`{"`, `":`, `"}`, etc.) and is not user-readable.
        progress_message = cls._text(payload.get("message"))
        return cls._envelope(
            title=title[:80],
            summary=(progress_message[:240] if progress_message else None),
            status_label=_StatusLabel.RUNNING,
            kind=_Kind.PROGRESS,
            group_key=group_key,
            primary_entity=tool,
            action_label=progress_message[:60] if progress_message else None,
        )

    @staticmethod
    def _envelope(
        *,
        title: str,
        summary: str | None,
        status_label: str,
        kind: str,
        group_key: str | None,
        primary_entity: str | None = None,
        action_label: str | None = None,
        result_preview: list[JsonObject] | None = None,
    ) -> JsonObject:
        envelope: JsonObject = {
            "title": title,
            "status_label": status_label,
            "kind": kind,
            "debug_label": "Tool details",
        }
        if summary is not None:
            envelope["summary"] = summary
        if group_key is not None:
            envelope["group_key"] = group_key
        if primary_entity is not None:
            envelope["primary_entity"] = primary_entity
        if action_label is not None:
            envelope["action_label"] = action_label
        if result_preview:
            envelope["result_preview"] = result_preview
        return envelope

    @staticmethod
    def _text(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None

    @staticmethod
    def _lower_text(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip().lower()
        return stripped or None

    @classmethod
    def _first_text(
        cls, payload: Mapping[str, object], keys: tuple[str, ...]
    ) -> str | None:
        for key in keys:
            text = cls._text(payload.get(key))
            if text is not None:
                return text
        return None


class ToolTemplateRenderer:
    """Render presentations from a tool author's `ToolDisplayTemplate`.

    Used when the tool name on the event resolves (via the injected registries)
    to a registered display template. Returns ``None`` if the template's
    placeholders cannot all be satisfied from the payload — in that case the
    LLM path runs instead.
    """

    _RESULT_EVENT_TYPES = frozenset(
        {
            RuntimeApiEventType.TOOL_RESULT,
            RuntimeApiEventType.TOOL_CALL_COMPLETED,
        }
    )
    _START_EVENT_TYPES = frozenset(
        {
            RuntimeApiEventType.TOOL_CALL,
            RuntimeApiEventType.TOOL_CALL_STARTED,
            RuntimeApiEventType.TOOL_CALL_DELTA,
            RuntimeApiEventType.PROGRESS,
        }
    )

    @classmethod
    def render(
        cls,
        *,
        event_type: RuntimeApiEventType,
        payload: Mapping[str, object],
        template: ToolDisplayTemplate,
        group_key: str | None,
    ) -> JsonObject | None:
        if event_type in cls._RESULT_EVENT_TYPES:
            title_template = template.result_title_template or template.title_template
            summary_template = (
                template.result_summary_template or template.summary_template
            )
            kind = _Kind.RESULT
            status_label = _StatusLabel.DONE
        elif event_type in cls._START_EVENT_TYPES:
            title_template = template.title_template
            summary_template = template.summary_template
            kind = _Kind.PROGRESS
            status_label = _StatusLabel.RUNNING
        else:
            return None

        title = cls._safe_format(title_template, payload)
        if title is None:
            return None
        summary = (
            cls._safe_format(summary_template, payload) if summary_template else None
        )
        envelope: JsonObject = {
            "title": title[:80],
            "status_label": status_label,
            "kind": kind,
            "debug_label": "Tool details",
        }
        if summary is not None:
            envelope["summary"] = summary[:240]
        if group_key is not None:
            envelope["group_key"] = group_key
        primary_entity = cls._safe_text(payload.get("display_name")) or cls._safe_text(
            payload.get("connector")
        )
        if primary_entity is not None:
            envelope["primary_entity"] = primary_entity[:80]
        if event_type in cls._RESULT_EVENT_TYPES:
            preview = PayloadProjector.project(payload=payload, template=template)
            if preview:
                envelope["result_preview"] = preview
        return envelope

    @staticmethod
    def _safe_format(template: str, payload: Mapping[str, object]) -> str | None:
        """Format ``template`` against ``payload`` and return None if a key is missing."""

        formatter = Formatter()
        rendered_parts: list[str] = []
        for literal_text, field_name, format_spec, conversion in formatter.parse(
            template
        ):
            if literal_text:
                rendered_parts.append(literal_text)
            if field_name is None:
                continue
            try:
                value, _ = formatter.get_field(field_name, (), payload)
            except (KeyError, IndexError, AttributeError, TypeError):
                return None
            if value is None or (isinstance(value, str) and not value.strip()):
                return None
            converted = formatter.convert_field(value, conversion)
            try:
                rendered_parts.append(
                    formatter.format_field(converted, format_spec or "")
                )
            except (TypeError, ValueError):
                return None
        return "".join(rendered_parts).strip() or None

    @staticmethod
    def _safe_text(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None


class PayloadProjector:
    """Synthesize result preview rows from a tool's output payload.

    The projector tries (in order):

    1. The tool's declared ``result_preview_path`` + ``result_preview_row``
       on its ``ToolDisplayTemplate``.
    2. A small list of common payload shapes — ``output``, ``results``,
       ``items``, ``rows``, ``matches``, ``documents``, or a top-level list.
       Each row is mapped to ``{title, subtitle, url, badge}`` via
       field-name heuristics.

    Output is capped at five rows. Strings are sanitized and length-bounded
    so the result satisfies ``RuntimeEventPresentationPreviewRow``.
    """

    MAX_ROWS = 5
    _CONTAINER_KEYS: tuple[str, ...] = (
        "results",
        "items",
        "rows",
        "matches",
        "documents",
        "sources",
        "output",
    )
    _TITLE_KEYS: tuple[str, ...] = ("title", "name", "subject", "filename", "headline")
    _SUBTITLE_KEYS: tuple[str, ...] = (
        "snippet",
        "description",
        "preview",
        "summary",
        "excerpt",
    )
    _URL_KEYS: tuple[str, ...] = ("url", "link", "href", "permalink")
    _BADGE_KEYS: tuple[str, ...] = ("source", "connector", "kind", "type")

    @classmethod
    def project(
        cls,
        *,
        payload: Mapping[str, object],
        template: ToolDisplayTemplate | None = None,
    ) -> list[JsonObject]:
        rows = cls._declared_rows(payload, template) or cls._heuristic_rows(payload)
        preview: list[JsonObject] = []
        for row in rows[: cls.MAX_ROWS]:
            projected = cls._project_row(row, template)
            if projected is not None:
                preview.append(projected)
        return preview

    @classmethod
    def _declared_rows(
        cls,
        payload: Mapping[str, object],
        template: ToolDisplayTemplate | None,
    ) -> list[Mapping[str, object]]:
        if template is None or not template.result_preview_path:
            return []
        rows = cls._walk_path(payload, template.result_preview_path)
        if isinstance(rows, list):
            return [item for item in rows if isinstance(item, Mapping)]
        return []

    @classmethod
    def _heuristic_rows(
        cls, payload: Mapping[str, object]
    ) -> list[Mapping[str, object]]:
        candidates: list[object] = []
        output = payload.get("output")
        if output is not None:
            candidates.append(output)
        candidates.append(payload)
        for candidate in candidates:
            parsed = cls._parse_value(candidate)
            if isinstance(parsed, list):
                rows = [item for item in parsed if isinstance(item, Mapping)]
                if rows:
                    return rows
            if isinstance(parsed, Mapping):
                for key in cls._CONTAINER_KEYS:
                    nested = parsed.get(key)
                    parsed_nested = cls._parse_value(nested)
                    if isinstance(parsed_nested, list):
                        rows = [
                            item for item in parsed_nested if isinstance(item, Mapping)
                        ]
                        if rows:
                            return rows
        return []

    @classmethod
    def _project_row(
        cls,
        row: Mapping[str, object],
        template: ToolDisplayTemplate | None,
    ) -> JsonObject | None:
        declared = template.result_preview_row if template else None
        title = cls._field_value(row, declared, "title", cls._TITLE_KEYS)
        if title is None:
            return None
        projected: JsonObject = {"title": cls._clamp(title, 120)}
        subtitle = cls._field_value(row, declared, "subtitle", cls._SUBTITLE_KEYS)
        if subtitle is not None and subtitle != title:
            projected["subtitle"] = cls._clamp(subtitle, 240)
        url = cls._field_value(row, declared, "url", cls._URL_KEYS)
        if url is not None and url.startswith(("http://", "https://")):
            projected["url"] = cls._clamp(url, 500)
        badge = cls._field_value(row, declared, "badge", cls._BADGE_KEYS)
        if badge is not None:
            projected["badge"] = cls._clamp(badge, 40)
        return projected

    @classmethod
    def _field_value(
        cls,
        row: Mapping[str, object],
        declared: dict[str, str] | None,
        slot: str,
        fallback_keys: tuple[str, ...],
    ) -> str | None:
        keys: tuple[str, ...]
        if declared and isinstance(declared.get(slot), str):
            keys = (declared[slot],)
        else:
            keys = fallback_keys
        for key in keys:
            value = row.get(key)
            text = cls._safe_text(value)
            if text is not None:
                return text
        return None

    @classmethod
    def _walk_path(cls, value: object, path: str) -> object:
        current: object = value
        for segment in path.split("."):
            segment = segment.strip()
            if not segment:
                continue
            if isinstance(current, str):
                current = cls._parse_value(current)
            if isinstance(current, Mapping):
                current = current.get(segment)
            else:
                return None
        return cls._parse_value(current)

    @staticmethod
    def _parse_value(value: object) -> object:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value

    @staticmethod
    def _safe_text(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        text = " ".join(value.replace("<", "").replace(">", "").split())
        if not text or "/large_tool_results/" in text:
            return None
        return text

    @staticmethod
    def _clamp(text: str, limit: int) -> str:
        return text if len(text) <= limit else text[:limit]
