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

describe("RunWorkspaceRail pendingV2 (PRD-E2)", () => {
  it("absent: neither the pending queue nor the fleet list render", () => {
    render(<RunWorkspaceRail mode="studio" chatSlot={chatSlot()} />);
    fireEvent.click(screen.getByRole("tab", { name: /Approvals/ }));
    expect(screen.queryByTestId("pending-card-list")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: /Agents/ }));
    expect(screen.queryByTestId("agent-fleet-list")).toBeNull();
  });

  it("present: the Approvals panel leads with the cross-run PendingCardList", () => {
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        pendingV2={pendingV2()}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: /Approvals/ }));
    expect(screen.getByTestId("pending-card-list")).toBeInTheDocument();
    expect(screen.getByTestId("pending-card-review")).toBeInTheDocument();
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

  it("the approvals badge count adds the cross-run cards", () => {
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        pendingV2={pendingV2({
          cards: [gateCard({ gateId: "a" }), gateCard({ gateId: "b" })],
        })}
      />,
    );
    // No v1 in-chat approvals + 2 cross-run cards → badge reads "2".
    expect(screen.getByTestId("run-rail-approvals-badge").textContent).toBe(
      "2",
    );
  });
});
