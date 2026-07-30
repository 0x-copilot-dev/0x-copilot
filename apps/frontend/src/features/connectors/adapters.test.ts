// `applyConnectorEnvelope` — the pure SSE reducer over the connectors list.

import { describe, expect, it } from "vitest";

import type {
  Connector,
  ConnectorId,
  ConnectorSlug,
  ConnectorStreamEnvelope,
  ConnectorStreamEventType,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";

import { applyConnectorEnvelope } from "./adapters";

function connector(overrides: Partial<Connector> = {}): Connector {
  return {
    id: "conn_1" as ConnectorId,
    tenant_id: "tenant_1" as TenantId,
    slug: "gmail" as ConnectorSlug,
    display_name: "Gmail",
    description: "Email",
    status: "connected",
    owner_user_id: "user_1" as UserId,
    access_mode: "read_act",
    scopes: [],
    last_sync_at: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-18T09:00:00Z",
    ...overrides,
  };
}

function envelope(
  event_type: ConnectorStreamEventType,
  conn?: Connector,
): ConnectorStreamEnvelope {
  return {
    event_id: "evt_1",
    sequence_no: 1,
    event_type,
    connector: conn,
    created_at: "2026-05-18T09:00:00Z",
  };
}

describe("applyConnectorEnvelope", () => {
  it("splices a created connector in", () => {
    const incoming = connector({ id: "conn_2" as ConnectorId });
    const next = applyConnectorEnvelope(
      [connector()],
      envelope("connector.created", incoming),
    );
    expect(next.map((c) => c.id)).toEqual(["conn_2", "conn_1"]);
  });

  it("patches a status change in place", () => {
    const next = applyConnectorEnvelope(
      [connector()],
      envelope("connector.status_changed", connector({ status: "expired" })),
    );
    expect(next).toHaveLength(1);
    expect(next[0].status).toBe("expired");
  });

  it("drops the row on connector.removed", () => {
    const items = [connector(), connector({ id: "conn_2" as ConnectorId })];
    const next = applyConnectorEnvelope(
      items,
      envelope("connector.removed", connector()),
    );
    expect(next.map((c) => c.id)).toEqual(["conn_2"]);
  });

  it("does not resurrect a removed row that is already gone", () => {
    // `connector.removed` carries the row it deleted. Falling through to the
    // patch path would splice a deleted tool back onto the list.
    const items = [connector({ id: "conn_2" as ConnectorId })];
    const next = applyConnectorEnvelope(
      items,
      envelope("connector.removed", connector({ id: "conn_1" as ConnectorId })),
    );
    expect(next).toBe(items);
  });

  it("ignores heartbeats and error-threshold events", () => {
    const items = [connector()];
    expect(applyConnectorEnvelope(items, envelope("heartbeat"))).toBe(items);
    expect(
      applyConnectorEnvelope(
        items,
        envelope("connector.error_threshold", connector()),
      ),
    ).toBe(items);
  });
});
