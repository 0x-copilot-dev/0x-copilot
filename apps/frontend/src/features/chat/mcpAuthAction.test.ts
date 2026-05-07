import { describe, expect, it } from "vitest";
import { runIdFromMcpAuthApprovalId } from "./mcpAuthAction";

describe("runIdFromMcpAuthApprovalId", () => {
  it("extracts the run id from an mcp_auth approval id", () => {
    expect(runIdFromMcpAuthApprovalId("mcp_auth:run_42:server_xyz")).toBe(
      "run_42",
    );
  });

  // PR 4.4.7 Phase 2 — discovery cards use ``mcp_discovery:`` instead
  // of ``mcp_auth:``. The pending-action store needs the runId to
  // route the user back to the same chat after OAuth, so the parser
  // must accept both prefixes. Without this, the catalog 1-click flow
  // ends up on settings/connectors instead of the original chat.
  it("extracts the run id from an mcp_discovery approval id", () => {
    expect(runIdFromMcpAuthApprovalId("mcp_discovery:run_42:seed:linear")).toBe(
      "run_42",
    );
  });

  it("returns null for an unknown prefix", () => {
    expect(runIdFromMcpAuthApprovalId("approval:run_42:foo")).toBeNull();
  });

  it("returns null for a malformed approval id", () => {
    expect(runIdFromMcpAuthApprovalId("mcp_auth")).toBeNull();
    expect(runIdFromMcpAuthApprovalId("mcp_discovery:")).toBeNull();
    expect(runIdFromMcpAuthApprovalId("mcp_auth::server")).toBeNull();
  });
});
