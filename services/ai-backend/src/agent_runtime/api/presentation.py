"""Card presentation metadata for user-facing runtime events.

The generator runs in three layers:

1. Deterministic templates — events whose presentation is fully derivable
   from payload (approval/auth/error/delta) skip the LLM entirely.
2. Tool author templates — when a tool registers a `ToolDisplayTemplate`
   on its `ToolCard` / `McpServerCard` / `McpToolDescriptor`, the renderer
   fills the template from the payload and skips the LLM.
3. LLM path — for tool/progress events without registered templates, a
   small fast OpenAI model (default `gpt-4.1-nano`, configured via
   `RUNTIME_PRESENTATION_MODEL`) returns a strict-schema structured output.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, ValidationError

from agent_runtime.api.presentation_templates import (
    DeterministicTemplates,
    PresentationOutput,
    ToolTemplateRenderer,
)
from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate
from agent_runtime.execution.contracts import ModelConfig, StreamEventSource
from agent_runtime.execution.deep_agent_builder import build_chat_model
from agent_runtime.settings import RuntimePresentationSettings
from runtime_api.schemas import (
    RunRecord,
    RuntimeApiEventType,
    RuntimeEventPresentation,
)

JsonObject = dict[str, object]
LlmPresenter = Callable[[str], object | Awaitable[object]]
ToolDisplayLookup = Callable[[str], ToolDisplayTemplate | None]


@dataclass
class PresentationGenerator:
    """Generate validated card presentation metadata from safe event context.

    Attributes:
        presentation_settings: Pinned model + timeout for the LLM path. When
            ``None``, defaults to ``RuntimePresentationSettings()`` (gpt-4.1-nano).
        llm_factory: Builds the small chat model. Override in tests to inject
            a fake; production passes through ``build_chat_model``.
        presenter: Test seam — when set, called instead of the structured-output
            LLM path. Receives the prompt string and returns a dict (or awaitable).
        tool_display_lookup: Optional resolver from tool name → display template.
            When the resolver returns a template, the LLM is skipped entirely.
    """

    presentation_settings: RuntimePresentationSettings | None = None
    llm_factory: Callable[[ModelConfig], BaseChatModel] = build_chat_model
    presenter: LlmPresenter | None = None
    tool_display_lookup: ToolDisplayLookup | None = None
    cache: dict[str, JsonObject] = field(default_factory=dict)
    _cached_model: BaseChatModel | None = field(default=None, init=False, repr=False)

    # Event types that go through the LLM path when no template matches.
    # Deterministic event types (approvals, auth, errors, deltas) are handled
    # by `DeterministicTemplates` and never reach the LLM.
    llm_eligible_event_types = frozenset(
        {
            RuntimeApiEventType.PROGRESS,
            RuntimeApiEventType.TOOL_CALL,
            RuntimeApiEventType.TOOL_CALL_STARTED,
            RuntimeApiEventType.TOOL_RESULT,
        }
    )

    async def presentation_for_event(
        self,
        *,
        run: RunRecord,
        event_type: RuntimeApiEventType,
        source: StreamEventSource,
        payload: JsonObject,
        metadata: JsonObject,
        timeline_fields: Mapping[str, object],
    ) -> JsonObject | None:
        """Return validated presentation JSON (preliminary + LLM enrichment).

        Backwards-compatible single-call entry point. Producers should prefer
        ``preliminary_presentation_for_event`` (synchronous, no LLM) followed
        by a background ``enrich_presentation_for_event`` so events emit to
        the SSE stream within milliseconds instead of blocking on the LLM.
        """

        preliminary = self.preliminary_presentation_for_event(
            event_type=event_type,
            payload=payload,
            metadata=metadata,
            timeline_fields=timeline_fields,
        )
        if not self.event_eligible_for_enrichment(event_type, payload, metadata):
            return preliminary
        enriched = await self.enrich_presentation_for_event(
            run=run,
            event_type=event_type,
            source=source,
            payload=payload,
            metadata=metadata,
            timeline_fields=timeline_fields,
        )
        return enriched or preliminary

    def preliminary_presentation_for_event(
        self,
        *,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
        metadata: JsonObject,
        timeline_fields: Mapping[str, object],
    ) -> JsonObject | None:
        """Return a presentation built from templates only (no LLM call).

        For LLM-eligible event types with no template match, returns the
        deterministic ``_fallback`` so the card still renders something.
        Returns ``None`` for event types that have no presentation at all.
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
        if tool_template is not None:
            tool_rendered = ToolTemplateRenderer.render(
                event_type=event_type,
                payload=payload,
                template=tool_template,
                group_key=group_key,
            )
            if tool_rendered is not None:
                validated = self._validated(tool_rendered)
                if validated is not None:
                    return validated

        if event_type not in self.llm_eligible_event_types:
            return None

        # LLM-eligible with no template: render a low-confidence fallback so
        # the SSE stream gets a card immediately. The background enrichment
        # task will replace it via PRESENTATION_UPDATED.
        context = self._context(
            event_type=event_type,
            source=StreamEventSource.SYSTEM,
            payload=payload,
            metadata=metadata,
            timeline_fields=timeline_fields,
        )
        return self._fallback(context)

    def event_eligible_for_enrichment(
        self,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> bool:
        """Return whether this event should trigger a background LLM enrichment.

        False if a deterministic / tool template already produced a high-confidence
        card, or if the event type isn't LLM-eligible at all.
        """

        if event_type not in self.llm_eligible_event_types:
            return False
        if metadata.get("presentation") is not None:
            return False
        if event_type in DeterministicTemplates.HANDLED:
            return False
        if self._resolve_tool_template(payload) is not None:
            return False
        return True

    async def enrich_presentation_for_event(
        self,
        *,
        run: RunRecord,
        event_type: RuntimeApiEventType,
        source: StreamEventSource,
        payload: JsonObject,
        metadata: JsonObject,
        timeline_fields: Mapping[str, object],
    ) -> JsonObject | None:
        """Run only the LLM-backed presentation path. Returns ``None`` on miss."""

        if event_type not in self.llm_eligible_event_types:
            return None

        group_key = self._group_key(payload, timeline_fields)
        context = self._context(
            event_type=event_type,
            source=source,
            payload=payload,
            metadata=metadata,
            timeline_fields=timeline_fields,
        )
        cache_key = json.dumps(
            {
                "run_id": run.run_id,
                "event_type": event_type.value,
                "call_id": payload.get("call_id"),
                "approval_id": payload.get("approval_id"),
                "status": payload.get("status"),
            },
            sort_keys=True,
            default=str,
        )
        if cache_key in self.cache:
            return self.cache[cache_key]

        generated = await self._generate(context)
        validated = self._validated(generated)
        if validated is None:
            return None
        enriched = self._with_deterministic_fields(validated, group_key=group_key)
        self.cache[cache_key] = enriched
        return enriched

    async def _generate(self, context: JsonObject) -> object:
        prompt = self._prompt(context)
        if self.presenter is not None:
            result = self.presenter(prompt)
            if inspect.isawaitable(result):
                return await result
            return result
        settings = self.presentation_settings or RuntimePresentationSettings()
        try:
            structured = self._structured_model(settings)
            response = await asyncio.wait_for(
                structured.ainvoke(
                    [
                        SystemMessage(
                            content=(
                                "You write concise, plain-text UI card metadata "
                                "for an enterprise assistant. Never include raw "
                                "IDs, protocol names, JSON, markdown, or HTML."
                            )
                        ),
                        HumanMessage(content=prompt),
                    ]
                ),
                timeout=settings.timeout_seconds,
            )
        except (TimeoutError, asyncio.TimeoutError):
            logging.getLogger(__name__).warning(
                "LLM presentation generation timed out after %ss",
                settings.timeout_seconds,
            )
            return None
        except Exception:
            logging.getLogger(__name__).warning(
                "LLM presentation generation failed", exc_info=True
            )
            return None
        if isinstance(response, PresentationOutput):
            return response.model_dump(mode="json", exclude_none=True)
        if isinstance(response, Mapping):
            return dict(response)
        return None

    def _structured_model(
        self, settings: RuntimePresentationSettings
    ) -> Runnable[LanguageModelInput, BaseModel | dict[str, object]]:
        if self._cached_model is None:
            self._cached_model = self.llm_factory(
                ModelConfig(
                    provider="openai",
                    model_name=settings.model_name,
                    max_input_tokens=128_000,
                    timeout_seconds=settings.timeout_seconds,
                    temperature=0,
                    supports_streaming=False,
                )
            )
        return cast(
            Runnable[LanguageModelInput, BaseModel | dict[str, object]],
            self._cached_model.with_structured_output(
                PresentationOutput, method="json_schema", strict=True
            ),
        )

    @classmethod
    def _prompt(cls, context: JsonObject) -> str:
        return (
            "Create user-facing activity card metadata for this safe runtime event.\n"
            "Do not include raw IDs, protocol names, server IDs, JSON, markdown, or HTML.\n"
            "Do not decide permissions or button labels.\n"
            "Return only the structured fields requested by the schema.\n"
            f"Safe event context:\n{json.dumps(context, sort_keys=True, default=str)}"
        )

    @classmethod
    def _context(
        cls,
        *,
        event_type: RuntimeApiEventType,
        source: StreamEventSource,
        payload: JsonObject,
        metadata: JsonObject,
        timeline_fields: Mapping[str, object],
    ) -> JsonObject:
        agent_intent = metadata.get("agent_intent_hint")
        context: JsonObject = {
            "event_type": event_type.value,
            "source": source.value,
            "activity_kind": timeline_fields.get("activity_kind"),
            "status": timeline_fields.get("status") or payload.get("status"),
            "title_hint": timeline_fields.get("display_title"),
            "summary_hint": timeline_fields.get("summary"),
            "display_facts": cls._display_facts(payload),
            "result_preview": cls._result_preview(payload),
            "safe_payload": cls._safe_json(payload),
            "safe_metadata": cls._safe_json(metadata),
            "group_key": cls._group_key(payload, timeline_fields),
        }
        if isinstance(agent_intent, str) and agent_intent.strip():
            context["agent_intent_hint"] = agent_intent.strip()[:300]
        return context

    @classmethod
    def _safe_json(cls, value: object) -> object:
        if isinstance(value, Mapping):
            result: JsonObject = {}
            for key, entry in value.items():
                if not isinstance(key, str) or cls._secretish(key):
                    continue
                if key in {
                    "server_id",
                    "approval_id",
                    "action_id",
                    "call_id",
                    "source_tool_call_id",
                    "server_name",
                    "tool_name",
                    "native_interrupt_id",
                }:
                    continue
                result[key] = cls._safe_json(entry)
            return result
        if isinstance(value, list | tuple):
            return [cls._safe_json(item) for item in value[:6]]
        if isinstance(value, str):
            if "/large_tool_results/" in value:
                return "Large result saved for internal inspection."
            return value[:500]
        if isinstance(value, int | float | bool) or value is None:
            return value
        return str(value)[:300]

    @classmethod
    def _display_facts(cls, payload: JsonObject) -> JsonObject:
        facts: JsonObject = {}
        entity = cls._connector_display_name(payload)
        action = cls._action_display_name(payload)
        status = payload.get("status")
        if entity:
            facts["primary_entity"] = entity
        if action:
            facts["action"] = action
        if isinstance(status, str) and status:
            facts["status"] = status
        if isinstance(payload.get("read_only"), bool):
            facts["read_only"] = payload["read_only"]
        risk_level = payload.get("risk_level")
        if isinstance(risk_level, str) and risk_level:
            facts["risk_level"] = risk_level
        message = payload.get("message")
        if isinstance(message, str) and message:
            facts["message_hint"] = cls._safe_text(message, 180)
        return facts

    @classmethod
    def _connector_display_name(cls, payload: JsonObject) -> str | None:
        for key in ("display_name", "primary_entity"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return cls._humanize_identifier(value)
        server_card = cls._server_card(payload)
        for key in ("display_name", "name"):
            value = server_card.get(key)
            if isinstance(value, str) and value.strip():
                return cls._humanize_identifier(value)
        server_name = payload.get("server_name")
        return (
            cls._humanize_identifier(server_name)
            if isinstance(server_name, str)
            else None
        )

    @classmethod
    def _action_display_name(cls, payload: JsonObject) -> str | None:
        tool_name = payload.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            return None
        entity = cls._connector_display_name(payload)
        normalized = tool_name
        if entity:
            token = "".join(
                character.lower() for character in entity if character.isalnum()
            )
            normalized = normalized.replace(f"{token}_", "")
        words = [
            word
            for word in normalized.replace("-", "_").split("_")
            if word and word.lower() not in {"mcp", "tool", "call"}
        ]
        return " ".join(words[:4]) or None

    @classmethod
    def _server_card(cls, payload: JsonObject) -> JsonObject:
        loaded_server = payload.get("loaded_server")
        if isinstance(loaded_server, Mapping):
            card = loaded_server.get("server_card")
            return dict(card) if isinstance(card, Mapping) else {}
        output = payload.get("output")
        if isinstance(output, Mapping):
            loaded = output.get("loaded_server")
            if isinstance(loaded, Mapping):
                card = loaded.get("server_card")
                return dict(card) if isinstance(card, Mapping) else {}
        return {}

    @classmethod
    def _result_preview(cls, payload: JsonObject) -> list[JsonObject]:
        rows = cls._rows_from_payload(payload)
        preview: list[JsonObject] = []
        for row in rows[:4]:
            title = cls._row_text(row, ("title", "name", "summary", "url", "link"))
            if not title:
                continue
            preview_row: JsonObject = {"title": title}
            subtitle = cls._row_text(
                row, ("snippet", "description", "content", "status")
            )
            if subtitle and subtitle != title:
                preview_row["subtitle"] = subtitle
            url = cls._row_text(row, ("url", "link"))
            if url and url.startswith(("http://", "https://")):
                preview_row["url"] = url
            badge = cls._row_text(row, ("source", "type", "status"))
            if badge:
                preview_row["badge"] = badge[:40]
            preview.append(preview_row)
        return preview

    @classmethod
    def _rows_from_payload(cls, payload: JsonObject) -> list[JsonObject]:
        candidates = [payload.get("output"), payload]
        for candidate in candidates:
            parsed = cls._parse_json_value(candidate)
            if isinstance(parsed, list):
                return [dict(item) for item in parsed if isinstance(item, Mapping)]
            if isinstance(parsed, Mapping):
                for key in ("results", "items", "sources"):
                    rows = parsed.get(key)
                    if isinstance(rows, list):
                        return [
                            dict(item) for item in rows if isinstance(item, Mapping)
                        ]
                content = parsed.get("content")
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, Mapping) and isinstance(
                            item.get("text"), str
                        ):
                            text_rows = cls._rows_from_text(item["text"])
                            if text_rows:
                                return text_rows
                text = parsed.get("text")
                if isinstance(text, str):
                    text_rows = cls._rows_from_text(text)
                    if text_rows:
                        return text_rows
        return []

    @classmethod
    def _rows_from_text(cls, text: str) -> list[JsonObject]:
        parsed = cls._parse_json_value(text)
        if isinstance(parsed, list):
            return [dict(item) for item in parsed if isinstance(item, Mapping)]
        if isinstance(parsed, Mapping):
            rows = parsed.get("results") or parsed.get("items") or parsed.get("sources")
            if isinstance(rows, list):
                return [dict(item) for item in rows if isinstance(item, Mapping)]
        return []

    @staticmethod
    def _parse_json_value(value: object) -> object:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value

    @classmethod
    def _row_text(cls, row: Mapping[str, object], keys: tuple[str, ...]) -> str | None:
        for key in keys:
            value = row.get(key)
            if isinstance(value, str) and value.strip():
                return cls._safe_text(value, 180)
        return None

    @staticmethod
    def _safe_text(value: str, max_length: int) -> str:
        text = " ".join(value.replace("<", "").replace(">", "").split())
        if "/large_tool_results/" in text:
            return "Large result saved for internal inspection."
        return text[:max_length]

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
    def _secretish(key: str) -> bool:
        lowered = key.lower()
        return any(token in lowered for token in ("token", "secret", "password", "key"))

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

    def _resolve_tool_template(self, payload: JsonObject) -> ToolDisplayTemplate | None:
        if self.tool_display_lookup is None:
            return None
        tool_name = payload.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            return None
        try:
            return self.tool_display_lookup(tool_name.strip())
        except Exception:
            logging.getLogger(__name__).warning(
                "Tool display lookup failed for %s", tool_name, exc_info=True
            )
            return None

    @classmethod
    def _with_deterministic_fields(
        cls,
        validated: JsonObject,
        *,
        group_key: str | None,
    ) -> JsonObject:
        """Backfill deterministic group_key / debug_label / confidence on LLM output.

        Status_label and kind come from the LLM (constrained by the schema) but
        we always set group_key + the fixed presentation defaults so cards are
        consistent regardless of model variance.
        """

        if group_key is not None and not validated.get("group_key"):
            validated["group_key"] = group_key
        validated.setdefault("debug_label", "Tool details")
        validated.setdefault("confidence", "medium")
        return validated

    @classmethod
    def _fallback(cls, context: JsonObject) -> JsonObject:
        status = str(context.get("status") or "").lower()
        failed = (
            status in {"failed", "error"} or context.get("event_type") == "run_failed"
        )
        waiting = context.get("event_type") in {
            "approval_requested",
            "mcp_auth_required",
        }
        done = status in {"completed", "complete", "done", "success", "succeeded"}
        status_label = (
            "Failed"
            if failed
            else "Waiting for permission"
            if waiting
            else "Done"
            if done
            else "Running"
        )
        kind = (
            "error"
            if failed
            else "approval"
            if context.get("event_type") == "approval_requested"
            else "auth"
            if context.get("event_type") == "mcp_auth_required"
            else "result"
            if done
            else "progress"
        )
        return {
            "title": cls._fallback_title(kind),
            "summary": cls._fallback_summary(kind),
            "status_label": status_label,
            "kind": kind,
            "group_key": context.get("group_key"),
            "debug_label": "Tool details",
            "confidence": "low",
        }

    @staticmethod
    def _fallback_title(kind: str) -> str:
        if kind == "approval":
            return "Permission needed"
        if kind == "auth":
            return "Connect app"
        if kind == "result":
            return "Checked source"
        if kind == "error":
            return "Step failed"
        return "Working on step"

    @staticmethod
    def _fallback_summary(kind: str) -> str:
        if kind == "approval":
            return "Enterprise Search needs your permission before continuing."
        if kind == "auth":
            return "Enterprise Search needs permission to connect this app."
        if kind == "result":
            return "Enterprise Search finished this step."
        if kind == "error":
            return "Enterprise Search could not complete this step."
        return "Enterprise Search is working on this step."
