import type { SectionResult, SkillId } from "@enterprise-search/api-types";
import { render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { RouterProvider } from "../../../providers/RouterProvider";
import {
  __resetItemRefRegistryForTests,
  registerItemRefResolver,
} from "../../../refs/registry";
import type { ArtifactRoute, Router } from "../../../routing/router";

import type { HomeFavoriteTool } from "../_home-stub";
import { FavoriteTools } from "./FavoriteTools";

afterEach(() => {
  __resetItemRefRegistryForTests();
});

const noopRouter: Router<ArtifactRoute> = {
  current: () => ({ kind: "chat", conversationId: "x" }) as ArtifactRoute,
  navigate: () => undefined,
  subscribe: () => () => undefined,
};

const NOW_MS = Date.parse("2026-05-18T12:00:00Z");

function wrap(node: ReactElement): ReactElement {
  return <RouterProvider router={noopRouter}>{node}</RouterProvider>;
}

function makeTool(overrides: Partial<HomeFavoriteTool> = {}): HomeFavoriteTool {
  return {
    skill_id: "skl_search" as SkillId,
    name: "Web search",
    tool_kind: "builtin",
    use_count: 12,
    last_used_at: "2026-05-18T11:50:00Z",
    ...overrides,
  };
}

describe("<FavoriteTools>", () => {
  it("renders a card per tool when status='ok'", () => {
    registerItemRefResolver("skill", async () => null);
    const tools: SectionResult<HomeFavoriteTool[]> = {
      status: "ok",
      data: [
        makeTool({ skill_id: "skl_search" as SkillId, use_count: 12 }),
        makeTool({
          skill_id: "skl_calc" as SkillId,
          name: "Calculator",
          use_count: 1,
          subtitle: "math helper",
        }),
      ],
    };

    render(wrap(<FavoriteTools tools={tools} now={NOW_MS} />));

    const section = screen.getByTestId("home-favorite-tools");
    expect(section).toHaveAttribute("data-section-status", "ok");
    expect(screen.getAllByTestId("home-favorite-tool-card")).toHaveLength(2);

    // Pluralization branch coverage.
    const uses = screen.getAllByTestId("home-favorite-tool-use-count");
    expect(uses[0]).toHaveTextContent("12 uses");
    expect(uses[1]).toHaveTextContent("1 use");

    // last_used_at appears as a relative-time chip.
    expect(
      screen.getAllByTestId("home-favorite-tool-last-used")[0],
    ).toBeInTheDocument();
  });

  it("renders empty state when status='ok' and data is empty", () => {
    const tools: SectionResult<HomeFavoriteTool[]> = {
      status: "ok",
      data: [],
    };
    render(wrap(<FavoriteTools tools={tools} />));
    expect(screen.getByTestId("home-favorite-tools-empty")).toHaveAttribute(
      "data-section-status",
      "ok",
    );
    expect(screen.getByTestId("empty-state-title")).toHaveTextContent(
      "Use a tool to see it here.",
    );
  });

  it("renders the error branch", () => {
    const tools: SectionResult<HomeFavoriteTool[]> = {
      status: "error",
      error: "user_skills query failed",
    };
    render(wrap(<FavoriteTools tools={tools} />));
    expect(screen.getByTestId("home-favorite-tools-error")).toHaveAttribute(
      "role",
      "alert",
    );
    expect(screen.getByTestId("empty-state-body")).toHaveTextContent(
      "user_skills query failed",
    );
  });

  it("renders the unavailable branch", () => {
    const tools: SectionResult<HomeFavoriteTool[]> = {
      status: "unavailable",
      error: "permission_denied",
    };
    render(wrap(<FavoriteTools tools={tools} />));
    expect(
      screen.getByTestId("home-favorite-tools-unavailable"),
    ).toHaveAttribute("data-section-status", "unavailable");
  });

  it("omits the last-used chip when last_used_at is undefined", () => {
    registerItemRefResolver("skill", async () => null);
    const tools: SectionResult<HomeFavoriteTool[]> = {
      status: "ok",
      data: [makeTool({ last_used_at: undefined, use_count: 0 })],
    };
    render(wrap(<FavoriteTools tools={tools} />));
    expect(
      screen.queryByTestId("home-favorite-tool-last-used"),
    ).not.toBeInTheDocument();
  });
});
