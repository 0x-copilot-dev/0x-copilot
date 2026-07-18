// ActivityRoute — host binder for the Activity destination
// (desktop redesign, Phase 4 · PR-4.6).
//
// Renders the package-shipped `<ActivityDestination>` (PR-4.5, pure
// presentation) and owns everything the component intentionally does not:
//   1. Fetch + composition — `fetchActivity` composes
//      `/v1/agent/conversations` + `/v1/audit` into a flat, newest-first
//      `ActivityRunRow[]` wrapped in a `SectionResult` (FR-4.19; the
//      component stays endpoint-agnostic).
//   2. The 4-state machine (FR-4.2) — `null` while the first fetch is in
//      flight (loading skeleton), then the resolved `SectionResult`
//      (`ok` with rows → feed, `ok` empty → "No activity yet",
//      `error` → Retry). Held in local state.
//   3. `now` — captured once per load so the in-shell day grouping +
//      relative times are stable across re-renders and refresh on retry
//      (FR-4.4 / FR-4.14).
//   4. Navigation hand-off — the host passes `onOpenRun` (→ Run cockpit)
//      and `onOpenRetentionSettings` (→ Settings → Privacy & data). Per
//      PRD §5, Settings is a web-only screen reached through a callback,
//      NOT an `ArtifactRoute`, so both navigation targets are host
//      callbacks the App wires to `router.navigate(...)` at dispatch
//      time (PR-4.11 IA-fold). `onRetry` refetches.
//
// Boundary: no `apps/*` → `apps/*` import; the component comes from
// `@0x-copilot/chat-surface`, the wire types from `@0x-copilot/api-types`
// (FR-4.32).

import { useEffect, useState, type ReactElement } from "react";

import { ActivityDestination } from "@0x-copilot/chat-surface";
import type {
  ActivityRunRow,
  RunId,
  SectionResult,
} from "@0x-copilot/api-types";

import type { RequestIdentity } from "../../api/config";
import { fetchActivity } from "./api/activityApi";

export interface ActivityRouteProps {
  readonly identity: RequestIdentity;
  /**
   * Open the live Run cockpit for a running run (FR-4.16). The host wires
   * this to `router.navigate` toward the Run destination — a callback,
   * not an in-component navigation, so the route stays decoupled from the
   * host's `AppRoute` union.
   */
  readonly onOpenRun: (runId: RunId) => void;
  /**
   * Open Settings → Privacy & data, where retention / export / delete
   * live (FR-4.17). Settings is a web-only host screen (PRD §5), reached
   * through this callback rather than an `ArtifactRoute`.
   */
  readonly onOpenRetentionSettings: () => void;
  /** BCP-47 locale for the explicit-date day dividers; defaults to runtime. */
  readonly locale?: string;
}

/** Route-level `data-state` for the wrapper section (mirrors sibling routes). */
function routeDataState(
  result: SectionResult<ActivityRunRow[]> | null,
): "loading" | "error" | "empty" | "ready" {
  if (result === null) return "loading";
  if (result.status === "error") return "error";
  if (result.status === "unavailable") return "empty";
  return (result.data?.length ?? 0) === 0 ? "empty" : "ready";
}

export function ActivityRoute({
  identity,
  onOpenRun,
  onOpenRetentionSettings,
  locale,
}: ActivityRouteProps): ReactElement {
  // `null` = first load in flight → the destination renders its loading
  // skeleton (FR-4.2). Once resolved, the SectionResult drives the
  // error / empty / ready branches inside the component.
  const [result, setResult] = useState<SectionResult<ActivityRunRow[]> | null>(
    null,
  );
  // Reference instant for day grouping + relative time. Captured at load
  // (not per render) so the feed doesn't re-bucket on every re-render;
  // refreshed on each (re)fetch so a long-open tab's "Today" stays honest.
  const [now, setNow] = useState<number>(() => Date.now());
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setResult(null);

    // `fetchActivity` resolves for both success AND failure (it maps
    // errors into a `SectionResult` with `status:"error"`), so there is
    // no rejection branch to handle here.
    void fetchActivity(identity).then((next) => {
      if (cancelled) return;
      setNow(Date.now());
      setResult(next);
    });

    return () => {
      cancelled = true;
    };
  }, [identity, reloadToken]);

  return (
    <section
      aria-label="Activity destination"
      data-testid="activity-route"
      data-state={routeDataState(result)}
      style={{ height: "100%", width: "100%", overflow: "auto" }}
    >
      <ActivityDestination
        items={result}
        now={now}
        locale={locale}
        onOpenRun={onOpenRun}
        onOpenRetentionSettings={onOpenRetentionSettings}
        onRetry={() => setReloadToken((token) => token + 1)}
      />
    </section>
  );
}
