// MemoryProposalToast + MemoryProposalToastStack tests (P12-B2).
//
// Covers: Accept / Reject / Snooze callbacks fire with the proposal id,
// stack collapse at >maxVisible (default 3), ARIA roles, empty stack
// renders nothing.

import type {
  ConversationId,
  MemoryProposal,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import type { ArtifactRoute, Router } from "../../routing/router";

// Pull the memory destination index so the `memory` ItemRef resolver
// registers (the toast's source ItemLink may render).
import "./index";

import {
  MemoryProposalToast,
  MemoryProposalToastStack,
} from "./MemoryProposalToast";

const asTenantId = (s: string): TenantId => s as unknown as TenantId;
const asUserId = (s: string): UserId => s as unknown as UserId;
const asConversationId = (s: string): ConversationId =>
  s as unknown as ConversationId;

function makeRouter(): Router<ArtifactRoute> {
  return {
    current: () => ({ kind: "workspace", workspaceId: "mem" }),
    navigate: vi.fn(),
    subscribe: () => () => {},
  };
}

function makeProposal(over: Partial<MemoryProposal> = {}): MemoryProposal {
  return {
    id: over.id ?? "prop_1",
    tenant_id: asTenantId("tnt_1"),
    user_id: asUserId("usr_self"),
    proposed_at: over.proposed_at ?? "2026-05-17T10:00:00.000Z",
    proposed_kind: over.proposed_kind ?? "preference",
    proposed_title: over.proposed_title ?? "Sign-off as 'Best, Parth'",
    proposed_body:
      over.proposed_body ??
      "Atlas noticed you always sign off with 'Best, Parth' — save as preference.",
    source: over.source ?? {
      kind: "chat",
      id: asConversationId("conv_42"),
    },
    status: over.status ?? "pending",
    decided_at: over.decided_at ?? null,
  };
}

type IdHandler = (id: string) => void;

function renderToast(props: {
  readonly proposal: MemoryProposal;
  readonly onAccept?: IdHandler;
  readonly onReject?: IdHandler;
  readonly onSnooze?: IdHandler;
}): void {
  render(
    <RouterProvider router={makeRouter()}>
      <MemoryProposalToast
        proposal={props.proposal}
        onAccept={props.onAccept ?? vi.fn()}
        onReject={props.onReject ?? vi.fn()}
        onSnooze={props.onSnooze ?? vi.fn()}
      />
    </RouterProvider>,
  );
}

describe("MemoryProposalToast — single toast", () => {
  it("renders title + kind chip + body excerpt", () => {
    renderToast({ proposal: makeProposal() });
    expect(screen.getByTestId("memory-proposal-toast")).toBeInTheDocument();
    expect(screen.getByTestId("memory-proposal-toast-title")).toHaveTextContent(
      /Sign-off/,
    );
    expect(
      screen.getByTestId("memory-proposal-toast-excerpt"),
    ).toHaveTextContent(/Atlas noticed/);
  });

  it("has role=status + aria-live=polite so AT users hear the toast", () => {
    renderToast({ proposal: makeProposal() });
    const toast = screen.getByTestId("memory-proposal-toast");
    expect(toast).toHaveAttribute("role", "status");
    expect(toast).toHaveAttribute("aria-live", "polite");
    expect(toast).toHaveAttribute("aria-label");
  });

  it("fires onAccept with the proposal id", () => {
    const onAccept = vi.fn();
    const proposal = makeProposal({ id: "prop_accept_me" });
    renderToast({ proposal, onAccept });
    fireEvent.click(screen.getByTestId("memory-proposal-toast-accept"));
    expect(onAccept).toHaveBeenCalledTimes(1);
    expect(onAccept).toHaveBeenCalledWith("prop_accept_me");
  });

  it("fires onReject with the proposal id", () => {
    const onReject = vi.fn();
    const proposal = makeProposal({ id: "prop_reject_me" });
    renderToast({ proposal, onReject });
    fireEvent.click(screen.getByTestId("memory-proposal-toast-reject"));
    expect(onReject).toHaveBeenCalledTimes(1);
    expect(onReject).toHaveBeenCalledWith("prop_reject_me");
  });

  it("fires onSnooze with the proposal id", () => {
    const onSnooze = vi.fn();
    const proposal = makeProposal({ id: "prop_snooze_me" });
    renderToast({ proposal, onSnooze });
    fireEvent.click(screen.getByTestId("memory-proposal-toast-snooze"));
    expect(onSnooze).toHaveBeenCalledTimes(1);
    expect(onSnooze).toHaveBeenCalledWith("prop_snooze_me");
  });

  it("clamps long bodies down to the excerpt limit", () => {
    const longBody = "lorem ipsum ".repeat(80);
    renderToast({ proposal: makeProposal({ proposed_body: longBody }) });
    const excerpt = screen.getByTestId("memory-proposal-toast-excerpt");
    expect(excerpt.textContent!.length).toBeLessThanOrEqual(161);
    expect(excerpt.textContent).toMatch(/…$/);
  });
});

// ===========================================================================
// Stack behaviour
// ===========================================================================

describe("MemoryProposalToastStack — stacking + collapse", () => {
  function renderStack(
    proposals: ReadonlyArray<MemoryProposal>,
    extra: {
      readonly maxVisible?: number;
      readonly onExpandStack?: () => void;
    } = {},
  ): void {
    render(
      <RouterProvider router={makeRouter()}>
        <MemoryProposalToastStack
          proposals={proposals}
          onAccept={vi.fn()}
          onReject={vi.fn()}
          onSnooze={vi.fn()}
          maxVisible={extra.maxVisible}
          onExpandStack={extra.onExpandStack}
        />
      </RouterProvider>,
    );
  }

  it("renders nothing when there are no proposals", () => {
    renderStack([]);
    expect(
      screen.queryByTestId("memory-proposal-toast-stack"),
    ).not.toBeInTheDocument();
  });

  it("renders all proposals when count <= maxVisible (default 3)", () => {
    renderStack([
      makeProposal({ id: "p1" }),
      makeProposal({ id: "p2" }),
      makeProposal({ id: "p3" }),
    ]);
    expect(screen.getAllByTestId("memory-proposal-toast")).toHaveLength(3);
    expect(
      screen.queryByTestId("memory-proposal-toast-overflow"),
    ).not.toBeInTheDocument();
  });

  it("collapses older toasts into a '+N more' pill when count > 3", () => {
    renderStack([
      makeProposal({ id: "p1" }),
      makeProposal({ id: "p2" }),
      makeProposal({ id: "p3" }),
      makeProposal({ id: "p4" }),
      makeProposal({ id: "p5" }),
    ]);
    expect(screen.getAllByTestId("memory-proposal-toast")).toHaveLength(3);
    const overflow = screen.getByTestId("memory-proposal-toast-overflow");
    expect(overflow).toHaveTextContent(/\+2 more/);
    expect(screen.getByTestId("memory-proposal-toast-stack")).toHaveAttribute(
      "data-count",
      "5",
    );
    expect(screen.getByTestId("memory-proposal-toast-stack")).toHaveAttribute(
      "data-overflow",
      "2",
    );
  });

  it("respects a custom maxVisible cap", () => {
    renderStack(
      [
        makeProposal({ id: "p1" }),
        makeProposal({ id: "p2" }),
        makeProposal({ id: "p3" }),
      ],
      { maxVisible: 1 },
    );
    expect(screen.getAllByTestId("memory-proposal-toast")).toHaveLength(1);
    expect(
      screen.getByTestId("memory-proposal-toast-overflow"),
    ).toHaveTextContent(/\+2 more/);
  });

  it("fires onExpandStack when the overflow pill is clicked", () => {
    const onExpandStack = vi.fn();
    renderStack(
      [
        makeProposal({ id: "p1" }),
        makeProposal({ id: "p2" }),
        makeProposal({ id: "p3" }),
        makeProposal({ id: "p4" }),
      ],
      { onExpandStack },
    );
    fireEvent.click(screen.getByTestId("memory-proposal-toast-overflow"));
    expect(onExpandStack).toHaveBeenCalledTimes(1);
  });
});
