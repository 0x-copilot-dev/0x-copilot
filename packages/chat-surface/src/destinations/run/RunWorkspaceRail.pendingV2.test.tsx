// RunWorkspaceRail — PRD-E2 `pendingV2` wiring tests. 🎨
//
// The `pendingV2` prop is additive + optional: absent ⇒ the rail is byte-
// identical to today (the pre-existing `RunWorkspaceRail.test.tsx` is the
// byte-identity proof); present ⇒ the Approvals panel leads with the cross-run
// `PendingCardList`, the Agents panel leads with the `AgentFleetList`, and the
// approvals badge count ADDS `cards.length`.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PendingAgentRow } from "@0x-copilot/api-types";

import type { PendingCard } from "./pendingCardsProjection";
import type { PendingWorkCardV2 } from "./pendingWorkV2Projection";
import { RunWorkspaceRail } from "./RunWorkspaceRail";

function chatSlot() {
  return <div data-testid="rail-chat-content">CHAT SURFACE</div>;
}

function gateCard(over: Partial<PendingCard> = {}): PendingCard {
  return {
    itemKind: "gate",
    runId: "run_other",
    gateId: "g_other",
    stageId: null,
    surfaceId: null,
    title: "to read ENG-9",
    connector: "linear",
    ledgerId: "rb00·001",
    openedSeq: 1,
    rowsPending: null,
    rowsTotal: null,
    ...over,
  };
}

function agentRow(over: Partial<PendingAgentRow> = {}): PendingAgentRow {
  return {
    v: 1,
    run_id: "run_other",
    conversation_id: "conv_other",
    conversation_title: "Other run",
    run_status: "waiting_for_approval",
    pending_count: 1,
    ...over,
  };
}

function pendingV2(over: Record<string, unknown> = {}) {
  return {
    cards: [gateCard()],
    agents: [agentRow()],
    onReview: vi.fn(),
    onOpenRun: vi.fn(),
    currentRunId: "run_open",
    ...over,
  };
}

function canonicalCard(
  over: Partial<PendingWorkCardV2> = {},
): PendingWorkCardV2 {
  return {
    runId: "run_other",
    subjectKind: "effect",
    subjectId: "stage_other",
    status: "held",
    openedSeq: 1,
    latestSeq: 1,
    ...over,
  };
}

function pendingWorkV21(over: Record<string, unknown> = {}) {
  return {
    cards: [canonicalCard()],
    loading: false,
    partial: false,
    stale: false,
    hasMore: false,
    onReview: vi.fn(),
    onLoadMore: vi.fn(),
    ...over,
  };
}

describe("RunWorkspaceRail pendingV2 (PRD-E2)", () => {
  it("absent: neither the pending queue nor the fleet list render", () => {
    render(<RunWorkspaceRail mode="studio" chatSlot={chatSlot()} />);
    fireEvent.click(screen.getByRole("tab", { name: /Approvals/ }));
    expect(screen.queryByTestId("pending-card-list")).toBeNull();
    expect(screen.queryByTestId("pending-work-v2-list")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: /Agents/ }));
    expect(screen.queryByTestId("agent-fleet-list")).toBeNull();
  });

  it("Focus stays compact: canonical cards never mount in its Run-details rail", () => {
    render(
      <RunWorkspaceRail
        mode="focus"
        chatSlot={chatSlot()}
        pendingWorkV21={pendingWorkV21()}
      />,
    );
    expect(screen.queryByTestId("pending-work-v2-list")).toBeNull();
    expect(screen.getByTestId("tc-focus-panel")).toBeInTheDocument();
  });

  it("present: the Agents panel leads with the AgentFleetList", () => {
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        pendingV2={pendingV2()}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: /Agents/ }));
    expect(screen.getByTestId("agent-fleet-list")).toBeInTheDocument();
  });

  it("hides AgentFleetList when pendingV2 has no cross-run agents", () => {
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        pendingV2={{
          ...pendingV2(),
          cards: [],
          agents: [],
        }}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: /Agents/ }));
    expect(screen.queryByTestId("agent-fleet-list")).toBeNull();
    expect(screen.queryByTestId("agent-fleet-empty")).toBeNull();
    expect(screen.getByText(/Subagents run here/i)).toBeInTheDocument();
  });

  // PRD-E2's cross-run queue is GONE from this panel — both the legacy
  // `PendingCardList` and the canonical `PendingWorkV2List`.
  //
  // It answered "what is parked ANYWHERE?" from inside a surface scoped to one
  // conversation. That is why its cards had to render above a "nothing pending
  // in this conversation" empty state, and why the header counter needed the
  // word "elsewhere" to stay honest: the placement was wrong, not the wording.
  // A global count belongs on the nav rail's Chats badge.
  it("the Approvals panel shows THIS conversation only, never cross-run work", () => {
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        defaultTab="approvals"
        pendingV2={pendingV2({ cards: [gateCard(), gateCard()] })}
      />,
    );

    expect(screen.queryByTestId("pending-card-list")).toBeNull();
    expect(screen.queryByTestId("pending-work-v2-list")).toBeNull();
  });

  it("the approvals badge counts this conversation only", () => {
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        pendingV2={pendingV2({ cards: [gateCard(), gateCard()] })}
        approvalsQueue={{ pending: [], recent: [] }}
      />,
    );

    const tab = screen.getByRole("tab", { name: /approvals/i });
    expect(tab.textContent).not.toContain("2");
  });
});
