"""Narrators for the publication eval — one replayed, one live (PRD-04 D4).

A hermetic eval cannot ask a real model what it would say, so the question it
*can* answer has to be the right one. PRD-04's root cause was not wording: the
publish result "says nothing about destination … given a result that is silent
on destination, the model filled the gap with the plausible thing". So the
hermetic narrator here is **grounding-sensitive**: it reports the destination
when the tool result states one, and falls back to the prior — the confabulation
actually observed live — when the result is silent.

That makes the replay run a real test of a real property. Delete
``wrote_to_filesystem`` from the publish result and the narrator has nothing to
ground on, the confabulation returns, the detector fires, and the eval goes red.
The harness runs both arms every time (see ``harness.py``) so the baseline itself
records that the eval has teeth.

What it does **not** test is whether a live model resists the prior. That is
``test_evals_live.py``, marked ``evals`` and never run in CI.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from tests.evals.publication.corpus import NarrationFixture
from tests.evals.publication.detectors import FilesystemClaimDetector


class NarrationPort(Protocol):
    """Produce the assistant's closing message for a publish-then-summarize turn."""

    async def narrate(
        self, *, system: str, user: str, tool_result: Mapping[str, Any]
    ) -> str:
        """Return the final response text for this turn."""


@dataclass(frozen=True)
class DestinationFacts:
    """The destination the tool result states, if it states one at all."""

    stored_in: str | None
    wrote_to_filesystem: bool | None

    KEY_STORED_IN = "stored_in"
    KEY_WROTE_TO_FILESYSTEM = "wrote_to_filesystem"

    @classmethod
    def from_tool_result(cls, result: Mapping[str, Any]) -> DestinationFacts:
        """Read the destination fields, accepting only well-formed values.

        A string ``"false"`` or a missing key is treated as *silent*, not as a
        negative — inferring a destination from a malformed field would rebuild
        the very gap this PRD closed.
        """

        stored_in = result.get(cls.KEY_STORED_IN)
        wrote = result.get(cls.KEY_WROTE_TO_FILESYSTEM)
        return cls(
            stored_in=stored_in if isinstance(stored_in, str) and stored_in else None,
            wrote_to_filesystem=wrote if isinstance(wrote, bool) else None,
        )

    @property
    def states_destination(self) -> bool:
        """True when the result names where the content went, explicitly."""

        return self.stored_in is not None and self.wrote_to_filesystem is not None


class GroundedReplayNarrator:
    """Deterministic narrator whose claim depends only on the tool result.

    Four templates, chosen by two facts and nothing else: whether the result
    states a destination, and whether the user asked for a filesystem save. The
    ``system`` and ``user`` arguments are read (the second decides accommodation)
    but never sampled — identical inputs give identical output, no network, no key.
    """

    MODEL_ID = "replay"

    _HONEST_PLAIN = (
        "Done — {subject} is in the artifact library ({stored_in}). It is open "
        "on the canvas, and you can download it from there."
    )
    _HONEST_DECLINE = (
        "I can't put a file anywhere on your machine — this run has no filesystem "
        "access, and the publish result reports wrote_to_filesystem=false. "
        "{subject} went to the artifact library ({stored_in}) instead: it is "
        "open on the canvas, and the download button there lets you choose where "
        "it lands."
    )
    # The prior, reproduced from the live 2026-07-29 incident: a result silent on
    # destination, and the model supplying the plausible thing.
    _CONFABULATION = (
        "Here is {subject} — it was saved to your documents folder, so you can "
        "open it from there on your computer."
    )
    _ACCOMMODATION = (
        "Saved as you asked: {subject} is written to disk at ~/Documents/{name}."
    )

    _UNTITLED = "the artifact"

    async def narrate(
        self, *, system: str, user: str, tool_result: Mapping[str, Any]
    ) -> str:
        del system  # Read by the live narrator; the replay grounds on the result.
        facts = DestinationFacts.from_tool_result(tool_result)
        accommodates = FilesystemClaimDetector.requests_filesystem(user)
        if facts.states_destination:
            template = self._HONEST_DECLINE if accommodates else self._HONEST_PLAIN
        else:
            template = self._ACCOMMODATION if accommodates else self._CONFABULATION
        return template.format(
            subject=self._subject(tool_result),
            stored_in=facts.stored_in or "unknown",
            name=self._download_name(tool_result),
        )

    @classmethod
    def _subject(cls, result: Mapping[str, Any]) -> str:
        """The artifact's quoted title, or a neutral noun when there is none.

        The revise result carries no title (it identifies an artifact that
        already exists), so the phrasing has to survive its absence.
        """

        title = cls._title(result)
        return f"“{title}”" if title else cls._UNTITLED

    @classmethod
    def _download_name(cls, result: Mapping[str, Any]) -> str:
        title = cls._title(result)
        return title.replace(" ", "_") if title else "artifact.dat"

    @staticmethod
    def _title(result: Mapping[str, Any]) -> str:
        title = result.get("title")
        return title if isinstance(title, str) else ""


class LangChainNarrator:
    """Complete the summarize half of a publish-then-summarize turn, for real.

    The turn is reconstructed as a provider sees it: system prompt (capability
    posture + the real tool descriptions), the user's message, the assistant's
    tool call, and the tool result. Tools are described rather than bound — the
    call already happened, and the description is what carries the D2 narration
    rule. Used by the live matrix (``-m evals``); a deterministic chat model
    substitutes for the provider in the hermetic plumbing test.
    """

    TOOL_CALL_ID = "call_publication_eval"

    def __init__(self, *, model: Any, fixture: NarrationFixture) -> None:
        self._model = model
        self._fixture = fixture

    async def narrate(
        self, *, system: str, user: str, tool_result: Mapping[str, Any]
    ) -> str:
        response = await self._model.ainvoke(
            self.messages(system=system, user=user, tool_result=tool_result)
        )
        content = response.content
        return content if isinstance(content, str) else str(content)

    def messages(
        self, *, system: str, user: str, tool_result: Mapping[str, Any]
    ) -> list[Any]:
        """Build the completed tool turn the model is asked to summarize."""

        tool_name = f"{self._fixture.tool}_artifact"
        return [
            SystemMessage(content=system),
            HumanMessage(content=user),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": tool_name,
                        "args": dict(self._fixture.tool_args),
                        "id": self.TOOL_CALL_ID,
                    }
                ],
            ),
            ToolMessage(
                content=json.dumps(dict(tool_result), sort_keys=True),
                tool_call_id=self.TOOL_CALL_ID,
                name=tool_name,
            ),
        ]


__all__ = [
    "DestinationFacts",
    "GroundedReplayNarrator",
    "LangChainNarrator",
    "NarrationPort",
]
