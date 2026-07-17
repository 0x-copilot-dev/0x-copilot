// ConnectorDetailView — tab nav + admin gating + skeleton.

import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  AgentId,
  Connector,
  ConnectorDetailResponse,
  ConnectorId,
  ConnectorSlug,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";

import { RouterProvider } from "../../providers/RouterProvider";
import {
  __resetItemRefRegistryForTests,
  registerItemRefResolver,
} from "../../refs/registry";
import type { ArtifactRoute, Router } from "../../routing/router";

import { ConnectorDetailView } from "./ConnectorDetailView";

afterEach(() => {
  __resetItemRefRegistryForTests();
});

const NOW = Date.parse("2026-05-17T12:00:00.000Z");

const noopRouter: Router<ArtifactRoute> = {
  current: () => ({ kind: "chat", conversationId: "x" }) as ArtifactRoute,
  navigate: () => undefined,
  subscribe: () => () => undefined,
};

function makeConnector(): Connector {
  return {
    id: "conn_1" as ConnectorId,
    tenant_id: "tnt_1" as TenantId,
    slug: "gmail" as ConnectorSlug,
    display_name: "Gmail",
    description: "Read Gmail threads and labels.",
    status: "connected",
    owner_user_id: "user_1" as UserId,
    scopes: [
      { scope: "gmail.readonly", granted: true, description: "Read mail" },
      { scope: "gmail.modify", granted: false, description: "Modify mail" },
    ],
    last_sync_at: "2026-05-17T11:50:00.000Z",
    created_at: "2026-05-15T10:00:00.000Z",
    updated_at: "2026-05-17T11:50:00.000Z",
  };
}

function makeDetail(): ConnectorDetailResponse {
  return {
    connector: makeConnector(),
    consumers: {
      agents: [],
      tools: [],
      projects: [],
      chats_with_grant: 0,
    },
  };
}

function renderInProvider(ui: React.ReactElement): void {
  render(<RouterProvider router={noopRouter}>{ui}</RouterProvider>);
}

describe("ConnectorDetailView", () => {
  it("renders a skeleton when detail is null", () => {
    renderInProvider(
      <ConnectorDetailView detail={null} isAdmin={false} now={NOW} />,
    );
    expect(
      screen.getByTestId("connector-detail-view-skeleton"),
    ).toBeInTheDocument();
  });

  it("renders the connector name, status pill, and last-sync line", () => {
    renderInProvider(
      <ConnectorDetailView detail={makeDetail()} isAdmin={false} now={NOW} />,
    );
    expect(screen.getByTestId("connector-detail-name")).toHaveTextContent(
      "Gmail",
    );
    expect(screen.getByTestId("connector-detail-slug")).toHaveTextContent(
      "gmail",
    );
    expect(screen.getByTestId("connector-detail-last-sync")).toHaveTextContent(
      /Last sync/,
    );
  });

  it("exposes a tablist with five tabs", () => {
    renderInProvider(
      <ConnectorDetailView detail={makeDetail()} isAdmin={false} now={NOW} />,
    );
    const list = screen.getByRole("tablist", { name: "Connector detail" });
    expect(list).toBeInTheDocument();
    for (const id of ["overview", "scope", "consumers", "audit", "settings"]) {
      expect(
        screen.getByTestId(`connector-detail-tab-${id}`),
      ).toBeInTheDocument();
    }
  });

  it("switches to the Scope tab on click", () => {
    renderInProvider(
      <ConnectorDetailView detail={makeDetail()} isAdmin={false} now={NOW} />,
    );
    fireEvent.click(screen.getByTestId("connector-detail-tab-scope"));
    expect(
      screen.getByTestId("connector-detail-tabpanel-scope"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("connector-scope-review")).toBeInTheDocument();
  });

  it("admin gate: non-admin Audit panel shows the explainer", () => {
    renderInProvider(
      <ConnectorDetailView detail={makeDetail()} isAdmin={false} now={NOW} />,
    );
    fireEvent.click(screen.getByTestId("connector-detail-tab-audit"));
    expect(
      screen.getByTestId("connector-audit-admin-empty"),
    ).toBeInTheDocument();
  });

  it("admin gate: admin Audit panel renders rows when entries exist", () => {
    registerItemRefResolver("agent", async () => ({
      label: "Agent",
      icon: null,
      route: { kind: "chat", conversationId: "x" } as ArtifactRoute,
    }));
    renderInProvider(
      <ConnectorDetailView
        detail={makeDetail()}
        isAdmin={true}
        now={NOW}
        auditEntries={[
          {
            id: "evt_1",
            connector_id: "conn_1" as ConnectorId,
            tenant_id: "tnt_1" as TenantId,
            ts: "2026-05-17T11:00:00.000Z",
            caller: { kind: "agent", id: "agent_1" as AgentId },
            endpoint: "GET /v1/threads",
            bytes_read: 1024,
            status: "ok",
          },
        ]}
      />,
    );
    fireEvent.click(screen.getByTestId("connector-detail-tab-audit"));
    expect(screen.getByTestId("connector-audit-table")).toBeInTheDocument();
    expect(screen.getAllByTestId("connector-audit-row").length).toBe(1);
  });

  it("fires onDisconnect from the Settings tab", () => {
    const onDisconnect = vi.fn();
    renderInProvider(
      <ConnectorDetailView
        detail={makeDetail()}
        isAdmin={false}
        now={NOW}
        onDisconnect={onDisconnect}
      />,
    );
    fireEvent.click(screen.getByTestId("connector-detail-tab-settings"));
    fireEvent.click(screen.getByTestId("connector-detail-disconnect"));
    expect(onDisconnect).toHaveBeenCalledTimes(1);
  });
});
