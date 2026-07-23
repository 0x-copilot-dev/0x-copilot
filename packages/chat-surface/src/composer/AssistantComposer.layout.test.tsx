// v3 composer parity — the bottom row's SHAPE (punch-list rows 4-9, 16-22).
//
// These lock the owner's rulings against the design-parity findings for
// tools/design-parity/surfaces/composer:
//
//   row 4  bottom-row order is [+] [tools] [model] … [mic] [send]
//   row 5  no divider between the icon cluster and the pill cluster
//   row 6+7 NO static hint row at all (neither the host's nor Composer's
//          built-in fallback), while the transient "/" slash cue survives
//   row 8  the "+" is a drawn 14px <svg>, not the text character "+"
//   row 9  send is a drawn 14px <svg>, not the text character "↑"
//
// Metrics (sizes, radii, colours) are CSS and are verified by the parity
// harness, not here — these assert the DOM contract the CSS hangs off.

import { fireEvent, render, screen } from "@testing-library/react";
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

const models: Array<ModelCatalogModel & { disabled?: boolean }> = [
  {
    id: "openai/gpt-5.4",
    provider: "openai",
    model_name: "gpt-5.4",
    name: "GPT-5.4",
    description: "Default fast model",
    configured: true,
  },
];

function renderComposer(
  overrides: Partial<AssistantComposerProps> = {},
): HTMLElement {
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
    models,
    selectedModel: "openai/gpt-5.4",
    onModelChange: vi.fn(),
    toolsTrigger: <button data-testid="tools-trigger">Tools</button>,
    ...overrides,
  };
  const { container } = render(
    <TransportProvider transport={makeTransport()}>
      <AssistantComposer {...props} />
    </TransportProvider>,
  );
  return container;
}

/** Document order index — lower means "earlier in the DOM". */
function orderOf(container: HTMLElement, el: Element): number {
  return Array.from(container.querySelectorAll("*")).indexOf(el);
}

describe("AssistantComposer bottom row (v3 parity)", () => {
  it("orders the row [+] → tools → model … mic → send (row 4)", () => {
    const container = renderComposer();

    const plus = screen.getByRole("button", {
      name: /Open attachment and tools menu/i,
    });
    const tools = screen.getByTestId("tools-trigger");
    const model = screen.getByRole("button", { name: /Model: GPT-5\.4/ });
    const mic = screen.getByRole("button", { name: /Voice input/i });
    const send = screen.getByRole("button", { name: /Send message/i });

    const positions = [plus, tools, model, mic, send].map((el) =>
      orderOf(container, el),
    );
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
  });

  it("puts mic + send in the right cluster, everything else on the left (row 4/7)", () => {
    const container = renderComposer();

    const left = container.querySelector(".aui-composer-tools");
    const right = container.querySelector(
      ".aui-composer-action-wrapper__right",
    );
    expect(left).not.toBeNull();
    expect(right).not.toBeNull();

    const mic = screen.getByRole("button", { name: /Voice input/i });
    const send = screen.getByRole("button", { name: /Send message/i });
    expect(right?.contains(mic)).toBe(true);
    expect(right?.contains(send)).toBe(true);

    const plus = screen.getByRole("button", {
      name: /Open attachment and tools menu/i,
    });
    const model = screen.getByRole("button", { name: /Model: GPT-5\.4/ });
    expect(left?.contains(plus)).toBe(true);
    expect(left?.contains(model)).toBe(true);
  });

  it("renders no tools divider (row 5)", () => {
    const container = renderComposer();
    expect(container.querySelector(".aui-composer-tools-spacer")).toBeNull();
  });

  it("renders no static hint row — host's or Composer's fallback (row 6+7)", () => {
    const container = renderComposer();
    // The host hint element…
    expect(container.querySelector(".aui-composer__hint")).toBeNull();
    // …the slot wrapper Composer would emit for a non-null hintRender…
    expect(screen.queryByTestId("composer-hint-slot")).toBeNull();
    // …and Composer's OWN built-in `↵ send · ⇧+↵ new line · / skills` row,
    // which reappears if `hintRender` is ever dropped instead of nulled.
    expect(screen.queryByTestId("composer-hint")).toBeNull();
    expect(screen.queryByText(/Sources cited inline/)).toBeNull();
  });

  it("still shows the transient slash cue while typing '/' (row 6+7 carve-out)", () => {
    const container = renderComposer({ onOpenSkillsPanel: vi.fn() });
    fireEvent.keyDown(screen.getByRole("textbox", { name: /Message/i }), {
      key: "/",
    });
    expect(container.querySelector(".aui-composer-slash-cue")).not.toBeNull();
  });

  it("draws the + as an svg, not the text character (row 8)", () => {
    renderComposer();
    const plus = screen.getByRole("button", {
      name: /Open attachment and tools menu/i,
    });
    expect(plus.querySelector("svg")).not.toBeNull();
    expect(plus.textContent).toBe("");
    expect(plus.className).toContain("ui-cicon");
  });

  it("draws send as an svg, not the ↑ character (row 9/16/17)", () => {
    renderComposer();
    const send = screen.getByRole("button", { name: /Send message/i });
    expect(send.querySelector("svg")).not.toBeNull();
    expect(send.textContent).toBe("");
    expect(send.className).toContain("ui-csend");
  });

  it("swaps send for the stop control while a run is in flight", () => {
    renderComposer({ running: true, onCancel: vi.fn() });
    expect(screen.queryByRole("button", { name: /Send message/i })).toBeNull();
    expect(
      screen.getByRole("button", { name: /Stop response/i }),
    ).toBeInTheDocument();
  });
});
