import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, act } from "@testing-library/react";

import type { PendingDiff } from "@enterprise-search/chat-surface";
import {
  EMAIL_FIXTURE,
  MockTransport,
  type Transport,
} from "@enterprise-search/chat-transport";

import { EmailRenderer } from "./EmailRenderer";

const ACTIVE_DIFF: PendingDiff = {
  diffId: "diff-1",
  provenance: EMAIL_FIXTURE.pendingDiff.provenance,
  title: EMAIL_FIXTURE.pendingDiff.title,
  description: EMAIL_FIXTURE.pendingDiff.description,
  regionAnchorId: EMAIL_FIXTURE.pendingDiff.regionAnchorId,
};

function buildStaticTransport(): Transport {
  return {
    async request<TRes>(): Promise<TRes> {
      return EMAIL_FIXTURE.draft as unknown as TRes;
    },
    subscribeServerSentEvents() {
      return { close: () => {} };
    },
    getSession() {
      return { bearer: null };
    },
    capabilities() {
      return {
        substrate: "web",
        nativeSecretStorage: false,
        fileSystemAccess: false,
        clipboardWrite: false,
        openExternal: false,
      };
    },
  };
}

describe("EmailRenderer", () => {
  it("renders without crashing", () => {
    const transport = buildStaticTransport();
    render(<EmailRenderer uri="email://draft-1" transport={transport} />);
    expect(screen.getByTestId("email-renderer")).toBeInTheDocument();
  });

  it("populates To/Cc/Subject from the transport request fixture", async () => {
    const transport = buildStaticTransport();
    render(<EmailRenderer uri="email://draft-1" transport={transport} />);
    // request promise resolves synchronously after a microtask flush.
    await act(async () => {});
    expect(screen.getByTestId("email-to")).toHaveTextContent(
      EMAIL_FIXTURE.draft.to,
    );
    expect(screen.getByTestId("email-cc")).toHaveTextContent(
      EMAIL_FIXTURE.draft.cc,
    );
    expect(screen.getByTestId("email-subject")).toHaveTextContent(
      EMAIL_FIXTURE.draft.subject,
    );
  });

  it("renders the PENDING block anchor", () => {
    const transport = buildStaticTransport();
    render(<EmailRenderer uri="email://draft-1" transport={transport} />);
    expect(screen.getByTestId("pending-block")).toBeInTheDocument();
  });

  it("renders the overlay when activeDiff is passed in", async () => {
    const transport = buildStaticTransport();
    render(
      <EmailRenderer
        uri="email://draft-1"
        transport={transport}
        activeDiff={ACTIVE_DIFF}
        onApproveDiff={() => {}}
      />,
    );
    await act(async () => {});
    expect(screen.getByTestId("email-diff-overlay")).toBeInTheDocument();
  });

  it("calls onApproveDiff with the diff id when Approve is clicked", async () => {
    const onApproveDiff = vi.fn();
    const transport = buildStaticTransport();
    render(
      <EmailRenderer
        uri="email://draft-1"
        transport={transport}
        activeDiff={ACTIVE_DIFF}
        onApproveDiff={onApproveDiff}
      />,
    );
    await act(async () => {});
    fireEvent.click(screen.getByTestId("tc-inline-diff-approve"));
    expect(onApproveDiff).toHaveBeenCalledTimes(1);
    expect(onApproveDiff).toHaveBeenCalledWith(ACTIVE_DIFF.diffId);
  });

  it("calls onRejectDiff with the diff id when Reject is clicked", async () => {
    const onRejectDiff = vi.fn();
    const transport = buildStaticTransport();
    render(
      <EmailRenderer
        uri="email://draft-1"
        transport={transport}
        activeDiff={ACTIVE_DIFF}
        onRejectDiff={onRejectDiff}
      />,
    );
    await act(async () => {});
    fireEvent.click(screen.getByTestId("tc-inline-diff-reject"));
    expect(onRejectDiff).toHaveBeenCalledTimes(1);
    expect(onRejectDiff).toHaveBeenCalledWith(ACTIVE_DIFF.diffId);
  });
});

describe("EmailRenderer with MockTransport (end-to-end stream)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("accumulates body fragments and mounts the overlay on pending_diff_appeared", async () => {
    const transport = new MockTransport();
    render(<EmailRenderer uri="email://draft-1" transport={transport} />);
    // Resolve the initial request microtask before advancing timers.
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      vi.advanceTimersByTime(2800);
      await Promise.resolve();
    });
    // Body should contain the full streamed payload.
    const expected = EMAIL_FIXTURE.streamingBodyChunks.join("");
    expect(screen.getByTestId("pending-body").textContent).toBe(expected);
    expect(screen.getByTestId("email-diff-overlay")).toBeInTheDocument();
  });
});
