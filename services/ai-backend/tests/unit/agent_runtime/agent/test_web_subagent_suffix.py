"""B8 — the supervisor / subagent prompt suffix references the configured cap."""

from __future__ import annotations

from agent_runtime.execution.deep_agent_builder import (
    WEB_SUBAGENT_CHECKPOINT_SUFFIX,
    format_web_subagent_suffix,
)


class TestFormatWebSubagentSuffix:
    def test_default_includes_default_cap(self) -> None:
        # Backwards-compat: the default still says "5 invocations" so the
        # legacy module constant is byte-identical.
        suffix = format_web_subagent_suffix()
        assert "5 invocations" in suffix
        assert suffix == WEB_SUBAGENT_CHECKPOINT_SUFFIX

    def test_custom_cap_is_interpolated(self) -> None:
        for cap in (3, 6, 10):
            suffix = format_web_subagent_suffix(cap)
            assert f"{cap} invocations" in suffix
            assert f"{cap} calls of the same tool" in suffix

    def test_no_orphan_literal_five_when_cap_is_six(self) -> None:
        # The previous literal "5" was a bug — the configured cap was 6
        # (RUNTIME_TOOL_CALL_BUDGET default) but the prompt told the
        # model "5". With the dynamic format, asking for 6 puts 6
        # everywhere and never mentions a stray 5.
        suffix = format_web_subagent_suffix(6)
        # The string "5" can legitimately appear inside other words /
        # numbers later in the prompt; assert specifically that the
        # bound phrasing references 6 and not 5.
        assert "6 invocations" in suffix
        assert "5 invocations" not in suffix
