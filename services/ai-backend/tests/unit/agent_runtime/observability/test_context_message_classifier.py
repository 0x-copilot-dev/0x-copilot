"""Unit tests for message-origin classification (PRD-07, design §4.6).

Five properties carry this module, and the classes below are organized by them:

1. **The note split is exact.** The parts of one ``ToolMessage`` reassemble it
   byte-for-byte, so a peeled citation or budget note is measured rather than
   estimated. The notes in these tests are rendered by their **real producers**
   (``CitationHint`` / ``ToolBudgetUsage`` through ``ToolResultNote``) rather
   than hand-typed, because a hand-typed note would let the classifier and the
   producer drift while the suite stayed green — which is the exact failure the
   single-sourced constants exist to prevent.
2. **Every message resolves to a declared origin, or visibly to none.** The
   resolution order is first-match-wins, so the tests that matter most are the
   ones where two rules could fire (base64 inside a tool result, a summary that
   is also a ``HumanMessage``).
3. **Nothing raises.** Message content is untrusted; a malformed shape, an
   exploding attribute, and a non-sequence request all degrade to recorded rows.
4. **No content leaks into identifiers.** ``detail`` stays an ordinal no matter
   how large or how multi-line the message is.
5. **A message is rendered once.** Seven rules read one message, and rendering
   per rule would make classification a multiple of request size against §3.4's
   p95 budget — as well as letting two reads of a library object disagree, which
   would quietly falsify property 1.
"""

from __future__ import annotations

from collections.abc import Sequence
import json
from typing import Any, Final

from annotated_types import MaxLen
import pytest
from pydantic import ValidationError

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from agent_runtime.capabilities.citation_capturing_tool import CitationHint
from agent_runtime.capabilities.tool_budget_middleware import ToolBudgetUsage
from agent_runtime.capabilities.tool_result_notes import ToolResultNote
from agent_runtime.context.tool_result_admission import ToolResultAdmissionAdapter
from agent_runtime.observability.context_message_classifier import (
    ClassifiedMessagePart,
    ContextMessageClassifier,
    MessageContextOrigins,
)
from agent_runtime.observability.context_occupancy import ContextSegment
from agent_runtime.observability.context_origin import (
    MAX_LABEL_LENGTH as MAX_CONTEXT_LABEL_LENGTH,
)
from agent_runtime.observability.context_origin import (
    UNDECLARED_CONTEXT_LABEL,
    ContextLifecycle,
    ContextOrigin,
    ContextSegmentClass,
)
from agent_runtime.observability.context_token_counter import (
    ContextTokenCounter,
    DigestTokenCache,
)
from agent_runtime.observability.redactor import SafeLogDumper


class ExplodingMessage:
    """A message-shaped object whose content access raises.

    Stands in for the class of failure §6.4 is written for: a library shape the
    runtime does not control, reached on the model-call path, that misbehaves
    while a run is in flight. A raising *property* is specifically not covered
    by ``getattr(..., None)``, which is why the classifier reads every attribute
    through a guard rather than a default.
    """

    @property
    def content(self) -> str:
        raise RuntimeError("content is unavailable")


class CountingContentMessage:
    """A message whose ``content`` reads are counted.

    Stands in for the real risk behind materializing once: ``content`` on a
    library message is an ordinary attribute today, but it is not *contractually*
    a cheap pure read, and the rule chain has seven rules that would each want
    the same bytes.
    """

    def __init__(self, content: object) -> None:
        self._content = content
        self.reads = 0

    @property
    def content(self) -> object:
        self.reads += 1
        return self._content


class ExplodingToolCallsMessage:
    """A readable turn whose ``tool_calls`` explode.

    Exercises the pre-pass that indexes ``/subagents/`` reads, which runs before
    any message is classified and therefore outside the per-message guard.
    """

    content = "I will look that up"

    @property
    def tool_calls(self) -> list[dict[str, object]]:
        raise RuntimeError("tool calls are unavailable")


class ExplodingToolCallEntry(dict[str, object]):
    """A tool-call *entry* that passes the ``Mapping`` check and then raises.

    The list of tool calls reads fine here; one element inside it misbehaves.
    That reaches past the attribute guard into the trace-index walk, which is
    the one loop that runs before any per-message guard is in scope.
    """

    def get(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("tool call entry is unavailable")


class StubMessage:
    """A minimal message-shaped object carrying arbitrary ``content``.

    LangChain's own message classes validate ``content`` into ``str`` or a block
    list, which is exactly the validation the classifier may not assume: a
    request reaches the model-call path through middleware and library code that
    can put other shapes there, and §6.4 says measurement absorbs them. Building
    those shapes needs an object the library is not policing.
    """

    def __init__(self, content: object) -> None:
        self.content = content


class UnrenderableContent:
    """A content value that refuses both JSON and ``str``.

    The last rung of ``_safe_json``'s ladder: not serializable, and its
    ``__str__`` — the fallback both ``default=str`` and the outer guard reach
    for — raises too.
    """

    def __str__(self) -> str:
        raise RuntimeError("value has no string form")


class UnwalkableBlocks(Sequence[object]):
    """Content that claims to be a block list and then refuses to be walked.

    Distinct from :class:`ExplodingMessage`, whose *attribute* raises and is
    absorbed by the attribute guard. Here the attribute reads cleanly and the
    failure happens during materialization, which is the only thing the
    per-message fail-open guard can catch.
    """

    def __len__(self) -> int:
        return 2

    def __getitem__(self, index: object) -> object:
        raise RuntimeError("content blocks are unavailable")


class MessageFixtureMixin:
    """Builders for every message shape the resolution order distinguishes."""

    TOOL_NAME: Final[str] = "web_search"
    CALL_ID: Final[str] = "call-0001"
    RESULT_BODY: Final[str] = "the search returned three relevant papers"
    SUBAGENT_PATH: Final[str] = "/subagents/task-7/summary.md"

    @staticmethod
    def citation_note(*, ordinal: int = 3, tool_name: str = TOOL_NAME) -> str:
        """Render the citation pointer note exactly as the wrapper does."""

        return CitationHint.render(ordinal=ordinal, tool_name=tool_name)

    @staticmethod
    def budget_note(*, used: int = 4, limit: int = 5) -> str:
        """Render the tool-budget note exactly as the middleware does."""

        return ToolBudgetUsage(
            tool_name=MessageFixtureMixin.TOOL_NAME,
            used=used,
            limit=limit,
        ).render_note()

    @staticmethod
    def annotate(result: str, *notes: str) -> str:
        """Append ``notes`` to ``result`` through the shared append walk."""

        annotated: Any = result
        for index, note in enumerate(notes):
            annotated = ToolResultNote.append(
                annotated,
                note=note,
                dict_key=f"_note_{index}",
            )
        return str(annotated)

    @classmethod
    def tool_message(
        cls,
        content: Any,
        *,
        call_id: str = CALL_ID,
        **kwargs: Any,
    ) -> ToolMessage:
        """Build a tool result carrying ``content``."""

        return ToolMessage(
            content=content,
            tool_call_id=call_id,
            name=cls.TOOL_NAME,
            **kwargs,
        )

    @classmethod
    def read_file_call(cls, path: str, *, call_id: str = CALL_ID) -> AIMessage:
        """Build the assistant turn that requested a file read at ``path``."""

        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"file_path": path},
                    "id": call_id,
                    "type": "tool_call",
                }
            ],
        )

    @staticmethod
    def labels(parts: tuple[ClassifiedMessagePart, ...]) -> tuple[str, ...]:
        """Project parts to their labels for order-sensitive assertions."""

        return tuple(part.label for part in parts)

    @staticmethod
    def classify(*messages: object) -> tuple[ClassifiedMessagePart, ...]:
        """Classify ``messages`` as one request."""

        return ContextMessageClassifier.classify(messages)


class TestToolResultNoteSplit(MessageFixtureMixin):
    """Rule 4 — the split that makes audit items L and M visible."""

    def test_splits_both_notes_and_preserves_every_byte(self) -> None:
        content = self.annotate(
            self.RESULT_BODY,
            self.budget_note(),
            self.citation_note(),
        )

        parts = self.classify(self.tool_message(content))

        assert self.labels(parts) == (
            MessageContextOrigins.TOOL_RESULT.label,
            MessageContextOrigins.TOOL_BUDGET_NOTE.label,
            MessageContextOrigins.CITATION_POINTER_NOTE.label,
        )
        assert parts[0].text == self.RESULT_BODY
        assert "".join(part.text for part in parts) == content
        assert sum(part.byte_count for part in parts) == len(content.encode("utf-8"))

    def test_split_survives_the_reverse_append_order(self) -> None:
        content = self.annotate(
            self.RESULT_BODY,
            self.citation_note(),
            self.budget_note(),
        )

        parts = self.classify(self.tool_message(content))

        assert self.labels(parts) == (
            MessageContextOrigins.TOOL_RESULT.label,
            MessageContextOrigins.CITATION_POINTER_NOTE.label,
            MessageContextOrigins.TOOL_BUDGET_NOTE.label,
        )
        assert "".join(part.text for part in parts) == content

    def test_splits_a_single_citation_note(self) -> None:
        content = self.annotate(self.RESULT_BODY, self.citation_note())

        parts = self.classify(self.tool_message(content))

        assert self.labels(parts) == (
            MessageContextOrigins.TOOL_RESULT.label,
            MessageContextOrigins.CITATION_POINTER_NOTE.label,
        )
        assert parts[1].text == ToolResultNote.SEPARATOR + self.citation_note()

    def test_splits_a_single_budget_note(self) -> None:
        content = self.annotate(self.RESULT_BODY, self.budget_note(used=5, limit=5))

        parts = self.classify(self.tool_message(content))

        assert self.labels(parts) == (
            MessageContextOrigins.TOOL_RESULT.label,
            MessageContextOrigins.TOOL_BUDGET_NOTE.label,
        )

    def test_notes_are_declared_per_result(self) -> None:
        content = self.annotate(
            self.RESULT_BODY,
            self.budget_note(),
            self.citation_note(),
        )

        parts = self.classify(self.tool_message(content))

        assert all(part.lifecycle is ContextLifecycle.PER_RESULT for part in parts), (
            "per-result notes are a multiplier on tool-call count, not per-turn rent"
        )

    def test_unannotated_result_is_one_part(self) -> None:
        parts = self.classify(self.tool_message(self.RESULT_BODY))

        assert self.labels(parts) == (MessageContextOrigins.TOOL_RESULT.label,)
        assert parts[0].text == self.RESULT_BODY

    def test_note_only_result_emits_no_phantom_remainder(self) -> None:
        content = self.annotate("", self.citation_note())

        parts = self.classify(self.tool_message(content))

        assert self.labels(parts) == (
            MessageContextOrigins.CITATION_POINTER_NOTE.label,
        )

    def test_empty_result_still_reports_a_row(self) -> None:
        parts = self.classify(self.tool_message(""))

        assert self.labels(parts) == (MessageContextOrigins.TOOL_RESULT.label,)
        assert parts[0].byte_count == 0

    def test_forged_note_prefix_in_untrusted_output_is_not_split(self) -> None:
        # Tool output that quotes the note prefix and then continues for longer
        # than a real note ever runs. Splitting here would hand a large slab of
        # result bytes to a per-result label and misreport the multiplier.
        content = (
            "search results:"
            + ToolResultNote.SEPARATOR
            + CitationHint.NOTE_PREFIX
            + "9 - "
            + "padding " * 100
            + "]"
        )

        parts = self.classify(self.tool_message(content))

        assert self.labels(parts) == (MessageContextOrigins.TOOL_RESULT.label,)
        assert parts[0].text == content

    def test_unterminated_note_candidate_is_not_split(self) -> None:
        content = self.RESULT_BODY + ToolResultNote.SEPARATOR + CitationHint.NOTE_PREFIX

        parts = self.classify(self.tool_message(content))

        assert self.labels(parts) == (MessageContextOrigins.TOOL_RESULT.label,)

    def test_note_inside_a_serialized_dict_result_stays_with_the_result(self) -> None:
        # The boundary the module docstring documents, pinned so it cannot move
        # silently. ``ToolResultNote`` appends onto a *dict* result as a new
        # top-level key rather than as a trailing string, so once the result is
        # serialized into a ToolMessage the note sits inside the JSON body with
        # no separator and its own characters escaped. Peeling it would mean
        # re-deriving the producer's escaping — a pattern guess over untrusted
        # text — so those bytes stay attributed to the result.
        annotated = ToolResultNote.append(
            {"rows": [1, 2]},
            note=self.citation_note(),
            dict_key="_citation",
        )
        content = json.dumps(annotated)

        parts = self.classify(self.tool_message(content))

        assert self.labels(parts) == (MessageContextOrigins.TOOL_RESULT.label,)
        assert parts[0].text == content
        assert CitationHint.NOTE_PREFIX in parts[0].text, (
            "the note is present in the body — it is the separator that is not, "
            "which is exactly why the split declines it"
        )


class TestSubagentTraceResolution(MessageFixtureMixin):
    """Rule 4's second half — a result the model pulled from a trace read."""

    def test_result_of_a_subagents_read_is_the_trace_origin(self) -> None:
        parts = self.classify(
            self.read_file_call(self.SUBAGENT_PATH),
            self.tool_message("# Subagent task-7"),
        )

        assert parts[-1].label == MessageContextOrigins.SUBAGENT_TRACE.label
        assert parts[-1].lifecycle is ContextLifecycle.ON_DEMAND

    def test_ordinary_read_stays_a_tool_result(self) -> None:
        parts = self.classify(
            self.read_file_call("/workspace/report.md"),
            self.tool_message("report body"),
        )

        assert parts[-1].label == MessageContextOrigins.TOOL_RESULT.label

    def test_trace_result_still_splits_its_notes(self) -> None:
        content = self.annotate("# Subagent task-7", self.citation_note())

        parts = self.classify(
            self.read_file_call(self.SUBAGENT_PATH),
            self.tool_message(content),
        )

        assert self.labels(parts[1:]) == (
            MessageContextOrigins.SUBAGENT_TRACE.label,
            MessageContextOrigins.CITATION_POINTER_NOTE.label,
        )

    def test_read_file_path_metadata_also_resolves_the_trace(self) -> None:
        parts = self.classify(
            self.tool_message(
                "trace bytes",
                additional_kwargs={"read_file_path": self.SUBAGENT_PATH},
            )
        )

        assert parts[0].label == MessageContextOrigins.SUBAGENT_TRACE.label

    def test_unmatched_call_id_does_not_borrow_another_calls_path(self) -> None:
        parts = self.classify(
            self.read_file_call(self.SUBAGENT_PATH, call_id="call-A"),
            self.tool_message("unrelated result", call_id="call-B"),
        )

        assert parts[-1].label == MessageContextOrigins.TOOL_RESULT.label


class TestBinaryContentRule(MessageFixtureMixin):
    """Rule 1 — base64 payloads (audit item R) win over every later rule."""

    PAYLOAD: Final[str] = "QUJDRA=="

    def test_deepagents_binary_read_is_the_workspace_origin(self) -> None:
        message = ToolMessage(
            content_blocks=[
                {
                    "type": "file",
                    "base64": self.PAYLOAD,
                    "mime_type": "application/pdf",
                }
            ],
            tool_call_id=self.CALL_ID,
            name="read_file",
        )

        parts = self.classify(message)

        assert self.labels(parts) == (MessageContextOrigins.WORKSPACE_BINARY_B64.label,)
        assert parts[0].lifecycle is ContextLifecycle.ON_DEMAND
        assert self.PAYLOAD in parts[0].text

    def test_file_data_encoding_marker_is_recognised(self) -> None:
        message = self.tool_message(
            [{"content": self.PAYLOAD, "encoding": "base64"}],
        )

        parts = self.classify(message)

        assert self.labels(parts) == (MessageContextOrigins.WORKSPACE_BINARY_B64.label,)

    def test_nested_source_block_is_recognised(self) -> None:
        message = HumanMessage(
            content=[
                {
                    "type": "image",
                    "source": {"type": "base64", "data": self.PAYLOAD},
                }
            ]
        )

        parts = self.classify(message)

        assert self.labels(parts) == (MessageContextOrigins.WORKSPACE_BINARY_B64.label,)

    def test_message_metadata_encoding_marker_is_recognised(self) -> None:
        message = self.tool_message(
            "not obviously binary",
            additional_kwargs={"encoding": "base64"},
        )

        parts = self.classify(message)

        assert self.labels(parts) == (MessageContextOrigins.WORKSPACE_BINARY_B64.label,)

    def test_multiple_payloads_roll_up_under_one_item_count(self) -> None:
        message = self.tool_message(
            [
                {"type": "file", "base64": self.PAYLOAD},
                {"type": "file", "base64": self.PAYLOAD},
            ],
        )

        parts = self.classify(message)

        assert parts[0].item_count == 2

    def test_plain_text_blocks_are_not_mistaken_for_binary(self) -> None:
        message = self.tool_message([{"type": "text", "text": "plain result"}])

        parts = self.classify(message)

        assert self.labels(parts) == (MessageContextOrigins.TOOL_RESULT.label,)
        assert parts[0].text == "plain result"


class TestOffloadStubRule(MessageFixtureMixin):
    """Rule 3 — the bounded stub an oversized result was replaced by."""

    @staticmethod
    def admission(payload: str = "x" * 200_000) -> Any:
        """Produce a real offload admission through the real adapter."""

        adapter = ToolResultAdmissionAdapter(
            offload_writer=lambda _content: "/offload/ref-1"
        )
        return adapter.admit(payload, trace_id="trace-1")

    def test_stub_content_is_the_offload_origin(self) -> None:
        admitted = self.admission()

        parts = self.classify(self.tool_message(admitted.model_content))

        assert self.labels(parts) == (MessageContextOrigins.OFFLOAD_STUB.label,)
        assert parts[0].lifecycle is ContextLifecycle.PER_RESULT

    def test_typed_artifact_is_the_offload_origin(self) -> None:
        admitted = self.admission()

        parts = self.classify(
            self.tool_message("preview only", artifact=admitted),
        )

        assert self.labels(parts) == (MessageContextOrigins.OFFLOAD_STUB.label,)

    def test_stub_is_not_note_split(self) -> None:
        # §4.6 is first-match-wins: rule 3 claims the whole message, so a note
        # appended after admission reads as stub bytes. Asserted so the
        # documented trade-off cannot change silently.
        admitted = self.admission()
        content = self.annotate(admitted.model_content, self.citation_note())

        parts = self.classify(self.tool_message(content))

        assert self.labels(parts) == (MessageContextOrigins.OFFLOAD_STUB.label,)
        assert parts[0].text == content


class TestSummaryAndStateFileRules(MessageFixtureMixin):
    """Rules 2 and 6 — compaction output and evicted message bodies."""

    def test_summarization_metadata_marker_wins_over_the_user_rule(self) -> None:
        message = HumanMessage(
            content="<summary>compacted</summary>",
            additional_kwargs={"lc_source": "summarization"},
        )

        parts = self.classify(message)

        assert self.labels(parts) == (MessageContextOrigins.SUMMARY.label,)
        assert parts[0].lifecycle is ContextLifecycle.PER_TURN

    @pytest.mark.parametrize(
        "prefix",
        [
            "You are in the middle of a conversation that has been summarized.",
            "Here is a summary of the conversation to date:",
        ],
    )
    def test_summary_content_templates_are_recognised_without_metadata(
        self,
        prefix: str,
    ) -> None:
        parts = self.classify(HumanMessage(content=f"{prefix}\n\nthe summary"))

        assert self.labels(parts) == (MessageContextOrigins.SUMMARY.label,)

    def test_evicted_message_metadata_is_the_state_file_origin(self) -> None:
        message = HumanMessage(
            content="Message content too large and was saved to the filesystem "
            "at: /state/msg.md\n\npreview",
            additional_kwargs={"lc_evicted_to": "/state/msg.md"},
        )

        parts = self.classify(message)

        assert self.labels(parts) == (MessageContextOrigins.STATE_FILE.label,)

    def test_eviction_notice_is_recognised_without_metadata(self) -> None:
        parts = self.classify(
            HumanMessage(
                content="Message content too large and was saved to the "
                "filesystem at: /state/msg.md"
            )
        )

        assert self.labels(parts) == (MessageContextOrigins.STATE_FILE.label,)

    def test_ordinary_user_turn_is_the_conversation_origin(self) -> None:
        parts = self.classify(HumanMessage(content="find me the paper"))

        assert self.labels(parts) == (MessageContextOrigins.USER.label,)
        assert parts[0].text == "find me the paper"


class TestAssistantMessages(MessageFixtureMixin):
    """Rule 5 — thinking, tool calls, and answer text are three costs."""

    THINKING: Final[str] = "weighing the two candidate sources"
    ANSWER: Final[str] = "here is what I found"

    def assistant_turn(self) -> AIMessage:
        """An assistant turn that thinks, calls a tool, and answers."""

        return AIMessage(
            content=[
                {
                    "type": "thinking",
                    "thinking": self.THINKING,
                    "signature": "sig-1",
                },
                {"type": "text", "text": self.ANSWER},
            ],
            tool_calls=[
                {
                    "name": self.TOOL_NAME,
                    "args": {"query": "context occupancy"},
                    "id": self.CALL_ID,
                    "type": "tool_call",
                }
            ],
        )

    def test_thinking_tool_calls_and_text_are_separate_parts(self) -> None:
        parts = self.classify(self.assistant_turn())

        assert self.labels(parts) == (
            MessageContextOrigins.ASSISTANT_THINKING.label,
            MessageContextOrigins.ASSISTANT_TOOL_CALLS.label,
            MessageContextOrigins.ASSISTANT_TEXT.label,
        )

    def test_thinking_text_never_leaks_into_the_answer_part(self) -> None:
        parts = self.classify(self.assistant_turn())

        answer = parts[-1]
        assert answer.text == self.ANSWER
        assert self.THINKING not in answer.text

    def test_tool_call_part_carries_the_wire_fields_only(self) -> None:
        parts = self.classify(self.assistant_turn())

        tool_calls = parts[1]
        assert tool_calls.item_count == 1
        assert "context occupancy" in tool_calls.text
        assert '"type"' not in tool_calls.text

    @pytest.mark.parametrize(
        "block_type", ["thinking", "reasoning", "redacted_thinking"]
    )
    def test_every_reasoning_block_shape_is_recognised(self, block_type: str) -> None:
        message = AIMessage(
            content=[{"type": block_type, "reasoning": "internal", "data": "opaque"}]
        )

        parts = self.classify(message)

        assert self.labels(parts) == (MessageContextOrigins.ASSISTANT_THINKING.label,)

    def test_multiple_thinking_blocks_roll_up_under_one_item_count(self) -> None:
        message = AIMessage(
            content=[
                {"type": "thinking", "thinking": "first"},
                {"type": "thinking", "thinking": "second"},
                {"type": "text", "text": self.ANSWER},
            ]
        )

        parts = self.classify(message)

        assert parts[0].item_count == 2

    def test_plain_text_turn_is_one_part(self) -> None:
        parts = self.classify(AIMessage(content=self.ANSWER))

        assert self.labels(parts) == (MessageContextOrigins.ASSISTANT_TEXT.label,)
        assert parts[0].text == self.ANSWER

    def test_tool_call_only_turn_reports_no_empty_text_part(self) -> None:
        parts = self.classify(self.read_file_call("/workspace/report.md"))

        assert self.labels(parts) == (MessageContextOrigins.ASSISTANT_TOOL_CALLS.label,)

    def test_empty_turn_still_reports_a_row(self) -> None:
        parts = self.classify(AIMessage(content=""))

        assert self.labels(parts) == (MessageContextOrigins.ASSISTANT_TEXT.label,)
        assert parts[0].byte_count == 0


class TestUnmatchedAndMalformedShapes(MessageFixtureMixin):
    """No silent catch-all, and no failure path that reaches the model call."""

    def test_system_message_in_the_message_list_is_undeclared(self) -> None:
        parts = self.classify(SystemMessage(content="you are an agent"))

        assert self.labels(parts) == (UNDECLARED_CONTEXT_LABEL,)
        assert parts[0].origin is None
        assert parts[0].byte_count == len(b"you are an agent")

    @pytest.mark.parametrize(
        "message",
        [None, {"role": "user"}, 17, "a bare string"],
    )
    def test_non_message_values_are_undeclared_rather_than_fatal(
        self,
        message: object,
    ) -> None:
        parts = self.classify(message)

        assert self.labels(parts) == (UNDECLARED_CONTEXT_LABEL,)

    def test_exploding_message_degrades_to_an_undeclared_row(self) -> None:
        parts = self.classify(ExplodingMessage())

        assert self.labels(parts) == (UNDECLARED_CONTEXT_LABEL,)
        assert parts[0].byte_count == 0

    def test_a_broken_message_does_not_suppress_its_neighbours(self) -> None:
        parts = self.classify(
            HumanMessage(content="before"),
            ExplodingMessage(),
            HumanMessage(content="after"),
        )

        assert self.labels(parts) == (
            MessageContextOrigins.USER.label,
            UNDECLARED_CONTEXT_LABEL,
            MessageContextOrigins.USER.label,
        )
        assert tuple(part.detail for part in parts) == ("msg[0]", "msg[1]", "msg[2]")

    def test_content_that_fails_mid_materialization_is_still_a_row(self) -> None:
        # The per-message fail-open guard proper (§6.4). The attribute reads
        # cleanly and the failure lands inside materialization, which is the
        # only failure the outer guard — rather than the attribute guard —
        # exists to absorb.
        parts = self.classify(StubMessage(UnwalkableBlocks()))

        assert self.labels(parts) == (UNDECLARED_CONTEXT_LABEL,)
        assert parts[0].byte_count == 0

    def test_malformed_tool_call_entries_are_skipped_not_trusted(self) -> None:
        # A call with non-mapping arguments and a call with no id: neither can
        # prove it read a trace path, so neither may lend its label to a result.
        turn = StubMessage("")
        turn.tool_calls = [  # type: ignore[attr-defined]
            {"name": "read_file", "args": "not-a-mapping", "id": self.CALL_ID},
            {"name": "read_file", "args": {"file_path": self.SUBAGENT_PATH}},
        ]

        parts = self.classify(turn, self.tool_message("body"))

        assert parts[-1].label == MessageContextOrigins.TOOL_RESULT.label

    def test_an_exploding_tool_call_entry_does_not_blind_the_index(self) -> None:
        broken = StubMessage("")
        broken.tool_calls = [ExplodingToolCallEntry()]  # type: ignore[attr-defined]

        parts = self.classify(
            broken,
            self.read_file_call(self.SUBAGENT_PATH, call_id="call-trace"),
            self.tool_message("# Subagent task-7", call_id="call-trace"),
        )

        assert parts[-1].label == MessageContextOrigins.SUBAGENT_TRACE.label, (
            "one unreadable turn must not cost the whole request its trace attribution"
        )

    def test_exploding_tool_calls_do_not_break_the_trace_index(self) -> None:
        parts = self.classify(
            ExplodingToolCallsMessage(),
            self.read_file_call(self.SUBAGENT_PATH, call_id="call-trace"),
            self.tool_message("# Subagent task-7", call_id="call-trace"),
        )

        # The broken turn measures as undeclared, and the trace attribution of
        # the *other* turns still resolves.
        assert self.labels(parts) == (
            UNDECLARED_CONTEXT_LABEL,
            MessageContextOrigins.ASSISTANT_TOOL_CALLS.label,
            MessageContextOrigins.SUBAGENT_TRACE.label,
        )

    def test_unreadable_message_list_measures_as_empty(self) -> None:
        class Unwalkable:
            def __iter__(self) -> Any:
                raise RuntimeError("cannot iterate")

        assert ContextMessageClassifier.classify(Unwalkable()) == ()

    def test_empty_block_list_occupies_nothing(self) -> None:
        parts = self.classify(AIMessage(content=[]))

        assert self.labels(parts) == (MessageContextOrigins.ASSISTANT_TEXT.label,)
        assert parts[0].byte_count == 0

    def test_unknown_content_block_shapes_still_measure(self) -> None:
        parts = self.classify(self.tool_message([{"type": "future_block", "x": 1}]))

        assert self.labels(parts) == (MessageContextOrigins.TOOL_RESULT.label,)
        assert parts[0].byte_count > 0

    def test_lone_surrogate_content_still_attributes_to_its_origin(self) -> None:
        # ``"\ud800"`` is a legal JSON string escape, so an MCP server or a
        # provider can hand this runtime a str holding an unpaired surrogate.
        # A plain ``.encode("utf-8")`` raises on one, which would push an
        # ordinary tool result into the fail-open guard and record it as a
        # zero-byte UNDECLARED part — making the field that flags real
        # declaration breaches (§4.4) fire on text nobody declared wrong.
        body = f"orphan {chr(0xD800)} tail"

        parts = self.classify(self.tool_message(body))

        assert self.labels(parts) == (MessageContextOrigins.TOOL_RESULT.label,)
        assert parts[0].byte_count == len(body.encode("utf-8", "surrogatepass"))


class TestContentShapeLadder(MessageFixtureMixin):
    """Every rung of materialization, including the ones that give up.

    The classifier's contract is that *any* content shape produces a measured
    row. These are the shapes a message can carry once middleware and library
    code have had their turn — string blocks, scalars, unserializable objects —
    and each must land on a number rather than on an exception.
    """

    def test_string_content_blocks_are_joined_verbatim(self) -> None:
        parts = self.classify(self.tool_message(["alpha", "beta"]))

        assert self.labels(parts) == (MessageContextOrigins.TOOL_RESULT.label,)
        assert parts[0].text == "alphabeta"

    def test_bare_base64_typed_block_is_recognised(self) -> None:
        parts = self.classify(self.tool_message([{"type": "base64", "data": "QQ=="}]))

        assert self.labels(parts) == (MessageContextOrigins.WORKSPACE_BINARY_B64.label,)

    def test_non_mapping_blocks_are_rendered_rather_than_dropped(self) -> None:
        # Built on a stub rather than a ToolMessage on purpose: LangChain
        # coerces a scalar inside a content list to ``str``, which would hide
        # the rung being tested. Nothing guarantees every producer on the
        # model-call path applies that coercion first.
        parts = self.classify(StubMessage([1, "x"]))

        assert parts[0].text == "1x", (
            "a block shape the classifier does not model still occupies context"
        )

    def test_single_mapping_content_is_one_block(self) -> None:
        parts = self.classify(StubMessage({"type": "text", "text": "solo"}))

        assert parts[0].text == "solo"

    def test_scalar_content_is_rendered_as_json(self) -> None:
        parts = self.classify(StubMessage(42))

        assert parts[0].text == "42"

    def test_unserializable_content_falls_back_to_its_string_form(self) -> None:
        # Mixed-type keys defeat ``sort_keys`` — a real shape for a tool result
        # that was deserialized loosely. The row reports the value's string
        # form: a worse number than canonical JSON, never a failed run.
        parts = self.classify(StubMessage({1: "a", "b": 2}))

        assert parts[0].byte_count > 0

    def test_content_with_no_string_form_measures_as_empty(self) -> None:
        parts = self.classify(StubMessage(UnrenderableContent()))

        assert self.labels(parts) == (UNDECLARED_CONTEXT_LABEL,)
        assert parts[0].byte_count == 0


class TestContentIsMaterializedOnce(MessageFixtureMixin):
    """§3.4 — seven rules read one message; the message is rendered once.

    A cost property and a correctness property in one assertion. Rendering per
    rule would make classification a multiple of request size against a p95
    budget of 15 ms *including* the tokenizer, and a message is a library object
    whose ``content`` is not contractually a pure read — two renders could
    disagree, which would break the note split's "the parts sum back to the
    message" claim.
    """

    def test_string_content_is_read_once(self) -> None:
        message = CountingContentMessage("a plain body")

        ContextMessageClassifier.classify((message,))

        assert message.reads == 1

    def test_block_content_is_read_once(self) -> None:
        message = CountingContentMessage(
            [
                {"type": "thinking", "thinking": "deciding"},
                {"type": "text", "text": "answering"},
            ]
        )

        ContextMessageClassifier.classify((message,))

        assert message.reads == 1


class TestDetailIsAnIdentifierNotContent(MessageFixtureMixin):
    """§6.5 — occupancy is served over HTTP, so ``detail`` carries no content."""

    def test_detail_is_the_message_ordinal(self) -> None:
        parts = self.classify(
            HumanMessage(content="first"),
            AIMessage(content="second"),
        )

        assert tuple(part.detail for part in parts) == ("msg[0]", "msg[1]")

    def test_detail_ignores_large_multiline_content(self) -> None:
        body = "line one\nline two\n" * 500

        parts = self.classify(self.tool_message(body))

        detail = parts[0].detail
        assert detail == "msg[0]"
        assert detail is not None
        assert len(detail) <= ClassifiedMessagePart.MAX_DETAIL_LENGTH

    def test_detail_is_accepted_by_the_persisted_segment_contract(self) -> None:
        counter = ContextTokenCounter(cache=DigestTokenCache(max_entries=8))
        part = self.classify(self.tool_message(self.RESULT_BODY))[0]
        assert part.origin is not None

        segment = ContextSegment.measure(
            part.text,
            counter=counter,
            model="gpt-5.4-mini",
            origin=part.origin,
            detail=part.detail,
            item_count=part.item_count,
        )

        assert segment.label == MessageContextOrigins.TOOL_RESULT.label
        assert segment.segment_class is ContextSegmentClass.MESSAGES
        assert segment.byte_count == part.byte_count


class TestPartTextNeverReachesALogLine(MessageFixtureMixin):
    """``text`` is the one content-bearing field in the family; it stays local."""

    def test_safe_dump_elides_the_part_text(self) -> None:
        part = self.classify(HumanMessage(content="my home address is …"))[0]

        dumped = SafeLogDumper.dump_safe(part)

        assert "text" not in dumped
        assert dumped["label"] == MessageContextOrigins.USER.label
        assert dumped["byte_count"] == part.byte_count

    def test_the_part_is_tagged_sensitive(self) -> None:
        assert "text" in SafeLogDumper.sensitive_field_names(ClassifiedMessagePart)


class ContractBoundsMixin:
    """Reads a declared field bound off a contract instead of restating it."""

    @staticmethod
    def max_length_of(model: type[Any], field: str) -> int:
        """Return the single ``max_length`` ``model.field`` declares."""

        bounds = [
            constraint.max_length
            for constraint in model.model_fields[field].metadata
            if isinstance(constraint, MaxLen)
        ]
        assert len(bounds) == 1, f"{model.__name__}.{field} declares no single bound"
        return bounds[0]


class TestClassifiedMessagePartContract(ContractBoundsMixin, MessageFixtureMixin):
    """The part contract cannot state a declaration it did not measure."""

    def test_declared_part_mirrors_its_origin(self) -> None:
        part = ClassifiedMessagePart.declared(
            MessageContextOrigins.USER,
            text="hello",
            detail="msg[0]",
        )

        assert part.label == MessageContextOrigins.USER.label
        assert part.lifecycle is MessageContextOrigins.USER.lifecycle
        assert part.segment_class is ContextSegmentClass.MESSAGES
        assert part.is_undeclared is False

    def test_undeclared_part_carries_the_reserved_sentinel(self) -> None:
        part = ClassifiedMessagePart.undeclared(text="mystery", detail="msg[4]")

        assert part.label == UNDECLARED_CONTEXT_LABEL
        assert part.origin is None
        assert part.is_undeclared is True
        assert part.lifecycle is ClassifiedMessagePart.UNDECLARED_LIFECYCLE

    def test_byte_count_must_describe_the_text(self) -> None:
        with pytest.raises(ValidationError):
            ClassifiedMessagePart(
                label=MessageContextOrigins.USER.label,
                lifecycle=ContextLifecycle.PER_TURN,
                byte_count=99,
                text="hello",
                origin=MessageContextOrigins.USER,
            )

    def test_label_must_mirror_the_origin(self) -> None:
        with pytest.raises(ValidationError):
            ClassifiedMessagePart(
                label="agent_runtime.conversation:something_else",
                lifecycle=ContextLifecycle.PER_TURN,
                byte_count=5,
                text="hello",
                origin=MessageContextOrigins.USER,
            )

    def test_a_part_without_an_origin_must_be_undeclared(self) -> None:
        with pytest.raises(ValidationError):
            ClassifiedMessagePart(
                label=MessageContextOrigins.USER.label,
                lifecycle=ContextLifecycle.PER_TURN,
                byte_count=5,
                text="hello",
                origin=None,
            )

    def test_a_non_message_origin_is_rejected(self) -> None:
        tool_origin = ContextOrigin(
            owner="agent_runtime.capabilities.backends",
            name="publish_artifact",
            segment_class=ContextSegmentClass.TOOLS,
            lifecycle=ContextLifecycle.RESIDENT,
        )

        with pytest.raises(ValidationError):
            ClassifiedMessagePart.declared(tool_origin, text="hello")

    def test_segment_class_cannot_be_anything_but_messages(self) -> None:
        with pytest.raises(ValidationError):
            ClassifiedMessagePart(
                label=UNDECLARED_CONTEXT_LABEL,
                segment_class=ContextSegmentClass.TOOLS,
                lifecycle=ContextLifecycle.PER_TURN,
                byte_count=0,
                text="",
            )

    def test_label_and_detail_bounds_match_the_persisted_segment(self) -> None:
        # A part exists to become a ``ContextSegment``, so the bounds must agree:
        # a part validating under a wider bound would push the failure one step
        # downstream, into the pass that can no longer fall back to an UNDECLARED
        # row for it.
        #
        # This assertion earned its keep. All three label bounds were once
        # restated literals reading 240, while a valid ContextOrigin can spell
        # 401 — so measurement raised on a legal declaration, on the model-call
        # path that §6.4 says must never fail a run. This test is what caught
        # the first two being widened without the third. They now all import the
        # bound from ``context_origin``, which owns the definition of a label
        # and (contrary to the note that used to live here) pulls in neither the
        # token counter nor litellm.
        assert ClassifiedMessagePart.MAX_LABEL_LENGTH == self.max_length_of(
            ContextSegment, "label"
        )
        assert ClassifiedMessagePart.MAX_LABEL_LENGTH == MAX_CONTEXT_LABEL_LENGTH
        assert (
            ClassifiedMessagePart.MAX_DETAIL_LENGTH == ContextSegment.MAX_DETAIL_LENGTH
        )


class TestMessageOriginInventory:
    """The §4.6 table, pinned — a new message origin is a conscious decision."""

    EXPECTED_LABELS: Final[tuple[str, ...]] = (
        "agent_runtime.capabilities.workspace:binary_b64",
        "agent_runtime.capabilities:citation_pointer_note",
        "agent_runtime.capabilities:tool_budget_note",
        "agent_runtime.context.memory:subagent_trace",
        "agent_runtime.context.memory:summary",
        "agent_runtime.context:offload_stub",
        "agent_runtime.conversation:assistant_text",
        "agent_runtime.conversation:assistant_thinking",
        "agent_runtime.conversation:assistant_tool_calls",
        "agent_runtime.conversation:tool_result",
        "agent_runtime.conversation:user",
        "agent_runtime.execution:state_file",
    )

    def test_inventory_matches_the_design_table(self) -> None:
        labels = tuple(origin.label for origin in MessageContextOrigins.ALL)

        assert labels == self.EXPECTED_LABELS

    def test_inventory_is_label_sorted(self) -> None:
        labels = [origin.label for origin in MessageContextOrigins.ALL]

        assert labels == sorted(labels)

    def test_labels_are_unique(self) -> None:
        labels = [origin.label for origin in MessageContextOrigins.ALL]

        assert len(set(labels)) == len(labels)

    def test_every_origin_declares_the_messages_segment_class(self) -> None:
        assert all(
            origin.segment_class is ContextSegmentClass.MESSAGES
            for origin in MessageContextOrigins.ALL
        )

    def test_no_message_origin_is_third_party(self) -> None:
        # These are first-party declarations even when a third party produced
        # the text: the runtime owns the fix (a compaction setting, a note the
        # wrappers append), which is what ``third_party`` is read for.
        assert not any(origin.third_party for origin in MessageContextOrigins.ALL)

    def test_no_message_origin_claims_cache_eligibility(self) -> None:
        assert all(
            origin.cache_eligibility is None for origin in MessageContextOrigins.ALL
        )

    def test_every_origin_in_the_inventory_is_reachable(self) -> None:
        # The inventory is only worth pinning if it describes what the rules can
        # actually emit. A corpus exercising all seven rules must cover it
        # exactly: a declaration nothing reaches is dead, and a label the rules
        # emit from outside the inventory would slip past the gate.
        emitted = {
            part.label for part in ContextMessageClassifier.classify(self.rule_corpus())
        }

        assert emitted == set(self.EXPECTED_LABELS)

    @classmethod
    def rule_corpus(cls) -> tuple[object, ...]:
        """One message per §4.6 rule, including both split note kinds."""

        fixtures = MessageFixtureMixin()
        annotated = fixtures.annotate(
            "result body",
            fixtures.budget_note(),
            fixtures.citation_note(),
        )
        adapter = ToolResultAdmissionAdapter(
            offload_writer=lambda _content: "/offload/ref-1"
        )
        return (
            HumanMessage(content="find me the paper"),
            AIMessage(
                content=[
                    {"type": "thinking", "thinking": "deciding"},
                    {"type": "text", "text": "on it"},
                ],
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": fixtures.SUBAGENT_PATH},
                        "id": "call-trace",
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(
                content="# Subagent task-7",
                tool_call_id="call-trace",
                name="read_file",
            ),
            fixtures.tool_message(annotated),
            fixtures.tool_message(
                adapter.admit("x" * 200_000, trace_id="trace-1").model_content,
                call_id="call-offload",
            ),
            fixtures.tool_message(
                [{"type": "file", "base64": "QUJDRA=="}],
                call_id="call-binary",
            ),
            HumanMessage(
                content="<summary>compacted</summary>",
                additional_kwargs={"lc_source": "summarization"},
            ),
            HumanMessage(
                content="preview",
                additional_kwargs={"lc_evicted_to": "/state/msg.md"},
            ),
        )
