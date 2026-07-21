// OnboardingComposer — H1 + chips + real AssistantComposer mount (PRD-P3 §6.2).

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import type { ModelCatalogModel } from "@0x-copilot/api-types";
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
import type { AttachmentAdapter } from "../composer";
import {
  OnboardingComposer,
  ONBOARDING_COMPOSER_COPY,
  type OnboardingComposerProps,
} from "./OnboardingComposer";

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

const MODEL: ModelCatalogModel = {
  id: "m1",
  provider: "anthropic",
  model_name: "claude-sonnet-4-5",
  name: "Claude Sonnet 4.5",
  configured: true,
  supports_streaming: true,
};

function singleStageAdapter(): AttachmentAdapter {
  return {
    add: vi.fn(async (file: File) => ({
      id: `att-${file.name}`,
      name: file.name,
      size: file.size,
      type: file.type,
      content: [{ type: "text", text: `<attachment>${file.name}</attachment>` }],
      status: { type: "complete" as const },
    })),
    remove: vi.fn(),
  };
}

function renderOnboardingComposer(
  overrides: Partial<OnboardingComposerProps> = {},
): { onSubmit: ReturnType<typeof vi.fn>; container: HTMLElement } {
  const onSubmit = vi.fn();
  const filePicker: FilePickerPort = { pick: vi.fn(async () => []) };
  const props: OnboardingComposerProps = {
    connectors: { servers: [], loading: false },
    skills: { skills: [], loading: false },
    attachmentAdapter: singleStageAdapter(),
    filePicker,
    renderPlusMenu: ({ open, children }): ReactNode =>
      open ? <div>{children}</div> : null,
    skillInstructionPrompt: (name) => `Use the ${name} skill for this request.`,
    mcpServerInstructionPrompt: (name) =>
      `Use the ${name} MCP server for this request.`,
    onShowConnectors: vi.fn(),
    onOpenSkillsSettings: vi.fn(),
    onOpenMcpSettings: vi.fn(),
    models: [MODEL],
    selectedModel: "m1",
    onModelChange: vi.fn(),
    onSubmit,
    ...overrides,
  };
  const { container } = render(
    <TransportProvider transport={makeTransport()}>
      <OnboardingComposer {...props} />
    </TransportProvider>,
  );
  return { onSubmit, container };
}

function textarea(container: HTMLElement): HTMLTextAreaElement {
  const ta = container.querySelector<HTMLTextAreaElement>(
    "[data-testid='composer-textarea']",
  );
  if (ta === null) throw new Error("composer textarea not mounted");
  return ta;
}

describe("<OnboardingComposer>", () => {
  it("renders the verbatim H1 + textarea placeholder", () => {
    const { container } = renderOnboardingComposer();
    expect(screen.getByTestId("first-run-composer-h1").textContent).toBe(
      "What should we run first?",
    );
    expect(textarea(container).placeholder).toBe(
      ONBOARDING_COMPOSER_COPY.placeholder,
    );
  });

  it("picking a chip sets the composer text to the verbatim prompt", () => {
    const { container } = renderOnboardingComposer();
    fireEvent.click(screen.getByTestId("first-run-chip-watch-wallet"));
    expect(textarea(container).value).toBe(
      "Watch 0x7f3C…a92C and alert me on any transfer over $500. Keep running in the background.",
    );
  });

  it("the CSV chip resolves + attaches airdrop-claims.csv (addAttachment)", async () => {
    const csv = new File(["a,b\n1,2\n"], "airdrop-claims.csv", {
      type: "text/csv",
    });
    const resolveAttachment = vi.fn(async () => csv);
    const adapter = singleStageAdapter();
    const { container } = renderOnboardingComposer({
      resolveAttachment,
      attachmentAdapter: adapter,
    });

    fireEvent.click(screen.getByTestId("first-run-chip-explain-csv"));
    // Prompt inserted synchronously…
    expect(textarea(container).value).toBe(
      "Explain this CSV… chart the top movers.",
    );
    // …then the host resolves the fixture and the adapter attaches it.
    await waitFor(() =>
      expect(resolveAttachment).toHaveBeenCalledWith("airdrop-claims.csv"),
    );
    await waitFor(() => expect(adapter.add).toHaveBeenCalledWith(csv));
  });

  it("forwards { text, attachments } to onSubmit on send", async () => {
    const { container, onSubmit } = renderOnboardingComposer();
    const ta = textarea(container);
    fireEvent.change(ta, { target: { value: "do a thing" } });
    fireEvent.click(
      container.querySelector<HTMLButtonElement>(
        "button[aria-label='Send message']",
      ) as HTMLButtonElement,
    );
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0]).toMatchObject({ text: "do a thing" });
    expect(Array.isArray(onSubmit.mock.calls[0][0].attachments)).toBe(true);
  });

  it("renders the inline error + 'Add a key' CTA on a configuration_error", () => {
    const onAddKey = vi.fn();
    renderOnboardingComposer({
      startError: {
        message: "Missing API key for model provider 'anthropic'.",
        code: "configuration_error",
      },
      onAddKey,
    });
    expect(
      screen.getByTestId("first-run-composer-error-message").textContent,
    ).toContain("Missing API key");
    fireEvent.click(screen.getByTestId("first-run-composer-error-cta"));
    expect(onAddKey).toHaveBeenCalledTimes(1);
  });

  it("hides the CTA for a non-configuration error", () => {
    renderOnboardingComposer({
      startError: { message: "Server error.", code: "internal_error" },
      onAddKey: vi.fn(),
    });
    expect(
      screen.queryByTestId("first-run-composer-error-cta"),
    ).toBeNull();
  });
});
