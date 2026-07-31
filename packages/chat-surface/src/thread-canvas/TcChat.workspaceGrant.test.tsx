// The mid-run folder ask, where it actually lands.
//
// A card exported from the barrel that nothing renders is not a surface. These
// tests are the check that an interrupt carrying `payload.workspace_grant`
// reaches `WorkspaceGrantCard` in BOTH modes and does NOT reach the Approve /
// Reject `/decision` card — approving a folder ask through `/decision` would
// resume the run with no grant, which is precisely the failure this whole path
// exists to remove (an ungranted read answered with an empty listing).

import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
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

const DOWNLOADS: WorkspaceGrantRequest = {
  path: "/Users/parthpahwa/Downloads",
  folderName: "Downloads",
  mode: "read_only",
  reason: "to see what you downloaded today",
};

// The ask rides an ORDINARY interrupt — same `tool_action` kind a plain approval
// carries. Only the payload block makes it a folder ask, which is the contract
// (`WORKSPACE_GRANT_PAYLOAD_KEY`): a backend raises this card without inventing
// a new approval kind for the hosts to learn.
function grantApproval(
  overrides: Partial<TcChatApproval> = {},
): TcChatApproval {
  return {
    approvalId: "appr-fs-1",
    title: "List Downloads",
    reason: "Copilot needs a folder it has not been given.",
    summary: null,
    approvalKind: "tool_action",
    serverId: null,
    category: null,
    params: [],
    presentation: null,
    connectorTrust: EMPTY_CONNECTOR_TRUST,
    question: null,
    workspaceGrant: DOWNLOADS,
    resolved: false,
    decision: null,
    createdAtMs: 1716000090000,
    ...overrides,
  };
}

function renderChat(
  mode: TcChatMode,
  props: Partial<React.ComponentProps<typeof TcChat>> = {},
): ReactNode {
  render(
    <TransportProvider transport={makeTransport()}>
      <TcChat
        conversationId="c"
        mode={mode}
        messages={[]}
        approvals={[grantApproval()]}
        {...props}
      />
    </TransportProvider>,
  );
  return null;
}

describe("TcChat — mid-run folder grant ask", () => {
  it.each(["studio", "focus"] as const)(
    "renders the folder card in %s mode, naming the exact folder",
    (mode) => {
      renderChat(mode, { onWorkspaceGrant: vi.fn() });

      expect(
        screen.getByTestId("tc-chat-workspace-grant-appr-fs-1"),
      ).toBeTruthy();
      expect(screen.getByTestId("wg-path").textContent).toBe(
        "/Users/parthpahwa/Downloads",
      );
      expect(screen.getByText(/Let the agent read Downloads\?/)).toBeTruthy();
      // Never the `/decision` Approve/Reject card.
      expect(
        screen.queryByTestId("tc-chat-approval-approve-appr-fs-1"),
      ).toBeNull();
      expect(screen.queryByTestId("tc-chat-conf-approve-appr-fs-1")).toBeNull();
    },
  );

  it("hands Grant the parsed ask, and Deny the approval id", () => {
    const onWorkspaceGrant = vi.fn();
    const onWorkspaceGrantDeny = vi.fn();
    renderChat("studio", { onWorkspaceGrant, onWorkspaceGrantDeny });

    fireEvent.click(
      screen.getByTestId("tc-chat-workspace-grant-approve-appr-fs-1"),
    );
    expect(onWorkspaceGrant).toHaveBeenCalledWith("appr-fs-1", DOWNLOADS);

    fireEvent.click(
      screen.getByTestId("tc-chat-workspace-grant-deny-appr-fs-1"),
    );
    expect(onWorkspaceGrantDeny).toHaveBeenCalledWith("appr-fs-1");
  });

  it("renders the ask inert — but readable — when the host wired no grant handler", () => {
    renderChat("studio");

    // Still named, still explained. Web lands here.
    expect(screen.getByTestId("wg-path").textContent).toBe(
      "/Users/parthpahwa/Downloads",
    );
    expect(
      screen
        .getByTestId("tc-chat-workspace-grant-approve-appr-fs-1")
        .hasAttribute("disabled"),
    ).toBe(true);
    // And emphatically not a `/decision` approve, which would resume the run
    // without a grant.
    expect(
      screen.queryByTestId("tc-chat-approval-approve-appr-fs-1"),
    ).toBeNull();
  });

  it("shows the host's per-approval state and failure message", () => {
    renderChat("studio", {
      onWorkspaceGrant: vi.fn(),
      workspaceGrantStates: { "appr-fs-1": "failed" },
      workspaceGrantFailures: {
        "appr-fs-1": "macOS refused access to that folder.",
      },
    });

    expect(screen.getByTestId("wg-failure").textContent).toBe(
      "macOS refused access to that folder.",
    );
  });

  it("leaves an approval with no grant block on the ordinary approval card", () => {
    render(
      <TransportProvider transport={makeTransport()}>
        <TcChat
          conversationId="c"
          mode="studio"
          messages={[]}
          approvals={[grantApproval({ workspaceGrant: null })]}
          onApprove={vi.fn()}
        />
      </TransportProvider>,
    );

    expect(
      screen.queryByTestId("tc-chat-workspace-grant-appr-fs-1"),
    ).toBeNull();
    expect(
      screen.getByTestId("tc-chat-approval-approve-appr-fs-1"),
    ).toBeTruthy();
  });
});
