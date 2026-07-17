import type {
  ConversationId,
  RunId,
  SubagentId,
  ToolResultId,
} from "@0x-copilot/api-types";
import { describe, expect, it } from "vitest";

import { hasItemRefResolver, resolveItemRef } from "../../refs/registry";

// Importing the destination's index runs its `registerIfAbsent(...)`
// calls as a side-effect; the test asserts on the post-import state of
// the registry. The registry is a module-singleton, so this load is
// idempotent across the suite (the home registrations guard themselves
// with `hasItemRefResolver`).
import "./index";

describe("home/index.ts — ItemRef resolver registration", () => {
  it("registers resolvers for the four kinds Home owns (chat / run / subagent / tool_result)", () => {
    expect(hasItemRefResolver("chat")).toBe(true);
    expect(hasItemRefResolver("run")).toBe(true);
    expect(hasItemRefResolver("subagent")).toBe(true);
    expect(hasItemRefResolver("tool_result")).toBe(true);
  });

  it("resolves a chat ref to an ArtifactRoute kind=chat", async () => {
    const resolved = await resolveItemRef({
      kind: "chat",
      id: "conv_xyz" as ConversationId,
    });
    expect(resolved).not.toBeNull();
    expect(resolved!.route).not.toBeNull();
    expect(resolved!.route).toMatchObject({
      kind: "chat",
      conversationId: "conv_xyz",
    });
  });

  it("resolves a run ref to an ArtifactRoute kind=run", async () => {
    const resolved = await resolveItemRef({
      kind: "run",
      id: "run_xyz" as RunId,
    });
    expect(resolved).not.toBeNull();
    expect(resolved!.route).toMatchObject({
      kind: "run",
      runId: "run_xyz",
    });
  });

  it("resolves subagent/tool_result refs with route=null (richer resolver lands in chats destination)", async () => {
    const sub = await resolveItemRef({
      kind: "subagent",
      id: "sub_x" as SubagentId,
    });
    const tool = await resolveItemRef({
      kind: "tool_result",
      id: "tr_x" as ToolResultId,
    });
    expect(sub).not.toBeNull();
    expect(sub!.route).toBeNull();
    expect(tool).not.toBeNull();
    expect(tool!.route).toBeNull();
  });
});
