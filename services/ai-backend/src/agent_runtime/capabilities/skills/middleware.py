"""Model-facing tools for virtual Skill discovery and loading.

Two tools, one progressive-disclosure ladder:

``list_skills``  search the run's *offered* Skill set — the same set the prompt
                 index was cut from, including the entries the index bound
                 deferred. This is the "ask for more" half of a bounded index:
                 without it, truncation would silently remove capability.
``load_skill``   read one Skill's full Markdown body.

``list_skills`` never widens visibility. It lists what
:class:`SkillUsageLedger` recorded as offered for this run, so a Skill whose
declared conditions were unmet stays unmentioned rather than becoming reachable
through a second door.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import Field, ValidationError

from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.capabilities.skills.constants import Keys, Limits
from agent_runtime.capabilities.skills.usage import SkillUsageLedger
from agent_runtime.capabilities.skills.virtual import (
    VirtualSkillBundle,
    VirtualSkillRegistry,
)
from agent_runtime.capabilities.skills.visibility import SkillCardText
from agent_runtime.prompts.tools import (
    LIST_SKILLS_TOOL_DESCRIPTION,
    LOAD_SKILL_TOOL_DESCRIPTION,
)


class LoadSkillInput(RuntimeContract):
    """Input contract for loading a virtual Skill by stable name."""

    skill_name: str = Field(min_length=1)


class ListSkillsInput(RuntimeContract):
    """Input contract for searching the Skills this run may reach."""

    query: str = ""
    limit: int = Field(
        default=Limits.SKILL_LIST_DEFAULT_LIMIT,
        ge=1,
        le=Limits.SKILL_LIST_MAX_LIMIT,
    )


@dataclass(frozen=True)
class LoadSkillTool:
    """Small adapter that lets the model load full Skill markdown on demand."""

    registry: VirtualSkillRegistry
    name: str = "load_skill"
    description: str = LOAD_SKILL_TOOL_DESCRIPTION

    async def ainvoke(
        self, raw_input: LoadSkillInput | Mapping[str, Any] | str
    ) -> dict[str, Any]:
        """Parse input, load the skill by name, and return a bundle payload or error dict."""
        parsed_input = LoadSkillInputParser.parse(raw_input)
        if isinstance(parsed_input, dict):
            return parsed_input
        try:
            bundle = await self.registry.load_skill_by_name(parsed_input.skill_name)
        except AgentRuntimeError as exc:
            return {
                "ok": False,
                "error": {
                    "code": exc.code.value,
                    "safe_message": exc.safe_message,
                    "retryable": exc.retryable,
                },
            }
        # The run's ONLY "this Skill was actually used" signal. Recorded on the
        # success path only: a failed load is not a use, and counting it would
        # make a broken Skill look valuable.
        SkillUsageLedger.record_use(bundle.name or parsed_input.skill_name)
        return self._bundle_payload(bundle)

    async def __call__(
        self, raw_input: LoadSkillInput | Mapping[str, Any] | str
    ) -> dict[str, Any]:
        """Delegate to ``ainvoke``."""
        return await self.ainvoke(raw_input)

    @classmethod
    def _bundle_payload(cls, bundle: VirtualSkillBundle) -> dict[str, Any]:
        """Serialise a ``VirtualSkillBundle`` to a JSON-ready dict with ``ok: True``."""
        payload = bundle.model_dump(mode="json")
        payload["ok"] = True
        return payload


@dataclass(frozen=True)
class ListSkillsTool:
    """Search the Skills this run may reach, including the deferred tail.

    The prompt index is bounded, so some offered Skills are named nowhere in the
    system prompt. This tool is how the model reaches them: one keyword search
    over name + description, returning compact rows only — the full body still
    costs a ``load_skill`` call.
    """

    registry: VirtualSkillRegistry
    runtime_context: AgentRuntimeContext
    name: str = "list_skills"
    description: str = LIST_SKILLS_TOOL_DESCRIPTION

    async def ainvoke(
        self, raw_input: ListSkillsInput | Mapping[str, Any] | str | None = None
    ) -> dict[str, Any]:
        """Return compact rows for the offered Skills matching ``query``."""
        parsed_input = ListSkillsInputParser.parse(raw_input)
        try:
            cards = await self.registry.list_available_skills(self.runtime_context)
        except AgentRuntimeError as exc:
            return {
                "ok": False,
                "error": {
                    "code": exc.code.value,
                    "safe_message": exc.safe_message,
                    "retryable": exc.retryable,
                },
            }
        offered = self._offered(cards)
        matches = [
            card for card in offered if self._matches(card, parsed_input.query.strip())
        ]
        return {
            "ok": True,
            "query": parsed_input.query.strip(),
            "total": len(matches),
            "skills": [self._row(card) for card in matches[: parsed_input.limit]],
        }

    async def __call__(
        self, raw_input: ListSkillsInput | Mapping[str, Any] | str | None = None
    ) -> dict[str, Any]:
        """Delegate to ``ainvoke``."""
        return await self.ainvoke(raw_input)

    @classmethod
    def _offered(cls, cards: Sequence[object]) -> tuple[object, ...]:
        """Restrict ``cards`` to what this run actually offered.

        A bound ledger that has recorded an index decision is authoritative:
        anything it excluded failed its own declared conditions and must not be
        listed here. With no ledger bound (replay, evals, unit tests) there is
        no decision to honour and the registry's own enabled set stands.
        """

        ledger = SkillUsageLedger.active()
        if ledger is None or not ledger.has_offer:
            return tuple(cards)
        offered = set(ledger.offered_names)
        return tuple(
            card
            for card in cards
            if SkillCardText.attribute(card, Keys.Fields.NAME) in offered
        )

    @classmethod
    def _matches(cls, card: object, query: str) -> bool:
        """Return ``True`` when ``query`` is empty or occurs in name/description."""
        if not query:
            return True
        lowered = query.lower()
        return lowered in SkillCardText.attribute(card, Keys.Fields.NAME).lower() or (
            lowered in SkillCardText.attribute(card, Keys.Fields.DESCRIPTION).lower()
        )

    @classmethod
    def _row(cls, card: object) -> dict[str, str]:
        """Project one card to the compact row the model reads."""
        name = SkillCardText.attribute(card, Keys.Fields.NAME)
        return {
            "name": name,
            "display_name": SkillCardText.attribute(card, Keys.Fields.DISPLAY_NAME)
            or name,
            "description": SkillCardText.attribute(card, Keys.Fields.DESCRIPTION),
        }


class ListSkillsInputParser:
    """Parser for untrusted model input to the Skill search."""

    @classmethod
    def parse(
        cls, raw_input: ListSkillsInput | Mapping[str, Any] | str | None
    ) -> ListSkillsInput:
        """Validate ``raw_input``; fall back to an unfiltered search on junk.

        A search is read-only and bounded, so a malformed argument is answered
        with the default listing rather than an error the model has to recover
        from.
        """

        if isinstance(raw_input, ListSkillsInput):
            return raw_input
        if raw_input is None:
            return ListSkillsInput()
        if isinstance(raw_input, str):
            return ListSkillsInput(query=raw_input)
        try:
            return ListSkillsInput.model_validate(raw_input)
        except ValidationError:
            return ListSkillsInput()


class LoadSkillInputParser:
    """Parser for untrusted model input to the Skill loader."""

    @classmethod
    def parse(
        cls, raw_input: LoadSkillInput | Mapping[str, Any] | str
    ) -> LoadSkillInput | dict[str, Any]:
        """Validate ``raw_input`` to a typed request; return an error dict on invalid skill_name."""
        if isinstance(raw_input, LoadSkillInput):
            return raw_input
        if isinstance(raw_input, str):
            raw_input = {"skill_name": raw_input}
        try:
            return LoadSkillInput.model_validate(raw_input)
        except ValidationError:
            return {
                "ok": False,
                "error": {
                    "code": "invalid_skill_name",
                    "safe_message": "A stable skill_name is required.",
                    "retryable": False,
                },
            }
