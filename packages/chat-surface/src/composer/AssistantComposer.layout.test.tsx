// v3 composer parity — the bottom row's SHAPE (punch-list rows 4-9, 16-22),
// plus the PRD-FS-10 §4.2 reorder (test 8).
//
// These lock the owner's rulings against the design-parity findings for
// tools/design-parity/surfaces/composer:
//
//   row 4  bottom-row order is [+] … [model] [mic] [send]; per-run tools live
//          inside the `+` menu rather than as a second composer control.
//          PRD-FS-10 moved the model pill OUT of the left cluster: execution
//          mode (bypass) takes the slot right of Tools because it is the
//          decision a user re-makes per task, while model choice is
//          set-and-forget. Model still sits LEFT of the mic — mic and send are
//          the trailing pair and nothing goes between them.
//   row 5  no divider between the icon cluster and the pill cluster
//   row 6+7 NO static hint row at all (neither the host's nor Composer's
//          built-in fallback), while the transient "/" slash cue survives
//   row 8  the "+" is a drawn 14px <svg>, not the text character "+"
//   row 9  send is a drawn 14px <svg>, not the text character "↑"
//
// Metrics (sizes, radii, colours) are CSS and are verified by the parity
// harness, not here — these assert the DOM contract the CSS hangs off.

import { act, fireEvent, render, screen } from "@testing-library/react";
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
import type { DictationCallbacks, DictationPort } from "../ports/DictationPort";
import type { FilePickerPort } from "../ports/FilePickerPort";
import type {
  WorkspaceGrant,
  WorkspaceGrantPort,
} from "../ports/WorkspaceGrantPort";
import {
  AssistantComposer,
  type AssistantComposerProps,
} from "./AssistantComposer";
import { BypassPill } from "./BypassPill";

/** A host that HAS the folder capability — what gates the bypass pill. */
function makeGrantPort(): WorkspaceGrantPort {
  return {
    requestGrant: vi.fn(async () => ({ status: "cancelled" as const })),
    listGrants: vi.fn(async () => [] as ReadonlyArray<WorkspaceGrant>),
    revokeGrant: vi.fn(async () => ({ status: "revoked" as const })),
  };
}

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
  it("orders the row [+] → model → mic → send (row 4)", () => {
    const container = renderComposer();

    const plus = screen.getByRole("button", {
      name: /Open attachment and tools menu/i,
    });
    const model = screen.getByRole("button", { name: /Model: GPT-5\.4/ });
    const mic = screen.getByRole("button", { name: /Voice input/i });
    const send = screen.getByRole("button", { name: /Send message/i });

    const positions = [plus, model, mic, send].map((el) =>
      orderOf(container, el),
    );
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
  });

  // PRD-FS-10 §8 test 8. The order is the product decision, so it is asserted
  // on the RENDERED document rather than on which cluster div a control landed
  // in: a future layout change may move the boxes, but bypass must stay left of
  // the mic and the model must stay between them.
  it("orders [+] → bypass → model → mic → send (PRD-FS-10 §4.2)", async () => {
    // The pill arrives through the host-owned `bypassTrigger` slot (PRD-FS-11
    // moved the mount out of this component — see `BypassPill.tsx`), but WHERE
    // the slot renders is still this component's decision, which is what this
    // asserts. `workspaceGrantPort` is the capability gate on that slot.
    const container = renderComposer({
      workspaceGrantPort: makeGrantPort(),
      hasSentFirstMessage: true,
      bypassTrigger: (
        <BypassPill mode="manual" enabled onChange={() => undefined} />
      ),
    });

    const plus = screen.getByRole("button", {
      name: /Open attachment and tools menu/i,
    });
    const bypass = await screen.findByRole("button", {
      name: /Execution mode/i,
    });
    const model = screen.getByRole("button", { name: /Model: GPT-5\.4/ });
    const mic = screen.getByRole("button", { name: /Voice input/i });
    const send = screen.getByRole("button", { name: /Send message/i });

    const positions = [plus, bypass, model, mic, send].map((el) =>
      orderOf(container, el),
    );
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
    // Stated separately so a failure names the rule it broke.
    expect(orderOf(container, bypass)).toBeLessThan(orderOf(container, mic));
    expect(orderOf(container, model)).toBeLessThan(orderOf(container, mic));
    expect(orderOf(container, bypass)).toBeLessThan(orderOf(container, model));
  });

  it("puts model + mic + send in the right cluster, the rest on the left (row 4/7)", () => {
    const container = renderComposer();

    const left = container.querySelector(".aui-composer-tools");
    const right = container.querySelector(
      ".aui-composer-action-wrapper__right",
    );
    expect(left).not.toBeNull();
    expect(right).not.toBeNull();

    const mic = screen.getByRole("button", { name: /Voice input/i });
    const send = screen.getByRole("button", { name: /Send message/i });
    const model = screen.getByRole("button", { name: /Model: GPT-5\.4/ });
    expect(right?.contains(mic)).toBe(true);
    expect(right?.contains(send)).toBe(true);
    // Moved here from the left cluster (PRD-FS-10 §4.2) — and it is the one
    // control in this cluster that may shrink, which composer.css relies on.
    expect(right?.contains(model)).toBe(true);

    const plus = screen.getByRole("button", {
      name: /Open attachment and tools menu/i,
    });
    expect(left?.contains(plus)).toBe(true);
    expect(left?.contains(model)).toBe(false);
  });

  it("wires the host dictation port into the microphone control", () => {
    let callbacks: DictationCallbacks | null = null;
    const stop = vi.fn();
    const dictationPort: DictationPort = {
      start: vi.fn((next) => {
        callbacks = next;
        return { stop, cancel: vi.fn() };
      }),
    };
    renderComposer({ dictationPort });

    const mic = screen.getByRole("button", { name: "Voice input" });
    expect(mic).not.toBeDisabled();
    fireEvent.click(mic);
    expect(dictationPort.start).toHaveBeenCalledTimes(1);
    act(() => callbacks?.onStart());
    expect(
      screen.getByRole("button", { name: "Stop voice input" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.getByTestId("assistant-composer-dictation-status"),
    ).toHaveTextContent("Listening");

    fireEvent.click(screen.getByRole("button", { name: "Stop voice input" }));
    expect(stop).toHaveBeenCalledTimes(1);
  });

  // The row's outermost controls align their tooltips inward. A centred
  // tooltip on the flush-right send button hangs past the composer — and in the
  // Run cockpit's chat column that overflow is what turned the whole column
  // (transcript included) into a horizontal scroller.
  it("aligns the edge controls' tooltips inward", () => {
    renderComposer();
    expect(
      screen.getByRole("button", { name: /Open attachment and tools menu/i }),
    ).toHaveAttribute("data-tooltip-align", "start");
    expect(
      screen.getByRole("button", { name: /Voice input/i }),
    ).toHaveAttribute("data-tooltip-align", "end");
    expect(
      screen.getByRole("button", { name: /Send message/i }),
    ).toHaveAttribute("data-tooltip-align", "end");
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
