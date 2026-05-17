// ThreadCanvas tests.
//
// The crown jewel here is the **mount-once invariant** suite. The
// three modes (Studio / Focus / Auto) are presentation slots, not
// separate canvases — switching modes MUST NOT remount the inner
// components. We assert this via a `useRef`-stamped instance id on
// TcSurfaceMount + TcChat that persists across rerenders only if the
// underlying component instance survives.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";

import type {
  RuntimeApiEventType,
  RuntimeEventEnvelope,
} from "@enterprise-search/api-types";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@enterprise-search/chat-transport";

import type { ConversationId, RunId } from "@enterprise-search/api-types";

import { TransportProvider } from "../providers/TransportProvider";
import { clearRegistry } from "../surfaces/SurfaceRegistry";
import { ThreadCanvas, type ThreadMode } from "./ThreadCanvas";
import type { TcTab } from "./TcTabs";

// ============================================================
// Helpers
// ============================================================

function makeTransport(): Transport {
  return {
    request: <TRes,>(_req: TypedRequest): Promise<TRes> =>
      Promise.resolve({ messages: [] } as unknown as TRes),
    subscribeServerSentEvents: (
      _opts: SseSubscribeOptions,
    ): SseSubscription => ({
      close: () => {},
    }),
    getSession: (): Session => ({ bearer: null }),
    capabilities: (): TransportCapabilities => ({
      substrate: "web",
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
  };
}

function withProviders(transport: Transport, children: ReactNode): ReactNode {
  return (
    <TransportProvider transport={transport}>{children}</TransportProvider>
  );
}

let nextSeq = 0;

function makeEnvelope(
  type: RuntimeApiEventType,
  overrides: Partial<RuntimeEventEnvelope> = {},
): RuntimeEventEnvelope {
  const seq = overrides.sequence_no ?? nextSeq;
  nextSeq = Math.max(nextSeq, seq + 1);
  return {
    event_id: overrides.event_id ?? `evt-${seq}`,
    run_id: "run-1",
    conversation_id: "conv-1",
    sequence_no: seq,
    event_type: type,
    activity_kind: overrides.activity_kind ?? "event",
    payload: overrides.payload ?? {},
    created_at: new Date(1700000000000 + seq * 1000).toISOString(),
    ...overrides,
  };
}

const SAMPLE_TABS: readonly TcTab[] = [
  { uri: "email://draft-1", title: "Renewal email" },
  { uri: "sf-opp://acme/op-1", title: "Acme — Closed Won", pinned: true },
];

const CONV_ID = "conv-1" as ConversationId;
const RUN_ID = "run-1" as RunId;

interface RenderArgs {
  readonly mode?: ThreadMode;
  readonly onModeChange?: (mode: ThreadMode) => void;
  readonly events?: readonly RuntimeEventEnvelope[];
  readonly runId?: RunId | null;
  readonly tabs?: readonly TcTab[];
  readonly activeUri?: string;
  readonly transport?: Transport;
  readonly scrubbedSeq?: number | null;
  readonly onScrub?: (sequenceNo: number) => void;
  readonly onSnapToNow?: () => void;
}

function renderCanvas(args: RenderArgs = {}) {
  const transport = args.transport ?? makeTransport();
  const runId = "runId" in args ? args.runId : RUN_ID;
  return render(
    withProviders(
      transport,
      <ThreadCanvas
        mode={args.mode ?? "studio"}
        conversationId={CONV_ID}
        runId={runId ?? null}
        events={args.events ?? []}
        onModeChange={args.onModeChange ?? (() => {})}
        tabs={args.tabs ?? SAMPLE_TABS}
        activeUri={args.activeUri ?? "email://draft-1"}
        onActivateTab={() => {}}
        onCloseTab={() => {}}
        transport={transport}
        scrubbedSeq={args.scrubbedSeq ?? null}
        onScrub={args.onScrub}
        onSnapToNow={args.onSnapToNow}
      />,
    ),
  );
}

// ============================================================
// Tests
// ============================================================

describe("ThreadCanvas", () => {
  let warnSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    nextSeq = 0;
    warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    clearRegistry();
    warnSpy.mockRestore();
    vi.restoreAllMocks();
  });

  describe("structure", () => {
    it("renders the canvas root with conversation + mode metadata", () => {
      renderCanvas({ mode: "studio" });
      const root = screen.getByTestId("thread-canvas");
      expect(root).toHaveAttribute("data-conversation-id", "conv-1");
      expect(root).toHaveAttribute("data-mode", "studio");
      expect(root).toHaveAttribute("data-resolved-mode", "studio");
    });

    it("renders the mode-switcher tablist with three tabs (Studio/Focus/Auto)", () => {
      renderCanvas();
      const tablist = screen.getByTestId("tc-mode-switcher");
      expect(tablist).toHaveAttribute("role", "tablist");
      expect(screen.getByTestId("tc-mode-switcher-studio")).toBeInTheDocument();
      expect(screen.getByTestId("tc-mode-switcher-focus")).toBeInTheDocument();
      expect(screen.getByTestId("tc-mode-switcher-auto")).toBeInTheDocument();
    });

    it("marks the active mode button with aria-selected=true", () => {
      renderCanvas({ mode: "focus" });
      expect(screen.getByTestId("tc-mode-switcher-focus")).toHaveAttribute(
        "aria-selected",
        "true",
      );
      expect(screen.getByTestId("tc-mode-switcher-studio")).toHaveAttribute(
        "aria-selected",
        "false",
      );
    });

    it("renders the chat slot in every mode", () => {
      renderCanvas({ mode: "studio" });
      expect(screen.getByTestId("tc-chat-slot")).toBeInTheDocument();
    });

    it("renders the surface slot visibly in Studio mode", () => {
      renderCanvas({ mode: "studio" });
      expect(screen.getByTestId("tc-surface-slot")).toHaveAttribute(
        "data-visible",
        "true",
      );
    });

    it("hides the surface slot in Focus mode (but does NOT remove it)", () => {
      renderCanvas({ mode: "focus" });
      // The surface slot div stays in the DOM (mount-once invariant)
      // but its data-visible flag is false; CSS hides it.
      expect(screen.getByTestId("tc-surface-slot")).toHaveAttribute(
        "data-visible",
        "false",
      );
    });

    it("renders the swimlanes slot in Studio mode when runId is non-null", () => {
      renderCanvas({ mode: "studio", runId: RUN_ID });
      expect(screen.getByTestId("tc-swimlanes-slot")).toBeInTheDocument();
    });

    it("does not render swimlanes when runId is null", () => {
      renderCanvas({ mode: "studio", runId: null });
      expect(screen.queryByTestId("tc-swimlanes-slot")).not.toBeInTheDocument();
    });

    it("renders the mini-timeline in Studio and Focus modes", () => {
      const { rerender } = renderCanvas({ mode: "studio" });
      expect(screen.getByTestId("tc-mini-timeline-slot")).toBeInTheDocument();
      const transport = makeTransport();
      rerender(
        withProviders(
          transport,
          <ThreadCanvas
            mode="focus"
            conversationId={CONV_ID}
            runId={RUN_ID}
            events={[]}
            onModeChange={() => {}}
            tabs={SAMPLE_TABS}
            activeUri="email://draft-1"
            onActivateTab={() => {}}
            onCloseTab={() => {}}
            transport={transport}
          />,
        ),
      );
      expect(screen.getByTestId("tc-mini-timeline-slot")).toBeInTheDocument();
    });

    it("renders the tabs strip in Studio and Focus modes", () => {
      renderCanvas({ mode: "studio" });
      expect(screen.getByTestId("tc-tabs")).toBeInTheDocument();
    });
  });

  describe("mode switching", () => {
    it("calls onModeChange when a tablist button is clicked", () => {
      const onModeChange = vi.fn();
      renderCanvas({ mode: "studio", onModeChange });
      fireEvent.click(screen.getByTestId("tc-mode-switcher-focus"));
      expect(onModeChange).toHaveBeenCalledWith("focus");
    });

    it("ArrowRight on the tablist advances to the next mode", () => {
      const onModeChange = vi.fn();
      renderCanvas({ mode: "studio", onModeChange });
      fireEvent.keyDown(screen.getByTestId("tc-mode-switcher"), {
        key: "ArrowRight",
      });
      expect(onModeChange).toHaveBeenCalledWith("focus");
    });

    it("ArrowLeft on the tablist wraps to the previous mode", () => {
      const onModeChange = vi.fn();
      renderCanvas({ mode: "studio", onModeChange });
      fireEvent.keyDown(screen.getByTestId("tc-mode-switcher"), {
        key: "ArrowLeft",
      });
      // studio → auto (wrap).
      expect(onModeChange).toHaveBeenCalledWith("auto");
    });
  });

  describe("Auto mode resolution", () => {
    it("resolves Auto to Studio when at least one surface has an active payload", () => {
      const events = [
        makeEnvelope("tool_result", {
          display_title: "Wrote a row",
          payload: { surface_uri: "sheet://acme", state: { rows: 5 } },
        }),
      ];
      renderCanvas({ mode: "auto", events });
      expect(screen.getByTestId("thread-canvas")).toHaveAttribute(
        "data-resolved-mode",
        "studio",
      );
      expect(screen.getByTestId("thread-canvas")).toHaveAttribute(
        "data-has-active-surfaces",
        "true",
      );
    });

    it("resolves Auto to Focus when no surfaces are active", () => {
      renderCanvas({ mode: "auto", events: [] });
      expect(screen.getByTestId("thread-canvas")).toHaveAttribute(
        "data-resolved-mode",
        "focus",
      );
      expect(screen.getByTestId("thread-canvas")).toHaveAttribute(
        "data-has-active-surfaces",
        "false",
      );
    });
  });

  describe("mount-once invariant (the critical correctness rule)", () => {
    it("does not remount TcSurfaceMount across mode switches", () => {
      // Strategy: TcSurfaceMount renders an element with a stable
      // data-testid. We grab the actual DOM node reference, switch mode
      // twice, then verify the *same* node reference is still there.
      // React preserves DOM nodes only when the underlying fiber
      // survives — i.e. no remount.
      const transport = makeTransport();
      const { rerender } = render(
        withProviders(
          transport,
          <ThreadCanvas
            mode="studio"
            conversationId={CONV_ID}
            runId={RUN_ID}
            events={[]}
            onModeChange={() => {}}
            tabs={SAMPLE_TABS}
            activeUri="email://draft-1"
            onActivateTab={() => {}}
            onCloseTab={() => {}}
            transport={transport}
          />,
        ),
      );
      const initialMount = screen.getByTestId("tc-surface-mount");

      rerender(
        withProviders(
          transport,
          <ThreadCanvas
            mode="focus"
            conversationId={CONV_ID}
            runId={RUN_ID}
            events={[]}
            onModeChange={() => {}}
            tabs={SAMPLE_TABS}
            activeUri="email://draft-1"
            onActivateTab={() => {}}
            onCloseTab={() => {}}
            transport={transport}
          />,
        ),
      );
      const afterFocus = screen.getByTestId("tc-surface-mount");
      expect(afterFocus).toBe(initialMount);

      rerender(
        withProviders(
          transport,
          <ThreadCanvas
            mode="studio"
            conversationId={CONV_ID}
            runId={RUN_ID}
            events={[]}
            onModeChange={() => {}}
            tabs={SAMPLE_TABS}
            activeUri="email://draft-1"
            onActivateTab={() => {}}
            onCloseTab={() => {}}
            transport={transport}
          />,
        ),
      );
      const afterStudio = screen.getByTestId("tc-surface-mount");
      expect(afterStudio).toBe(initialMount);
    });

    it("does not remount TcChat across mode switches", () => {
      const transport = makeTransport();
      const { rerender } = render(
        withProviders(
          transport,
          <ThreadCanvas
            mode="studio"
            conversationId={CONV_ID}
            runId={RUN_ID}
            events={[]}
            onModeChange={() => {}}
            tabs={SAMPLE_TABS}
            activeUri="email://draft-1"
            onActivateTab={() => {}}
            onCloseTab={() => {}}
            transport={transport}
          />,
        ),
      );
      const initialChat = screen.getByTestId("tc-chat");

      rerender(
        withProviders(
          transport,
          <ThreadCanvas
            mode="auto"
            conversationId={CONV_ID}
            runId={RUN_ID}
            events={[]}
            onModeChange={() => {}}
            tabs={SAMPLE_TABS}
            activeUri="email://draft-1"
            onActivateTab={() => {}}
            onCloseTab={() => {}}
            transport={transport}
          />,
        ),
      );
      const afterAuto = screen.getByTestId("tc-chat");
      expect(afterAuto).toBe(initialChat);

      rerender(
        withProviders(
          transport,
          <ThreadCanvas
            mode="focus"
            conversationId={CONV_ID}
            runId={RUN_ID}
            events={[]}
            onModeChange={() => {}}
            tabs={SAMPLE_TABS}
            activeUri="email://draft-1"
            onActivateTab={() => {}}
            onCloseTab={() => {}}
            transport={transport}
          />,
        ),
      );
      const afterFocus = screen.getByTestId("tc-chat");
      expect(afterFocus).toBe(initialChat);
    });

    it("does not remount the canvas root across N mode switches", () => {
      const transport = makeTransport();
      const { rerender } = render(
        withProviders(
          transport,
          <ThreadCanvas
            mode="studio"
            conversationId={CONV_ID}
            runId={RUN_ID}
            events={[]}
            onModeChange={() => {}}
            tabs={SAMPLE_TABS}
            activeUri="email://draft-1"
            onActivateTab={() => {}}
            onCloseTab={() => {}}
            transport={transport}
          />,
        ),
      );
      const initialRoot = screen.getByTestId("thread-canvas");

      const modes: readonly ThreadMode[] = [
        "focus",
        "auto",
        "studio",
        "focus",
        "studio",
      ];
      for (const m of modes) {
        rerender(
          withProviders(
            transport,
            <ThreadCanvas
              mode={m}
              conversationId={CONV_ID}
              runId={RUN_ID}
              events={[]}
              onModeChange={() => {}}
              tabs={SAMPLE_TABS}
              activeUri="email://draft-1"
              onActivateTab={() => {}}
              onCloseTab={() => {}}
              transport={transport}
            />,
          ),
        );
        expect(screen.getByTestId("thread-canvas")).toBe(initialRoot);
      }
    });
  });

  describe("event projection — one projector, multiple consumers", () => {
    it("forwards mini-timeline beads from the projected state", () => {
      const events = [
        makeEnvelope("tool_result", {
          display_title: "Wrote a row",
          payload: { surface_uri: "sheet://acme", state: { rows: 5 } },
        }),
        makeEnvelope("final_response", { display_title: "Done" }),
      ];
      renderCanvas({ events });
      // Each state-changing event becomes a bead → both should render.
      expect(
        screen.getByTestId("tc-mini-timeline-bead-evt-0"),
      ).toBeInTheDocument();
      expect(
        screen.getByTestId("tc-mini-timeline-bead-evt-1"),
      ).toBeInTheDocument();
    });

    it("calls onScrub when a bead is clicked", () => {
      const onScrub = vi.fn();
      const events = [
        makeEnvelope("tool_result", {
          display_title: "row",
          payload: { surface_uri: "sheet://x" },
        }),
      ];
      renderCanvas({ events, onScrub });
      fireEvent.click(screen.getByTestId("tc-mini-timeline-bead-evt-0"));
      expect(onScrub).toHaveBeenCalledWith(0);
    });

    it("snaps to live when the Now pill is clicked", () => {
      const onSnapToNow = vi.fn();
      const events = [
        makeEnvelope("tool_result", {
          display_title: "row",
          payload: { surface_uri: "sheet://x" },
        }),
      ];
      renderCanvas({ events, scrubbedSeq: 0, onSnapToNow });
      fireEvent.click(screen.getByTestId("tc-mini-timeline-now"));
      expect(onSnapToNow).toHaveBeenCalledTimes(1);
    });

    it("expand chevron switches mode back to Studio when in Focus", () => {
      const onModeChange = vi.fn();
      const events = [
        makeEnvelope("tool_result", {
          display_title: "row",
          payload: { surface_uri: "sheet://x" },
        }),
      ];
      renderCanvas({ mode: "focus", events, onModeChange });
      fireEvent.click(screen.getByTestId("tc-mini-timeline-expand"));
      expect(onModeChange).toHaveBeenCalledWith("studio");
    });

    it("does NOT render the expand chevron in Studio mode (only Focus)", () => {
      const events = [
        makeEnvelope("tool_result", {
          display_title: "row",
          payload: { surface_uri: "sheet://x" },
        }),
      ];
      renderCanvas({ mode: "studio", events });
      expect(
        screen.queryByTestId("tc-mini-timeline-expand"),
      ).not.toBeInTheDocument();
    });

    it("the projector reflects the most-recent surface payload (single source of truth)", () => {
      const events = [
        makeEnvelope("tool_result", {
          display_title: "row 1",
          payload: { surface_uri: "sheet://x", state: { rows: 1 } },
        }),
        makeEnvelope("tool_result", {
          display_title: "row 2",
          payload: { surface_uri: "sheet://x", state: { rows: 2 } },
        }),
      ];
      renderCanvas({ mode: "auto", events });
      // Auto resolves to Studio when surfaces are active.
      expect(screen.getByTestId("thread-canvas")).toHaveAttribute(
        "data-resolved-mode",
        "studio",
      );
      // Both beads project from the same events list (no duplicate
      // projection from a sibling consumer).
      expect(
        screen.getByTestId("tc-mini-timeline-bead-evt-0"),
      ).toBeInTheDocument();
      expect(
        screen.getByTestId("tc-mini-timeline-bead-evt-1"),
      ).toBeInTheDocument();
    });
  });

  describe("scrub forwarding", () => {
    it("reflects the scrubbedSeq cursor on the mini-timeline", () => {
      const events = [
        makeEnvelope("tool_result", {
          display_title: "row",
          payload: { surface_uri: "sheet://x" },
        }),
      ];
      renderCanvas({ events, scrubbedSeq: 0 });
      const bead = screen.getByTestId("tc-mini-timeline-bead-evt-0");
      expect(bead).toHaveAttribute("aria-pressed", "true");
    });

    it("renders the timeline in live state when scrubbedSeq is null", () => {
      const events = [
        makeEnvelope("tool_result", {
          display_title: "row",
          payload: { surface_uri: "sheet://x" },
        }),
      ];
      renderCanvas({ events, scrubbedSeq: null });
      expect(screen.getByTestId("tc-mini-timeline")).toHaveAttribute(
        "data-state",
        "live",
      );
    });
  });

  describe("backwards-compatible chrome", () => {
    it("renders the TcTabs strip with the provided tabs", () => {
      renderCanvas({ tabs: SAMPLE_TABS });
      expect(screen.getByText("Renewal email")).toBeInTheDocument();
      expect(screen.getByText("Acme — Closed Won")).toBeInTheDocument();
    });

    it("delegates tab activation to the parent", () => {
      const onActivate = vi.fn();
      const transport = makeTransport();
      render(
        withProviders(
          transport,
          <ThreadCanvas
            mode="studio"
            conversationId={CONV_ID}
            runId={RUN_ID}
            events={[]}
            onModeChange={() => {}}
            tabs={SAMPLE_TABS}
            activeUri="email://draft-1"
            onActivateTab={onActivate}
            onCloseTab={() => {}}
            transport={transport}
          />,
        ),
      );
      fireEvent.click(screen.getByText("Renewal email"));
      expect(onActivate).toHaveBeenCalledWith("email://draft-1");
    });

    it("forwards pendingDiff + approval callbacks to TcSurfaceMount", () => {
      const onApprove = vi.fn();
      const transport = makeTransport();
      render(
        withProviders(
          transport,
          <ThreadCanvas
            mode="studio"
            conversationId={CONV_ID}
            runId={RUN_ID}
            events={[]}
            onModeChange={() => {}}
            tabs={SAMPLE_TABS}
            activeUri="email://draft-1"
            onActivateTab={() => {}}
            onCloseTab={() => {}}
            transport={transport}
            pendingDiff={{
              diff: { field: "subject" },
              meta: {
                diffId: "d-1",
                provenance: "test",
                title: "Test diff",
                regionAnchorId: "anchor-1",
              },
            }}
            onApprove={onApprove}
          />,
        ),
      );
      fireEvent.click(screen.getByTestId("tc-surface-mount-approve"));
      expect(onApprove).toHaveBeenCalledTimes(1);
    });
  });
});
