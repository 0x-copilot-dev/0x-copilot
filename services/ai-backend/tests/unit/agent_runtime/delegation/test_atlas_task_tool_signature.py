"""Pin the atlas task-tool mirror to the upstream builder's signature.

The deepagents 1.x upgrade added ``private_state_keys`` / ``state_schema``
kwargs to ``_build_task_tool``; the monkey-patched Atlas mirror silently
broke at runtime (TypeError inside SubAgentMiddleware) while every unit
fake kept passing. This suite fails at test time instead the next time
upstream's builder signature drifts.
"""

import inspect
import json
import subprocess
import sys
from collections.abc import Callable
from typing import Any

from deepagents.middleware import subagents as upstream

from agent_runtime.delegation.subagents.atlas_task_tool import (
    build_atlas_task_tool,
    install_atlas_task_tool,
)


class TestAtlasTaskToolSignature:
    def test_private_upstream_seams_and_atlas_mirror_are_pinned(self) -> None:
        """Read pristine private signatures outside the process-global patch.

        Pytest imports the runtime factory during collection, and the factory
        replaces ``deepagents.middleware.subagents._build_task_tool`` at module
        load. Comparing the in-process symbol to our mirror can therefore
        compare the mirror to itself and miss upstream drift. A clean isolated
        interpreter keeps this unavoidable private contract honest.
        """

        expected_build_task_tool = (
            ("subagents", "POSITIONAL_OR_KEYWORD", "<required>"),
            ("task_description", "POSITIONAL_OR_KEYWORD", "None"),
            ("private_state_keys", "KEYWORD_ONLY", "frozenset()"),
            ("state_schema", "KEYWORD_ONLY", "None"),
        )
        expected_create_sub_agent = (
            ("spec", "POSITIONAL_OR_KEYWORD", "<required>"),
            ("state_schema", "KEYWORD_ONLY", "None"),
            ("response_format", "KEYWORD_ONLY", "None"),
        )
        pristine = _pristine_upstream_contracts()

        assert pristine["_build_task_tool"] == expected_build_task_tool, (
            "Private deepagents._build_task_tool drifted. Re-sync the isolated "
            "Atlas mirror in atlas_task_tool.py before upgrading deepagents. "
            f"expected={expected_build_task_tool!r}, "
            f"actual={pristine['_build_task_tool']!r}"
        )
        assert pristine["create_sub_agent"] == expected_create_sub_agent, (
            "Private deepagents.create_sub_agent drifted. The Atlas mirror "
            "calls this compiler directly and must be reviewed before upgrade. "
            f"expected={expected_create_sub_agent!r}, "
            f"actual={pristine['create_sub_agent']!r}"
        )
        assert _parameter_contract(build_atlas_task_tool) == expected_build_task_tool, (
            "build_atlas_task_tool no longer mirrors the pinned pristine "
            "_build_task_tool call shape."
        )

    def test_middleware_constructs_with_patched_builder(self) -> None:
        """SubAgentMiddleware must accept the mirror exactly as upstream's.

        This is the call path that broke: middleware __init__ invokes
        ``_build_task_tool(subagents, task_description, private_state_keys=...,
        state_schema=...)``.
        """
        install_atlas_task_tool()
        tool = upstream._build_task_tool(
            [
                {
                    "name": "researcher",
                    "description": "does research",
                    "runnable": _NoopRunnable(),
                }
            ],
            None,
            private_state_keys=frozenset({"provider_keys"}),
            state_schema=None,
        )
        assert tool.name == "task"


class _NoopRunnable:
    """Minimal runnable-shaped object for compile-spec configuration."""

    def with_config(self, *_args: object, **_kwargs: object) -> "_NoopRunnable":
        return self

    def invoke(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        return {"messages": []}

    async def ainvoke(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        return {"messages": []}


def _parameter_contract(
    callable_: Callable[..., Any],
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (
            parameter.name,
            parameter.kind.name,
            (
                "<required>"
                if parameter.default is inspect.Parameter.empty
                else repr(parameter.default)
            ),
        )
        for parameter in inspect.signature(callable_).parameters.values()
    )


def _pristine_upstream_contracts() -> dict[str, tuple[tuple[str, str, str], ...]]:
    script = """
import inspect
import json
from deepagents.middleware import subagents

def contract(callable_):
    return [
        [
            parameter.name,
            parameter.kind.name,
            (
                "<required>"
                if parameter.default is inspect.Parameter.empty
                else repr(parameter.default)
            ),
        ]
        for parameter in inspect.signature(callable_).parameters.values()
    ]

print(json.dumps({
    "_build_task_tool": contract(subagents._build_task_tool),
    "create_sub_agent": contract(subagents.create_sub_agent),
}))
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    payload = json.loads(completed.stdout)
    return {
        name: tuple(tuple(item) for item in parameters)
        for name, parameters in payload.items()
    }
