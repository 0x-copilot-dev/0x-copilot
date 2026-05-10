"""Card presentation metadata for user-facing runtime events.

Polish-removal Phase 4 (docs/refactor/01-presentation-polish-removal.md).
The generator is now fully deterministic — no LLM, no async polish, no
``PRESENTATION_UPDATED`` follow-up event. Resolution order:

1. **Deterministic templates** — events whose presentation is fully derivable
   from payload (approval / auth / error / tool_call_delta).
2. **Tool template** — when a tool registers a :class:`ToolDisplayTemplate`
   (Phase 1) or the MCP descriptor synthesiser builds one (Phase 2.A/B),
   the renderer fills the template from the payload.
3. **Agent-supplied ``_display_*``** (Phase 3) — when the matched template
   is ``synthetic=True`` (or absent), the agent's optional
   ``_display_title`` / ``_display_summary`` from tool args wins. Author
   templates always beat agent override.
4. **Minimal envelope** — humanised tool name + status, with
   :class:`PayloadProjector` filling ``result_preview`` rows for result
   events. Always returns something so cards never render empty.

The full chain produces a complete envelope synchronously. There is no
asynchronous patch path; the envelope written with the event is final.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass

from pydantic import ValidationError

from agent_runtime.api.presentation_templates import (
    DeterministicTemplates,
    PayloadProjector,
    ToolTemplateRenderer,
    _ErrorMessage,
)
from agent_runtime.capabilities.middleware.display_metadata import (
    agent_display_from_payload,
)
from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate
from agent_runtime.execution.tool_outcomes import TOOL_FAILURE_STATUSES
from runtime_api.schemas import (
    RuntimeApiEventType,
    RuntimeEventPresentation,
)

JsonObject = dict[str, object]
ToolDisplayLookup = Callable[[str], ToolDisplayTemplate | None]


@dataclass
class PresentationGenerator:
    """Generate validated card presentation metadata from safe event context.

    Attributes:
        tool_display_lookup: Optional resolver from tool name → display
            template. Instance-level injection used by tests; production
            wires the per-run lookup via ``ToolDisplayLookupContext``.
    """

    tool_display_lookup: ToolDisplayLookup | None = None

    # Event types that get a presentation envelope at all. Events outside
    # this set (heartbeats, model deltas, lifecycle markers) return ``None``
    # so the FE never tries to render a card for them.
    _PRESENTATION_TARGET_EVENT_TYPES = frozenset(
        {
            RuntimeApiEventType.PROGRESS,
            RuntimeApiEventType.TOOL_CALL,
            RuntimeApiEventType.TOOL_CALL_STARTED,
            RuntimeApiEventType.TOOL_RESULT,
        }
    )

    # Polish-removal Phase 2.B (docs/refactor/01-presentation-polish-removal.md).
    # MCP tool calls flow through a single dispatcher tool. The runtime
    # emits events with ``payload.tool_name`` set to the dispatcher's name;
    # the *actual* MCP tool name lives nested inside ``payload.args``. Pin
    # the dispatcher name here so the resolution chain knows when to
    # extract instead of looking up the dispatcher itself.
    _MCP_DISPATCHER_TOOL_NAME = "call_mcp_tool"

    _RESULT_EVENT_TYPES = frozenset(
        {
            RuntimeApiEventType.TOOL_RESULT,
            RuntimeApiEventType.TOOL_CALL_COMPLETED,
        }
    )

    def preliminary_presentation_for_event(
        self,
        *,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
        metadata: JsonObject,
        timeline_fields: Mapping[str, object],
    ) -> JsonObject | None:
        """Return a presentation built from the deterministic chain.

        Always returns a usable envelope for tool / progress event types so
        cards never render empty. Returns ``None`` only for event types that
        have no card at all (heartbeats, model deltas).
        """

        explicit = self._validated(metadata.get("presentation"))
        if explicit is not None:
            return explicit

        group_key = self._group_key(payload, timeline_fields)

        deterministic = DeterministicTemplates.render(
            event_type=event_type,
            payload=payload,
            timeline_fields=timeline_fields,
            group_key=group_key,
        )
        if deterministic is not None:
            validated = self._validated(deterministic)
            if validated is not None:
                return validated

        tool_template = self._resolve_tool_template(payload)
        # Phase 2.B — promote inner MCP arguments to the top level for the
        # template + projector. No-op for non-dispatcher events. See
        # ``_effective_template_payload`` for the rationale.
        effective_payload = self._effective_template_payload(payload)
        if tool_template is not None:
            tool_rendered = ToolTemplateRenderer.render(
                event_type=event_type,
                payload=effective_payload,
                template=tool_template,
                group_key=group_key,
            )
            if tool_rendered is not None:
                # Phase 3.A — Tier-3 override: agent-supplied ``_display_*``
                # in tool args wins over a synthesised template, never over
                # an author-written one.
                self._apply_tier3_override(tool_rendered, payload, tool_template)
                validated = self._validated(tool_rendered)
                if validated is not None:
                    return validated

        if event_type not in self._PRESENTATION_TARGET_EVENT_TYPES:
            return None

        envelope = self._minimal_envelope(
            event_type=event_type,
            payload=effective_payload,
            timeline_fields=timeline_fields,
            group_key=group_key,
            template=tool_template,
        )
        # Phase 3.A — when no tool template fired, Tier-3 fills the envelope
        # title / summary if the agent supplied them.
        if envelope is not None:
            self._apply_tier3_override(envelope, payload, tool_template)
        return envelope

    def _resolve_tool_template(self, payload: JsonObject) -> ToolDisplayTemplate | None:
        name = self._effective_tool_name(payload)
        if name is None:
            return None
        # Instance-level injection (used by tests) wins over the per-run
        # ContextVar (set by the run / approval handler at handle() entry).
        # Either source returning ``None`` is treated the same as "no
        # template registered" — fall through to the minimal envelope.
        lookup = self.tool_display_lookup or ToolDisplayLookupContext.active()
        if lookup is None:
            return None
        try:
            return lookup(name)
        except Exception:
            logging.getLogger(__name__).warning(
                "Tool display lookup failed for %s", name, exc_info=True
            )
            return None

    @classmethod
    def _effective_tool_name(cls, payload: JsonObject) -> str | None:
        """Return the tool name the lookup should resolve against.

        For all events except the MCP dispatcher this is just
        ``payload.tool_name``. When the event is a ``call_mcp_tool``
        dispatcher invocation the actual MCP tool name lives inside
        ``payload.args.tool_name`` (Phase 2.B); we extract it so the
        synthesised MCP template resolves rather than the dispatcher
        itself (which has no meaningful copy on its own).
        """

        tool_name = payload.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            return None
        name = tool_name.strip()
        if name != cls._MCP_DISPATCHER_TOOL_NAME:
            return name
        args = payload.get("args")
        if not isinstance(args, Mapping):
            return name
        inner = args.get("tool_name")
        if not isinstance(inner, str) or not inner.strip():
            return name
        return inner.strip()

    @staticmethod
    def _apply_tier3_override(
        envelope: JsonObject,
        payload: JsonObject,
        template: ToolDisplayTemplate | None,
    ) -> None:
        """Mutate ``envelope`` in place: agent-supplied ``_display_*`` wins
        over a synthesised template (or a missing one); never wins over an
        author-written template.

        Read order: ``payload.args._display_title`` and
        ``payload.args._display_summary`` (same shape for regular tools
        and the ``call_mcp_tool`` dispatcher — see
        :func:`agent_display_from_payload` for the exact contract).
        """

        if template is not None and not template.synthetic:
            # Author-written template wins. Agent's ``_display_*`` is ignored.
            return
        title, summary = agent_display_from_payload(payload)
        if title is not None:
            envelope["title"] = title
        if summary is not None:
            envelope["summary"] = summary

    @classmethod
    def _effective_template_payload(cls, payload: JsonObject) -> JsonObject:
        """Promote inner MCP arguments to the top level for template render.

        The synthesised MCP template uses placeholders like ``{query}`` (the
        agent-supplied tool argument). For non-dispatcher events those
        placeholders already resolve against ``payload`` directly because
        the agent's args ARE the top-level payload keys. For dispatcher
        events the args are nested at ``payload.args.arguments``; without
        this promotion ``ToolTemplateRenderer._safe_format`` would fail
        every placeholder and fall back to the minimal envelope.

        Same rationale applies to ``result_preview_path`` walking — the
        synthesised path is e.g. ``"items"`` (a top-level key in the
        underlying tool's output), not the dispatcher-shaped nested form.
        """

        tool_name = payload.get("tool_name")
        if tool_name != cls._MCP_DISPATCHER_TOOL_NAME:
            return payload
        args = payload.get("args")
        if not isinstance(args, Mapping):
            return payload
        arguments = args.get("arguments")
        if isinstance(arguments, Mapping):
            return {**payload, **arguments}
        return payload

    def _minimal_envelope(
        self,
        *,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
        timeline_fields: Mapping[str, object],
        group_key: str | None,
        template: ToolDisplayTemplate | None,
    ) -> JsonObject | None:
        """Build a minimal envelope for tool events without a deterministic template.

        Title comes from the projector's display_title hint or a humanised
        tool name. Status / kind come from the event lifecycle.
        :class:`PayloadProjector` fills ``result_preview`` for result events
        when the payload has rows.
        """

        title_hint = self._first_text(timeline_fields, ("display_title",))
        tool_name = payload.get("tool_name")
        humanized_tool = (
            self._humanize_identifier(tool_name)
            if isinstance(tool_name, str) and tool_name.strip()
            else None
        )
        status = self._payload_status(payload)
        is_failed = status in TOOL_FAILURE_STATUSES or status == "error"
        is_result = not is_failed and (
            event_type in self._RESULT_EVENT_TYPES
            or status in {"completed", "complete", "done", "success", "succeeded"}
        )
        error_summary: str | None = None
        if is_failed:
            status_label = "Failed"
            kind = "error"
            error_code = self._first_text(payload, ("error_code",))
            error_title, error_summary_template = _ErrorMessage.for_code(error_code)
            # Prefer a tool-call-specific message when one is on the payload;
            # fall back to the typed error code's static copy.
            error_summary = (
                self._first_text(payload, ("error_message", "safe_message"))
                or error_summary_template
            )
            default_title = error_title
        elif is_result:
            status_label = "Done"
            kind = "result"
            default_title = humanized_tool or "Checked source"
        else:
            status_label = "Running"
            kind = "progress"
            default_title = humanized_tool or "Working on step"
        title = title_hint or default_title
        envelope: JsonObject = {
            "title": title[:80],
            "status_label": status_label,
            "kind": kind,
            "debug_label": "Tool details",
        }
        if error_summary is not None:
            envelope["summary"] = error_summary[:240]
        if group_key is not None:
            envelope["group_key"] = group_key
        if humanized_tool:
            envelope["primary_entity"] = humanized_tool[:80]
        # Skip the projector on failed results — error payloads typically
        # don't carry preview-able rows, and the heuristics could surface
        # noise (e.g. an `error.context` dict) that misleads the user.
        if event_type in self._RESULT_EVENT_TYPES and not is_failed:
            preview = PayloadProjector.project(payload=payload, template=template)
            if preview:
                envelope["result_preview"] = preview
        return self._validated(envelope)

    @staticmethod
    def _payload_status(payload: JsonObject) -> str:
        raw = payload.get("status")
        return raw.lower() if isinstance(raw, str) else ""

    @staticmethod
    def _first_text(source: Mapping[str, object], keys: tuple[str, ...]) -> str | None:
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @classmethod
    def _humanize_identifier(cls, value: str) -> str:
        text = value.strip()
        lowered = text.lower()
        if lowered.startswith("mcp_"):
            text = text[4:]
        for suffix in ("_com", "_io", "_app"):
            if text.lower().endswith(suffix):
                text = text[: -len(suffix)]
        words = [word for word in text.replace("-", "_").split("_") if word]
        if not words:
            return value.strip()
        return " ".join(word.capitalize() for word in words)

    @staticmethod
    def _group_key(
        payload: JsonObject, timeline_fields: Mapping[str, object]
    ) -> str | None:
        for key in ("source_tool_call_id", "call_id", "approval_id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        span_id = timeline_fields.get("span_id")
        return span_id if isinstance(span_id, str) and span_id else None

    @staticmethod
    def _validated(value: object) -> JsonObject | None:
        if not isinstance(value, Mapping):
            return None
        try:
            presentation = RuntimeEventPresentation.model_validate(value)
        except ValidationError:
            return None
        dumped = presentation.model_dump(mode="json", exclude_none=True)
        return PresentationGenerator._without_raw_protocol_terms(dumped)

    @classmethod
    def _without_raw_protocol_terms(cls, value: JsonObject) -> JsonObject:
        cleaned: JsonObject = {}
        for key, entry in value.items():
            if isinstance(entry, str):
                cleaned[key] = cls._clean_generated_text(entry)
            elif isinstance(entry, list):
                cleaned[key] = [
                    cls._without_raw_protocol_terms(dict(item))
                    if isinstance(item, Mapping)
                    else item
                    for item in entry
                ]
            else:
                cleaned[key] = entry
        return cleaned

    @staticmethod
    def _clean_generated_text(value: str) -> str:
        if "/large_tool_results/" in value:
            return "Large result saved for internal inspection."
        words = value.replace("mcp_", "").replace("_com", "")
        return " ".join(words.split())


_TOOL_DISPLAY_LOOKUP_CTX: ContextVar[ToolDisplayLookup | None] = ContextVar(
    "tool_display_lookup",
    default=None,
)


class ToolDisplayLookupContext:
    """Per-run binding for the active tool-display-template lookup.

    The run / approval handler binds a lookup callable at ``handle()`` entry
    so every ``RuntimeEventProducer.append_*`` call made during that run
    consults the per-run tool registry without the producer needing a
    direct reference to it. Mirrors the ``CitationLedger.bind_for_run``
    pattern so the binding is inherited by ``asyncio.Task`` children
    spawned from the run's context.

    Resolution order in :meth:`PresentationGenerator._resolve_tool_template`:
    instance-level ``tool_display_lookup`` (used by unit tests) wins; this
    ContextVar is the production fallback.
    """

    @classmethod
    def bind_for_run(cls, lookup: ToolDisplayLookup) -> object:
        """Set the active lookup; return the previous token for restoration."""

        return _TOOL_DISPLAY_LOOKUP_CTX.set(lookup)

    @classmethod
    def unbind(cls, token: object) -> None:
        """Restore the previous binding. Safe to call with the bind result."""

        _TOOL_DISPLAY_LOOKUP_CTX.reset(token)  # type: ignore[arg-type]

    @classmethod
    def active(cls) -> ToolDisplayLookup | None:
        """Return the active lookup or ``None`` (fallback / test helper)."""

        return _TOOL_DISPLAY_LOOKUP_CTX.get(None)
