// AC8 agentic browser — shared TypeScript/Zod protocol (source of truth).
//
// This module is the single source of truth for the browser capability
// contract. Zod schemas derive runtime validation on the desktop side; the
// AI backend consumes the derived MCP JSON Schemas via `tools/list` and does
// NOT hand-copy these types.
//
// SCOPE: the safe read surface (navigate/snapshot/screenshot/wait/close) plus
// the first exact staged-effect cohort (click/submit). Public read dispatch
// cannot invoke either action; A4/A5 reaches them only through the private
// prepare/apply/reconcile protocol. Type/select/upload/download remain outside
// this production cohort. There is deliberately NO generic eval/JS/selector/
// coordinate/CDP escape hatch.

import { z } from "zod";

// --- Enumerations ---------------------------------------------------------

export const BROWSER_PROTOCOL_VERSION = 1 as const;

/** Stable server name of the desktop-local browser MCP provider. */
export const DESKTOP_BROWSER_SERVER_NAME = "desktop_browser";

/** Broker audience — every action credential is bound to this audience. */
export const BROWSER_BROKER_AUDIENCE = "desktop-browser-broker";

/**
 * Browser build shipped with the pinned root Playwright 1.62.1 dependency.
 * The worker independently reports the launched binary's version and Electron
 * main refuses to expose the broker when it does not match this value.
 *
 * Chromium is unchanged across 1.62.0 -> 1.62.1: both resolve chromium
 * revision 1234 / 151.0.7922.34 in playwright-core's browsers.json, so the
 * staged browser payload does not move with this bump.
 */
export const PINNED_CHROMIUM_VERSION = "151.0.7922.34";
export const PINNED_PLAYWRIGHT_VERSION = "1.62.1";

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

// --- AI broker read request ----------------------------------------------

/**
 * The complete authority the AI process may send to Electron main for a read.
 * It deliberately cannot carry a profile, origin policy, action class,
 * approval, deadline, page handle, or browser-process credential. Main derives
 * those fields from its own policy and emits a full BrowserActionRequest only
 * after reclassifying the named tool.
 */
export const BrowserReadToolCallSchema = z
  .object({
    name: z.string().min(1).max(128),
    arguments: z.unknown(),
  })
  .strict();
export type BrowserReadToolCall = z.infer<typeof BrowserReadToolCallSchema>;

export const BrowserReadBrokerRequestSchema = z
  .object({
    runId: z.string().min(1).max(255),
    workspaceId: z.string().min(1).max(255),
    tool: BrowserReadToolCallSchema,
  })
  .strict();
export type BrowserReadBrokerRequest = z.infer<
  typeof BrowserReadBrokerRequestSchema
>;

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
  /** Generation-bound exact target fingerprint used by a staged plan. */
  fingerprint?: string;
  /** Safe exact-form review facts; absent for non-form/unaddressable nodes. */
  formFingerprint?: string;
  /** SHA-256 of exact successful controls; raw names/values never leave worker. */
  formPayloadDigest?: string;
  formActionUrl?: string;
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  children?: BrowserSnapshotNode[];
}

export const BrowserSnapshotNodeSchema: z.ZodType<BrowserSnapshotNode> = z.lazy(
  () =>
    z.object({
      ref: z.string(),
      role: z.string(),
      name: z.string(),
      fingerprint: z
        .string()
        .regex(/^[a-f0-9]{64}$/u)
        .optional(),
      formFingerprint: z
        .string()
        .regex(/^[a-f0-9]{64}$/u)
        .optional(),
      formPayloadDigest: z
        .string()
        .regex(/^[a-f0-9]{64}$/u)
        .optional(),
      formActionUrl: z
        .string()
        .url()
        .refine((value) => {
          const parsed = new URL(value);
          return (
            parsed.protocol === "https:" &&
            parsed.username === "" &&
            parsed.password === "" &&
            parsed.search === "" &&
            parsed.hash === ""
          );
        })
        .optional(),
      method: z.enum(["GET", "POST", "PUT", "PATCH", "DELETE"]).optional(),
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
  /** Opaque exact-plan facts; present only after a successful live-page read. */
  sessionRef: z.lazy(() => OpaqueBrowserRefSchema).optional(),
  pageRef: z.lazy(() => OpaqueBrowserRefSchema).optional(),
  topLevelOrigin: CanonicalOriginSchema.optional(),
  pageGeneration: z.number().int().nonnegative().optional(),
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

// --- Private staged-effect protocol ---------------------------------------
//
// These values travel only over Electron-main's private worker bridge. They
// are intentionally NOT broker tool arguments: no remote service receives a
// cookie, a browser-process credential, a selector, a host path, or an ability
// to invoke a generic effect action.

const SHA256_HEX = z.string().regex(/^[a-f0-9]{64}$/u);
export const OpaqueBrowserRefSchema = z
  .string()
  .min(1)
  .max(2048)
  .refine(
    (value) => {
      if (
        value.startsWith("/") ||
        value.startsWith("~") ||
        value.startsWith("\\") ||
        /^file:/iu.test(value) ||
        /^https?:/iu.test(value) ||
        /^data:/iu.test(value) ||
        /(?:^|[\\/])\.\.?([\\/]|$)/u.test(value)
      )
        return false;
      try {
        const parsed = new URL(value);
        return (
          parsed.protocol !== "file:" &&
          parsed.protocol !== "http:" &&
          parsed.protocol !== "https:" &&
          parsed.protocol !== "data:" &&
          parsed.hostname !== "" &&
          parsed.search === "" &&
          parsed.hash === ""
        );
      } catch {
        return false;
      }
    },
    { message: "browser reference must be opaque and scoped" },
  );

export const BrowserEffectActionKind = {
  Click: "click",
  Input: "input",
  Select: "select",
  Submit: "submit",
  UploadSubmit: "upload_submit",
} as const;
export type BrowserEffectActionKind =
  (typeof BrowserEffectActionKind)[keyof typeof BrowserEffectActionKind];

export const BrowserPreconditionSchema = z.object({
  pageGeneration: z.number().int().nonnegative(),
  origin: CanonicalOriginSchema,
  elementFingerprint: SHA256_HEX.optional(),
  formFingerprint: SHA256_HEX.optional(),
  formPayloadDigest: SHA256_HEX.optional(),
});
export type BrowserPrecondition = z.infer<typeof BrowserPreconditionSchema>;

/** Immutable A2 revision metadata authorized for a staged upload. */
export const BrowserUploadArtifactSchema = z.object({
  artifactRef: OpaqueBrowserRefSchema.refine(
    (value) => value.startsWith("artifact://"),
    { message: "upload source must be an artifact revision" },
  ),
  digest: SHA256_HEX,
  byteSize: z.number().int().nonnegative(),
  mediaType: z.string().min(1).max(255),
  suggestedFilename: z
    .string()
    .min(1)
    .max(255)
    .refine(
      (value) =>
        value.trim() === value &&
        value !== "." &&
        value !== ".." &&
        !/[\\/\u0000]/u.test(value),
      { message: "upload filename must be safe metadata" },
    ),
});
export type BrowserUploadArtifact = z.infer<typeof BrowserUploadArtifactSchema>;

export const BrowserActionPlanSchema = z
  .object({
    sessionRef: OpaqueBrowserRefSchema,
    pageRef: OpaqueBrowserRefSchema,
    origin: CanonicalOriginSchema,
    topLevelOrigin: CanonicalOriginSchema,
    actionKind: z.enum([
      BrowserEffectActionKind.Click,
      BrowserEffectActionKind.Input,
      BrowserEffectActionKind.Select,
      BrowserEffectActionKind.Submit,
      BrowserEffectActionKind.UploadSubmit,
    ]),
    elementRef: z.string().min(1).max(255).optional(),
    elementFingerprint: SHA256_HEX.optional(),
    formFingerprint: SHA256_HEX.optional(),
    formPayloadDigest: SHA256_HEX.optional(),
    formActionUrl: z
      .string()
      .url()
      .max(2048)
      .refine((value) => {
        const parsed = new URL(value);
        return (
          parsed.protocol === "https:" &&
          parsed.username === "" &&
          parsed.password === "" &&
          parsed.search === "" &&
          parsed.hash === ""
        );
      })
      .optional(),
    method: z.enum(["GET", "POST", "PUT", "PATCH", "DELETE"]).optional(),
    canonicalFieldsRef: OpaqueBrowserRefSchema,
    fieldsDigest: SHA256_HEX,
    uploadArtifactRefs: z.array(OpaqueBrowserRefSchema).max(32).readonly(),
    uploadArtifacts: z.array(BrowserUploadArtifactSchema).max(32).readonly(),
    precondition: BrowserPreconditionSchema,
    preconditionDigest: SHA256_HEX,
    userVisibleSummary: z.string().min(1).max(512),
  })
  .superRefine((plan, ctx) => {
    const requiresElement = true;
    if (
      requiresElement &&
      (plan.elementRef === undefined || plan.elementFingerprint === undefined)
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "browser action requires exact element identity",
      });
    }
    const authorizedRefs = plan.uploadArtifacts.map(
      (upload) => upload.artifactRef,
    );
    if (
      plan.uploadArtifactRefs.length !== authorizedRefs.length ||
      plan.uploadArtifactRefs.some(
        (ref, index) => ref !== authorizedRefs[index],
      )
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "upload authorization must bind every exact artifact revision",
      });
    }
    if (
      plan.actionKind === BrowserEffectActionKind.UploadSubmit &&
      authorizedRefs.length === 0
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "upload_submit requires an artifact revision",
      });
    }
    if (plan.precondition.origin !== plan.origin) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "precondition origin must match plan origin",
      });
    }
    if (plan.precondition.elementFingerprint !== plan.elementFingerprint) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "precondition fingerprint must match action element",
      });
    }
    if (plan.precondition.formFingerprint !== plan.formFingerprint) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "precondition fingerprint must match action form",
      });
    }
    if (plan.precondition.formPayloadDigest !== plan.formPayloadDigest) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "precondition payload digest must match action form",
      });
    }
    const formIdentity = [
      plan.formFingerprint,
      plan.formPayloadDigest,
      plan.formActionUrl,
      plan.method,
    ];
    const formIdentityCount = formIdentity.filter(
      (value) => value !== undefined,
    ).length;
    if (formIdentityCount !== 0 && formIdentityCount !== formIdentity.length) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "browser form identity must be complete or absent",
      });
    }
    if (
      (plan.actionKind === BrowserEffectActionKind.Submit ||
        plan.actionKind === BrowserEffectActionKind.UploadSubmit) &&
      (plan.formFingerprint === undefined ||
        plan.formPayloadDigest === undefined ||
        plan.formActionUrl === undefined ||
        plan.method === undefined)
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "browser submission requires exact form identity",
      });
    }
  });
export type BrowserActionPlan = z.infer<typeof BrowserActionPlanSchema>;

export const BrowserPrepareResultSchema = z
  .object({
    preparedRef: OpaqueBrowserRefSchema.optional(),
    observedPreconditionDigest: SHA256_HEX,
    expiresAt: z.string().max(64).optional(),
    preconditionDrift: z.boolean(),
  })
  .superRefine((value, ctx) => {
    if (value.preconditionDrift === (value.preparedRef !== undefined)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "prepared action must be either prepared or drifted",
      });
    }
  });
export type BrowserPrepareResult = z.infer<typeof BrowserPrepareResultSchema>;

export const BrowserEffectOutcome = {
  Applied: "applied",
  PreconditionDrift: "precondition_drift",
  Failed: "failed",
  Indeterminate: "indeterminate",
} as const;
export type BrowserEffectOutcome =
  (typeof BrowserEffectOutcome)[keyof typeof BrowserEffectOutcome];

export const BrowserEffectReceiptSchema = z.object({
  outcome: z.enum([
    BrowserEffectOutcome.Applied,
    BrowserEffectOutcome.PreconditionDrift,
    BrowserEffectOutcome.Failed,
    BrowserEffectOutcome.Indeterminate,
  ]),
  receiptRef: OpaqueBrowserRefSchema.optional(),
  resultDigest: SHA256_HEX.optional(),
  safeMessage: z.string().max(512).optional(),
});
export type BrowserEffectReceipt = z.infer<typeof BrowserEffectReceiptSchema>;

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
