"""D2 — where the MCP write axis is enforced, now that the umbrella is gone.

This file used to prove the tool-use policy end to end against ``call_mcp_tool``:
the write axis either added that one name to ``interrupt_on`` or replaced the
tool with a blocked-result wrapper. Both mechanisms keyed on the NAME, and the
name is deleted — per-tool registration replaced the umbrella.

The property did not leave the product, it moved to a stronger owner, and that
is worth stating precisely because "the enforcer's map is empty" reads like a
regression:

* the **PDP** (``capabilities/policy/service.py``) decides the write axis per
  call, and a workspace ``BLOCK`` there is *terminal* — it survives both the
  per-connector override and BYPASS. The gateway's wrapper had no such
  guarantee. Covered by ``capabilities/policy/test_policy_service.py``
  (``test_write_block_survives_bypass``,
  ``test_allow_always_never_overrides_write_block``).
* the **POLICY stage** of the per-tool pipeline applies that decision, parking a
  gated write on the approval channel. Covered end to end by
  ``execution/test_mcp_per_tool_flip.py``: a trusted read auto-runs, a write
  parks and then executes exactly once after approval, and a declined write
  never dispatches.

What remains here is the tripwire the deletion created. ``ToolUsePolicyEnforcer``
still lists ``call_mcp_tool`` in its gated-tool map, and that entry is now
INERT — the map keys on a model-tool name, and no tool by that name is ever
composed. Inert is fine; what is not fine is the entry becoming live again
under a per-tool name, so that is what these assert.
"""

from __future__ import annotations

from agent_runtime.capabilities.tools.tool_use_enforcement import (
    ToolUsePolicyEnforcer,
)


class TestTheEnforcerNoLongerClaimsTheMcpSurface:
    """The umbrella is gone; the enforcer must not pretend to gate MCP."""

    def test_the_gated_names_never_intersect_the_composed_mcp_surface(self) -> None:
        """A name-keyed MCP gate here would double-prompt every write.

        The POLICY stage parks a gated write *after* the PDP decides. Deep
        Agents' ``interrupt_on`` middleware interrupts BEFORE the tool runs, for
        every call of a listed name, knowing nothing of the decision — so
        listing an MCP tool here raises a second approval for one write. That is
        the double-prompt ``McpPerToolInterrupts`` exists to avoid.
        """

        gated = set(ToolUsePolicyEnforcer._GATED_TOOL_SIDE_EFFECTS)
        # The composed MCP surface is now the connectors' own tool names. The
        # retired umbrella is the ONE name allowed to remain here, because
        # nothing composes it any more — the entry is dead weight, not a gate.
        live = gated - {"call_mcp_tool"}

        assert live == set(), (
            f"{sorted(live)} makes the enforcer gate a composed tool by name; if "
            "any of those is an MCP tool the write is gated TWICE — here before "
            "the call, and again by the POLICY stage after the PDP decides"
        )

    def test_the_retired_umbrella_is_the_only_entry_left(self) -> None:
        """Pins the map's size so a new entry is a deliberate review, not a drift.

        The routing machinery it drives (block -> refusal wrapper, require ->
        approval interrupt) is still exercised by
        ``capabilities/tools/test_tool_use_enforcement.py`` against this same
        name, which is why the entry stays rather than being deleted outright.
        """

        assert set(ToolUsePolicyEnforcer._GATED_TOOL_SIDE_EFFECTS) == {"call_mcp_tool"}
