import tsParser from "@typescript-eslint/parser";
import globals from "globals";

// surface-renderers is substrate-agnostic — adapters render state to JSX
// and nothing else (PRD D28). They mount inside chat-surface's
// TcSurfaceMount, which itself can be hosted in any substrate.
//
// Lint enforces D28 for tier-1 at developer-feedback time. Tier-2's
// enforcement is the install-time AST allowlist scanner shipping in
// Phase 6D. The two layers are intentionally separate: this lint rule
// gives renderer authors immediate feedback; the AST scanner is the
// safety gate for agent-generated code.
//
// ALLOWED IMPORTS (static or dynamic) for tier-1 renderers under
// packages/surface-renderers/src/**:
//   - react
//   - react-dom
//   - @enterprise-search/design-system
//   - @enterprise-search/chat-surface   (design tokens, TcInlineDiff)
//
// Specific bans (in addition to the existing chat-surface boundary set):
//   - Transport: @enterprise-search/chat-transport is forbidden. Adapters
//     do not call Transport; the host (TcSurfaceMount) owns I/O.
//   - Browser globals: window, document, history, navigator, location,
//     localStorage, sessionStorage, fetch, EventSource, XMLHttpRequest,
//     WebSocket, crypto, clipboard.
//   - Member-expression bans: document.cookie, navigator.clipboard.write*.
//   - Dynamic import() of any specifier outside the allowlist above.
//
// Migration carve-out: src/email/** retains the chat-transport import
// allowance because the spike-prep EmailRenderer still consumes the
// deprecated SurfaceRendererProps shape. Phase 4-C migrates EmailRenderer
// onto the SaaSRendererAdapter contract and removes that carve-out.

const BOUNDARY_MESSAGE_GLOBALS =
  "surface-renderers is substrate-agnostic and pure-render (D28). Browser primitives belong behind a Transport / KeyValueStore / etc. port implemented by the host substrate.";

const BOUNDARY_MESSAGE_APP_IMPORT =
  "surface-renderers cannot import from the host app. Add a port to chat-surface and let the host implement it.";

const BOUNDARY_MESSAGE_SHELL_IMPORT =
  "surface-renderers cannot import from chat-surface/shell. Renderers are leaves; the shell is the host scaffolding.";

const BOUNDARY_MESSAGE_TRANSPORT_IMPORT =
  "surface-renderers cannot import Transport. Adapters are pure render of state; the host calls transport (PRD D28).";

const BOUNDARY_MESSAGE_DOC_COOKIE =
  "document.cookie is a banned escape hatch in surface-renderers (D28). Cookies belong behind the Transport port.";

const BOUNDARY_MESSAGE_CLIPBOARD_WRITE =
  "navigator.clipboard.write* is a banned side-effect in surface-renderers (D28). Clipboard writes belong to host-owned actions, not adapter render.";

const BOUNDARY_MESSAGE_DYNAMIC_IMPORT =
  "Dynamic import() is restricted in surface-renderers to react / react-dom / @enterprise-search/design-system / @enterprise-search/chat-surface (PRD §9.5, D29 import allowlist).";

const RESTRICTED_GLOBALS = [
  { name: "window", message: BOUNDARY_MESSAGE_GLOBALS },
  { name: "document", message: BOUNDARY_MESSAGE_GLOBALS },
  { name: "history", message: BOUNDARY_MESSAGE_GLOBALS },
  { name: "navigator", message: BOUNDARY_MESSAGE_GLOBALS },
  { name: "location", message: BOUNDARY_MESSAGE_GLOBALS },
  { name: "localStorage", message: BOUNDARY_MESSAGE_GLOBALS },
  { name: "sessionStorage", message: BOUNDARY_MESSAGE_GLOBALS },
  { name: "fetch", message: BOUNDARY_MESSAGE_GLOBALS },
  { name: "EventSource", message: BOUNDARY_MESSAGE_GLOBALS },
  { name: "XMLHttpRequest", message: BOUNDARY_MESSAGE_GLOBALS },
  { name: "WebSocket", message: BOUNDARY_MESSAGE_GLOBALS },
  { name: "crypto", message: BOUNDARY_MESSAGE_GLOBALS },
  { name: "clipboard", message: BOUNDARY_MESSAGE_GLOBALS },
];

const APP_IMPORT_PATTERNS = [
  {
    group: [
      "@enterprise-search/frontend",
      "@enterprise-search/frontend/*",
      "@enterprise-search/desktop",
      "@enterprise-search/desktop/*",
      "apps/*",
      "**/apps/*",
    ],
    message: BOUNDARY_MESSAGE_APP_IMPORT,
  },
  {
    group: [
      "@enterprise-search/chat-surface/shell",
      "@enterprise-search/chat-surface/shell/*",
      "@enterprise-search/chat-surface/src/shell",
      "@enterprise-search/chat-surface/src/shell/*",
    ],
    message: BOUNDARY_MESSAGE_SHELL_IMPORT,
  },
];

const TRANSPORT_IMPORT_PATTERN = {
  group: [
    "@enterprise-search/chat-transport",
    "@enterprise-search/chat-transport/*",
  ],
  message: BOUNDARY_MESSAGE_TRANSPORT_IMPORT,
};

// AST selectors for property-access and dynamic-import bans. We accept the
// false-negative on aliased / destructured access (e.g. `const c = document;
// c.cookie`) — the same false-negative applies to no-restricted-globals
// and is good enough for tier-1. Tier-2's AST scanner closes the gap.
const DOC_COOKIE_SELECTOR =
  "MemberExpression[object.name='document'][property.name='cookie']";
const CLIPBOARD_WRITE_SELECTOR =
  "MemberExpression[object.object.name='navigator'][object.property.name='clipboard'][property.name=/^write/]";
const DYNAMIC_IMPORT_NON_LITERAL_SELECTOR =
  "ImportExpression:not(:has(Literal))";
const DYNAMIC_IMPORT_DISALLOWED_SELECTOR =
  "ImportExpression > Literal[value!='react'][value!='react-dom'][value!='@enterprise-search/design-system'][value!='@enterprise-search/chat-surface']";

const RESTRICTED_SYNTAX = [
  { selector: DOC_COOKIE_SELECTOR, message: BOUNDARY_MESSAGE_DOC_COOKIE },
  {
    selector: CLIPBOARD_WRITE_SELECTOR,
    message: BOUNDARY_MESSAGE_CLIPBOARD_WRITE,
  },
  {
    selector: DYNAMIC_IMPORT_NON_LITERAL_SELECTOR,
    message: BOUNDARY_MESSAGE_DYNAMIC_IMPORT,
  },
  {
    selector: DYNAMIC_IMPORT_DISALLOWED_SELECTOR,
    message: BOUNDARY_MESSAGE_DYNAMIC_IMPORT,
  },
];

export default [
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        ...globals.browser,
        ...globals.es2022,
      },
    },
  },

  // Default tier-1 rules: D28 / D29 enforcement, full Transport ban.
  {
    files: ["src/**/*.{ts,tsx}"],
    ignores: ["src/email/**", "src/__lint-negatives__/**"],
    rules: {
      "no-restricted-globals": ["error", ...RESTRICTED_GLOBALS],
      "no-restricted-imports": [
        "error",
        {
          patterns: [...APP_IMPORT_PATTERNS, TRANSPORT_IMPORT_PATTERN],
        },
      ],
      "no-restricted-syntax": ["error", ...RESTRICTED_SYNTAX],
    },
  },

  // Carve-out for src/email/**: the spike-prep EmailRenderer still uses
  // the deprecated SurfaceRendererProps shape that takes a Transport.
  // Phase 4-C migrates it onto SaaSRendererAdapter and deletes this block.
  {
    files: ["src/email/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-globals": ["error", ...RESTRICTED_GLOBALS],
      "no-restricted-imports": [
        "error",
        {
          patterns: APP_IMPORT_PATTERNS,
        },
      ],
      "no-restricted-syntax": ["error", ...RESTRICTED_SYNTAX],
    },
  },

  // Lint negatives: these files deliberately violate the rules and are
  // exercised via `npm run lint:negatives`. They must NOT be ignored from
  // ESLint discovery — running lint on them is the assertion.
  {
    files: ["src/__lint-negatives__/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-globals": ["error", ...RESTRICTED_GLOBALS],
      "no-restricted-imports": [
        "error",
        {
          patterns: [...APP_IMPORT_PATTERNS, TRANSPORT_IMPORT_PATTERN],
        },
      ],
      "no-restricted-syntax": ["error", ...RESTRICTED_SYNTAX],
    },
  },
];
