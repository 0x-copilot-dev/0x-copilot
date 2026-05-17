import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../providers/RouterProvider";
import type { ArtifactRoute, NavigateOptions, Router } from "../routing/router";
import { CommandPalette } from "./CommandPalette";

function makeRouter(): Router<ArtifactRoute | null> & {
  readonly navigate: ReturnType<typeof vi.fn>;
} {
  const navigate =
    vi.fn<(route: ArtifactRoute | null, opts?: NavigateOptions) => void>();
  return {
    current: () => null,
    navigate,
    subscribe: () => () => {},
  };
}

function pressCmdK(): void {
  fireEvent.keyDown(globalThis.document, { key: "k", metaKey: true });
}

function pressEscape(): void {
  fireEvent.keyDown(globalThis.document, { key: "Escape" });
}

describe("CommandPalette", () => {
  let router: ReturnType<typeof makeRouter>;

  beforeEach(() => {
    router = makeRouter();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function renderPalette(
    extraEntries?: React.ComponentProps<typeof CommandPalette>["extraEntries"],
  ) {
    return render(
      <RouterProvider router={router}>
        <CommandPalette extraEntries={extraEntries} />
      </RouterProvider>,
    );
  }

  it("is closed by default", () => {
    renderPalette();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens on Cmd+K", () => {
    renderPalette();
    pressCmdK();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("opens on Ctrl+K", () => {
    renderPalette();
    fireEvent.keyDown(globalThis.document, { key: "k", ctrlKey: true });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("toggles closed when Cmd+K is pressed again", () => {
    renderPalette();
    pressCmdK();
    pressCmdK();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes on Escape", () => {
    renderPalette();
    pressCmdK();
    pressEscape();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders destination entries from ROUTE_TABLE", () => {
    renderPalette();
    pressCmdK();
    // Every destination label appears at least once.
    expect(screen.getAllByRole("option").length).toBeGreaterThan(0);
    // Filter to entries whose label matches the destination kind (text appears
    // twice for "Workspace": once as a label, once as a hint on placeholders).
    expect(screen.getAllByText("Chat").length).toBeGreaterThan(0);
    expect(screen.getByText("Connector")).toBeInTheDocument();
    expect(screen.getAllByText("Workspace").length).toBeGreaterThan(0);
  });

  it("filters by query (case-insensitive substring)", async () => {
    const user = userEvent.setup();
    renderPalette();
    pressCmdK();
    const input = screen.getByRole("combobox");
    await user.type(input, "conn");
    expect(screen.getByText("Connector")).toBeInTheDocument();
    expect(screen.queryByText("Chat")).not.toBeInTheDocument();
    expect(screen.queryByText("Workspace")).not.toBeInTheDocument();
  });

  it("shows 'No matches' for empty result set", async () => {
    const user = userEvent.setup();
    renderPalette();
    pressCmdK();
    const input = screen.getByRole("combobox");
    await user.type(input, "qqqqqqqq-not-a-label");
    expect(screen.getByText("No matches")).toBeInTheDocument();
  });

  it("Arrow Down moves selection forward; Enter navigates the selected entry", async () => {
    const user = userEvent.setup();
    renderPalette();
    pressCmdK();
    const input = screen.getByRole("combobox");
    await user.type(input, "chat");
    // Move to the next item after Chat (filtered list still has chat-suffix entries).
    await user.keyboard("{ArrowDown}");
    await user.keyboard("{Enter}");
    expect(router.navigate).toHaveBeenCalledTimes(1);
  });

  it("Arrow Up wraps from the top", async () => {
    const user = userEvent.setup();
    renderPalette();
    pressCmdK();
    const input = screen.getByRole("combobox");
    await user.type(input, "chat");
    await user.keyboard("{ArrowUp}");
    await user.keyboard("{Enter}");
    expect(router.navigate).toHaveBeenCalled();
  });

  it("Enter on the first match navigates that route and closes the palette", async () => {
    const user = userEvent.setup();
    renderPalette();
    pressCmdK();
    const input = screen.getByRole("combobox");
    await user.type(input, "Connector");
    await user.keyboard("{Enter}");
    expect(router.navigate).toHaveBeenCalledTimes(1);
    const [route] = router.navigate.mock.calls[0] ?? [];
    expect(route).toEqual({ kind: "mcp", serverId: "all" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("clicking a row navigates and closes", async () => {
    const user = userEvent.setup();
    renderPalette();
    pressCmdK();
    // Use the search to isolate a single matching row first.
    const input = screen.getByRole("combobox");
    await user.type(input, "Acme workspace");
    const row = screen.getByText("Acme workspace").closest("li");
    expect(row).not.toBeNull();
    fireEvent.click(row!);
    expect(router.navigate).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("clicking the scrim closes without navigating", () => {
    renderPalette();
    pressCmdK();
    const dialog = screen.getByRole("dialog");
    fireEvent.click(dialog);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(router.navigate).not.toHaveBeenCalled();
  });

  it("renders extraEntries alongside destinations", async () => {
    const user = userEvent.setup();
    renderPalette([
      {
        id: "extra-1",
        label: "Q4 sales push",
        hint: "Project",
        route: { kind: "workspace", workspaceId: "q4-sales" },
      },
    ]);
    pressCmdK();
    const input = screen.getByRole("combobox");
    await user.type(input, "q4 sales");
    expect(screen.getByText("Q4 sales push")).toBeInTheDocument();
    await user.keyboard("{Enter}");
    expect(router.navigate).toHaveBeenCalledWith({
      kind: "workspace",
      workspaceId: "q4-sales",
    });
  });
});
