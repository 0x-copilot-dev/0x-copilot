"""A reasoning delta is a chunk, so its edge whitespace must survive the funnel.

`_reasoning_summary_payload` passed both `summary` and `delta` through `_text`,
which strips. Every chunk boundary therefore lost its space: the provider sent
``"...either the system"`` then ``" date is set far in the future"`` and the
ledger stored them stripped, so the transcript rendered ``"the systemdate is
set"``. Reasoning showed this and the answer did not, because only reasoning is
projected through an allow-list that calls `_text` on its chunk — `model_delta`
never was, which is exactly what the captured ledger showed.

The sequences below are the real chunks from a run, with the spaces the provider
actually sent restored.
"""

from __future__ import annotations

from agent_runtime.api.constants import Keys
from runtime_api.schemas import (
    RuntimeApiEventType,
    RuntimeEventPresentationProjector,
)

# Captured from a live Anthropic run's ledger; the sole edit is restoring the
# boundary spaces the funnel had removed.
CHUNKS = [
    "The",
    " search results are showing inconsistent dates, either the system",
    " date is set far in the future or these are speculative articles.",
]
EXPECTED = (
    "The search results are showing inconsistent dates, either the system"
    " date is set far in the future or these are speculative articles."
)


def _delta_payload(chunk: str) -> dict[str, object]:
    """Project one delta exactly as the emitter writes it (same chunk twice)."""
    return RuntimeEventPresentationProjector.payload_for_event(
        event_type=RuntimeApiEventType.REASONING_SUMMARY_DELTA,
        payload={
            Keys.Payload.DELTA: chunk,
            Keys.Field.SUMMARY: chunk,
        },
    )


def test_reassembled_reasoning_keeps_every_boundary_space() -> None:
    """The whole point: concatenating the projected deltas rebuilds the text."""

    rebuilt = "".join(
        str(_delta_payload(c).get(Keys.Payload.DELTA, "")) for c in CHUNKS
    )

    assert rebuilt == EXPECTED
    # The specific glue-ups the user reported, stated so a regression names itself.
    assert "Thesearch" not in rebuilt
    assert "systemdate" not in rebuilt


def test_leading_and_trailing_space_both_survive() -> None:
    """`_text` stripped both edges; a chunk can carry either or both."""

    payload = _delta_payload(" the system ")

    assert payload[Keys.Payload.DELTA] == " the system "
    # The emitter writes the same chunk to `summary`, so it must not be stripped
    # either — the transcript is free to read whichever field it prefers.
    assert payload[Keys.Field.SUMMARY] == " the system "


def test_a_whitespace_only_chunk_is_kept() -> None:
    """It IS the space between two words.

    Guarding on the STRIPPED value would drop this chunk and re-create the bug
    one chunk further along, which is why the empty check is on the raw string.
    """

    payload = _delta_payload(" ")

    assert payload[Keys.Payload.DELTA] == " "


def test_an_empty_chunk_is_still_dropped() -> None:
    """Preserving whitespace must not turn into preserving nothing."""

    payload = _delta_payload("")

    assert Keys.Payload.DELTA not in payload
    assert Keys.Field.SUMMARY not in payload


def test_a_non_string_delta_is_still_rejected() -> None:
    """The allow-list is still a type gate, not just a whitespace policy."""

    payload = RuntimeEventPresentationProjector.payload_for_event(
        event_type=RuntimeApiEventType.REASONING_SUMMARY_DELTA,
        payload={Keys.Payload.DELTA: {"nested": "object"}},
    )

    assert Keys.Payload.DELTA not in payload


def test_the_final_assembled_summary_is_still_trimmed() -> None:
    """A whole thought is not a chunk — trimming its ends stays correct."""

    payload = RuntimeEventPresentationProjector.payload_for_event(
        event_type=RuntimeApiEventType.REASONING_SUMMARY,
        payload={Keys.Field.SUMMARY: "  the whole assembled thought  "},
    )

    assert payload[Keys.Field.SUMMARY] == "the whole assembled thought"
