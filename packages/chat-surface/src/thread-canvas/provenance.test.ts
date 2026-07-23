// Unit tests for `projectProvenance` (PRD-B2 D1) — the pure, ledger-only
// footer-fields fold. Fixtures mirror the REAL emitter convention (SDR §5 note:
// `read.executed` and `surface.created` share `payload_ref = call:<call_id>`),
// which is the join the live runtime produces; the A1 golden fixture's
// illustrative payload_refs are intentionally mismatched, so a strict join over
// it is exercised as the fail-closed case.

import { beforeEach, describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import {
  formatAccessClass,
  formatLatency,
  projectProvenance,
  resolveSurfaceOpenIn,
  type SurfaceProvenance,
} from "./provenance";

const RUN = "a7f3c9d2e5b14f60";

let seq = 0;
beforeEach(() => {
  seq = 0;
});
function ev(
  event_type: string,
  payload: Record<string, unknown>,
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
  };
}

/** The real read path for one surface, with a shared `call:<id>` payload_ref. */
function readPath(
  surfaceId: string,
  {
    callId = "call_01",
    cls = "read",
    latency = 142,
    tier = "shaped",
    kind = "record",
  }: {
    callId?: string;
    cls?: string;
    latency?: number;
    tier?: string;
    kind?: string;
  } = {},
): RuntimeEventEnvelope[] {
  const ref = `call:${callId}`;
  return [
    ev("action.classified", {
      v: 1,
      call_id: callId,
      connector: "linear",
      op: "get_issue",
      class: cls,
      basis: "catalog",
    }),
    ev("read.executed", {
      v: 1,
      call_id: callId,
      connector: "linear",
      op: "get_issue",
      latency_ms: latency,
      payload_ref: ref,
    }),
    ev("surface.created", {
      v: 1,
      surface_id: surfaceId,
      kind,
      source: { connector: "linear", op: "get_issue" },
      title: "ENG-142",
      payload_ref: ref,
    }),
    ev("view.derived", {
      v: 1,
      surface_id: surfaceId,
      tier,
      basis: "registry",
    }),
  ];
}

describe("projectProvenance — fields from ledger events only", () => {
  it("projects every footer field from the golden read path", () => {
    const map = projectProvenance(readPath("s1"));
    const p = map.get("s1") as SurfaceProvenance;
    expect(p).toMatchObject({
      surfaceId: "s1",
      connector: "linear",
      op: "get_issue",
      kind: "record",
      latencyMs: 142,
      accessClass: "read",
      tier: "shaped",
      openIn: null,
    });
  });

  it("formats the ledger id as r<short>·<seq> from surface.created's seq", () => {
    const map = projectProvenance(readPath("s1"));
    // surface.created is the 3rd event ⇒ seq 3.
    expect(map.get("s1")?.ledgerId).toBe("ra7f·003");
  });

  it("joins read.executed via payload_ref and action.classified via call_id", () => {
    const map = projectProvenance(
      readPath("s1", { callId: "call_99", latency: 512 }),
    );
    expect(map.get("s1")?.latencyMs).toBe(512);
    expect(map.get("s1")?.accessClass).toBe("read");
  });

  it("fails closed to write_held when action.classified is missing", () => {
    // Drop the classification event.
    const events = readPath("s1").filter(
      (e) => e.event_type !== "action.classified",
    );
    expect(projectProvenance(events).get("s1")?.accessClass).toBe("write_held");
  });

  it("fails closed to write_held when class is unknown / write", () => {
    expect(
      projectProvenance(readPath("s1", { cls: "unknown" })).get("s1")
        ?.accessClass,
    ).toBe("write_held");
    expect(
      projectProvenance(readPath("s1", { cls: "write" })).get("s1")
        ?.accessClass,
    ).toBe("write_held");
  });

  it("is pending with null latency until the read/view land", () => {
    const created = ev("surface.created", {
      v: 1,
      surface_id: "s1",
      kind: "table",
      source: { connector: "github", op: "list_issues" },
      title: "t",
      payload_ref: "call:x",
    });
    const p = projectProvenance([created]).get("s1") as SurfaceProvenance;
    expect(p.tier).toBe("pending");
    expect(p.latencyMs).toBeNull();
    expect(p.accessClass).toBe("write_held");
  });

  it("tolerates malformed payloads and degrades per-field (never throws)", () => {
    const events: RuntimeEventEnvelope[] = [
      ev("surface.created", { v: 1, surface_id: "s1" }), // no source/kind
      ev("read.executed", { v: 1, latency_ms: "nope", payload_ref: "call:x" }),
      ev("view.derived", { v: 1, surface_id: "s1", tier: "bogus" }),
      ev("garbage.type", { anything: true }),
    ];
    const p = projectProvenance(events).get("s1") as SurfaceProvenance;
    expect(p.connector).toBe("");
    expect(p.kind).toBe("");
    expect(p.latencyMs).toBeNull();
    expect(p.tier).toBe("pending"); // bogus tier ignored
    expect(p.accessClass).toBe("write_held");
  });

  it("is pure: same events twice ⇒ deep-equal; input array not mutated", () => {
    const events = readPath("s1");
    const frozen = JSON.stringify(events);
    const a = projectProvenance(events).get("s1");
    const b = projectProvenance(events).get("s1");
    expect(a).toEqual(b);
    expect(JSON.stringify(events)).toBe(frozen);
  });
});

describe("resolveSurfaceOpenIn — deep link from hydrated content", () => {
  const base = projectProvenance(readPath("s1")).get("s1") as SurfaceProvenance;

  it("resolves a safe http url_path against the payload", () => {
    const payload = {
      spec: { link: { label: "Open issue", url_path: "data.url" } },
      data: { url: "https://linear.app/x/ENG-142" },
    };
    const p = resolveSurfaceOpenIn(base, payload);
    expect(p.openIn).toEqual({
      label: "Open issue",
      url: "https://linear.app/x/ENG-142",
    });
  });

  it("carries a null label when the spec link omits one (component fallback)", () => {
    const payload = {
      spec: { link: { url_path: "data.url" } },
      data: { url: "https://linear.app/x" },
    };
    expect(resolveSurfaceOpenIn(base, payload).openIn?.label).toBeNull();
  });

  it("omits the link for an unsafe / relative / missing url_path", () => {
    expect(
      resolveSurfaceOpenIn(base, {
        spec: { link: { url_path: "data.url" } },
        data: { url: "javascript:alert(1)" },
      }).openIn,
    ).toBeNull();
    expect(
      resolveSurfaceOpenIn(base, {
        spec: { link: { url_path: "data.url" } },
        data: { url: "/relative" },
      }).openIn,
    ).toBeNull();
    expect(resolveSurfaceOpenIn(base, { data: {} }).openIn).toBeNull();
    expect(resolveSurfaceOpenIn(base, undefined).openIn).toBeNull();
  });
});

describe("display helpers", () => {
  it("formats latency as ms under 1s and s at/above", () => {
    expect(formatLatency(420)).toBe("420ms");
    expect(formatLatency(999)).toBe("999ms");
    expect(formatLatency(1000)).toBe("1.0s");
    expect(formatLatency(1240)).toBe("1.2s");
  });

  it("formats access class labels", () => {
    expect(formatAccessClass("read")).toBe("read-only");
    expect(formatAccessClass("write_held")).toBe("write · held");
  });
});
