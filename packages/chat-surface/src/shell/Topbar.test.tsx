import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RouterProvider } from "../providers/RouterProvider";
import type { ArtifactRoute, Router } from "../routing/router";

import { Topbar } from "./Topbar";

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

describe("Topbar", () => {
  it("renders the breadcrumb destination label for the active route", () => {
    render(
      <RouterProvider
        router={staticRouter({ kind: "chat", conversationId: "" })}
      >
        <Topbar />
      </RouterProvider>,
    );
    const crumb = screen.getByTestId("topbar-breadcrumb");
    expect(crumb).toHaveTextContent("Chats");
  });

  it("renders the leaf identifier when one is present on the route", () => {
    render(
      <RouterProvider
        router={staticRouter({ kind: "chat", conversationId: "c-77" })}
      >
        <Topbar />
      </RouterProvider>,
    );
    const leaf = screen.getByTestId("topbar-breadcrumb-leaf");
    expect(leaf).toHaveTextContent("c-77");
  });

  it("renders an em-dash leaf when the route has no leaf identifier", () => {
    render(
      <RouterProvider
        router={staticRouter({ kind: "chat", conversationId: "" })}
      >
        <Topbar />
      </RouterProvider>,
    );
    const leaf = screen.getByTestId("topbar-breadcrumb-leaf");
    expect(leaf).toHaveTextContent("—");
  });

  it("renders the placeholder mode toggle", () => {
    render(
      <RouterProvider router={staticRouter(null)}>
        <Topbar />
      </RouterProvider>,
    );
    const toggle = screen.getByTestId("topbar-mode-toggle");
    expect(toggle).toBeInTheDocument();
    expect(toggle.tagName).toBe("BUTTON");
  });

  it("falls back to Home label when route is absent", () => {
    render(
      <RouterProvider router={staticRouter(null)}>
        <Topbar />
      </RouterProvider>,
    );
    expect(screen.getByTestId("topbar-breadcrumb")).toHaveTextContent("Home");
  });
});
