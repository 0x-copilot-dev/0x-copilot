import type { ItemKind } from "@0x-copilot/api-types";
import { describe, expect, it } from "vitest";

import type { AppRoute } from "./routes";
import { WEB_NAVIGABLE_KINDS, webItemRoute } from "./itemRoutes";

describe("web itemRoutes table (PRD-04 Seam B)", () => {
  it("returns a non-null AppRoute for every kind the web surfaces navigate to", () => {
    for (const kind of WEB_NAVIGABLE_KINDS) {
      const route = webItemRoute(kind, "id_1");
      expect(route).not.toBeNull();
      // Type-level: the return is `AppRoute | null`, so a non-null value IS an
      // AppRoute by construction — `/settings#undefined` is unreachable.
      const asAppRoute: AppRoute = route!;
      expect(asAppRoute).toBeDefined();
    }
  });

  it("carries the id through as the destination subPath where applicable", () => {
    expect(webItemRoute("chat", "conv_9")).toEqual({
      screen: "chat",
      destination: "run",
      subPath: "conv_9",
    });
    expect(webItemRoute("project", "proj_9")).toEqual({
      screen: "chat",
      destination: "projects",
      subPath: "proj_9",
    });
  });

  it("returns null for kinds with no mounted web destination (inert text, not a broken route)", () => {
    const inert: ReadonlyArray<ItemKind> = [
      "todo",
      "library_file",
      "memory",
      "routine",
      "person",
      "approval",
    ];
    for (const kind of inert) {
      expect(webItemRoute(kind, "x")).toBeNull();
    }
  });

  it("every non-null route satisfies AppRoute for EVERY ItemKind (exhaustive)", () => {
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
      const route = webItemRoute(kind, "id");
      if (route !== null) {
        // Assignable to AppRoute — the compiler is the real guard here.
        const asAppRoute: AppRoute = route;
        expect(asAppRoute.screen).toBe("chat");
      }
    }
  });
});
