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
