import { createElement, type ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import type { Transport } from "@enterprise-search/chat-transport";

import { type SaaSRendererAdapter } from "../surfaces/SaaSRendererAdapter";
import { clearRegistry, registerAdapter } from "../surfaces/SurfaceRegistry";
import {
  TcSurfaceMount,
  __setRenderBudgetClockForTests,
} from "./TcSurfaceMount";

const stubTransport = {} as unknown as Transport;

function adapterRenderingText(text: string): SaaSRendererAdapter {
  return {
    scheme: "email",
    matches: () => true,
    renderCurrent: (): ReactElement => createElement("div", null, text),
    renderDiff: (): ReactElement => createElement("div", null, "diff"),
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
    renderDiff: (): ReactElement => createElement("div", null, "diff"),
    metadata: { origin: "first-party", schemaVersion: 1 },
  };
}

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

  it("renders the placeholder when no adapter is registered", () => {
    render(<TcSurfaceMount uri="email://draft-1" transport={stubTransport} />);
    const placeholder = screen.getByTestId("surface-placeholder");
    expect(placeholder).toBeInTheDocument();
    expect(placeholder).toHaveTextContent(/no renderer registered for email/i);
  });

  it("includes the scheme name in the placeholder when URI is malformed", () => {
    render(<TcSurfaceMount uri="not-a-uri" transport={stubTransport} />);
    const placeholder = screen.getByTestId("surface-placeholder");
    expect(placeholder).toHaveTextContent(/unknown scheme/i);
  });

  it("renders the adapter's renderCurrent output when one is registered", () => {
    registerAdapter(adapterRenderingText("hello from email adapter"));
    render(<TcSurfaceMount uri="email://draft-1" transport={stubTransport} />);
    expect(screen.getByText("hello from email adapter")).toBeInTheDocument();
  });

  it("falls back when adapter.renderCurrent throws synchronously", () => {
    registerAdapter(adapterThatThrows("boom"));
    render(<TcSurfaceMount uri="email://draft-1" transport={stubTransport} />);
    expect(screen.getByTestId("surface-placeholder")).toBeInTheDocument();
    expect(warnSpy).toHaveBeenCalled();
  });

  it("falls back when adapter.renderCurrent exceeds the render budget", () => {
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

  it("does not render Approve/Reject controls when no pendingDiff", () => {
    registerAdapter(adapterRenderingText("current"));
    render(<TcSurfaceMount uri="email://draft-1" transport={stubTransport} />);
    expect(
      screen.queryByTestId("tc-surface-mount-actions"),
    ).not.toBeInTheDocument();
  });

  it("renders Approve/Reject controls outside the adapter output when pendingDiff is non-null", () => {
    registerAdapter(adapterRenderingText("current"));
    const onApprove = vi.fn();
    const onReject = vi.fn();
    render(
      <TcSurfaceMount
        uri="email://draft-1"
        transport={stubTransport}
        pendingDiff={{ field: "subject" }}
        onApprove={onApprove}
        onReject={onReject}
      />,
    );
    expect(screen.getByTestId("tc-surface-mount-actions")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("tc-surface-mount-approve"));
    fireEvent.click(screen.getByTestId("tc-surface-mount-reject"));
    expect(onApprove).toHaveBeenCalledTimes(1);
    expect(onReject).toHaveBeenCalledTimes(1);
  });

  it("renders Approve/Reject controls over the placeholder when no adapter but pendingDiff present", () => {
    render(
      <TcSurfaceMount
        uri="unknown://x"
        transport={stubTransport}
        pendingDiff={{ field: "subject" }}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );
    expect(screen.getByTestId("surface-placeholder")).toBeInTheDocument();
    expect(screen.getByTestId("tc-surface-mount-actions")).toBeInTheDocument();
  });
});
