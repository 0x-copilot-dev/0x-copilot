// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getOptOut, setOptOut, type OptOutDeps } from "./opt-out";
import {
  PROMOTED_INSTALLS_FILE,
  optOutKey,
  type AdapterStateStore,
  type KeyValueStore,
  type LifecycleAuditLog,
  type LifecycleEvent,
  type PromotedInstallLedger,
  type RegistryHost,
} from "./types";

function makeKv(): KeyValueStore {
  const store = new Map<string, string>();
  return {
    get: async (key) => store.get(key) ?? null,
    set: async (key, value) => {
      store.set(key, value);
    },
    delete: async (key) => {
      store.delete(key);
    },
  };
}

function makeStateStore(): AdapterStateStore {
  const store = new Map<string, unknown>();
  return {
    readJson: async <T>(name: string) => (store.get(name) as T) ?? null,
    writeJsonAtomic: async <T>(name: string, value: T) => {
      store.set(name, value);
    },
  };
}

function makeAuditLog(): LifecycleAuditLog & {
  readonly events: LifecycleEvent[];
} {
  const events: LifecycleEvent[] = [];
  const handlers = new Set<(e: LifecycleEvent) => void>();
  return {
    events,
    subscribe: (handler) => {
      handlers.add(handler);
      return () => {
        handlers.delete(handler);
      };
    },
    emit: (event) => {
      events.push(event);
      for (const h of handlers) h(event);
    },
  };
}

function makeDeps(overrides: Partial<OptOutDeps> = {}): {
  deps: OptOutDeps;
  kv: KeyValueStore;
  stateStore: AdapterStateStore;
  registryHost: RegistryHost & { calls: Array<[string, number]> };
  auditLog: LifecycleAuditLog & { readonly events: LifecycleEvent[] };
} {
  const kv = overrides.kv ?? makeKv();
  const stateStore = overrides.stateStore ?? makeStateStore();
  const auditLog = overrides.auditLog
    ? Object.assign(overrides.auditLog, { events: [] as LifecycleEvent[] })
    : makeAuditLog();
  const calls: Array<[string, number]> = [];
  const registryHost: RegistryHost & { calls: Array<[string, number]> } = {
    calls,
    installAdapter: vi.fn(),
    uninstallAdapter: async (scheme, version) => {
      calls.push([scheme, version]);
    },
    readAdapterSource: vi.fn(),
  };
  const merged = overrides.registryHost
    ? (Object.assign(overrides.registryHost as object, {
        calls,
      }) as typeof registryHost)
    : registryHost;
  return {
    deps: { kv, stateStore, registryHost: merged, auditLog },
    kv,
    stateStore,
    registryHost: merged,
    auditLog: auditLog as LifecycleAuditLog & {
      readonly events: LifecycleEvent[];
    },
  };
}

describe("opt-out", () => {
  let tenantId: string;

  beforeEach(() => {
    tenantId = "tenant-abc";
  });

  it("defaults to false when nothing has been written", async () => {
    const { deps } = makeDeps();
    expect(await getOptOut(deps, tenantId)).toBe(false);
  });

  it("persists the setting under the tenant-scoped key", async () => {
    const { deps, kv } = makeDeps();
    await setOptOut(deps, tenantId, true);
    expect(await kv.get(optOutKey(tenantId))).toBe("true");
    expect(await getOptOut(deps, tenantId)).toBe(true);
  });

  it("uninstalls every promoted adapter when toggled false→true", async () => {
    const { deps, stateStore, registryHost, auditLog } = makeDeps();
    await stateStore.writeJsonAtomic<PromotedInstallLedger>(
      PROMOTED_INSTALLS_FILE,
      {
        entries: {
          "email@3": { scheme: "email", version: 3 },
          "sf-opp@1": { scheme: "sf-opp", version: 1 },
        },
      },
    );

    await setOptOut(deps, tenantId, true);

    expect(registryHost.calls.sort()).toEqual(
      [
        ["email", 3],
        ["sf-opp", 1],
      ].sort(),
    );
    const uninstallEvents = auditLog.events.filter(
      (e) => e.kind === "adapter.optout.uninstalled",
    );
    expect(uninstallEvents).toHaveLength(2);
    expect(
      auditLog.events.some(
        (e) => e.kind === "adapter.optout.enabled" && e.tenantId === tenantId,
      ),
    ).toBe(true);
    const ledger = await stateStore.readJson<PromotedInstallLedger>(
      PROMOTED_INSTALLS_FILE,
    );
    expect(ledger?.entries).toEqual({});
  });

  it("does not re-uninstall when set true→true (no transition)", async () => {
    const { deps, stateStore, registryHost } = makeDeps();
    await setOptOut(deps, tenantId, true);
    await stateStore.writeJsonAtomic<PromotedInstallLedger>(
      PROMOTED_INSTALLS_FILE,
      { entries: { "email@3": { scheme: "email", version: 3 } } },
    );
    registryHost.calls.length = 0;

    await setOptOut(deps, tenantId, true);

    expect(registryHost.calls).toEqual([]);
  });

  it("logs the disabled transition but does not redownload mid-session", async () => {
    const { deps, auditLog } = makeDeps();
    await setOptOut(deps, tenantId, true);
    auditLog.events.length = 0;

    await setOptOut(deps, tenantId, false);

    expect(
      auditLog.events.some(
        (e) => e.kind === "adapter.optout.disabled" && e.tenantId === tenantId,
      ),
    ).toBe(true);
    expect(await getOptOut(deps, tenantId)).toBe(false);
  });

  it("tolerates uninstall errors and still clears the ledger", async () => {
    const { deps, stateStore } = makeDeps({
      registryHost: {
        installAdapter: vi.fn(),
        uninstallAdapter: async () => {
          throw new Error("not registered");
        },
        readAdapterSource: vi.fn(),
      },
    });
    await stateStore.writeJsonAtomic<PromotedInstallLedger>(
      PROMOTED_INSTALLS_FILE,
      { entries: { "email@3": { scheme: "email", version: 3 } } },
    );

    await expect(setOptOut(deps, tenantId, true)).resolves.toBeUndefined();
    const ledger = await stateStore.readJson<PromotedInstallLedger>(
      PROMOTED_INSTALLS_FILE,
    );
    expect(ledger?.entries).toEqual({});
  });
});
