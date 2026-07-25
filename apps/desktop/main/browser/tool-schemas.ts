// AC8 agentic browser — MCP tool schemas (tools/list payload).
//
// Hand-authored JSON Schemas that MIRROR the Zod argument schemas in
// `protocol.ts` (kept dependency-free — no zod-to-json-schema at runtime). The
// desktop-local browser MCP provider on the AI backend discovers these via
// `tools/list`; it does NOT hand-copy them.
//
// `BROWSER_TOOL_SCHEMAS` is the READ-ONLY surface (always advertised).
// `BROWSER_ACTION_TOOL_SCHEMAS` adds only the exact-plan action cohort
// (click/submit). It is advertised ONLY when the worker is composed with the
// private prepare/apply/reconcile authority and the AI side can stage the
// complete plan (`browserToolSchemas({ includeActions })`).

import { BrowserToolName } from "./protocol";

export interface BrowserToolSchema {
  readonly name: string;
  readonly description: string;
  readonly inputSchema: Record<string, unknown>;
}

const OBJECT = "object";
const STRING = "string";
const INTEGER = "integer";
const BOOLEAN = "boolean";

export const BROWSER_TOOL_SCHEMAS: readonly BrowserToolSchema[] = [
  {
    name: BrowserToolName.Navigate,
    description:
      "Navigate the isolated browser to an approved HTTPS origin. Returns the " +
      "resulting origin and status. Off-policy origins are denied.",
    inputSchema: {
      type: OBJECT,
      properties: {
        url: { type: STRING, description: "Approved HTTPS URL to open." },
      },
      required: ["url"],
      additionalProperties: false,
    },
  },
  {
    name: BrowserToolName.Snapshot,
    description:
      "Capture a bounded accessibility snapshot of the current page. Input " +
      "values, passwords, and hidden fields are never included. Element refs " +
      "are generation-bound and go stale after any navigation.",
    inputSchema: {
      type: OBJECT,
      properties: {
        ref: { type: STRING, description: "Optional element ref to scope to." },
        depth: { type: INTEGER, minimum: 1, description: "Depth bound." },
      },
      additionalProperties: false,
    },
  },
  {
    name: BrowserToolName.Wait,
    description: "Wait for a bounded page condition before the next read.",
    inputSchema: {
      type: OBJECT,
      properties: {
        condition: { type: STRING, enum: ["load", "networkidle", "timeout"] },
        timeoutMs: { type: INTEGER, minimum: 1, maximum: 30000 },
      },
      required: ["condition"],
      additionalProperties: false,
    },
  },
  {
    name: BrowserToolName.Screenshot,
    description:
      "Capture a screenshot of the current page. Input fields are masked by " +
      "default. The image is stored by reference; it is never inlined.",
    inputSchema: {
      type: OBJECT,
      properties: {
        fullPage: { type: BOOLEAN },
        redact: {
          type: BOOLEAN,
          description: "Mask input fields (default true).",
        },
      },
      additionalProperties: false,
    },
  },
  {
    name: BrowserToolName.Close,
    description: "Close the browser session and clean its staging area.",
    inputSchema: { type: OBJECT, properties: {}, additionalProperties: false },
  },
];

const ELEMENT_REF_PROPERTY = {
  type: STRING,
  description:
    "Generation-bound element ref from the latest snapshot. Goes stale after " +
    "any navigation or DOM-mutating action.",
} as const;

const SHA256_PROPERTY = {
  type: STRING,
  pattern: "^[a-f0-9]{64}$",
} as const;

const EXACT_ACTION_PROPERTIES = {
  sessionRef: {
    type: STRING,
    description: "Opaque session ref returned by the latest browser read.",
  },
  pageRef: {
    type: STRING,
    description: "Opaque page ref returned by the latest browser read.",
  },
  origin: {
    type: STRING,
    description:
      "Exact current HTTPS origin returned by the latest browser read.",
  },
  topLevelOrigin: {
    type: STRING,
    description: "Exact top-level HTTPS origin shown during review.",
  },
  elementRef: ELEMENT_REF_PROPERTY,
  elementFingerprint: {
    ...SHA256_PROPERTY,
    description: "Exact fingerprint returned with the reviewed snapshot node.",
  },
  pageGeneration: {
    type: INTEGER,
    minimum: 0,
    description: "Generation returned by the latest browser snapshot.",
  },
  formFingerprint: {
    ...SHA256_PROPERTY,
    description: "Optional exact form fingerprint when one was observed.",
  },
  formPayloadDigest: {
    ...SHA256_PROPERTY,
    description:
      "Optional worker-produced digest of the reviewed successful form controls.",
  },
  formActionUrl: {
    type: STRING,
    description: "Optional reviewed HTTPS form action URL.",
  },
  method: {
    type: STRING,
    enum: ["GET", "POST", "PUT", "PATCH", "DELETE"],
  },
} as const;

const EXACT_ACTION_REQUIRED = [
  "sessionRef",
  "pageRef",
  "origin",
  "topLevelOrigin",
  "elementRef",
  "elementFingerprint",
  "pageGeneration",
] as const;

/**
 * The first production side-effect cohort. These schemas carry only opaque
 * identity and review facts from a prior snapshot. They cannot dispatch via
 * the read broker; the AI runtime turns them into a `browser_submission`
 * proposal, and A5 later invokes the private exact-plan bridge.
 */
export const BROWSER_ACTION_TOOL_SCHEMAS: readonly BrowserToolSchema[] = [
  {
    name: BrowserToolName.Click,
    description:
      "Propose an exact click from the latest browser snapshot. A click may " +
      "submit, purchase, delete, or navigate, so it is always held for review.",
    inputSchema: {
      type: OBJECT,
      properties: EXACT_ACTION_PROPERTIES,
      required: EXACT_ACTION_REQUIRED,
      additionalProperties: false,
    },
  },
  {
    name: BrowserToolName.Submit,
    description:
      "Propose an exact form submission from the latest browser snapshot. It " +
      "is always held for review and an uncertain outcome is never retried.",
    inputSchema: {
      type: OBJECT,
      properties: EXACT_ACTION_PROPERTIES,
      required: [
        ...EXACT_ACTION_REQUIRED,
        "formFingerprint",
        "formPayloadDigest",
        "formActionUrl",
        "method",
      ],
      additionalProperties: false,
    },
  },
];

/**
 * The tool set to advertise. Read-only by default; the action layer is included
 * ONLY when the worker is composed with the private exact-plan effect
 * authority; public read dispatch cannot execute them.
 */
export function browserToolSchemas(opts?: {
  includeActions?: boolean;
}): readonly BrowserToolSchema[] {
  if (opts?.includeActions === true) {
    return [...BROWSER_TOOL_SCHEMAS, ...BROWSER_ACTION_TOOL_SCHEMAS];
  }
  return BROWSER_TOOL_SCHEMAS;
}
