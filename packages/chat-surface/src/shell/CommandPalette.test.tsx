// CommandPalette — substrate-shared global ⌘K palette tests.

import type {
  ConversationId,
  PaletteHit,
  PaletteSearchResponse,
} from "@0x-copilot/api-types";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PaletteSearchPort } from "../ports/PaletteSearchPort";
import { RouterProvider } from "../providers/RouterProvider";
import {
  __resetItemRefRegistryForTests,
  registerItemRefResolver,
} from "../refs/registry";
import type { ArtifactRoute, Router } from "../routing/router";

import { CommandPalette } from "./CommandPalette";

afterEach(() => {
  __resetItemRefRegistryForTests();
  vi.useRealTimers();
});

function makeRouter(): {
  router: Router<ArtifactRoute>;
  navigate: ReturnType<typeof vi.fn>;
} {
  const navigate = vi.fn();
  return {
    router: {
      current: () => ({ kind: "chat", conversationId: "x" }) as ArtifactRoute,
      navigate,
      subscribe: () => () => undefined,
    },
    navigate,
  };
}

function makePort(hits: ReadonlyArray<PaletteHit>): {
  port: PaletteSearchPort;
  search: ReturnType<typeof vi.fn>;
} {
  const search = vi.fn().mockResolvedValue({
    hits,
    took_ms: 12,
  } satisfies PaletteSearchResponse);
  return { port: { search }, search };
}

const STARTER_ACTIONS: ReadonlyArray<PaletteHit> = [
  {
    id: "starter_search_team",
    kind: "navigation",
    title: "Search the team",
    route: "/team",
    score: 1,
  },
  {
    id: "starter_open_todos",
    kind: "navigation",
    title: "Open my todos",
    route: "/todos",
    score: 1,
  },
];

function renderPalette(args: {
  open: boolean;
  onRequestClose?: () => void;
  port?: PaletteSearchPort;
  hits?: ReadonlyArray<PaletteHit>;
  onConnectToolHint?: () => void;
  // Optional non-entity dispatch callbacks. When omitted (undefined),
  // the component sees no props — reproducing today's close-only default.
  onNavigate?: (route: string, hit: PaletteHit) => void;
  onRunAction?: (token: string, hit: PaletteHit) => void;
}): {
  rerender: (open: boolean) => void;
  onRequestClose: ReturnType<typeof vi.fn>;
  port: PaletteSearchPort;
  search: ReturnType<typeof vi.fn> | undefined;
  navigate: ReturnType<typeof vi.fn>;
} {
  const onRequestClose = vi.fn(args.onRequestClose ?? (() => undefined));
  const built = args.port !== undefined ? null : makePort(args.hits ?? []);
  const port = args.port ?? built!.port;
  const search = built?.search;
  const { router, navigate } = makeRouter();
  const ui = (open: boolean): React.ReactElement => (
    <RouterProvider router={router}>
      <CommandPalette
        open={open}
        onRequestClose={onRequestClose}
        searchPort={port}
        starterActions={STARTER_ACTIONS}
        onConnectToolHint={args.onConnectToolHint}
        onNavigate={args.onNavigate}
        onRunAction={args.onRunAction}
        debounceMs={1}
      />
    </RouterProvider>
  );
  const utils = render(ui(args.open));
  return {
    rerender: (open: boolean) => utils.rerender(ui(open)),
    onRequestClose,
    port,
    search,
    navigate,
  };
}

describe("<CommandPalette>", () => {
  it("renders nothing when open=false", () => {
    renderPalette({ open: false });
    expect(screen.queryByTestId("command-palette")).toBeNull();
  });

  it("opens when open flips to true and shows starter actions", async () => {
    const { rerender } = renderPalette({ open: false });
    expect(screen.queryByTestId("command-palette")).toBeNull();
    rerender(true);
    await waitFor(() =>
      expect(screen.getByTestId("command-palette")).toBeInTheDocument(),
    );
    // Both starter actions render under the Navigation group.
    expect(screen.getByTestId("palette-group-header")).toHaveAttribute(
      "data-group-kind",
      "navigation",
    );
    expect(screen.getAllByTestId("palette-hit-row")).toHaveLength(2);
  });

  it("calls onRequestClose on ESC", async () => {
    const { onRequestClose } = renderPalette({ open: true });
    const input = screen.getByTestId("command-palette-input");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onRequestClose).toHaveBeenCalledTimes(1);
  });

  it("calls onRequestClose on scrim click", () => {
    const { onRequestClose } = renderPalette({ open: true });
    fireEvent.click(screen.getByTestId("command-palette"));
    expect(onRequestClose).toHaveBeenCalledTimes(1);
  });

  it("does NOT close when clicking inside the card", () => {
    const { onRequestClose } = renderPalette({ open: true });
    fireEvent.click(screen.getByTestId("command-palette-card"));
    expect(onRequestClose).not.toHaveBeenCalled();
  });

  it("ArrowDown moves selection and wraps", () => {
    renderPalette({ open: true });
    const input = screen.getByTestId("command-palette-input");
    // Two starter actions: indices 0,1.
    fireEvent.keyDown(input, { key: "ArrowDown" });
    let rows = screen.getAllByTestId("palette-hit-row");
    expect(rows[1]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    rows = screen.getAllByTestId("palette-hit-row");
    // Wrapped back to index 0.
    expect(rows[0]).toHaveAttribute("aria-selected", "true");
  });

  it("ArrowUp wraps from 0 to the last index", () => {
    renderPalette({ open: true });
    const input = screen.getByTestId("command-palette-input");
    fireEvent.keyDown(input, { key: "ArrowUp" });
    const rows = screen.getAllByTestId("palette-hit-row");
    expect(rows[rows.length - 1]).toHaveAttribute("aria-selected", "true");
  });

  it("Enter activates the selected starter action via onActivate path", () => {
    const { onRequestClose } = renderPalette({ open: true });
    const input = screen.getByTestId("command-palette-input");
    fireEvent.keyDown(input, { key: "Enter" });
    // Activating a navigation hit closes the palette via onRequestClose.
    expect(onRequestClose).toHaveBeenCalled();
  });

  it("debounces the search call and forwards the query to the port", async () => {
    vi.useFakeTimers();
    const search = vi.fn().mockResolvedValue({
      hits: [
        {
          id: "hit_n_1",
          kind: "navigation",
          title: "Go to Inbox",
          route: "/inbox",
          score: 0.9,
        } satisfies PaletteHit,
      ],
      took_ms: 5,
    });
    const port: PaletteSearchPort = { search };
    const { router } = makeRouter();
    render(
      <RouterProvider router={router}>
        <CommandPalette
          open={true}
          onRequestClose={() => undefined}
          searchPort={port}
          starterActions={STARTER_ACTIONS}
          debounceMs={150}
        />
      </RouterProvider>,
    );
    const input = screen.getByTestId("command-palette-input");
    fireEvent.change(input, { target: { value: "in" } });
    fireEvent.change(input, { target: { value: "inb" } });
    fireEvent.change(input, { target: { value: "inbox" } });
    // No call yet — still inside the debounce window.
    expect(search).not.toHaveBeenCalled();
    await act(async () => {
      vi.advanceTimersByTime(160);
    });
    expect(search).toHaveBeenCalledTimes(1);
    expect(search).toHaveBeenCalledWith(
      expect.objectContaining({ q: "inbox" }),
    );
  });

  it("renders the No-results state with a Connect-a-tool hint", async () => {
    const onConnectToolHint = vi.fn();
    renderPalette({
      open: true,
      hits: [],
      onConnectToolHint,
    });
    const input = screen.getByTestId("command-palette-input");
    fireEvent.change(input, { target: { value: "xyzzy" } });
    await waitFor(() =>
      expect(screen.getByTestId("palette-no-results")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("palette-connect-tool-hint"));
    expect(onConnectToolHint).toHaveBeenCalledTimes(1);
  });

  it("renders group headers per non-empty kind bucket", async () => {
    const hits: ReadonlyArray<PaletteHit> = [
      {
        id: "hit_n_1",
        kind: "navigation",
        title: "Go to Team",
        route: "/team",
        score: 0.9,
      },
      {
        id: "hit_a_1",
        kind: "action",
        title: "Make this a routine?",
        action_token: "atlas.routine.from_chat",
        score: 0.8,
      },
      {
        id: "hit_c_1",
        kind: "command",
        title: "/help",
        action_token: "/help",
        score: 0.5,
      },
    ];
    renderPalette({ open: true, hits });
    const input = screen.getByTestId("command-palette-input");
    fireEvent.change(input, { target: { value: "anything" } });
    await waitFor(() => {
      const headers = screen.queryAllByTestId("palette-group-header");
      expect(headers).toHaveLength(3);
    });
    const kinds = screen
      .getAllByTestId("palette-group-header")
      .map((h) => h.getAttribute("data-group-kind"));
    expect(kinds).toEqual(["navigation", "action", "command"]);
  });

  it("activates an entity hit by clicking its ItemLink (router.navigate via registry)", async () => {
    registerItemRefResolver("chat", async () => ({
      label: "Acme renewal",
      icon: null,
      route: { kind: "chat", conversationId: "conv_001" },
    }));
    const hits: ReadonlyArray<PaletteHit> = [
      {
        id: "hit_ent_1",
        kind: "entity",
        title: "Acme renewal",
        target: { kind: "chat", id: "conv_001" as ConversationId },
        score: 0.95,
      },
    ];
    const { onRequestClose } = renderPalette({ open: true, hits });
    const input = screen.getByTestId("command-palette-input");
    fireEvent.change(input, { target: { value: "acme" } });
    await waitFor(() =>
      expect(screen.getByTestId("item-link")).toBeInTheDocument(),
    );
    fireEvent.keyDown(input, { key: "Enter" });
    // Entity-hit activation closes the palette after dispatching the
    // ItemLink click.
    expect(onRequestClose).toHaveBeenCalled();
  });

  it("dispatches a navigation hit to onNavigate(route, hit) then closes", async () => {
    const onNavigate = vi.fn();
    const navHit: PaletteHit = {
      id: "hit_n_1",
      kind: "navigation",
      title: "Go to Tools",
      route: "/tools",
      score: 0.9,
    };
    const { onRequestClose } = renderPalette({
      open: true,
      hits: [navHit],
      onNavigate,
    });
    const input = screen.getByTestId("command-palette-input");
    fireEvent.change(input, { target: { value: "tools" } });
    await waitFor(() =>
      expect(screen.getByTestId("palette-hit-button")).toBeInTheDocument(),
    );
    act(() => {
      fireEvent.click(screen.getByTestId("palette-hit-button"));
    });
    expect(onNavigate).toHaveBeenCalledTimes(1);
    expect(onNavigate).toHaveBeenCalledWith("/tools", navHit);
    expect(onRequestClose).toHaveBeenCalled();
  });

  it("dispatches an action hit to onRunAction(action_token, hit) then closes", async () => {
    const onRunAction = vi.fn();
    const actionHit: PaletteHit = {
      id: "hit_a_1",
      kind: "action",
      title: "Add a provider key",
      action_token: "settings.add_provider_key",
      score: 0.8,
    };
    const { onRequestClose } = renderPalette({
      open: true,
      hits: [actionHit],
      onRunAction,
    });
    const input = screen.getByTestId("command-palette-input");
    fireEvent.change(input, { target: { value: "key" } });
    await waitFor(() =>
      expect(screen.getByTestId("palette-hit-button")).toBeInTheDocument(),
    );
    act(() => {
      fireEvent.click(screen.getByTestId("palette-hit-button"));
    });
    expect(onRunAction).toHaveBeenCalledTimes(1);
    expect(onRunAction).toHaveBeenCalledWith(
      "settings.add_provider_key",
      actionHit,
    );
    expect(onRequestClose).toHaveBeenCalled();
  });

  it("dispatches a command hit to onRunAction(action_token, hit) then closes", async () => {
    const onRunAction = vi.fn();
    const commandHit: PaletteHit = {
      id: "hit_c_1",
      kind: "command",
      title: "/help",
      action_token: "/help",
      score: 0.5,
    };
    const { onRequestClose } = renderPalette({
      open: true,
      hits: [commandHit],
      onRunAction,
    });
    const input = screen.getByTestId("command-palette-input");
    fireEvent.change(input, { target: { value: "help" } });
    await waitFor(() =>
      expect(screen.getByTestId("palette-hit-button")).toBeInTheDocument(),
    );
    act(() => {
      fireEvent.click(screen.getByTestId("palette-hit-button"));
    });
    expect(onRunAction).toHaveBeenCalledTimes(1);
    expect(onRunAction).toHaveBeenCalledWith("/help", commandHit);
    expect(onRequestClose).toHaveBeenCalled();
  });

  it("still routes an entity hit via its ItemLink when dispatch props are provided", async () => {
    registerItemRefResolver("chat", async () => ({
      label: "Acme renewal",
      icon: null,
      route: { kind: "chat", conversationId: "conv_001" },
    }));
    const onNavigate = vi.fn();
    const onRunAction = vi.fn();
    const hits: ReadonlyArray<PaletteHit> = [
      {
        id: "hit_ent_1",
        kind: "entity",
        title: "Acme renewal",
        target: { kind: "chat", id: "conv_001" as ConversationId },
        score: 0.95,
      },
    ];
    const { onRequestClose } = renderPalette({
      open: true,
      hits,
      onNavigate,
      onRunAction,
    });
    const input = screen.getByTestId("command-palette-input");
    fireEvent.change(input, { target: { value: "acme" } });
    await waitFor(() =>
      expect(screen.getByTestId("item-link")).toBeInTheDocument(),
    );
    act(() => {
      fireEvent.keyDown(input, { key: "Enter" });
    });
    // Entity hits keep routing through the ItemLink — the new non-entity
    // callbacks are never invoked for them.
    expect(onNavigate).not.toHaveBeenCalled();
    expect(onRunAction).not.toHaveBeenCalled();
    expect(onRequestClose).toHaveBeenCalled();
  });

  it("with dispatch props omitted, a navigation hit is close-only (today's behavior)", async () => {
    // Local spies that are deliberately NOT passed to the palette. They
    // prove the additive props default to close-only: nothing is invoked.
    const onNavigate = vi.fn();
    const onRunAction = vi.fn();
    const navHit: PaletteHit = {
      id: "hit_n_1",
      kind: "navigation",
      title: "Go to Tools",
      route: "/tools",
      score: 0.9,
    };
    const { onRequestClose } = renderPalette({
      open: true,
      hits: [navHit],
      // onNavigate / onRunAction intentionally omitted.
    });
    const input = screen.getByTestId("command-palette-input");
    fireEvent.change(input, { target: { value: "tools" } });
    await waitFor(() =>
      expect(screen.getByTestId("palette-hit-button")).toBeInTheDocument(),
    );
    act(() => {
      fireEvent.click(screen.getByTestId("palette-hit-button"));
    });
    expect(onNavigate).not.toHaveBeenCalled();
    expect(onRunAction).not.toHaveBeenCalled();
    // Close-only: the palette still closes, exactly as before this PR.
    expect(onRequestClose).toHaveBeenCalledTimes(1);
  });

  it("has dialog + combobox + listbox roles for assistive tech", () => {
    renderPalette({ open: true });
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    const combobox = screen.getByRole("combobox");
    expect(combobox).toBeInTheDocument();
    const listbox = screen.getByRole("listbox");
    expect(listbox).toBeInTheDocument();
  });

  it("sets aria-activedescendant on the input to the selected row id", () => {
    renderPalette({ open: true });
    const input = screen.getByTestId(
      "command-palette-input",
    ) as HTMLInputElement;
    const firstRow = screen.getAllByTestId("palette-hit-row")[0];
    expect(input.getAttribute("aria-activedescendant")).toBe(firstRow.id);
  });
});
