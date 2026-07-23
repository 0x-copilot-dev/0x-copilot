// <RoutineDetail /> — Routines destination detail view.
//
// Source:
//   docs/atlas-new-design/destinations/routines-prd.md §3.4 (detail view
//     stack: header + triggers + configuration summary + run history +
//     audit) + §3.6 (trigger kinds: schedule / webhook / event / manual)
//     + §3.12 (webhook fire flow).
//   docs/atlas-new-design/cross-audit.md §2.4 (webhook security —
//     rotating secret, 7-day grace, optional IP allowlist) + §9.7 Q6
//     (HMAC-of-payload signature is the next add; wire shape lands now)
//     + §1.2 (ClipboardPort is the substrate port for copy actions).
//
// Invariants:
//   - **Pure presentation.** Every side-effect (rotate, run-now,
//     pause/activate, copy URL, fetch run history) lands through a
//     callback prop. The host owns the transport call. This file does
//     NOT call `fetch`, `router.navigate`, or `transport.request`.
//   - **SP-1 primitives only.** Status chips render through
//     `<StatusPill>`. Cross-destination links (run history rows) render
//     through `<ItemLink>` — direct `router.navigate(…)` is forbidden
//     (cross-audit §1.1 + §3.3).
//   - **ClipboardPort for every "copy this string" action**
//     (cross-audit §1.2). The host injects the port; tests inject a
//     spy. No direct `navigator.clipboard.writeText(…)` here.
//   - **Mask secrets unless within reveal window.** The webhook URL +
//     secret cleartext are shown ONCE, in the response to a freshly-
//     issued rotate. The host hands us a `webhookReveal` prop carrying
//     the cleartext for THIS render only; the next mount sees only the
//     masked form. The component itself does not persist the cleartext
//     across remounts.

import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import type { ItemRef, RoutineId, RunId, UserId } from "@0x-copilot/api-types";

import type { ClipboardPort } from "../../ports/ClipboardPort";
import { ItemLink } from "../../refs/ItemLink";
import { itemKindNoun } from "../../refs/itemKindNoun";
import { StatusPill, type StatusTone } from "../../shell/StatusPill";

// ===========================================================================
// Display-layer types — mirror `destinations/routines-prd.md §4.1` but
// kept local until P5-A lands the api-types wire. (Same pattern as
// `InboxDetail.tsx` — list endpoint contracts ship later.)
// ===========================================================================

export type RoutineDetailStatus = "draft" | "active" | "paused" | "errored";

export type RoutineDetailTriggerKind =
  | "schedule"
  | "webhook"
  | "event"
  | "manual";

/**
 * Trigger ID — string; the wire `TriggerId` brand lands in P5-A.
 * Treated opaquely here.
 */
export type RoutineDetailTriggerId = string;

export interface RoutineDetailScheduleTrigger {
  readonly kind: "schedule";
  readonly triggerId: RoutineDetailTriggerId;
  /** Raw cron string for the small preview readout. */
  readonly cron: string;
  /** IANA tz, e.g. `Asia/Kolkata`. Surfaced in the human preview. */
  readonly tz: string;
  /**
   * Pre-computed human-readable preview, e.g. "Runs weekdays at 18:00
   * GMT+5:30". The host pre-formats this; we just render. (Avoids
   * pulling a cron-formatter dep into chat-surface.)
   */
  readonly humanPreview: string;
  /** Optional next-fire summary, e.g. "next: in 2h". */
  readonly nextFireSummary?: string;
}

export interface RoutineDetailWebhookTrigger {
  readonly kind: "webhook";
  readonly triggerId: RoutineDetailTriggerId;
  /**
   * Masked webhook URL — e.g. `https://api.example.com/v1/webhook/routines/****…trg_42`.
   * This is the default render. Host pre-masks; we never derive masking
   * from the cleartext.
   */
  readonly urlMasked: string;
  /**
   * Masked secret form — e.g. `****…abcd`. ALWAYS shown when no reveal
   * window is active.
   */
  readonly secretMasked: string;
  /** ISO; when null the trigger has never been rotated. */
  readonly secretRotatedAt: string | null;
  /**
   * ISO end-of-grace timestamp for the *previous* secret. When non-null
   * and in the future, we render a "grace until …" chip so the owner
   * remembers the old secret is still accepted (cross-audit §2.4).
   */
  readonly secretGraceUntil: string | null;
  /**
   * CIDRs. Empty array = no restriction. Rendered as muted chips.
   */
  readonly ipAllowlist: ReadonlyArray<string>;
}

export interface RoutineDetailEventTrigger {
  readonly kind: "event";
  readonly triggerId: RoutineDetailTriggerId;
  /** Allowlisted event source, e.g. `inbox.item_created`. */
  readonly eventSource: string;
  /** Optional pre-formatted summary, e.g. "priority = high". */
  readonly filterSummary?: string;
}

export interface RoutineDetailManualTrigger {
  readonly kind: "manual";
  readonly triggerId: RoutineDetailTriggerId;
}

export type RoutineDetailTrigger =
  | RoutineDetailScheduleTrigger
  | RoutineDetailWebhookTrigger
  | RoutineDetailEventTrigger
  | RoutineDetailManualTrigger;

export interface RoutineDetailOwner {
  readonly userId: UserId;
  readonly label: string;
}

export interface RoutineDetailItem {
  readonly id: RoutineId;
  readonly name: string;
  readonly status: RoutineDetailStatus;
  readonly owner: RoutineDetailOwner;
  /** ISO timestamp of the last fire. `null` when never fired. */
  readonly lastFireAt: string | null;
  readonly lastFireStatus: "succeeded" | "failed" | "skipped" | null;
  readonly triggers: ReadonlyArray<RoutineDetailTrigger>;
  /** Pre-formatted instructions preview. Empty when no instructions. */
  readonly instructionsPreview: string;
  /** Pre-formatted permissions summary lines. */
  readonly permissionsSummary: ReadonlyArray<string>;
  /** Pre-formatted audit log rows; host pre-renders the prose. */
  readonly auditEntries: ReadonlyArray<RoutineDetailAuditEntry>;
}

export interface RoutineDetailAuditEntry {
  readonly id: string;
  /** ISO timestamp. */
  readonly at: string;
  /** Pre-formatted prose, e.g. "Alex rotated webhook secret". */
  readonly message: string;
}

/**
 * Run-history list state. The host proxies the
 * `GET /v1/agent/runs?source.kind=routine&source.routine_id=<id>`
 * fetch (routines-prd.md §3.4 + §4.2) and hands us the result.
 */
export type RoutineDetailRunHistoryState =
  | { readonly kind: "idle" }
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | {
      readonly kind: "ready";
      readonly runs: ReadonlyArray<RoutineDetailRunRow>;
    };

export interface RoutineDetailRunRow {
  readonly id: RunId;
  readonly ref: ItemRef; // ref.kind === "run"; rendered via <ItemLink>
  readonly status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  readonly startedAt: string;
  /** Pre-formatted trigger label, e.g. "schedule" / "manual". */
  readonly triggerLabel: string;
}

/**
 * Reveal window payload. The host receives this in the response to
 * `POST .../rotate-secret` (routines-prd.md §3.4); it lives for ONE
 * render cycle. The host clears it (sets to `null`) after the user
 * dismisses the reveal banner or after their session reload.
 *
 * The component itself never persists cleartext.
 */
export interface RoutineDetailWebhookReveal {
  readonly triggerId: RoutineDetailTriggerId;
  /** Full cleartext URL — only safe to render inside the banner. */
  readonly url: string;
  /** Full cleartext secret — only safe to render inside the banner. */
  readonly secret: string;
}

export type RoutineDetailTabSlug =
  | "overview"
  | "run-history"
  | "triggers"
  | "permissions"
  | "audit";

const TAB_ORDER: ReadonlyArray<RoutineDetailTabSlug> = [
  "overview",
  "run-history",
  "triggers",
  "permissions",
  "audit",
];

const TAB_LABELS: Readonly<Record<RoutineDetailTabSlug, string>> = {
  overview: "Overview",
  "run-history": "Run history",
  triggers: "Triggers",
  permissions: "Permissions",
  audit: "Audit",
};

export interface RoutineDetailProps {
  readonly routine: RoutineDetailItem;

  /** Initial selected tab. Default `overview`. */
  readonly initialTab?: RoutineDetailTabSlug;

  /** Host-controlled tab. When provided, component is controlled. */
  readonly activeTab?: RoutineDetailTabSlug;
  readonly onTabChange?: (tab: RoutineDetailTabSlug) => void;

  /** Run-history state (host proxies the fetch — see §3.4 + §4.2). */
  readonly runHistory: RoutineDetailRunHistoryState;
  /** Optional retry handler; host wires the re-fetch. */
  readonly onRetryRunHistory?: () => void;

  /**
   * Clipboard substrate port (cross-audit §1.2). Used for every "copy
   * URL" / "copy secret" affordance. Required because the webhook URL
   * + reveal flow is the WHOLE POINT of the Triggers tab; without
   * `copyText` the user cannot get the value off-screen safely.
   */
  readonly clipboard: ClipboardPort;

  /**
   * Fresh webhook-reveal payload. Lives one render cycle: when the
   * host receives `{secret, url}` from `POST .../rotate-secret`, it
   * passes the value in here; on the next mount it passes `null`.
   * See `routines-prd.md` §3.4.
   */
  readonly webhookReveal?: RoutineDetailWebhookReveal | null;
  /**
   * Called when the user dismisses the reveal banner (the "I've copied
   * it" affordance). The host clears its in-memory reveal slot.
   */
  readonly onDismissWebhookReveal?: () => void;

  // --- Action callbacks (all optional; missing → button hidden) ---
  readonly onRunNow?: (id: RoutineId) => void;
  readonly onPause?: (id: RoutineId) => void;
  readonly onActivate?: (id: RoutineId) => void;
  readonly onRotateWebhookSecret?: (
    routineId: RoutineId,
    triggerId: RoutineDetailTriggerId,
  ) => void;

  /**
   * Pending action ids — drives per-button disabled/pending text.
   * Trigger-scoped pending uses `rotate:<triggerId>` so multiple
   * webhook triggers don't share state.
   */
  readonly pending?: ReadonlySet<string>;

  /** Optional copy-feedback message for the latest copy attempt. */
  readonly onCopyError?: (message: string) => void;
}

// ===========================================================================
// Tone helpers
// ===========================================================================

function routineStatusTone(status: RoutineDetailStatus): StatusTone {
  if (status === "active") return "ok";
  if (status === "paused") return "warning";
  if (status === "errored") return "error";
  return "muted"; // draft
}

function runStatusTone(status: RoutineDetailRunRow["status"]): StatusTone {
  if (status === "succeeded") return "ok";
  if (status === "failed") return "error";
  if (status === "running") return "info";
  if (status === "cancelled") return "muted";
  return "muted"; // queued
}

function lastFireTone(
  status: RoutineDetailItem["lastFireStatus"],
): StatusTone | null {
  if (status === null) return null;
  if (status === "succeeded") return "ok";
  if (status === "failed") return "error";
  return "warning"; // skipped
}

// ===========================================================================
// Component
// ===========================================================================

export function RoutineDetail({
  routine,
  initialTab,
  activeTab,
  onTabChange,
  runHistory,
  onRetryRunHistory,
  clipboard,
  webhookReveal,
  onDismissWebhookReveal,
  onRunNow,
  onPause,
  onActivate,
  onRotateWebhookSecret,
  pending,
  onCopyError,
}: RoutineDetailProps): ReactElement {
  // Uncontrolled tab state (controlled mode preempts via `activeTab`).
  const [uncontrolledTab, setUncontrolledTab] = useState<RoutineDetailTabSlug>(
    initialTab ?? "overview",
  );
  const tab: RoutineDetailTabSlug = activeTab ?? uncontrolledTab;

  const isPending = (key: string): boolean =>
    pending !== undefined && pending.has(key);

  const handleTabClick = (next: RoutineDetailTabSlug): void => {
    if (activeTab === undefined) setUncontrolledTab(next);
    onTabChange?.(next);
  };

  const handleCopy = async (text: string): Promise<void> => {
    try {
      await clipboard.copyText(text);
    } catch (err) {
      onCopyError?.(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <article
      aria-label={`Routine — ${routine.name}`}
      data-testid="routine-detail"
      data-routine-status={routine.status}
      data-routine-id={routine.id}
      style={rootStyle}
    >
      <RoutineDetailHeader
        routine={routine}
        onRunNow={onRunNow}
        onPause={onPause}
        onActivate={onActivate}
        isPending={isPending}
      />

      <RoutineDetailTabs activeTab={tab} onTabChange={handleTabClick} />

      <section
        role="tabpanel"
        id={`routine-tabpanel-${tab}`}
        aria-labelledby={`routine-tab-${tab}`}
        data-testid={`routine-tabpanel-${tab}`}
        style={tabPanelStyle}
      >
        {tab === "overview" ? <OverviewTab routine={routine} /> : null}
        {tab === "run-history" ? (
          <RunHistoryTab state={runHistory} onRetry={onRetryRunHistory} />
        ) : null}
        {tab === "triggers" ? (
          <TriggersTab
            routine={routine}
            webhookReveal={webhookReveal ?? null}
            onCopy={handleCopy}
            onRotate={onRotateWebhookSecret}
            onDismissReveal={onDismissWebhookReveal}
            isPending={isPending}
          />
        ) : null}
        {tab === "permissions" ? <PermissionsTab routine={routine} /> : null}
        {tab === "audit" ? <AuditTab routine={routine} /> : null}
      </section>
    </article>
  );
}

// ===========================================================================
// Header
// ===========================================================================

interface HeaderProps {
  readonly routine: RoutineDetailItem;
  readonly onRunNow?: (id: RoutineId) => void;
  readonly onPause?: (id: RoutineId) => void;
  readonly onActivate?: (id: RoutineId) => void;
  readonly isPending: (key: string) => boolean;
}

function RoutineDetailHeader({
  routine,
  onRunNow,
  onPause,
  onActivate,
  isPending,
}: HeaderProps): ReactElement {
  const fireTone = lastFireTone(routine.lastFireStatus);

  return (
    <header style={headerStyle} data-testid="routine-detail-header">
      <div style={titleRowStyle}>
        <h1 style={titleStyle} data-testid="routine-detail-name">
          {routine.name}
        </h1>
        <StatusPill
          status={routineStatusTone(routine.status)}
          label={routine.status}
        />
      </div>
      <div style={metaRowStyle}>
        <span style={metaItemStyle} data-testid="routine-detail-owner">
          Owner: {routine.owner.label}
        </span>
        <span style={metaSeparatorStyle} aria-hidden="true">
          ·
        </span>
        <span style={metaItemStyle} data-testid="routine-detail-last-fired">
          {routine.lastFireAt !== null ? (
            <>
              Last fired{" "}
              <time dateTime={routine.lastFireAt}>{routine.lastFireAt}</time>
              {fireTone !== null && routine.lastFireStatus !== null ? (
                <>
                  {" "}
                  <StatusPill
                    status={fireTone}
                    label={routine.lastFireStatus}
                  />
                </>
              ) : null}
            </>
          ) : (
            "Never fired"
          )}
        </span>
      </div>
      <div style={actionsRowStyle} role="group" aria-label="Routine actions">
        {onRunNow !== undefined ? (
          <button
            type="button"
            onClick={() => onRunNow(routine.id)}
            disabled={isPending("run-now")}
            style={primaryButtonStyle(isPending("run-now"))}
            data-testid="routine-detail-run-now"
          >
            {isPending("run-now") ? "Starting…" : "Run now"}
          </button>
        ) : null}
        {routine.status === "active" && onPause !== undefined ? (
          <button
            type="button"
            onClick={() => onPause(routine.id)}
            disabled={isPending("pause")}
            style={secondaryButtonStyle(isPending("pause"))}
            data-testid="routine-detail-pause"
          >
            {isPending("pause") ? "Pausing…" : "Pause"}
          </button>
        ) : null}
        {(routine.status === "paused" || routine.status === "draft") &&
        onActivate !== undefined ? (
          <button
            type="button"
            onClick={() => onActivate(routine.id)}
            disabled={isPending("activate")}
            style={secondaryButtonStyle(isPending("activate"))}
            data-testid="routine-detail-activate"
          >
            {isPending("activate") ? "Activating…" : "Activate"}
          </button>
        ) : null}
      </div>
    </header>
  );
}

// ===========================================================================
// Tabs
// ===========================================================================

interface TabsProps {
  readonly activeTab: RoutineDetailTabSlug;
  readonly onTabChange: (tab: RoutineDetailTabSlug) => void;
}

function RoutineDetailTabs({
  activeTab,
  onTabChange,
}: TabsProps): ReactElement {
  return (
    <div
      role="tablist"
      aria-label="Routine sections"
      style={tabListStyle}
      data-testid="routine-detail-tabs"
    >
      {TAB_ORDER.map((slug) => {
        const selected = slug === activeTab;
        return (
          <button
            key={slug}
            id={`routine-tab-${slug}`}
            role="tab"
            type="button"
            aria-selected={selected}
            aria-controls={`routine-tabpanel-${slug}`}
            tabIndex={selected ? 0 : -1}
            onClick={() => onTabChange(slug)}
            style={tabButtonStyle(selected)}
            data-testid={`routine-detail-tab-${slug}`}
          >
            {TAB_LABELS[slug]}
          </button>
        );
      })}
    </div>
  );
}

// ===========================================================================
// Overview tab
// ===========================================================================

function OverviewTab({
  routine,
}: {
  readonly routine: RoutineDetailItem;
}): ReactElement {
  return (
    <div style={panelInnerStyle} data-testid="routine-overview">
      <h2 style={sectionHeadingStyle}>Instructions</h2>
      {routine.instructionsPreview.length > 0 ? (
        <p
          style={instructionsStyle}
          data-testid="routine-overview-instructions"
        >
          {routine.instructionsPreview}
        </p>
      ) : (
        <p style={mutedStyle}>No instructions yet.</p>
      )}

      <h2 style={sectionHeadingStyle}>Triggers</h2>
      <ul style={summaryListStyle} data-testid="routine-overview-triggers">
        {routine.triggers.map((trigger) => (
          <li key={trigger.triggerId} style={summaryItemStyle}>
            <StatusPill status="muted" label={trigger.kind} />
            <span>{triggerSummaryLine(trigger)}</span>
          </li>
        ))}
        {routine.triggers.length === 0 ? (
          <li style={mutedStyle}>No triggers configured.</li>
        ) : null}
      </ul>
    </div>
  );
}

function triggerSummaryLine(trigger: RoutineDetailTrigger): string {
  if (trigger.kind === "schedule") {
    return trigger.humanPreview;
  }
  if (trigger.kind === "webhook") {
    return trigger.urlMasked;
  }
  if (trigger.kind === "event") {
    return trigger.filterSummary !== undefined
      ? `${trigger.eventSource} — ${trigger.filterSummary}`
      : trigger.eventSource;
  }
  return "Manual fire only";
}

// ===========================================================================
// Run history tab
// ===========================================================================

interface RunHistoryTabProps {
  readonly state: RoutineDetailRunHistoryState;
  readonly onRetry?: () => void;
}

function RunHistoryTab({ state, onRetry }: RunHistoryTabProps): ReactElement {
  return (
    <div style={panelInnerStyle} data-testid="routine-run-history">
      {state.kind === "idle" ? (
        <p style={mutedStyle}>Run history not yet loaded.</p>
      ) : null}
      {state.kind === "loading" ? (
        <p style={mutedStyle} role="status">
          Loading run history…
        </p>
      ) : null}
      {state.kind === "error" ? (
        <div role="alert" style={errorRowStyle}>
          <span>Could not load run history: {state.message}</span>
          {onRetry !== undefined ? (
            <button
              type="button"
              onClick={onRetry}
              style={smallButtonStyle}
              data-testid="routine-run-history-retry"
            >
              Retry
            </button>
          ) : null}
        </div>
      ) : null}
      {state.kind === "ready" ? (
        state.runs.length === 0 ? (
          <p style={mutedStyle}>No runs yet.</p>
        ) : (
          <ul style={runListStyle}>
            {state.runs.map((row) => (
              <li
                key={row.id}
                style={runRowStyle}
                data-testid="routine-run-history-row"
                data-run-id={row.id}
              >
                <ItemLink ref={row.ref} label={itemKindNoun(row.ref.kind)} />
                <StatusPill
                  status={runStatusTone(row.status)}
                  label={row.status}
                />
                <span style={runTriggerStyle}>{row.triggerLabel}</span>
                <time dateTime={row.startedAt} style={runTimeStyle}>
                  {row.startedAt}
                </time>
              </li>
            ))}
          </ul>
        )
      ) : null}
    </div>
  );
}

// ===========================================================================
// Triggers tab — owns the webhook URL UI + copy-once reveal
// ===========================================================================

interface TriggersTabProps {
  readonly routine: RoutineDetailItem;
  readonly webhookReveal: RoutineDetailWebhookReveal | null;
  readonly onCopy: (text: string) => Promise<void>;
  readonly onRotate?: (
    routineId: RoutineId,
    triggerId: RoutineDetailTriggerId,
  ) => void;
  readonly onDismissReveal?: () => void;
  readonly isPending: (key: string) => boolean;
}

function TriggersTab({
  routine,
  webhookReveal,
  onCopy,
  onRotate,
  onDismissReveal,
  isPending,
}: TriggersTabProps): ReactElement {
  return (
    <div style={panelInnerStyle} data-testid="routine-triggers">
      {routine.triggers.length === 0 ? (
        <p style={mutedStyle}>No triggers configured.</p>
      ) : null}
      {routine.triggers.map((trigger) => (
        <TriggerCard
          key={trigger.triggerId}
          routineId={routine.id}
          trigger={trigger}
          reveal={
            webhookReveal !== null &&
            webhookReveal.triggerId === trigger.triggerId
              ? webhookReveal
              : null
          }
          onCopy={onCopy}
          onRotate={onRotate}
          onDismissReveal={onDismissReveal}
          isPending={isPending}
        />
      ))}
    </div>
  );
}

interface TriggerCardProps {
  readonly routineId: RoutineId;
  readonly trigger: RoutineDetailTrigger;
  readonly reveal: RoutineDetailWebhookReveal | null;
  readonly onCopy: (text: string) => Promise<void>;
  readonly onRotate?: (
    routineId: RoutineId,
    triggerId: RoutineDetailTriggerId,
  ) => void;
  readonly onDismissReveal?: () => void;
  readonly isPending: (key: string) => boolean;
}

function TriggerCard({
  routineId,
  trigger,
  reveal,
  onCopy,
  onRotate,
  onDismissReveal,
  isPending,
}: TriggerCardProps): ReactElement {
  if (trigger.kind === "schedule") {
    return (
      <div
        style={triggerCardStyle}
        data-testid="routine-trigger-schedule"
        data-trigger-id={trigger.triggerId}
      >
        <div style={triggerHeaderStyle}>
          <StatusPill status="info" label="schedule" />
          <span style={triggerTitleStyle}>{trigger.humanPreview}</span>
        </div>
        <dl style={triggerDetailsStyle}>
          <dt style={dtStyle}>Cron</dt>
          <dd style={ddCodeStyle}>{trigger.cron}</dd>
          <dt style={dtStyle}>Timezone</dt>
          <dd>{trigger.tz}</dd>
          {trigger.nextFireSummary !== undefined ? (
            <>
              <dt style={dtStyle}>Next</dt>
              <dd>{trigger.nextFireSummary}</dd>
            </>
          ) : null}
        </dl>
      </div>
    );
  }

  if (trigger.kind === "event") {
    return (
      <div
        style={triggerCardStyle}
        data-testid="routine-trigger-event"
        data-trigger-id={trigger.triggerId}
      >
        <div style={triggerHeaderStyle}>
          <StatusPill status="info" label="event" />
          <span style={triggerTitleStyle}>{trigger.eventSource}</span>
        </div>
        {trigger.filterSummary !== undefined ? (
          <p style={mutedStyle}>Filter: {trigger.filterSummary}</p>
        ) : null}
      </div>
    );
  }

  if (trigger.kind === "manual") {
    return (
      <div
        style={triggerCardStyle}
        data-testid="routine-trigger-manual"
        data-trigger-id={trigger.triggerId}
      >
        <div style={triggerHeaderStyle}>
          <StatusPill status="muted" label="manual" />
          <span style={triggerTitleStyle}>Manual fire only</span>
        </div>
        <p style={mutedStyle}>
          Use the "Run now" button in the header to fire this routine.
        </p>
      </div>
    );
  }

  // kind === "webhook"
  return (
    <WebhookTriggerCard
      routineId={routineId}
      trigger={trigger}
      reveal={reveal}
      onCopy={onCopy}
      onRotate={onRotate}
      onDismissReveal={onDismissReveal}
      isPending={isPending}
    />
  );
}

// ===========================================================================
// Webhook trigger card — copy-once reveal + rotate
// ===========================================================================

interface WebhookTriggerCardProps {
  readonly routineId: RoutineId;
  readonly trigger: RoutineDetailWebhookTrigger;
  readonly reveal: RoutineDetailWebhookReveal | null;
  readonly onCopy: (text: string) => Promise<void>;
  readonly onRotate?: (
    routineId: RoutineId,
    triggerId: RoutineDetailTriggerId,
  ) => void;
  readonly onDismissReveal?: () => void;
  readonly isPending: (key: string) => boolean;
}

function WebhookTriggerCard({
  routineId,
  trigger,
  reveal,
  onCopy,
  onRotate,
  onDismissReveal,
  isPending,
}: WebhookTriggerCardProps): ReactElement {
  // Track whether each copy action has been performed at least once in
  // this reveal window. Used to show the "Copied" visual hint; the
  // cleartext is *always* hidden once `reveal` goes back to null.
  const [copiedUrl, setCopiedUrl] = useState(false);
  const [copiedSecret, setCopiedSecret] = useState(false);

  // Reset copied state when the reveal payload changes (new rotation).
  const previousRevealRef = useRef<RoutineDetailWebhookReveal | null>(reveal);
  useEffect(() => {
    if (previousRevealRef.current !== reveal) {
      setCopiedUrl(false);
      setCopiedSecret(false);
      previousRevealRef.current = reveal;
    }
  }, [reveal]);

  const rotating = isPending(`rotate:${trigger.triggerId}`);
  const inGrace =
    trigger.secretGraceUntil !== null &&
    new Date(trigger.secretGraceUntil).getTime() > Date.now();

  const handleCopyUrl = async (): Promise<void> => {
    if (reveal === null) return;
    await onCopy(reveal.url);
    setCopiedUrl(true);
  };
  const handleCopySecret = async (): Promise<void> => {
    if (reveal === null) return;
    await onCopy(reveal.secret);
    setCopiedSecret(true);
  };
  const handleCopyMaskedUrl = async (): Promise<void> => {
    // Copying the masked URL is safe — but it is not a working URL.
    // Provided so the owner can grab the URL skeleton for docs.
    await onCopy(trigger.urlMasked);
  };

  return (
    <div
      style={triggerCardStyle}
      data-testid="routine-trigger-webhook"
      data-trigger-id={trigger.triggerId}
      data-reveal-active={reveal !== null ? "true" : "false"}
    >
      <div style={triggerHeaderStyle}>
        <StatusPill status="info" label="webhook" />
        <span style={triggerTitleStyle}>Webhook trigger</span>
        {inGrace ? (
          <StatusPill
            status="warning"
            label={`grace until ${trigger.secretGraceUntil ?? ""}`}
          />
        ) : null}
      </div>

      {/* --- URL row (masked OR revealed) --- */}
      <dl style={triggerDetailsStyle}>
        <dt style={dtStyle}>URL</dt>
        <dd style={ddCodeStyle}>
          {reveal !== null ? (
            <span data-testid="routine-webhook-url-clear">{reveal.url}</span>
          ) : (
            <span data-testid="routine-webhook-url-masked">
              {trigger.urlMasked}
            </span>
          )}
          <button
            type="button"
            onClick={reveal !== null ? handleCopyUrl : handleCopyMaskedUrl}
            style={inlineCopyButtonStyle}
            data-testid="routine-webhook-copy-url"
            aria-label="Copy webhook URL"
          >
            {reveal !== null && copiedUrl ? "Copied" : "Copy"}
          </button>
        </dd>

        {/* --- Secret row --- */}
        <dt style={dtStyle}>Secret</dt>
        <dd style={ddCodeStyle}>
          {reveal !== null ? (
            <span data-testid="routine-webhook-secret-clear">
              {reveal.secret}
            </span>
          ) : (
            <span data-testid="routine-webhook-secret-masked">
              {trigger.secretMasked}
            </span>
          )}
          {reveal !== null ? (
            <button
              type="button"
              onClick={handleCopySecret}
              style={inlineCopyButtonStyle}
              data-testid="routine-webhook-copy-secret"
              aria-label="Copy webhook secret"
            >
              {copiedSecret ? "Copied" : "Copy"}
            </button>
          ) : null}
        </dd>

        {trigger.secretRotatedAt !== null ? (
          <>
            <dt style={dtStyle}>Last rotated</dt>
            <dd>
              <time dateTime={trigger.secretRotatedAt}>
                {trigger.secretRotatedAt}
              </time>
            </dd>
          </>
        ) : null}

        {trigger.ipAllowlist.length > 0 ? (
          <>
            <dt style={dtStyle}>IP allowlist</dt>
            <dd style={chipRowStyle}>
              {trigger.ipAllowlist.map((cidr) => (
                <StatusPill key={cidr} status="muted" label={cidr} />
              ))}
            </dd>
          </>
        ) : null}
      </dl>

      {/* --- Reveal banner (one-shot copy window) --- */}
      {reveal !== null ? (
        <div
          role="alert"
          style={revealBannerStyle}
          data-testid="routine-webhook-reveal-banner"
        >
          <strong>Copy the secret now.</strong> It will not be shown again.{" "}
          {onDismissReveal !== undefined ? (
            <button
              type="button"
              onClick={onDismissReveal}
              style={smallButtonStyle}
              data-testid="routine-webhook-reveal-dismiss"
            >
              I've copied it
            </button>
          ) : null}
        </div>
      ) : null}

      {/* --- Rotate button --- */}
      {onRotate !== undefined ? (
        <div style={triggerActionsRowStyle}>
          <button
            type="button"
            onClick={() => onRotate(routineId, trigger.triggerId)}
            disabled={rotating}
            style={secondaryButtonStyle(rotating)}
            data-testid="routine-webhook-rotate"
          >
            {rotating ? "Rotating…" : "Rotate secret"}
          </button>
        </div>
      ) : null}
    </div>
  );
}

// ===========================================================================
// Permissions tab
// ===========================================================================

function PermissionsTab({
  routine,
}: {
  readonly routine: RoutineDetailItem;
}): ReactElement {
  return (
    <div style={panelInnerStyle} data-testid="routine-permissions">
      {routine.permissionsSummary.length === 0 ? (
        <p style={mutedStyle}>No permissions configured.</p>
      ) : (
        <ul style={summaryListStyle}>
          {routine.permissionsSummary.map((line, i) => (
            <li
              key={`${i}-${line}`}
              style={summaryItemStyle}
              data-testid="routine-permissions-row"
            >
              {line}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ===========================================================================
// Audit tab
// ===========================================================================

function AuditTab({
  routine,
}: {
  readonly routine: RoutineDetailItem;
}): ReactElement {
  return (
    <div style={panelInnerStyle} data-testid="routine-audit">
      {routine.auditEntries.length === 0 ? (
        <p style={mutedStyle}>No audit entries yet.</p>
      ) : (
        <ul style={summaryListStyle}>
          {routine.auditEntries.map((entry) => (
            <li
              key={entry.id}
              style={auditRowStyle}
              data-testid="routine-audit-row"
            >
              <time dateTime={entry.at} style={auditTimeStyle}>
                {entry.at}
              </time>
              <span>{entry.message}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ===========================================================================
// Styles
// ===========================================================================

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 16,
  width: "100%",
  maxWidth: 960,
  margin: "0 auto",
  padding: "16px 20px 32px",
  boxSizing: "border-box",
  color: "var(--color-text)",
};

const headerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: 16,
  borderRadius: 10,
  border: "1px solid var(--color-border)",
  background: "var(--color-surface)",
};

const titleRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  flexWrap: "wrap",
};

const titleStyle: CSSProperties = {
  fontSize: "var(--font-size-xl)",
  fontWeight: 700,
  margin: 0,
  lineHeight: 1.3,
};

const metaRowStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  gap: 6,
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text-muted)",
};

const metaItemStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
};

const metaSeparatorStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
};

const actionsRowStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 6,
  paddingTop: 6,
};

const primaryButtonStyle = (busy: boolean): CSSProperties => ({
  height: 32,
  padding: "0 14px",
  borderRadius: 6,
  border: "1px solid var(--color-accent)",
  background: "var(--color-accent)",
  color: "var(--color-on-accent, #fff)",
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  cursor: busy ? "default" : "pointer",
  opacity: busy ? 0.6 : 1,
});

const secondaryButtonStyle = (busy: boolean): CSSProperties => ({
  height: 30,
  padding: "0 12px",
  borderRadius: 6,
  border: "1px solid var(--color-border-strong)",
  background: "transparent",
  color: "var(--color-accent)",
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  cursor: busy ? "default" : "pointer",
  opacity: busy ? 0.6 : 1,
});

const smallButtonStyle: CSSProperties = {
  height: 26,
  padding: "0 10px",
  borderRadius: 6,
  border: "1px solid var(--color-border-strong)",
  background: "transparent",
  color: "var(--color-accent)",
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  cursor: "pointer",
};

const inlineCopyButtonStyle: CSSProperties = {
  marginLeft: 8,
  height: 22,
  padding: "0 8px",
  borderRadius: 4,
  border: "1px solid var(--color-border-strong)",
  background: "transparent",
  color: "var(--color-accent)",
  fontSize: "var(--font-size-2xs)",
  fontWeight: 600,
  cursor: "pointer",
};

const tabListStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 4,
  borderBottom: "1px solid var(--color-border)",
};

const tabButtonStyle = (selected: boolean): CSSProperties => ({
  height: 32,
  padding: "0 12px",
  border: "1px solid transparent",
  borderBottomColor: selected ? "var(--color-accent)" : "transparent",
  background: "transparent",
  color: selected ? "var(--color-accent)" : "var(--color-text-muted)",
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  cursor: "pointer",
});

const tabPanelStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 10,
};

const panelInnerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  padding: 16,
  borderRadius: 10,
  border: "1px solid var(--color-border)",
  background: "var(--color-surface)",
};

const sectionHeadingStyle: CSSProperties = {
  fontSize: "var(--font-size-sm)",
  fontWeight: 700,
  margin: 0,
  color: "var(--color-text)",
};

const instructionsStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm)",
  lineHeight: 1.5,
  whiteSpace: "pre-wrap",
};

const mutedStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm)",
  color: "var(--color-text-muted)",
};

const summaryListStyle: CSSProperties = {
  margin: 0,
  paddingLeft: 0,
  listStyle: "none",
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const summaryItemStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  fontSize: "var(--font-size-sm)",
};

const runListStyle: CSSProperties = {
  margin: 0,
  paddingLeft: 0,
  listStyle: "none",
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const runRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  fontSize: "var(--font-size-sm)",
  padding: "6px 8px",
  borderRadius: 6,
  border: "1px solid var(--color-border)",
};

const runTriggerStyle: CSSProperties = {
  color: "var(--color-text-muted)",
  fontSize: "var(--font-size-xs)",
};

const runTimeStyle: CSSProperties = {
  marginLeft: "auto",
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text-subtle)",
};

const triggerCardStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 10,
  padding: 12,
  borderRadius: 8,
  border: "1px solid var(--color-border)",
  background: "var(--color-bg-subtle, transparent)",
};

const triggerHeaderStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  flexWrap: "wrap",
};

const triggerTitleStyle: CSSProperties = {
  fontSize: "var(--font-size-sm)",
  fontWeight: 600,
};

const triggerDetailsStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "max-content 1fr",
  rowGap: 6,
  columnGap: 12,
  margin: 0,
  fontSize: "var(--font-size-sm)",
  alignItems: "center",
};

const dtStyle: CSSProperties = {
  fontWeight: 600,
  color: "var(--color-text-muted)",
};

const ddCodeStyle: CSSProperties = {
  margin: 0,
  fontFamily:
    "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
  fontSize: "var(--font-size-xs)",
  wordBreak: "break-all",
  display: "flex",
  alignItems: "center",
};

const chipRowStyle: CSSProperties = {
  margin: 0,
  display: "flex",
  flexWrap: "wrap",
  gap: 4,
};

const triggerActionsRowStyle: CSSProperties = {
  display: "flex",
  gap: 6,
};

const revealBannerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  padding: "8px 10px",
  borderRadius: 6,
  border: "1px solid var(--color-warning, #d9a857)",
  background: "var(--color-warning-bg, #322615)",
  color: "var(--color-warning, #d9a857)",
  fontSize: "var(--font-size-xs)",
};

const errorRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  color: "var(--color-danger)",
  fontSize: "var(--font-size-sm)",
};

const auditRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "baseline",
  gap: 8,
  fontSize: "var(--font-size-sm)",
};

const auditTimeStyle: CSSProperties = {
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text-subtle)",
  fontVariantNumeric: "tabular-nums",
  minWidth: 160,
};
