import type { ConversationId, RunId } from "@0x-copilot/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../providers/RouterProvider";
import type { ArtifactRoute, Router } from "../routing/router";

import { ItemLink } from "./ItemLink";
import {
  __resetItemRouteRegistryForTests,
  registerItemRoute,
} from "./registry";

afterEach(() => {
  __resetItemRouteRegistryForTests();
});

function makeRouter(): {
  router: Router<ArtifactRoute>;
  navigate: ReturnType<typeof vi.fn>;
} {
  const navigate = vi.fn();
  const router: Router<ArtifactRoute> = {
    current: () => ({ kind: "chat", conversationId: "x" }) as ArtifactRoute,
    navigate,
    subscribe: () => () => undefined,
  };
  return { router, navigate };
}

function renderWithRouter(
  ui: React.ReactElement,
  router: Router<ArtifactRoute>,
): void {
  render(<RouterProvider router={router}>{ui}</RouterProvider>);
}

describe("<ItemLink>", () => {
  it("renders the caller's label as an <a> and navigates to the registered route on click", () => {
    registerItemRoute("chat", (id) => ({
      kind: "chat",
      conversationId: id,
    }));
    const { router, navigate } = makeRouter();
    renderWithRouter(
      <ItemLink
        ref={{ kind: "chat", id: "conv_001" as ConversationId }}
        label="Acme renewal"
      />,
      router,
    );
    const link = screen.getByTestId("item-link");
    expect(link.tagName).toBe("A");
    expect(link).toHaveTextContent("Acme renewal");
    fireEvent.click(link);
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(navigate).toHaveBeenCalledWith({
      kind: "chat",
      conversationId: "conv_001",
    });
  });

  it("does NOT navigate on cmd-click (lets the browser open in a new tab)", () => {
    registerItemRoute("chat", (id) => ({ kind: "chat", conversationId: id }));
    const { router, navigate } = makeRouter();
    renderWithRouter(
      <ItemLink
        ref={{ kind: "chat", id: "conv_001" as ConversationId }}
        label="Acme renewal"
      />,
      router,
    );
    fireEvent.click(screen.getByTestId("item-link"), { metaKey: true });
    expect(navigate).not.toHaveBeenCalled();
  });

  // DoD 3 — a kind with NO registered route renders inert text (a <span>, not
  // an <a>), carrying the caller's label. "not navigable yet" is not "deleted".
  it("renders the label as a plain <span> with no onClick when no route is registered for the kind", () => {
    const { router, navigate } = makeRouter();
    renderWithRouter(
      <ItemLink
        ref={{ kind: "run", id: "run_x" as RunId }}
        label="Weekly treasury reconciliation"
      />,
      router,
    );
    const node = screen.getByTestId("item-link-static");
    expect(node.tagName).toBe("SPAN");
    expect(node).toHaveTextContent("Weekly treasury reconciliation");
    // No anchor was rendered.
    expect(screen.queryByTestId("item-link")).toBeNull();
    // Clicking the inert span does not navigate.
    fireEvent.click(node);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("renders inert text when the registered resolver returns null for the id", () => {
    registerItemRoute("chat", () => null);
    const { router } = makeRouter();
    renderWithRouter(
      <ItemLink
        ref={{ kind: "chat", id: "conv_gone" as ConversationId }}
        label="(deleted) Acme renewal"
      />,
      router,
    );
    const node = screen.getByTestId("item-link-static");
    expect(node.tagName).toBe("SPAN");
    expect(node).toHaveTextContent("(deleted) Acme renewal");
  });

  // README G11 — the link declares no colour and no font-size; it inherits its
  // slot's typography. The style objects carry neither property.
  it("declares no colour and no font-size on the anchor (accent-link policy)", () => {
    registerItemRoute("chat", (id) => ({ kind: "chat", conversationId: id }));
    const { router } = makeRouter();
    renderWithRouter(
      <ItemLink
        ref={{ kind: "chat", id: "conv_001" as ConversationId }}
        label="Acme renewal"
      />,
      router,
    );
    const link = screen.getByTestId("item-link");
    expect(link.style.color).toBe("");
    expect(link.style.fontSize).toBe("");
  });
});
