"""Pin the atlas task-tool mirror to the upstream builder's signature.

The deepagents 1.x upgrade added ``private_state_keys`` / ``state_schema``
kwargs to ``_build_task_tool``; the monkey-patched Atlas mirror silently
broke at runtime (TypeError inside SubAgentMiddleware) while every unit
fake kept passing. This suite fails at test time instead the next time
upstream's builder signature drifts.
"""

import inspect

from deepagents.middleware import subagents as upstream

from agent_runtime.delegation.subagents.atlas_task_tool import (
    build_atlas_task_tool,
    install_atlas_task_tool,
)


class TestAtlasTaskToolSignature:
    def test_accepts_every_upstream_builder_parameter(self) -> None:
        ours = inspect.signature(build_atlas_task_tool).parameters
        theirs = inspect.signature(upstream._build_task_tool).parameters
        missing = [
            name
            for name, param in theirs.items()
            if name not in ours
            and param.kind
            in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            )
        ]
        assert not missing, (
            "build_atlas_task_tool is missing upstream _build_task_tool "
            f"parameters {missing}; the mirror must be re-synced with the "
            "installed deepagents version (see atlas_task_tool.py docstring)."
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
