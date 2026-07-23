// ConnectorsDestination — "Tools" relabel, access-mode segment wiring,
// approval-policy note, and reconnect (FR-4.20/4.22/4.24/4.25).

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
  TOOLS_SUBTITLE,
} from "./ConnectorsDestination";
import type { ConnectorAccessPort } from "./ports/ConnectorAccessPort";

type Items = SectionResult<{
  readonly connectors: ReadonlyArray<Connector>;
  readonly available: ReadonlyArray<ConnectorCatalogEntry>;
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

describe("ConnectorsDestination — Tools relabel", () => {
  it("renders the 'Tools' title, subtitle, and region label", () => {
    const { container } = render(<ConnectorsDestination items={makeItems()} />);
    expect(screen.getByTestId("page-header-title")).toHaveTextContent("Tools");
    expect(screen.getByTestId("page-header-subtitle")).toHaveTextContent(
      TOOLS_SUBTITLE,
    );
    expect(
      container.querySelector('[data-component="connectors-destination"]'),
    ).toHaveAttribute("aria-label", "Tools");
  });

  it("'Connect a tool' CTA fires onConnect", () => {
    const onConnect = vi.fn();
    render(<ConnectorsDestination items={makeItems()} onConnect={onConnect} />);
    const cta = screen.getByTestId("page-header-primary-action");
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

  it("does not hardcode Safe/Dune as catalog defaults (FR-4.24)", () => {
    render(<ConnectorsDestination items={makeItems()} />);
    expect(screen.queryByText(/Safe/i)).toBeNull();
    expect(screen.queryByText(/Dune/i)).toBeNull();
  });
});

describe("ConnectorsDestination — access-mode segment (FR-4.21/4.22, PRD-06)", () => {
  it("each connected row renders an AccessModeSegment reflecting its mode", () => {
    render(<ConnectorsDestination items={makeItems()} />);
    const gmail = screen.getByRole("radiogroup", {
      name: "Access mode for Gmail",
    });
    expect(within(gmail).getByRole("radio", { name: "Read" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    // A read_act connector renders its segment with data-value="read_act".
    const notion = screen.getByRole("radiogroup", {
      name: "Access mode for Notion",
    });
    expect(notion).toHaveAttribute("data-testid", "access-mode-segment");
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
    // Starts at read_act.
    expect(notion).toHaveAttribute("data-value", "read_act");
    fireEvent.click(within(notion).getByTestId("access-mode-option-off"));
    expect(setAccessMode).toHaveBeenCalledTimes(1);
    expect(setAccessMode).toHaveBeenCalledWith("conn_notion", "off");
    // Optimistically flips to off before the promise settles.
    expect(notion).toHaveAttribute("data-value", "off");
    // Settle to avoid an unhandled promise.
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
    // Optimistic flip happens first.
    expect(notion).toHaveAttribute("data-value", "off");
    // After rejection it reverts to the server value and the banner renders.
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

describe("ConnectorsDestination — reconnect (FR-4.25)", () => {
  it("renders a Reconnect action for error/expired connectors wired to onReconnect", () => {
    const onReconnect = vi.fn();
    render(
      <ConnectorsDestination items={makeItems()} onReconnect={onReconnect} />,
    );
    const action = screen.getByTestId("connector-card-action");
    expect(action).toHaveTextContent("Reconnect");
    fireEvent.click(action);
    expect(onReconnect).toHaveBeenCalledWith("conn_notion");
  });
});
