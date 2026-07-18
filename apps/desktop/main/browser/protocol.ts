// AC8 agentic browser — shared TypeScript/Zod protocol (source of truth).
//
// This module is the single source of truth for the browser capability
// contract. Zod schemas derive runtime validation on the desktop side; the
// AI backend consumes the derived MCP JSON Schemas via `tools/list` and does
// NOT hand-copy these types.
//
// SCOPE (foundation, read-only core): only the safe read-only surface is
// modelled here — navigate, snapshot (accessibility inspect), screenshot,
// wait, and close. Side-effecting actions (click/type/select/submit/upload/
// download) are DEFERRED; their action classes are named in the enum with a
// `// deferred` marker so the policy layer can reason about them, but no tool
// schema is exposed for them yet. There is deliberately NO generic
// eval/JS/selector/coordinate/CDP escape hatch.

import { z } from "zod";

// --- Enumerations ---------------------------------------------------------

export const BROWSER_PROTOCOL_VERSION = 1 as const;

/** Stable server name of the desktop-local browser MCP provider. */
export const DESKTOP_BROWSER_SERVER_NAME = "desktop_browser";

/** Broker audience — every action credential is bound to this audience. */
export const BROWSER_BROKER_AUDIENCE = "desktop-browser-broker";

export const BrowserProfileMode = {
  Ephemeral: "ephemeral",
  Persistent: "persistent",
} as const;
export type BrowserProfileMode =
  (typeof BrowserProfileMode)[keyof typeof BrowserProfileMode];

/**
 * The action classes the policy layer can reason about. The read classes
 * (`read`, `navigate`) run freely within an approved origin set. The
 * side-effecting classes (`input`, `submit`, `download`) are LIVE in the action
 * layer but MUST clear a per-action approval before they dispatch. `upload` and
 * the `external_effect` marker remain DEFERRED (upload needs an AC5 object-ref
 * grant not modelled in this slice).
 */
export const BrowserActionClass = {
  Read: "read",
  Navigate: "navigate",
  Input: "input",
  Submit: "submit",
  Upload: "upload", // deferred (needs AC5 object-ref grant)
  Download: "download",
  ExternalEffect: "external_effect", // classification marker; no tool dispatches it
} as const;
export type BrowserActionClass =
  (typeof BrowserActionClass)[keyof typeof BrowserActionClass];

/** Action classes that run WITHOUT a per-action approval (reads). */
export const READ_ACTION_CLASSES: ReadonlySet<BrowserActionClass> = new Set([
  BrowserActionClass.Read,
  BrowserActionClass.Navigate,
]);

/**
 * Side-effecting action classes: every one MUST clear an approval before the
 * worker dispatches it (PRD §Action policy and approvals). `external_effect`
 * is the catch-all class for an ambiguous control treated as a side effect.
 */
export const SIDE_EFFECTING_ACTION_CLASSES: ReadonlySet<BrowserActionClass> =
  new Set([
    BrowserActionClass.Input,
    BrowserActionClass.Submit,
    BrowserActionClass.Download,
    BrowserActionClass.Upload,
    BrowserActionClass.ExternalEffect,
  ]);

/** True when an action class must clear a per-action approval before dispatch. */
export function actionRequiresApproval(
  actionClass: BrowserActionClass,
): boolean {
  return SIDE_EFFECTING_ACTION_CLASSES.has(actionClass);
}

/** MCP tool names exposed by the desktop browser server. */
export const BrowserToolName = {
  Navigate: "browser_navigate",
  Snapshot: "browser_snapshot",
  Wait: "browser_wait",
  Screenshot: "browser_screenshot",
  Close: "browser_close",
  // Action layer (side-effecting; each clears an approval before dispatch).
  Click: "browser_click",
  Type: "browser_type",
  Select: "browser_select",
  Submit: "browser_submit",
  Download: "browser_download",
} as const;
export type BrowserToolName =
  (typeof BrowserToolName)[keyof typeof BrowserToolName];

/**
 * Map a tool name to its action class. This is the AUTHORITATIVE classification
 * used by the worker — caller-supplied `actionClass` on a request is treated as
 * untrusted and re-derived here so a mislabelled request cannot smuggle a
 * side-effecting tool through the read path. Unknown tools return `null`.
 */
export function classifyTool(toolName: string): BrowserActionClass | null {
  switch (toolName) {
    case BrowserToolName.Navigate:
      return BrowserActionClass.Navigate;
    case BrowserToolName.Snapshot:
    case BrowserToolName.Wait:
    case BrowserToolName.Screenshot:
    case BrowserToolName.Close:
      return BrowserActionClass.Read;
    case BrowserToolName.Type:
    case BrowserToolName.Select:
      return BrowserActionClass.Input;
    case BrowserToolName.Click:
      // A click is treated as a side effect: it may submit, navigate
      // cross-origin, or trigger a download. Ambiguity resolves to "interrupt".
      return BrowserActionClass.ExternalEffect;
    case BrowserToolName.Submit:
      return BrowserActionClass.Submit;
    case BrowserToolName.Download:
      return BrowserActionClass.Download;
    default:
      return null;
  }
}

/**
 * Tool names that are DEFERRED. They are enumerated so the provider can assert
 * they are NOT advertised, and so a later slice can wire them without
 * re-deciding the contract. Upload needs an AC5 object-ref grant.
 */
export const DEFERRED_TOOL_NAMES: readonly string[] = ["browser_upload"];

/** Stable error codes (PRD §Stable errors). */
export const BrowserErrorCode = {
  Disabled: "browser_disabled",
  Unavailable: "browser_unavailable",
  ProfileBusy: "browser_profile_busy",
  ProfileVersionMismatch: "browser_profile_version_mismatch",
  ConsentRequired: "browser_consent_required",
  TakeoverActive: "browser_takeover_active",
  OriginApprovalRequired: "browser_origin_approval_required",
  NetworkDenied: "browser_network_denied",
  ElementStale: "browser_element_stale",
  SensitiveInputRequired: "browser_sensitive_input_required",
  ActionApprovalRequired: "browser_action_approval_required",
  ActionTimeout: "browser_action_timeout",
  ActionOutcomeUnknown: "browser_action_outcome_unknown",
  DownloadDenied: "browser_download_denied",
  ArtifactQuotaExceeded: "browser_artifact_quota_exceeded",
  Cancelled: "browser_cancelled",
  CleanupPending: "browser_cleanup_pending",
  // Foundation-internal: a tool was requested that this slice does not expose.
  ToolNotImplemented: "browser_tool_not_implemented",
  InvalidRequest: "browser_invalid_request",
} as const;
export type BrowserErrorCode =
  (typeof BrowserErrorCode)[keyof typeof BrowserErrorCode];

// --- Origin policy --------------------------------------------------------

/**
 * A canonical exact origin: `https://<punycode-host>` with an implicit :443.
 * Non-default ports, http, raw IPs, user-info, and wildcards are rejected at
 * validation time by `canonicalizeOrigin`.
 */
export const CanonicalOriginSchema = z
  .string()
  .min(1)
  .refine((v) => canonicalizeOrigin(v) === v, {
    message: "origin must be a canonical https exact origin",
  });

export const BrowserOriginPolicySchema = z.object({
  version: z.literal(1),
  topLevelOrigins: z.array(CanonicalOriginSchema).readonly(),
  subresourceOrigins: z.array(CanonicalOriginSchema).readonly(),
  denyPrivateNetworks: z.literal(true),
  serviceWorkers: z.literal("block"),
});
export type BrowserOriginPolicy = z.infer<typeof BrowserOriginPolicySchema>;

// --- Run binding ----------------------------------------------------------

export const BrowserRunBindingSchema = z.object({
  version: z.literal(1),
  runId: z.string().min(1),
  workspaceId: z.string().min(1),
  profileId: z.string().min(1),
  profileMode: z.enum([
    BrowserProfileMode.Ephemeral,
    BrowserProfileMode.Persistent,
  ]),
  approvalId: z.string().min(1),
  originPolicy: BrowserOriginPolicySchema,
  expiresAt: z.string().min(1),
  nonce: z.string().min(1),
});
export type BrowserRunBinding = z.infer<typeof BrowserRunBindingSchema>;

// --- Element references ---------------------------------------------------

export const BrowserElementRefSchema = z.object({
  sessionId: z.string().min(1),
  pageId: z.string().min(1),
  generation: z.number().int().nonnegative(),
  ref: z.string().min(1),
  role: z.string().min(1),
  redactedName: z.string(),
});
export type BrowserElementRef = z.infer<typeof BrowserElementRefSchema>;

// --- Accessibility snapshot (bounded) -------------------------------------

/** A single bounded accessibility node. Input values are never included. */
export interface BrowserSnapshotNode {
  ref: string;
  role: string;
  /** Redacted accessible name — never a raw value/secret. */
  name: string;
  children?: BrowserSnapshotNode[];
}

export const BrowserSnapshotNodeSchema: z.ZodType<BrowserSnapshotNode> = z.lazy(
  () =>
    z.object({
      ref: z.string(),
      role: z.string(),
      name: z.string(),
      children: z.array(BrowserSnapshotNodeSchema).optional(),
    }),
);

// --- Tool argument schemas (read-only foundation) -------------------------

export const NavigateArgsSchema = z.object({
  url: z.string().min(1),
});
export type NavigateArgs = z.infer<typeof NavigateArgsSchema>;

export const SnapshotArgsSchema = z.object({
  /** Optional element ref to scope the snapshot; omitted = whole page. */
  ref: z.string().min(1).optional(),
  /** Depth bound; clamped by the worker to SNAPSHOT_LIMITS.maxDepth. */
  depth: z.number().int().positive().optional(),
});
export type SnapshotArgs = z.infer<typeof SnapshotArgsSchema>;

export const WaitArgsSchema = z.object({
  /** Bounded, semantic wait condition. */
  condition: z.enum(["load", "networkidle", "timeout"]),
  timeoutMs: z.number().int().positive().max(30_000).optional(),
});
export type WaitArgs = z.infer<typeof WaitArgsSchema>;

export const ScreenshotArgsSchema = z.object({
  fullPage: z.boolean().optional(),
  /** Mask detected input fields / configured sensitive regions (default on). */
  redact: z.boolean().optional(),
});
export type ScreenshotArgs = z.infer<typeof ScreenshotArgsSchema>;

export const CloseArgsSchema = z.object({}).strict();

// --- Tool argument schemas (action layer, side-effecting) -----------------

/** A generation-bound element ref, as minted by the last snapshot. */
const ElementRefField = z.string().min(1);

export const ClickArgsSchema = z.object({
  ref: ElementRefField,
});
export type ClickArgs = z.infer<typeof ClickArgsSchema>;

export const TypeArgsSchema = z.object({
  ref: ElementRefField,
  /** Non-secret text. Secret fields (password/MFA/etc.) force user takeover. */
  text: z.string(),
});
export type TypeArgs = z.infer<typeof TypeArgsSchema>;

export const SelectArgsSchema = z.object({
  ref: ElementRefField,
  /** The option value/label to select. */
  value: z.string().min(1),
});
export type SelectArgs = z.infer<typeof SelectArgsSchema>;

export const SubmitArgsSchema = z.object({
  ref: ElementRefField,
});
export type SubmitArgs = z.infer<typeof SubmitArgsSchema>;

export const DownloadArgsSchema = z.object({
  /** The element that initiates the download when clicked. */
  ref: ElementRefField,
});
export type DownloadArgs = z.infer<typeof DownloadArgsSchema>;

// --- Action request / result ---------------------------------------------

export const BrowserActionRequestSchema = z.object({
  version: z.literal(1),
  requestId: z.string().min(1),
  binding: BrowserRunBindingSchema,
  actionClass: z.enum([
    BrowserActionClass.Read,
    BrowserActionClass.Navigate,
    BrowserActionClass.Input,
    BrowserActionClass.Submit,
    BrowserActionClass.Upload,
    BrowserActionClass.Download,
    BrowserActionClass.ExternalEffect,
  ]),
  toolName: z.string().min(1),
  arguments: z.unknown(),
  deadlineMs: z.number().int().positive(),
});
export type BrowserActionRequest = z.infer<typeof BrowserActionRequestSchema>;

export const BrowserActionStatus = {
  Succeeded: "succeeded",
  Denied: "denied",
  Failed: "failed",
  Cancelled: "cancelled",
  OutcomeUnknown: "outcome_unknown",
} as const;
export type BrowserActionStatus =
  (typeof BrowserActionStatus)[keyof typeof BrowserActionStatus];

export const BrowserActionResultSchema = z.object({
  version: z.literal(1),
  requestId: z.string().min(1),
  sessionId: z.string(),
  actionId: z.string(),
  status: z.enum([
    BrowserActionStatus.Succeeded,
    BrowserActionStatus.Denied,
    BrowserActionStatus.Failed,
    BrowserActionStatus.Cancelled,
    BrowserActionStatus.OutcomeUnknown,
  ]),
  currentOrigin: z.string().optional(),
  safeSummary: z.string(),
  artifactRefs: z.array(z.string()).readonly(),
  nextGeneration: z.number().int().nonnegative().optional(),
  errorCode: z.string().optional(),
  /** Bounded snapshot payload for read actions (never contains input values). */
  snapshot: BrowserSnapshotNodeSchema.optional(),
});
export type BrowserActionResult = z.infer<typeof BrowserActionResultSchema>;

// --- Bounds ---------------------------------------------------------------

export const SNAPSHOT_LIMITS = {
  maxDepth: 40,
  maxNodes: 4_000,
  inlinePreviewBytes: 32 * 1024,
  hardMaxBytes: 128 * 1024,
} as const;

export const SCREENSHOT_LIMITS = {
  maxMegapixels: 16,
  maxBytes: 10 * 1024 * 1024,
} as const;

export const DOWNLOAD_LIMITS = {
  /** Default per-download ceiling (PRD §Snapshot/screenshot/download). */
  maxBytes: 100 * 1024 * 1024,
  /** Hard ceiling; a download over this is cancelled and its staging removed. */
  hardMaxBytes: 512 * 1024 * 1024,
} as const;

// --- Origin canonicalization ----------------------------------------------

/**
 * Return the canonical exact origin for `input`, or `null` if it is not an
 * allowable AC8 origin. AC8 rules: scheme MUST be https; host is lowercased and
 * IDNA/punycode-normalized; the default :443 port is stripped and any explicit
 * non-default port is REJECTED; user-info, raw IP literals, wildcards, and
 * empty/single-label hosts are REJECTED. Returns e.g. `https://example.com`.
 */
export function canonicalizeOrigin(input: string): string | null {
  let url: URL;
  try {
    url = new URL(input.trim());
  } catch {
    return null;
  }
  if (url.protocol !== "https:") return null;
  if (url.username !== "" || url.password !== "") return null;
  if (url.pathname !== "" && url.pathname !== "/") return null;
  if (url.search !== "" || url.hash !== "") return null;
  // Explicit non-default port is denied (443 normalizes to empty in URL).
  if (url.port !== "" && url.port !== "443") return null;

  const host = url.hostname.toLowerCase();
  if (host === "") return null;
  // Wildcards / user-info leftovers.
  if (host.includes("*") || host.includes("@")) return null;
  // Raw IPv4 / IPv6 literals are denied as origins.
  if (isIpLiteral(host)) return null;
  // Require at least one dot (no single-label / `.local` bare names).
  if (!host.includes(".")) return null;
  if (host.endsWith(".")) return null; // trailing-dot normalization
  return `https://${host}`;
}

/** True when `host` is a bare IPv4 or bracketed/plain IPv6 literal. */
export function isIpLiteral(host: string): boolean {
  const h =
    host.startsWith("[") && host.endsWith("]") ? host.slice(1, -1) : host;
  // IPv6 contains a colon; a hostname never legitimately does here.
  if (h.includes(":")) return true;
  // Dotted-decimal IPv4 (all numeric labels).
  const labels = h.split(".");
  if (labels.length === 4 && labels.every((l) => /^\d{1,3}$/u.test(l))) {
    return labels.every((l) => Number(l) <= 255);
  }
  // Bare integer / hex / octal IPv4 forms (e.g. 2130706433, 0x7f000001).
  if (/^0x[0-9a-f]+$/iu.test(h)) return true;
  if (/^0[0-7]+$/u.test(h)) return true;
  if (/^\d+$/u.test(h)) return true;
  return false;
}
