// Per-run tools belong inside the composer's one `+` menu, never as a second
// bottom-bar trigger. This exercises the complete disclosure path.

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
    ...overrides,
  };
  render(
    <TransportProvider transport={makeTransport()}>
      <AssistantComposer {...props} />
    </TransportProvider>,
  );
}

describe("AssistantComposer per-run Tools menu", () => {
  it("uses the + menu as the only trigger and returns to its root", () => {
    renderComposer({
      renderToolsMenu: ({ onBack }) => (
        <button data-testid="tools-content" type="button" onClick={onBack}>
          Back from tools
        </button>
      ),
    });

    expect(screen.queryByTestId("first-run-tools-button")).toBeNull();

    fireEvent.click(
      screen.getByRole("button", { name: /Open attachment and tools menu/i }),
    );
    fireEvent.click(screen.getByTestId("composer-plus-menu-tools"));

    expect(
      screen.getByRole("menu", { name: "Tools menu" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("tools-content"));
    expect(
      screen.getByRole("menu", { name: "Attachment and tools menu" }),
    ).toBeInTheDocument();
  });
});
