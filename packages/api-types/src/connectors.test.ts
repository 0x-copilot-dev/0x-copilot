// @vitest-environment node
import { describe, expect, it } from "vitest";

import type { ConnectorId, TenantId, UserId } from "./brands";
import {
  CONNECTOR_ACCESS_MODES,
  type Connector,
  type ConnectorAccessMode,
  type SetConnectorAccessModeRequest,
} from "./connectors";
import type { ConnectorSlug } from "./projects";

// Runtime assertions over the per-connector access-mode contract (desktop
// redesign, Phase 4 — Tools destination). The mode tuple is the runtime
// SSOT the `ConnectorAccessMode` union derives from; `access_mode` is an
// OPTIONAL, backward-compatible addition to `Connector` (FR-4.21/4.22/4.33).

describe("ConnectorAccessMode — per-connector access union", () => {
  it("is exactly read / read_act / off, in order", () => {
    expect([...CONNECTOR_ACCESS_MODES]).toEqual(["read", "read_act", "off"]);
  });

  it("has no duplicate members", () => {
    expect(new Set(CONNECTOR_ACCESS_MODES).size).toBe(
      CONNECTOR_ACCESS_MODES.length,
    );
  });
});

// Minimal connector fixture; every required field present so the literal
// type-checks against `Connector` (the src typecheck enforces the shape).
function baseConnector(): Connector {
  return {
    id: "conn_001" as ConnectorId,
    tenant_id: "tenant_001" as TenantId,
    slug: "notion" as ConnectorSlug,
    display_name: "Notion",
    description: "Docs and databases.",
    status: "connected",
    access_mode: "read",
    owner_user_id: "user_001" as UserId,
    scopes: [],
    last_sync_at: null,
    created_at: "2026-07-18T00:00:00Z",
    updated_at: "2026-07-18T00:00:00Z",
  };
}

describe("Connector.access_mode — required (PRD-06)", () => {
  it("carries a mode drawn from the access-mode tuple", () => {
    const connector: Connector = {
      ...baseConnector(),
      access_mode: "read_act",
    };
    expect(CONNECTOR_ACCESS_MODES).toContain(connector.access_mode);
  });

  it("is a type error to omit access_mode from a Connector literal", () => {
    // @ts-expect-error — access_mode is REQUIRED; omitting it must not typecheck.
    const connector: Connector = {
      id: "conn_002" as ConnectorId,
      tenant_id: "tenant_001" as TenantId,
      slug: "gmail" as ConnectorSlug,
      display_name: "Gmail",
      description: "",
      status: "connected",
      owner_user_id: "user_001" as UserId,
      scopes: [],
      last_sync_at: null,
      created_at: "2026-07-18T00:00:00Z",
      updated_at: "2026-07-18T00:00:00Z",
    };
    expect(connector.id).toBe("conn_002");
  });
});

describe("SetConnectorAccessModeRequest — PATCH body", () => {
  it("carries a single access_mode field", () => {
    const body: SetConnectorAccessModeRequest = { access_mode: "off" };
    expect(Object.keys(body)).toEqual(["access_mode"]);
  });

  it("accepts every member of the mode union", () => {
    for (const mode of CONNECTOR_ACCESS_MODES) {
      const body: SetConnectorAccessModeRequest = {
        access_mode: mode as ConnectorAccessMode,
      };
      expect(body.access_mode).toBe(mode);
    }
  });
});
