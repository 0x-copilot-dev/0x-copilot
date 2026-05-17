import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import type { Transport } from "@enterprise-search/chat-transport";

import { clearRegistry } from "../surfaces/SurfaceRegistry";
import { ThreadCanvas } from "./ThreadCanvas";
import type { TcTab } from "./TcTabs";

const stubTransport = {} as unknown as Transport;

const sampleTabs: readonly TcTab[] = [
  { uri: "email://draft-1", title: "Renewal email" },
  { uri: "sf-opp://acme/op-1", title: "Acme — Closed Won", pinned: true },
];

describe("ThreadCanvas", () => {
  let warnSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    clearRegistry();
    warnSpy.mockRestore();
    vi.restoreAllMocks();
  });

  it("renders the canvas grid with tabs, surface mount, chat slot and swimlanes slot", () => {
    render(
      <ThreadCanvas
        conversationId="conv-1"
        tabs={sampleTabs}
        activeUri="email://draft-1"
        onActivateTab={() => {}}
        onCloseTab={() => {}}
        transport={stubTransport}
      />,
    );
    expect(screen.getByTestId("thread-canvas")).toHaveAttribute(
      "data-conversation-id",
      "conv-1",
    );
    expect(screen.getByTestId("tc-tabs")).toBeInTheDocument();
    expect(screen.getByTestId("tc-chat-slot")).toBeInTheDocument();
    expect(screen.getByTestId("swimlanes-slot")).toBeInTheDocument();
    expect(screen.getByTestId("surface-placeholder")).toBeInTheDocument();
  });

  it("delegates tab activation and close to the parent", () => {
    const onActivate = vi.fn();
    const onClose = vi.fn();
    render(
      <ThreadCanvas
        conversationId="conv-1"
        tabs={sampleTabs}
        activeUri="email://draft-1"
        onActivateTab={onActivate}
        onCloseTab={onClose}
        transport={stubTransport}
      />,
    );
    fireEvent.click(screen.getByText("Renewal email"));
    expect(onActivate).toHaveBeenCalledWith("email://draft-1");
    fireEvent.click(screen.getByTestId("tc-tabs-close-email://draft-1"));
    expect(onClose).toHaveBeenCalledWith("email://draft-1");
  });

  it("passes pendingDiff and host action callbacks through to TcSurfaceMount", () => {
    const onApprove = vi.fn();
    render(
      <ThreadCanvas
        conversationId="conv-1"
        tabs={sampleTabs}
        activeUri="email://draft-1"
        onActivateTab={() => {}}
        onCloseTab={() => {}}
        transport={stubTransport}
        onApprove={onApprove}
        onReject={() => {}}
        pendingDiff={{ field: "subject" }}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-surface-mount-approve"));
    expect(onApprove).toHaveBeenCalledTimes(1);
  });

  it("hides approval chrome when there is no pending diff", () => {
    render(
      <ThreadCanvas
        conversationId="conv-1"
        tabs={sampleTabs}
        activeUri="email://draft-1"
        onActivateTab={() => {}}
        onCloseTab={() => {}}
        transport={stubTransport}
      />,
    );
    expect(
      screen.queryByTestId("tc-surface-mount-actions"),
    ).not.toBeInTheDocument();
  });
});
