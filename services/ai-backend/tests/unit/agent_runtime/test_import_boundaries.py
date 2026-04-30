from __future__ import annotations

from pathlib import Path


class TestAgentRuntimeImportBoundaries:
    LEGACY_IMPORT_FRAGMENTS = (
        "from agent_runtime.agent",
        "import agent_runtime.agent",
        "from agent_runtime.mcp",
        "import agent_runtime.mcp",
        "from agent_runtime.tools",
        "import agent_runtime.tools",
        "from agent_runtime.memory",
        "import agent_runtime.memory",
        "from agent_runtime.skills",
        "import agent_runtime.skills",
        "from agent_runtime.subagents",
        "import agent_runtime.subagents",
        "from agent_runtime.api.contracts",
        "import agent_runtime.api.contracts",
        "from agent_runtime.api.app",
        "import agent_runtime.api.app",
        "from agent_runtime.api.errors",
        "import agent_runtime.api.errors",
        "from agent_runtime.api.streaming",
        "import agent_runtime.api.streaming",
        "from agent_runtime.api.in_memory",
        "import agent_runtime.api.in_memory",
        "from agent_runtime.persistence.contracts",
        "import agent_runtime.persistence.contracts",
        "from agent_runtime.persistence.postgres",
        "import agent_runtime.persistence.postgres",
    )

    def test_python_sources_use_canonical_import_paths(self) -> None:
        root = Path(__file__).resolve().parents[3]
        checked_roots = (root / "src", root / "tests")
        offenders: list[str] = []
        for checked_root in checked_roots:
            for path in checked_root.rglob("*.py"):
                if path == Path(__file__).resolve():
                    continue
                text = path.read_text()
                if any(fragment in text for fragment in self.LEGACY_IMPORT_FRAGMENTS):
                    offenders.append(str(path.relative_to(root)))

        assert offenders == []
