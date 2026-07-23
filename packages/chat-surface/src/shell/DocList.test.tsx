import type { ConversationId, ItemRefSnapshot } from "@0x-copilot/api-types";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { RouterProvider } from "../providers/RouterProvider";
import { __resetItemRouteRegistryForTests } from "../refs/registry";
import type { ArtifactRoute, Router } from "../routing/router";

import { DocList } from "./DocList";

afterEach(() => {
  __resetItemRouteRegistryForTests();
});

const noopRouter: Router<ArtifactRoute> = {
  current: () => ({ kind: "chat", conversationId: "x" }) as ArtifactRoute,
  navigate: () => undefined,
  subscribe: () => () => undefined,
};

describe("<DocList>", () => {
  it("renders one row per snapshot (refs mode)", async () => {
    // No route registered → ItemLink renders the caller's label as inert text.
    const snapshots: ReadonlyArray<ItemRefSnapshot> = [
      {
        ref: { kind: "chat", id: "conv_001" as ConversationId },
        display_label: "Renewal chat",
      },
      {
        ref: { kind: "chat", id: "conv_002" as ConversationId },
        display_label: "Sourcing chat",
      },
    ];
    render(
      <RouterProvider router={noopRouter}>
        <DocList refs={snapshots} ariaLabel="Recent chats" />
      </RouterProvider>,
    );
    const list = screen.getByTestId("doc-list");
    expect(list).toHaveAttribute("data-mode", "refs");
    expect(list).toHaveAttribute("aria-label", "Recent chats");
    const rows = await screen.findAllByTestId("doc-list-row");
    expect(rows).toHaveLength(2);
  });

  it("renders one row per item via renderRow (slot mode)", () => {
    const items = ["alpha", "beta", "gamma"];
    render(
      <DocList<string>
        items={items}
        renderRow={(s) => <span data-testid={`row-${s}`}>{s}</span>}
        keyFor={(s) => s}
      />,
    );
    const list = screen.getByTestId("doc-list");
    expect(list).toHaveAttribute("data-mode", "slot");
    expect(screen.getByTestId("row-alpha")).toBeInTheDocument();
    expect(screen.getByTestId("row-beta")).toBeInTheDocument();
    expect(screen.getByTestId("row-gamma")).toBeInTheDocument();
  });
});
