import { getOptOut } from "./opt-out";
import {
  PROMOTED_INSTALLS_FILE,
  ledgerKey,
  type AdapterMetadata,
  type AdapterStateStore,
  type HttpFetch,
  type KeyValueStore,
  type LayoutTemplate,
  type LifecycleAuditLog,
  type PromotedInstallLedger,
  type QualityGate,
  type RegistryHost,
} from "./types";

export interface DownloadDeps {
  readonly registryHost: RegistryHost;
  readonly qualityGate: QualityGate;
  readonly stateStore: AdapterStateStore;
  readonly auditLog: LifecycleAuditLog;
  readonly http: HttpFetch;
  readonly registryBaseUrl: string;
  readonly bearer: string;
  readonly tenantId: string;
  readonly kv: KeyValueStore;
}

export interface DownloadResult {
  readonly considered: number;
  readonly installed: number;
  readonly rejected: number;
  readonly skippedOptOut: boolean;
  readonly networkError: boolean;
}

interface PromotedAdapterPayload {
  readonly scheme: string;
  readonly version: number;
  readonly layout: LayoutTemplate;
  readonly source: string;
  readonly metadata: AdapterMetadata;
}

interface PromotedListResponse {
  readonly adapters: ReadonlyArray<PromotedAdapterPayload>;
}

const LAYOUTS: ReadonlySet<LayoutTemplate> = new Set([
  "form",
  "table",
  "kanban",
  "definition-list",
]);

export async function runDownloadOnStart(
  deps: DownloadDeps,
): Promise<DownloadResult> {
  const optedOut = await getOptOut(
    {
      kv: deps.kv,
      registryHost: deps.registryHost,
      stateStore: deps.stateStore,
      auditLog: deps.auditLog,
    },
    deps.tenantId,
  );

  if (optedOut) {
    return {
      considered: 0,
      installed: 0,
      rejected: 0,
      skippedOptOut: true,
      networkError: false,
    };
  }

  const list = await fetchPromotedList(deps);
  if (!list.ok) {
    return {
      considered: 0,
      installed: 0,
      rejected: 0,
      skippedOptOut: false,
      networkError: true,
    };
  }

  let installed = 0;
  let rejected = 0;

  const existing = await deps.stateStore.readJson<PromotedInstallLedger>(
    PROMOTED_INSTALLS_FILE,
  );
  const ledgerEntries: Record<string, { scheme: string; version: number }> = {
    ...(existing?.entries ?? {}),
  };

  for (const candidate of list.value.adapters) {
    if (!isValidCandidate(candidate)) {
      rejected += 1;
      deps.auditLog.emit({
        kind: "adapter.download.rejected",
        scheme: candidate?.scheme ?? "<unknown>",
        version: candidate?.version ?? -1,
        reason: "invalid_payload",
      });
      continue;
    }

    const outcome = await deps.qualityGate.runAll({
      source: candidate.source,
      metadata: candidate.metadata,
    });

    if (!outcome.ok) {
      rejected += 1;
      deps.auditLog.emit({
        kind: "adapter.download.rejected",
        scheme: candidate.scheme,
        version: candidate.version,
        reason: outcome.code,
      });
      continue;
    }

    try {
      await deps.registryHost.installAdapter({
        scheme: candidate.scheme,
        version: candidate.version,
        source: candidate.source,
        metadata: candidate.metadata,
      });
    } catch {
      rejected += 1;
      deps.auditLog.emit({
        kind: "adapter.download.rejected",
        scheme: candidate.scheme,
        version: candidate.version,
        reason: "install_failed",
      });
      continue;
    }

    ledgerEntries[ledgerKey(candidate.scheme, candidate.version)] = {
      scheme: candidate.scheme,
      version: candidate.version,
    };
    installed += 1;
  }

  await deps.stateStore.writeJsonAtomic<PromotedInstallLedger>(
    PROMOTED_INSTALLS_FILE,
    { entries: ledgerEntries },
  );

  return {
    considered: list.value.adapters.length,
    installed,
    rejected,
    skippedOptOut: false,
    networkError: false,
  };
}

async function fetchPromotedList(
  deps: DownloadDeps,
): Promise<
  { ok: true; value: PromotedListResponse } | { ok: false; reason: string }
> {
  let response: Awaited<ReturnType<HttpFetch>> | null = null;
  try {
    response = await deps.http(
      `${trimSlash(deps.registryBaseUrl)}/v1/adapter_registry/promoted`,
      {
        method: "GET",
        headers: {
          accept: "application/json",
          authorization: `Bearer ${deps.bearer}`,
          "x-tenant-id": deps.tenantId,
        },
      },
    );
  } catch (err) {
    return {
      ok: false,
      reason: err instanceof Error ? err.message : "network",
    };
  }
  if (response.status < 200 || response.status >= 300) {
    return { ok: false, reason: `http_${response.status}` };
  }
  let parsed: unknown;
  try {
    const text = await response.text();
    parsed = JSON.parse(text);
  } catch (err) {
    return { ok: false, reason: err instanceof Error ? err.message : "parse" };
  }
  if (!isPromotedListResponse(parsed)) {
    return { ok: false, reason: "schema_mismatch" };
  }
  return { ok: true, value: parsed };
}

function isPromotedListResponse(value: unknown): value is PromotedListResponse {
  if (!value || typeof value !== "object") return false;
  const adapters = (value as { adapters?: unknown }).adapters;
  return Array.isArray(adapters);
}

function isValidCandidate(
  candidate: unknown,
): candidate is PromotedAdapterPayload {
  if (!candidate || typeof candidate !== "object") return false;
  const c = candidate as Record<string, unknown>;
  if (typeof c.scheme !== "string" || c.scheme.length === 0) return false;
  if (typeof c.version !== "number" || !Number.isFinite(c.version))
    return false;
  if (typeof c.source !== "string" || c.source.length === 0) return false;
  if (typeof c.layout !== "string" || !LAYOUTS.has(c.layout as LayoutTemplate))
    return false;
  const metadata = c.metadata as Record<string, unknown> | undefined;
  if (!metadata || typeof metadata !== "object") return false;
  if (metadata.origin !== "community") return false;
  if (typeof metadata.schemaVersion !== "number") return false;
  if (typeof metadata.generatedAt !== "string") return false;
  if (typeof metadata.generatorModel !== "string") return false;
  if (metadata.layout !== c.layout) return false;
  return true;
}

function trimSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}
