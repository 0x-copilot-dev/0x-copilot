from __future__ import annotations

import re
from pathlib import Path


class TestAgentRuntimeImportBoundaries:
    # Module paths that no longer exist. Each entry is compiled as a regex
    # with a trailing word boundary so a legitimate module with a shared
    # prefix (e.g. ``agent_runtime.api.approval_coordinator``) does not
    # falsely match a retired path like ``agent_runtime.api.app``.
    LEGACY_MODULE_PATHS = (
        "agent_runtime.agent",
        "agent_runtime.mcp",
        "agent_runtime.tools",
        "agent_runtime.memory",
        "agent_runtime.skills",
        "agent_runtime.subagents",
        "agent_runtime.api.contracts",
        "agent_runtime.api.app",
        "agent_runtime.api.errors",
        "agent_runtime.api.streaming",
        "agent_runtime.api.in_memory",
        "agent_runtime.persistence.contracts",
        "agent_runtime.persistence.postgres",
    )

    def test_python_sources_use_canonical_import_paths(self) -> None:
        root = Path(__file__).resolve().parents[3]
        checked_roots = (root / "src", root / "tests")
        # Match ``from <path>`` or ``import <path>`` followed by anything
        # that is NOT an identifier continuation character (letter / digit
        # / underscore). The ``(?![\w.])`` negative lookahead also rejects
        # a continuation `.something`, so a deeper retired submodule is
        # still caught but a sibling (e.g. ``...api.approval_*``) is not.
        patterns = tuple(
            re.compile(rf"(?:from|import)\s+{re.escape(path)}(?![\w.])")
            for path in self.LEGACY_MODULE_PATHS
        )
        offenders: list[str] = []
        for checked_root in checked_roots:
            for path in checked_root.rglob("*.py"):
                if path == Path(__file__).resolve():
                    continue
                text = path.read_text()
                if any(pattern.search(text) for pattern in patterns):
                    offenders.append(str(path.relative_to(root)))

        assert offenders == []
