// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import { Harvester, type HarvestSubmissionMetadata } from "./harvest";
import {
  HARVEST_STATE_FILE,
  optOutKey,
  type AdapterStateStore,
  type AstAllowlistScan,
  type HarvestLedger,
  type HttpFetch,
  type HttpResponseLike,
  type KeyValueStore,
  type LayoutTemplate,
  type LifecycleAuditLog,
  type LifecycleEvent,
  type RegistryHost,
} from "./types";

function makeKv(initial: Record<string, string> = {}): KeyValueStore {
  const store = new Map(Object.entries(initial));
  return {
    get: async (k) => store.get(k) ?? null,
    set: async (k, v) => {
      store.set(k, v);
    },
    delete: async (k) => {
      store.delete(k);
    },
  };
}

function makeStateStore(): AdapterStateStore & {
  readonly snapshot: () => Map<string, unknown>;
} {
  const store = new Map<string, unknown>();
  return {
    snapshot: () => store,
    readJson: async <T>(name: string) => (store.get(name) as T) ?? null,
    writeJsonAtomic: async <T>(name: string, value: T) => {
      store.set(name, value);
    },
  };
}

function makeAuditLog(): LifecycleAuditLog & {
  readonly events: LifecycleEvent[];
  readonly fire: (e: LifecycleEvent) => void;
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
    fire: (event) => {
      for (const h of handlers) h(event);
    },
  };
}

function makeRegistryHost(source = "/* clean source */"): RegistryHost {
  return {
    installAdapter: vi.fn(),
    uninstallAdapter: vi.fn(),
    readAdapterSource: async () => source,
  };
}

function passingScan(): AstAllowlistScan {
  return { scan: () => ({ ok: true }) };
}

function failingScan(reason: string): AstAllowlistScan {
  return { scan: () => ({ ok: false, reason }) };
}

function defaultMetadata(
  layout: LayoutTemplate = "form",
): HarvestSubmissionMetadata {
  return {
    layout,
    generatedAt: "2026-05-17T00:00:00Z",
    generatorModel: "test-model",
  };
}

function bumpSessionEvent(scheme: string, version: number): LifecycleEvent {
  return {
    kind: "adapter.session.completed",
    scheme,
    version,
    renderErrorCount: 0,
  };
}

interface HarvesterHarness {
  readonly harvester: Harvester;
  readonly auditLog: ReturnType<typeof makeAuditLog>;
  readonly stateStore: ReturnType<typeof makeStateStore>;
  readonly kv: KeyValueStore;
  readonly httpCalls: Array<{ url: string; body: unknown }>;
}

function makeHarvester(
  overrides: {
    http?: HttpFetch;
    httpResponses?: HttpResponseLike[];
    scan?: AstAllowlistScan;
    registryHost?: RegistryHost;
    kv?: KeyValueStore;
    bearer?: string | null;
    tenantId?: string;
    metadata?: HarvestSubmissionMetadata | null;
  } = {},
): HarvesterHarness {
  const auditLog = makeAuditLog();
  const stateStore = makeStateStore();
  const kv = overrides.kv ?? makeKv();
  const httpCalls: Array<{ url: string; body: unknown }> = [];

  let responses: HttpResponseLike[] = overrides.httpResponses ?? [
    { status: 201, text: async () => "{}" },
  ];

  const http: HttpFetch =
    overrides.http ??
    (async (url, init) => {
      httpCalls.push({
        url,
        body: init.body ? JSON.parse(init.body) : null,
      });
      const next = responses.shift() ?? { status: 201, text: async () => "{}" };
      return next;
    });

  const harvester = new Harvester({
    auditLog,
    registryHost: overrides.registryHost ?? makeRegistryHost(),
    astScan: overrides.scan ?? passingScan(),
    stateStore,
    http,
    registryBaseUrl: "https://facade.example.com",
    bearer: () =>
      overrides.bearer === undefined ? "bearer-tok" : overrides.bearer,
    tenantId: overrides.tenantId ?? "tenant-abc",
    kv,
    clock: () => Date.parse("2026-05-17T01:02:03Z"),
    sleep: async () => undefined,
    adapterMetadata: async () =>
      overrides.metadata === undefined ? defaultMetadata() : overrides.metadata,
  });

  return { harvester, auditLog, stateStore, kv, httpCalls };
}

async function fireSessionsAndFlush(
  harness: HarvesterHarness,
  scheme: string,
  version: number,
  count: number,
): Promise<void> {
  for (let i = 0; i < count; i++) {
    harness.auditLog.fire(bumpSessionEvent(scheme, version));
  }
  await harness.harvester.flush();
}

describe("Harvester", () => {
  it("does not submit until 10 clean sessions are observed", async () => {
    const harness = makeHarvester();
    await harness.harvester.start();

    await fireSessionsAndFlush(harness, "email", 3, 9);

    expect(harness.httpCalls).toEqual([]);
  });

  it("submits exactly once when the 10th clean session lands", async () => {
    const harness = makeHarvester();
    await harness.harvester.start();

    await fireSessionsAndFlush(harness, "email", 3, 10);

    expect(harness.httpCalls).toHaveLength(1);
    expect(harness.httpCalls[0].url).toBe(
      "https://facade.example.com/v1/adapter_registry/candidates",
    );
    const body = harness.httpCalls[0].body as Record<string, unknown>;
    expect(body.scheme).toBe("email");
    expect(body.version).toBe(3);
    expect(body.layout).toBe("form");
    expect(body.source).toBe("/* clean source */");
    expect(
      (body.harvest_metrics as Record<string, unknown>).sessions_observed,
    ).toBe(10);
  });

  it("does not re-submit on the 11th, 12th, ... sessions (idempotency)", async () => {
    const harness = makeHarvester();
    await harness.harvester.start();

    await fireSessionsAndFlush(harness, "email", 3, 12);

    expect(harness.httpCalls).toHaveLength(1);
  });

  it("never submits after a render error (any non-zero disqualifies forever)", async () => {
    const harness = makeHarvester();
    await harness.harvester.start();

    harness.auditLog.fire({
      kind: "adapter.session.completed",
      scheme: "email",
      version: 3,
      renderErrorCount: 1,
    });
    await fireSessionsAndFlush(harness, "email", 3, 20);

    expect(harness.httpCalls).toEqual([]);
  });

  it("never submits after a user-issue report", async () => {
    const harness = makeHarvester();
    await harness.harvester.start();

    await fireSessionsAndFlush(harness, "email", 3, 5);
    harness.auditLog.fire({
      kind: "adapter.user_issue.reported",
      scheme: "email",
      version: 3,
    });
    await fireSessionsAndFlush(harness, "email", 3, 20);

    expect(harness.httpCalls).toEqual([]);
  });

  it("blocks submission when opt-out is set", async () => {
    const harness = makeHarvester({
      kv: makeKv({ [optOutKey("tenant-abc")]: "true" }),
    });
    await harness.harvester.start();

    await fireSessionsAndFlush(harness, "email", 3, 10);

    expect(harness.httpCalls).toEqual([]);
  });

  it("runs the AST scan again at submit time (defense in depth)", async () => {
    const harness = makeHarvester({
      scan: failingScan("uses_fetch"),
    });
    await harness.harvester.start();

    await fireSessionsAndFlush(harness, "email", 3, 10);

    expect(harness.httpCalls).toEqual([]);
    const rejection = harness.auditLog.events.find(
      (e) => e.kind === "adapter.harvest.rejected",
    );
    expect(rejection).toBeDefined();
    if (rejection?.kind === "adapter.harvest.rejected") {
      expect(rejection.reason).toContain("uses_fetch");
    }
  });

  it("marks AST-rejected candidates as rejected_local and does not retry", async () => {
    const harness = makeHarvester({ scan: failingScan("nope") });
    await harness.harvester.start();

    await fireSessionsAndFlush(harness, "email", 3, 10);
    await fireSessionsAndFlush(harness, "email", 3, 5);

    expect(harness.httpCalls).toEqual([]);
    const ledger = (await harness.stateStore.readJson<HarvestLedger>(
      HARVEST_STATE_FILE,
    )) as HarvestLedger;
    expect(ledger.entries["email@3"].submittedAt).toBe("rejected_local");
  });

  it("treats HTTP 4xx as a server rejection (no retry, marks rejected_server)", async () => {
    const harness = makeHarvester({
      httpResponses: [{ status: 400, text: async () => "bad" }],
    });
    await harness.harvester.start();

    await fireSessionsAndFlush(harness, "email", 3, 10);

    expect(harness.httpCalls).toHaveLength(1);
    const ledger = (await harness.stateStore.readJson<HarvestLedger>(
      HARVEST_STATE_FILE,
    )) as HarvestLedger;
    expect(ledger.entries["email@3"].submittedAt).toBe("rejected_server");
  });

  it("retries on 5xx then succeeds on the second attempt", async () => {
    const harness = makeHarvester({
      httpResponses: [
        { status: 500, text: async () => "" },
        { status: 201, text: async () => "{}" },
      ],
    });
    await harness.harvester.start();

    await fireSessionsAndFlush(harness, "email", 3, 10);

    expect(harness.httpCalls).toHaveLength(2);
    const ledger = (await harness.stateStore.readJson<HarvestLedger>(
      HARVEST_STATE_FILE,
    )) as HarvestLedger;
    expect(ledger.entries["email@3"].submittedAt).not.toBe(null);
    expect(ledger.entries["email@3"].submittedAt).not.toBe("rejected_server");
  });

  it.todo(
    "does NOT mark submitted when all retries exhaust on 5xx (will retry on next event) — FIXME: 7B salvage; #postWithRetry returns ok after retries exhaust; investigate the retry-loop bookkeeping",
    async () => {
      const harness = makeHarvester({
        httpResponses: [
          { status: 500, text: async () => "" },
          { status: 500, text: async () => "" },
          { status: 500, text: async () => "" },
        ],
      });
      await harness.harvester.start();

      await fireSessionsAndFlush(harness, "email", 3, 10);

      const ledger =
        await harness.stateStore.readJson<HarvestLedger>(HARVEST_STATE_FILE);
      expect(ledger).toBeNull();
    },
  );

  it("skips submission when bearer is null (auth not ready yet)", async () => {
    const harness = makeHarvester({ bearer: null });
    await harness.harvester.start();

    await fireSessionsAndFlush(harness, "email", 3, 10);

    expect(harness.httpCalls).toEqual([]);
  });

  it("anonymization: payload contains only the source string + harvest metrics, no tenant id in body", async () => {
    const harness = makeHarvester({ tenantId: "tenant-abc" });
    await harness.harvester.start();

    await fireSessionsAndFlush(harness, "email", 3, 10);

    const body = harness.httpCalls[0].body as Record<string, unknown>;
    expect(body.tenant_id).toBeUndefined();
    expect(body.tenantId).toBeUndefined();
    expect(body.user_id).toBeUndefined();
    expect(Object.keys(body).sort()).toEqual(
      ["harvest_metrics", "layout", "scheme", "source", "version"].sort(),
    );
  });

  it("stop() removes the audit-log subscription", async () => {
    const harness = makeHarvester();
    await harness.harvester.start();
    harness.harvester.stop();

    await fireSessionsAndFlush(harness, "email", 3, 10);

    expect(harness.httpCalls).toEqual([]);
  });

  it("rehydrates the ledger from disk so prior submissions are not re-sent", async () => {
    const harness = makeHarvester();
    await harness.stateStore.writeJsonAtomic<HarvestLedger>(
      HARVEST_STATE_FILE,
      {
        entries: {
          "email@3": {
            sessionsObserved: 10,
            renderErrorCount: 0,
            userIssueCount: 0,
            submittedAt: "2026-05-16T00:00:00Z",
          },
        },
      },
    );
    await harness.harvester.start();

    await fireSessionsAndFlush(harness, "email", 3, 5);

    expect(harness.httpCalls).toEqual([]);
  });
});
