// AC8 agentic browser — read-only MCP tool schemas (tools/list payload).
//
// Hand-authored JSON Schemas that MIRROR the Zod argument schemas in
// `protocol.ts` (kept dependency-free — no zod-to-json-schema at runtime). The
// desktop-local browser MCP provider on the AI backend discovers these via
// `tools/list`; it does NOT hand-copy them. ONLY the read-only tools are
// listed — side-effecting tools are deferred and deliberately absent.

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
