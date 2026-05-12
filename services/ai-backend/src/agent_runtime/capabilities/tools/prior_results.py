"""Model-facing tool for loading prior persisted tool observations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import inspect
from typing import Any

from pydantic import Field, ValidationError

from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract


class LoadPriorToolResultInput(RuntimeContract):
    """Input for retrieving one prior tool observation by stable id."""

    observation_id: str = Field(min_length=1, max_length=300)


@dataclass(frozen=True)
class LoadPriorToolResultTool:
    """Load a redacted tool result that was already persisted in this conversation."""

    loader: object
    runtime_context: AgentRuntimeContext
    name: str = "load_prior_tool_result"
    description: str = (
        "Load the full redacted result for a prior tool observation listed in the "
        "conversation context. Use this only when the prior observation summary is "
        "directly relevant but does not contain enough detail. This reads prior "
        "persisted observations; it does not refresh live data."
    )

    async def ainvoke(
        self, raw_input: LoadPriorToolResultInput | Mapping[str, Any]
    ) -> dict[str, Any]:
        """Validate input and delegate to the run-scoped loader to retrieve the observation."""
        parsed = self._parse(raw_input)
        if isinstance(parsed, dict):
            return parsed

        load = getattr(self.loader, "load_prior_tool_result", None)
        if not callable(load):
            return self._fail(
                "loader_unavailable",
                "Prior tool result loading is not available for this run.",
            )

        result = load(
            observation_id=parsed.observation_id,
            runtime_context=self.runtime_context,
        )
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, dict):
            return result
        return self._fail(
            "invalid_loader_result",
            "Prior tool result loading returned an invalid response.",
        )

    @classmethod
    def _parse(
        cls, raw_input: LoadPriorToolResultInput | Mapping[str, Any]
    ) -> LoadPriorToolResultInput | dict[str, Any]:
        """Return a validated input model or a failure dict on missing/invalid observation_id."""
        if isinstance(raw_input, LoadPriorToolResultInput):
            return raw_input
        try:
            return LoadPriorToolResultInput.model_validate(raw_input)
        except ValidationError:
            return cls._fail(
                "invalid_observation_id",
                "A valid prior tool observation id is required.",
            )

    @staticmethod
    def _fail(error_code: str, safe_message: str) -> dict[str, Any]:
        """Return a normalized failure dict safe to return as the tool's result."""
        return {
            "ok": False,
            "error_code": error_code,
            "safe_message": safe_message,
        }

    async def __call__(
        self, raw_input: LoadPriorToolResultInput | Mapping[str, Any]
    ) -> dict[str, Any]:
        """Delegate to ``ainvoke``."""
        return await self.ainvoke(raw_input)
