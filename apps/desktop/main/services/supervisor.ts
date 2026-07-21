import type { BootPhase, BootStatusPayload } from "@0x-copilot/chat-transport";

import type { BootSecrets } from "./boot-secrets";
import type { FatalCrashLoop } from "./python-service";
import type { SupervisedServiceName } from "./runtime-paths";
import { AI_BACKEND_DB_NAME, BACKEND_DB_NAME } from "./service-env";

// Orchestration only. Every capability (secrets, ports, postgres,
// migrations, children, health) is a narrow injected dependency so the
// unit tests drive the full boot/stop state machine with fakes; the real
// wiring lives in desktop-supervisor.ts.

export interface AllocatedPorts {
  readonly pg: number;
  readonly backend: number;
  readonly aiBackend: number;
  readonly facade: number;
}

export interface ServiceController {
  start(): void;
  stop(): Promise<void>;
}

export interface PostgresController {
  start(): Promise<void>;
  ensureDatabase(name: string): Promise<void>;
  stop(): Promise<void>;
}

export interface SupervisorDeps {
  loadSecrets(): Promise<BootSecrets>;
  allocatePorts(count: number): Promise<number[]>;
  createPostgres(opts: { port: number; password: string }): PostgresController;
  runMigrations(
    service: "backend" | "ai-backend",
    opts: { ports: AllocatedPorts; secrets: BootSecrets },
  ): Promise<void>;
  createService(
    name: SupervisedServiceName,
    opts: {
      ports: AllocatedPorts;
      secrets: BootSecrets;
      onFatal: (err: FatalCrashLoop) => void;
    },
  ): ServiceController;
  waitForHealthy(name: SupervisedServiceName, baseUrl: string): Promise<void>;
}

export type BootStatusListener = (status: BootStatusPayload) => void;

type SupervisorState =
  | "idle"
  | "starting"
  | "ready"
  | "fatal"
  | "stopping"
  | "stopped";

const PHASE_PERCENT: Record<BootPhase, number> = {
  secrets: 5,
  ports: 12,
  postgres: 25,
  migrations: 40,
  services: 60,
  health: 75,
  ready: 100,
  stopping: 100,
};

// Child start order; stop() walks it in reverse (facade first so nothing
// routes onto backends that are going away, postgres last).
const CHILD_ORDER: readonly SupervisedServiceName[] = [
  "backend",
  "ai-backend",
  "backend-facade",
];

export class ServiceSupervisor {
  readonly #deps: SupervisorDeps;
  readonly #listeners = new Set<BootStatusListener>();
  #latestStatus: BootStatusPayload | null = null;
  #state: SupervisorState = "idle";
  #postgres: PostgresController | null = null;
  #children: Array<{
    name: SupervisedServiceName;
    controller: ServiceController;
  }> = [];
  #facadeUrl: string | null = null;
  #stopPromise: Promise<void> | null = null;

  constructor(deps: SupervisorDeps) {
    this.#deps = deps;
  }

  onStatus(listener: BootStatusListener): () => void {
    this.#listeners.add(listener);
    return () => {
      this.#listeners.delete(listener);
    };
  }

  get latestStatus(): BootStatusPayload | null {
    return this.#latestStatus;
  }

  get facadeUrl(): string | null {
    return this.#facadeUrl;
  }

  get state(): SupervisorState {
    return this.#state;
  }

  async start(): Promise<{ facadeUrl: string; hostToken: string }> {
    if (this.#state !== "idle") {
      throw new Error(`supervisor.start() called in state "${this.#state}"`);
    }
    this.#state = "starting";

    const secrets = await this.#phase(
      "secrets",
      "Unlocking secure storage…",
      () => this.#deps.loadSecrets(),
    );

    const ports = await this.#phase(
      "ports",
      "Preparing your workspace…",
      async (): Promise<AllocatedPorts> => {
        const [pg, backend, aiBackend, facade] =
          await this.#deps.allocatePorts(4);
        return { pg, backend, aiBackend, facade };
      },
    );

    await this.#phase("postgres", "Starting the local database…", async () => {
      const postgres = this.#deps.createPostgres({
        port: ports.pg,
        password: secrets.pgPassword,
      });
      this.#postgres = postgres;
      await postgres.start();
      await postgres.ensureDatabase(BACKEND_DB_NAME);
      await postgres.ensureDatabase(AI_BACKEND_DB_NAME);
    });

    await this.#phase("migrations", "Setting up the database…", async () => {
      await this.#deps.runMigrations("backend", { ports, secrets });
      await this.#deps.runMigrations("ai-backend", { ports, secrets });
    });

    await this.#phase("services", "Starting 0xCopilot…", () => {
      const onFatal = (err: FatalCrashLoop): void => {
        this.#onFatalCrashLoop(err);
      };
      for (const name of CHILD_ORDER) {
        const controller = this.#deps.createService(name, {
          ports,
          secrets,
          onFatal,
        });
        this.#children.push({ name, controller });
      }
      // Children start in parallel; the health gate below sequences
      // readiness (backend + ai-backend first, then facade).
      for (const child of this.#children) {
        child.controller.start();
      }
      return Promise.resolve();
    });

    await this.#phase("health", "Finishing up…", async () => {
      await Promise.all([
        this.#deps.waitForHealthy(
          "backend",
          `http://127.0.0.1:${ports.backend}`,
        ),
        this.#deps.waitForHealthy(
          "ai-backend",
          `http://127.0.0.1:${ports.aiBackend}`,
        ),
      ]);
      await this.#deps.waitForHealthy(
        "backend-facade",
        `http://127.0.0.1:${ports.facade}`,
      );
    });

    this.#facadeUrl = `http://127.0.0.1:${ports.facade}`;
    this.#state = "ready";
    this.#emit({ phase: "ready", message: "Ready", percent: 100 });
    // hostToken: the per-install ENTERPRISE_SERVICE_TOKEN. Handed to the auth
    // layer so "Use locally" can mint the device-account session — the token
    // is what proves the mint request comes from THIS app's main process.
    return { facadeUrl: this.#facadeUrl, hostToken: secrets.serviceToken };
  }

  // Reverse boot order: facade -> ai-backend -> backend -> postgres.
  // Idempotent; concurrent callers share one stop pass.
  stop(): Promise<void> {
    if (this.#stopPromise !== null) return this.#stopPromise;
    this.#stopPromise = this.#doStop();
    return this.#stopPromise;
  }

  async #doStop(): Promise<void> {
    this.#state = "stopping";
    this.#emit({
      phase: "stopping",
      message: "Shutting down services…",
      percent: 100,
    });
    const children = [...this.#children].reverse();
    this.#children = [];
    for (const child of children) {
      try {
        await child.controller.stop();
      } catch {
        // Shutdown is best-effort; keep going so postgres still stops.
      }
    }
    const postgres = this.#postgres;
    this.#postgres = null;
    if (postgres !== null) {
      try {
        await postgres.stop();
      } catch {
        // Best-effort.
      }
    }
    this.#state = "stopped";
  }

  async #phase<T>(
    phase: BootPhase,
    message: string,
    body: () => Promise<T>,
  ): Promise<T> {
    this.#emit({ phase, message, percent: PHASE_PERCENT[phase] });
    try {
      return await body();
    } catch (err) {
      this.#state = "fatal";
      this.#emit({
        phase,
        message: err instanceof Error ? err.message : String(err),
        percent: PHASE_PERCENT[phase],
        fatal: true,
      });
      throw err;
    }
  }

  #onFatalCrashLoop(err: FatalCrashLoop): void {
    if (this.#state === "stopping" || this.#state === "stopped") return;
    this.#state = "fatal";
    this.#emit({
      phase: "services",
      message: err.message,
      percent: this.#latestStatus?.percent ?? PHASE_PERCENT.services,
      fatal: true,
    });
  }

  #emit(status: BootStatusPayload): void {
    this.#latestStatus = status;
    for (const listener of this.#listeners) {
      listener(status);
    }
  }
}
