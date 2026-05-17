import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../providers/RouterProvider";
import type { ArtifactRoute, Router } from "../routing/router";

import { AppRail } from "./AppRail";
import { SHELL_DESTINATIONS } from "./destinations";

function makeRouter(initial: ArtifactRoute | null): Router<ArtifactRoute> & {
  __set(r: ArtifactRoute): void;
} {
  let current: ArtifactRoute | null = initial;
  const subscribers = new Set<(r: ArtifactRoute) => void>();
  const navigate = vi.fn((r: ArtifactRoute) => {
    current = r;
    for (const s of subscribers) s(r);
  });
  return {
    current(): ArtifactRoute {
      if (current === null) {
        throw new Error("no route");
      }
      return current;
    },
    navigate,
    subscribe(handler) {
      subscribers.add(handler);
      return () => subscribers.delete(handler);
    },
    __set(r: ArtifactRoute) {
      current = r;
      for (const s of subscribers) s(r);
    },
  };
}

describe("AppRail", () => {
  it("renders 11 destination buttons in order", () => {
    const router = makeRouter(null);
    render(
      <RouterProvider router={router}>
        <AppRail />
      </RouterProvider>,
    );
    const nav = screen.getByRole("navigation", { name: /atlas destinations/i });
    const buttons = within(nav).getAllByRole("button");
    expect(buttons).toHaveLength(11);
    const slugs = buttons.map((b) => b.getAttribute("data-destination"));
    expect(slugs).toEqual(SHELL_DESTINATIONS.map((d) => d.slug));
  });

  it("clicking the chats button calls router.navigate with a chat route", () => {
    const router = makeRouter(null);
    render(
      <RouterProvider router={router}>
        <AppRail />
      </RouterProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Chats" }));
    expect(router.navigate).toHaveBeenCalledWith({
      kind: "chat",
      conversationId: "",
    });
  });

  it("clicking a destination without a route shape is a navigate-noop", () => {
    const router = makeRouter(null);
    render(
      <RouterProvider router={router}>
        <AppRail />
      </RouterProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Home" }));
    fireEvent.click(screen.getByRole("button", { name: "Inbox" }));
    fireEvent.click(screen.getByRole("button", { name: "Memory" }));
    expect(router.navigate).not.toHaveBeenCalled();
  });

  it("marks the destination matching the current route as aria-current=page", () => {
    const router = makeRouter({ kind: "chat", conversationId: "c-1" });
    render(
      <RouterProvider router={router}>
        <AppRail />
      </RouterProvider>,
    );
    const chats = screen.getByRole("button", { name: "Chats" });
    expect(chats).toHaveAttribute("aria-current", "page");
    expect(chats).toHaveAttribute("data-state", "active");
    const home = screen.getByRole("button", { name: "Home" });
    expect(home).not.toHaveAttribute("aria-current");
  });

  it("falls back to home when the current route maps to no destination", () => {
    const router = makeRouter(null);
    render(
      <RouterProvider router={router}>
        <AppRail />
      </RouterProvider>,
    );
    expect(screen.getByRole("button", { name: "Home" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("updates the active highlight when the router publishes a new route", () => {
    const router = makeRouter({ kind: "chat", conversationId: "" });
    render(
      <RouterProvider router={router}>
        <AppRail />
      </RouterProvider>,
    );
    expect(screen.getByRole("button", { name: "Chats" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    act(() => {
      router.__set({ kind: "mcp", serverId: "srv-1" });
    });
    expect(screen.getByRole("button", { name: "Connectors" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("button", { name: "Chats" })).not.toHaveAttribute(
      "aria-current",
    );
  });
});
