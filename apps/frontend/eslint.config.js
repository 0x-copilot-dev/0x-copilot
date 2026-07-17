import tsParser from "@typescript-eslint/parser";
import globals from "globals";

// Substrate-boundary enforcement for apps/frontend.
//
// The Transport port (packages/chat-transport) is the seam between the
// chat surface and any HTTP/SSE substrate (browser fetch today, VS Code
// extension RPC for the desktop app). Features must not couple to the
// web substrate — they call typed api modules (agentApi, mcpApi, …),
// which call Transport, which is implemented per substrate.
//
// The lint rule below pins that contract. Without it, the next regression
// is inevitable. Architecture context:
//   docs/architecture/desktop-app.md
//   docs/plan/desktop/PRD.md §3.2, §6.5

const BOUNDARY_MESSAGE_GLOBALS =
  "features must call typed api modules under src/api/*; the substrate primitive (fetch/SSE/XHR) lives behind the Transport port, not in features.";

const BOUNDARY_MESSAGE_HTTP =
  "features must call typed api modules (agentApi, mcpApi, …); HTTP/Transport plumbing lives behind src/api/*.";

const BOUNDARY_MESSAGE_TRANSPORT =
  "features must not import the Transport package directly — go through a typed api module under src/api/*.";

export default [
  // Common TS/TSX parser config for everything under src/.
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
    linterOptions: {
      // The codebase carries pre-existing `eslint-disable-next-line
      // react-hooks/exhaustive-deps` (and similar) directives from a
      // prior lint setup whose plugins are no longer installed. They are
      // still useful as hints if the plugins are ever re-added, so we
      // don't strip them — and we don't want ESLint flagging them as
      // errors against the boundary-only setup either.
      reportUnusedDisableDirectives: "off",
    },
  },

  // The substrate boundary. Applies to feature code only.
  {
    files: ["src/features/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-globals": [
        "error",
        { name: "fetch", message: BOUNDARY_MESSAGE_GLOBALS },
        { name: "EventSource", message: BOUNDARY_MESSAGE_GLOBALS },
        { name: "XMLHttpRequest", message: BOUNDARY_MESSAGE_GLOBALS },
        { name: "WebSocket", message: BOUNDARY_MESSAGE_GLOBALS },
      ],
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: [
                "**/api/http",
                "**/api/transport",
                "../api/http",
                "../api/transport",
                "../../api/http",
                "../../api/transport",
                "../../../api/http",
                "../../../api/transport",
              ],
              message: BOUNDARY_MESSAGE_HTTP,
            },
            {
              group: [
                "@0x-copilot/chat-transport",
                "@0x-copilot/chat-transport/*",
              ],
              message: BOUNDARY_MESSAGE_TRANSPORT,
            },
          ],
        },
      ],
    },
  },

  // Tests can drive whatever they need to drive — including direct
  // Transport / http.ts imports for setup — so the boundary rule is off
  // here. Production code paths are still pinned.
  {
    files: ["src/features/**/*.{test,spec}.{ts,tsx}"],
    rules: {
      "no-restricted-globals": "off",
      "no-restricted-imports": "off",
    },
  },

  // AuthContext is the one feature-side configurator of the substrate
  // boundary: it wires the bearer provider and the 401 handler into the
  // Transport singleton. Importing the transport config API from here is
  // not a violation — it's the entire job of this file. devIdp.ts is the
  // local dev IdP minting flow, explicitly web-substrate-only by design;
  // the desktop substrate uses real OIDC (architecture spec §8). Both
  // files are exempt for those reasons, not as ad-hoc escape hatches.
  {
    files: ["src/features/auth/AuthContext.tsx", "src/features/auth/devIdp.ts"],
    rules: {
      "no-restricted-globals": "off",
      "no-restricted-imports": "off",
    },
  },
];
