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

  it("includes the scheme name in the fallback when URI is malformed", () => {
    render(<TcSurfaceMount uri="not-a-uri" transport={stubTransport} />);
    const fallback = screen.getByTestId("surface-placeholder");
    expect(fallback).toHaveTextContent(/unknown scheme/i);
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
