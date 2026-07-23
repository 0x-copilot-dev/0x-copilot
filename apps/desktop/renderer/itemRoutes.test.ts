// @vitest-environment node
import type { ItemKind } from "@0x-copilot/api-types";
import type { ArtifactRoute } from "@0x-copilot/chat-surface";
import { describe, expect, it } from "vitest";

import { DESKTOP_NAVIGABLE_KINDS, desktopItemRoute } from "./itemRoutes";

describe("desktop itemRoutes table (PRD-04 Seam B)", () => {
  it("returns a non-null ArtifactRoute for every kind the desktop surfaces navigate to", () => {
    for (const kind of DESKTOP_NAVIGABLE_KINDS) {
      const route = desktopItemRoute(kind, "id_1");
      expect(route).not.toBeNull();
      // Type-level: the return is `ArtifactRoute | null`, so a non-null value
      // IS an ArtifactRoute by construction.
      const asArtifactRoute: ArtifactRoute = route!;
      expect(asArtifactRoute).toBeDefined();
    }
  });

  it("binds a chat to the conversation route (cockpit binds by conversation id)", () => {
    expect(desktopItemRoute("chat", "conv_9")).toEqual({
      kind: "conversation",
      conversationId: "conv_9",
    });
  });

  it("routes a run by run id and a skill by skill id", () => {
    expect(desktopItemRoute("run", "run_9")).toEqual({
      kind: "run",
      runId: "run_9",
    });
    expect(desktopItemRoute("skill", "skill_9")).toEqual({
      kind: "skill",
      skillId: "skill_9",
    });
  });

  it("every non-null route satisfies ArtifactRoute for EVERY ItemKind (exhaustive)", () => {
    const allKinds: ReadonlyArray<ItemKind> = [
      "chat",
      "run",
      "subagent",
      "tool_result",
      "todo",
      "inbox_item",
      "project",
      "library_file",
      "library_page",
      "library_dataset",
      "agent",
      "tool",
      "skill",
      "connector",
      "person",
      "memory",
      "routine",
      "approval",
      "meeting_external",
    ];
    for (const kind of allKinds) {
      const route = desktopItemRoute(kind, "id");
      if (route !== null) {
        // Assignable to ArtifactRoute — the compiler is the real guard.
        const asArtifactRoute: ArtifactRoute = route;
        expect(asArtifactRoute.kind).toBeDefined();
      }
    }
  });
});
