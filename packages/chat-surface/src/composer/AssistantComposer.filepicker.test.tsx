// FR-1.9 / FR-1.10 — the hoisted AssistantComposer shell routes its
// attachment picker through the injected `FilePickerPort`, never through a
// direct `document.createElement("input")`. This is the machine check that
// the shell is substrate-agnostic: the `+` menu's Attach Image / Attach File
// actions call `filePicker.pick({ multiple, accept })` with the right options.

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
import {
  AssistantComposer,
  type AssistantComposerProps,
} from "./AssistantComposer";
import { fileAttachmentAccept } from "./fileAttachmentAccept";

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

function renderComposer(overrides: Partial<AssistantComposerProps> = {}): {
  filePicker: FilePickerPort;
} {
  const filePicker = overrides.filePicker ?? makeFilePicker();
  const props: AssistantComposerProps = {
    connectors: { servers: [], loading: false },
    skills: { skills: [], loading: false },
    filePicker,
    // Render the popover inline (no host portal) so the menu items are in
    // the DOM to click. The production host wraps this in `AnchoredPlusMenu`.
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
  return { filePicker };
}

function openPlusMenu(): void {
  fireEvent.click(
    screen.getByRole("button", { name: /Open attachment and tools menu/i }),
  );
}

describe("AssistantComposer file picker (FR-1.9)", () => {
  it("routes Attach File through FilePickerPort.pick with the office accept list", () => {
    const { filePicker } = renderComposer();
    openPlusMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /Attach File/i }));
    expect(filePicker.pick).toHaveBeenCalledTimes(1);
    expect(filePicker.pick).toHaveBeenCalledWith({
      multiple: true,
      accept: [fileAttachmentAccept],
    });
  });

  it("routes Attach Image through FilePickerPort.pick with an image accept", () => {
    const { filePicker } = renderComposer();
    openPlusMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /Attach Image/i }));
    expect(filePicker.pick).toHaveBeenCalledWith({
      multiple: true,
      accept: ["image/*"],
    });
  });

  it("forwards each picked file to the composer via addAttachment", async () => {
    const file = new File(["hi"], "notes.txt", { type: "text/plain" });
    const filePicker: FilePickerPort = { pick: vi.fn(async () => [file]) };
    const add = vi.fn(async () => ({
      id: "a1",
      name: "notes.txt",
      type: "text/plain",
      size: 2,
      status: { type: "pending" as const },
    }));
    renderComposer({
      filePicker,
      attachmentAdapter: {
        add,
        remove: vi.fn(),
      },
    });
    openPlusMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /Attach File/i }));
    // `pick()` resolves on a microtask, then the shell forwards the file.
    await waitFor(() => expect(add).toHaveBeenCalledTimes(1));
    expect(add).toHaveBeenCalledWith(file);
  });
});
