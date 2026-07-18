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
 * The action classes the policy layer can reason about. The read-only classes
 * (`read`, `navigate`) are LIVE in this foundation. The remaining classes are
 * DEFERRED — named so the egress/approval policy is total, but no MCP tool
 * dispatches them yet.
 */
export const BrowserActionClass = {
  Read: "read",
  Navigate: "navigate",
  Input: "input", // deferred
  Submit: "submit", // deferred
  Upload: "upload", // deferred
  Download: "download", // deferred
  ExternalEffect: "external_effect", // deferred
} as const;
export type BrowserActionClass =
  (typeof BrowserActionClass)[keyof typeof BrowserActionClass];

/** Action classes that dispatch in the read-only foundation. */
export const LIVE_ACTION_CLASSES: ReadonlySet<BrowserActionClass> = new Set([
  BrowserActionClass.Read,
  BrowserActionClass.Navigate,
]);

/** Read-only MCP tool names exposed by the desktop browser server. */
export const BrowserToolName = {
  Navigate: "browser_navigate",
  Snapshot: "browser_snapshot",
  Wait: "browser_wait",
  Screenshot: "browser_screenshot",
  Close: "browser_close",
} as const;
export type BrowserToolName =
  (typeof BrowserToolName)[keyof typeof BrowserToolName];

/**
 * Tool names that are DEFERRED in this foundation. They are enumerated so the
 * provider can assert they are NOT advertised, and so a later slice can wire
 * them without re-deciding the contract.
 */
export const DEFERRED_TOOL_NAMES: readonly string[] = [
  "browser_click",
  "browser_type",
  "browser_select",
  "browser_submit",
  "browser_download",
  "browser_upload",
];

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
