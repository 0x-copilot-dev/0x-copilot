"""Build branch-scoped context for prior tool observations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import json

from agent_runtime.api.constants import Keys
from agent_runtime.api.ports import EventStorePort
from agent_runtime.execution.contracts import AgentRuntimeContext, JsonObject
from agent_runtime.persistence.ports import ConversationToolOrdinalStorePort
from runtime_worker.stream_messages import StreamTextHelper
from runtime_api.schemas import (
    MessageRecord,
    RuntimeApiEventType,
    RuntimeEventEnvelope,
    RuntimeEventRedactionState,
    RuntimeEventVisibility,
)


@dataclass(frozen=True)
class ToolObservation:
    """A redacted prior tool result that can be summarized or loaded."""

    observation_id: str
    run_id: str
    call_id: str
    tool_name: str
    args_preview: str | None
    result_preview: str
    payload: JsonObject
    created_at: str
    # PR 04 — conversation_ordinal sourced from the persistent
    # ``agent_conversation_tool_ordinals`` binding map, joined by
    # ``call_id``. The same number the in-turn resolver stamps on
    # ``citation_made`` events, so a cross-turn ``[[N]]`` cited by the
    # model in T+k resolves to the *same* tool the originating turn
    # used. ``None`` for observation kinds without a tool-call ordinal
    # (subagent summaries, or call_ids missing from the binding map
    # because they pre-date the persistence layer).
    conversation_ordinal: int | None = None


@dataclass(frozen=True)
class ToolObservationIndex:
    """Branch-scoped prior tool observations for one runtime run."""

    observations: tuple[ToolObservation, ...]
    prompt_context: str | None

    @property
    def has_observations(self) -> bool:
        return bool(self.observations)


class ToolObservationIndexBuilder:
    """Project prior tool result events into compact model context.

    PR 04 — ordinals come from the persistent binding map
    (:class:`ConversationToolOrdinalStorePort`), not from re-counting
    ``TOOL_CALL_STARTED`` events at build time. The runtime allocator
    is the only writer; this builder is one of the readers. Joining on
    ``tool_call_id`` guarantees the prompt context's ``cite as [[N]]``
    hint matches the resolver's binding for that same tool call —
    cross-turn citation and within-turn citation share one source of
    truth.

    When the store is not bound (replay / eval paths that build the
    builder without the worker's full DI), observations are returned
    without ordinals — the model still sees the prior result preview
    and the ``load_prior_tool_result`` affordance, just no ``[[N]]``
    cite hint. Citations remain best-effort decoration.
    """

    max_observations = 8
    max_prompt_chars = 4_000
    max_preview_chars = 600
    max_args_chars = 240

    def __init__(
        self,
        event_store: EventStorePort,
        *,
        conversation_tool_ordinal_store: (
            ConversationToolOrdinalStorePort | None
        ) = None,
    ) -> None:
        self.event_store = event_store
        self._ordinal_store = conversation_tool_ordinal_store

    async def build(
        self,
        *,
        org_id: str,
        conversation_id: str,
        current_run_id: str,
        selected_messages: Sequence[MessageRecord],
    ) -> ToolObservationIndex:
        run_ids = self._prior_run_ids(selected_messages, current_run_id)
        observations: list[ToolObservation] = []
        for run_id in run_ids:
            events = await self.event_store.list_events_after(
                org_id=org_id,
                run_id=run_id,
                after_sequence=0,
            )
            observations.extend(
                self._observations_for_run(
                    events=events,
                    expected_conversation_id=conversation_id,
                )
            )
        # PR 04 — join observations to the canonical binding map by
        # ``call_id``. The map is whatever the runtime allocator has
        # persisted so far for the conversation; observations whose
        # call_id isn't bound (subagent summaries, pre-PR04
        # backfill-pending rows) just lack ``conversation_ordinal``.
        ordinal_by_call_id: dict[str, int] = {}
        if self._ordinal_store is not None:
            bindings = await self._ordinal_store.load(
                org_id=org_id, conversation_id=conversation_id
            )
            ordinal_by_call_id = {
                binding.tool_call_id: binding.conversation_ordinal
                for binding in bindings
            }
        observations = [
            self._with_ordinal(obs, ordinal_by_call_id.get(obs.call_id))
            for obs in observations
        ]
        bounded = tuple(observations[-self.max_observations :])
        return ToolObservationIndex(
            observations=bounded,
            prompt_context=self._prompt_context(bounded),
        )

    @staticmethod
    def _with_ordinal(
        observation: ToolObservation, ordinal: int | None
    ) -> ToolObservation:
        if observation.conversation_ordinal == ordinal:
            return observation
        return ToolObservation(
            observation_id=observation.observation_id,
            run_id=observation.run_id,
            call_id=observation.call_id,
            tool_name=observation.tool_name,
            args_preview=observation.args_preview,
            result_preview=observation.result_preview,
            payload=observation.payload,
            created_at=observation.created_at,
            conversation_ordinal=ordinal,
        )

    @classmethod
    def _prior_run_ids(
        cls,
        selected_messages: Sequence[MessageRecord],
        current_run_id: str,
    ) -> tuple[str, ...]:
        run_ids: list[str] = []
        seen: set[str] = set()
        for message in selected_messages:
            run_id = message.run_id
            if run_id is None or run_id == current_run_id or run_id in seen:
                continue
            seen.add(run_id)
            run_ids.append(run_id)
        return tuple(run_ids)

    def _observations_for_run(
        self,
        *,
        events: Sequence[RuntimeEventEnvelope],
        expected_conversation_id: str,
    ) -> tuple[ToolObservation, ...]:
        calls_by_id: dict[str, JsonObject] = {}
        # `task_id -> SUBAGENT_STARTED.payload`. Lets a SUBAGENT_COMPLETED carry
        # the dispatched task description into the next-turn prompt context so
        # the model can reuse the prior result instead of re-dispatching.
        subagent_started_by_task_id: dict[str, JsonObject] = {}
        observations: list[ToolObservation] = []
        for event in events:
            if event.conversation_id != expected_conversation_id:
                continue
            if event.event_type in {
                RuntimeApiEventType.TOOL_CALL,
                RuntimeApiEventType.TOOL_CALL_STARTED,
                RuntimeApiEventType.TOOL_CALL_DELTA,
            }:
                call_id = StreamTextHelper.extract(
                    event.payload.get(Keys.Field.CALL_ID)
                )
                if call_id is not None:
                    calls_by_id[call_id] = event.payload
                continue
            if event.event_type is RuntimeApiEventType.SUBAGENT_STARTED:
                task_id = StreamTextHelper.extract(
                    event.payload.get(Keys.Field.TASK_ID)
                )
                if task_id is not None:
                    subagent_started_by_task_id[task_id] = event.payload
                continue
            if event.event_type is RuntimeApiEventType.SUBAGENT_COMPLETED:
                observation = self._observation_from_subagent_event(
                    event=event,
                    started_payload=subagent_started_by_task_id.get(
                        StreamTextHelper.extract(event.payload.get(Keys.Field.TASK_ID))
                        or ""
                    ),
                )
                if observation is not None:
                    observations.append(observation)
                continue
            if event.event_type is not RuntimeApiEventType.TOOL_RESULT:
                continue
            observation = self._observation_from_event(
                event=event,
                call_payload=calls_by_id.get(
                    StreamTextHelper.extract(event.payload.get(Keys.Field.CALL_ID))
                    or ""
                ),
            )
            if observation is not None:
                observations.append(observation)
        return tuple(observations)

    def _observation_from_subagent_event(
        self,
        *,
        event: RuntimeEventEnvelope,
        started_payload: JsonObject | None,
    ) -> ToolObservation | None:
        if event.visibility is not RuntimeEventVisibility.USER:
            return None
        if event.redaction_state is RuntimeEventRedactionState.OFFLOADED:
            return None
        task_id = StreamTextHelper.extract(event.payload.get(Keys.Field.TASK_ID))
        if task_id is None:
            return None
        subagent_name = (
            StreamTextHelper.extract(event.payload.get(Keys.Field.SUBAGENT_NAME))
            or "subagent"
        )
        result_preview = (
            StreamTextHelper.extract(event.payload.get(Keys.Field.SUMMARY))
            or StreamTextHelper.extract(event.summary)
            or self._payload_preview(event.payload.get(Keys.Field.OUTPUT))
        )
        if result_preview is None:
            return None
        objective_preview: str | None = None
        if started_payload is not None:
            objective = StreamTextHelper.extract(
                started_payload.get(Keys.Field.SUMMARY)
            ) or StreamTextHelper.extract(started_payload.get("description"))
            if objective:
                objective_preview = self._truncate(objective, self.max_args_chars)
        return ToolObservation(
            observation_id=f"obs_{event.event_id}",
            run_id=event.run_id,
            call_id=task_id,
            tool_name=f"subagent:{subagent_name}",
            args_preview=objective_preview,
            result_preview=self._truncate(result_preview, self.max_preview_chars),
            payload=event.payload,
            created_at=event.created_at.isoformat(),
        )

    def _observation_from_event(
        self,
        *,
        event: RuntimeEventEnvelope,
        call_payload: JsonObject | None,
    ) -> ToolObservation | None:
        if event.visibility is not RuntimeEventVisibility.USER:
            return None
        if event.redaction_state is RuntimeEventRedactionState.OFFLOADED:
            return None
        call_id = StreamTextHelper.extract(event.payload.get(Keys.Field.CALL_ID))
        if call_id is None:
            return None
        tool_name = (
            StreamTextHelper.extract(event.payload.get(Keys.Field.TOOL_NAME))
            or StreamTextHelper.extract((call_payload or {}).get(Keys.Field.TOOL_NAME))
            or "unknown_tool"
        )
        result_preview = (
            StreamTextHelper.extract(event.summary)
            or self._payload_preview(event.payload.get(Keys.Field.OUTPUT))
            or self._payload_preview(event.payload)
        )
        if result_preview is None:
            return None
        args_preview = None
        if call_payload is not None:
            args_preview = self._payload_preview(
                call_payload.get(Keys.Field.ARGS),
                limit=self.max_args_chars,
            )
        return ToolObservation(
            observation_id=f"obs_{event.event_id}",
            run_id=event.run_id,
            call_id=call_id,
            tool_name=tool_name,
            args_preview=args_preview,
            result_preview=self._truncate(result_preview, self.max_preview_chars),
            payload=event.payload,
            created_at=event.created_at.isoformat(),
        )

    def _prompt_context(self, observations: Sequence[ToolObservation]) -> str | None:
        if not observations:
            return None
        lines = [
            "Prior tool and subagent observations from earlier turns are "
            "available below.",
            "Use them when directly relevant. Call tools again when the user "
            "asks for fresh/current/latest data or when these summaries lack "
            "detail. Reuse a prior subagent summary instead of re-dispatching "
            "the same research unless the user explicitly asks for fresh work.",
            "Use load_prior_tool_result with an observation_id only when you "
            "need the full persisted redacted result.",
            # PR 1.1-rev2 — cross-turn citation. Each observation that
            # came from a tool call has a stable ``[[N]]`` pointer
            # listed below; cite that pointer when grounding any claim
            # in the corresponding prior result.
            "When grounding a claim in one of these observations, append "
            "the listed `[[N]]` marker (if shown) immediately after the "
            "claim so the source resolves to the right prior tool call.",
            "",
            "Observations:",
        ]
        for observation in observations:
            args = (
                f" args={observation.args_preview};"
                if observation.args_preview is not None
                else ""
            )
            cite_hint = (
                f" cite as [[{observation.conversation_ordinal}]];"
                if observation.conversation_ordinal is not None
                else ""
            )
            lines.append(
                f"- {observation.observation_id}: {observation.tool_name}"
                f"({args}{cite_hint} run_id={observation.run_id}, "
                f"call_id={observation.call_id}) "
                f"preview: {observation.result_preview}"
            )
        prompt = "\n".join(lines)
        return self._truncate(prompt, self.max_prompt_chars)

    @classmethod
    def _payload_preview(cls, value: object, *, limit: int | None = None) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
        else:
            text = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
        if not text:
            return None
        return cls._truncate(text, limit or cls.max_preview_chars)

    @staticmethod
    def _truncate(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return f"{value[:limit].rstrip()} [truncated]"


class PriorToolResultLoader:
    """Resolve full prior tool observations scoped to one selected branch."""

    def __init__(self, index: ToolObservationIndex) -> None:
        self._observations = {
            observation.observation_id: observation
            for observation in index.observations
        }

    def load_prior_tool_result(
        self,
        *,
        observation_id: str,
        runtime_context: AgentRuntimeContext,
    ) -> dict[str, object]:
        observation = self._observations.get(observation_id)
        if observation is None:
            return {
                "ok": False,
                "error_code": "observation_not_found",
                "safe_message": (
                    "Prior tool observation was not found for this run context."
                ),
            }
        return {
            "ok": True,
            "observation_id": observation.observation_id,
            "run_id": observation.run_id,
            "call_id": observation.call_id,
            "tool_name": observation.tool_name,
            "created_at": observation.created_at,
            "result": observation.payload,
            "context": {
                "org_id": runtime_context.org_id,
                "current_run_id": runtime_context.run_id,
            },
        }
