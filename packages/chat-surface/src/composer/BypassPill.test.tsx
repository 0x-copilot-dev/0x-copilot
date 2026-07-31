// BypassPill — PRD-FS-10 §8 test 9, and the gating around it.
//
// The DISABLED state is the load-bearing one. This PRD ships the control before
// PRD-FS-11 ships the behaviour, so until the Settings master is on the pill
// must not offer Bypass AT ALL: a choice that is offered and then ignored is
// worse than an absent one, and "the user said bypass" is exactly the kind of
// claim that later gets read as authorization by something downstream.
//
// The composer-level cases drive the REAL composition (gating, wiring, the
// capability check) rather than rendering the pill directly — a pill can be
// perfect and mounted nowhere, or mounted everywhere it should not be.

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
import { BypassPill } from "./BypassPill";
import {
  AssistantComposer,
  type AssistantComposerProps,
} from "./AssistantComposer";

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
    ...overrides,
  };
  render(
    <TransportProvider transport={makeTransport()}>
      <AssistantComposer {...props} />
    </TransportProvider>,
  );
}

function pill(): HTMLElement {
  return screen.getByRole("button", { name: /Execution mode/i });
}

describe("BypassPill — master OFF (test 9)", () => {
  it("is a disabled Manual pill that offers nothing", () => {
    const onChange = vi.fn();
    render(<BypassPill mode="manual" enabled={false} onChange={onChange} />);

    const trigger = screen.getByRole("button", {
      name: "Execution mode: Manual",
    });
    expect(trigger).toBeDisabled();

    fireEvent.click(trigger);
    // No menu, and in particular no reachable "Bypass" anywhere in the document.
    expect(screen.queryByRole("menuitemradio")).toBeNull();
    expect(screen.queryByText("Bypass")).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("still says MANUAL when a stale bypass selection outlives the master", () => {
    // A selection kept from when the master was on must not be DISPLAYED as
    // bypass: the pill's job is to say what this run will actually do, and with
    // the master off the answer is "it will ask".
    render(<BypassPill mode="bypass" enabled={false} onChange={vi.fn()} />);
    expect(
      screen.getByRole("button", { name: "Execution mode: Manual" }),
    ).toBeTruthy();
    expect(screen.queryByText("Bypass")).toBeNull();
  });
});

describe("BypassPill — master ON", () => {
  it("offers Manual and Bypass, with the standing clarifier", () => {
    const onChange = vi.fn();
    render(<BypassPill mode="manual" enabled onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: /Execution mode/i }));

    expect(
      screen.getByRole("menuitemradio", { name: /Manual/ }),
    ).toHaveAttribute("aria-checked", "true");
    const bypass = screen.getByRole("menuitemradio", { name: /Bypass/ });
    expect(bypass).toHaveAttribute("aria-checked", "false");

    // Terse, and NOT selectable: it is a standing rule about the system, not a
    // third option and not a warning about the choice being made.
    const clarifier = screen.getByText("Ungranted still asks");
    expect(clarifier).toBeTruthy();
    expect(clarifier.closest("button")).toBeNull();
    expect(
      screen.getByText("bypass never widens what you granted"),
    ).toBeTruthy();

    fireEvent.click(bypass);
    expect(onChange).toHaveBeenCalledWith("bypass");
  });
});

describe("AssistantComposer — where the pill may appear", () => {
  it("is absent on a host with no grant capability (web)", () => {
    renderComposer({ workspaceGrantPort: null });
    expect(
      screen.queryByRole("button", { name: /Execution mode/i }),
    ).toBeNull();
  });

  it("renders DISABLED by default — the master is off until Settings says so", () => {
    renderComposer({ onBypassModeChange: vi.fn() });
    expect(pill()).toBeDisabled();
    expect(pill()).toHaveAccessibleName("Execution mode: Manual");
  });

  it("stays disabled when the master is on but nothing consumes the choice", () => {
    // Offered-but-ignored is the failure this guards: a host that flips the
    // master without wiring the sink would otherwise ship a live-looking control
    // that changes nothing about the run.
    renderComposer({ bypassMasterEnabled: true });
    expect(pill()).toBeDisabled();
  });

  it("becomes a real menu once the master is on AND the host consumes it", () => {
    const onBypassModeChange = vi.fn();
    renderComposer({ bypassMasterEnabled: true, onBypassModeChange });

    expect(pill()).not.toBeDisabled();
    fireEvent.click(pill());
    fireEvent.click(screen.getByRole("menuitemradio", { name: /Bypass/ }));
    expect(onBypassModeChange).toHaveBeenCalledWith("bypass");
  });

  it("reflects the host's selection", () => {
    renderComposer({
      bypassMasterEnabled: true,
      bypassMode: "bypass",
      onBypassModeChange: vi.fn(),
    });
    expect(pill()).toHaveAccessibleName("Execution mode: Bypass");
  });
});
