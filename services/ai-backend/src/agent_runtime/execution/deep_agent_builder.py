"""Concrete Deep Agents construction for the runtime factory."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from deepagents import create_deep_agent


@runtime_checkable
class DeepAgentsBackend(Protocol):
    """Backend protocol accepted by Deep Agents filesystem integration."""

    memory_paths: Sequence[str]

    def download_files(self, paths: list[str]) -> dict[str, str]:
        """Download files for synchronous Deep Agents calls."""

    def upload_files(self, files: dict[str, str]) -> None:
        """Upload files for synchronous Deep Agents calls."""

    async def adownload_files(self, paths: list[str]) -> dict[str, str]:
        """Download files for asynchronous Deep Agents calls."""

    async def aupload_files(self, files: dict[str, str]) -> None:
        """Upload files for asynchronous Deep Agents calls."""


@dataclass(frozen=True)
class DeepAgentBuildRequest:
    """Resolved, authorized inputs for a concrete Deep Agents instance."""

    tools: tuple[object, ...]
    model_name: str
    system_prompt: str
    subagents: tuple[object, ...] = ()
    memory_backend: DeepAgentsBackend | None = None
    memory_paths: tuple[str, ...] = ()
    skill_directories: tuple[str, ...] = ()


def build_deep_agent(request: DeepAgentBuildRequest) -> object:
    """Build a Deep Agents graph with an explicit, version-pinned API call."""

    return create_deep_agent(
        model=request.model_name,
        tools=list(request.tools),
        system_prompt=request.system_prompt,
        subagents=list(request.subagents) or None,
        skills=list(request.skill_directories) or None,
        memory=list(request.memory_paths) or None,
        backend=request.memory_backend,
    )
