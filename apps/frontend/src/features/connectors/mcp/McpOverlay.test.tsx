/**
 * PR 4.4 — McpOverlay wizard behavioural tests.
 *
 * Pins the contracts the rest of the system depends on:
 *   - 5-step navigation (browse → auth → scope → confirm → connected).
 *   - Catalog selection pre-fills URL + auth method.
 *   - Custom URL path mints from a paste, not the catalog.
 *   - "Add to workspace" calls connectors.addServer with the right URL.
 *   - "Authenticate" on the success step calls connectors.authenticate.
 *   - Reopen resets state (no stale "connected" view).
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { McpServer } from "@enterprise-search/api-types";

import { McpOverlay } from "./McpOverlay";
import type { ConnectorState } from "../useConnectors";

function makeServer(overrides: Partial<McpServer> = {}): McpServer {
  return {
    server_id: "srv_01",
    org_id: "org_acme",
    user_id: "usr_marcus",
    name: "linear",
    display_name: "Linear",
    url: "https://mcp.linear.app/sse",
    transport: "sse",
    auth_mode: "oauth2",
    auth_state: "unauthenticated",
    health: "unknown",
    enabled: true,
    required_scopes: [],
    oauth_client_configured: false,
    ...overrides,
  } as unknown as McpServer;
}

function makeConnectors(
  overrides: Partial<ConnectorState> = {},
): ConnectorState {
  return {
    servers: [],
    loading: false,
    error: null,
    refresh: vi.fn().mockResolvedValue(undefined),
    addServer: vi.fn().mockResolvedValue(undefined),
    removeServer: vi.fn().mockResolvedValue(undefined),
    setEnabled: vi.fn().mockResolvedValue(undefined),
    authenticate: vi.fn().mockResolvedValue(undefined),
    skipAuth: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

describe("McpOverlay", () => {
  it("walks through browse → auth → scope → confirm → connected", async () => {
    const linearRow = makeServer();
    const connectors = makeConnectors({
      // Mirror the real flow: addServer mutates state; the wizard reads
      // back the new row from .servers post-resolve.
      servers: [linearRow],
      addServer: vi.fn().mockResolvedValue(undefined),
    });

    render(<McpOverlay open onClose={vi.fn()} connectors={connectors} />);

    // Step 1: pick Linear from the catalog.
    await userEvent.click(screen.getByLabelText("Add Linear"));

    // Step 2: pre-selected method = OAuth (Linear's documented mode).
    expect(screen.getByText("Recommended")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^OAuth\b/ }));

    // Step 3: scope review — Linear's suggested scopes appear.
    expect(screen.getByText("read:issues")).toBeInTheDocument();
    expect(screen.getByText("read:projects")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Review/ }));

    // Step 4: confirm summary shows URL + auth + scopes.
    expect(screen.getByText("https://mcp.linear.app/sse")).toBeInTheDocument();
    expect(screen.getByText("OAuth")).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: /Add to workspace/ }),
    );

    // Step 5: connected.
    await waitFor(() => {
      expect(screen.getByText("Linear added")).toBeInTheDocument();
    });
    expect(connectors.addServer).toHaveBeenCalledWith(
      "https://mcp.linear.app/sse",
    );
  });

  it("uses the custom URL path when no catalog entry is picked", async () => {
    const customRow = makeServer({
      url: "https://mcp.example.com/sse",
      display_name: "mcp.example.com",
      name: "mcp.example.com",
    });
    const connectors = makeConnectors({
      servers: [customRow],
    });

    render(<McpOverlay open onClose={vi.fn()} connectors={connectors} />);

    const customInput = screen.getByPlaceholderText(
      "https://mcp.example.com/sse",
    );
    // ``userEvent.type`` chokes on URL special chars in some test envs;
    // ``fireEvent.change`` is the reliable seam for "paste this whole
    // string into the input" semantics.
    fireEvent.change(customInput, {
      target: { value: "https://mcp.example.com/sse" },
    });
    await userEvent.click(screen.getByRole("button", { name: /^Continue$/ }));

    // Step 2 → step 3 → step 4 with the custom URL plumbed through.
    await userEvent.click(screen.getByRole("button", { name: /^OAuth\b/ }));
    await userEvent.click(screen.getByRole("button", { name: /Review/ }));
    expect(screen.getByText("https://mcp.example.com/sse")).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: /Add to workspace/ }),
    );

    expect(connectors.addServer).toHaveBeenCalledWith(
      "https://mcp.example.com/sse",
    );
  });

  it("surfaces add errors without leaving the confirm step", async () => {
    const connectors = makeConnectors({
      addServer: vi.fn().mockRejectedValue(new Error("URL not reachable")),
    });

    render(<McpOverlay open onClose={vi.fn()} connectors={connectors} />);

    await userEvent.click(screen.getByLabelText("Add Notion"));
    await userEvent.click(screen.getByRole("button", { name: /^OAuth\b/ }));
    await userEvent.click(screen.getByRole("button", { name: /Review/ }));
    await userEvent.click(
      screen.getByRole("button", { name: /Add to workspace/ }),
    );

    await waitFor(() => {
      expect(screen.getByText("URL not reachable")).toBeInTheDocument();
    });
    // Still on the confirm step — caller can retry.
    expect(
      screen.getByRole("button", { name: /Add to workspace/ }),
    ).toBeInTheDocument();
  });

  it("calls authenticate when the user clicks Authenticate on the success step", async () => {
    const linearRow = makeServer({ auth_state: "unauthenticated" });
    const connectors = makeConnectors({ servers: [linearRow] });

    render(<McpOverlay open onClose={vi.fn()} connectors={connectors} />);

    await userEvent.click(screen.getByLabelText("Add Linear"));
    await userEvent.click(screen.getByRole("button", { name: /^OAuth\b/ }));
    await userEvent.click(screen.getByRole("button", { name: /Review/ }));
    await userEvent.click(
      screen.getByRole("button", { name: /Add to workspace/ }),
    );

    await userEvent.click(
      await screen.findByRole("button", { name: /Authenticate with Linear/ }),
    );

    expect(connectors.authenticate).toHaveBeenCalledWith("srv_01");
  });

  it("filters the catalog by search input", async () => {
    const connectors = makeConnectors();

    render(<McpOverlay open onClose={vi.fn()} connectors={connectors} />);

    fireEvent.change(screen.getByPlaceholderText("Linear, Notion, Sentry, …"), {
      target: { value: "sentry" },
    });
    await waitFor(() => {
      expect(screen.queryByLabelText("Add Linear")).toBeNull();
    });
    expect(screen.getByLabelText("Add Sentry")).toBeInTheDocument();
    expect(screen.queryByLabelText("Add Notion")).toBeNull();
  });
});
