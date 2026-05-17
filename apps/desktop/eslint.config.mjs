import tsParser from "@typescript-eslint/parser";
import globals from "globals";

// Per-process boundary rules for the Electron desktop app.
//
// main/    Node + Electron main process. No DOM, no browser globals.
//          Cannot reach into apps/* siblings or apps/frontend.
// preload/ Node + Electron preload. DOM globals exist because the
//          preload script runs in a sandboxed renderer context that
//          has access to contextBridge / ipcRenderer.
// renderer/Browser only. No Node, no Electron — the only escape hatch
//          is window.bridge (preload exposed via contextBridge). fetch /
//          XMLHttpRequest / EventSource / WebSocket are banned to fail
//          fast at lint time, before CSP catches them at run time.

const APP_BOUNDARY_MESSAGE =
  "@enterprise-search/desktop is its own deployable. It cannot import from another app.";

const FORBIDDEN_SIBLING_APPS = {
  patterns: [
    {
      group: [
        "@enterprise-search/frontend",
        "@enterprise-search/frontend/*",
        "../../frontend",
        "../../frontend/*",
      ],
      message: APP_BOUNDARY_MESSAGE,
    },
  ],
};

export default [
  {
    ignores: ["out/**", "dist/**", "node_modules/**"],
  },
  {
    files: ["main/**/*.ts"],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        ...globals.node,
        ...globals.es2022,
      },
    },
    rules: {
      "no-restricted-imports": ["error", FORBIDDEN_SIBLING_APPS],
      "no-restricted-globals": [
        "error",
        {
          name: "window",
          message:
            "main runs in Node — no DOM. Reach the renderer via webContents.send / ipcMain.handle.",
        },
        {
          name: "document",
          message: "main runs in Node — no DOM.",
        },
        {
          name: "localStorage",
          message: "main runs in Node — no DOM storage.",
        },
      ],
    },
  },
  {
    files: ["preload/**/*.ts"],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        ...globals.node,
        ...globals.browser,
        ...globals.es2022,
      },
    },
    rules: {
      "no-restricted-imports": ["error", FORBIDDEN_SIBLING_APPS],
    },
  },
  {
    files: ["renderer/**/*.ts", "renderer/**/*.tsx"],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: 2022,
      sourceType: "module",
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
      globals: {
        ...globals.browser,
        ...globals.es2022,
      },
    },
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            ...FORBIDDEN_SIBLING_APPS.patterns,
            {
              group: ["electron", "electron/*"],
              message:
                "Renderer must not import electron directly. Reach main via window.bridge.ipc.",
            },
          ],
        },
      ],
      "no-restricted-globals": [
        "error",
        {
          name: "fetch",
          message:
            "Renderer must not fetch directly — CSP connect-src 'none' blocks it. Go through window.bridge.ipc.invoke instead.",
        },
        {
          name: "XMLHttpRequest",
          message:
            "Renderer must not make HTTP requests directly. Go through window.bridge.ipc.invoke instead.",
        },
        {
          name: "EventSource",
          message:
            "Renderer must not open SSE directly. Subscriptions cross IPC via window.bridge.",
        },
        {
          name: "WebSocket",
          message: "Renderer must not open WebSocket connections directly.",
        },
      ],
    },
  },
  {
    files: ["**/*.test.ts", "**/*.test.tsx"],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: 2022,
      sourceType: "module",
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
      globals: {
        ...globals.node,
        ...globals.browser,
        ...globals.es2022,
      },
    },
  },
];
