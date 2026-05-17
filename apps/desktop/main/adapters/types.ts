export type LayoutTemplate = "form" | "table" | "kanban" | "definition-list";

export type AdapterOrigin = "agent-generated" | "community";

export interface AdapterMetadata {
  readonly origin: AdapterOrigin;
  readonly generatedAt: string;
  readonly generatorModel: string;
  readonly schemaVersion: number;
  readonly layout: LayoutTemplate;
}

export type LifecycleEvent =
  | {
      readonly kind: "adapter.session.completed";
      readonly scheme: string;
      readonly version: number;
      readonly renderErrorCount: number;
    }
  | {
      readonly kind: "adapter.user_issue.reported";
      readonly scheme: string;
      readonly version: number;
    }
  | {
      readonly kind: "adapter.installed";
      readonly scheme: string;
      readonly version: number;
      readonly origin: AdapterOrigin;
    }
  | {
      readonly kind: "adapter.broken";
      readonly scheme: string;
      readonly version: number;
      readonly reason: string;
    }
  | {
      readonly kind: "adapter.harvest.submitted";
      readonly scheme: string;
      readonly version: number;
    }
  | {
      readonly kind: "adapter.harvest.rejected";
      readonly scheme: string;
      readonly version: number;
      readonly reason: string;
    }
  | {
      readonly kind: "adapter.download.rejected";
      readonly scheme: string;
      readonly version: number;
      readonly reason: string;
    }
  | {
      readonly kind: "adapter.optout.enabled";
      readonly tenantId: string;
    }
  | {
      readonly kind: "adapter.optout.disabled";
      readonly tenantId: string;
    }
  | {
      readonly kind: "adapter.optout.uninstalled";
      readonly scheme: string;
      readonly version: number;
    };

export interface LifecycleAuditLog {
  readonly subscribe: (handler: (e: LifecycleEvent) => void) => () => void;
  readonly emit: (e: LifecycleEvent) => void;
}

export interface RegistryHost {
  readonly installAdapter: (args: {
    readonly scheme: string;
    readonly version: number;
    readonly source: string;
    readonly metadata: AdapterMetadata;
  }) => Promise<void>;
  readonly uninstallAdapter: (scheme: string, version: number) => Promise<void>;
  readonly readAdapterSource: (
    scheme: string,
    version: number,
  ) => Promise<string>;
}

export type QualityGateOutcome =
  | { readonly ok: true }
  | {
      readonly ok: false;
      readonly code: "schema" | "allowlist" | "smoke_render";
      readonly detail: string;
    };

export interface QualityGate {
  readonly runAll: (args: {
    readonly source: string;
    readonly metadata: AdapterMetadata;
  }) => Promise<QualityGateOutcome>;
}

export interface AstAllowlistScan {
  readonly scan: (
    source: string,
  ) => { readonly ok: true } | { readonly ok: false; readonly reason: string };
}

export interface KeyValueStore {
  readonly get: (key: string) => Promise<string | null>;
  readonly set: (key: string, value: string) => Promise<void>;
  readonly delete: (key: string) => Promise<void>;
}

export interface AdapterStateStore {
  readonly readJson: <T>(name: string) => Promise<T | null>;
  readonly writeJsonAtomic: <T>(name: string, value: T) => Promise<void>;
}

export interface HttpResponseLike {
  readonly status: number;
  readonly text: () => Promise<string>;
}

export type HttpFetch = (
  url: string,
  init: {
    readonly method: string;
    readonly headers: Record<string, string>;
    readonly body?: string;
  },
) => Promise<HttpResponseLike>;

export interface HarvestState {
  readonly sessionsObserved: number;
  readonly renderErrorCount: number;
  readonly userIssueCount: number;
  readonly submittedAt: string | null;
}

export interface HarvestLedger {
  readonly entries: Readonly<Record<string, HarvestState>>;
}

export interface PromotedInstallEntry {
  readonly scheme: string;
  readonly version: number;
}

export interface PromotedInstallLedger {
  readonly entries: Readonly<Record<string, PromotedInstallEntry>>;
}

export const HARVEST_STATE_FILE = "harvest-state.json";
export const PROMOTED_INSTALLS_FILE = "promoted-installs.json";
export const SUBMISSION_THRESHOLD_SESSIONS = 10;
export const OPT_OUT_KEY_PREFIX = "adapter-registry.opt-out.";

export function ledgerKey(scheme: string, version: number): string {
  return `${scheme}@${version}`;
}

export function optOutKey(tenantId: string): string {
  return `${OPT_OUT_KEY_PREFIX}${tenantId}`;
}
