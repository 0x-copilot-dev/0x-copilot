"""The model-guidance blocks, and the one rule that keeps them honest.

Guidance naming a tool the runtime does not expose is worse than no guidance:
it teaches the model to plan around a capability it cannot reach, and the
failure surfaces as a confusing refusal rather than a missing feature. The
upstream text this was ported from referenced `terminal`, `execute_code` and
`search_files`, none of which exist here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent_runtime.prompts.guidance import GuidanceLibrary
from agent_runtime.prompts.runtime import DEFAULT_INSTRUCTIONS

_CATALOG = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "agent_runtime"
    / "capabilities"
    / "operations"
    / "builtin_operation_catalog.json"
)

# Backtick-quoted lowercase snake_case tokens are how a block names a tool.
_BACKTICKED = re.compile(r"`([a-z][a-z0-9_]{2,})`")

# Backticked tokens that are deliberately NOT tool names. Kept explicit so a new
# non-tool term has to be declared rather than silently widening the check.
_NOT_TOOLS = frozenset(
    {
        "package_json",
        "requirements_txt",
        "cargo_toml",
        "tool_persistence",
        "mandatory_tool_use",
        "act_dont_ask",
        "prerequisite_checks",
        "missing_context",
    }
)


def builtin_tool_names() -> frozenset[str]:
    """Every tool name the MODEL can actually see.

    Two sources, because there are two vocabularies. The operation catalog
    carries our OP names (`read`, `write`, `edit`) — the policy layer's words,
    not the model's. Deep Agents owns the filesystem tools under its own names
    (`read_file`, `write_file`, …), and `_FilesystemApproval.TOOL_OPERATIONS`
    is where production already enumerates them, so this reads that rather than
    keeping a second copy that could drift.
    """

    catalog = json.loads(_CATALOG.read_text(encoding="utf-8"))
    entries = catalog if isinstance(catalog, list) else catalog.get("operations", [])
    from runtime_worker.stream_events import _FilesystemApproval

    return frozenset(
        str(entry["tool_name"]) for entry in entries if entry.get("tool_name")
    ) | frozenset(_FilesystemApproval.TOOL_OPERATIONS)


class TestGuidanceNamesOnlyRealTools:
    @pytest.mark.parametrize("name", GuidanceLibrary.BLOCKS)
    def test_every_named_tool_exists(self, name: str) -> None:
        tools = builtin_tool_names()
        referenced = {
            token
            for token in _BACKTICKED.findall(GuidanceLibrary.block(name))
            if token not in _NOT_TOOLS
        }
        unknown = referenced - tools
        assert not unknown, (
            f"guidance block '{name}' names tools this runtime does not expose: "
            f"{sorted(unknown)}. Either the runtime should expose them, or the "
            "block must be rewritten against the real tool surface."
        )

    def test_the_upstream_tool_names_did_not_survive_the_port(self) -> None:
        # Regression pin on the specific mistake: copying upstream verbatim.
        text = GuidanceLibrary.text()
        for absent in ("terminal", "execute_code", "search_files", "sha256sum"):
            assert f"`{absent}`" not in text, absent


class TestLibrary:
    def test_every_declared_block_has_a_file(self) -> None:
        for name in GuidanceLibrary.BLOCKS:
            assert GuidanceLibrary.block(name).strip()

    def test_no_orphan_files_left_out_of_the_render_order(self) -> None:
        # A block on disk but absent from BLOCKS is dead prose nobody sees.
        on_disk = {
            path.stem
            for path in GuidanceLibrary.DIRECTORY.glob("*.md")
            if path.stem.lower() != "readme"
        }
        assert on_disk == set(GuidanceLibrary.BLOCKS)

    def test_a_missing_block_fails_loudly(self) -> None:
        # Silent degradation would change every model's behaviour with nothing
        # in the logs naming the cause.
        with pytest.raises(RuntimeError, match="missing"):
            GuidanceLibrary.block("no-such-block")

    def test_blocks_render_in_declared_order(self) -> None:
        text = GuidanceLibrary.text()
        positions = [
            text.index(GuidanceLibrary.block(n)) for n in GuidanceLibrary.BLOCKS
        ]
        assert positions == sorted(positions)


class TestWiring:
    def test_the_guidance_actually_reaches_the_default_instructions(self) -> None:
        # A landed-but-unwired block is prose nobody reads. Assert on content,
        # not on a flag: this is the check that would have caught shipping the
        # files without the import.
        assert GuidanceLibrary.text() in DEFAULT_INSTRUCTIONS

    def test_guidance_is_appended_after_the_runtime_identity(self) -> None:
        assert DEFAULT_INSTRUCTIONS.index("You are the 0xCopilot agent runtime") < (
            DEFAULT_INSTRUCTIONS.index(GuidanceLibrary.text())
        )

    def test_it_does_not_ask_the_model_to_narrate(self) -> None:
        # The whole point of the reasoning work: thinking comes from the
        # provider's structured channel, NOT from prompting the model to
        # narrate between tool calls. Upstream agrees — their only wording on
        # this is "Focus on actions and results over narration."
        assert "over narration" in GuidanceLibrary.text()
