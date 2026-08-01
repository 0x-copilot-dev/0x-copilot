// Composer execution-mode pill (PRD-FS-10 §4.3 the control, PRD-FS-11 the
// behaviour).
//
// The load-bearing assertions are the NEGATIVE ones, and they are made by
// accessible name rather than test id: if a user can reach "Bypass" while the
// master switch is off, it exists for them regardless of what a data attribute
// says. "Not offered" has to mean not-in-the-tree.
//
// The last block drives the REAL composition rather than the pill alone,
// because a pill can be perfect and mounted where it must never appear. That
// case came from the FS-10 lane, which mounted the pill inside the composer off
// data props; the mount moved to a host-owned `bypassTrigger` slot, and the
// capability gate it was carrying did not move with it — so it is re-pinned
// here against the slot.

import { fireEvent, render, screen } from "@testing-library/react";
import { type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";

import { TransportProvider } from "../providers/TransportProvider";
import type { FilePickerPort } from "../ports/FilePickerPort";
import type {
  WorkspaceGrant,
  WorkspaceGrantPort,
} from "../ports/WorkspaceGrantPort";
import {
  AssistantComposer,
  type AssistantComposerProps,
} from "./AssistantComposer";
import { BYPASS_BOUND_NOTE, BYPASS_BOUND_SUB, BypassPill } from "./BypassPill";
import {
  bypassSelectionForSend,
  bypassStateAfterSend,
  MANUAL_BYPASS_STATE,
} from "./filesystemBypass";

describe("BypassPill — master switch OFF", () => {
  it("renders a disabled Manual pill", () => {
    render(
      <BypassPill mode="manual" enabled={false} onChange={() => undefined} />,
    );
    const trigger = screen.getByRole("button", {
      name: /Execution mode: Manual/,
    });
    expect(trigger).toBeDisabled();
  });

  it("offers no Bypass option at all — not even after a click", () => {
    render(
      <BypassPill mode="manual" enabled={false} onChange={() => undefined} />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /Execution mode: Manual/ }),
    );
    expect(screen.queryByRole("menuitemradio", { name: /Bypass/ })).toBeNull();
    expect(screen.queryByText(BYPASS_BOUND_NOTE)).toBeNull();
  });

  it("reports Manual even when a stale mode says bypass", () => {
    // Defence against a host that persisted a selection, then had the master
    // switch turned off underneath it. The pill must not display a posture the
    // deployment no longer permits.
    const onChange = vi.fn();
    render(<BypassPill mode="bypass" enabled={false} onChange={onChange} />);
    expect(
      screen.getByRole("button", { name: /Execution mode: Manual/ }),
    ).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("points at Settings rather than silently doing nothing", () => {
    render(
      <BypassPill mode="manual" enabled={false} onChange={() => undefined} />,
    );
    expect(
      screen
        .getByRole("button", { name: /Execution mode: Manual/ })
        .getAttribute("data-tooltip"),
    ).toMatch(/Settings/);
  });
});

describe("BypassPill — master switch ON", () => {
  it("opens a menu offering Manual and Bypass", () => {
    render(<BypassPill mode="manual" enabled onChange={() => undefined} />);
    fireEvent.click(
      screen.getByRole("button", { name: /Execution mode: Manual/ }),
    );
    expect(
      screen.getByRole("menuitemradio", { name: /Manual/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitemradio", { name: /Bypass/ }),
    ).toBeInTheDocument();
  });

  it("states the standing bound as a non-selectable note", () => {
    render(<BypassPill mode="bypass" enabled onChange={() => undefined} />);
    fireEvent.click(
      screen.getByRole("button", { name: /Execution mode: Bypass/ }),
    );
    const note = screen.getByRole("note");
    expect(note).toHaveTextContent(BYPASS_BOUND_NOTE);
    expect(note).toHaveTextContent(BYPASS_BOUND_SUB);
    // A clarifier that could be clicked would read as a fourth option.
    expect(
      screen.queryByRole("menuitemradio", {
        name: new RegExp(BYPASS_BOUND_NOTE),
      }),
    ).toBeNull();
  });

  it("reports the selection and closes", () => {
    const onChange = vi.fn();
    render(<BypassPill mode="manual" enabled onChange={onChange} />);
    fireEvent.click(
      screen.getByRole("button", { name: /Execution mode: Manual/ }),
    );
    fireEvent.click(screen.getByRole("menuitemradio", { name: /Bypass/ }));
    expect(onChange).toHaveBeenCalledWith("bypass");
    expect(screen.queryByRole("menuitemradio", { name: /Bypass/ })).toBeNull();
  });

  it("offers the scope choice only once Bypass is the mode", () => {
    const { rerender } = render(
      <BypassPill
        mode="manual"
        enabled
        onChange={() => undefined}
        scope="message"
        onScopeChange={() => undefined}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /Execution mode: Manual/ }),
    );
    expect(
      screen.queryByRole("menuitemradio", { name: /This run/ }),
    ).toBeNull();

    rerender(
      <BypassPill
        mode="bypass"
        enabled
        onChange={() => undefined}
        scope="message"
        onScopeChange={() => undefined}
      />,
    );
    expect(
      screen.getByRole("menuitemradio", { name: /This run/ }),
    ).toBeInTheDocument();
  });

  it("stays inert while the composer is otherwise disabled", () => {
    render(
      <BypassPill mode="manual" enabled disabled onChange={() => undefined} />,
    );
    const trigger = screen.getByRole("button", {
      name: /Execution mode: Manual/,
    });
    expect(trigger).toBeDisabled();
    fireEvent.click(trigger);
    expect(screen.queryByRole("menuitemradio", { name: /Bypass/ })).toBeNull();
  });
});

describe("bypassSelectionForSend", () => {
  it("sends nothing while the master switch is off", () => {
    expect(
      bypassSelectionForSend(
        { mode: "bypass", scope: "run" },
        { masterEnabled: false },
      ),
    ).toBeUndefined();
  });

  it("sends nothing for the default Manual posture", () => {
    // A host that never surfaces the pill must produce the byte-identical
    // run-create body it produced before bypass existed.
    expect(
      bypassSelectionForSend(MANUAL_BYPASS_STATE, { masterEnabled: true }),
    ).toBeUndefined();
  });

  it("files the selection under the slot that names its scope", () => {
    expect(
      bypassSelectionForSend(
        { mode: "bypass", scope: "message" },
        { masterEnabled: true },
      ),
    ).toEqual({ message: "bypass" });
    expect(
      bypassSelectionForSend(
        { mode: "bypass", scope: "run" },
        { masterEnabled: true },
      ),
    ).toEqual({ run: "bypass" });
  });

  it("sends an explicit Manual at run scope", () => {
    // "This run does not bypass" is a real statement, distinct from absence,
    // and the backend distinguishes the two.
    expect(
      bypassSelectionForSend(
        { mode: "manual", scope: "run" },
        { masterEnabled: true },
      ),
    ).toEqual({ run: "manual" });
  });
});

describe("bypassStateAfterSend", () => {
  it("spends a message-scoped selection", () => {
    expect(bypassStateAfterSend({ mode: "bypass", scope: "message" })).toEqual(
      MANUAL_BYPASS_STATE,
    );
  });

  it("keeps a run-scoped selection", () => {
    const sticky = { mode: "bypass", scope: "run" } as const;
    expect(bypassStateAfterSend(sticky)).toEqual(sticky);
  });
});

// --- Where the pill may appear ------------------------------------------
// The slot is host-owned, but the GATE is not: bypass only ever applies inside
// a folder the user granted with write permission, so a composer with no grant
// capability has nothing bypass could permit. A host that passes a trigger
// anyway must still get nothing.

function makeTransport(): Transport {
  return {
    request: <TRes,>(_req: TypedRequest): Promise<TRes> =>
      Promise.resolve({ tools: [], candidates: [] }) as Promise<TRes>,
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

function makeGrantPort(): WorkspaceGrantPort {
  return {
    requestGrant: vi.fn(async () => ({ status: "cancelled" as const })),
    listGrants: vi.fn(async () => [] as ReadonlyArray<WorkspaceGrant>),
    revokeGrant: vi.fn(async () => ({ status: "revoked" as const })),
  };
}

function renderComposer(overrides: Partial<AssistantComposerProps> = {}): void {
  const props: AssistantComposerProps = {
    connectors: { servers: [], loading: false },
    skills: { skills: [], loading: false },
    filePicker: { pick: vi.fn(async () => []) } satisfies FilePickerPort,
    renderPlusMenu: ({ open, children }): ReactNode =>
      open ? <div>{children}</div> : null,
    skillInstructionPrompt: (name) => `Use the ${name} skill for this request.`,
    mcpServerInstructionPrompt: (name) =>
      `Use the ${name} MCP server for this request.`,
    onOpenMcpSettings: vi.fn(),
    onOpenSkillsSettings: vi.fn(),
    onShowConnectors: vi.fn(),
    onSubmit: vi.fn(),
    workspaceGrantPort: makeGrantPort(),
    bypassTrigger: (
      <BypassPill mode="manual" enabled onChange={() => undefined} />
    ),
    ...overrides,
  };
  render(
    <TransportProvider transport={makeTransport()}>
      <AssistantComposer {...props} />
    </TransportProvider>,
  );
}

describe("AssistantComposer — where the pill may appear", () => {
  it("mounts a supplied trigger on a host with the grant capability", () => {
    renderComposer();
    expect(
      screen.getByRole("button", { name: /Execution mode/i }),
    ).toBeInTheDocument();
  });

  it("is absent on a host with no grant capability (web)", () => {
    renderComposer({ workspaceGrantPort: null });
    expect(
      screen.queryByRole("button", { name: /Execution mode/i }),
    ).toBeNull();
  });

  it("renders nothing when the host supplies no trigger", () => {
    renderComposer({ bypassTrigger: undefined });
    expect(
      screen.queryByRole("button", { name: /Execution mode/i }),
    ).toBeNull();
  });
});
