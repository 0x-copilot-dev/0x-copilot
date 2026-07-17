// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import type { BootStatusPayload } from "@0x-copilot/chat-transport";

import type { BootSecrets } from "./boot-secrets";
import { MigrationsFailed } from "./migrations";
import { FatalCrashLoop } from "./python-service";
import type { SupervisedServiceName } from "./runtime-paths";
import {
  ServiceSupervisor,
  type PostgresController,
  type ServiceController,
  type SupervisorDeps,
} from "./supervisor";

const SECRETS: BootSecrets = {
  authSecret: "as",
  serviceToken: "st",
  vaultSecret: "vs",
  pgPassword: "pg-pass",
  auditHmacKey: "ah",
};

interface Harness {
  supervisor: ServiceSupervisor;
  log: string[];
  statuses: BootStatusPayload[];
  fatalHooks: Map<SupervisedServiceName, (err: FatalCrashLoop) => void>;
}

interface HarnessOverrides {
  loadSecrets?: SupervisorDeps["loadSecrets"];
  runMigrations?: SupervisorDeps["runMigrations"];
  waitForHealthy?: SupervisorDeps["waitForHealthy"];
  postgresStart?: () => Promise<void>;
}

function makeHarness(overrides: HarnessOverrides = {}): Harness {
  const log: string[] = [];
  const statuses: BootStatusPayload[] = [];
  const fatalHooks = new Map<
    SupervisedServiceName,
    (err: FatalCrashLoop) => void
  >();

  const deps: SupervisorDeps = {
    loadSecrets:
      overrides.loadSecrets ??
      (() => {
        log.push("secrets");
        return Promise.resolve(SECRETS);
      }),
    allocatePorts: (count) => {
      log.push(`ports(${count})`);
      return Promise.resolve([5432, 8101, 8001, 8201].slice(0, count));
    },
    createPostgres: ({ port, password }): PostgresController => {
      log.push(`pg-create(${port},${password})`);
      return {
        start: () => {
          log.push("pg-start");
          return overrides.postgresStart?.() ?? Promise.resolve();
        },
        ensureDatabase: (name) => {
          log.push(`pg-db(${name})`);
          return Promise.resolve();
        },
        stop: () => {
          log.push("pg-stop");
          return Promise.resolve();
        },
      };
    },
    runMigrations:
      overrides.runMigrations ??
      ((service) => {
        log.push(`migrate(${service})`);
        return Promise.resolve();
      }),
    createService: (name, { ports, onFatal }): ServiceController => {
      log.push(`svc-create(${name})`);
      expect(ports).toEqual({
        pg: 5432,
        backend: 8101,
        aiBackend: 8001,
        facade: 8201,
      });
      fatalHooks.set(name, onFatal);
      return {
        start: () => {
          log.push(`svc-start(${name})`);
        },
        stop: () => {
          log.push(`svc-stop(${name})`);
          return Promise.resolve();
        },
      };
    },
    waitForHealthy:
      overrides.waitForHealthy ??
      ((name) => {
        log.push(`health(${name})`);
        return Promise.resolve();
      }),
  };

  const supervisor = new ServiceSupervisor(deps);
  supervisor.onStatus((status) => statuses.push(status));
  return { supervisor, log, statuses, fatalHooks };
}

describe("ServiceSupervisor.start", () => {
  it("boots in order: secrets -> ports -> postgres -> migrations -> children -> health -> ready", async () => {
    const h = makeHarness();
    const { facadeUrl } = await h.supervisor.start();

    expect(facadeUrl).toBe("http://127.0.0.1:8201");
    expect(h.supervisor.facadeUrl).toBe(facadeUrl);
    expect(h.supervisor.state).toBe("ready");
    expect(h.log).toEqual([
      "secrets",
      "ports(4)",
      "pg-create(5432,pg-pass)",
      "pg-start",
      "pg-db(atlas_backend)",
      "pg-db(atlas_ai)",
      "migrate(backend)",
      "migrate(ai-backend)",
      "svc-create(backend)",
      "svc-create(ai-backend)",
      "svc-create(backend-facade)",
      "svc-start(backend)",
      "svc-start(ai-backend)",
      "svc-start(backend-facade)",
      "health(backend)",
      "health(ai-backend)",
      "health(backend-facade)",
    ]);
  });

  it("emits the boot:status sequence with monotonic percent and terminal ready", async () => {
    const h = makeHarness();
    await h.supervisor.start();

    expect(h.statuses.map((s) => s.phase)).toEqual([
      "secrets",
      "ports",
      "postgres",
      "migrations",
      "services",
      "health",
      "ready",
    ]);
    const percents = h.statuses.map((s) => s.percent);
    expect([...percents].sort((a, b) => a - b)).toEqual(percents);
    expect(h.statuses.at(-1)).toEqual({
      phase: "ready",
      message: "Ready",
      percent: 100,
    });
    expect(h.statuses.every((s) => s.fatal !== true)).toBe(true);
    expect(h.supervisor.latestStatus?.phase).toBe("ready");
  });

  it("gates facade health behind backend + ai-backend health", async () => {
    const order: string[] = [];
    let releaseBackends: (() => void) | null = null;
    const backendsHealthy = new Promise<void>((resolve) => {
      releaseBackends = resolve;
    });
    const h = makeHarness({
      waitForHealthy: async (name) => {
        order.push(`health-req(${name})`);
        if (name !== "backend-facade") {
          await backendsHealthy;
        }
        order.push(`health-ok(${name})`);
      },
    });
    const startPromise = h.supervisor.start();
    // Flush microtasks so start() progresses until it blocks on the
    // backend/ai-backend health gate.
    await new Promise((resolve) => setImmediate(resolve));
    expect(order).toContain("health-req(backend)");
    expect(order).toContain("health-req(ai-backend)");
    // The facade request must not have been issued yet.
    expect(order).not.toContain("health-req(backend-facade)");
    releaseBackends!();
    await startPromise;
    expect(order.at(-1)).toBe("health-ok(backend-facade)");
  });

  it("migration failure -> fatal status, children never created, start rejects", async () => {
    const failure = new MigrationsFailed("backend", 2, "FATAL: boom");
    const h = makeHarness({
      runMigrations: () => Promise.reject(failure),
    });
    await expect(h.supervisor.start()).rejects.toBe(failure);
    expect(h.supervisor.state).toBe("fatal");
    const last = h.statuses.at(-1)!;
    expect(last.fatal).toBe(true);
    expect(last.phase).toBe("migrations");
    expect(last.message).toContain("FATAL: boom");
    expect(h.log.some((l) => l.startsWith("svc-create"))).toBe(false);
  });

  it("secrets failure -> fatal at the secrets phase", async () => {
    const h = makeHarness({
      loadSecrets: () => Promise.reject(new Error("BootSecretsUnreadable")),
    });
    await expect(h.supervisor.start()).rejects.toThrow(
      /BootSecretsUnreadable/u,
    );
    const last = h.statuses.at(-1)!;
    expect(last).toMatchObject({ phase: "secrets", fatal: true });
  });

  it("start() cannot be called twice", async () => {
    const h = makeHarness();
    await h.supervisor.start();
    await expect(h.supervisor.start()).rejects.toThrow(/state "ready"/u);
  });
});

describe("ServiceSupervisor.stop", () => {
  it("stops in reverse order: facade -> ai-backend -> backend -> postgres", async () => {
    const h = makeHarness();
    await h.supervisor.start();
    h.log.length = 0;
    await h.supervisor.stop();
    expect(h.log).toEqual([
      "svc-stop(backend-facade)",
      "svc-stop(ai-backend)",
      "svc-stop(backend)",
      "pg-stop",
    ]);
    expect(h.supervisor.state).toBe("stopped");
  });

  it("is idempotent — concurrent and repeated calls share one pass", async () => {
    const h = makeHarness();
    await h.supervisor.start();
    h.log.length = 0;
    await Promise.all([h.supervisor.stop(), h.supervisor.stop()]);
    await h.supervisor.stop();
    expect(h.log.filter((l) => l === "pg-stop")).toHaveLength(1);
  });

  it("stops postgres even when it is the only thing started (boot failed later)", async () => {
    const failure = new MigrationsFailed("backend", 1, "x");
    const h = makeHarness({
      runMigrations: () => Promise.reject(failure),
    });
    await expect(h.supervisor.start()).rejects.toBe(failure);
    h.log.length = 0;
    await h.supervisor.stop();
    expect(h.log).toEqual(["pg-stop"]);
  });

  it("keeps stopping children after one child stop throws", async () => {
    const h = makeHarness();
    await h.supervisor.start();
    // Sabotage the facade's stop by re-wiring the log call to throw.
    const original = h.log.push.bind(h.log);
    const pushSpy = vi
      .spyOn(h.log, "push")
      .mockImplementation((entry: string) => {
        if (entry === "svc-stop(backend-facade)") {
          throw new Error("stop failed");
        }
        return original(entry);
      });
    await h.supervisor.stop();
    pushSpy.mockRestore();
    expect(h.log).toContain("pg-stop");
    expect(h.log).toContain("svc-stop(backend)");
  });
});

describe("crash-loop propagation", () => {
  it("a FatalCrashLoop after ready emits a fatal services status", async () => {
    const h = makeHarness();
    await h.supervisor.start();
    const err = new FatalCrashLoop("ai-backend", 5, 300_000);
    h.fatalHooks.get("ai-backend")!(err);
    const last = h.statuses.at(-1)!;
    expect(last.fatal).toBe(true);
    expect(last.phase).toBe("services");
    expect(last.message).toContain("ai-backend");
    expect(h.supervisor.state).toBe("fatal");
  });

  it("ignores crash-loop reports while stopping", async () => {
    const h = makeHarness();
    await h.supervisor.start();
    await h.supervisor.stop();
    const count = h.statuses.length;
    h.fatalHooks.get("backend")!(new FatalCrashLoop("backend", 5, 300_000));
    expect(h.statuses).toHaveLength(count);
  });
});
