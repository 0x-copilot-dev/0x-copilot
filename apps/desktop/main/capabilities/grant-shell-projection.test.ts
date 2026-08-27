// @vitest-environment node
//
// PRD-shell-execution §7.3 — where the per-workspace command flag is allowed to
// GO, and where it must not.
//
// `Grant` holds the decision; three projections carry it (or refuse to) to three
// different audiences. Each has a different reason, and each fails differently:
//
//   toRendererGrant     → the Settings toggle. Needs the CURRENT state, so the
//                         control shows what main holds rather than what the
//                         renderer last asked for.
//   toBrokerGrant       → the runtime worker. THE READ PATH (§7.1 prerequisite
//                         3). Present only while the grant is live.
//   toHostSessionGrant  → the C2 write-authority bootstrap. Must NOT carry it:
//                         `_assert_host_session_wire_is_private` allow-lists
//                         five grant keys by name and raises
//                         `BrokerProtocolError` on any sixth, so a leak here
//                         fails every live host session closed while every test
//                         that builds the payload by hand stays green.
//
// A projection test is cheap and this one is not decorative: the flag decides
// whether an agent may run code on the machine, so "which audience learns it"
// is a security question with three different right answers.

import { describe, expect, it } from "vitest";

import type { Grant } from "./types";
import { toBrokerGrant, toHostSessionGrant, toRendererGrant } from "./types";

function makeGrant(overrides: Partial<Grant> = {}): Grant {
  return {
    grantId: "g_atlas",
    root: "/Users/x/projects/atlas",
    mode: "read_write",
    label: "atlas",
    shellEnabled: false,
    status: "active",
    createdAt: 1,
    updatedAt: 1,
    ...overrides,
  };
}

describe("toRendererGrant — the Settings toggle's view", () => {
  it("reports the flag so the toggle can show the state main actually holds", () => {
    expect(
      toRendererGrant(makeGrant({ shellEnabled: true })).shellEnabled,
    ).toBe(true);
    expect(
      toRendererGrant(makeGrant({ shellEnabled: false })).shellEnabled,
    ).toBe(false);
  });

  it("a grant that authorizes nothing advertises nothing", () => {
    // `GrantStore.list` reports an expired grant as revoked, so `status` here is
    // already the EFFECTIVE one and expiry rides this same line. Without it a
    // detached workspace's row would read "commands allowed" over authority
    // that has lapsed.
    expect(
      toRendererGrant(makeGrant({ shellEnabled: true, status: "revoked" }))
        .shellEnabled,
    ).toBe(false);
  });

  it("stays path-free — the flag did not become a path oracle", () => {
    const projected = toRendererGrant(makeGrant({ shellEnabled: true }));
    expect(Object.keys(projected).sort()).toEqual([
      "grantId",
      "label",
      "mode",
      "shellEnabled",
      "status",
    ]);
    expect(JSON.stringify(projected)).not.toContain("/Users/x");
  });
});

describe("toBrokerGrant — the runtime's read path (§7.1 prerequisite 3)", () => {
  it("carries the flag for a live grant", () => {
    const projected = toBrokerGrant(makeGrant({ shellEnabled: true }), "mnt_1");
    expect(projected.shellEnabled).toBe(true);
    expect(projected.root).toBe("/Users/x/projects/atlas");
  });

  it("OMITS the flag entirely for a revoked grant, alongside the root", () => {
    // Absence is the same answer an older Electron main gives, and the Python
    // side defaults `shell_enabled` to False — so every route by which the fact
    // can be missing means "this workspace cannot run commands", never "it can".
    const projected = toBrokerGrant(
      makeGrant({ shellEnabled: true, status: "revoked" }),
      "mnt_1",
    );
    expect(projected).not.toHaveProperty("shellEnabled");
    expect(projected).not.toHaveProperty("root");
  });
});

describe("toHostSessionGrant — the one projection that must NOT carry it", () => {
  it("emits exactly the five allow-listed keys", () => {
    // Adding a sixth key here does not merely widen a contract: the ai-backend
    // asserts this wire field-by-field and fails the whole host session closed
    // on anything it does not recognise. The flag has no business here anyway —
    // this bootstrap carries WRITE authority for staged effects, and shell
    // enablement is read off `/v1/grants/snapshot`.
    const projected = toHostSessionGrant(
      makeGrant({ shellEnabled: true }),
      "mnt_1",
    );
    expect(Object.keys(projected).sort()).toEqual([
      "grantId",
      "label",
      "mode",
      "mount",
      "status",
    ]);
    expect(projected).not.toHaveProperty("shellEnabled");
    expect(projected).not.toHaveProperty("root");
  });
});
