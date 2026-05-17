import {
  PROMOTED_INSTALLS_FILE,
  optOutKey,
  type AdapterStateStore,
  type KeyValueStore,
  type LifecycleAuditLog,
  type PromotedInstallLedger,
  type RegistryHost,
} from "./types";

export interface OptOutDeps {
  readonly kv: KeyValueStore;
  readonly registryHost: RegistryHost;
  readonly stateStore: AdapterStateStore;
  readonly auditLog: LifecycleAuditLog;
}

export async function getOptOut(
  deps: OptOutDeps,
  tenantId: string,
): Promise<boolean> {
  const raw = await deps.kv.get(optOutKey(tenantId));
  return raw === "true";
}

export async function setOptOut(
  deps: OptOutDeps,
  tenantId: string,
  optedOut: boolean,
): Promise<void> {
  const previous = await getOptOut(deps, tenantId);
  await deps.kv.set(optOutKey(tenantId), optedOut ? "true" : "false");

  if (optedOut && !previous) {
    await uninstallAllPromoted(deps);
    deps.auditLog.emit({ kind: "adapter.optout.enabled", tenantId });
    return;
  }

  if (!optedOut && previous) {
    deps.auditLog.emit({ kind: "adapter.optout.disabled", tenantId });
  }
}

async function uninstallAllPromoted(deps: OptOutDeps): Promise<void> {
  const ledger = await deps.stateStore.readJson<PromotedInstallLedger>(
    PROMOTED_INSTALLS_FILE,
  );
  if (!ledger) return;

  const remaining: Record<string, never> = {};
  for (const entry of Object.values(ledger.entries)) {
    try {
      await deps.registryHost.uninstallAdapter(entry.scheme, entry.version);
      deps.auditLog.emit({
        kind: "adapter.optout.uninstalled",
        scheme: entry.scheme,
        version: entry.version,
      });
    } catch {
      // Uninstall failure is non-fatal: the adapter may already be missing
      // from the registry. The ledger is cleared either way so we don't
      // loop on it.
    }
  }

  await deps.stateStore.writeJsonAtomic<PromotedInstallLedger>(
    PROMOTED_INSTALLS_FILE,
    { entries: remaining },
  );
}
