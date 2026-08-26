"""Pin the wire shape of ``surface_spec_requested`` — the shaping progress signal.

The transport layer is where an unregistered event type goes wrong quietly: an
``event_type`` that is not an enum member cannot be appended at all
(``append_api_event`` takes the enum), and a payload with no allow-list entry
falls through the projector unfiltered — so the same emit that looks correct in
a domain test ships either nothing or everything.

These tests pin the three facts a client is written against:

* the type is a registered member, spelled exactly ``surface_spec_requested``;
* the payload is exactly ``{surface_id, model_id}``, both keys ALWAYS present,
  with ``None`` for anything that is not a non-empty string. The contract types
  both as ``string|null``, and a progress signal is read by a client that has
  nothing else to go on: an omitted key and an explicit null are the same fact
  only if the key is always there to read;
* it is an ``EVENT``, not a timeline card. A run that shapes five surfaces must
  merge five pending states, not append five rows.
"""

from __future__ import annotations

from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.schemas.common import RuntimeActivityKind, RuntimeApiEventType
from runtime_api.schemas.events import RuntimeEventPresentationProjector

_SURFACE_ID = "record://customsvc/get_thing/1"
_MODEL_ID = "claude-haiku-4-5"
_REQUESTED = RuntimeApiEventType.SURFACE_SPEC_REQUESTED


def _project(payload: dict[str, object]) -> dict[str, object]:
    return RuntimeEventPresentationProjector.payload_for_event(
        event_type=_REQUESTED,
        payload=payload,
    )


class TestRegistration:
    def test_the_wire_value_is_the_contract_spelling(self) -> None:
        assert _REQUESTED.value == "surface_spec_requested"

    def test_the_value_converts_back_to_the_member(self) -> None:
        # The call every emitter that converts by value makes. A member present
        # under a different spelling would pass the test above and raise here.
        assert RuntimeApiEventType("surface_spec_requested") is _REQUESTED


class TestPayloadProjection:
    def test_the_contract_payload_round_trips(self) -> None:
        assert _project({"surface_id": _SURFACE_ID, "model_id": _MODEL_ID}) == {
            "surface_id": _SURFACE_ID,
            "model_id": _MODEL_ID,
        }

    def test_both_keys_are_present_as_null_when_unknown(self) -> None:
        assert _project({}) == {"surface_id": None, "model_id": None}

    def test_blank_and_non_string_values_become_null(self) -> None:
        assert _project({"surface_id": "   ", "model_id": 7}) == {
            "surface_id": None,
            "model_id": None,
        }

    def test_nothing_else_rides_the_envelope(self) -> None:
        # The allow-list is the defence: a future emitter that reaches for the
        # sample output, the connector token or the tool arguments cannot leak
        # them through a progress event.
        assert _project(
            {
                "surface_id": _SURFACE_ID,
                "model_id": _MODEL_ID,
                "sample_output": {"email": "someone@example.com"},
                "api_key": "sk-should-never-appear",
            }
        ) == {"surface_id": _SURFACE_ID, "model_id": _MODEL_ID}


class TestPresentation:
    def test_it_is_an_event_not_a_timeline_card(self) -> None:
        assert (
            RuntimeEventPresentationProjector.activity_kind_for(
                event_type=_REQUESTED,
                source=StreamEventSource.SYSTEM,
            )
            is RuntimeActivityKind.EVENT
        )

    def test_a_tool_sourced_emit_is_still_an_event(self) -> None:
        # Stated separately because the fallback routes by SOURCE: the surface
        # pipeline is driven off tool results, so a future emit re-sourced to
        # TOOL would otherwise land in the tool bucket and render as a card.
        assert (
            RuntimeEventPresentationProjector.activity_kind_for(
                event_type=_REQUESTED,
                source=StreamEventSource.TOOL,
            )
            is RuntimeActivityKind.EVENT
        )

    def test_the_display_title_is_the_present_tense_of_its_sibling(self) -> None:
        fields = RuntimeEventPresentationProjector.presentation_fields(
            event_type=_REQUESTED,
            source=StreamEventSource.SYSTEM,
            parent_task_id=None,
            payload={"surface_id": _SURFACE_ID, "model_id": _MODEL_ID},
            metadata={},
        )
        assert fields["display_title"] == "Preparing a view"
