"""The §6.5 no-content-leakage property, quantified over generated requests.

Design §9's last bullet asks for a *property* test rather than an example one:
"over segments asserting ``detail`` never contains message or tool-result
content". The distinction matters here more than usual, because occupancy is the
one observability lane that is **exposed over an HTTP read API** (§7) and whose
inputs are almost entirely untrusted — model output, tool results, MCP tool
descriptors, and workspace file bodies all pass through the request this ledger
measures. An example test proves one shape does not leak. This proves that
*every* generated shape does not, end to end through the three surfaces content
would have to cross to escape:

1. the domain segment the recorder measures,
2. the JSONB envelope the persistence boundary accepts, and
3. the payload the read API and the ``context_occupancy`` stream event publish.

The generator is seeded rather than random so a failure is reproducible, and the
markers it plants are checked for *substring* presence: any tokenizer, any
truncation, and any digest would all still leave a recognizable fragment behind,
so a leak cannot hide behind a transformation.

Deliberately **not** an assertion about a fixed set of field names. A field
added to a segment tomorrow would pass a name allow-list while carrying content;
sweeping every string that reaches the wire is the invariant that survives the
contract growing.
"""

from __future__ import annotations

import json
import random
from types import SimpleNamespace
from typing import Any, Final, cast

from langchain.agents.middleware.types import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel
import pytest

from agent_runtime.observability.context_occupancy import GraphScope
from agent_runtime.observability.context_occupancy_recorder import (
    ContextOccupancyRecorder,
)
from agent_runtime.persistence.records import RuntimeContextOccupancyRecord
from runtime_api.schemas.context_occupancy import ContextOccupancySnapshotPayload
from runtime_api.schemas.events import ContextOccupancyPayload


#: Markers planted in every untrusted position a request can carry. Each is
#: shaped like something a compliance reviewer would care about escaping, and
#: each is long and distinctive enough that a substring search cannot false-negative.
_MARKERS: Final[tuple[str, ...]] = (
    "SSN-123-45-6789",
    "4111111111111111",
    "sk-live-DEADBEEFCAFE",
    "patient.zero@example.invalid",
    "/Users/someone/Documents/board-deck-q3.xlsx",
    "BEGIN RSA PRIVATE KEY",
)


class _ToolArgs(BaseModel):
    query: str


class HostileRequestFactory:
    """Generate provider requests whose every untrusted slot carries a marker.

    Untrusted per this service's own rules: model output, tool payloads, MCP
    descriptors, and memory. The generator therefore plants markers in the
    system text, in tool names *and* descriptions (an MCP registry names its own
    tools), in human / assistant / tool message bodies, in an assistant
    tool-call's arguments, in a structured content block, and in the
    ``response_format`` schema — every position §2's inventory says can occupy a
    window.
    """

    def __init__(self, seed: int) -> None:
        self._random = random.Random(seed)

    def _marked(self, prefix: str) -> str:
        marker = self._random.choice(_MARKERS)
        filler = "".join(
            self._random.choices("abcdefgh ", k=self._random.randint(0, 80))
        )
        return f"{prefix} {filler} {marker} {filler}"

    def _tool(self) -> StructuredTool:
        # A tool NAME is the one untrusted string §6.5 explicitly allows into
        # ``detail``, so the generator makes it hostile on purpose: the property
        # below still holds because the recorder sanitizes and bounds it, and a
        # regression that widened that bound would surface here.
        return StructuredTool.from_function(
            func=lambda query: query,
            name=f"mcp_{self._random.randint(0, 999)}",
            description=self._marked("Use this to"),
            args_schema=_ToolArgs,
        )

    def _messages(self) -> list[object]:
        messages: list[object] = [HumanMessage(content=self._marked("please analyse"))]
        for _ in range(self._random.randint(1, 4)):
            kind = self._random.randint(0, 3)
            if kind == 0:
                messages.append(AIMessage(content=self._marked("here is what I found")))
            elif kind == 1:
                messages.append(
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "search",
                                "args": {"query": self._marked("lookup")},
                                "id": f"tc_{self._random.randint(0, 999)}",
                            }
                        ],
                    )
                )
            elif kind == 2:
                messages.append(
                    ToolMessage(
                        content=self._marked("row 1:"),
                        tool_call_id=f"tc_{self._random.randint(0, 999)}",
                    )
                )
            else:
                messages.append(
                    HumanMessage(
                        content=[
                            {"type": "text", "text": self._marked("attached")},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": self._marked("data:image/png;base64,")
                                },
                            },
                        ]
                    )
                )
        return messages

    def build(self, *, child: bool) -> ModelRequest[Any]:
        metadata = {"supervisor_task_call_id": "task_leak"} if child else {}
        return ModelRequest(
            model=FakeListChatModel(responses=["done"]),
            messages=self._messages(),
            system_message=SystemMessage(content=self._marked("You are an agent.")),
            tools=[self._tool() for _ in range(self._random.randint(1, 3))],
            state={"runtime_control_model_turn": 1},
            runtime=cast(Any, SimpleNamespace(config={"metadata": metadata})),
            model_settings={},
            response_format=(_ToolArgs if self._random.randint(0, 1) else None),
        )


class OccupancySurfacesMixin:
    """Drive one generated request through all three publishable surfaces."""

    ORG_ID: Final[str] = "org_leak"
    RUN_ID: Final[str] = "run_leak"
    CONVERSATION_ID: Final[str] = "conv_leak"

    def surfaces(self, request: ModelRequest[Any]) -> tuple[str, str, str]:
        """Return ``(segments_json, read_payload_json, stream_payload_json)``.

        A fresh recorder per call so the digest memoization cannot mask a leak by
        answering from a cache populated by an earlier, cleaner request.
        """

        recorder = ContextOccupancyRecorder()
        snapshot = recorder.capture(
            request,
            identity=SimpleNamespace(
                model_call_id="call_leak", execution_scope="supervisor"
            ),
            attempt_ordinal=1,
            graph_scope=GraphScope.ROOT,
            provider="anthropic",
            model_family="claude-opus-4-7",
            context_window_tokens=200_000,
        )
        assert snapshot is not None
        record = recorder.project(
            snapshot,
            org_id=self.ORG_ID,
            run_id=self.RUN_ID,
            conversation_id=self.CONVERSATION_ID,
        )
        payload = ContextOccupancySnapshotPayload.from_record(record)
        stream = ContextOccupancyPayload(snapshot=payload)
        return (
            json.dumps(record.segments_json),
            json.dumps(payload.model_dump(mode="json")),
            json.dumps(stream.model_dump(mode="json")),
        )


class TestSegmentsNeverCarryContent(OccupancySurfacesMixin):
    """§6.5, over generated requests rather than one fixture."""

    @pytest.mark.parametrize("seed", range(24))
    @pytest.mark.parametrize("child", [False, True])
    def test_no_marker_reaches_any_publishable_surface(
        self,
        seed: int,
        child: bool,
    ) -> None:
        """Not one marker survives into the row, the read payload, or the event.

        Both graph scopes, because a subagent call materializes a different
        request shape and the design measures it through the same seam (§3.1).
        """

        request = HostileRequestFactory(seed).build(child=child)

        for surface in self.surfaces(request):
            for marker in _MARKERS:
                assert marker not in surface, (
                    f"context occupancy leaked {marker!r} onto a published surface"
                )

    @pytest.mark.parametrize("seed", range(12))
    def test_every_detail_stays_a_single_printable_token(self, seed: int) -> None:
        """The shape §6.5 promises: an identifier, not a line of text.

        Asserted over the *stored* envelope rather than the domain object, because
        the row is what an HTTP read and a stream event are both projected from —
        and because a bound that holds only in memory is not a bound.
        """

        recorder = ContextOccupancyRecorder()
        snapshot = recorder.capture(
            HostileRequestFactory(seed).build(child=False),
            identity=SimpleNamespace(
                model_call_id="call_leak", execution_scope="supervisor"
            ),
            attempt_ordinal=1,
            graph_scope=GraphScope.ROOT,
            provider="anthropic",
            model_family="claude-opus-4-7",
            context_window_tokens=200_000,
        )
        assert snapshot is not None
        record = recorder.project(
            snapshot,
            org_id=self.ORG_ID,
            run_id=self.RUN_ID,
            conversation_id=self.CONVERSATION_ID,
        )

        assert record.segments, "the generated request must produce segments"
        for segment in record.segments:
            detail = segment.get("detail")
            if detail is None:
                continue
            assert isinstance(detail, str)
            assert len(detail) <= (
                RuntimeContextOccupancyRecord.Limits.MAX_SEGMENT_DETAIL_CHARS
            )
            assert detail == detail.strip("\r\n")
            assert not any(
                character < " " or character == "\x7f" for character in detail
            )


class TestTheBoundaryRefusesSmuggledContent(OccupancySurfacesMixin):
    """A writer that is not the recorder must not be able to store content.

    The recorder is well-behaved; the point of a durability-boundary invariant is
    that it holds for writers that are not. These are the two shapes a leak would
    actually take — a body short enough to pass a length check, and a body pasted
    into a field the length check has to leave wide for a 401-character label.
    """

    def _segment(self, **overrides: object) -> dict[str, object]:
        segment: dict[str, object] = {
            "segment_class": "messages",
            "label": "agent_runtime.conversation:tool_result",
            "lifecycle": "per_result",
            "third_party": False,
            "detail": "msg[3]",
            "byte_count": 64,
            "estimated_tokens": 16,
            "item_count": 1,
            "cache_eligibility": None,
            "counter_source": "tokenizer",
        }
        segment.update(overrides)
        return segment

    def _row(self, segment: dict[str, object]) -> RuntimeContextOccupancyRecord:
        return RuntimeContextOccupancyRecord.from_measurement(
            org_id=self.ORG_ID,
            run_id=self.RUN_ID,
            conversation_id=self.CONVERSATION_ID,
            model_call_id="call_leak",
            provider="anthropic",
            model_family="claude-opus-4-7",
            estimated_input_tokens=16,
            segments=(segment,),
        )

    def test_a_detail_longer_than_an_identifier_is_refused_at_the_column(self) -> None:
        """The structural sweep has to admit a 401-char label; ``detail`` does not."""

        smuggled = "x" * (
            RuntimeContextOccupancyRecord.Limits.MAX_SEGMENT_DETAIL_CHARS + 1
        )

        with pytest.raises(ValueError):
            self._row(self._segment(detail=smuggled))

    @pytest.mark.parametrize(
        "field",
        ["detail", "label", "segment_class"],
    )
    def test_a_multi_line_segment_value_is_refused_at_the_column(
        self,
        field: str,
    ) -> None:
        """Short enough to pass a length bound, still unmistakably content.

        Applied to every string in the envelope, not only ``detail``: a leak that
        rides an unexpected field is exactly the one a name allow-list misses.
        """

        with pytest.raises(ValueError):
            self._row(self._segment(**{field: "row 1\nSSN-123-45-6789"}))

    def test_a_legitimate_segment_still_stores(self) -> None:
        """The guard must not be so tight that the real producer trips it."""

        record = self._row(self._segment())

        assert record.segment_count == 1
        assert record.segments[0]["detail"] == "msg[3]"
