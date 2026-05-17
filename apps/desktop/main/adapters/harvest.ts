import { getOptOut } from "./opt-out";
import {
  HARVEST_STATE_FILE,
  SUBMISSION_THRESHOLD_SESSIONS,
  ledgerKey,
  type AdapterStateStore,
  type AstAllowlistScan,
  type HarvestLedger,
  type HarvestState,
  type HttpFetch,
  type KeyValueStore,
  type LifecycleAuditLog,
  type LifecycleEvent,
  type RegistryHost,
} from "./types";

export interface HarvesterDeps {
  readonly auditLog: LifecycleAuditLog;
  readonly registryHost: RegistryHost;
  readonly astScan: AstAllowlistScan;
  readonly stateStore: AdapterStateStore;
  readonly http: HttpFetch;
  readonly registryBaseUrl: string;
  readonly bearer: () => string | null;
  readonly tenantId: string;
  readonly kv: KeyValueStore;
  readonly clock?: () => number;
  readonly sleep?: (ms: number) => Promise<void>;
  readonly adapterMetadata: (
    scheme: string,
    version: number,
  ) => Promise<HarvestSubmissionMetadata | null>;
}

export interface HarvestSubmissionMetadata {
  readonly layout: "form" | "table" | "kanban" | "definition-list";
  readonly generatedAt: string;
  readonly generatorModel: string;
}

const BACKOFFS_MS = [1000, 3000, 10000];

const REJECTED_LOCAL = "rejected_local";
const REJECTED_SERVER = "rejected_server";

export class Harvester {
  readonly #deps: HarvesterDeps;
  #unsubscribe: (() => void) | null = null;
  #ledger: HarvestLedger = { entries: {} };
  #inflight: Promise<void> = Promise.resolve();

  constructor(deps: HarvesterDeps) {
    this.#deps = deps;
  }

  async start(): Promise<void> {
    const persisted =
      await this.#deps.stateStore.readJson<HarvestLedger>(HARVEST_STATE_FILE);
    this.#ledger = persisted ?? { entries: {} };

    this.#unsubscribe = this.#deps.auditLog.subscribe((event) => {
      const next = this.#applyEvent(event);
      if (!next) return;
      this.#queueSubmissionAttempt(next.scheme, next.version);
    });
  }

  stop(): void {
    if (this.#unsubscribe) {
      this.#unsubscribe();
      this.#unsubscribe = null;
    }
  }

  // Test seam: tests can await the in-flight submission chain.
  flush(): Promise<void> {
    return this.#inflight;
  }

  #applyEvent(
    event: LifecycleEvent,
  ): { scheme: string; version: number } | null {
    if (event.kind === "adapter.session.completed") {
      return this.#bumpSession(
        event.scheme,
        event.version,
        event.renderErrorCount,
      );
    }
    if (event.kind === "adapter.user_issue.reported") {
      return this.#bumpIssue(event.scheme, event.version);
    }
    return null;
  }

  #bumpSession(
    scheme: string,
    version: number,
    renderErrorCount: number,
  ): { scheme: string; version: number } {
    const key = ledgerKey(scheme, version);
    const prev = this.#ledger.entries[key] ?? blankState();
    const next: HarvestState = {
      sessionsObserved: prev.sessionsObserved + 1,
      renderErrorCount: prev.renderErrorCount + Math.max(0, renderErrorCount),
      userIssueCount: prev.userIssueCount,
      submittedAt: prev.submittedAt,
    };
    this.#ledger = {
      entries: { ...this.#ledger.entries, [key]: next },
    };
    return { scheme, version };
  }

  #bumpIssue(
    scheme: string,
    version: number,
  ): { scheme: string; version: number } {
    const key = ledgerKey(scheme, version);
    const prev = this.#ledger.entries[key] ?? blankState();
    const next: HarvestState = {
      sessionsObserved: prev.sessionsObserved,
      renderErrorCount: prev.renderErrorCount,
      userIssueCount: prev.userIssueCount + 1,
      submittedAt: prev.submittedAt,
    };
    this.#ledger = {
      entries: { ...this.#ledger.entries, [key]: next },
    };
    return { scheme, version };
  }

  #queueSubmissionAttempt(scheme: string, version: number): void {
    this.#inflight = this.#inflight
      .catch(() => {})
      .then(() => this.#trySubmit(scheme, version));
  }

  async #trySubmit(scheme: string, version: number): Promise<void> {
    if (await getOptOut(this.#deps, this.#deps.tenantId)) return;

    const key = ledgerKey(scheme, version);
    const state = this.#ledger.entries[key];
    if (!state) return;
    if (state.submittedAt !== null) return;
    if (state.sessionsObserved < SUBMISSION_THRESHOLD_SESSIONS) return;
    if (state.renderErrorCount !== 0) return;
    if (state.userIssueCount !== 0) return;

    const metadata = await this.#deps.adapterMetadata(scheme, version);
    if (!metadata) return;

    const source = await this.#deps.registryHost.readAdapterSource(
      scheme,
      version,
    );

    const scan = this.#deps.astScan.scan(source);
    if (!scan.ok) {
      await this.#markSubmitted(scheme, version, REJECTED_LOCAL);
      this.#deps.auditLog.emit({
        kind: "adapter.harvest.rejected",
        scheme,
        version,
        reason: `ast_scan_failed:${scan.reason}`,
      });
      return;
    }

    const outcome = await this.#postWithRetry(
      scheme,
      version,
      source,
      metadata,
      state,
    );

    if (outcome === "ok") {
      await this.#markSubmitted(
        scheme,
        version,
        new Date(this.#now()).toISOString(),
      );
      this.#deps.auditLog.emit({
        kind: "adapter.harvest.submitted",
        scheme,
        version,
      });
      return;
    }

    if (outcome === "rejected") {
      await this.#markSubmitted(scheme, version, REJECTED_SERVER);
      this.#deps.auditLog.emit({
        kind: "adapter.harvest.rejected",
        scheme,
        version,
        reason: "server_4xx",
      });
    }
  }

  async #postWithRetry(
    scheme: string,
    version: number,
    source: string,
    metadata: HarvestSubmissionMetadata,
    state: HarvestState,
  ): Promise<"ok" | "rejected" | "retry_exhausted"> {
    const bearer = this.#deps.bearer();
    if (!bearer) return "retry_exhausted";

    const body = JSON.stringify({
      scheme,
      version,
      layout: metadata.layout,
      source,
      harvest_metrics: {
        sessions_observed: state.sessionsObserved,
        render_error_count: state.renderErrorCount,
        user_issue_count: state.userIssueCount,
        generated_at: metadata.generatedAt,
        generator_model: metadata.generatorModel,
      },
    });

    for (let attempt = 0; attempt < BACKOFFS_MS.length; attempt++) {
      let response: Awaited<ReturnType<HttpFetch>> | null = null;
      try {
        response = await this.#deps.http(
          `${trimSlash(this.#deps.registryBaseUrl)}/v1/adapter_registry/candidates`,
          {
            method: "POST",
            headers: {
              "content-type": "application/json",
              authorization: `Bearer ${bearer}`,
              "x-tenant-id": this.#deps.tenantId,
            },
            body,
          },
        );
      } catch {
        // network error — fall through to retry
      }

      if (response) {
        if (response.status >= 200 && response.status < 300) return "ok";
        if (response.status >= 400 && response.status < 500) return "rejected";
      }

      if (attempt + 1 < BACKOFFS_MS.length) {
        await this.#sleep(BACKOFFS_MS[attempt + 1]);
      }
    }
    return "retry_exhausted";
  }

  async #markSubmitted(
    scheme: string,
    version: number,
    sentinel: string,
  ): Promise<void> {
    const key = ledgerKey(scheme, version);
    const prev = this.#ledger.entries[key] ?? blankState();
    const next: HarvestState = { ...prev, submittedAt: sentinel };
    this.#ledger = {
      entries: { ...this.#ledger.entries, [key]: next },
    };
    await this.#deps.stateStore.writeJsonAtomic<HarvestLedger>(
      HARVEST_STATE_FILE,
      this.#ledger,
    );
  }

  #now(): number {
    return this.#deps.clock ? this.#deps.clock() : Date.now();
  }

  #sleep(ms: number): Promise<void> {
    if (this.#deps.sleep) return this.#deps.sleep(ms);
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}

function blankState(): HarvestState {
  return {
    sessionsObserved: 0,
    renderErrorCount: 0,
    userIssueCount: 0,
    submittedAt: null,
  };
}

function trimSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}
