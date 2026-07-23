// Unit tests for the pure `projectLedger` fold (PRD-B1). The cross-language
// parity gate lives in `ledgerProjection.parity.test.ts`; this file pins the
// fold invariants + the URI codec + the tolerate-and-ignore contract.

import { beforeEach, describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import {
  ledgerTabsAsSurfaceTabs,
  projectLedger,
  surfaceIdForTabUri,
  tabUriForSurface,
} from "./ledgerProjection";

const RUN = "a7f3c9d2e5b14f60";

// Reset the per-file sequence counter before each test so `sequence_no` (and the
// `ledgerId` it anchors) starts at 1 in every case.
let seq = 0;
beforeEach(() => {
  seq = 0;
});
function ev(
  event_type: string,
  payload: Record<string, unknown>,
  overrides: Partial<RuntimeEventEnvelope> = {},
): RuntimeEventEnvelope {
  seq += 1;
  return {
    event_id: `evt_${seq}`,
    run_id: RUN,
    conversation_id: "c1",
    sequence_no: seq,
    event_type: event_type as RuntimeEventEnvelope["event_type"],
    activity_kind: "tool" as RuntimeEventEnvelope["activity_kind"],
    payload,
    created_at: "2026-07-23T10:00:00Z",
    ...overrides,
  };
}

function created(
  surface_id: string,
  extra: Record<string, unknown> = {},
): RuntimeEventEnvelope {
  return ev("surface.created", {
    v: 1,
    surface_id,
    kind: "record",
    source: { connector: "linear", op: "get_issue" },
    title: `Title ${surface_id}`,
    payload_ref: `payload/${surface_id}`,
    ...extra,
  });
}

function derived(
  surface_id: string,
  extra: Record<string, unknown> = {},
): RuntimeEventEnvelope {
  return ev("view.derived", {
    v: 1,
    surface_id,
    tier: "generic",
    basis: "registry",
    ...extra,
  });
}

describe("projectLedger — fold invariants", () => {
  it("folds surface.created into a tab", () => {
    const p = projectLedger([created("s1")]);
    expect(p.surfaces.size).toBe(1);
    const s = p.surfaces.get("s1");
    expect(s?.title).toBe("Title s1");
    expect(s?.kind).toBe("record");
    expect(s?.source).toEqual({ connector: "linear", op: "get_issue" });
    expect(s?.viewTier).toBeNull();
    expect(s?.ledgerId).toBe("ra7f·001");
    expect(p.tabs).toHaveLength(1);
    expect(p.lastLedgerSeq).toBe(1);
  });

  it("view.derived bumps lastSeq + sets viewTier and full view state", () => {
    const p = projectLedger([
      created("s1"),
      derived("s1", {
        tier: "shaped",
        basis: "generated",
        spec_ref: "spec/x",
        gen: { model: "gpt-5.4-mini", ms: 820 },
      }),
    ]);
    const s = p.surfaces.get("s1");
    expect(s?.viewTier).toBe("shaped");
    expect(s?.view).toEqual({
      tier: "shaped",
      basis: "generated",
      specRef: "spec/x",
      generatorModel: "gpt-5.4-mini",
    });
    expect(s?.lastSeq).toBe(2);
    expect(s?.createdSeq).toBe(1);
  });

  it("orders tabs by lastSeq desc; same-surface updates never duplicate", () => {
    const c1 = created("s1");
    const c2 = created("s2");
    const d1 = derived("s1"); // touches s1 last → s1 becomes newest
    const p = projectLedger([c1, c2, d1]);
    expect(p.tabs.map((t) => t.surfaceId)).toEqual(["s1", "s2"]);
    expect(p.surfaces.size).toBe(2);
  });

  it("dedupes by event_id (SSE resend yields identical projection)", () => {
    const c = created("s1");
    const once = projectLedger([c]);
    const twice = projectLedger([c, c]);
    expect(twice.surfaces.get("s1")).toEqual(once.surfaces.get("s1"));
    expect(twice.tabs).toEqual(once.tabs);
  });

  it("repeat surface.created upserts title/payloadRef, keeps createdSeq + ledgerId", () => {
    const first = created("s1", { title: "First" });
    const second = created("s1", {
      title: "Refreshed",
      payload_ref: "payload/refreshed",
    });
    // Distinct event_ids so the upsert (not the dedup) is exercised.
    const p = projectLedger([first, second]);
    const s = p.surfaces.get("s1");
    expect(s?.title).toBe("Refreshed");
    expect(s?.payloadRef).toBe("payload/refreshed");
    expect(s?.createdSeq).toBe(first.sequence_no);
    expect(s?.lastSeq).toBe(second.sequence_no);
    expect(s?.ledgerId).toBe("ra7f·001");
  });

  it("re-projection is idempotent (deep-equal)", () => {
    const events = [created("s1"), created("s2"), derived("s1")];
    expect(projectLedger(events)).toEqual(projectLedger(events));
  });

  it("sorts out-of-order events by sequence_no before folding", () => {
    const c = created("s1");
    const d = derived("s1", { tier: "shaped", basis: "generated" });
    const p = projectLedger([d, c]); // reversed input
    expect(p.surfaces.get("s1")?.viewTier).toBe("shaped");
  });
});

describe("projectLedger — tolerate + ignore (adversarial)", () => {
  it("ignores unknown v2 event types (usage.recorded, action.classified)", () => {
    const p = projectLedger([
      ev("usage.recorded", { v: 1, purpose: "run" }),
      created("s1"),
      ev("action.classified", { v: 1, call_id: "c1" }),
    ]);
    expect([...p.surfaces.keys()]).toEqual(["s1"]);
    // watermark still counts EVERY event (parity with the Python fold)
    expect(p.latestSequenceNo).toBe(3);
    // hydration trigger only counts the two v2-surface events
    expect(p.lastLedgerSeq).toBe(2);
  });

  it("drops view.derived for an unknown surface_id without throwing", () => {
    const p = projectLedger([derived("ghost", { tier: "shaped" })]);
    expect(p.surfaces.size).toBe(0);
    expect(p.latestSequenceNo).toBe(1);
  });

  it("skips malformed surface.created (no surface_id) without throwing", () => {
    const bad = ev("surface.created", { v: 1, kind: "record", title: "no id" });
    const p = projectLedger([bad, created("s1")]);
    expect([...p.surfaces.keys()]).toEqual(["s1"]);
  });

  it("skips a non-object payload without throwing", () => {
    const bad = ev("surface.created", {} as Record<string, unknown>);
    // Force a non-object payload past the typed builder.
    (bad as { payload: unknown }).payload = null;
    expect(() => projectLedger([bad])).not.toThrow();
    expect(projectLedger([bad]).surfaces.size).toBe(0);
  });

  it("falls an unknown kind to the raw scheme (tier-3)", () => {
    const p = projectLedger([created("s1", { kind: "wormhole" })]);
    const s = p.surfaces.get("s1");
    expect(s?.kind).toBe("raw");
    expect(tabUriForSurface(s!)).toBe("raw://surfaces-v2/s1");
  });
});

describe("URI codec", () => {
  it("maps call → record and round-trips surfaceId", () => {
    const p = projectLedger([created("s1", { kind: "call" })]);
    const s = p.surfaces.get("s1")!;
    const uri = tabUriForSurface(s);
    expect(uri).toBe("record://surfaces-v2/s1");
    expect(surfaceIdForTabUri(uri)).toBe("s1");
  });

  it("keeps raw/receipt/gate on their own scheme (no adapter → tier-3)", () => {
    for (const kind of ["raw", "receipt", "gate"] as const) {
      const p = projectLedger([created(`s_${kind}`, { kind })]);
      const s = p.surfaces.get(`s_${kind}`)!;
      expect(tabUriForSurface(s)).toBe(`${kind}://surfaces-v2/s_${kind}`);
    }
  });

  it("round-trips surfaceId across every kind", () => {
    for (const kind of [
      "record",
      "message",
      "table",
      "call",
      "raw",
      "receipt",
      "gate",
    ] as const) {
      const p = projectLedger([created("abc-123", { kind })]);
      const s = p.surfaces.get("abc-123")!;
      expect(surfaceIdForTabUri(tabUriForSurface(s))).toBe("abc-123");
    }
  });

  it("returns null for non-surfaces-v2 URIs (v1, garbage)", () => {
    expect(surfaceIdForTabUri("sheet-row://legacy/xyz")).toBeNull();
    expect(surfaceIdForTabUri("record://surfaces-v2/")).toBeNull();
    expect(surfaceIdForTabUri("not a uri")).toBeNull();
    expect(surfaceIdForTabUri("")).toBeNull();
  });
});

describe("ledgerTabsAsSurfaceTabs", () => {
  it("adapts to the SurfaceTab strip shape (uri, archetype, title, lastSeq)", () => {
    const p = projectLedger([created("s1", { kind: "table" }), derived("s1")]);
    const tabs = ledgerTabsAsSurfaceTabs(p);
    expect(tabs).toHaveLength(1);
    expect(tabs[0]).toEqual({
      uri: "table://surfaces-v2/s1",
      archetype: "table",
      title: "Title s1",
      lastSeq: 2,
    });
  });
});
