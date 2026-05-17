import tsParser from "@typescript-eslint/parser";
import globals from "globals";

// Substrate-portability enforcement for @enterprise-search/chat-surface.
//
// This package is mounted in two substrates today (web via apps/frontend,
// desktop webview via apps/desktop — Phase 2). Anything in here that
// directly references a browser primitive (`window.*`, `document.*`,
// `localStorage`, `fetch`, …) silently breaks portability — at best it
// throws inside the webview; at worst it appears to work, then drifts
// behavior between substrates.
//
// Substrate-specific work belongs in one of two places:
//   - A port defined here (Transport / Router / KeyValueStore /
//     PresenceSignal) that the host substrate implements
//   - A consumer of that port inside the host substrate
//     (apps/frontend or apps/desktop)
//
// Architecture context:
//   docs/architecture/desktop-app.md
//   docs/architecture/desktop-app-rollout.md §3, §E3
//
// The one allowed substrate touchpoint inside this package is the web
// reference implementation of KeyValueStore — it uses
// `globalThis.localStorage` (member access, not a banned global), which
// is honest substrate code marked by the deliberate `globalThis.`
// prefix. Adding more substrate-bound classes here should be rare; when
// it is necessary, prefer `globalThis.X` over `window.X` so the intent
// reads as "I know I'm touching the substrate."

const BOUNDARY_MESSAGE_GLOBALS =
  "chat-surface is substrate-agnostic. Browser primitives belong behind a port (Transport / KeyValueStore / etc.) implemented by the host substrate, not in this package.";

const BOUNDARY_MESSAGE_APP_IMPORT =
  "chat-surface cannot import from the host app. Add a port here and let the host implement it instead.";

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

  // The substrate boundary itself.
  {
    files: ["src/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-globals": [
        "error",
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
      ],
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: [
                "@enterprise-search/frontend",
                "@enterprise-search/frontend/*",
                "apps/*",
                "**/apps/frontend/*",
              ],
              message: BOUNDARY_MESSAGE_APP_IMPORT,
            },
          ],
        },
      ],
    },
  },
];
