import { createElement, type ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import type { Transport } from "@0x-copilot/chat-transport";

import {
  TIER3_SCHEME,
  type SaaSRendererAdapter,
} from "../surfaces/SaaSRendererAdapter";
import { clearRegistry, registerAdapter } from "../surfaces/SurfaceRegistry";
import type { PendingDiff } from "../surfaces/types";
import {
  TcSurfaceMount,
  __setRenderBudgetClockForTests,
  reduceTo,
  type PendingDiffHandle,
} from "./TcSurfaceMount";
import { TcTabs } from "./TcTabs";
import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

const stubTransport = {} as unknown as Transport;

function adapterRenderingText(
  text: string,
  diffText: string = "diff content",
): SaaSRendererAdapter {
  return {
    scheme: "email",
    matches: () => true,
    renderCurrent: (): ReactElement => createElement("div", null, text),
    renderDiff: (): ReactElement => createElement("div", null, diffText),
    metadata: { origin: "first-party", schemaVersion: 1 },
  };
}

function adapterThatThrows(message: string): SaaSRendererAdapter {
  return {
    scheme: "email",
    matches: () => true,
    renderCurrent: (): ReactElement => {
      throw new Error(message);
    },
    renderDiff: (): ReactElement => {
      throw new Error(message);
    },
    metadata: { origin: "first-party", schemaVersion: 1 },
  };
}

function tier3RenderingText(text: string): SaaSRendererAdapter {
  return {
    scheme: TIER3_SCHEME,
    matches: () => true,
    renderCurrent: (): ReactElement => createElement("div", null, text),
    renderDiff: (): ReactElement => createElement("div", null, `${text}-diff`),
    metadata: { origin: "first-party", schemaVersion: 1 },
  };
}

const pendingDiffMeta = (diffId: string): PendingDiff => ({
  diffId,
  provenance: "test",
  title: "Test diff",
  regionAnchorId: "anchor-1",
});

const pendingHandle = (
  diffId: string,
  diff: unknown = { id: diffId },
): PendingDiffHandle => ({
  diff,
  meta: pendingDiffMeta(diffId),
});

describe("TcSurfaceMount", () => {
  let warnSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    clearRegistry();
    warnSpy.mockRestore();
    vi.restoreAllMocks();
    __setRenderBudgetClockForTests(null);
  });

  it("renders the null-state fallback when no adapter is registered", () => {
    render(<TcSurfaceMount uri="email://draft-1" transport={stubTransport} />);
    const fallback = screen.getByTestId("surface-placeholder");
    expect(fallback).toBeInTheDocument();
    expect(fallback).toHaveTextContent(/no adapter registered for email/i);
  });

  it("uses the honest record surface rather than an adapter error for a record tab", () => {
    render(
      <TcSurfaceMount
        uri="record://linear/eng-142"
        title="ENG-142"
        transport={stubTransport}
      />,
    );

    const record = screen.getByTestId("surface-placeholder");
    expect(record).toHaveAttribute("data-record-state", "hydrating");
    expect(record).toHaveTextContent("Connected record");
    expect(
      screen.getByTestId("surface-record-fallback-title"),
    ).toHaveTextContent("ENG-142");
    expect(record).not.toHaveTextContent(/no adapter registered/i);
  });

  it("includes the scheme name in the fallback when URI is malformed", () => {
    render(<TcSurfaceMount uri="not-a-uri" transport={stubTransport} />);
    const fallback = screen.getByTestId("surface-placeholder");
    expect(fallback).toHaveTextContent(/unknown scheme/i);
  });

  it("shows the human empty state when no surface tab is active", () => {
    render(<TcSurfaceMount uri="" transport={stubTransport} />);
    // Quiet "waiting" copy — never the tier-3 jargon card or the dashed
    // placeholder, both of which read as errors.
    expect(screen.getByTestId("surface-empty-state")).toBeInTheDocument();
    expect(screen.queryByTestId("surface-placeholder")).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("generic-structured-diff"),
    ).not.toBeInTheDocument();
  });

  it("renders the adapter's renderCurrent output when one is registered", () => {
    registerAdapter(adapterRenderingText("hello from email adapter"));
    render(<TcSurfaceMount uri="email://draft-1" transport={stubTransport} />);
    expect(screen.getByText("hello from email adapter")).toBeInTheDocument();
  });

  it("renders renderDiff output when pendingDiff is supplied", () => {
    registerAdapter(adapterRenderingText("current state", "diff payload"));
    render(
      <TcSurfaceMount
        uri="email://draft-1"
        transport={stubTransport}
        pendingDiff={pendingHandle("d-1")}
      />,
    );
    expect(screen.getByText("diff payload")).toBeInTheDocument();
    expect(screen.queryByText("current state")).not.toBeInTheDocument();
  });

  it("falls back to the placeholder when adapter throws and no tier-3 registered", () => {
    registerAdapter(adapterThatThrows("boom"));
    render(<TcSurfaceMount uri="email://draft-1" transport={stubTransport} />);
    expect(screen.getByTestId("surface-placeholder")).toBeInTheDocument();
    expect(warnSpy).toHaveBeenCalled();
  });

  it("falls back to tier-3 when adapter throws and tier-3 is registered (D29)", () => {
    registerAdapter(adapterThatThrows("boom"));
    registerAdapter(tier3RenderingText("tier-3 rendered"));
    render(<TcSurfaceMount uri="email://draft-1" transport={stubTransport} />);
    expect(screen.getByText("tier-3 rendered")).toBeInTheDocument();
    expect(screen.queryByTestId("surface-placeholder")).not.toBeInTheDocument();
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringMatching(/threw during render/),
    );
  });

  it("falls back to tier-3 when adapter exceeds the render budget", () => {
    const ticks = [0, 150];
    __setRenderBudgetClockForTests(() => ticks.shift() ?? 0);
    registerAdapter(adapterRenderingText("slow content"));
    registerAdapter(tier3RenderingText("tier-3 saved us"));
    render(<TcSurfaceMount uri="email://draft-1" transport={stubTransport} />);
    expect(screen.getByText("tier-3 saved us")).toBeInTheDocument();
    expect(screen.queryByText("slow content")).not.toBeInTheDocument();
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringMatching(/exceeded 100ms render budget/),
    );
  });

  it("falls back to placeholder when adapter exceeds budget and no tier-3 registered", () => {
    const ticks = [0, 150];
    __setRenderBudgetClockForTests(() => ticks.shift() ?? 0);
    registerAdapter(adapterRenderingText("slow content"));
    render(<TcSurfaceMount uri="email://draft-1" transport={stubTransport} />);
    expect(screen.getByTestId("surface-placeholder")).toBeInTheDocument();
    expect(screen.queryByText("slow content")).not.toBeInTheDocument();
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringMatching(/exceeded 100ms render budget/),
    );
  });

  it("falls back to placeholder when tier-3 also throws", () => {
    registerAdapter(adapterThatThrows("primary boom"));
    const brokenTier3: SaaSRendererAdapter = {
      scheme: TIER3_SCHEME,
      matches: () => true,
      renderCurrent: (): ReactElement => {
        throw new Error("tier-3 boom");
      },
      renderDiff: (): ReactElement => {
        throw new Error("tier-3 boom");
      },
      metadata: { origin: "first-party", schemaVersion: 1 },
    };
    registerAdapter(brokenTier3);
    render(<TcSurfaceMount uri="email://draft-1" transport={stubTransport} />);
    expect(screen.getByTestId("surface-placeholder")).toBeInTheDocument();
  });

  it("does not render host controls when pendingDiff is absent", () => {
    registerAdapter(adapterRenderingText("just current"));
    render(<TcSurfaceMount uri="email://draft-1" transport={stubTransport} />);
    expect(
      screen.queryByTestId("tc-surface-mount-controls"),
    ).not.toBeInTheDocument();
  });

  it("renders Approve / Reject / Suggest changes controls around the adapter output when pendingDiff is present", () => {
    registerAdapter(adapterRenderingText("current", "the diff"));
    render(
      <TcSurfaceMount
        uri="email://draft-1"
        transport={stubTransport}
        pendingDiff={pendingHandle("d-7")}
      />,
    );
    expect(screen.getByText("the diff")).toBeInTheDocument();
    expect(screen.getByTestId("tc-surface-mount-controls")).toBeInTheDocument();
    expect(screen.getByTestId("tc-surface-mount-approve")).toHaveTextContent(
      "Approve",
    );
    expect(screen.getByTestId("tc-surface-mount-reject")).toHaveTextContent(
      "Reject",
    );
    expect(screen.getByTestId("tc-surface-mount-suggest")).toHaveTextContent(
      "Suggest changes",
    );
  });

  it("fires Approve / Reject / Suggest handlers with the diffId", () => {
    const onApprove = vi.fn();
    const onReject = vi.fn();
    const onSuggestChanges = vi.fn();
    registerAdapter(adapterRenderingText("current"));
    render(
      <TcSurfaceMount
        uri="email://draft-1"
        transport={stubTransport}
        pendingDiff={pendingHandle("d-42")}
        onApprove={onApprove}
        onReject={onReject}
        onSuggestChanges={onSuggestChanges}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-surface-mount-approve"));
    fireEvent.click(screen.getByTestId("tc-surface-mount-reject"));
    fireEvent.click(screen.getByTestId("tc-surface-mount-suggest"));
    expect(onApprove).toHaveBeenCalledWith("d-42");
    expect(onReject).toHaveBeenCalledWith("d-42");
    expect(onSuggestChanges).toHaveBeenCalledWith("d-42");
  });

  it("FR-3.20: renders a `streaming · N%` chip when the pending diff is streaming", () => {
    registerAdapter(adapterRenderingText("current", "the diff"));
    render(
      <TcSurfaceMount
        uri="email://draft-1"
        transport={stubTransport}
        pendingDiff={{ ...pendingHandle("d-stream"), streamProgress: 37 }}
      />,
    );
    expect(
      screen.getByTestId("tc-surface-mount-stream-chip"),
    ).toHaveTextContent(/streaming · 37%/i);
    expect(screen.getByTestId("tc-surface-mount")).toHaveAttribute(
      "data-streaming",
      "true",
    );
    // The diff is still the center-pane render (not chat text).
    expect(screen.getByText("the diff")).toBeInTheDocument();
  });

  it("rounds and clamps the streaming progress on the mount chip", () => {
    registerAdapter(adapterRenderingText("current", "the diff"));
    render(
      <TcSurfaceMount
        uri="email://draft-1"
        transport={stubTransport}
        pendingDiff={{ ...pendingHandle("d-stream"), streamProgress: 149.5 }}
      />,
    );
    expect(
      screen.getByTestId("tc-surface-mount-stream-chip"),
    ).toHaveTextContent(/streaming · 100%/i);
  });

  it("omits the streaming chip when the pending diff carries no progress", () => {
    registerAdapter(adapterRenderingText("current", "the diff"));
    render(
      <TcSurfaceMount
        uri="email://draft-1"
        transport={stubTransport}
        pendingDiff={pendingHandle("d-static")}
      />,
    );
    expect(
      screen.queryByTestId("tc-surface-mount-stream-chip"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("tc-surface-mount")).toHaveAttribute(
      "data-streaming",
      "false",
    );
  });

  it("wraps the tier-3 fallback path with the host controls too (D28)", () => {
    registerAdapter(adapterThatThrows("primary boom"));
    registerAdapter(tier3RenderingText("tier-3 here"));
    render(
      <TcSurfaceMount
        uri="email://draft-1"
        transport={stubTransport}
        pendingDiff={pendingHandle("d-99")}
      />,
    );
    expect(screen.getByText("tier-3 here-diff")).toBeInTheDocument();
    expect(screen.getByTestId("tc-surface-mount-controls")).toBeInTheDocument();
  });

  it("forwards state to the adapter renderCurrent call", () => {
    const captureState = vi.fn(
      (s: unknown): ReactElement =>
        createElement("div", null, JSON.stringify(s)),
    );
    const adapter: SaaSRendererAdapter = {
      scheme: "email",
      matches: () => true,
      renderCurrent: captureState,
      renderDiff: (): ReactElement => createElement("div", null, "diff"),
      metadata: { origin: "first-party", schemaVersion: 1 },
    };
    registerAdapter(adapter);
    render(
      <TcSurfaceMount
        uri="email://draft-1"
        transport={stubTransport}
        state={{ id: "draft-1", subject: "hi" }}
      />,
    );
    expect(captureState).toHaveBeenCalledWith({ id: "draft-1", subject: "hi" });
  });

  it("exposes data-tier on the mount root for diagnostics", () => {
    registerAdapter(adapterRenderingText("ok"));
    render(<TcSurfaceMount uri="email://draft-1" transport={stubTransport} />);
    expect(screen.getByTestId("tc-surface-mount")).toHaveAttribute(
      "data-tier",
      "primary",
    );
  });

  it("data-tier reports tier3 when primary fails and tier-3 served the render", () => {
    registerAdapter(adapterThatThrows("boom"));
    registerAdapter(tier3RenderingText("tier-3"));
    render(<TcSurfaceMount uri="email://draft-1" transport={stubTransport} />);
    expect(screen.getByTestId("tc-surface-mount")).toHaveAttribute(
      "data-tier",
      "tier3",
    );
  });

  // The identity register. A surface says WHICH SOURCE it is; the tab strip and
  // the card are that one statement shown in two places, so the tests below
  // assert the two AGREE rather than that a prop was handed over.
  describe("source hue", () => {
    const ARTIFACT_URI = "artifact-dataset://art_1@1";

    /**
     * Render the tab strip and the mount from the SAME surface and the SAME
     * chosen accent — the arrangement a user actually sees — and report what
     * each one ended up claiming.
     */
    function renderTabAndMount(
      uri: string,
      chosen?: string,
    ): { readonly tab: string | null; readonly mount: string | null } {
      const choice = chosen === undefined ? {} : { hue: chosen };
      render(
        <>
          <TcTabs
            tabs={[{ uri, title: "forecast", ...choice }]}
            activeUri={uri}
            onActivate={() => {}}
            onClose={() => {}}
          />
          <TcSurfaceMount uri={uri} transport={stubTransport} {...choice} />
        </>,
      );
      return {
        tab: screen.getByRole("tab").getAttribute("data-surface-hue"),
        mount: screen
          .getByTestId("tc-surface-mount")
          .getAttribute("data-surface-hue"),
      };
    }

    it("carries the author's chosen hue to the card, not just the tab", () => {
      const { tab, mount } = renderTabAndMount(ARTIFACT_URI, "ember");
      // Both halves state the choice. Equality alone would also hold if both
      // had dropped it, so the resolved value is pinned too.
      expect(mount).toBe(tab);
      expect(mount).toBe("ember");
    });

    it("derives the same hue from the URI on both when nothing was chosen", () => {
      const { tab, mount } = renderTabAndMount(ARTIFACT_URI);
      expect(mount).toBe(tab);
      expect(mount).toBe("sky");
    });

    // The choice arrives from a model argument, so "malformed" is a reachable
    // case, not a hypothetical. Both halves must degrade the same way — to the
    // kind's own hue, never to a blank identity and never to the raw string.
    it("degrades a malformed choice identically on both", () => {
      const hostile = "ember; background: url(x)";
      const { tab, mount } = renderTabAndMount(ARTIFACT_URI, hostile);
      expect(mount).toBe(tab);
      expect(mount).toBe("sky");
      expect(mount).not.toBe(hostile);
    });

    it("honours an explicit none on both — a chosen absence is still a choice", () => {
      const { tab, mount } = renderTabAndMount("table://safe/batch", "none");
      expect(mount).toBe(tab);
      expect(mount).toBe("none");
    });

    // Everything that renders today passes no hue at all; that path must be
    // byte-identical to what shipped before the prop existed.
    it.each([
      ["table://safe/batch", "jade"],
      ["board://linear/cycle/14", "plum"],
      ["record://salesforce/opportunity/006Ab", "indigo"],
      ["incident://pagerduty/4127", "none"],
      ["not-a-uri", "none"],
    ])("keeps the URI-derived hue for %s with no choice", (uri, expected) => {
      const { tab, mount } = renderTabAndMount(uri);
      expect(mount).toBe(tab);
      expect(mount).toBe(expected);
    });

    // The hue says which SOURCE a surface is, which does not change with which
    // renderer painted it. The cases above all land on the placeholder (no
    // adapter is registered); this pins the degraded render too.
    it("keeps the chosen hue when the render degrades to tier-3", () => {
      registerAdapter(adapterThatThrows("boom"));
      registerAdapter(tier3RenderingText("tier-3 rendered"));
      render(
        <TcSurfaceMount
          uri="email://draft-1"
          transport={stubTransport}
          hue="plum"
        />,
      );
      expect(screen.getByText("tier-3 rendered")).toBeInTheDocument();
      const mount = screen.getByTestId("tc-surface-mount");
      expect(mount).toHaveAttribute("data-tier", "tier3");
      expect(mount).toHaveAttribute("data-surface-hue", "plum");
    });
  });
});

function makeToolResultEvent(
  sequenceNo: number,
  uri: string,
  state: Record<string, unknown>,
): RuntimeEventEnvelope {
  return {
    event_id: `evt-${sequenceNo}`,
    run_id: "run-1",
    conversation_id: "conv-1",
    sequence_no: sequenceNo,
    event_type: "tool_result",
    activity_kind: "tool",
    payload: { surface_uri: uri, state },
    created_at: new Date(1_700_000_000_000 + sequenceNo * 1000).toISOString(),
  };
}

describe("TcSurfaceMount.reduceTo (client-side time-travel)", () => {
  it("returns the surface payload at the given sequence_no", () => {
    const events = [
      makeToolResultEvent(0, "sheet://acme", { rows: 1 }),
      makeToolResultEvent(1, "sheet://acme", { rows: 2 }),
      makeToolResultEvent(2, "sheet://acme", { rows: 3 }),
    ];
    expect(reduceTo(events, 0, "sheet://acme")).toEqual({ rows: 1 });
    expect(reduceTo(events, 1, "sheet://acme")).toEqual({ rows: 2 });
    expect(reduceTo(events, 2, "sheet://acme")).toEqual({ rows: 3 });
  });

  it("ignores events past the cursor (time-travel)", () => {
    const events = [
      makeToolResultEvent(0, "sheet://acme", { rows: 1, columns: 1 }),
      makeToolResultEvent(5, "sheet://acme", { rows: 10 }),
    ];
    expect(reduceTo(events, 0, "sheet://acme")).toEqual({
      rows: 1,
      columns: 1,
    });
    expect(reduceTo(events, 5, "sheet://acme")).toEqual({
      rows: 10,
      columns: 1,
    });
  });

  it("returns undefined for an unknown surface URI", () => {
    const events = [makeToolResultEvent(0, "sheet://acme", { rows: 1 })];
    expect(reduceTo(events, 0, "sheet://other")).toBeUndefined();
  });

  it("returns undefined when no event has been observed before the cursor", () => {
    const events = [makeToolResultEvent(5, "sheet://acme", { rows: 1 })];
    expect(reduceTo(events, 2, "sheet://acme")).toBeUndefined();
  });

  it("replay of the same events produces the same state (idempotent)", () => {
    const events = [
      makeToolResultEvent(0, "sheet://acme", { rows: 1 }),
      makeToolResultEvent(1, "sheet://acme", { rows: 2 }),
    ];
    const a = reduceTo(events, 1, "sheet://acme");
    const b = reduceTo(events, 1, "sheet://acme");
    expect(a).toEqual(b);
  });
});
