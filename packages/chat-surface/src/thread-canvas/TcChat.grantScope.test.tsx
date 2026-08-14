// The once/always control, where it actually lands — and, more importantly,
// where it must NOT.
//
// Both lanes that emit `grant_options` render through the SAME ask card
// (`renderAskCard`), so "the write gate offers a run-scoped always" and "the
// filesystem card does not" are two states of one component. A test that only
// drove the happy lane would pass over a card offering to widen a decision the
// server's resume builder never carries.

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

import { EMPTY_CONNECTOR_TRUST } from "../approvals";
import { WRITE_GATE_APPROVAL_PREFIX } from "../destinations/run/approvalProjection";
import { TransportProvider } from "../providers/TransportProvider";
import { TcChat, type TcChatApproval } from "./TcChat";

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

const GATE_ID = `${WRITE_GATE_APPROVAL_PREFIX}run-1:call-1`;

function approval(overrides: Partial<TcChatApproval> = {}): TcChatApproval {
  return {
    approvalId: GATE_ID,
    title: "Create an issue in Parth-test",
    reason: "Copilot is about to file the bug you described.",
    summary: null,
    approvalKind: "ask_a_question",
    serverId: null,
    category: { vendor: "linear", access: "WRITE" },
    params: [],
    presentation: null,
    connectorTrust: EMPTY_CONNECTOR_TRUST,
    question: null,
    resolved: false,
    decision: null,
    createdAtMs: 1716000090000,
    allowsRunScopedGrant: true,
    ...overrides,
  };
}

function renderChat(
  approvals: readonly TcChatApproval[],
  props: Partial<React.ComponentProps<typeof TcChat>> = {},
): void {
  render(
    <TransportProvider transport={makeTransport()}>
      <TcChat
        conversationId="c"
        mode="studio"
        messages={[]}
        approvals={approvals}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        {...props}
      />
    </TransportProvider>,
  );
}

function expand(approvalId: string): void {
  const card = screen.getByTestId(`tc-chat-approval-${approvalId}`);
  fireEvent.click(within(card).getByTestId("tc-write-gate-review"));
}

describe("TcChat — the once/always grant control", () => {
  it("offers the run-scoped always on a parked write, approval-scoped", () => {
    const onApproveAlways = vi.fn();
    renderChat([approval()], { onApproveAlways });
    expand(GATE_ID);
    const card = screen.getByTestId(`tc-chat-approval-${GATE_ID}`);
    const always = within(card).getByTestId(
      `tc-chat-approval-always-${GATE_ID}`,
    );
    fireEvent.click(always);
    // Named after the approval it decides, like every other decision on this
    // card: two asks parked at once is a drawn state, and an ambiguous selector
    // is a decision that never happens.
    expect(onApproveAlways).toHaveBeenCalledWith(GATE_ID);
  });

  it("withholds it when the projection says this lane's `always` is not a run scope", () => {
    // The filesystem card: same component, `allow_always` on the wire, and an
    // entirely different act behind it (attach a folder — durable, wider than
    // the path on the card, settled by an OS dialog). `decision_scope` is
    // dropped on that lane, so a control here would post into the void.
    renderChat(
      [
        approval({
          approvalId: "appr-fs-1",
          approvalKind: "tool_action",
          title: "Read reports",
          allowsRunScopedGrant: false,
        }),
      ],
      { onApproveAlways: vi.fn() },
    );
    expand("appr-fs-1");
    expect(
      screen.queryByTestId("tc-chat-approval-always-appr-fs-1"),
    ).toBeNull();
    // …and the ask card itself is unchanged, so the reader loses nothing.
    expect(screen.getByTestId("tc-write-gate")).toBeTruthy();
    expect(
      screen.getByTestId("tc-chat-approval-approve-appr-fs-1"),
    ).toBeTruthy();
  });

  it("withholds it when no host wired a handler", () => {
    renderChat([approval()]);
    expand(GATE_ID);
    expect(
      screen.queryByTestId(`tc-chat-approval-always-${GATE_ID}`),
    ).toBeNull();
  });

  it("withholds it for an irreversible write even if the projection said yes", () => {
    // Defence in depth: the server withholds `allow_always` for a destructive
    // op and the projection would follow, but the card is where "no advance yes
    // to an irreversible act" is drawn, so it refuses on its own evidence too.
    renderChat(
      [
        approval({
          irreversible: true,
          params: [{ label: "count", value: "14" }],
        }),
      ],
      { onApproveAlways: vi.fn() },
    );
    expand(GATE_ID);
    expect(
      screen.queryByTestId(`tc-chat-approval-always-${GATE_ID}`),
    ).toBeNull();
    // The one approve an irreversible write gets is still the payload-gated one.
    expect(
      screen.getByTestId(`tc-chat-approval-body-approve-${GATE_ID}`),
    ).toBeTruthy();
    expect(
      screen.queryByTestId(`tc-chat-approval-approve-${GATE_ID}`),
    ).toBeNull();
  });

  it("is never one click from the collapsed card", () => {
    renderChat([approval()], { onApproveAlways: vi.fn() });
    // Unexpanded: Approve and Decline are reachable, the scope is not.
    expect(
      screen.getByTestId(`tc-chat-approval-approve-${GATE_ID}`),
    ).toBeTruthy();
    expect(
      screen.getByTestId(`tc-chat-approval-reject-${GATE_ID}`),
    ).toBeTruthy();
    expect(
      screen.queryByTestId(`tc-chat-approval-always-${GATE_ID}`),
    ).toBeNull();
  });
});
