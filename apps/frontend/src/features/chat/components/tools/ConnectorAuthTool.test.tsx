import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type React from "react";
import { describe, expect, it, vi } from "vitest";
import { ConnectorAuthTool } from "./ConnectorAuthTool";

function renderConnectorAuth(
  args: Record<string, unknown>,
  result?: unknown,
  extra: Partial<React.ComponentProps<typeof ConnectorAuthTool>> = {},
) {
  const onConnect = vi.fn().mockResolvedValue(undefined);
  const onSkip = vi.fn().mockResolvedValue(undefined);
  const onInstallCatalog = vi.fn();
  const onMuteCatalogSuggestion = vi.fn();
  const resume = vi.fn();
  const props = {
    args,
    argsText: "",
    result,
    status: { type: "requires-action", reason: "interrupt" },
    isError: false,
    toolCallId: "connector-1",
    toolName: "mcp_auth_required",
    resume,
    onConnect,
    onSkip,
    onInstallCatalog,
    onMuteCatalogSuggestion,
    ...extra,
  } as unknown as React.ComponentProps<typeof ConnectorAuthTool>;
  const utils = render(<ConnectorAuthTool {...props} />);
  return {
    ...utils,
    onConnect,
    onSkip,
    onInstallCatalog,
    onMuteCatalogSuggestion,
    resume,
  };
}

describe("ConnectorAuthTool", () => {
  it("renders Connect and Not now actions while pending", () => {
    renderConnectorAuth({
      server_id: "slack",
      approval_id: "approval-1",
      display_name: "Slack",
    });
    expect(
      screen.getByRole("button", { name: /^connect$/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /not now/i }),
    ).toBeInTheDocument();
  });
  it("calls onConnect when Connect is clicked", () => {
    const { onConnect } = renderConnectorAuth({
      server_id: "slack",
      approval_id: "approval-9",
      display_name: "Slack",
    });
    fireEvent.click(screen.getByRole("button", { name: /^connect$/i }));
    expect(onConnect).toHaveBeenCalledWith({
      approvalId: "approval-9",
      serverId: "slack",
    });
  });

  // PR 3.3 — non-blocking discovery variant.
  describe("discovery variant (PR 3.3)", () => {
    it("renders Connect/Skip buttons when discovery_reason is set", () => {
      renderConnectorAuth({
        server_id: "linear",
        approval_id: "mcp_discovery:run_1:linear",
        display_name: "Linear",
        discovery_reason: "fetch ticket statuses",
        expected_value: "ground claims about ticket progress",
      });
      // Discovery variant uses "Skip" instead of "Not now".
      expect(
        screen.getByRole("button", { name: /^skip$/i }),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: /not now/i }),
      ).not.toBeInTheDocument();
      // Status pill reads "Suggested" — not the blocking
      // "Waiting for permission" copy.
      expect(screen.getByText(/suggested/i)).toBeInTheDocument();
    });

    it("uses the discovery title and expected_value description", () => {
      renderConnectorAuth({
        server_id: "linear",
        approval_id: "mcp_discovery:run_1:linear",
        display_name: "Linear",
        discovery_reason: "fetch ticket statuses",
        expected_value: "ground claims about ticket progress",
      });
      expect(screen.getByText(/connect linear\?/i)).toBeInTheDocument();
      expect(
        screen.getByText(/ground claims about ticket progress/i),
      ).toBeInTheDocument();
    });

    it("Skip records the discovery reason in the resume payload", async () => {
      const { resume, onSkip } = renderConnectorAuth({
        server_id: "linear",
        approval_id: "mcp_discovery:run_1:linear",
        display_name: "Linear",
        discovery_reason: "fetch ticket statuses",
        expected_value: "ground claims",
      });
      fireEvent.click(screen.getByRole("button", { name: /^skip$/i }));
      // ``submit("skip")`` awaits ``onSkip`` before calling ``resume``.
      await waitFor(() => {
        expect(onSkip).toHaveBeenCalledWith({
          approvalId: "mcp_discovery:run_1:linear",
          serverId: "linear",
        });
      });
      await waitFor(() => {
        expect(resume).toHaveBeenCalledWith(
          expect.objectContaining({
            approval_id: "mcp_discovery:run_1:linear",
            decision: "rejected",
            reason: "mcp_discovery_skipped",
          }),
        );
      });
    });

    it("blocking variant does not include the discovery reason on Skip", async () => {
      const { resume } = renderConnectorAuth({
        server_id: "salesforce",
        approval_id: "salesforce-1",
        display_name: "Salesforce",
      });
      fireEvent.click(screen.getByRole("button", { name: /not now/i }));
      await waitFor(() => {
        expect(resume).toHaveBeenCalledWith(
          expect.not.objectContaining({ reason: "mcp_discovery_skipped" }),
        );
      });
    });
  });

  // PR 4.4.7 Phase 2 (Slice C) — catalog suggestion variant. Same
  // discovery card chrome, but the Connect button routes to the
  // McpOverlay deep-link (not OAuth) and the Skip button mutes the
  // suggestion via the user's preferences.
  describe("catalog suggestion variant (PR 4.4.7)", () => {
    it("routes Connect to onInstallCatalog with requiresPreRegisteredClient=false (1-click)", async () => {
      const { onInstallCatalog, onConnect } = renderConnectorAuth({
        server_id: "seed:linear",
        approval_id: "mcp_discovery:run_1:seed:linear",
        display_name: "Linear",
        discovery_reason: "fetch ticket statuses",
        expected_value: "ground claims",
        catalog_slug: "linear",
        requires_pre_registered_client: false,
      });
      fireEvent.click(screen.getByRole("button", { name: /^connect$/i }));
      await waitFor(() => {
        expect(onInstallCatalog).toHaveBeenCalledWith({
          slug: "linear",
          requiresPreRegisteredClient: false,
          // approvalId / serverId are forwarded so the host can stash
          // the pending action under the post-install ``server_id``
          // before redirecting to OAuth — without that, the post-OAuth
          // callback sees no pending action and routes the user to
          // settings instead of back into chat.
          approvalId: "mcp_discovery:run_1:seed:linear",
          serverId: "seed:linear",
        });
      });
      // Catalog branch must NOT call the OAuth-start path — that
      // would target a server row that doesn't exist yet.
      expect(onConnect).not.toHaveBeenCalled();
    });

    it("forwards requires_pre_registered_client=true so the host opens the credentials form", async () => {
      const { onInstallCatalog } = renderConnectorAuth({
        server_id: "seed:atlassian",
        approval_id: "mcp_discovery:run_1:seed:atlassian",
        display_name: "Atlassian",
        discovery_reason: "fetch jira issues",
        expected_value: "ground claims",
        catalog_slug: "atlassian",
        requires_pre_registered_client: true,
      });
      fireEvent.click(screen.getByRole("button", { name: /^connect$/i }));
      await waitFor(() => {
        expect(onInstallCatalog).toHaveBeenCalledWith({
          slug: "atlassian",
          requiresPreRegisteredClient: true,
          approvalId: "mcp_discovery:run_1:seed:atlassian",
          serverId: "seed:atlassian",
        });
      });
    });

    it("defaults requires_pre_registered_client to false when the field is absent", async () => {
      // Backwards-compat for ai-backend payloads that predate the
      // new field. The 1-click branch is the safe default — at worst
      // it falls through to the credentials form via the OAuth
      // setup-required error classifier.
      const { onInstallCatalog } = renderConnectorAuth({
        server_id: "seed:linear",
        approval_id: "mcp_discovery:run_1:seed:linear",
        display_name: "Linear",
        discovery_reason: "fetch ticket statuses",
        expected_value: "ground claims",
        catalog_slug: "linear",
      });
      fireEvent.click(screen.getByRole("button", { name: /^connect$/i }));
      await waitFor(() => {
        expect(onInstallCatalog).toHaveBeenCalledWith({
          slug: "linear",
          requiresPreRegisteredClient: false,
          approvalId: "mcp_discovery:run_1:seed:linear",
          serverId: "seed:linear",
        });
      });
    });

    it("falls back to onConnect when catalog_slug is absent", async () => {
      // Same discovery payload, no catalog_slug — the user already has
      // the connector installed; OAuth is the right next step.
      const { onInstallCatalog, onConnect } = renderConnectorAuth({
        server_id: "seed:linear",
        approval_id: "mcp_discovery:run_1:seed:linear",
        display_name: "Linear",
        discovery_reason: "fetch ticket statuses",
        expected_value: "ground claims",
      });
      fireEvent.click(screen.getByRole("button", { name: /^connect$/i }));
      await waitFor(() => {
        expect(onConnect).toHaveBeenCalledWith({
          approvalId: "mcp_discovery:run_1:seed:linear",
          serverId: "seed:linear",
        });
      });
      expect(onInstallCatalog).not.toHaveBeenCalled();
    });

    it("Skip on a catalog suggestion mutes the slug permanently", async () => {
      const { onSkip, onMuteCatalogSuggestion } = renderConnectorAuth({
        server_id: "seed:linear",
        approval_id: "mcp_discovery:run_1:seed:linear",
        display_name: "Linear",
        discovery_reason: "fetch ticket statuses",
        expected_value: "ground claims",
        catalog_slug: "linear",
      });
      fireEvent.click(screen.getByRole("button", { name: /^skip$/i }));
      // Standard discovery skip still fires (audit chain stays
      // consistent across blocking + catalog variants).
      await waitFor(() => {
        expect(onSkip).toHaveBeenCalledOnce();
      });
      // Plus the new mute call so the agent never re-suggests Linear.
      await waitFor(() => {
        expect(onMuteCatalogSuggestion).toHaveBeenCalledWith({
          slug: "linear",
        });
      });
    });

    it("Skip on a non-catalog discovery does NOT call mute", async () => {
      // The user already has the connector installed (no catalog_slug).
      // Skip means "don't auth right now" — not "mute future
      // suggestions for an uninstalled vendor" — so the mute side
      // effect must not fire.
      const { onMuteCatalogSuggestion } = renderConnectorAuth({
        server_id: "seed:linear",
        approval_id: "mcp_discovery:run_1:seed:linear",
        display_name: "Linear",
        discovery_reason: "fetch ticket statuses",
        expected_value: "ground claims",
      });
      fireEvent.click(screen.getByRole("button", { name: /^skip$/i }));
      await waitFor(() => {
        // give submit() time to settle
      });
      expect(onMuteCatalogSuggestion).not.toHaveBeenCalled();
    });
  });
});
