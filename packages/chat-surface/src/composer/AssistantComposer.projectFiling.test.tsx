// The below-frame project zone — where the chat BELONGS.
//
// The design rule these tests exist to hold: what the agent can REACH sits
// ABOVE the composer frame (the folder bar — many folders, chat-owned), where
// the work BELONGS sits BELOW it (one project, or none). Capability points up,
// filing points down. So the assertions are about PLACEMENT: outside the frame,
// after it, and never inside the control row (a tenth control there is exactly
// what the two-zone design avoids).
//
// The zone is asserted through a plain marker node rather than the real
// `<ProjectFilingChip>`: what `AssistantComposer` owns is WHERE a filing slot
// renders and WHETHER it renders at all, and a test that mounted the chip would
// fail for the chip's reasons too.
//
// The load-bearing case is the third one — NO `workspaceGrantPort`. The stack
// wrapper used to be returned only when the folder bar was visible, so a naive
// "render the slot inside the stack" edit drops the zone on WEB, which has no
// grant port and therefore never a bar. That is the regression this file is
// here to prevent.

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

/** A host that HAS the folder capability — what gates the bar above the frame. */
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

/** Stands in for `<ProjectFilingChip>`; carries the class the zone CSS hangs off. */
const FILING_SLOT: ReactNode = (
  <div className="aui-composer-filing" data-testid="filing-slot">
    filed under
  </div>
);

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

function frameOf(container: HTMLElement): HTMLElement {
  const frame = container.querySelector<HTMLElement>(".aui-composer");
  expect(frame).not.toBeNull();
  return frame as HTMLElement;
}

describe("AssistantComposer — the project filing zone", () => {
  it("renders the slot BELOW the frame, as its sibling", () => {
    const container = renderComposer({ projectFilingSlot: FILING_SLOT });

    const frame = frameOf(container);
    const slot = screen.getByTestId("filing-slot");
    const stack = container.querySelector(".aui-composer-stack");

    // Outside the frame, not inside it: the zone is a satellite of the
    // composer, so the frame's border must not enclose it.
    expect(frame.contains(slot)).toBe(false);
    expect(slot.parentElement).toBe(stack);
    expect(stack?.contains(frame)).toBe(true);
    // …and AFTER it. Filing points DOWN; the folder bar is the thing that
    // points up.
    expect(orderOf(container, slot)).toBeGreaterThan(orderOf(container, frame));
  });

  it("keeps the slot out of the control row", () => {
    // The row is full ( + · connectors · Tools · bypass · depth · model · mic ·
    // send ) and a tenth control in it is what the two-zone design avoids.
    const container = renderComposer({ projectFilingSlot: FILING_SLOT });

    const row = container.querySelector(".aui-composer-action-wrapper");
    const slot = screen.getByTestId("filing-slot");
    expect(row).not.toBeNull();
    expect(row?.contains(slot)).toBe(false);
    expect(orderOf(container, slot)).toBeGreaterThan(
      orderOf(container, row as Element),
    );
  });

  it("renders with NO workspaceGrantPort — the web case", () => {
    // THE regression guard. Web supplies no grant port, so it never gets a
    // folder bar; a zone that only mounted alongside a bar would be invisible
    // on the surface it was built for.
    const container = renderComposer({
      workspaceGrantPort: null,
      projectFilingSlot: FILING_SLOT,
    });

    const slot = screen.getByTestId("filing-slot");
    expect(slot).toBeInTheDocument();
    // No bar, in either of its two forms.
    expect(container.querySelector(".aui-folder-bar")).toBeNull();
    expect(
      screen.queryByRole("button", { name: /Attach a folder/i }),
    ).toBeNull();
    // The frame is the FIRST child of the stack here, which is the premise the
    // `.aui-folder-bar + .aui-composer` margin reset is scoped on: with no bar
    // above it, the frame keeps its own top margin.
    const stack = container.querySelector(".aui-composer-stack");
    expect(stack?.firstElementChild).toBe(frameOf(container));
  });

  it("treats an omitted port the same as an explicit null", () => {
    renderComposer({ projectFilingSlot: FILING_SLOT });
    expect(screen.getByTestId("filing-slot")).toBeInTheDocument();
  });

  it("renders together with the folder bar, one above and one below", async () => {
    const port = makeGrantPort({ listGrants: vi.fn(async () => [DOWNLOADS]) });
    const container = renderComposer({
      workspaceGrantPort: port,
      // The bar is a capability AND a moment — it shows only before the first
      // message. Filing has no such moment, but this is the case where both are
      // on screen at once, which is the one the layout has to survive.
      hasSentFirstMessage: false,
      projectFilingSlot: FILING_SLOT,
    });

    const bar = await screen.findByRole("button", { name: /^Downloads/ });
    const frame = frameOf(container);
    const slot = screen.getByTestId("filing-slot");

    const positions = [bar, frame, slot].map((el) => orderOf(container, el));
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
    // Stated separately so a failure names the rule it broke.
    expect(orderOf(container, bar)).toBeLessThan(orderOf(container, frame));
    expect(orderOf(container, slot)).toBeGreaterThan(orderOf(container, frame));
  });

  it("changes nothing when no slot is passed", async () => {
    const container = renderComposer();

    // The bare frame is returned, NOT a stack — every mount with no satellite
    // is byte-for-byte what it was before filing existed.
    expect(container.querySelector(".aui-composer-stack")).toBeNull();
    expect(container.querySelector(".aui-composer-filing")).toBeNull();
    expect(frameOf(container).parentElement).toBe(container);
    // The composer itself is untouched.
    expect(
      await screen.findByRole("button", { name: /Send message/i }),
    ).toBeInTheDocument();
  });

  it("treats a null slot as absent, adding no empty row", () => {
    // The natural binder shape is `projects.length > 0 ? <chip/> : null`, so a
    // host with nothing to file into must land on the bare frame — not on a
    // stack holding an empty zone that still takes the gap.
    const container = renderComposer({ projectFilingSlot: null });

    expect(container.querySelector(".aui-composer-stack")).toBeNull();
    expect(frameOf(container).parentElement).toBe(container);
  });
});
