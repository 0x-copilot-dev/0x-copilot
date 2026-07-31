// The folder affordance — PRD-FS-10 §8, tests 1-7.
//
// It moved OUT of the `+` menu and ONTO the composer frame, so the first
// assertion here is an inversion of what this file used to pin: with a grant
// port wired (the case that used to render the row), the menu must no longer
// offer "Attach Folder" at all. Two entry points to one capability is how the
// grant model got muddled; a test that tolerated both would let it back.
//
// Everything is asserted by ACCESSIBLE NAME rather than by test id: if a user
// cannot reach a control by its label, the affordance does not exist for them
// regardless of what the DOM contains.
//
// These drive the REAL `AssistantComposer` composition (its own hook, its own
// gating) rather than rendering `WorkspaceFolderBar` directly — the bar can be
// perfect and still be mounted nowhere, which is the failure mode this whole
// subsystem keeps hitting.

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

const KALEIDOSCOPE: WorkspaceGrant = {
  grantId: "grant_02",
  mount: "m_41ab",
  label: "kaleidoscope",
  mode: "read_only",
};

const NOTES: WorkspaceGrant = {
  grantId: "grant_03",
  mount: "m_77de",
  label: "notes",
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

function renderComposer(
  overrides: Partial<AssistantComposerProps> = {},
): HTMLElement {
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
    // The interesting moment is BEFORE the first send — the surfaces that are
    // pre-first-message by construction (FTUE, the cockpit's empty state) all
    // pass this, so the tests state it too rather than leaning on a default.
    hasSentFirstMessage: false,
    ...overrides,
  };
  const { container } = render(
    <TransportProvider transport={makeTransport()}>
      <AssistantComposer {...props} />
    </TransportProvider>,
  );
  return container;
}

function openPlusMenu(): void {
  fireEvent.click(
    screen.getByRole("button", { name: /Open attachment and tools menu/i }),
  );
}

/** The bar's control, whatever it currently says. */
function folderControl(): HTMLElement | null {
  return (
    screen.queryByRole("button", { name: /Attach a folder/i }) ??
    screen.queryByRole("button", { name: /^(Downloads|kaleidoscope|notes)/i })
  );
}

describe("`+` menu — Attach Folder is gone (test 1)", () => {
  it("does not offer Attach Folder even where the capability EXISTS", async () => {
    // The strong form. Web never had the row; the regression to guard against
    // is the desktop menu keeping it "for discoverability" beside the new bar.
    const port = makeGrantPort({ listGrants: vi.fn(async () => [DOWNLOADS]) });
    renderComposer({ workspaceGrantPort: port });
    await screen.findByRole("button", { name: /^Downloads/ });

    openPlusMenu();

    expect(
      screen.queryByRole("menuitem", { name: /Attach Folder/i }),
    ).toBeNull();
    // The message-attachment rows are untouched — this menu still does its job.
    expect(
      screen.getByRole("menuitem", { name: /Attach Image/i }),
    ).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: /Attach File/i })).toBeTruthy();
  });
});

describe("WorkspaceFolderBar — what it names (tests 2, 4, 6, 7)", () => {
  it("names the folder by BASENAME before the first message (test 2)", async () => {
    const port = makeGrantPort({
      listGrants: vi.fn(async () => [KALEIDOSCOPE]),
    });
    renderComposer({ workspaceGrantPort: port });

    expect(
      await screen.findByRole("button", { name: /^kaleidoscope/ }),
    ).toBeTruthy();
  });

  it("offers the empty affordance with zero grants (test 4)", async () => {
    const port = makeGrantPort();
    renderComposer({ workspaceGrantPort: port });

    // A bar that appeared only once you already had a folder could never teach
    // anyone that folders exist, so the empty state is the point.
    const attach = await screen.findByRole("button", {
      name: /Attach a folder/i,
    });
    fireEvent.click(attach);
    await waitFor(() => expect(port.requestGrant).toHaveBeenCalledTimes(1));
    // No path from this entry point — the host's own picker IS the consent.
    expect(port.requestGrant).toHaveBeenCalledWith(undefined);
  });

  it("renders nothing at all when the host has no port — web (test 6)", () => {
    renderComposer({ workspaceGrantPort: null });
    expect(folderControl()).toBeNull();
    // …and the pill it gates is absent too, not a control that changes nothing.
    expect(
      screen.queryByRole("button", { name: /Execution mode/i }),
    ).toBeNull();
  });

  it("treats an omitted port the same as an explicit null (test 6)", () => {
    renderComposer();
    expect(folderControl()).toBeNull();
  });

  it("puts NO host path in the DOM, whatever the broker sends (test 7)", async () => {
    // The projection is path-free by contract, so the risk is a surface that
    // renders whatever ELSE a future broker field carries. Hand it exactly that:
    // a grant object with an extra host root on it.
    const leaky = {
      ...KALEIDOSCOPE,
      root: "/Users/sarah/Documents/kaleidoscope",
    } as unknown as WorkspaceGrant;
    const port = makeGrantPort({ listGrants: vi.fn(async () => [leaky]) });
    const container = renderComposer({ workspaceGrantPort: port });

    await screen.findByRole("button", { name: /^kaleidoscope/ });
    const rendered = container.textContent ?? "";
    expect(rendered).toContain("kaleidoscope");
    expect(rendered).not.toContain("/Users/");
    expect(container.innerHTML).not.toContain("/Users/");
    // The opaque per-boot mount id is not for a person to read either.
    expect(rendered).not.toContain("m_41ab");
  });
});

describe("WorkspaceFolderBar — several grants", () => {
  it("names the MOST RECENTLY granted folder, plus +N", async () => {
    // The broker returns its own order, and it is not a promise anyone made:
    // `notes` leads the list, but `kaleidoscope` is the folder the user just
    // handed over, so that is the one the bar names.
    const active: WorkspaceGrant[] = [NOTES, DOWNLOADS];
    const port = makeGrantPort({
      listGrants: vi.fn(async () => [...active]),
      requestGrant: vi.fn(async () => {
        active.push(KALEIDOSCOPE);
        return { status: "granted" as const, grant: KALEIDOSCOPE };
      }),
    });
    renderComposer({ workspaceGrantPort: port });

    fireEvent.click(await screen.findByRole("button", { name: /^notes/ }));

    const named = await screen.findByRole("button", { name: /^kaleidoscope/ });
    expect(named.textContent).toContain("kaleidoscope");
    expect(named.textContent).toContain("+2");
  });

  it("revokes the named folder through the port", async () => {
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

    // "Stop sharing", not "Remove" — dismissing this takes access away.
    const remove = await screen.findByRole("button", {
      name: "Stop sharing Downloads with the agent",
    });
    fireEvent.click(remove);

    await waitFor(() => expect(revokeGrant).toHaveBeenCalledWith("grant_01"));
    // The broker is the source of truth: the name goes away because the re-read
    // says the grant is gone, not because we forgot it locally.
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /^Downloads/ })).toBeNull(),
    );
  });
});

describe("WorkspaceFolderBar — cancelled vs failed (test 5)", () => {
  it("leaves the bar unchanged when the user dismisses the picker", async () => {
    const port = makeGrantPort({
      listGrants: vi.fn(async () => [DOWNLOADS]),
      requestGrant: vi.fn(async () => ({ status: "cancelled" as const })),
    });
    renderComposer({ workspaceGrantPort: port });

    fireEvent.click(await screen.findByRole("button", { name: /^Downloads/ }));
    await waitFor(() => expect(port.requestGrant).toHaveBeenCalledTimes(1));

    // Their own decision must not read as an app failure: same folder, no line.
    expect(screen.getByRole("button", { name: /^Downloads/ })).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows the message when the grant FAILS", async () => {
    const port = makeGrantPort({
      listGrants: vi.fn(async () => [DOWNLOADS]),
      requestGrant: vi.fn(async () => ({
        status: "failed" as const,
        message: "That folder is on a disk that is no longer mounted.",
      })),
    });
    renderComposer({ workspaceGrantPort: port });

    fireEvent.click(await screen.findByRole("button", { name: /^Downloads/ }));

    expect(
      await screen.findByText(
        "That folder is on a disk that is no longer mounted.",
      ),
    ).toBeTruthy();
    // The folder that IS known stays named — a failure is never an empty state.
    expect(screen.getByRole("button", { name: /^Downloads/ })).toBeTruthy();
  });

  it("shows a failed list-read instead of silently rendering no folders", async () => {
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

describe("WorkspaceFolderBar — visibility rule (test 3)", () => {
  it("is ABSENT once the chat has sent its first message, grant or not", async () => {
    const port = makeGrantPort({ listGrants: vi.fn(async () => [DOWNLOADS]) });
    renderComposer({ workspaceGrantPort: port, hasSentFirstMessage: true });

    // Give the mount's grant read time to land — the bar's absence has to be
    // the visibility rule, not a race we happened to win.
    await waitFor(() => expect(port.listGrants).toHaveBeenCalled());
    expect(folderControl()).toBeNull();
    expect(screen.queryByText("Downloads")).toBeNull();

    // And the `+` menu is NOT a fallback: the capability is reachable from
    // Settings, never from a re-added row.
    openPlusMenu();
    expect(
      screen.queryByRole("menuitem", { name: /Attach Folder/i }),
    ).toBeNull();
  });

  it("renders the bar before the first message with the same port", async () => {
    // The pair that proves the previous test is about the MOMENT, not the port.
    const port = makeGrantPort({ listGrants: vi.fn(async () => [DOWNLOADS]) });
    renderComposer({ workspaceGrantPort: port, hasSentFirstMessage: false });
    expect(
      await screen.findByRole("button", { name: /^Downloads/ }),
    ).toBeTruthy();
  });
});
