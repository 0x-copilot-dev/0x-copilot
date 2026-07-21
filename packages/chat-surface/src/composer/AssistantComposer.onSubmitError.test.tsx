// AssistantComposer adoption of the onSubmit error channel.
//
// The AssistantComposer wraps the base Composer and, on submit, dispatches the
// host's `onSubmit` and clears the selected-skill pills once it resolves. It
// now RETURNS that promise to the base Composer so the SSOT `.catch` routes a
// rejection into `onSubmitError`. These tests lock the wired semantics:
//   - a resolving submit still fires `onClearSkills`;
//   - a rejecting submit fires `onSubmitError` (with the error) and does NOT
//     fire `onClearSkills` (skills survive a failed send for a clean retry);
//   - a rejecting submit with no `onSubmitError` never becomes an unhandled
//     rejection.

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";

import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import type { Skill } from "@0x-copilot/api-types";

import { TransportProvider } from "../providers/TransportProvider";
import type { FilePickerPort } from "../ports/FilePickerPort";
import {
  AssistantComposer,
  type AssistantComposerProps,
} from "./AssistantComposer";

// Substrate-agnostic package (no @types/node); declare the minimal `process`
// surface the unhandled-rejection guard uses so tsc stays clean.
declare const process: {
  on(event: "unhandledRejection", listener: (reason: unknown) => void): void;
  off(event: "unhandledRejection", listener: (reason: unknown) => void): void;
};

function makeTransport(): Transport {
  return {
    request: <TRes,>(_req: TypedRequest): Promise<TRes> =>
      Promise.resolve({}) as Promise<TRes>,
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

function makeSkill(id: string, name: string): Skill {
  return {
    skill_id: id,
    name,
    display_name: name,
  } as unknown as Skill;
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

function textarea(): HTMLTextAreaElement {
  return screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
}

function typeAndSend(text: string): void {
  fireEvent.change(textarea(), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: /Send message/i }));
}

describe("AssistantComposer onSubmit error channel", () => {
  let unhandled: unknown[];
  const onUnhandled = (reason: unknown): void => {
    unhandled.push(reason);
  };
  beforeEach(() => {
    unhandled = [];
    process.on("unhandledRejection", onUnhandled);
  });
  afterEach(() => {
    process.off("unhandledRejection", onUnhandled);
  });

  it("clears the selected skills once a submit resolves", async () => {
    const onSubmit = vi.fn(() => Promise.resolve());
    const onClearSkills = vi.fn();
    renderComposer({
      onSubmit,
      onClearSkills,
      selectedSkills: [makeSkill("s1", "researcher")],
    });

    typeAndSend("go");

    await waitFor(() => expect(onClearSkills).toHaveBeenCalledTimes(1));
  });

  it("routes a rejected submit to onSubmitError and keeps the skills (no clear)", async () => {
    const boom = new Error("missing provider key");
    const onSubmit = vi.fn(() => Promise.reject(boom));
    const onSubmitError = vi.fn();
    const onClearSkills = vi.fn();
    renderComposer({
      onSubmit,
      onSubmitError,
      onClearSkills,
      selectedSkills: [makeSkill("s1", "researcher")],
    });

    typeAndSend("go");

    await waitFor(() => expect(onSubmitError).toHaveBeenCalledTimes(1));
    expect(onSubmitError).toHaveBeenCalledWith(boom);
    // Skills are NOT cleared on failure — the user can retry with them intact.
    expect(onClearSkills).not.toHaveBeenCalled();
  });

  it("catches a rejected submit with no onSubmitError (no unhandled rejection, no clear)", async () => {
    const onSubmit = vi.fn(() => Promise.reject(new Error("no channel")));
    const onClearSkills = vi.fn();
    renderComposer({ onSubmit, onClearSkills });

    expect(() => typeAndSend("go")).not.toThrow();

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(unhandled).toEqual([]);
    expect(onClearSkills).not.toHaveBeenCalled();
  });
});
