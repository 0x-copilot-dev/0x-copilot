// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import { runDownloadOnStart, type DownloadDeps } from "./download";
import {
  PROMOTED_INSTALLS_FILE,
  optOutKey,
  type AdapterMetadata,
  type AdapterStateStore,
  type HttpFetch,
  type KeyValueStore,
  type LayoutTemplate,
  type LifecycleAuditLog,
  type LifecycleEvent,
  type PromotedInstallLedger,
  type QualityGate,
  type QualityGateOutcome,
  type RegistryHost,
} from "./types";

function makeKv(initial: Record<string, string> = {}): KeyValueStore {
  const store = new Map(Object.entries(initial));
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
  return {
    events,
    subscribe: () => () => undefined,
    emit: (event) => {
      events.push(event);
    },
  };
}

function makeRegistryHost(): RegistryHost & {
  readonly installs: Array<{ scheme: string; version: number; source: string }>;
} {
  const installs: Array<{ scheme: string; version: number; source: string }> =
    [];
  return {
    installs,
    installAdapter: async (args) => {
      installs.push({
        scheme: args.scheme,
        version: args.version,
        source: args.source,
      });
    },
    uninstallAdapter: vi.fn(),
    readAdapterSource: vi.fn(),
  };
}

function makeQualityGate(
  outcome: QualityGateOutcome = { ok: true },
): QualityGate {
  return { runAll: async () => outcome };
}

function communityMetadata(layout: LayoutTemplate = "form"): AdapterMetadata {
  return {
    origin: "community",
    generatedAt: "2026-05-17T00:00:00Z",
    generatorModel: "test-model",
    schemaVersion: 1,
    layout,
  };
}

function promotedResponseBody(
  adapters: ReadonlyArray<{
    scheme: string;
    version: number;
    source: string;
    layout?: LayoutTemplate;
    metadata?: AdapterMetadata;
  }>,
): string {
  return JSON.stringify({
    adapters: adapters.map((a) => ({
      scheme: a.scheme,
      version: a.version,
      layout: a.layout ?? "form",
      source: a.source,
      metadata: a.metadata ?? communityMetadata(a.layout ?? "form"),
    })),
  });
}

function okHttp(body: string): HttpFetch {
  return async () => ({
    status: 200,
    text: async () => body,
  });
}

function makeDeps(overrides: Partial<DownloadDeps> = {}): {
  deps: DownloadDeps;
  registryHost: ReturnType<typeof makeRegistryHost>;
  stateStore: AdapterStateStore;
  auditLog: ReturnType<typeof makeAuditLog>;
  kv: KeyValueStore;
} {
  const registryHost = overrides.registryHost
    ? (overrides.registryHost as ReturnType<typeof makeRegistryHost>)
    : makeRegistryHost();
  const stateStore = overrides.stateStore ?? makeStateStore();
  const auditLog = overrides.auditLog
    ? (overrides.auditLog as ReturnType<typeof makeAuditLog>)
    : makeAuditLog();
  const kv = overrides.kv ?? makeKv();
  const deps: DownloadDeps = {
    registryHost,
    qualityGate: overrides.qualityGate ?? makeQualityGate(),
    stateStore,
    auditLog,
    http: overrides.http ?? okHttp(promotedResponseBody([])),
    registryBaseUrl: overrides.registryBaseUrl ?? "https://facade.example.com",
    bearer: overrides.bearer ?? "bearer-123",
    tenantId: overrides.tenantId ?? "tenant-abc",
    kv,
  };
  return { deps, registryHost, stateStore, auditLog, kv };
}

describe("runDownloadOnStart", () => {
  it("skips network entirely when opt-out is true", async () => {
    const fetchSpy = vi.fn();
    const { deps } = makeDeps({
      kv: makeKv({ [optOutKey("tenant-abc")]: "true" }),
      http: fetchSpy as unknown as HttpFetch,
    });

    const result = await runDownloadOnStart(deps);

    expect(result.skippedOptOut).toBe(true);
    expect(result.installed).toBe(0);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("runs the quality gate on every candidate before installing", async () => {
    const gate = vi.fn(async () => ({ ok: true as const }));
    const { deps, registryHost } = makeDeps({
      qualityGate: { runAll: gate },
      http: okHttp(
        promotedResponseBody([
          { scheme: "email", version: 3, source: "/* good */" },
          { scheme: "sf-opp", version: 2, source: "/* also good */" },
        ]),
      ),
    });

    const result = await runDownloadOnStart(deps);

    expect(gate).toHaveBeenCalledTimes(2);
    expect(registryHost.installs).toHaveLength(2);
    expect(result.installed).toBe(2);
    expect(result.rejected).toBe(0);
  });

  it("rejects (does not install) any candidate that fails the gate", async () => {
    const gate: QualityGate = {
      runAll: async ({ source }) =>
        source.includes("bad")
          ? { ok: false, code: "allowlist", detail: "fetch found" }
          : { ok: true },
    };
    const { deps, registryHost, auditLog } = makeDeps({
      qualityGate: gate,
      http: okHttp(
        promotedResponseBody([
          { scheme: "good-scheme", version: 1, source: "/* good */" },
          { scheme: "bad-scheme", version: 1, source: "/* bad */" },
        ]),
      ),
    });

    const result = await runDownloadOnStart(deps);

    expect(registryHost.installs.map((i) => i.scheme)).toEqual(["good-scheme"]);
    expect(result.installed).toBe(1);
    expect(result.rejected).toBe(1);
    expect(
      auditLog.events.some(
        (e) =>
          e.kind === "adapter.download.rejected" &&
          e.scheme === "bad-scheme" &&
          e.reason === "allowlist",
      ),
    ).toBe(true);
  });

  it("does NOT bypass the gate even when the server promoted the adapter", async () => {
    const gate = vi.fn(async () => ({
      ok: false as const,
      code: "smoke_render" as const,
      detail: "threw",
    }));
    const { deps, registryHost } = makeDeps({
      qualityGate: { runAll: gate },
      http: okHttp(
        promotedResponseBody([
          { scheme: "email", version: 3, source: "/* server-promoted */" },
        ]),
      ),
    });

    await runDownloadOnStart(deps);

    expect(gate).toHaveBeenCalled();
    expect(registryHost.installs).toEqual([]);
  });

  it("records every successful install in the promoted-installs ledger", async () => {
    const { deps, stateStore } = makeDeps({
      http: okHttp(
        promotedResponseBody([
          { scheme: "email", version: 3, source: "/* a */" },
          { scheme: "sf-opp", version: 1, source: "/* b */" },
        ]),
      ),
    });

    await runDownloadOnStart(deps);
    const ledger = await stateStore.readJson<PromotedInstallLedger>(
      PROMOTED_INSTALLS_FILE,
    );
    expect(Object.keys(ledger?.entries ?? {}).sort()).toEqual([
      "email@3",
      "sf-opp@1",
    ]);
  });

  it("returns networkError=true when the registry list call fails", async () => {
    const { deps, registryHost } = makeDeps({
      http: async () => {
        throw new Error("ECONNREFUSED");
      },
    });

    const result = await runDownloadOnStart(deps);

    expect(result.networkError).toBe(true);
    expect(registryHost.installs).toEqual([]);
  });

  it("returns networkError=true on a non-2xx status", async () => {
    const { deps } = makeDeps({
      http: async () => ({ status: 503, text: async () => "" }),
    });

    const result = await runDownloadOnStart(deps);
    expect(result.networkError).toBe(true);
  });

  it("rejects payloads with the wrong shape (defense against compromised server)", async () => {
    const { deps, registryHost } = makeDeps({
      http: okHttp(
        JSON.stringify({
          adapters: [
            { scheme: "email", version: 3, layout: "form", source: "" },
            {
              scheme: "ok",
              version: 1,
              layout: "form",
              source: "/* x */",
              metadata: {
                origin: "first-party",
                generatedAt: "x",
                generatorModel: "x",
                schemaVersion: 1,
                layout: "form",
              },
            },
          ],
        }),
      ),
    });

    const result = await runDownloadOnStart(deps);
    expect(registryHost.installs).toEqual([]);
    expect(result.rejected).toBe(2);
  });

  it("attaches the bearer + tenant header on the list call", async () => {
    const captured: Array<{ url: string; headers: Record<string, string> }> =
      [];
    const http: HttpFetch = async (url, init) => {
      captured.push({ url, headers: init.headers });
      return { status: 200, text: async () => promotedResponseBody([]) };
    };
    const { deps } = makeDeps({ http, bearer: "tok-xyz", tenantId: "t-7" });

    await runDownloadOnStart(deps);

    expect(captured).toHaveLength(1);
    expect(captured[0].url).toBe(
      "https://facade.example.com/v1/adapter_registry/promoted",
    );
    expect(captured[0].headers.authorization).toBe("Bearer tok-xyz");
    expect(captured[0].headers["x-tenant-id"]).toBe("t-7");
  });
});
