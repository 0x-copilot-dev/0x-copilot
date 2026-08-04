// The two behavioural flips that come with moving artifacts inline.
//
// Both are about NOT showing the same thing twice, and both are easy to break
// silently: the strip's suppression is a boolean nobody looks at, and
// follow-live's home changes depending on whether that strip is on screen.

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";

import type { ConversationId, RunId } from "@0x-copilot/api-types";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";

import { TransportProvider } from "../providers/TransportProvider";
import { ThreadCanvas, type ThreadMode } from "./ThreadCanvas";
import type { TcTab } from "./TcTabs";

const CONV_ID = "conv_1" as unknown as ConversationId;
const RUN_ID = "run_1" as unknown as RunId;
const TABS: readonly TcTab[] = [
  { uri: "artifact-dataset://art_1@1", title: "bookings-forecast.csv · r1" },
];

function makeTransport(): Transport {
  return {
    request: <TRes,>(_req: TypedRequest): Promise<TRes> =>
      Promise.resolve({ messages: [] } as unknown as TRes),
    subscribeServerSentEvents: (_o: SseSubscribeOptions): SseSubscription => ({
      close: () => {},
    }),
    getSession: (): Session => ({}) as Session,
    capabilities: (): TransportCapabilities => ({}) as TransportCapabilities,
  } as unknown as Transport;
}

function renderCanvas(args: {
  mode?: ThreadMode;
  hasInlineSubjects?: boolean;
  focusCards?: ReactNode;
  onFollowLive?: () => void;
}) {
  const transport = makeTransport();
  return render(
    <TransportProvider transport={transport}>
      <ThreadCanvas
        mode={args.mode ?? "focus"}
        conversationId={CONV_ID}
        runId={RUN_ID}
        events={[]}
        onModeChange={() => {}}
        tabs={TABS}
        activeUri={TABS[0]!.uri}
        onActivateTab={() => {}}
        onCloseTab={() => {}}
        transport={transport}
        scrubbedSeq={null}
        focusCards={args.focusCards}
        hasInlineSubjects={args.hasInlineSubjects}
        onFollowLive={args.onFollowLive}
      />
    </TransportProvider>,
  );
}

describe("ThreadCanvas — inline subjects", () => {
  it("suppresses the Focus tab strip when artifacts already render inline", () => {
    renderCanvas({ mode: "focus", hasInlineSubjects: true });
    // The transcript is already showing this artifact; a strip listing it again
    // is a second affordance for the same thing.
    expect(screen.queryByTestId("tc-tabs")).toBeNull();
  });

  it("still shows the Focus strip when nothing is inline", () => {
    renderCanvas({ mode: "focus", hasInlineSubjects: false });
    expect(screen.getByTestId("tc-tabs")).toBeTruthy();
  });

  it("never suppresses the Studio strip — it is Studio's only navigation", () => {
    renderCanvas({ mode: "studio", hasInlineSubjects: true });
    expect(screen.getByTestId("tc-tabs")).toBeTruthy();
  });

  // Follow-live must have exactly one home. The strip owns it whenever the
  // strip is on screen; the timeline picks it up only when the strip is gone,
  // which is precisely the case moving artifacts inline created.
  it("moves follow-live to the mini-timeline when the strip is suppressed", () => {
    const onFollowLive = vi.fn();
    renderCanvas({ mode: "focus", hasInlineSubjects: true, onFollowLive });

    expect(screen.queryByTestId("tc-tabs-follow-live")).toBeNull();
    expect(screen.getByTestId("tc-mini-timeline-follow-live")).toBeTruthy();
  });

  it("keeps follow-live on the strip — and only there — when the strip shows", () => {
    const onFollowLive = vi.fn();
    renderCanvas({ mode: "focus", hasInlineSubjects: false, onFollowLive });

    expect(screen.getByTestId("tc-tabs-follow-live")).toBeTruthy();
    // Two buttons for one action is how a user learns to distrust both.
    expect(screen.queryByTestId("tc-mini-timeline-follow-live")).toBeNull();
  });

  it("offers follow-live nowhere when the host reports no pin", () => {
    renderCanvas({ mode: "focus", hasInlineSubjects: true });
    expect(screen.queryByTestId("tc-mini-timeline-follow-live")).toBeNull();
    expect(screen.queryByTestId("tc-tabs-follow-live")).toBeNull();
  });
});
