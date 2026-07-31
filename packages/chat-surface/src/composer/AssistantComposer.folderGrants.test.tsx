// Attach Folder — the composer's half of "granted, not assumed".
//
// The gating assertion is the important one. Web implements no
// `WorkspaceGrantPort`, so the row must not render there at all: a menu item
// that opens nothing is worse than an absent one, and this is the same rule the
// `DeploymentProfile` rail follows (the capability decides, not the component).
// The rest pins that a granted folder is visible as a pill and that dismissing
// the pill REVOKES through the port rather than just forgetting locally.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

const DOWNLOADS: WorkspaceGrant = {
  grantId: "grant_01",
  mount: "m_9f2c",
  label: "Downloads",
  mode: "read_only",
};

function makeGrantPort(
  overrides: Partial<WorkspaceGrantPort> = {},
): WorkspaceGrantPort {
  return {
    requestGrant: vi.fn(async () => ({ status: "cancelled" as const })),
    listGrants: vi.fn(async () => [] as ReadonlyArray<WorkspaceGrant>),
    revokeGrant: vi.fn(async () => ({ status: "revoked" as const })),
    ...overrides,
  };
}

function renderComposer(overrides: Partial<AssistantComposerProps> = {}): void {
  const filePicker: FilePickerPort = { pick: vi.fn(async () => []) };
  const props: AssistantComposerProps = {
    connectors: { servers: [], loading: false },
    skills: { skills: [], loading: false },
    filePicker,
    // Inline popover (no host portal) so the menu rows are in the DOM.
    renderPlusMenu: ({ open, children }): ReactNode =>
      open ? <div>{children}</div> : null,
    skillInstructionPrompt: (name) => `Use the ${name} skill for this request.`,
    mcpServerInstructionPrompt: (name) =>
      `Use the ${name} MCP server for this request.`,
    onOpenMcpSettings: vi.fn(),
    onOpenSkillsSettings: vi.fn(),
    onShowConnectors: vi.fn(),
    onSubmit: vi.fn(),
    ...overrides,
  };
  render(
    <TransportProvider transport={makeTransport()}>
      <AssistantComposer {...props} />
    </TransportProvider>,
  );
}

function openPlusMenu(): void {
  fireEvent.click(
    screen.getByRole("button", { name: /Open attachment and tools menu/i }),
  );
}

describe("AssistantComposer — Attach Folder row gating", () => {
  it("does not render the row when no WorkspaceGrantPort is supplied", () => {
    renderComposer();
    openPlusMenu();

    expect(
      screen.queryByRole("menuitem", { name: /Attach Folder/i }),
    ).toBeNull();
    // The four unconditional rows are untouched.
    expect(
      screen.getByRole("menuitem", { name: /Attach Image/i }),
    ).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: /Attach File/i })).toBeTruthy();
  });

  it("renders the row when a port is supplied, and asks the host for a folder", async () => {
    const port = makeGrantPort();
    renderComposer({ workspaceGrantPort: port });
    openPlusMenu();

    const row = screen.getByRole("menuitem", { name: /Attach Folder/i });
    expect(row).toBeTruthy();

    fireEvent.click(row);
    await waitFor(() => expect(port.requestGrant).toHaveBeenCalledTimes(1));
    // No path from this entry point — the host's own picker IS the consent.
    expect(port.requestGrant).toHaveBeenCalledWith(undefined);
    // The popover dismisses before the native dialog takes the focus.
    expect(
      screen.queryByRole("menuitem", { name: /Attach Folder/i }),
    ).toBeNull();
  });

  it("treats an explicit null port as no capability", () => {
    renderComposer({ workspaceGrantPort: null });
    openPlusMenu();
    expect(
      screen.queryByRole("menuitem", { name: /Attach Folder/i }),
    ).toBeNull();
  });
});

describe("AssistantComposer — granted folder pills", () => {
  it("renders a pill per active grant, labelled with its access", async () => {
    const port = makeGrantPort({ listGrants: vi.fn(async () => [DOWNLOADS]) });
    renderComposer({ workspaceGrantPort: port });

    expect(await screen.findByText("Downloads")).toBeTruthy();
    // The pill's small print is the ACCESS, not a MIME type.
    expect(screen.getByText("Read-only")).toBeTruthy();
  });

  it("revokes through the port when the pill is dismissed", async () => {
    let active: readonly WorkspaceGrant[] = [DOWNLOADS];
    const revokeGrant = vi.fn(async (grantId: string) => {
      active = active.filter((grant) => grant.grantId !== grantId);
      return { status: "revoked" as const };
    });
    const port = makeGrantPort({
      listGrants: vi.fn(async () => active),
      revokeGrant,
    });
    renderComposer({ workspaceGrantPort: port });

    const remove = await screen.findByRole("button", {
      name: "Stop sharing Downloads with the agent",
    });
    fireEvent.click(remove);

    await waitFor(() => expect(revokeGrant).toHaveBeenCalledWith("grant_01"));
    // The broker is the source of truth: the pill goes away because the
    // re-read says the grant is gone, not because we forgot it locally.
    await waitFor(() => expect(screen.queryByText("Downloads")).toBeNull());
  });

  it("shows a failed grant read instead of silently rendering no folders", async () => {
    const port = makeGrantPort({
      listGrants: vi.fn(async () => {
        throw new Error("The capability broker is not running.");
      }),
    });
    renderComposer({ workspaceGrantPort: port });

    expect(
      await screen.findByText("The capability broker is not running."),
    ).toBeTruthy();
  });
});
