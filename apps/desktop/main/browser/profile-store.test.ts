// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  ProfileError,
  ProfileStore,
  type ProfileFsPort,
} from "./profile-store";

class FakeFs implements ProfileFsPort {
  readonly dirs = new Set<string>();
  readonly files = new Map<string, string>();
  async mkdir(path: string): Promise<void> {
    this.dirs.add(path);
  }
  async writeFile(path: string, data: string): Promise<void> {
    this.files.set(path, data);
  }
  async readFile(path: string): Promise<string> {
    const v = this.files.get(path);
    if (v === undefined) throw new Error("ENOENT");
    return v;
  }
  async rm(path: string): Promise<void> {
    this.dirs.delete(path);
  }
  async exists(path: string): Promise<boolean> {
    return this.files.has(path);
  }
}

function store(fs: FakeFs, ids: string[] = ["a", "b", "c", "d"]): ProfileStore {
  let i = 0;
  return new ProfileStore({
    profilesRoot: "/prof",
    ephemeralRoot: "/eph",
    fs,
    browserVersion: "chromium-1",
    randomId: () => ids[i++] ?? `x${i}`,
    now: () => 1000,
  });
}

describe("ProfileStore isolation", () => {
  it("gives two workspaces distinct, non-shared directories", async () => {
    const fs = new FakeFs();
    const s = store(fs, ["ws1prof", "ws2prof"]);
    const p1 = await s.createPersistent("workspace-1");
    const p2 = await s.createPersistent("workspace-2");
    expect(p1.userDataDir).not.toBe(p2.userDataDir);
    expect(p1.workspaceId).toBe("workspace-1");
    expect(p2.workspaceId).toBe("workspace-2");
    // Paths derive from opaque ids, not workspace names.
    expect(p1.userDataDir).not.toContain("workspace-1");
  });

  it("mints ephemeral profiles under the ephemeral root and discards them", async () => {
    const fs = new FakeFs();
    const s = store(fs, ["eph1"]);
    const p = await s.newEphemeral("workspace-1");
    expect(p.mode).toBe("ephemeral");
    expect(fs.dirs.has("/eph/eph1")).toBe(true);
    await s.discardEphemeral(p);
    expect(fs.dirs.has("/eph/eph1")).toBe(false);
  });
});

describe("ProfileStore leasing", () => {
  it("allows one lease and denies a second (profile_busy)", async () => {
    const fs = new FakeFs();
    const s = store(fs, ["p1"]);
    const p = await s.createPersistent("ws");
    await s.acquireLease(p, "ws");
    await expect(s.acquireLease(p, "ws")).rejects.toMatchObject({
      code: "browser_profile_busy",
    });
    s.releaseLease(p.profileId);
    await expect(s.acquireLease(p, "ws")).resolves.toBeUndefined();
  });

  it("denies a cross-workspace open (version_mismatch semantics)", async () => {
    const fs = new FakeFs();
    const s = store(fs, ["p1"]);
    const p = await s.createPersistent("ws-owner");
    await expect(s.acquireLease(p, "ws-other")).rejects.toBeInstanceOf(
      ProfileError,
    );
    await expect(s.acquireLease(p, "ws-other")).rejects.toMatchObject({
      code: "browser_profile_version_mismatch",
    });
  });

  it("denies an incompatible browser version", async () => {
    const fs = new FakeFs();
    const s = store(fs, ["p1"]);
    const p = await s.createPersistent("ws");
    const stale = { ...p, browserVersion: "chromium-0" };
    await expect(s.acquireLease(stale, "ws")).rejects.toMatchObject({
      code: "browser_profile_version_mismatch",
    });
  });
});

describe("ProfileStore persistence", () => {
  it("writes and reloads a manifest bound to one workspace", async () => {
    const fs = new FakeFs();
    const s = store(fs, ["p1"]);
    const p = await s.createPersistent("ws");
    const loaded = await s.load(p.profileId);
    expect(loaded.workspaceId).toBe("ws");
    expect(loaded.mode).toBe("persistent");
  });
});
