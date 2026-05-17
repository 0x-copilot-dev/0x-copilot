import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RouterProvider } from "../providers/RouterProvider";
import type { ArtifactRoute, Router } from "../routing/router";

import { ContextPanel } from "./ContextPanel";

function staticRouter(route: ArtifactRoute | null): Router<ArtifactRoute> {
  return {
    current(): ArtifactRoute {
      if (route === null) throw new Error("no route");
      return route;
    },
    navigate(): void {
      /* unused */
    },
    subscribe(): () => void {
      return () => {
        /* unused */
      };
    },
  };
}

describe("ContextPanel", () => {
  it("renders the destination header for the active route", () => {
    render(
      <RouterProvider
        router={staticRouter({ kind: "chat", conversationId: "" })}
      >
        <ContextPanel />
      </RouterProvider>,
    );
    expect(screen.getByTestId("context-panel-header")).toHaveTextContent(
      "Chats",
    );
  });

  it("renders three placeholder filter rows", () => {
    render(
      <RouterProvider router={staticRouter(null)}>
        <ContextPanel />
      </RouterProvider>,
    );
    const rows = screen.getByTestId("context-panel-rows");
    const items = within(rows).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("Filter row 1");
    expect(items[1]).toHaveTextContent("Filter row 2");
    expect(items[2]).toHaveTextContent("Filter row 3");
  });

  it("falls back to Home when route is absent", () => {
    render(
      <RouterProvider router={staticRouter(null)}>
        <ContextPanel />
      </RouterProvider>,
    );
    expect(screen.getByTestId("context-panel-header")).toHaveTextContent(
      "Home",
    );
  });
});
