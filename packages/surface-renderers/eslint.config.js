import tsParser from "@typescript-eslint/parser";
import globals from "globals";

// surface-renderers is substrate-agnostic — it mounts inside the
// chat-surface's TcSurfaceMount (which itself can be hosted in any
// substrate). Same boundary as chat-surface: no bare browser primitives,
// no host-app imports.
//
// Additional rule (beyond chat-surface): surface-renderers MUST NOT import
// from chat-surface/src/shell — renderers are leaf components, the shell
// is the host scaffolding. Importing the other direction would re-couple
// the substrate-independent renderer to the layout layer.

const BOUNDARY_MESSAGE_GLOBALS =
  "surface-renderers is substrate-agnostic. Browser primitives belong behind a Transport / KeyValueStore / etc. port implemented by the host substrate.";

const BOUNDARY_MESSAGE_APP_IMPORT =
  "surface-renderers cannot import from the host app. Add a port to chat-surface and let the host implement it.";

const BOUNDARY_MESSAGE_SHELL_IMPORT =
  "surface-renderers cannot import from chat-surface/shell. Renderers are leaves; the shell is the host scaffolding.";

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
        { name: "crypto", message: BOUNDARY_MESSAGE_GLOBALS },
      ],
      // TODO(Phase 4-a): remove this allowance. The EmailRenderer currently
      // imports Transport as part of the deprecated SurfaceRendererProps
      // flow from spike-prep. Once Phase 4-a migrates EmailRenderer to the
      // SaaSRendererAdapter contract (pure render of state — no transport),
      // this exception goes away and the boundary becomes strict.
      //
      // Concretely: when Phase 4-a lands, add the following entry to the
      // `patterns` array below and remove every chat-transport import from
      // packages/surface-renderers/src/**:
      //   {
      //     group: [
      //       "@enterprise-search/chat-transport",
      //       "@enterprise-search/chat-transport/*",
      //     ],
      //     message:
      //       "surface-renderers cannot import Transport. Adapters are pure render of state; the host calls transport. (D28)",
      //   }
      "no-restricted-imports": [
        "error",
        {
          patterns: [
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
          ],
        },
      ],
    },
  },
];
