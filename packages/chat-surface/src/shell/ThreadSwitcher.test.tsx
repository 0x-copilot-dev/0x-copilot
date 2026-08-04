import type {
  ChatArchiveRow,
  ConversationId,
  ProjectColorHue,
  ProjectId,
} from "@0x-copilot/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CHATS_SECTION_ORDER } from "../destinations/chats/ChatsArchive";

import { CONTEXT_PANEL_WIDTH } from "./ContextPanel";
import {
  THREAD_SECTION_ORDER,
  THREAD_SWITCHER_COMPACT_WIDTH,
  THREAD_SWITCHER_DOCK_FLOOR,
  ThreadSwitcher,
  ThreadSwitcherToggle,
  threadSwitcherDockWidth,
  type ThreadListSource,
  type ThreadScopeOption,
} from "./ThreadSwitcher";

function row(id: string, title: string, over: Partial<ChatArchiveRow> = {}) {
  return {
    id: id as ConversationId,
    title,
    status: "done",
    preview: "",
    model: "",
    updated_at: "2026-08-01T10:00:00Z",
    pinned: false,
    // REQUIRED on `ChatArchiveRow` — a row is a projection, not a wire payload,
    // so "unfiled" has to be stated rather than left absent.
    project_id: null,
    ...over,
  } as ChatArchiveRow;
}

const ACME = "p-acme" as ProjectId;
const ATLAS = "p-atlas" as ProjectId;

const SCOPES: ReadonlyArray<ThreadScopeOption> = [
  {
    id: ACME,
    name: "Acme renewal",
    colorHue: 210 as ProjectColorHue,
    count: 4,
  },
  // No `count` — the bucketed archive does not return per-project totals, so a
  // host that cannot count must be able to omit the number.
  { id: ATLAS, name: "Atlas launch", colorHue: 140 as ProjectColorHue },
];

function source(over: Partial<ThreadListSource> = {}): ThreadListSource {
  return {
    archive: {
      status: "ok",
      data: {
        pinned: [row("c-pin", "Windowed-mode PRD suite", { pinned: true })],
        recent: [
          row("c-1", "Manual file creation", { status: "running" }),
          row("c-2", "Fix Linear MCP 401"),
        ],
        archived: [],
      },
    },
    hasMore: { pinned: false, recent: false, archived: false },
    onLoadMore: vi.fn(),
    retry: vi.fn(),
    ...over,
  };
}

describe("ThreadSwitcher", () => {
  it("renders bucketed threads and hides empty buckets", () => {
    render(
      <ThreadSwitcher
        variant="docked"
        controller={source()}
        activeConversationId={null}
        onOpenConversation={vi.fn()}
      />,
    );
    expect(screen.getByTestId("thread-switcher-section-pinned")).toBeTruthy();
    expect(screen.getByTestId("thread-switcher-section-recent")).toBeTruthy();
    // `archived` is empty — an empty labelled section is noise, not information.
    expect(screen.queryByTestId("thread-switcher-section-archived")).toBeNull();
    expect(screen.getByText("Manual file creation")).toBeTruthy();
  });

  it("marks the active conversation with aria-current (FR-1.6)", () => {
    render(
      <ThreadSwitcher
        variant="docked"
        controller={source()}
        activeConversationId={"c-1" as ConversationId}
        onOpenConversation={vi.fn()}
      />,
    );
    expect(
      screen
        .getByTestId("thread-switcher-row-c-1")
        .getAttribute("aria-current"),
    ).toBe("true");
    expect(
      screen
        .getByTestId("thread-switcher-row-c-2")
        .getAttribute("aria-current"),
    ).toBeNull();
  });

  it("reports the picked conversation to the host (FR-1.7)", () => {
    const onOpen = vi.fn();
    render(
      <ThreadSwitcher
        variant="docked"
        controller={source()}
        activeConversationId={null}
        onOpenConversation={onOpen}
      />,
    );
    fireEvent.click(screen.getByTestId("thread-switcher-row-c-2"));
    expect(onOpen).toHaveBeenCalledWith("c-2");
  });

  it("closes the overlay on activation but leaves the dock open (FR-1.8)", () => {
    const close = vi.fn();
    const { unmount } = render(
      <ThreadSwitcher
        variant="overlay"
        controller={source()}
        activeConversationId={null}
        onOpenConversation={vi.fn()}
        onRequestClose={close}
      />,
    );
    fireEvent.click(screen.getByTestId("thread-switcher-row-c-2"));
    expect(close).toHaveBeenCalledTimes(1);
    unmount();

    const close2 = vi.fn();
    render(
      <ThreadSwitcher
        variant="docked"
        controller={source()}
        activeConversationId={null}
        onOpenConversation={vi.fn()}
        onRequestClose={close2}
      />,
    );
    fireEvent.click(screen.getByTestId("thread-switcher-row-c-2"));
    expect(close2).not.toHaveBeenCalled();
  });

  it("is a modal dialog as an overlay and a plain region when docked", () => {
    const { unmount } = render(
      <ThreadSwitcher
        variant="overlay"
        controller={source()}
        activeConversationId={null}
        onOpenConversation={vi.fn()}
        onRequestClose={vi.fn()}
      />,
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    unmount();

    render(
      <ThreadSwitcher
        variant="docked"
        controller={source()}
        activeConversationId={null}
        onOpenConversation={vi.fn()}
      />,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("closes the overlay on Escape (FR-1.9)", () => {
    const close = vi.fn();
    render(
      <ThreadSwitcher
        variant="overlay"
        controller={source()}
        activeConversationId={null}
        onOpenConversation={vi.fn()}
        onRequestClose={close}
      />,
    );
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("ignores Escape when docked — it is not modal", () => {
    const close = vi.fn();
    render(
      <ThreadSwitcher
        variant="docked"
        controller={source()}
        activeConversationId={null}
        onOpenConversation={vi.fn()}
        onRequestClose={close}
      />,
    );
    fireEvent.keyDown(screen.getByTestId("thread-switcher-body"), {
      key: "Escape",
    });
    expect(close).not.toHaveBeenCalled();
  });

  it("offers Retry on error without hiding the panel (FR-1.10)", () => {
    const retry = vi.fn();
    render(
      <ThreadSwitcher
        variant="docked"
        controller={source({ archive: { status: "error" }, retry })}
        activeConversationId={null}
        onOpenConversation={vi.fn()}
      />,
    );
    // The panel chrome is still there — a failed list must not take the panel
    // (or, in the cockpit, the transcript) down with it.
    expect(screen.getByTestId("thread-switcher-title")).toBeTruthy();
    fireEvent.click(screen.getByTestId("thread-switcher-retry"));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it("distinguishes an ok-with-no-payload from an empty list", () => {
    render(
      <ThreadSwitcher
        variant="docked"
        controller={source({ archive: { status: "ok" } })}
        activeConversationId={null}
        onOpenConversation={vi.fn()}
      />,
    );
    // `SectionResult.data` is optional even on `ok`. "Threads unavailable" and
    // "No threads yet" look alike and mean opposite things.
    expect(screen.getByTestId("thread-switcher-unavailable")).toBeTruthy();
    expect(screen.queryByTestId("thread-switcher-empty")).toBeNull();
  });

  it("renders no account row — the rail avatar is the only account affordance", () => {
    // The regression this guards: the panel used to carry its own avatar +
    // display name in a foot, which rendered at the same time as `AppRail`'s
    // `data-rail-me` button — two identical circles with the same initial, on
    // the same baseline, only one of them clickable.
    render(
      <ThreadSwitcher
        variant="docked"
        controller={source()}
        activeConversationId={null}
        onOpenConversation={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("thread-switcher-identity")).toBeNull();
  });

  it("narrows the DOCKED panel at compact so the canvas stays usable", () => {
    // The regression this guards: the first cut turned the panel into a modal
    // overlay at `compact`, which put a scrim over the composer of an ordinary
    // 640px window — you could browse threads or type, never both.
    render(
      <ThreadSwitcher
        variant="docked"
        compact
        controller={source()}
        activeConversationId={null}
        onOpenConversation={vi.fn()}
      />,
    );
    const panel = screen.getByLabelText("Threads");
    expect(panel.style.width).toBe(`${THREAD_SWITCHER_COMPACT_WIDTH}px`);
    // 48px rail + 200px panel still leaves 392px of canvas at a 640px window.
    expect(640 - 48 - THREAD_SWITCHER_COMPACT_WIDTH).toBeGreaterThanOrEqual(
      350,
    );
  });

  it("keeps the full dock width when not compact", () => {
    render(
      <ThreadSwitcher
        variant="docked"
        controller={source()}
        activeConversationId={null}
        onOpenConversation={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("Threads").style.width).toBe(
      `${CONTEXT_PANEL_WIDTH}px`,
    );
  });

  it("keeps the dock floor below the narrowest cockpit the app can produce", () => {
    // The floor is a CONTAINER width — the observer sits inside the 48px app
    // rail. Comparing it against a WINDOW width is off by exactly the rail,
    // which is how the first fix still produced a scrim at a 640px window.
    const NARROWEST_COCKPIT = 640 - 48; // host minWidth 640, minus the app rail
    expect(THREAD_SWITCHER_DOCK_FLOOR).toBeLessThan(NARROWEST_COCKPIT);
    // …and it must still leave room to type once the panel is subtracted.
    expect(
      THREAD_SWITCHER_DOCK_FLOOR - THREAD_SWITCHER_COMPACT_WIDTH,
    ).toBeGreaterThanOrEqual(350);
    expect(threadSwitcherDockWidth(true)).toBe(THREAD_SWITCHER_COMPACT_WIDTH);
    expect(threadSwitcherDockWidth(false)).toBe(CONTEXT_PANEL_WIDTH);
  });

  it("keeps its section order equal to the Chats destination's", () => {
    // Two lists of the same three buckets is one drift away from the switcher
    // and the archive disagreeing about order for identical data.
    expect([...THREAD_SECTION_ORDER]).toEqual([...CHATS_SECTION_ORDER]);
  });
});

describe("ThreadSwitcher — project scope (D-1.4)", () => {
  function renderScoped(
    over: {
      scope?: ProjectId | null;
      scopeOptions?: ReadonlyArray<ThreadScopeOption>;
      onScopeChange?: (next: ProjectId | null) => void;
      onNewRun?: () => void;
      onRequestClose?: () => void;
      variant?: "docked" | "overlay";
    } = {},
  ) {
    const {
      variant = "docked",
      scope = null,
      scopeOptions = SCOPES,
      ...rest
    } = over;
    return render(
      <ThreadSwitcher
        variant={variant}
        controller={source()}
        activeConversationId={null}
        onOpenConversation={vi.fn()}
        onNewRun={vi.fn()}
        scope={scope}
        scopeOptions={scopeOptions}
        {...rest}
      />,
    );
  }

  it("puts + New run BEFORE the scope control in DOM order", () => {
    // Owner decision, not an accident of layout: New run is the ACTION and the
    // scope is its qualifier, so the two must read top-to-bottom as one
    // sentence. Asserted structurally so a later tidy-up cannot transpose them.
    renderScoped();
    const newRun = screen.getByTestId("thread-switcher-new");
    const scopeControl = screen.getByTestId("thread-switcher-scope");
    expect(
      newRun.compareDocumentPosition(scopeControl) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeGreaterThan(0);
    // …and both sit above the bucket list.
    expect(
      scopeControl.compareDocumentPosition(
        screen.getByTestId("thread-switcher-body"),
      ) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeGreaterThan(0);
  });

  it("renders no scope control at all when the host has no projects", () => {
    // A host with nothing to scope to must get EXACTLY today's panel — not a
    // picker whose only entry is the state it is already in.
    const { unmount } = render(
      <ThreadSwitcher
        variant="docked"
        controller={source()}
        activeConversationId={null}
        onOpenConversation={vi.fn()}
        onNewRun={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("thread-switcher-scope")).toBeNull();
    expect(screen.getByTestId("thread-switcher-new")).toBeTruthy();
    unmount();

    renderScoped({ scopeOptions: [] });
    expect(screen.queryByTestId("thread-switcher-scope")).toBeNull();
  });

  it("reads All threads when unscoped and the project when scoped", () => {
    const { unmount } = renderScoped();
    const trigger = screen.getByTestId("thread-switcher-scope-trigger");
    expect(trigger.textContent).toContain("All threads");
    expect(trigger.getAttribute("aria-label")).toBe("Scope: all threads");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    unmount();

    renderScoped({ scope: ACME });
    const scoped = screen.getByTestId("thread-switcher-scope-trigger");
    expect(scoped.textContent).toContain("Acme renewal");
    // Monogram on the hue, NEVER `icon_emoji` — the server defaults that field
    // to 📁 for every project, which renders a wall of identical folders.
    expect(scoped.textContent).toContain("A");
    expect(scoped.getAttribute("aria-label")).toBe("Scope: Acme renewal");
  });

  it("lists All threads first, then a separator, then each project", () => {
    renderScoped({ scope: ACME });
    fireEvent.click(screen.getByTestId("thread-switcher-scope-trigger"));

    const all = screen.getByTestId("thread-switcher-scope-all");
    const options = screen.getAllByTestId("thread-switcher-scope-option");
    expect(options.map((o) => o.getAttribute("data-project-id"))).toEqual([
      ACME,
      ATLAS,
    ]);
    expect(
      all.compareDocumentPosition(options[0]!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeGreaterThan(0);

    // Single-select semantics, matching the composer's filing chip.
    expect(options[0]!.getAttribute("aria-checked")).toBe("true");
    expect(options[1]!.getAttribute("aria-checked")).toBe("false");
    expect(all.getAttribute("aria-checked")).toBe("false");

    // Counts are optional: Acme supplies one, Atlas does not.
    expect(options[0]!.textContent).toContain("4");
    expect(options[1]!.textContent).toBe("AAtlas launch");
  });

  it("reports the picked project to the host", () => {
    const onScopeChange = vi.fn();
    renderScoped({ onScopeChange });
    fireEvent.click(screen.getByTestId("thread-switcher-scope-trigger"));
    fireEvent.click(screen.getAllByTestId("thread-switcher-scope-option")[1]!);
    expect(onScopeChange).toHaveBeenCalledWith(ATLAS);
    // Picking closes the menu — the answer is on the trigger now.
    expect(screen.queryByTestId("thread-switcher-scope-menu")).toBeNull();
  });

  it("reports null when All threads is picked", () => {
    const onScopeChange = vi.fn();
    renderScoped({ scope: ACME, onScopeChange });
    fireEvent.click(screen.getByTestId("thread-switcher-scope-trigger"));
    fireEvent.click(screen.getByTestId("thread-switcher-scope-all"));
    expect(onScopeChange).toHaveBeenCalledWith(null);
  });

  it("names the active scope on the New run button", () => {
    // The only place a user learns that a new run inherits the scope.
    const { unmount } = renderScoped({ scope: ACME });
    expect(screen.getByTestId("thread-switcher-new").textContent).toContain(
      "New runin Acme renewal",
    );
    expect(screen.getByTestId("thread-switcher-new-scope")).toBeTruthy();
    unmount();

    renderScoped();
    expect(screen.queryByTestId("thread-switcher-new-scope")).toBeNull();
    expect(screen.getByTestId("thread-switcher-new").textContent).toBe(
      "New run",
    );
  });

  it("closes the MENU on Escape, not the overlay panel", () => {
    // The trap: the menu lives inside the panel, and the panel's own
    // `handleKeyDown` closes the overlay on Escape. A menu-Escape that bubbled
    // would take the whole panel down with the menu still open.
    const onRequestClose = vi.fn();
    renderScoped({ variant: "overlay", onRequestClose });

    const trigger = screen.getByTestId("thread-switcher-scope-trigger");
    fireEvent.click(trigger);
    fireEvent.keyDown(screen.getByTestId("thread-switcher-scope-menu"), {
      key: "Escape",
    });
    expect(screen.queryByTestId("thread-switcher-scope-menu")).toBeNull();
    expect(onRequestClose).not.toHaveBeenCalled();

    // Opening does NOT move focus into the menu, so the very next Escape a real
    // user presses is dispatched on the TRIGGER — which is outside the menu's
    // subtree. That keystroke must close the menu too, not the panel.
    fireEvent.click(trigger);
    fireEvent.keyDown(trigger, { key: "Escape" });
    expect(screen.queryByTestId("thread-switcher-scope-menu")).toBeNull();
    expect(onRequestClose).not.toHaveBeenCalled();

    // …and with the menu closed, FR-1.9 is untouched: Escape still closes the
    // overlay.
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onRequestClose).toHaveBeenCalledTimes(1);
  });
});

describe("ThreadSwitcherToggle", () => {
  it("is a disclosure control wired to the panel (FR-1.2)", () => {
    const onToggle = vi.fn();
    render(
      <ThreadSwitcherToggle
        open={false}
        onToggle={onToggle}
        controls="run-thread-switcher"
      />,
    );
    const btn = screen.getByTestId("thread-switcher-toggle");
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    expect(btn.getAttribute("aria-controls")).toBe("run-thread-switcher");
    expect(btn.getAttribute("aria-label")).toBe("Show threads");
    fireEvent.click(btn);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("flips its label when open", () => {
    render(
      <ThreadSwitcherToggle
        open
        onToggle={vi.fn()}
        controls="run-thread-switcher"
      />,
    );
    const btn = screen.getByTestId("thread-switcher-toggle");
    expect(btn.getAttribute("aria-expanded")).toBe("true");
    expect(btn.getAttribute("aria-label")).toBe("Hide threads");
  });
});
