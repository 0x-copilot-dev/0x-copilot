// <RoutineEditor /> — tabbed editor for the Routines destination.
//
// Source:
//   - docs/atlas-new-design/destinations/routines-prd.md §3.5 (editor)
//     §3.6 (trigger kinds + cron editor)
//     §3.8 (Connectors tab)
//     §3.9 (Behavior tab + missed_fire_policy + output target)
//     §3.10 (Permissions tab)
//     §3.11 (manual_fire override)
//     §4.1 (wire shapes)
//   - docs/atlas-new-design/cross-audit.md §9.7 (binding decisions:
//     missed_fire_policy default `fire_once`, manual_fire override,
//     agent_version_pin opt-in)
//
// Invariants:
//   - Pure presentation. The save callback receives the assembled
//     Routine-edit payload; the host owns persistence (POST /v1/routines
//     or PATCH /v1/routines/{id}).
//   - SP-1 primitives only (StatusPill). Composer is reused for the
//     Instructions textarea — there is one and only one composer.
//   - The cron editor's structure mirrors the Todo recurrence-editor:
//     discrete frequency picker + interval + day-of-week multi-select
//     when Weekly + advanced raw-cron toggle with live human-readable
//     preview. Wire output is the §4.1 `{cron, tz}` shape.
//   - ARIA tabs pattern (master §3.6 / routines-prd §10): role="tablist"
//     / role="tab" / role="tabpanel", arrow keys cycle, Home/End jump.
//   - 1-minute minimum granularity enforced client-side (server
//     re-validates per routines-prd §3.6.1 hard constraint).
//
// Local types (`RoutineEditorValue` etc) mirror routines-prd §4.1 shapes.
// They live here until `packages/api-types/src/routines.ts` lands (P5-A);
// at that point this file replaces the local declarations with imports
// and the public shape is unchanged.

import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";

import type {
  ConnectorId,
  ItemRef,
  ProjectId,
  ReasoningDepth,
  SkillId,
  ToolId,
} from "@0x-copilot/api-types";

import { Composer } from "../../composer/Composer";
import { StatusPill } from "../../shell/StatusPill";

// ===========================================================================
// Local types — mirror routines-prd §4.1 (see file header).
// ===========================================================================

export type RoutineMissedFirePolicy = "fire_once" | "fire_all" | "skip_all";
export type RoutineAutonomy = "manual_approval" | "auto_apply" | "full_auto";
export type RoutineScope = "read_only" | "read_write";
export type RoutineDataResidency =
  | "inherit"
  | "us_only"
  | "eu_only"
  | "apac_only";
export type RoutineManualFire = "owner" | "project_members" | "tenant";

export type RoutineOutputTarget =
  | { readonly kind: "inbox" }
  | {
      readonly kind: "library_page";
      readonly ref: ItemRef;
      readonly mode: "new_per_fire" | "update_same";
    }
  | { readonly kind: "existing_chat"; readonly ref: ItemRef }
  | { readonly kind: "project_log"; readonly ref: ItemRef };

/**
 * Trigger as carried by the editor. The wire shape (`§4.1 RoutineTrigger`)
 * carries a server-assigned `trigger_id` for `schedule`/`webhook`/`event`
 * — for the editor's draft view we keep an optional `trigger_id` so
 * existing triggers round-trip but new ones can be added without one.
 */
export type RoutineEditorTrigger =
  | {
      readonly kind: "schedule";
      readonly trigger_id?: string;
      readonly cron: string;
      readonly tz: string;
    }
  | {
      readonly kind: "webhook";
      readonly trigger_id?: string;
      readonly secret_masked?: string;
      readonly ip_allowlist: ReadonlyArray<string>;
    }
  | {
      readonly kind: "event";
      readonly trigger_id?: string;
      readonly event_source: string;
      readonly filter: ReadonlyArray<{
        readonly field: string;
        readonly op:
          | "eq"
          | "ne"
          | "gt"
          | "gte"
          | "lt"
          | "lte"
          | "in"
          | "matches";
        readonly value: string;
      }>;
    }
  | { readonly kind: "manual"; readonly trigger_id?: string };

export interface RoutineEditorConnectorConfig {
  readonly connector_id: ConnectorId;
  readonly mode: "inherit" | "read_only" | "custom";
  readonly custom_scope?: ReadonlyArray<string>;
}

export interface RoutineEditorBehavior {
  readonly autonomy: RoutineAutonomy;
  readonly max_retries: number;
  readonly backoff: "exponential" | "linear" | "none";
  readonly backoff_base_seconds: number;
  readonly max_duration_seconds: number;
  readonly output_target: RoutineOutputTarget;
  readonly notify_on_success: ReadonlyArray<
    "owner" | "project_members" | "tenant_admin"
  >;
  readonly notify_on_failure: ReadonlyArray<
    "owner" | "project_members" | "tenant_admin"
  >;
}

export interface RoutineEditorPermissions {
  readonly scope: RoutineScope;
  readonly allowed_tools: ReadonlyArray<ToolId>;
  readonly allowed_skills: ReadonlyArray<SkillId>;
  readonly max_tool_calls_per_fire: number;
  readonly max_output_tokens_per_fire: number;
  readonly data_residency: RoutineDataResidency;
  readonly manual_fire: RoutineManualFire;
}

/**
 * The full editor draft — the payload `onSave` receives. Matches
 * routines-prd §4.1 `Routine` minus server-assigned fields (`id`,
 * `tenant_id`, `owner_user_id`, `next_fire_at`, `last_fire_at`, `status`,
 * `created_at`, `updated_at`).
 *
 * `agent_version_pin` is the cross-audit §9.7 Q11 opt-in: null means
 * "live re-resolve at fire time" (default); a string means "pin to this
 * agent version". Forwards-compatible per the binding decision.
 */
export interface RoutineEditorValue {
  readonly name: string;
  readonly description: string;
  readonly instructions: string;
  readonly model: string;
  readonly depth: ReasoningDepth | null;
  readonly base_agent_id: string | null;
  readonly agent_version_pin: string | null;
  readonly project_id: ProjectId | null;
  readonly triggers: ReadonlyArray<RoutineEditorTrigger>;
  readonly connectors: ReadonlyArray<RoutineEditorConnectorConfig>;
  readonly behavior: RoutineEditorBehavior;
  readonly permissions: RoutineEditorPermissions;
  readonly missed_fire_policy: RoutineMissedFirePolicy;
}

// ===========================================================================
// Defaults — line up with routines-prd §3.9 / §3.10 / cross-audit §9.7.
// ===========================================================================

const DEFAULT_BEHAVIOR: RoutineEditorBehavior = {
  autonomy: "manual_approval",
  max_retries: 0,
  backoff: "exponential",
  backoff_base_seconds: 30,
  max_duration_seconds: 600,
  output_target: { kind: "inbox" },
  notify_on_success: [],
  notify_on_failure: ["owner"],
};

const DEFAULT_PERMISSIONS: RoutineEditorPermissions = {
  scope: "read_only",
  allowed_tools: [],
  allowed_skills: [],
  max_tool_calls_per_fire: 200,
  max_output_tokens_per_fire: 32000,
  data_residency: "inherit",
  manual_fire: "owner", // routines-prd §3.11
};

export const ROUTINE_EDITOR_DEFAULTS: RoutineEditorValue = {
  name: "",
  description: "",
  instructions: "",
  model: "claude-opus-4-7",
  depth: "balanced",
  base_agent_id: null,
  agent_version_pin: null, // cross-audit §9.7 Q11 — opt-in
  project_id: null,
  triggers: [],
  connectors: [],
  behavior: DEFAULT_BEHAVIOR,
  permissions: DEFAULT_PERMISSIONS,
  missed_fire_policy: "fire_once", // cross-audit §9.7 Q7
};

// ===========================================================================
// Cron helpers — mirrors the recurrence-editor's pure-helper discipline.
// Wire output is a standard 5-field cron string. 1-minute minimum
// granularity (routines-prd §3.6.1).
// ===========================================================================

export type CronFrequency =
  | "hourly"
  | "daily"
  | "weekdays"
  | "weekly"
  | "monthly"
  | "custom";

export const WEEKDAY_CODES = [0, 1, 2, 3, 4, 5, 6] as const;
export type WeekdayCode = (typeof WEEKDAY_CODES)[number];

const WEEKDAY_SHORT: Readonly<Record<WeekdayCode, string>> = {
  0: "S",
  1: "M",
  2: "T",
  3: "W",
  4: "T",
  5: "F",
  6: "S",
};

const WEEKDAY_LONG: Readonly<Record<WeekdayCode, string>> = {
  0: "Sun",
  1: "Mon",
  2: "Tue",
  3: "Wed",
  4: "Thu",
  5: "Fri",
  6: "Sat",
};

/**
 * Build a 5-field cron string from a high-level form draft. Pure — no
 * clock access.
 *
 * Hours/minutes are normalized to [0,23] / [0,59]; out-of-range inputs
 * are clamped (the form already constrains, but defensive clamping
 * keeps the spec valid).
 */
export function buildCronSpec(args: {
  readonly frequency: CronFrequency;
  readonly hour: number;
  readonly minute: number;
  readonly weekdays: ReadonlyArray<WeekdayCode>;
  readonly dayOfMonth: number;
  readonly raw: string;
}): string {
  if (args.frequency === "custom") {
    return args.raw.trim();
  }
  const h = clamp(args.hour, 0, 23);
  const m = clamp(args.minute, 0, 59);
  const dom = clamp(args.dayOfMonth, 1, 31);
  if (args.frequency === "hourly") {
    // every hour, at minute m
    return `${m} * * * *`;
  }
  if (args.frequency === "daily") {
    return `${m} ${h} * * *`;
  }
  if (args.frequency === "weekdays") {
    return `${m} ${h} * * 1-5`;
  }
  if (args.frequency === "weekly") {
    if (args.weekdays.length === 0) {
      return `${m} ${h} * * 1`; // default Mon if user picks weekly with no days
    }
    const sorted = WEEKDAY_CODES.filter((d) => args.weekdays.includes(d));
    return `${m} ${h} * * ${sorted.join(",")}`;
  }
  // monthly
  return `${m} ${h} ${dom} * *`;
}

function clamp(n: number, lo: number, hi: number): number {
  if (!Number.isFinite(n)) return lo;
  return Math.max(lo, Math.min(hi, Math.floor(n)));
}

/**
 * Parse a 5-field cron string back into a form draft. Returns
 * `{frequency: "custom"}` when the string doesn't match one of the
 * known high-level shapes — the editor then drops into Advanced mode
 * with the raw string preserved.
 */
export function parseCronSpec(spec: string): {
  readonly frequency: CronFrequency;
  readonly hour: number;
  readonly minute: number;
  readonly weekdays: ReadonlyArray<WeekdayCode>;
  readonly dayOfMonth: number;
  readonly raw: string;
} {
  const trimmed = spec.trim();
  const fields = trimmed.split(/\s+/);
  if (fields.length !== 5) {
    return {
      frequency: "custom",
      hour: 9,
      minute: 0,
      weekdays: [],
      dayOfMonth: 1,
      raw: trimmed,
    };
  }
  const [minF, hourF, domF, monF, dowF] = fields as [
    string,
    string,
    string,
    string,
    string,
  ];
  const isMonAny = monF === "*";
  // hourly — `<m> * * * *`
  if (
    isInt(minF) &&
    hourF === "*" &&
    domF === "*" &&
    isMonAny &&
    dowF === "*"
  ) {
    return {
      frequency: "hourly",
      hour: 0,
      minute: Number.parseInt(minF, 10),
      weekdays: [],
      dayOfMonth: 1,
      raw: trimmed,
    };
  }
  // daily — `<m> <h> * * *`
  if (isInt(minF) && isInt(hourF) && domF === "*" && isMonAny && dowF === "*") {
    return {
      frequency: "daily",
      hour: Number.parseInt(hourF, 10),
      minute: Number.parseInt(minF, 10),
      weekdays: [],
      dayOfMonth: 1,
      raw: trimmed,
    };
  }
  // weekdays — `<m> <h> * * 1-5`
  if (
    isInt(minF) &&
    isInt(hourF) &&
    domF === "*" &&
    isMonAny &&
    dowF === "1-5"
  ) {
    return {
      frequency: "weekdays",
      hour: Number.parseInt(hourF, 10),
      minute: Number.parseInt(minF, 10),
      weekdays: [],
      dayOfMonth: 1,
      raw: trimmed,
    };
  }
  // weekly — `<m> <h> * * <d[,d,...]>`
  if (
    isInt(minF) &&
    isInt(hourF) &&
    domF === "*" &&
    isMonAny &&
    /^[0-6](,[0-6])*$/.test(dowF)
  ) {
    const days = dowF
      .split(",")
      .map((d) => Number.parseInt(d, 10) as WeekdayCode);
    return {
      frequency: "weekly",
      hour: Number.parseInt(hourF, 10),
      minute: Number.parseInt(minF, 10),
      weekdays: days,
      dayOfMonth: 1,
      raw: trimmed,
    };
  }
  // monthly — `<m> <h> <dom> * *`
  if (isInt(minF) && isInt(hourF) && isInt(domF) && isMonAny && dowF === "*") {
    return {
      frequency: "monthly",
      hour: Number.parseInt(hourF, 10),
      minute: Number.parseInt(minF, 10),
      weekdays: [],
      dayOfMonth: Number.parseInt(domF, 10),
      raw: trimmed,
    };
  }
  return {
    frequency: "custom",
    hour: 9,
    minute: 0,
    weekdays: [],
    dayOfMonth: 1,
    raw: trimmed,
  };
}

function isInt(s: string): boolean {
  return /^\d+$/.test(s);
}

/**
 * Human-readable preview of a cron spec. Routines-prd §3.6.1 calls for a
 * live aria-live region; this is the pure string those regions render.
 */
export function previewCron(spec: string, tz: string): string {
  const parsed = parseCronSpec(spec);
  const time = formatTime(parsed.hour, parsed.minute);
  if (parsed.frequency === "hourly") {
    return `Runs every hour at :${pad2(parsed.minute)} (${tz})`;
  }
  if (parsed.frequency === "daily") {
    return `Runs every day at ${time} (${tz})`;
  }
  if (parsed.frequency === "weekdays") {
    return `Runs weekdays at ${time} (${tz})`;
  }
  if (parsed.frequency === "weekly") {
    const days = parsed.weekdays.map((d) => WEEKDAY_LONG[d]).join(", ");
    return `Runs weekly on ${days || "Mon"} at ${time} (${tz})`;
  }
  if (parsed.frequency === "monthly") {
    return `Runs monthly on day ${parsed.dayOfMonth} at ${time} (${tz})`;
  }
  return `Custom: \`${spec.trim() || "(empty)"}\` (${tz})`;
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

function formatTime(h: number, m: number): string {
  return `${pad2(h)}:${pad2(m)}`;
}

/**
 * Validate that a cron spec passes the routines-prd §3.6.1 1-minute
 * minimum granularity rule (client-side gate; server re-validates).
 * Rejects:
 *   - `@reboot` / `@yearly` / any `@`-prefixed macro (we don't support)
 *   - per-second strings (6 fields)
 *   - sub-minute step expressions on the minute field (e.g. invalid /0 step)
 *   - empty / non-5-field strings
 */
export function isValidCronSpec(spec: string): boolean {
  const trimmed = spec.trim();
  if (trimmed.length === 0) return false;
  if (trimmed.startsWith("@")) return false;
  const fields = trimmed.split(/\s+/);
  if (fields.length !== 5) return false;
  return true;
}

// ===========================================================================
// Tab IDs + ARIA wiring.
// ===========================================================================

export type RoutineEditorTabId =
  | "name"
  | "instructions"
  | "triggers"
  | "connectors"
  | "behavior"
  | "permissions";

const TAB_ORDER: ReadonlyArray<RoutineEditorTabId> = [
  "name",
  "instructions",
  "triggers",
  "connectors",
  "behavior",
  "permissions",
];

const TAB_LABEL: Readonly<Record<RoutineEditorTabId, string>> = {
  name: "Name",
  instructions: "Instructions",
  triggers: "Triggers",
  connectors: "Connectors",
  behavior: "Behavior",
  permissions: "Permissions",
};

// ===========================================================================
// Public props.
// ===========================================================================

export interface RoutineEditorProps {
  /**
   * Initial value. When undefined, the editor renders the
   * `ROUTINE_EDITOR_DEFAULTS` shape (new-routine flow).
   */
  readonly initialValue?: RoutineEditorValue;
  /**
   * Save handler. Receives the full assembled draft. Host owns the
   * POST/PATCH transport call.
   */
  readonly onSave: (value: RoutineEditorValue) => void;
  /**
   * Cancel handler. The editor itself does not prompt on dirty — that
   * is a host concern (a route-leave confirm guard).
   */
  readonly onCancel?: () => void;
  /**
   * Which tab to render first. Defaults to "name". Useful for direct
   * deep-links (`/routines/<id>/edit#permissions`) where the host
   * routes to the right tab.
   */
  readonly initialTab?: RoutineEditorTabId;
  /**
   * Connectors the owner has connected; the Connectors tab lists these
   * and lets the routine opt into a subset (routines-prd §3.8). Pure
   * data: the host fetches `/v1/me/connectors` and passes the list in.
   */
  readonly availableConnectors?: ReadonlyArray<{
    readonly connector_id: ConnectorId;
    readonly label: string;
  }>;
  /**
   * Project options for the project_id picker (Permissions tab). Pure
   * data; host fetches `/v1/projects` and passes the list in.
   */
  readonly availableProjects?: ReadonlyArray<{
    readonly project_id: ProjectId;
    readonly label: string;
  }>;
  /**
   * Disabled state — used while a save is in flight. Tabs remain
   * navigable so users can re-read fields but inputs are locked.
   */
  readonly disabled?: boolean;
}

// ===========================================================================
// Component.
// ===========================================================================

export function RoutineEditor(props: RoutineEditorProps): ReactElement {
  const {
    initialValue,
    onSave,
    onCancel,
    initialTab = "name",
    availableConnectors = [],
    availableProjects = [],
    disabled = false,
  } = props;

  const [value, setValue] = useState<RoutineEditorValue>(
    () => initialValue ?? ROUTINE_EDITOR_DEFAULTS,
  );
  const [activeTab, setActiveTab] = useState<RoutineEditorTabId>(initialTab);
  const tabRefs = useRef<Record<RoutineEditorTabId, HTMLButtonElement | null>>({
    name: null,
    instructions: null,
    triggers: null,
    connectors: null,
    behavior: null,
    permissions: null,
  });

  const isDraft = useMemo(
    () => value.name.trim().length === 0 || value.triggers.length === 0,
    [value],
  );

  // -- Field setters ---------------------------------------------------------

  const updateField = useCallback(
    <K extends keyof RoutineEditorValue>(
      key: K,
      next: RoutineEditorValue[K],
    ) => {
      setValue((prev) => ({ ...prev, [key]: next }));
    },
    [],
  );

  const updateBehavior = useCallback(
    <K extends keyof RoutineEditorBehavior>(
      key: K,
      next: RoutineEditorBehavior[K],
    ) => {
      setValue((prev) => ({
        ...prev,
        behavior: { ...prev.behavior, [key]: next },
      }));
    },
    [],
  );

  const updatePermissions = useCallback(
    <K extends keyof RoutineEditorPermissions>(
      key: K,
      next: RoutineEditorPermissions[K],
    ) => {
      setValue((prev) => ({
        ...prev,
        permissions: { ...prev.permissions, [key]: next },
      }));
    },
    [],
  );

  // -- Tab navigation (arrow keys / Home / End) ------------------------------

  const focusTab = useCallback((tab: RoutineEditorTabId) => {
    setActiveTab(tab);
    const node = tabRefs.current[tab];
    node?.focus();
  }, []);

  const onTabKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      const idx = TAB_ORDER.indexOf(activeTab);
      if (idx < 0) return;
      if (event.key === "ArrowRight") {
        event.preventDefault();
        focusTab(TAB_ORDER[(idx + 1) % TAB_ORDER.length]!);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        focusTab(TAB_ORDER[(idx - 1 + TAB_ORDER.length) % TAB_ORDER.length]!);
      } else if (event.key === "Home") {
        event.preventDefault();
        focusTab(TAB_ORDER[0]!);
      } else if (event.key === "End") {
        event.preventDefault();
        focusTab(TAB_ORDER[TAB_ORDER.length - 1]!);
      }
    },
    [activeTab, focusTab],
  );

  // -- Save ------------------------------------------------------------------

  const handleSave = useCallback(() => {
    onSave(value);
  }, [onSave, value]);

  // -- Styles ----------------------------------------------------------------

  const containerStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 12,
    padding: 16,
    background: "var(--color-surface)",
    color: "var(--color-text)",
    border: "1px solid var(--color-border)",
    borderRadius: 10,
    boxSizing: "border-box",
  };

  const headerStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 12,
    flexWrap: "wrap",
  };

  const nameHeaderInputStyle: CSSProperties = {
    flex: 1,
    minWidth: 200,
    height: 32,
    padding: "0 10px",
    borderRadius: 6,
    border: "1px solid var(--color-border-strong)",
    background: "transparent",
    color: "var(--color-text)",
    fontSize: 14,
    fontWeight: 600,
  };

  const tabStripStyle: CSSProperties = {
    display: "flex",
    gap: 0,
    borderBottom: "1px solid var(--color-border)",
  };

  return (
    <div
      style={containerStyle}
      data-testid="routine-editor"
      data-status={isDraft ? "draft" : "ready"}
    >
      <div style={headerStyle}>
        <StatusPill
          status={isDraft ? "muted" : "ok"}
          label={isDraft ? "Draft" : "Ready"}
        />
        <input
          type="text"
          value={value.name}
          maxLength={80}
          onChange={(e) => updateField("name", e.target.value)}
          placeholder="Routine name"
          aria-label="Routine name (header)"
          data-testid="routine-editor-name-header"
          style={nameHeaderInputStyle}
          disabled={disabled}
        />
        <button
          type="button"
          onClick={onCancel}
          data-testid="routine-editor-cancel"
          style={secondaryButtonStyle}
          disabled={disabled}
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={handleSave}
          data-testid="routine-editor-save"
          style={primaryButtonStyle}
          disabled={disabled}
        >
          Save
        </button>
      </div>

      <div
        role="tablist"
        aria-label="Routine editor"
        style={tabStripStyle}
        onKeyDown={onTabKeyDown}
      >
        {TAB_ORDER.map((tab) => (
          <button
            key={tab}
            ref={(node) => {
              tabRefs.current[tab] = node;
            }}
            type="button"
            role="tab"
            id={`routine-editor-tab-${tab}`}
            aria-selected={activeTab === tab}
            aria-controls={`routine-editor-tabpanel-${tab}`}
            tabIndex={activeTab === tab ? 0 : -1}
            onClick={() => setActiveTab(tab)}
            data-testid={`routine-editor-tab-${tab}`}
            style={tabButtonStyle(activeTab === tab)}
          >
            {TAB_LABEL[tab]}
          </button>
        ))}
      </div>

      <div
        role="tabpanel"
        id={`routine-editor-tabpanel-${activeTab}`}
        aria-labelledby={`routine-editor-tab-${activeTab}`}
        data-testid={`routine-editor-tabpanel-${activeTab}`}
        data-active-tab={activeTab}
      >
        {activeTab === "name" ? (
          <NameTab
            value={value}
            disabled={disabled}
            onChangeName={(v) => updateField("name", v)}
            onChangeDescription={(v) => updateField("description", v)}
          />
        ) : null}
        {activeTab === "instructions" ? (
          <InstructionsTab
            value={value}
            disabled={disabled}
            onChange={(v) => updateField("instructions", v)}
          />
        ) : null}
        {activeTab === "triggers" ? (
          <TriggersTab
            triggers={value.triggers}
            disabled={disabled}
            onChange={(triggers) => updateField("triggers", triggers)}
          />
        ) : null}
        {activeTab === "connectors" ? (
          <ConnectorsTab
            connectors={value.connectors}
            available={availableConnectors}
            disabled={disabled}
            onChange={(connectors) => updateField("connectors", connectors)}
          />
        ) : null}
        {activeTab === "behavior" ? (
          <BehaviorTab
            behavior={value.behavior}
            missedFirePolicy={value.missed_fire_policy}
            agentVersionPin={value.agent_version_pin}
            disabled={disabled}
            onChangeBehavior={updateBehavior}
            onChangeMissedFirePolicy={(p) =>
              updateField("missed_fire_policy", p)
            }
            onChangeAgentVersionPin={(p) => updateField("agent_version_pin", p)}
          />
        ) : null}
        {activeTab === "permissions" ? (
          <PermissionsTab
            permissions={value.permissions}
            projectId={value.project_id}
            availableProjects={availableProjects}
            disabled={disabled}
            onChangePermissions={updatePermissions}
            onChangeProjectId={(id) => updateField("project_id", id)}
          />
        ) : null}
      </div>
    </div>
  );
}

// ===========================================================================
// Name tab.
// ===========================================================================

interface NameTabProps {
  readonly value: RoutineEditorValue;
  readonly disabled: boolean;
  readonly onChangeName: (v: string) => void;
  readonly onChangeDescription: (v: string) => void;
}

function NameTab(props: NameTabProps): ReactElement {
  const { value, disabled, onChangeName, onChangeDescription } = props;
  return (
    <FieldGroup>
      <Field label="Name" hint="Required, up to 80 characters.">
        <input
          type="text"
          value={value.name}
          onChange={(e) => onChangeName(e.target.value)}
          maxLength={80}
          aria-label="Name"
          data-testid="routine-editor-name-input"
          style={textInputStyle}
          disabled={disabled}
        />
      </Field>
      <Field label="Description" hint="Optional, up to 200 characters.">
        <textarea
          value={value.description}
          onChange={(e) => onChangeDescription(e.target.value)}
          maxLength={200}
          rows={2}
          aria-label="Description"
          data-testid="routine-editor-description-input"
          style={{ ...textInputStyle, height: "auto", padding: "8px 10px" }}
          disabled={disabled}
        />
      </Field>
    </FieldGroup>
  );
}

// ===========================================================================
// Instructions tab — reuses the shared Composer in mode="compose".
// ===========================================================================

interface InstructionsTabProps {
  readonly value: RoutineEditorValue;
  readonly disabled: boolean;
  readonly onChange: (v: string) => void;
}

function InstructionsTab(props: InstructionsTabProps): ReactElement {
  const { value, disabled, onChange } = props;

  // Composer is a "submit-on-Enter" surface; for the Instructions field
  // we just want the textarea + toolbar. We use `onSave` (edit-mode
  // handler) to capture the buffered text and treat `onSubmit` as the
  // same write to make rapid typing not commit on accidental Enter
  // submission — both paths funnel through `onChange`.
  const handleCommit = useCallback(
    (text: string) => {
      onChange(text);
    },
    [onChange],
  );

  return (
    <div data-testid="routine-editor-instructions">
      <p style={hintStyle}>
        Multi-line. Up to 16 KB. The agent uses these instructions every fire.
      </p>
      <Composer
        mode="compose"
        initialText={value.instructions}
        placeholder="Describe what the routine should do each time it runs…"
        disabled={disabled}
        onSubmit={(payload) => handleCommit(payload.text)}
        onSave={handleCommit}
      />
    </div>
  );
}

// ===========================================================================
// Triggers tab — schedule (cron) + webhook + event + manual.
// ===========================================================================

interface TriggersTabProps {
  readonly triggers: ReadonlyArray<RoutineEditorTrigger>;
  readonly disabled: boolean;
  readonly onChange: (triggers: ReadonlyArray<RoutineEditorTrigger>) => void;
}

function TriggersTab(props: TriggersTabProps): ReactElement {
  const { triggers, disabled, onChange } = props;

  const addSchedule = useCallback(() => {
    const next: RoutineEditorTrigger = {
      kind: "schedule",
      cron: "0 9 * * *",
      tz: "UTC",
    };
    onChange([...triggers, next]);
  }, [triggers, onChange]);

  const addWebhook = useCallback(() => {
    onChange([...triggers, { kind: "webhook", ip_allowlist: [] }]);
  }, [triggers, onChange]);

  const addEvent = useCallback(() => {
    onChange([
      ...triggers,
      { kind: "event", event_source: "inbox.item_created", filter: [] },
    ]);
  }, [triggers, onChange]);

  const addManual = useCallback(() => {
    if (triggers.some((t) => t.kind === "manual")) return;
    onChange([...triggers, { kind: "manual" }]);
  }, [triggers, onChange]);

  const update = useCallback(
    (index: number, next: RoutineEditorTrigger) => {
      const out = triggers.slice();
      out[index] = next;
      onChange(out);
    },
    [triggers, onChange],
  );

  const remove = useCallback(
    (index: number) => {
      onChange(triggers.filter((_, i) => i !== index));
    },
    [triggers, onChange],
  );

  return (
    <div data-testid="routine-editor-triggers">
      <p style={hintStyle}>
        At least one trigger is required to activate. Multiple triggers are
        OR-combined.
      </p>
      <div style={triggerListStyle}>
        {triggers.map((trigger, i) => (
          <TriggerCard
            key={i}
            trigger={trigger}
            disabled={disabled}
            onChange={(next) => update(i, next)}
            onRemove={() => remove(i)}
            testId={`routine-editor-trigger-${i}`}
          />
        ))}
        {triggers.length === 0 ? (
          <p
            data-testid="routine-editor-triggers-empty"
            role="status"
            style={emptyStyle}
          >
            No triggers yet — add one below.
          </p>
        ) : null}
      </div>
      <div style={addRowStyle} role="group" aria-label="Add trigger">
        <button
          type="button"
          onClick={addSchedule}
          data-testid="routine-editor-add-schedule"
          style={secondaryButtonStyle}
          disabled={disabled}
        >
          + Schedule
        </button>
        <button
          type="button"
          onClick={addWebhook}
          data-testid="routine-editor-add-webhook"
          style={secondaryButtonStyle}
          disabled={disabled}
        >
          + Webhook
        </button>
        <button
          type="button"
          onClick={addEvent}
          data-testid="routine-editor-add-event"
          style={secondaryButtonStyle}
          disabled={disabled}
        >
          + Event
        </button>
        <button
          type="button"
          onClick={addManual}
          data-testid="routine-editor-add-manual"
          style={secondaryButtonStyle}
          disabled={disabled || triggers.some((t) => t.kind === "manual")}
        >
          + Manual
        </button>
      </div>
    </div>
  );
}

interface TriggerCardProps {
  readonly trigger: RoutineEditorTrigger;
  readonly disabled: boolean;
  readonly onChange: (next: RoutineEditorTrigger) => void;
  readonly onRemove: () => void;
  readonly testId: string;
}

function TriggerCard(props: TriggerCardProps): ReactElement {
  const { trigger, disabled, onChange, onRemove, testId } = props;
  return (
    <div
      style={triggerCardStyle}
      data-testid={testId}
      data-trigger-kind={trigger.kind}
    >
      <div style={triggerCardHeaderStyle}>
        <StatusPill status="info" label={trigger.kind} />
        <button
          type="button"
          onClick={onRemove}
          data-testid={`${testId}-remove`}
          aria-label={`Remove ${trigger.kind} trigger`}
          style={iconButtonStyle}
          disabled={disabled}
        >
          remove
        </button>
      </div>
      {trigger.kind === "schedule" ? (
        <CronEditor
          cron={trigger.cron}
          tz={trigger.tz}
          disabled={disabled}
          onChange={(cron, tz) =>
            onChange({ ...trigger, kind: "schedule", cron, tz })
          }
        />
      ) : null}
      {trigger.kind === "webhook" ? (
        <WebhookEditor
          trigger={trigger}
          disabled={disabled}
          onChange={(next) => onChange(next)}
        />
      ) : null}
      {trigger.kind === "event" ? (
        <EventEditor
          trigger={trigger}
          disabled={disabled}
          onChange={(next) => onChange(next)}
        />
      ) : null}
      {trigger.kind === "manual" ? (
        <p style={hintStyle}>
          Manual triggers add a "Run now" button on the routine detail view. See
          Permissions tab for who can fire it.
        </p>
      ) : null}
    </div>
  );
}

// ---- Cron editor ----------------------------------------------------------

interface CronEditorProps {
  readonly cron: string;
  readonly tz: string;
  readonly disabled: boolean;
  readonly onChange: (cron: string, tz: string) => void;
}

/**
 * Mirrors the recurrence-editor structure: discrete frequency + time +
 * day picker, with Advanced mode for raw cron string + live preview.
 * Exported for reuse in P5-B3's detail panel (cross-component reuse,
 * still in chat-surface — SP-1).
 */
export function CronEditor(props: CronEditorProps): ReactElement {
  const { cron, tz, disabled, onChange } = props;
  const draft = useMemo(() => parseCronSpec(cron), [cron]);
  const [advanced, setAdvanced] = useState(draft.frequency === "custom");

  const emit = useCallback(
    (next: {
      frequency: CronFrequency;
      hour: number;
      minute: number;
      weekdays: ReadonlyArray<WeekdayCode>;
      dayOfMonth: number;
      raw: string;
    }) => {
      onChange(buildCronSpec(next), tz);
    },
    [onChange, tz],
  );

  const valid = isValidCronSpec(cron);

  return (
    <div data-testid="routine-editor-cron">
      <div style={rowStyle}>
        <label style={labelStyle}>Mode</label>
        <button
          type="button"
          aria-pressed={!advanced}
          data-testid="routine-editor-cron-mode-simple"
          onClick={() => setAdvanced(false)}
          style={toggleButtonStyle(!advanced)}
          disabled={disabled}
        >
          Simple
        </button>
        <button
          type="button"
          aria-pressed={advanced}
          data-testid="routine-editor-cron-mode-advanced"
          onClick={() => setAdvanced(true)}
          style={toggleButtonStyle(advanced)}
          disabled={disabled}
        >
          Advanced
        </button>
      </div>

      {advanced ? (
        <div style={rowStyle}>
          <label style={labelStyle} htmlFor="routine-editor-cron-raw">
            Cron
          </label>
          <input
            id="routine-editor-cron-raw"
            type="text"
            value={draft.raw}
            onChange={(e) =>
              emit({ ...draft, frequency: "custom", raw: e.target.value })
            }
            aria-invalid={!valid}
            aria-describedby="routine-editor-cron-preview"
            data-testid="routine-editor-cron-raw"
            placeholder="e.g. 0 9 * * 1-5"
            style={{ ...textInputStyle, fontFamily: "monospace" }}
            disabled={disabled}
          />
        </div>
      ) : (
        <>
          <div style={rowStyle}>
            <label style={labelStyle}>Frequency</label>
            <select
              value={draft.frequency}
              onChange={(e) =>
                emit({
                  ...draft,
                  frequency: e.target.value as CronFrequency,
                })
              }
              aria-label="Schedule frequency"
              data-testid="routine-editor-cron-frequency"
              style={selectStyle}
              disabled={disabled}
            >
              <option value="hourly">Hourly</option>
              <option value="daily">Daily</option>
              <option value="weekdays">Weekdays</option>
              <option value="weekly">Weekly</option>
              <option value="monthly">Monthly</option>
            </select>
          </div>
          {draft.frequency !== "hourly" ? (
            <div style={rowStyle}>
              <label style={labelStyle}>At</label>
              <input
                type="number"
                min={0}
                max={23}
                value={draft.hour}
                onChange={(e) =>
                  emit({
                    ...draft,
                    hour: Number.parseInt(e.target.value, 10),
                  })
                }
                aria-label="Hour (0-23)"
                data-testid="routine-editor-cron-hour"
                style={numberInputStyle}
                disabled={disabled}
              />
              <span>:</span>
              <input
                type="number"
                min={0}
                max={59}
                value={draft.minute}
                onChange={(e) =>
                  emit({
                    ...draft,
                    minute: Number.parseInt(e.target.value, 10),
                  })
                }
                aria-label="Minute (0-59)"
                data-testid="routine-editor-cron-minute"
                style={numberInputStyle}
                disabled={disabled}
              />
            </div>
          ) : (
            <div style={rowStyle}>
              <label style={labelStyle}>At minute</label>
              <input
                type="number"
                min={0}
                max={59}
                value={draft.minute}
                onChange={(e) =>
                  emit({
                    ...draft,
                    minute: Number.parseInt(e.target.value, 10),
                  })
                }
                aria-label="Minute (0-59)"
                data-testid="routine-editor-cron-minute"
                style={numberInputStyle}
                disabled={disabled}
              />
            </div>
          )}
          {draft.frequency === "weekly" ? (
            <div style={rowStyle} role="group" aria-label="Days of week">
              <label style={labelStyle}>On</label>
              {WEEKDAY_CODES.map((code) => {
                const active = draft.weekdays.includes(code);
                return (
                  <button
                    key={code}
                    type="button"
                    role="checkbox"
                    aria-checked={active}
                    aria-label={WEEKDAY_LONG[code]}
                    onClick={() =>
                      emit({
                        ...draft,
                        weekdays: active
                          ? draft.weekdays.filter((d) => d !== code)
                          : [...draft.weekdays, code],
                      })
                    }
                    data-testid={`routine-editor-cron-weekday-${code}`}
                    data-active={active ? "true" : "false"}
                    style={weekdayButtonStyle(active)}
                    disabled={disabled}
                  >
                    {WEEKDAY_SHORT[code]}
                  </button>
                );
              })}
            </div>
          ) : null}
          {draft.frequency === "monthly" ? (
            <div style={rowStyle}>
              <label style={labelStyle}>Day of month</label>
              <input
                type="number"
                min={1}
                max={31}
                value={draft.dayOfMonth}
                onChange={(e) =>
                  emit({
                    ...draft,
                    dayOfMonth: Number.parseInt(e.target.value, 10),
                  })
                }
                aria-label="Day of month (1-31)"
                data-testid="routine-editor-cron-day-of-month"
                style={numberInputStyle}
                disabled={disabled}
              />
            </div>
          ) : null}
        </>
      )}

      <div style={rowStyle}>
        <label style={labelStyle} htmlFor="routine-editor-cron-tz">
          Timezone
        </label>
        <input
          id="routine-editor-cron-tz"
          type="text"
          value={tz}
          onChange={(e) => onChange(cron, e.target.value)}
          aria-label="Timezone"
          data-testid="routine-editor-cron-tz"
          placeholder="UTC"
          style={textInputStyle}
          disabled={disabled}
        />
      </div>

      <p
        id="routine-editor-cron-preview"
        data-testid="routine-editor-cron-preview"
        aria-live="polite"
        style={previewStyle}
      >
        {valid ? previewCron(cron, tz) : "Invalid cron — fix and re-check"}
      </p>
    </div>
  );
}

// ---- Webhook editor -------------------------------------------------------

interface WebhookEditorProps {
  readonly trigger: Extract<RoutineEditorTrigger, { kind: "webhook" }>;
  readonly disabled: boolean;
  readonly onChange: (
    next: Extract<RoutineEditorTrigger, { kind: "webhook" }>,
  ) => void;
}

function WebhookEditor(props: WebhookEditorProps): ReactElement {
  const { trigger, disabled, onChange } = props;
  const [raw, setRaw] = useState(trigger.ip_allowlist.join(", "));

  const commitAllowlist = useCallback(
    (next: string) => {
      setRaw(next);
      const cidrs = next
        .split(",")
        .map((s) => s.trim())
        .filter((s) => s.length > 0);
      onChange({ ...trigger, ip_allowlist: cidrs });
    },
    [trigger, onChange],
  );

  return (
    <div data-testid="routine-editor-webhook">
      <p style={hintStyle}>
        URL + secret are generated server-side on save. Rotate the secret from
        the detail view (7-day grace window).
      </p>
      <Field label="IP allowlist (CIDR, comma-separated)">
        <input
          type="text"
          value={raw}
          onChange={(e) => commitAllowlist(e.target.value)}
          aria-label="IP allowlist"
          data-testid="routine-editor-webhook-allowlist"
          placeholder="10.0.0.0/8, 192.168.1.0/24"
          style={textInputStyle}
          disabled={disabled}
        />
      </Field>
    </div>
  );
}

// ---- Event editor ---------------------------------------------------------

const EVENT_SOURCES: ReadonlyArray<string> = [
  "inbox.item_created",
  "library.page_created",
  "library.file_uploaded",
  "library.dataset_updated",
  "todos.item_created",
  "chats.run_completed",
];

interface EventEditorProps {
  readonly trigger: Extract<RoutineEditorTrigger, { kind: "event" }>;
  readonly disabled: boolean;
  readonly onChange: (
    next: Extract<RoutineEditorTrigger, { kind: "event" }>,
  ) => void;
}

function EventEditor(props: EventEditorProps): ReactElement {
  const { trigger, disabled, onChange } = props;
  return (
    <div data-testid="routine-editor-event">
      <Field label="Event source">
        <select
          value={trigger.event_source}
          onChange={(e) =>
            onChange({ ...trigger, event_source: e.target.value })
          }
          data-testid="routine-editor-event-source"
          aria-label="Event source"
          style={selectStyle}
          disabled={disabled}
        >
          {EVENT_SOURCES.map((src) => (
            <option key={src} value={src}>
              {src}
            </option>
          ))}
        </select>
      </Field>
    </div>
  );
}

// ===========================================================================
// Connectors tab.
// ===========================================================================

interface ConnectorsTabProps {
  readonly connectors: ReadonlyArray<RoutineEditorConnectorConfig>;
  readonly available: ReadonlyArray<{
    readonly connector_id: ConnectorId;
    readonly label: string;
  }>;
  readonly disabled: boolean;
  readonly onChange: (
    next: ReadonlyArray<RoutineEditorConnectorConfig>,
  ) => void;
}

function ConnectorsTab(props: ConnectorsTabProps): ReactElement {
  const { connectors, available, disabled, onChange } = props;

  const updateConnector = useCallback(
    (
      connector_id: ConnectorId,
      mode: "inherit" | "read_only" | "custom" | null,
    ) => {
      if (mode === null) {
        onChange(connectors.filter((c) => c.connector_id !== connector_id));
        return;
      }
      const existing = connectors.find((c) => c.connector_id === connector_id);
      if (existing === undefined) {
        onChange([...connectors, { connector_id, mode }]);
      } else {
        onChange(
          connectors.map((c) =>
            c.connector_id === connector_id ? { ...c, mode } : c,
          ),
        );
      }
    },
    [connectors, onChange],
  );

  return (
    <div data-testid="routine-editor-connectors">
      <p style={hintStyle}>
        Routine cannot widen scope beyond the owner's current grants. Effective
        scope is intersected at fire time.
      </p>
      {available.length === 0 ? (
        <p
          data-testid="routine-editor-connectors-empty"
          role="status"
          style={emptyStyle}
        >
          No connectors available.
        </p>
      ) : (
        <ul style={listStyle}>
          {available.map((conn) => {
            const cfg = connectors.find(
              (c) => c.connector_id === conn.connector_id,
            );
            const mode = cfg?.mode ?? null;
            return (
              <li
                key={conn.connector_id}
                style={connectorRowStyle}
                data-testid={`routine-editor-connector-${conn.connector_id}`}
              >
                <span>{conn.label}</span>
                <select
                  value={mode ?? "off"}
                  onChange={(e) =>
                    updateConnector(
                      conn.connector_id,
                      e.target.value === "off"
                        ? null
                        : (e.target.value as
                            | "inherit"
                            | "read_only"
                            | "custom"),
                    )
                  }
                  aria-label={`Connector ${conn.label} mode`}
                  data-testid={`routine-editor-connector-${conn.connector_id}-mode`}
                  style={selectStyle}
                  disabled={disabled}
                >
                  <option value="off">Off</option>
                  <option value="inherit">Inherit</option>
                  <option value="read_only">Read-only</option>
                  <option value="custom">Custom</option>
                </select>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// ===========================================================================
// Behavior tab — output target, missed_fire_policy radio, agent_version_pin.
// ===========================================================================

interface BehaviorTabProps {
  readonly behavior: RoutineEditorBehavior;
  readonly missedFirePolicy: RoutineMissedFirePolicy;
  readonly agentVersionPin: string | null;
  readonly disabled: boolean;
  readonly onChangeBehavior: <K extends keyof RoutineEditorBehavior>(
    key: K,
    next: RoutineEditorBehavior[K],
  ) => void;
  readonly onChangeMissedFirePolicy: (p: RoutineMissedFirePolicy) => void;
  readonly onChangeAgentVersionPin: (p: string | null) => void;
}

const MISSED_FIRE_LABEL: Readonly<Record<RoutineMissedFirePolicy, string>> = {
  fire_once: "Fire once (catch up exactly once, skip rest)",
  fire_all: "Fire all (replay every missed window)",
  skip_all: "Skip all (drop the backlog)",
};

const OUTPUT_TARGET_KINDS: ReadonlyArray<RoutineOutputTarget["kind"]> = [
  "inbox",
  "library_page",
  "existing_chat",
  "project_log",
];

function BehaviorTab(props: BehaviorTabProps): ReactElement {
  const {
    behavior,
    missedFirePolicy,
    agentVersionPin,
    disabled,
    onChangeBehavior,
    onChangeMissedFirePolicy,
    onChangeAgentVersionPin,
  } = props;

  const setOutputKind = useCallback(
    (kind: RoutineOutputTarget["kind"]) => {
      // Resetting kind drops the ItemRef — host will re-pick via the
      // detail editor / picker before save in a follow-up. For the
      // editor we keep the kind valid by emitting the inbox shape for
      // non-ref kinds when we lack one.
      if (kind === "inbox") {
        onChangeBehavior("output_target", { kind: "inbox" });
        return;
      }
      // For ref-bearing kinds we keep any existing ref but reshape it.
      const existingRef =
        behavior.output_target.kind !== "inbox"
          ? behavior.output_target.ref
          : null;
      if (existingRef !== null) {
        if (kind === "library_page") {
          onChangeBehavior("output_target", {
            kind,
            ref: existingRef,
            mode: "new_per_fire",
          });
        } else {
          onChangeBehavior("output_target", { kind, ref: existingRef });
        }
      } else {
        // No ref yet → fall back to inbox; the user picks a ref in a
        // separate flow before activation (host-owned picker).
        onChangeBehavior("output_target", { kind: "inbox" });
      }
    },
    [behavior.output_target, onChangeBehavior],
  );

  return (
    <div data-testid="routine-editor-behavior">
      <FieldGroup>
        <Field
          label="Missed-fire policy"
          hint="Cross-audit §9.7 Q7. Default is 'fire_once' to avoid replay storms."
        >
          <div
            role="radiogroup"
            aria-label="Missed-fire policy"
            data-testid="routine-editor-missed-fire-policy"
            style={radioGroupStyle}
          >
            {(
              [
                "fire_once",
                "fire_all",
                "skip_all",
              ] as ReadonlyArray<RoutineMissedFirePolicy>
            ).map((p) => (
              <label
                key={p}
                style={radioLabelStyle}
                data-testid={`routine-editor-missed-fire-policy-${p}`}
              >
                <input
                  type="radio"
                  name="missed-fire-policy"
                  value={p}
                  checked={missedFirePolicy === p}
                  onChange={() => onChangeMissedFirePolicy(p)}
                  disabled={disabled}
                />
                <span>{MISSED_FIRE_LABEL[p]}</span>
              </label>
            ))}
          </div>
        </Field>

        <Field
          label="Output target"
          hint="Where each fire writes its result. Library pages and chats need a ref selected on the detail page."
        >
          <select
            value={behavior.output_target.kind}
            onChange={(e) =>
              setOutputKind(e.target.value as RoutineOutputTarget["kind"])
            }
            aria-label="Output target kind"
            data-testid="routine-editor-output-target-kind"
            style={selectStyle}
            disabled={disabled}
          >
            {OUTPUT_TARGET_KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
          {behavior.output_target.kind === "library_page" ? (
            <select
              value={behavior.output_target.mode}
              onChange={(e) => {
                if (behavior.output_target.kind !== "library_page") return;
                onChangeBehavior("output_target", {
                  ...behavior.output_target,
                  mode: e.target.value as "new_per_fire" | "update_same",
                });
              }}
              aria-label="Library-page output mode"
              data-testid="routine-editor-output-target-mode"
              style={selectStyle}
              disabled={disabled}
            >
              <option value="new_per_fire">New page per fire</option>
              <option value="update_same">Update the same page</option>
            </select>
          ) : null}
        </Field>

        <Field
          label="Pin agent version"
          hint="Cross-audit §9.7 Q11. Opt-in. Off = live re-resolve at fire time."
        >
          <label
            style={radioLabelStyle}
            data-testid="routine-editor-agent-version-pin-toggle"
          >
            <input
              type="checkbox"
              checked={agentVersionPin !== null}
              onChange={(e) =>
                onChangeAgentVersionPin(e.target.checked ? "" : null)
              }
              disabled={disabled}
            />
            <span>Pin this routine to a specific agent version</span>
          </label>
          {agentVersionPin !== null ? (
            <input
              type="text"
              value={agentVersionPin}
              onChange={(e) => onChangeAgentVersionPin(e.target.value)}
              aria-label="Agent version pin"
              data-testid="routine-editor-agent-version-pin-input"
              placeholder="e.g. v1.4.2"
              style={textInputStyle}
              disabled={disabled}
            />
          ) : null}
        </Field>
      </FieldGroup>
    </div>
  );
}

// ===========================================================================
// Permissions tab — manual_fire override + project_id picker.
// ===========================================================================

interface PermissionsTabProps {
  readonly permissions: RoutineEditorPermissions;
  readonly projectId: ProjectId | null;
  readonly availableProjects: ReadonlyArray<{
    readonly project_id: ProjectId;
    readonly label: string;
  }>;
  readonly disabled: boolean;
  readonly onChangePermissions: <K extends keyof RoutineEditorPermissions>(
    key: K,
    next: RoutineEditorPermissions[K],
  ) => void;
  readonly onChangeProjectId: (id: ProjectId | null) => void;
}

const MANUAL_FIRE_LABEL: Readonly<Record<RoutineManualFire, string>> = {
  owner: "Owner only (default)",
  project_members: "Project members can fire",
  tenant: "Anyone in the tenant can fire",
};

function PermissionsTab(props: PermissionsTabProps): ReactElement {
  const {
    permissions,
    projectId,
    availableProjects,
    disabled,
    onChangePermissions,
    onChangeProjectId,
  } = props;
  return (
    <div data-testid="routine-editor-permissions">
      <FieldGroup>
        <Field
          label="Manual-fire override"
          hint="Routines-prd §3.11 + cross-audit §9.7 Q2."
        >
          <div
            role="radiogroup"
            aria-label="Manual-fire override"
            data-testid="routine-editor-manual-fire"
            style={radioGroupStyle}
          >
            {(
              [
                "owner",
                "project_members",
                "tenant",
              ] as ReadonlyArray<RoutineManualFire>
            ).map((opt) => (
              <label
                key={opt}
                style={radioLabelStyle}
                data-testid={`routine-editor-manual-fire-${opt}`}
              >
                <input
                  type="radio"
                  name="manual-fire"
                  value={opt}
                  checked={permissions.manual_fire === opt}
                  onChange={() => onChangePermissions("manual_fire", opt)}
                  disabled={
                    disabled ||
                    (opt === "project_members" && projectId === null)
                  }
                />
                <span>{MANUAL_FIRE_LABEL[opt]}</span>
              </label>
            ))}
          </div>
        </Field>

        <Field
          label="Project"
          hint="When filed under a project, project ACL applies in addition to owner."
        >
          <select
            value={projectId ?? ""}
            onChange={(e) =>
              onChangeProjectId(
                e.target.value === ""
                  ? null
                  : (e.target.value as unknown as ProjectId),
              )
            }
            aria-label="Project"
            data-testid="routine-editor-project"
            style={selectStyle}
            disabled={disabled}
          >
            <option value="">(none)</option>
            {availableProjects.map((p) => (
              <option key={p.project_id} value={p.project_id}>
                {p.label}
              </option>
            ))}
          </select>
        </Field>
      </FieldGroup>
    </div>
  );
}

// ===========================================================================
// Layout primitives — local, kept tiny on purpose.
// ===========================================================================

function FieldGroup(props: { readonly children: ReactNode }): ReactElement {
  return <div style={fieldGroupStyle}>{props.children}</div>;
}

function Field(props: {
  readonly label: string;
  readonly hint?: string;
  readonly children: ReactNode;
}): ReactElement {
  return (
    <div style={fieldStyle}>
      <label style={labelStyle}>{props.label}</label>
      {props.children}
      {props.hint !== undefined ? <p style={hintStyle}>{props.hint}</p> : null}
    </div>
  );
}

// ===========================================================================
// Styles.
// ===========================================================================

const tabButtonStyle = (selected: boolean): CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  background: "transparent",
  border: "none",
  borderBottom: `2px solid ${selected ? "var(--color-accent)" : "transparent"}`,
  color: selected ? "var(--color-text)" : "var(--color-text-muted)",
  padding: "8px 14px",
  fontSize: 13,
  fontFamily: "inherit",
  cursor: "pointer",
});

const fieldGroupStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 14,
  padding: "12px 0",
};

const fieldStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  flexWrap: "wrap",
  padding: "6px 0",
};

const labelStyle: CSSProperties = {
  fontSize: 12,
  fontWeight: 600,
  color: "var(--color-text-muted)",
};

const hintStyle: CSSProperties = {
  margin: "4px 0 0 0",
  fontSize: 12,
  color: "var(--color-text-subtle)",
  fontStyle: "italic",
};

const textInputStyle: CSSProperties = {
  height: 30,
  padding: "0 10px",
  borderRadius: 6,
  border: "1px solid var(--color-border-strong)",
  background: "transparent",
  color: "var(--color-text)",
  fontSize: 13,
  fontFamily: "inherit",
  boxSizing: "border-box",
};

const numberInputStyle: CSSProperties = {
  ...textInputStyle,
  width: 64,
};

const selectStyle: CSSProperties = {
  height: 30,
  padding: "0 8px",
  borderRadius: 6,
  border: "1px solid var(--color-border-strong)",
  background: "transparent",
  color: "var(--color-text)",
  fontSize: 13,
  fontFamily: "inherit",
};

const primaryButtonStyle: CSSProperties = {
  background: "var(--color-accent)",
  color: "var(--color-accent-contrast, var(--color-bg))",
  border: "none",
  borderRadius: 6,
  padding: "6px 14px",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
};

const secondaryButtonStyle: CSSProperties = {
  background: "transparent",
  color: "var(--color-text)",
  border: "1px solid var(--color-border-strong)",
  borderRadius: 6,
  padding: "5px 12px",
  fontSize: 13,
  cursor: "pointer",
};

const iconButtonStyle: CSSProperties = {
  background: "transparent",
  color: "var(--color-text-muted)",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  padding: "3px 8px",
  fontSize: 11.5,
  cursor: "pointer",
};

const toggleButtonStyle = (active: boolean): CSSProperties => ({
  background: active ? "var(--color-accent-soft, transparent)" : "transparent",
  color: active ? "var(--color-accent)" : "var(--color-text-muted)",
  border: `1px solid ${active ? "var(--color-accent)" : "var(--color-border-strong)"}`,
  borderRadius: 999,
  padding: "3px 12px",
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
});

const weekdayButtonStyle = (active: boolean): CSSProperties => ({
  width: 28,
  height: 28,
  padding: 0,
  borderRadius: 999,
  border: `1px solid ${active ? "var(--color-accent)" : "var(--color-border-strong)"}`,
  background: active ? "var(--color-accent)" : "transparent",
  color: active ? "var(--color-bg)" : "var(--color-text)",
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
});

const previewStyle: CSSProperties = {
  margin: "8px 0 0 0",
  fontSize: 12,
  color: "var(--color-text-subtle)",
  fontStyle: "italic",
};

const radioGroupStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const radioLabelStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 8,
  fontSize: 13,
  color: "var(--color-text)",
};

const triggerListStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 10,
  padding: "8px 0",
};

const triggerCardStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: 12,
  borderRadius: 8,
  border: "1px solid var(--color-border)",
  background: "var(--color-surface-muted)",
};

const triggerCardHeaderStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
};

const addRowStyle: CSSProperties = {
  display: "flex",
  gap: 8,
  flexWrap: "wrap",
};

const emptyStyle: CSSProperties = {
  margin: "8px 0",
  fontSize: 12.5,
  color: "var(--color-text-subtle)",
};

const listStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  display: "flex",
  flexDirection: "column",
  gap: 8,
};

const connectorRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
  padding: "6px 8px",
  borderRadius: 6,
  border: "1px solid var(--color-border)",
};
