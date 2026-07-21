"""A fake "replay" completion for the hermetic eval harness (PRD-11).

Returns a fixture's *recorded output* for every call, ignoring the prompt. This
makes the whole harness — generation pipeline, scorers, and injection lint — run
deterministically in CI as unit tests, with no live model. Because the recorded
output is returned on every attempt, a deliberately-unsafe recorded output is
returned again on retry and correctly ends in a ``GenFailure`` (lint rejection),
exercising the reject path hermetically.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from agent_runtime.capabilities.surfaces.generator import SpecCompletionResult


class ReplayCompletion:
    """A :class:`SpecCompletionPort` that replays one recorded spec output.

    Deep-copies on each call so the generator's ``source`` force never mutates
    the shared corpus datum. ``model`` is fixed to ``"replay"`` so eval reports
    are stamped with a stable, non-sensitive model id.
    """

    MODEL_ID = "replay"

    def __init__(self, recorded_output: dict[str, Any]) -> None:
        self._recorded = recorded_output

    async def complete(self, *, system: str, user: str) -> SpecCompletionResult:
        candidate = copy.deepcopy(self._recorded)
        return SpecCompletionResult(
            candidate=candidate,
            raw_text=json.dumps(candidate, ensure_ascii=False, sort_keys=True),
            model=self.MODEL_ID,
            input_tokens=None,
            output_tokens=None,
        )


__all__ = ["ReplayCompletion"]
