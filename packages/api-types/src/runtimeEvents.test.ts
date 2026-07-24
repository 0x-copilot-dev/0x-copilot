// @vitest-environment node
import { describe, expect, it } from "vitest";

import type {
  SubagentActivityPayload,
  ToolCallPayload,
  ToolResultPayload,
} from "./index";

describe("runtime event presentation fields", () => {
  it("keeps source-backed MCP provenance and authority mode optional", () => {
    const payload: ToolCallPayload = {
      tool_name: "call_mcp_tool",
      call_id: "call_1",
      provenance: { source: "mcp", server_name: "notion" },
      access_mode: "read_act",
      subagent_task_ids: ["task_1"],
    };

    expect(payload.provenance?.server_name).toBe("notion");
    expect(payload.access_mode).toBe("read_act");
    expect(payload.subagent_task_ids).toEqual(["task_1"]);
  });

  it("accepts duration only when the runtime measured one", () => {
    const payload: ToolResultPayload = {
      tool_name: "search",
      call_id: "call_2",
      duration_ms: 42,
    };

    expect(payload.duration_ms).toBe(42);
  });

  it("represents subagent presentation facts without requiring a lead name", () => {
    const payload: SubagentActivityPayload = {
      task_id: "task_1",
      parent_agent_role: "supervisor",
      model_display_label: "GPT-5.4 Mini",
      current_activity: "Researching the market landscape.",
    };

    expect(payload.parent_agent_name).toBeUndefined();
    expect(payload.current_activity).toContain("Researching");
  });

  it("rejects unsupported access modes at compile time", () => {
    const payload: ToolCallPayload = {
      tool_name: "search",
      call_id: "call_3",
      // @ts-expect-error only the frozen connector authority modes are valid
      access_mode: "write",
    };
    expect(payload.call_id).toBe("call_3");
  });
});
