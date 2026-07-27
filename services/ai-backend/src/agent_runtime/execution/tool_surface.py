"""Reviewed graph-level tool-surface contracts."""

from __future__ import annotations


DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES = frozenset(
    {
        "edit_file",
        "execute",
        "write_file",
    }
)


__all__ = ("DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES",)
