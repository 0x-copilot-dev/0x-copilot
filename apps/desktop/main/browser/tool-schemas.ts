// AC8 agentic browser — MCP tool schemas (tools/list payload).
//
// Hand-authored JSON Schemas that MIRROR the Zod argument schemas in
// `protocol.ts` (kept dependency-free — no zod-to-json-schema at runtime). The
// desktop-local browser MCP provider on the AI backend discovers these via
// `tools/list`; it does NOT hand-copy them.
//
// `BROWSER_TOOL_SCHEMAS` is the READ-ONLY surface (always advertised).
// `BROWSER_ACTION_TOOL_SCHEMAS` adds the side-effecting action layer
// (click/type/select/submit/download); it is advertised ONLY when the worker is
// composed with an approval authority (`browserToolSchemas({ includeActions })`).

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

const REF_PROPERTY = {
  type: STRING,
  description:
    "Generation-bound element ref from the latest snapshot. Goes stale after " +
    "any navigation or DOM-mutating action.",
} as const;

/**
 * The side-effecting action layer. Every one of these requires clearing a
 * per-action approval before it dispatches (PRD §Action policy and approvals).
 */
export const BROWSER_ACTION_TOOL_SCHEMAS: readonly BrowserToolSchema[] = [
  {
    name: BrowserToolName.Click,
    description:
      "Click an element by ref. Treated as a side effect (may submit, " +
      "navigate, or download); requires approval before it runs.",
    inputSchema: {
      type: OBJECT,
      properties: { ref: REF_PROPERTY },
      required: ["ref"],
      additionalProperties: false,
    },
  },
  {
    name: BrowserToolName.Type,
    description:
      "Type non-secret text into a reviewed field by ref. Secret fields " +
      "(password/MFA/etc.) force user takeover; requires approval.",
    inputSchema: {
      type: OBJECT,
      properties: {
        ref: REF_PROPERTY,
        text: { type: STRING, description: "Non-secret text to enter." },
      },
      required: ["ref", "text"],
      additionalProperties: false,
    },
  },
  {
    name: BrowserToolName.Select,
    description:
      "Select an option in a listbox/select by ref; requires approval.",
    inputSchema: {
      type: OBJECT,
      properties: {
        ref: REF_PROPERTY,
        value: { type: STRING, description: "Option value or label." },
      },
      required: ["ref", "value"],
      additionalProperties: false,
    },
  },
  {
    name: BrowserToolName.Submit,
    description:
      "Submit a form / activate a send control by ref. High risk; always " +
      "requires approval and never auto-retries an unknown outcome.",
    inputSchema: {
      type: OBJECT,
      properties: { ref: REF_PROPERTY },
      required: ["ref"],
      additionalProperties: false,
    },
  },
  {
    name: BrowserToolName.Download,
    description:
      "Initiate a download by clicking an element ref. Bytes are captured to " +
      "the run staging area by reference (never a host path); executable-shaped " +
      "or oversized content is denied. Requires approval.",
    inputSchema: {
      type: OBJECT,
      properties: { ref: REF_PROPERTY },
      required: ["ref"],
      additionalProperties: false,
    },
  },
];

/**
 * The tool set to advertise. Read-only by default; the action layer is included
 * ONLY when the worker is composed with an approval authority.
 */
export function browserToolSchemas(opts?: {
  includeActions?: boolean;
}): readonly BrowserToolSchema[] {
  if (opts?.includeActions === true) {
    return [...BROWSER_TOOL_SCHEMAS, ...BROWSER_ACTION_TOOL_SCHEMAS];
  }
  return BROWSER_TOOL_SCHEMAS;
}
