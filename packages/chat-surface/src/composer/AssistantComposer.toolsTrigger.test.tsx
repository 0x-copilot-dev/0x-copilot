// FTUE P4 — the AssistantComposer bottom bar exposes an additive
// `toolsTrigger` slot (host-owned Tools popover anchor) rendered next to the
// existing `connectorsTrigger`. Additive: when unset the bottom bar is
// byte-identical to before.

import { render, screen } from "@testing-library/react";
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

function makeFilePicker(): FilePickerPort {
  return { pick: vi.fn(async () => []) };
}

function renderComposer(overrides: Partial<AssistantComposerProps> = {}): void {
  const props: AssistantComposerProps = {
    connectors: { servers: [], loading: false },
    skills: { skills: [], loading: false },
    filePicker: makeFilePicker(),
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

describe("AssistantComposer toolsTrigger slot (FTUE P4)", () => {
  it("renders the toolsTrigger in the bottom bar when supplied", () => {
    renderComposer({
      toolsTrigger: <button data-testid="tools-trigger">Tools</button>,
    });
    expect(screen.getByTestId("tools-trigger")).toBeInTheDocument();
  });

  it("renders no toolsTrigger when the slot is unset (byte-identical bottom bar)", () => {
    renderComposer();
    expect(screen.queryByTestId("tools-trigger")).toBeNull();
    // The + attachment button still anchors the bottom bar (slot is purely
    // additive — nothing else moved).
    expect(
      screen.getByRole("button", { name: /Open attachment and tools menu/i }),
    ).toBeInTheDocument();
  });

  it("renders both connectorsTrigger and toolsTrigger side by side", () => {
    renderComposer({
      connectorsTrigger: (
        <button data-testid="connectors-trigger">Connectors</button>
      ),
      toolsTrigger: <button data-testid="tools-trigger">Tools</button>,
    });
    const connectors = screen.getByTestId("connectors-trigger");
    const tools = screen.getByTestId("tools-trigger");
    expect(connectors).toBeInTheDocument();
    expect(tools).toBeInTheDocument();
    // toolsTrigger renders after connectorsTrigger in DOM order.
    expect(
      connectors.compareDocumentPosition(tools) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});
