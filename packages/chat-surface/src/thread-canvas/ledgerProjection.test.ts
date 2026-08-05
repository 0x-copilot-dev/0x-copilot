// Unit tests for the pure `projectLedger` fold (PRD-B1). The cross-language
// parity gate lives in `ledgerProjection.parity.test.ts`; this file pins the
// fold invariants + mount/tab identity + the tolerate-and-ignore contract.

import type { ReactElement } from "react";
import { beforeEach, describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import {
  clearRegistry,
  registerAdapter,
  resolveAdapter,
  surfaceHueForUri,
  type SaaSRendererAdapter,
} from "../surfaces";
import { ledgerTabsAsSurfaceTabs, projectLedger } from "./ledgerProjection";

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
      preference: null,
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

  it("falls an unknown kind to raw (tier-3)", () => {
    const p = projectLedger([created("s1", { kind: "wormhole" })]);
    expect(p.surfaces.get("s1")?.kind).toBe("raw");
  });
});

// The projector's own id shape: `<archetype>://<connector>/<tool>/<identifier>`
// (`SurfaceProjector._build_uri`). Every identity test below uses a real one,
// because the whole point of the change is that this string travels intact.
const TABLE_ID = "table://incidents/list_incidents/1532a206699e";
const DOC_ID = "doc://notion/get_page/9f2c11";

describe("mount/tab identity — the URI IS the surface id", () => {
  it("carries the surface id through to the tab strip unchanged", () => {
    const p = projectLedger([
      created(TABLE_ID, { kind: "table" }),
      derived(TABLE_ID),
    ]);
    const tabs = ledgerTabsAsSurfaceTabs(p);
    expect(tabs).toHaveLength(1);
    expect(tabs[0]).toEqual({
      uri: TABLE_ID,
      archetype: "table",
      title: `Title ${TABLE_ID}`,
      lastSeq: 2,
    });
    // The round trip that used to run through a codec is now the identity.
    expect(p.surfaces.get(tabs[0].uri)?.surfaceId).toBe(TABLE_ID);
  });

  it("reads the archetype off the id, not off the lossier ledger kind", () => {
    // `SurfaceKind` has no `doc`: the projector's `doc` archetype folds to
    // `record` on the ledger. Deriving the mount scheme from `kind` therefore
    // routed a doc surface to the record renderer; the id knows better.
    const p = projectLedger([created(DOC_ID, { kind: "record" })]);
    const [tab] = ledgerTabsAsSurfaceTabs(p);
    expect(tab.uri).toBe(DOC_ID);
    expect(tab.archetype).toBe("doc");
  });

  it("falls back to `kind` for a surface id that is not a URI", () => {
    // Defensive: `surface_id` is untrusted wire input. A bare id still yields a
    // usable archetype rather than an empty scheme.
    const p = projectLedger([created("s1", { kind: "message" })]);
    const [tab] = ledgerTabsAsSurfaceTabs(p);
    expect(tab.uri).toBe("s1");
    expect(tab.archetype).toBe("message");
  });

  it("keeps every id verbatim across every kind (no rewriting, no marker)", () => {
    for (const kind of [
      "record",
      "message",
      "table",
      "call",
      "raw",
      "receipt",
      "gate",
    ] as const) {
      const id = `${kind}://acme/op_${kind}/abc-123`;
      const p = projectLedger([created(id, { kind })]);
      const [tab] = ledgerTabsAsSurfaceTabs(p);
      expect(tab.uri).toBe(id);
      expect(tab.uri).not.toContain("surfaces-v2");
    }
  });

  it("agrees with surfaceHueForUri — one URI, one identity colour", () => {
    // The tab strip and the surface card read the SAME string, so the hue the
    // strip shows and the hue the card shows cannot drift.
    const p = projectLedger([created(TABLE_ID, { kind: "table" })]);
    const [tab] = ledgerTabsAsSurfaceTabs(p);
    expect(surfaceHueForUri(tab.uri)).toBe("jade");
    expect(surfaceHueForUri(DOC_ID)).toBe("violet");
  });
});

describe("mount/tab identity — adapter resolution by scheme", () => {
  beforeEach(() => {
    clearRegistry();
  });

  function stubAdapter(scheme: string): SaaSRendererAdapter {
    return {
      scheme,
      matches: (uri: string) => uri.startsWith(`${scheme}://`),
      renderCurrent: () => null as unknown as ReactElement,
      renderDiff: () => null as unknown as ReactElement,
      metadata: { origin: "first-party", schemaVersion: 1 },
    };
  }

  it("resolves the archetype adapter straight off a projector-minted id", () => {
    // The registry matches on the text before `://`, and a surface id already
    // starts with its archetype — so removing the outer wrapper cannot break
    // resolution. This is the assertion that proves it rather than assuming it.
    const table = stubAdapter("table");
    registerAdapter(table);
    const p = projectLedger([created(TABLE_ID, { kind: "table" })]);
    const [tab] = ledgerTabsAsSurfaceTabs(p);
    expect(resolveAdapter(tab.uri)).toBe(table);
  });

  it("routes a doc surface to the doc adapter, which the old scheme could not", () => {
    const doc = stubAdapter("doc");
    const record = stubAdapter("record");
    registerAdapter(doc);
    registerAdapter(record);
    const p = projectLedger([created(DOC_ID, { kind: "record" })]);
    const [tab] = ledgerTabsAsSurfaceTabs(p);
    expect(resolveAdapter(tab.uri)).toBe(doc);
  });

  it("leaves raw/receipt/gate unmatched so tier-3 renders them honestly", () => {
    registerAdapter(stubAdapter("table"));
    for (const kind of ["raw", "receipt", "gate"] as const) {
      const id = `${kind}://acme/op/1`;
      const p = projectLedger([created(id, { kind })]);
      const [tab] = ledgerTabsAsSurfaceTabs(p);
      expect(resolveAdapter(tab.uri)).toBeNull();
    }
  });

  it("does not resolve a non-surface tab URI as a surface", () => {
    // Artifact and effect-stage tabs share the strip. They are distinguished by
    // the AUTHORITY that owns them (the artifact registry, the hydration map),
    // never by parsing the string — but they must not accidentally collide with
    // an archetype adapter either. The third string is a pre-ledger `tool_result`
    // envelope URI, which no longer reaches the strip at all.
    registerAdapter(stubAdapter("table"));
    for (const uri of [
      "artifact-dataset://art_1@2",
      "effect-stage://stage_1",
      "sheet-row://legacy/xyz",
    ]) {
      expect(resolveAdapter(uri)).toBeNull();
    }
  });
});

// PRD-B3 — view lifecycle: preference, upgrade merge, effective tier, reload.
function preference(
  surface_id: string,
  keep: "generic" | "shaped",
): RuntimeEventEnvelope {
  return ev("view.preference", { v: 1, surface_id, keep, actor: "user" });
}

describe("projectLedger — PRD-B3 view lifecycle", () => {
  it("generic → shaped upgrade flips effectiveTier and merges in place", () => {
    const events = [
      created("s1"),
      derived("s1", { tier: "generic", basis: "schema" }),
      derived("s1", { tier: "shaped", basis: "generated" }),
    ];
    const p = projectLedger(events);
    const s = p.surfaces.get("s1");
    // Same surface (tab identity), no second entry — merged in place.
    expect(p.surfaces.size).toBe(1);
    expect(s?.viewState?.effectiveTier).toBe("shaped");
    expect(s?.viewState?.shapedAvailable).toBe(true);
  });

  it("keep: generic pins the tier across a later shaped derivation", () => {
    const events = [
      created("s1"),
      derived("s1", { tier: "generic", basis: "schema" }),
      preference("s1", "generic"),
      derived("s1", { tier: "shaped", basis: "generated" }),
    ];
    const s = projectLedger(events).surfaces.get("s1");
    // The shaped derivation lands (toggle enabled) but the pin holds the tier.
    expect(s?.viewState?.effectiveTier).toBe("generic");
    expect(s?.viewState?.keep).toBe("generic");
    expect(s?.viewState?.shapedAvailable).toBe(true);
    expect(s?.view?.preference).toBe("generic");
  });

  it("re-projecting the same events reproduces the pinned tier (reload DoD)", () => {
    const events = [
      created("s1"),
      derived("s1", { tier: "shaped", basis: "generated" }),
      preference("s1", "generic"),
    ];
    const first = projectLedger(events).surfaces.get("s1");
    const second = projectLedger(events).surfaces.get("s1");
    expect(first?.viewState?.effectiveTier).toBe("generic");
    expect(second?.viewState).toEqual(first?.viewState);
  });

  it("keep: shaped only folds once a shaped derivation exists", () => {
    const generic = projectLedger([
      created("s1"),
      derived("s1", { tier: "generic", basis: "schema" }),
      preference("s1", "shaped"),
    ]).surfaces.get("s1");
    // No shaped derivation yet ⇒ the pin does not force shaped.
    expect(generic?.viewState?.effectiveTier).toBe("generic");
  });

  it("counts regenerations (non-first, non-registry) for the cap mirror", () => {
    const s = projectLedger([
      created("s1"),
      derived("s1", { tier: "generic", basis: "schema" }), // first (seed)
      derived("s1", { tier: "shaped", basis: "generated" }), // regen 1
      derived("s1", { tier: "generic", basis: "schema" }), // regen 2
    ]).surfaces.get("s1");
    expect(s?.viewState?.regenCount).toBe(2);
  });

  it("ignores a view.preference for an unseen surface", () => {
    const p = projectLedger([preference("ghost", "generic")]);
    expect(p.surfaces.size).toBe(0);
  });
});
