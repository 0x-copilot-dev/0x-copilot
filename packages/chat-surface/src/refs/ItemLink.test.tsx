import type {
  ConversationId,
  RunId,
  ToolResultId,
} from "@0x-copilot/api-types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../providers/RouterProvider";
import type { ArtifactRoute, Router } from "../routing/router";

import { ItemLink } from "./ItemLink";
import {
  __resetItemRefRegistryForTests,
  registerItemRefResolver,
} from "./registry";

afterEach(() => {
  __resetItemRefRegistryForTests();
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
  it("renders a skeleton while the resolver is in flight", async () => {
    let resolve: (v: ReturnType<typeof labelOf>) => void = () => undefined;
    registerItemRefResolver("chat", () => new Promise((r) => (resolve = r)));
    const { router } = makeRouter();
    renderWithRouter(
      <ItemLink ref={{ kind: "chat", id: "conv_001" as ConversationId }} />,
      router,
    );
    expect(screen.getByTestId("item-link-skeleton")).toBeInTheDocument();
    resolve(
      labelOf("Acme renewal", { kind: "chat", conversationId: "conv_001" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("item-link")).toBeInTheDocument(),
    );
  });

  it("renders an <a> and calls router.navigate on click", async () => {
    registerItemRefResolver("chat", async () =>
      labelOf("Acme renewal", { kind: "chat", conversationId: "conv_001" }),
    );
    const { router, navigate } = makeRouter();
    renderWithRouter(
      <ItemLink ref={{ kind: "chat", id: "conv_001" as ConversationId }} />,
      router,
    );
    const link = await screen.findByTestId("item-link");
    expect(link.tagName).toBe("A");
    fireEvent.click(link);
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(navigate).toHaveBeenCalledWith({
      kind: "chat",
      conversationId: "conv_001",
    });
  });

  it("does NOT call router.navigate on cmd-click (lets browser open in new tab)", async () => {
    registerItemRefResolver("chat", async () =>
      labelOf("Acme renewal", { kind: "chat", conversationId: "conv_001" }),
    );
    const { router, navigate } = makeRouter();
    renderWithRouter(
      <ItemLink ref={{ kind: "chat", id: "conv_001" as ConversationId }} />,
      router,
    );
    const link = await screen.findByTestId("item-link");
    fireEvent.click(link, { metaKey: true });
    expect(navigate).not.toHaveBeenCalled();
  });

  it("renders the deleted chip when the resolver returns null", async () => {
    registerItemRefResolver("chat", async () => null);
    const { router } = makeRouter();
    renderWithRouter(
      <ItemLink ref={{ kind: "chat", id: "conv_001" as ConversationId }} />,
      router,
    );
    const chip = await screen.findByTestId("item-link-deleted");
    expect(chip).toHaveTextContent("deleted chat");
  });

  it("renders the deleted chip when the resolver returns route: null", async () => {
    registerItemRefResolver("chat", async () => ({
      label: "Old chat",
      icon: null,
      route: null,
    }));
    const { router } = makeRouter();
    renderWithRouter(
      <ItemLink ref={{ kind: "chat", id: "conv_001" as ConversationId }} />,
      router,
    );
    const chip = await screen.findByTestId("item-link-deleted");
    expect(chip).toBeInTheDocument();
  });

  it("renders a deleted chip when no resolver is registered for the kind", async () => {
    const { router } = makeRouter();
    renderWithRouter(
      <ItemLink ref={{ kind: "run", id: "run_x" as RunId }} />,
      router,
    );
    const chip = await screen.findByTestId("item-link-deleted");
    expect(chip).toHaveTextContent("deleted run");
  });

  it("humanises the kind (snake → space) in the deleted chip", async () => {
    registerItemRefResolver("tool_result", async () => null);
    const { router } = makeRouter();
    renderWithRouter(
      <ItemLink
        ref={{
          kind: "tool_result",
          id: "run:step" as ToolResultId,
        }}
      />,
      router,
    );
    const chip = await screen.findByTestId("item-link-deleted");
    expect(chip).toHaveTextContent("deleted tool result");
  });
});

function labelOf(
  label: string,
  route: ArtifactRoute,
): { label: string; icon: null; route: ArtifactRoute } {
  return { label, icon: null, route };
}
