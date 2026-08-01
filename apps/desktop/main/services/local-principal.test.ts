// `resolveLocalPrincipal` — the id the E2 cohort rule names.
//
// The property under test is ADOPT, NEVER MINT. Records are keyed by org/user,
// so an install that already has conversations would have its whole history
// orphaned by fresh ids: the app would look wiped. Every test here exists to
// make inventing an id impossible to do accidentally.

import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { resolveLocalPrincipal } from "./local-principal";

function userData(): string {
  return mkdtempSync(join(tmpdir(), "principal-"));
}

function writeStoreRecord(
  dir: string,
  row: unknown,
  name = "approvals.jsonl",
): void {
  const state = join(dir, "agent-data", "v1", "state");
  mkdirSync(state, { recursive: true });
  writeFileSync(join(state, name), `${JSON.stringify(row)}\n`, "utf8");
}

const RECORD = {
  record: { org_id: "org_abc123", user_id: "usr_def456", status: "pending" },
};

describe("resolveLocalPrincipal", () => {
  it("reports nothing for a fresh install rather than inventing an id", () => {
    // The honest first-boot state. A minted id would name a user the backend
    // has not created, so admission would miss and the lane would degrade for
    // a reason indistinguishable from having no policy at all.
    expect(resolveLocalPrincipal(userData())).toBeUndefined();
  });

  it("adopts the principal this install's records are already keyed by", () => {
    const dir = userData();
    writeStoreRecord(dir, RECORD);

    expect(resolveLocalPrincipal(dir)).toEqual({
      orgId: "org_abc123",
      userId: "usr_def456",
    });
  });

  it("caches the adopted principal so later boots skip the scan", () => {
    const dir = userData();
    writeStoreRecord(dir, RECORD);

    resolveLocalPrincipal(dir);

    const cached = JSON.parse(
      readFileSync(join(dir, "local-principal.json"), "utf8"),
    );
    expect(cached).toEqual({ orgId: "org_abc123", userId: "usr_def456" });
  });

  it("prefers the cache over a rescan, so the id cannot drift mid-life", () => {
    const dir = userData();
    writeFileSync(
      join(dir, "local-principal.json"),
      JSON.stringify({ orgId: "org_cached", userId: "usr_cached" }),
      "utf8",
    );
    writeStoreRecord(dir, RECORD);

    expect(resolveLocalPrincipal(dir)).toEqual({
      orgId: "org_cached",
      userId: "usr_cached",
    });
  });

  it("treats a malformed cache as absent instead of emitting a bad rule", () => {
    // `RolloutCohortRule` validates its selectors at STARTUP; an invalid one
    // fails the whole service rather than merely disabling a lane.
    const dir = userData();
    writeFileSync(join(dir, "local-principal.json"), "{ not json", "utf8");

    expect(resolveLocalPrincipal(dir)).toBeUndefined();
  });

  it("rejects an id that would not satisfy the rule's identifier pattern", () => {
    const dir = userData();
    writeStoreRecord(dir, {
      record: { org_id: "org with spaces", user_id: "usr_ok" },
    });

    expect(resolveLocalPrincipal(dir)).toBeUndefined();
  });

  it("survives a malformed line without abandoning the scan", () => {
    const dir = userData();
    const state = join(dir, "agent-data", "v1", "state");
    mkdirSync(state, { recursive: true });
    writeFileSync(
      join(state, "approvals.jsonl"),
      `{ broken\n${JSON.stringify(RECORD)}\n`,
      "utf8",
    );

    expect(resolveLocalPrincipal(dir)).toEqual({
      orgId: "org_abc123",
      userId: "usr_def456",
    });
  });

  it("finds a principal nested anywhere under the store, not just state/", () => {
    // Runs and messages live under `workspaces/<id>/sessions/<id>/`, and a
    // store whose only records are there must still resolve.
    const dir = userData();
    const nested = join(
      dir,
      "agent-data",
      "v1",
      "workspaces",
      "w1",
      "sessions",
      "s1",
    );
    mkdirSync(nested, { recursive: true });
    writeFileSync(
      join(nested, "runs.jsonl"),
      `${JSON.stringify(RECORD)}\n`,
      "utf8",
    );

    expect(resolveLocalPrincipal(dir)).toEqual({
      orgId: "org_abc123",
      userId: "usr_def456",
    });
  });
});
