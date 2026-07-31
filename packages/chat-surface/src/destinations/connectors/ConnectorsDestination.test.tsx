// ConnectorsDestination (Tools) — row-list migration, identity tile,
// access-mode segment wiring, approval-policy note, reconnect (PRD-11).

import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  Connector,
  ConnectorCatalogEntry,
  ConnectorId,
  ConnectorSlug,
  SectionResult,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";

import {
  ConnectorsDestination,
  TOOLS_POLICY_NOTE_COPY,
} from "./ConnectorsDestination";
import type { ConnectorAccessPort } from "./ports/ConnectorAccessPort";

type Items = SectionResult<{
  readonly connectors: ReadonlyArray<Connector>;
  readonly available: ReadonlyArray<unknown>;
}>;

function makeConnector(
  over: Partial<Connector> & Pick<Connector, "id">,
): Connector {
  return {
    tenant_id: "tnt_1" as TenantId,
    slug: "gmail" as ConnectorSlug,
    display_name: "Gmail",
    description: "Read Gmail threads and labels.",
    status: "connected",
    access_mode: "read",
    owner_user_id: "user_1" as UserId,
    scopes: [],
    last_sync_at: null,
    created_at: "2026-05-15T10:00:00.000Z",
    updated_at: "2026-05-17T11:50:00.000Z",
    ...over,
  };
}

function makeItems(): Items {
  return {
    status: "ok",
    data: {
      connectors: [
        makeConnector({
          id: "conn_gmail" as ConnectorId,
          display_name: "Gmail",
          slug: "gmail" as ConnectorSlug,
          access_mode: "read",
          status: "connected",
        }),
        makeConnector({
          id: "conn_slack" as ConnectorId,
          display_name: "Slack",
          slug: "slack" as ConnectorSlug,
          access_mode: "off",
          status: "connected",
        }),
        makeConnector({
          id: "conn_notion" as ConnectorId,
          display_name: "Notion",
          slug: "notion" as ConnectorSlug,
          access_mode: "read_act",
          status: "expired",
        }),
      ],
      available: [],
    },
  };
}

describe("ConnectorsDestination — Tools row list", () => {
  it("renders the section header eyebrow, region label, and NO page title", () => {
    const { container } = render(<ConnectorsDestination items={makeItems()} />);
    // Mono eyebrow `Connected · N`, not a 22px page <h1>.
    expect(screen.getByTestId("section-header-label")).toHaveTextContent(
      "Connected · 3",
    );
    expect(screen.queryByTestId("page-header-title")).toBeNull();
    expect(screen.queryByTestId("filter-tabs")).toBeNull();
    expect(
      container.querySelector('[data-component="connectors-destination"]'),
    ).toHaveAttribute("aria-label", "Tools");
  });

  it("lays connectors out as a single hairline RowList (not a card grid)", () => {
    render(<ConnectorsDestination items={makeItems()} />);
    expect(screen.getByTestId("row-list")).toBeInTheDocument();
    expect(screen.queryByTestId("card-grid")).toBeNull();
    expect(screen.getAllByTestId("connector-row")).toHaveLength(3);
  });

  // ── PRD-11 DoD 3 — the missing-tile regression guard ─────────────────────
  it("renders the default identity tile WITHOUT a renderIcon prop (DoD 3)", () => {
    const items: Items = {
      status: "ok",
      data: {
        connectors: [
          makeConnector({
            id: "conn_gmail" as ConnectorId,
            slug: "gmail" as ConnectorSlug,
            display_name: "Gmail",
          }),
        ],
        available: [],
      },
    };
    // No `renderIcon` — this was the exact defect: renderIcon was bound by
    // neither host, so no tile ever rendered. The destination now defaults to
    // an <AppIcon size="tile">.
    const { container } = render(<ConnectorsDestination items={items} />);
    expect(container.querySelector(".ui-app-icon--tile")).not.toBeNull();
  });

  it("'Connect a tool' CTA fires onConnect", () => {
    const onConnect = vi.fn();
    render(<ConnectorsDestination items={makeItems()} onConnect={onConnect} />);
    const cta = screen.getByTestId("connectors-connect-cta");
    expect(cta).toHaveTextContent("Connect a tool");
    fireEvent.click(cta);
    expect(onConnect).toHaveBeenCalledTimes(1);
  });

  it("renders the approval-policy note pointing at Settings → Model & behavior", () => {
    render(<ConnectorsDestination items={makeItems()} />);
    expect(screen.getByTestId("tools-policy-note")).toHaveTextContent(
      TOOLS_POLICY_NOTE_COPY,
    );
  });

  it("the policy note is a link firing onOpenApprovalSettings when wired", () => {
    const onOpenApprovalSettings = vi.fn();
    render(
      <ConnectorsDestination
        items={makeItems()}
        onOpenApprovalSettings={onOpenApprovalSettings}
      />,
    );
    fireEvent.click(screen.getByTestId("tools-policy-note-link"));
    expect(onOpenApprovalSettings).toHaveBeenCalledTimes(1);
  });

  it("moves the Webhooks pivot into the header action slot", () => {
    const onOpenWebhooks = vi.fn();
    render(
      <ConnectorsDestination
        items={makeItems()}
        onOpenWebhooks={onOpenWebhooks}
      />,
    );
    fireEvent.click(screen.getByTestId("connectors-webhooks"));
    expect(onOpenWebhooks).toHaveBeenCalledTimes(1);
  });
});

describe("ConnectorsDestination — access-mode segment (PRD-06)", () => {
  it("each connected row renders an AccessModeSegment reflecting its mode", () => {
    render(<ConnectorsDestination items={makeItems()} />);
    const gmail = screen.getByRole("radiogroup", {
      name: "Access mode for Gmail",
    });
    expect(within(gmail).getByRole("radio", { name: "Read" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    const notion = screen.getByRole("radiogroup", {
      name: "Access mode for Notion",
    });
    expect(notion).toHaveAttribute("data-value", "read_act");
  });

  it("clicking an option calls accessPort.setAccessMode once + optimistically flips", () => {
    let resolve!: (c: Connector) => void;
    const setAccessMode = vi.fn(
      () =>
        new Promise<Connector>((r) => {
          resolve = r;
        }),
    );
    const accessPort: ConnectorAccessPort = { setAccessMode };
    render(
      <ConnectorsDestination items={makeItems()} accessPort={accessPort} />,
    );
    const notion = screen.getByRole("radiogroup", {
      name: "Access mode for Notion",
    });
    expect(notion).toHaveAttribute("data-value", "read_act");
    fireEvent.click(within(notion).getByTestId("access-mode-option-off"));
    expect(setAccessMode).toHaveBeenCalledTimes(1);
    expect(setAccessMode).toHaveBeenCalledWith("conn_notion", "off");
    expect(notion).toHaveAttribute("data-value", "off");
    act(() => {
      resolve(
        makeConnector({ id: "conn_notion" as ConnectorId, access_mode: "off" }),
      );
    });
  });

  it("reverts to the server mode + renders the error banner on a rejected PATCH", async () => {
    const setAccessMode = vi.fn(() => Promise.reject(new Error("boom")));
    const accessPort: ConnectorAccessPort = { setAccessMode };
    render(
      <ConnectorsDestination items={makeItems()} accessPort={accessPort} />,
    );
    const notion = screen.getByRole("radiogroup", {
      name: "Access mode for Notion",
    });
    fireEvent.click(within(notion).getByTestId("access-mode-option-off"));
    expect(notion).toHaveAttribute("data-value", "off");
    await waitFor(() => {
      expect(
        screen.getByRole("radiogroup", { name: "Access mode for Notion" }),
      ).toHaveAttribute("data-value", "read_act");
    });
    expect(
      screen.getByTestId("connectors-access-mode-error"),
    ).toBeInTheDocument();
  });
});

describe("ConnectorsDestination — chip + reconnect", () => {
  it("renders a status chip ONLY on non-connected rows", () => {
    render(<ConnectorsDestination items={makeItems()} />);
    // 3 rows; only the expired Notion row carries a chip.
    const chips = screen.getAllByTestId("status-pill");
    expect(chips).toHaveLength(1);
    expect(chips[0]).toHaveTextContent(/re-auth/i);
  });

  it("renders a Reconnect action for error/expired connectors wired to onReconnect", () => {
    const onReconnect = vi.fn();
    render(
      <ConnectorsDestination items={makeItems()} onReconnect={onReconnect} />,
    );
    const action = screen.getByTestId("connector-reconnect");
    expect(action).toHaveTextContent("Reconnect");
    fireEvent.click(action);
    expect(onReconnect).toHaveBeenCalledWith("conn_notion");
  });
});

describe("ConnectorsDestination — states", () => {
  it("renders a loading skeleton when items is null", () => {
    render(<ConnectorsDestination items={null} />);
    expect(screen.getByTestId("connectors-skeleton")).toBeInTheDocument();
  });

  it("renders an error EmptyState with a retry action", () => {
    const onRetry = vi.fn();
    render(
      <ConnectorsDestination
        items={{ status: "error", error: "boom" }}
        onRetry={onRetry}
      />,
    );
    const retry = screen.getByRole("button", { name: /retry/i });
    fireEvent.click(retry);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("renders the empty-connected EmptyState with a Connect CTA", () => {
    const onConnect = vi.fn();
    render(
      <ConnectorsDestination
        items={{ status: "ok", data: { connectors: [], available: [] } }}
        onConnect={onConnect}
      />,
    );
    // Both the header CTA and the EmptyState action read "Connect a tool";
    // target the EmptyState action specifically.
    fireEvent.click(screen.getByTestId("empty-state-action"));
    expect(onConnect).toHaveBeenCalledTimes(1);
  });
});

describe("ConnectorsDestination — remove confirmation", () => {
  it("renders no Remove affordance when the host wires no handler", () => {
    render(<ConnectorsDestination items={makeItems()} />);
    expect(screen.queryByTestId("connector-remove")).toBeNull();
  });

  it("opens a confirmation dialog instead of removing on the first click", () => {
    const onRemove = vi.fn();
    render(<ConnectorsDestination items={makeItems()} onRemove={onRemove} />);

    fireEvent.click(screen.getByRole("button", { name: "Remove Gmail" }));

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Remove Gmail?")).toBeInTheDocument();
    // The whole point: one click asks, it does not delete.
    expect(onRemove).not.toHaveBeenCalled();
  });

  it("removes the connector the dialog named once confirmed", () => {
    const onRemove = vi.fn();
    render(<ConnectorsDestination items={makeItems()} onRemove={onRemove} />);

    fireEvent.click(screen.getByRole("button", { name: "Remove Slack" }));
    fireEvent.click(screen.getByTestId("connector-remove-confirm"));

    expect(onRemove).toHaveBeenCalledWith("conn_slack");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("cancels without removing", () => {
    const onRemove = vi.fn();
    render(<ConnectorsDestination items={makeItems()} onRemove={onRemove} />);

    fireEvent.click(screen.getByRole("button", { name: "Remove Gmail" }));
    fireEvent.click(screen.getByTestId("connector-remove-cancel"));

    expect(onRemove).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("dismisses on Escape without removing", () => {
    const onRemove = vi.fn();
    render(<ConnectorsDestination items={makeItems()} onRemove={onRemove} />);

    fireEvent.click(screen.getByRole("button", { name: "Remove Gmail" }));
    fireEvent.keyDown(screen.getByTestId("settings-modal-scrim"), {
      key: "Escape",
    });

    expect(onRemove).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("ConnectorsDestination — catalog row availability", () => {
  const EMPTY: Items = {
    status: "ok",
    data: { connectors: [], available: [] },
  };

  function catalogEntry(
    over: Partial<ConnectorCatalogEntry> & Pick<ConnectorCatalogEntry, "slug">,
  ): ConnectorCatalogEntry {
    return {
      display_name: "Atlassian",
      description: "Jira issues and Confluence pages.",
      ...over,
    };
  }

  it("offers Connect only for an available entry", () => {
    const onConnectEntry = vi.fn();
    render(
      <ConnectorsDestination
        items={EMPTY}
        catalog={[
          catalogEntry({
            slug: "atlassian" as ConnectorSlug,
            availability: "available",
          }),
        ]}
        onConnectEntry={onConnectEntry}
      />,
    );
    fireEvent.click(
      screen.getByTestId("connector-available-connect-atlassian"),
    );
    expect(onConnectEntry).toHaveBeenCalledWith("atlassian");
  });

  // The regression this whole change exists for: a preview / admin-setup /
  // coming-soon row used to render an identical Connect that the backend
  // refuses before a browser ever opens.
  it.each([
    ["preview", "Preview"],
    ["admin_setup_required", "Admin setup"],
    ["coming_soon", "Coming soon"],
  ] as const)(
    "renders a %s row as a chip with no Connect button",
    (availability, label) => {
      const onConnectEntry = vi.fn();
      render(
        <ConnectorsDestination
          items={EMPTY}
          catalog={[
            catalogEntry({ slug: "gmail" as ConnectorSlug, availability }),
          ]}
          onConnectEntry={onConnectEntry}
        />,
      );
      const row = screen.getByTestId("connector-available-row");
      expect(within(row).queryByRole("button")).toBeNull();
      expect(within(row).getByTestId("status-pill")).toHaveTextContent(label);
    },
  );

  it("keeps an entry with no availability field connectable", () => {
    // Older/web payloads omit `availability`. Missing evidence must not be
    // read as "unavailable".
    const onConnectEntry = vi.fn();
    render(
      <ConnectorsDestination
        items={EMPTY}
        catalog={[catalogEntry({ slug: "linear" as ConnectorSlug })]}
        onConnectEntry={onConnectEntry}
      />,
    );
    fireEvent.click(screen.getByTestId("connector-available-connect-linear"));
    expect(onConnectEntry).toHaveBeenCalledWith("linear");
  });

  it("shows the server's availability_reason in the sub-line", () => {
    render(
      <ConnectorsDestination
        items={EMPTY}
        catalog={[
          catalogEntry({
            slug: "slack" as ConnectorSlug,
            description: "Channels, DMs, and threads.",
            availability: "coming_soon",
            availability_reason: "Not yet available.",
          }),
        ]}
        onConnectEntry={vi.fn()}
      />,
    );
    expect(screen.getByTestId("connector-available-row")).toHaveTextContent(
      "Channels, DMs, and threads. — Not yet available.",
    );
  });
});

describe("ConnectorsDestination — connect feedback", () => {
  const EMPTY: Items = {
    status: "ok",
    data: { connectors: [], available: [] },
  };
  const CATALOG: ReadonlyArray<ConnectorCatalogEntry> = [
    {
      slug: "atlassian" as ConnectorSlug,
      display_name: "Atlassian",
      description: "Jira issues and Confluence pages.",
      availability: "available",
    },
    {
      slug: "linear" as ConnectorSlug,
      display_name: "Linear",
      description: "Issues, projects, and cycles.",
      availability: "available",
    },
  ];

  it("marks only the connecting row pending", () => {
    render(
      <ConnectorsDestination
        items={EMPTY}
        catalog={CATALOG}
        connectingSlug={"atlassian" as ConnectorSlug}
        onConnectEntry={vi.fn()}
      />,
    );
    const pending = screen.getByTestId("connector-available-connect-atlassian");
    expect(pending).toHaveTextContent("Connecting…");
    expect(pending).toBeDisabled();
    const other = screen.getByTestId("connector-available-connect-linear");
    expect(other).toHaveTextContent("Connect");
    expect(other).not.toBeDisabled();
  });

  // Before this, `flow.error` was rendered ONLY inside <ConnectModal>, which a
  // row-initiated connect never opens — so the failure was invisible.
  it("renders a connect failure as an inline alert", () => {
    render(
      <ConnectorsDestination
        items={EMPTY}
        catalog={CATALOG}
        connectError="This connector isn’t set up for sign-in yet."
        onConnectEntry={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "This connector isn’t set up for sign-in yet.",
    );
  });

  it("renders no alert when there is no error", () => {
    render(
      <ConnectorsDestination
        items={EMPTY}
        catalog={CATALOG}
        onConnectEntry={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("connectors-connect-error")).toBeNull();
  });
});
