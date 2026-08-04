// <ConnectModal /> — the Tools "Connect a tool" flow (DESIGN-SPEC §5, FR-4.23):
// catalog pick → OAuth spinner → permission (Read only / Read & act) → Connect.
// The host drives OAuth via the `pending` / `error` props; the test flips them
// on rerender to walk the flow forward.

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  ConnectorCatalogEntry,
  ConnectorSlug,
} from "@0x-copilot/api-types";

import { ConnectModal, type ConnectModalProps } from "./ConnectModal";

const CATALOG: readonly ConnectorCatalogEntry[] = [
  {
    slug: "notion" as ConnectorSlug,
    display_name: "Notion",
    description: "Docs & wikis",
  },
  {
    slug: "linear" as ConnectorSlug,
    display_name: "Linear",
    description: "Issues & projects",
  },
];

function renderModal(overrides: Partial<ConnectModalProps> = {}) {
  const onClose = vi.fn();
  const onConnect = vi.fn();
  const onSelectEntry = vi.fn();
  const utils = render(
    <ConnectModal
      open
      onClose={onClose}
      catalog={CATALOG}
      onConnect={onConnect}
      onSelectEntry={onSelectEntry}
      {...overrides}
    />,
  );
  const rerender = (next: Partial<ConnectModalProps> = {}) =>
    utils.rerender(
      <ConnectModal
        open
        onClose={onClose}
        catalog={CATALOG}
        onConnect={onConnect}
        onSelectEntry={onSelectEntry}
        {...next}
      />,
    );
  return { onClose, onConnect, onSelectEntry, rerender };
}

function pickNotion(): void {
  act(() => {
    fireEvent.click(screen.getAllByTestId("connect-catalog-option")[0]);
  });
}

function stepLabel(): string | null {
  return screen.getByTestId("step-dots").getAttribute("aria-label");
}

describe("<ConnectModal>", () => {
  it("does not render when closed", () => {
    renderModal({ open: false });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("lists the generic-SaaS catalog on step 1 of 3", () => {
    renderModal();
    const list = screen.getByTestId("connect-catalog-list");
    expect(within(list).getByText("Notion")).toBeInTheDocument();
    expect(within(list).getByText("Linear")).toBeInTheDocument();
    expect(stepLabel()).toBe("Step 1 of 3");
  });

  it("picking an entry fires onSelectEntry and shows the OAuth spinner (step 2)", () => {
    const { onSelectEntry } = renderModal({ pending: true });
    pickNotion();
    expect(onSelectEntry).toHaveBeenCalledWith("notion");
    expect(screen.getByTestId("connect-oauth")).toHaveAttribute(
      "role",
      "status",
    );
    expect(screen.getByText(/Authorizing with Notion/)).toBeInTheDocument();
    expect(stepLabel()).toBe("Step 2 of 3");
  });

  it("clearing pending advances to the permission choice (step 3)", () => {
    const { rerender } = renderModal({ pending: true });
    pickNotion();
    act(() => rerender({ pending: false }));
    expect(screen.getByTestId("connect-permission")).toHaveAttribute(
      "role",
      "radiogroup",
    );
    expect(stepLabel()).toBe("Step 3 of 3");
  });

  it("Connect fires onConnect with the picked entry and the default read permission", () => {
    const { onConnect, rerender } = renderModal({ pending: true });
    pickNotion();
    act(() => rerender({ pending: false }));
    act(() => fireEvent.click(screen.getByTestId("connect-confirm")));
    expect(onConnect).toHaveBeenCalledWith("notion", "read");
  });

  it("choosing Read & act connects with the read_act permission", () => {
    const { onConnect, rerender } = renderModal({ pending: true });
    pickNotion();
    act(() => rerender({ pending: false }));
    const options = screen.getAllByTestId("connect-permission-option");
    const readAct = options.find(
      (el) => el.getAttribute("data-value") === "read_act",
    )!;
    act(() => fireEvent.click(readAct));
    expect(readAct).toHaveAttribute("aria-checked", "true");
    act(() => fireEvent.click(screen.getByTestId("connect-confirm")));
    expect(onConnect).toHaveBeenCalledWith("notion", "read_act");
  });

  it("an OAuth error renders a role=alert and Retry re-fires onSelectEntry", () => {
    const { onSelectEntry, rerender } = renderModal({ pending: true });
    pickNotion();
    act(() => rerender({ pending: false, error: "window closed" }));
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/window closed/);
    // Still on the OAuth step — no permission choice leaked through.
    expect(screen.queryByTestId("connect-permission")).toBeNull();
    act(() => fireEvent.click(screen.getByTestId("connect-retry")));
    expect(onSelectEntry).toHaveBeenCalledTimes(2);
    expect(onSelectEntry).toHaveBeenLastCalledWith("notion");
  });

  it("Back from the OAuth error returns to the catalog (step 1)", () => {
    const { rerender } = renderModal({ pending: true });
    pickNotion();
    act(() => rerender({ pending: false, error: "denied" }));
    act(() => fireEvent.click(screen.getByTestId("connect-back")));
    expect(screen.getByTestId("connect-catalog-list")).toBeInTheDocument();
    expect(stepLabel()).toBe("Step 1 of 3");
  });

  it("disables Connect while a connect persist is pending", () => {
    const { rerender } = renderModal({ pending: true });
    pickNotion();
    act(() => rerender({ pending: false }));
    // Host flips pending back on while persisting the connection.
    act(() => rerender({ pending: true }));
    const confirm = screen.getByTestId("connect-confirm") as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect(confirm).toHaveTextContent(/Connecting/);
  });

  it("Cancel on the catalog step closes the modal", () => {
    const { onClose } = renderModal();
    act(() => fireEvent.click(screen.getByTestId("connect-cancel")));
    expect(onClose).toHaveBeenCalled();
  });

  // ── PRD-11 D7 — identity tiles + pinned custom row + trust-model copy ─────
  describe("identity tiles + escape hatch", () => {
    it("the header subtitle states the trust model, not a task", () => {
      renderModal();
      expect(
        screen.getByText("the agent acts through your accounts"),
      ).toBeInTheDocument();
    });

    it("each catalog row renders a real per-slug AppIcon tile (not a ◆ glyph)", () => {
      renderModal();
      const option = screen.getAllByTestId("connect-catalog-option")[0];
      // AppIcon always emits the `.ui-app-icon` base class; the neutral tile
      // chrome (`--tile`/`--neutral`) is added by design-system (verified
      // package-locally — the consumer resolves the pre-merge copy here).
      expect(option.querySelector(".ui-app-icon")).not.toBeNull();
      expect(option.textContent).not.toContain("◆");
    });

    it("the escape hatch is the 'Manage MCP' row, not '◆'/'＋'", () => {
      // Renamed from "Custom MCP server": the row opens the whole config as
      // one editable document now, so "custom server" understated it — and the
      // old sub-copy ("paste a JSON config") described a form that only ever
      // took a URL.
      renderModal({ onManageMcp: vi.fn() });
      const custom = screen.getByTestId("connect-catalog-custom");
      expect(custom).toHaveTextContent("Manage MCP");
      expect(custom).toHaveTextContent(/edit the JSON config/i);

      // Pinned, not dashed (PRD-11 D7) — and pinned on the LIST ITEM.
      //
      // This assertion used to read `custom.style.position` (the button's own)
      // and passed over a row that never moved: a sticky box is constrained by
      // its containing block, and the <li> wrapping the button is exactly as
      // tall as the button. Below a dozen catalog entries the escape hatch was
      // simply off the bottom of the modal. Assert the owner, and assert that
      // its containing block is the scrolling list.
      expect(custom.style.position).toBe("");
      const item = custom.closest("li");
      expect(item?.style.position).toBe("sticky");
      expect(item?.parentElement).toBe(
        screen.getByTestId("connect-catalog-list"),
      );
      // Last child, so it pins BELOW the catalog rather than floating over it.
      expect(item?.nextElementSibling).toBeNull();
    });

    it("clicking the pinned row opens Manage MCP", () => {
      const onManageMcp = vi.fn();
      renderModal({ onManageMcp });

      fireEvent.click(screen.getByTestId("connect-catalog-custom"));

      expect(onManageMcp).toHaveBeenCalledTimes(1);
    });

    it("hides the escape hatch unless onManageMcp is supplied", () => {
      // The modal used to own a built-in URL form behind this row; the row is
      // now nothing but the host's Manage MCP entry point, so with no host
      // claiming it there is no row to render.
      renderModal();
      expect(screen.queryByTestId("connect-catalog-custom")).toBeNull();
    });
  });
});

describe("ConnectModal — pre-registered OAuth client step", () => {
  const CATALOG = [
    {
      slug: "atlassian" as ConnectorSlug,
      display_name: "Atlassian",
      description: "Jira issues and Confluence pages.",
    },
  ];

  // The regression this guard exists for: a client-required stop clears BOTH
  // `pending` and `error`, which is exactly the pair the modal reads as
  // "OAuth succeeded". Without the guard it advances to the permission step
  // for a connector that never authorized anything.
  it("does not mistake a client-required stop for OAuth success", () => {
    render(
      <ConnectModal
        open
        onClose={vi.fn()}
        catalog={CATALOG}
        onConnect={vi.fn()}
        onSelectEntry={vi.fn()}
        pending={false}
        error={null}
        clientRequiredSlug={"atlassian" as ConnectorSlug}
        onSubmitOAuthClient={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText("Atlassian"));
    expect(screen.getByTestId("connect-client-form")).toBeInTheDocument();
    expect(screen.queryByTestId("connect-confirm")).toBeNull();
  });

  it("submits the client the user typed", () => {
    const onSubmitOAuthClient = vi.fn();
    render(
      <ConnectModal
        open
        onClose={vi.fn()}
        catalog={CATALOG}
        onConnect={vi.fn()}
        onSelectEntry={vi.fn()}
        clientRequiredSlug={"atlassian" as ConnectorSlug}
        onSubmitOAuthClient={onSubmitOAuthClient}
      />,
    );
    fireEvent.click(screen.getByText("Atlassian"));
    fireEvent.change(screen.getByPlaceholderText("client_id"), {
      target: { value: "my-client" },
    });
    fireEvent.change(screen.getByPlaceholderText("client_secret"), {
      target: { value: "my-secret" },
    });
    fireEvent.submit(screen.getByTestId("connect-client-form"));
    expect(onSubmitOAuthClient).toHaveBeenCalledWith(
      {
        client_id: "my-client",
        client_secret: "my-secret",
        token_endpoint_auth_method: "client_secret_post",
      },
      // Loopback is the default; a provider registered against the fixed
      // deep-link URI is the case the next test covers.
      "loopback",
    );
  });

  // The redirect the client was registered against is not cosmetic: a provider
  // that demands one exact callback URL rejects the varying loopback port
  // outright, so the client alone cannot unblock it.
  it("carries the deep-link redirect choice through to the host", () => {
    const onSubmitOAuthClient = vi.fn();
    render(
      <ConnectModal
        open
        onClose={vi.fn()}
        catalog={CATALOG}
        onConnect={vi.fn()}
        onSelectEntry={vi.fn()}
        clientRequiredSlug={"atlassian" as ConnectorSlug}
        onSubmitOAuthClient={onSubmitOAuthClient}
      />,
    );
    fireEvent.click(screen.getByText("Atlassian"));
    fireEvent.change(screen.getByPlaceholderText("client_id"), {
      target: { value: "my-client" },
    });
    fireEvent.change(screen.getByTestId("connect-client-callback-mode"), {
      target: { value: "deep_link" },
    });
    fireEvent.submit(screen.getByTestId("connect-client-form"));
    expect(onSubmitOAuthClient).toHaveBeenCalledWith(
      expect.objectContaining({ client_id: "my-client" }),
      "deep_link",
    );
  });

  it("refuses an empty client id", () => {
    const onSubmitOAuthClient = vi.fn();
    render(
      <ConnectModal
        open
        onClose={vi.fn()}
        catalog={CATALOG}
        onConnect={vi.fn()}
        onSelectEntry={vi.fn()}
        clientRequiredSlug={"atlassian" as ConnectorSlug}
        onSubmitOAuthClient={onSubmitOAuthClient}
      />,
    );
    fireEvent.click(screen.getByText("Atlassian"));
    fireEvent.submit(screen.getByTestId("connect-client-form"));
    expect(onSubmitOAuthClient).not.toHaveBeenCalled();
    expect(screen.getByTestId("connect-client-error")).toBeInTheDocument();
  });
});

describe("ConnectModal — one flow from row or CTA", () => {
  const CATALOG = [
    {
      slug: "atlassian" as ConnectorSlug,
      display_name: "Atlassian",
      description: "Jira issues and Confluence pages.",
    },
    {
      slug: "linear" as ConnectorSlug,
      display_name: "Linear",
      description: "Issues, projects, and cycles.",
    },
  ];

  // A row connect used to bypass the modal entirely, which silently skipped the
  // access-mode step — so the same action asked less of the user depending on
  // which button they pressed.
  it("an initial entry picks itself and authorizes on open", () => {
    const onSelectEntry = vi.fn();
    render(
      <ConnectModal
        open
        onClose={vi.fn()}
        catalog={CATALOG}
        onConnect={vi.fn()}
        onSelectEntry={onSelectEntry}
        initialEntrySlug={"linear" as ConnectorSlug}
        pending
      />,
    );
    expect(onSelectEntry).toHaveBeenCalledWith("linear");
    // Straight to the OAuth step — the catalog list is not shown again.
    expect(screen.queryByTestId("connect-catalog-list")).toBeNull();
  });

  it("reaches the SAME access-mode step a CTA connect reaches", () => {
    const onConnect = vi.fn();
    const { rerender } = render(
      <ConnectModal
        open
        onClose={vi.fn()}
        catalog={CATALOG}
        onConnect={onConnect}
        onSelectEntry={vi.fn()}
        initialEntrySlug={"linear" as ConnectorSlug}
        pending
      />,
    );
    // Host reports the OAuth round-trip finished.
    rerender(
      <ConnectModal
        open
        onClose={vi.fn()}
        catalog={CATALOG}
        onConnect={onConnect}
        onSelectEntry={vi.fn()}
        initialEntrySlug={"linear" as ConnectorSlug}
        pending={false}
      />,
    );
    const readAct = screen
      .getAllByTestId("connect-permission-option")
      .find((el) => el.getAttribute("data-value") === "read_act")!;
    fireEvent.click(readAct);
    fireEvent.click(screen.getByTestId("connect-confirm"));
    expect(onConnect).toHaveBeenCalledWith("linear", "read_act");
  });

  it("without an initial entry it still opens on the catalog", () => {
    const onSelectEntry = vi.fn();
    render(
      <ConnectModal
        open
        onClose={vi.fn()}
        catalog={CATALOG}
        onConnect={vi.fn()}
        onSelectEntry={onSelectEntry}
      />,
    );
    expect(onSelectEntry).not.toHaveBeenCalled();
    expect(screen.getByText("Atlassian")).toBeInTheDocument();
  });

  it("an unknown initial slug falls back to the catalog, not a blank modal", () => {
    render(
      <ConnectModal
        open
        onClose={vi.fn()}
        catalog={CATALOG}
        onConnect={vi.fn()}
        onSelectEntry={vi.fn()}
        initialEntrySlug={"nope" as ConnectorSlug}
      />,
    );
    expect(screen.getByText("Atlassian")).toBeInTheDocument();
  });
});
