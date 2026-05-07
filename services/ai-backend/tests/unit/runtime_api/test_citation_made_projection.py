"""Pin the wire-shape contract for ``citation_made`` events.

PR 1.1-rev2 — model-declared citation pointers.

These tests pin the projector's allow-list against shape regressions:

- ``source_tool_call_id`` is preserved as-is, including the empty string
  case (hallucinated ordinals, provider-native passthrough firing before
  a tool message materializes). The FE guard requires the field to be
  present as a string; dropping the field on the wire silently broke
  chip rendering for every assistant turn that emitted ``[[N]]`` for an
  unbound ordinal.
- ``message_id`` is required to be a non-empty string.
- ``conversation_ordinal`` must be a positive integer.
- Numeric offsets pass through.
"""

from __future__ import annotations

from runtime_api.schemas.common import RuntimeApiEventType
from runtime_api.schemas.events import RuntimeEventPresentationProjector


class _Values:
    ORDINAL = 7
    MESSAGE_ID = "msg_abc"
    TOOL_CALL_ID = "call_xyz"
    PROSE_OFFSET = 12
    PROSE_LENGTH = 6


def _project(payload: dict[str, object]) -> dict[str, object]:
    return RuntimeEventPresentationProjector.payload_for_event(
        event_type=RuntimeApiEventType.CITATION_MADE,
        payload=payload,
    )


class TestCitationMadeProjection:
    def test_full_payload_round_trips(self) -> None:
        out = _project(
            {
                "link": {
                    "conversation_ordinal": _Values.ORDINAL,
                    "message_id": _Values.MESSAGE_ID,
                    "prose_offset": _Values.PROSE_OFFSET,
                    "prose_length": _Values.PROSE_LENGTH,
                    "source_tool_call_id": _Values.TOOL_CALL_ID,
                }
            }
        )
        assert out == {
            "link": {
                "conversation_ordinal": _Values.ORDINAL,
                "message_id": _Values.MESSAGE_ID,
                "prose_offset": _Values.PROSE_OFFSET,
                "prose_length": _Values.PROSE_LENGTH,
                "source_tool_call_id": _Values.TOOL_CALL_ID,
            }
        }

    def test_empty_source_tool_call_id_is_preserved(self) -> None:
        # PR 1.1-rev2 regression — when the model emits ``[[N]]`` for an
        # ordinal the allocator hasn't bound to a tool_call_id (the
        # hallucination case), the resolver still emits the event with
        # ``source_tool_call_id=""``. The projector MUST keep the field
        # as an empty string so the FE type guard accepts it; previously
        # ``value.strip()`` filtered it out and the FE silently dropped
        # the event, leaving every chip as a muted ``?`` placeholder.
        out = _project(
            {
                "link": {
                    "conversation_ordinal": _Values.ORDINAL,
                    "message_id": _Values.MESSAGE_ID,
                    "prose_offset": _Values.PROSE_OFFSET,
                    "prose_length": _Values.PROSE_LENGTH,
                    "source_tool_call_id": "",
                }
            }
        )
        link = out["link"]
        assert isinstance(link, dict)
        assert link["source_tool_call_id"] == ""
        # Other fields still pass through unchanged.
        assert link["conversation_ordinal"] == _Values.ORDINAL
        assert link["message_id"] == _Values.MESSAGE_ID

    def test_missing_source_tool_call_id_defaults_to_empty_string(self) -> None:
        out = _project(
            {
                "link": {
                    "conversation_ordinal": _Values.ORDINAL,
                    "message_id": _Values.MESSAGE_ID,
                    "prose_offset": _Values.PROSE_OFFSET,
                    "prose_length": _Values.PROSE_LENGTH,
                }
            }
        )
        link = out["link"]
        assert isinstance(link, dict)
        assert link["source_tool_call_id"] == ""

    def test_non_string_source_tool_call_id_coerced_to_empty(self) -> None:
        out = _project(
            {
                "link": {
                    "conversation_ordinal": _Values.ORDINAL,
                    "message_id": _Values.MESSAGE_ID,
                    "prose_offset": _Values.PROSE_OFFSET,
                    "prose_length": _Values.PROSE_LENGTH,
                    "source_tool_call_id": 42,
                }
            }
        )
        link = out["link"]
        assert isinstance(link, dict)
        assert link["source_tool_call_id"] == ""

    def test_missing_link_returns_empty(self) -> None:
        assert _project({}) == {}

    def test_non_dict_link_returns_empty(self) -> None:
        assert _project({"link": "not a dict"}) == {}

    def test_empty_message_id_drops_field(self) -> None:
        out = _project(
            {
                "link": {
                    "conversation_ordinal": _Values.ORDINAL,
                    "message_id": "   ",
                    "prose_offset": _Values.PROSE_OFFSET,
                    "prose_length": _Values.PROSE_LENGTH,
                    "source_tool_call_id": _Values.TOOL_CALL_ID,
                }
            }
        )
        link = out["link"]
        assert isinstance(link, dict)
        assert "message_id" not in link

    def test_invalid_ordinal_drops_field(self) -> None:
        out = _project(
            {
                "link": {
                    "conversation_ordinal": -3,
                    "message_id": _Values.MESSAGE_ID,
                    "prose_offset": _Values.PROSE_OFFSET,
                    "prose_length": _Values.PROSE_LENGTH,
                    "source_tool_call_id": _Values.TOOL_CALL_ID,
                }
            }
        )
        link = out["link"]
        assert isinstance(link, dict)
        assert "conversation_ordinal" not in link

    def test_negative_offsets_drop(self) -> None:
        out = _project(
            {
                "link": {
                    "conversation_ordinal": _Values.ORDINAL,
                    "message_id": _Values.MESSAGE_ID,
                    "prose_offset": -1,
                    "prose_length": -2,
                    "source_tool_call_id": _Values.TOOL_CALL_ID,
                }
            }
        )
        link = out["link"]
        assert isinstance(link, dict)
        assert "prose_offset" not in link
        assert "prose_length" not in link
