import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EMAIL_FIXTURE } from "./email-fixture";
import { MockTransport, buildEmailEventSchedule } from "./MockTransport";

describe("MockTransport.request", () => {
  it("returns the email fixture for /drafts/draft-1", async () => {
    const transport = new MockTransport();
    const draft = await transport.request<{
      to: string;
      cc: string;
      subject: string;
      draftId: string;
      bodyPrefix: string;
      bodySuffix: string;
    }>({
      method: "GET",
      path: "/drafts/draft-1",
    });
    expect(draft.draftId).toBe(EMAIL_FIXTURE.draft.draftId);
    expect(draft.to).toBe(EMAIL_FIXTURE.draft.to);
    expect(draft.cc).toBe(EMAIL_FIXTURE.draft.cc);
    expect(draft.subject).toBe(EMAIL_FIXTURE.draft.subject);
  });

  it("rejects requests for unknown paths", async () => {
    const transport = new MockTransport();
    await expect(
      transport.request({ method: "GET", path: "/nope" }),
    ).rejects.toThrow();
  });
});

describe("MockTransport.subscribeServerSentEvents", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("emits the full event sequence in order", () => {
    const transport = new MockTransport();
    const received: string[] = [];
    transport.subscribeServerSentEvents({
      path: "/drafts/draft-1/events",
      onMessage: (raw) => received.push(raw),
    });
    vi.advanceTimersByTime(3500);
    expect(received.length).toBe(buildEmailEventSchedule().length);

    const parsed = received.map((r) => JSON.parse(r) as { type: string });
    const types = parsed.map((p) => p.type);
    expect(types[0]).toBe("tool_call_start");
    expect(types.slice(1, 6)).toEqual([
      "tool_call_chunk",
      "tool_call_chunk",
      "tool_call_chunk",
      "tool_call_chunk",
      "tool_call_chunk",
    ]);
    expect(types[6]).toBe("tool_call_end");
    expect(types[7]).toBe("pending_diff_appeared");
  });

  it("fires onOpen before any onMessage", () => {
    const transport = new MockTransport();
    const order: string[] = [];
    transport.subscribeServerSentEvents({
      path: "/drafts/draft-1/events",
      onOpen: () => order.push("open"),
      onMessage: () => order.push("message"),
    });
    vi.advanceTimersByTime(3500);
    expect(order[0]).toBe("open");
    expect(order.slice(1).every((s) => s === "message")).toBe(true);
  });

  it("close() cancels still-pending timers", () => {
    const transport = new MockTransport();
    const received: string[] = [];
    const sub = transport.subscribeServerSentEvents({
      path: "/drafts/draft-1/events",
      onMessage: (raw) => received.push(raw),
    });
    vi.advanceTimersByTime(500);
    // tool_call_start (0 ms) + tool_call_chunk @ 400 ms have fired by now.
    const beforeClose = received.length;
    expect(beforeClose).toBeGreaterThan(0);
    sub.close();
    vi.advanceTimersByTime(3000);
    expect(received.length).toBe(beforeClose);
  });

  it("emits the pending_diff payload with the fixture fields", () => {
    const transport = new MockTransport();
    const received: unknown[] = [];
    transport.subscribeServerSentEvents({
      path: "/drafts/draft-1/events",
      onMessage: (raw) => received.push(JSON.parse(raw)),
    });
    vi.advanceTimersByTime(3500);
    const last = received[received.length - 1] as {
      type: string;
      diffId: string;
      provenance: string;
      title: string;
      regionAnchorId: string;
    };
    expect(last.type).toBe("pending_diff_appeared");
    expect(last.diffId).toBe(EMAIL_FIXTURE.pendingDiff.diffId);
    expect(last.provenance).toBe(EMAIL_FIXTURE.pendingDiff.provenance);
    expect(last.title).toBe(EMAIL_FIXTURE.pendingDiff.title);
    expect(last.regionAnchorId).toBe(EMAIL_FIXTURE.pendingDiff.regionAnchorId);
  });

  it("throws for unknown event paths", () => {
    const transport = new MockTransport();
    expect(() =>
      transport.subscribeServerSentEvents({
        path: "/unknown/events",
        onMessage: () => {},
      }),
    ).toThrow();
  });
});

describe("MockTransport.getSession + capabilities", () => {
  it("returns a Session synchronously", () => {
    const transport = new MockTransport();
    expect(transport.getSession()).toEqual({ bearer: null });
  });

  it("returns TransportCapabilities synchronously", () => {
    const transport = new MockTransport();
    expect(transport.capabilities().substrate).toBe("web");
  });
});
