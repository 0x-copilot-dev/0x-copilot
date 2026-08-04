import type { ChatArchiveRow, ConversationId } from "@0x-copilot/api-types";
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
    ...over,
  } as ChatArchiveRow;
}

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
