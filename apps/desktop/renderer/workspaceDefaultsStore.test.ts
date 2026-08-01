// The join between Settings writing a workspace default and a live consumer
// seeing it. Before this store existed there was no join at all: seven call
// sites each fetched `/v1/agent/workspace/defaults` and kept a private
// snapshot, so turning bypass on in Settings left the composer pill disabled
// until the renderer reloaded — measured by the FS-D journey as
// `master_reached_pill_via: "a renderer reload"`.

import type { WorkspaceDefaultsResponse } from "@0x-copilot/api-types";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  currentWorkspaceDefaults,
  publishWorkspaceDefaults,
  resetWorkspaceDefaultsStore,
  subscribeWorkspaceDefaults,
} from "./workspaceDefaultsStore";

afterEach(() => {
  resetWorkspaceDefaultsStore();
});

function defaults(bypass: boolean): WorkspaceDefaultsResponse {
  return {
    behavior_overrides: { filesystem_bypass_enabled: bypass },
  } as unknown as WorkspaceDefaultsResponse;
}

describe("workspaceDefaultsStore", () => {
  it("delivers a later write to an already-subscribed reader", () => {
    // The actual bug: Settings PUTs, the composer never hears.
    const seen: boolean[] = [];
    subscribeWorkspaceDefaults((d) => {
      seen.push(d.behavior_overrides?.filesystem_bypass_enabled === true);
    });

    publishWorkspaceDefaults(defaults(false));
    publishWorkspaceDefaults(defaults(true));

    expect(seen).toEqual([false, true]);
  });

  it("replays the last value to a reader that subscribes afterwards", () => {
    // A composer mounted AFTER the user changed the setting must not sit on a
    // stale default waiting for a write that may never come again.
    publishWorkspaceDefaults(defaults(true));

    const seen: boolean[] = [];
    subscribeWorkspaceDefaults((d) => {
      seen.push(d.behavior_overrides?.filesystem_bypass_enabled === true);
    });

    expect(seen).toEqual([true]);
  });

  it("does not call a listener after it unsubscribes", () => {
    const listener = vi.fn();
    const off = subscribeWorkspaceDefaults(listener);
    off();
    publishWorkspaceDefaults(defaults(true));
    expect(listener).not.toHaveBeenCalled();
  });

  it("still reaches the other listeners when one throws", () => {
    // One bad subscriber must not deny the rest their update — otherwise a
    // single unrelated component turns a settings change into a silent no-op
    // for everyone downstream of it.
    const good = vi.fn();
    subscribeWorkspaceDefaults(() => {
      throw new Error("subscriber blew up");
    });
    subscribeWorkspaceDefaults(good);

    expect(() => publishWorkspaceDefaults(defaults(true))).not.toThrow();
    expect(good).toHaveBeenCalledTimes(1);
  });

  it("survives a listener unsubscribing during dispatch", () => {
    // A component may unmount in response to the very change being announced.
    // Iterating the live Set would skip its neighbour.
    const second = vi.fn();
    const off = subscribeWorkspaceDefaults(() => off());
    subscribeWorkspaceDefaults(second);

    publishWorkspaceDefaults(defaults(true));

    expect(second).toHaveBeenCalledTimes(1);
  });

  it("reports null before anything has been read", () => {
    // Distinct from "read and empty": a consumer must be able to tell
    // "nobody has asked yet" from "the workspace has no overrides".
    expect(currentWorkspaceDefaults()).toBeNull();
    publishWorkspaceDefaults(defaults(false));
    expect(currentWorkspaceDefaults()).not.toBeNull();
  });
});
