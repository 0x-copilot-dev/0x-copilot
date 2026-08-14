// The once/always choice, where it actually lands.
//
// The backend has emitted `grant_options` since before this program and the
// only reference in the whole app tree was a strip list — a control the user
// could never reach, on a decision the runtime was already offering. These are
// the check that binding it did not quietly create a THIRD thing: the ask card
// must still be the ask card, "once" must still be one click, and the durable
// arm must be reachable only where the folder it attaches is legible.
//
// The companion file `TcChat.workspaceGrant.test.tsx` covers the other block —
// `workspace_grant`, which REPLACES this card with a folder ask. The two are
// deliberately separate payload keys and separate cards; a test that let them
// blur would let a durable grant be handed over by a control the user read as
// "just this once".

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";

import {
  EMPTY_CONNECTOR_TRUST,
  type WorkspaceGrantRequest,
} from "../approvals";
import { TransportProvider } from "../providers/TransportProvider";
import { TcChat, type TcChatApproval, type TcChatMode } from "./TcChat";

function makeTransport(): Transport {
  return {
    request: <TRes,>(_req: TypedRequest): Promise<TRes> =>
      Promise.resolve({ messages: [] }) as Promise<TRes>,
    subscribeServerSentEvents: (
      _opts: SseSubscribeOptions,
    ): SseSubscription => ({ close: () => {} }),
    getSession: (): Session => ({ bearer: null }),
    capabilities: (): TransportCapabilities => ({
      substrate: "web",
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
  };
}

// The folder `allow_always` would attach — `payload.grant_scope`, which for a
// FILE-shaped call is the file's immediate container and never one level above
// it. The producer derives it; nothing here may widen it.
const REPORTS: WorkspaceGrantRequest = {
  path: "/Users/parthpahwa/Documents/reports",
  folderName: "reports",
  mode: "read_only",
  reason: "to summarise the quarter",
};

// A `filesystem_access` READ ask. It carries no `workspace_grant` block, so it
// routes to the ordinary Approve/Decline card — which is the point: this is an
// ask that can be answered just this once, that ALSO offers a durable option.
function readAsk(overrides: Partial<TcChatApproval> = {}): TcChatApproval {
  return {
    approvalId: "int-1:0",
    title: "Read q3.csv",
    reason: "Copilot wants to read a file it has not been given.",
    summary: null,
    approvalKind: "tool_action",
    serverId: null,
    category: null,
    params: [{ label: "path", value: "/Users/parthpahwa/Documents/reports" }],
    presentation: null,
    connectorTrust: EMPTY_CONNECTOR_TRUST,
    question: null,
    workspaceGrant: null,
    grantAlways: REPORTS,
    resolved: false,
    decision: null,
    createdAtMs: 1716000090000,
    ...overrides,
  };
}

function renderChat(
  mode: TcChatMode,
  props: Partial<React.ComponentProps<typeof TcChat>> = {},
): void {
  render(
    <TransportProvider transport={makeTransport()}>
      <TcChat
        conversationId="c"
        mode={mode}
        messages={[]}
        approvals={[readAsk()]}
        {...props}
      />
    </TransportProvider>,
  );
}

describe("TcChat — the once/always choice on an ask", () => {
  it.each(["studio", "focus"] as const)(
    "keeps it the ASK card in %s mode, with the once arm one click away",
    (mode) => {
      renderChat(mode, { onWorkspaceGrant: vi.fn() });

      // Still the ask card, not the folder card. A `grant_scope` names what a
      // durable option WOULD attach; it does not turn the interrupt into a
      // folder ask, and conflating the two blocks would delete the once arm
      // from every ask that merely offers a durable one.
      expect(screen.getByTestId("tc-write-gate")).toBeTruthy();
      expect(
        screen.queryByTestId("tc-chat-workspace-grant-int-1:0"),
      ).toBeNull();
      // "Once" is the header's Approve, unchanged and one click.
      expect(
        screen.getByTestId("tc-chat-approval-approve-int-1:0"),
      ).toBeTruthy();
    },
  );

  it("hands the host the SCOPE the wire named, keyed by this approval", () => {
    const onWorkspaceGrant = vi.fn();
    renderChat("studio", { onWorkspaceGrant });

    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    fireEvent.click(
      screen.getByTestId("tc-chat-approval-grant-always-int-1:0"),
    );

    // The FOLDER, not the ask's own `path` argument — those differ for a
    // file-shaped call, and sending the wrong one would attach a grant the
    // card never showed.
    expect(onWorkspaceGrant).toHaveBeenCalledWith("int-1:0", REPORTS);
  });

  it("offers nothing durable when no host can take the decision", () => {
    // Web supplies no WorkspaceGrantPort, so `onWorkspaceGrant` is absent and
    // the arm is OMITTED rather than drawn as a button that opens no dialog.
    // The ask stays fully answerable — once — which is the whole degradation.
    renderChat("studio");

    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.queryByTestId("tc-write-gate-grant")).toBeNull();
    expect(
      screen.queryByTestId("tc-chat-approval-grant-always-int-1:0"),
    ).toBeNull();
    expect(screen.getByTestId("tc-chat-approval-approve-int-1:0")).toBeTruthy();
  });

  it("offers nothing durable on an ask the wire never offered one for", () => {
    render(
      <TransportProvider transport={makeTransport()}>
        <TcChat
          conversationId="c"
          mode="studio"
          messages={[]}
          approvals={[readAsk({ grantAlways: null })]}
          onWorkspaceGrant={vi.fn()}
        />
      </TransportProvider>,
    );
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.queryByTestId("tc-write-gate-grant")).toBeNull();
  });

  it("reads the host's per-approval dialog state and failure text", () => {
    // The OS dialog is invisible to the run stream, so the state comes from the
    // host's machine — the SAME `useWorkspaceGrantCardStates` map the folder
    // card reads. A folder ask and an ordinary ask can never share an
    // `approval_id`, so the two consumers are disjoint by construction.
    renderChat("studio", {
      onWorkspaceGrant: vi.fn(),
      workspaceGrantStates: { "int-1:0": "failed" },
      workspaceGrantFailures: { "int-1:0": "macOS refused that folder." },
    });

    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.getByTestId("tc-write-gate-grant-failure").textContent).toBe(
      "macOS refused that folder.",
    );
  });

  it("stays unambiguous with two asks parked at once", () => {
    // The reason every decision control is approval-scoped. Playwright refuses
    // an ambiguous selector, so a global `tc-write-gate-grant-always` would turn
    // "two cards parked" into a durable grant that never happened — or worse,
    // one taken against the wrong folder.
    const second = readAsk({
      approvalId: "int-2:0",
      title: "Read notes.md",
      grantAlways: {
        path: "/Users/parthpahwa/Documents/notes",
        folderName: "notes",
        mode: "read_only",
        reason: null,
      },
    });
    const onWorkspaceGrant = vi.fn();
    render(
      <TransportProvider transport={makeTransport()}>
        <TcChat
          conversationId="c"
          mode="studio"
          messages={[]}
          approvals={[readAsk(), second]}
          onWorkspaceGrant={onWorkspaceGrant}
        />
      </TransportProvider>,
    );

    for (const el of screen.getAllByTestId("tc-write-gate-review")) {
      fireEvent.click(el);
    }
    // Scoped names prove the buttons exist; `within` proves each belongs to the
    // card carrying that approval id.
    const secondCard = screen.getByTestId("tc-chat-approval-int-2:0");
    fireEvent.click(
      within(secondCard).getByTestId("tc-chat-approval-grant-always-int-2:0"),
    );
    expect(onWorkspaceGrant).toHaveBeenCalledTimes(1);
    expect(onWorkspaceGrant.mock.calls[0]?.[0]).toBe("int-2:0");
    expect(onWorkspaceGrant.mock.calls[0]?.[1]).toMatchObject({
      path: "/Users/parthpahwa/Documents/notes",
    });
  });

  it("never lets the durable arm answer to the prefix journeys press for Approve", () => {
    // Five live desktop journeys press
    // `[data-testid^=tc-chat-approval-approve-]`. With the arm expanded there
    // must still be exactly ONE match per card, and it must be the once arm.
    renderChat("studio", { onWorkspaceGrant: vi.fn() });
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));

    const matches = document.querySelectorAll(
      "[data-testid^=tc-chat-approval-approve-]",
    );
    expect(matches).toHaveLength(1);
    expect(matches[0]).toBe(
      screen.getByTestId("tc-chat-approval-approve-int-1:0"),
    );
  });
});
