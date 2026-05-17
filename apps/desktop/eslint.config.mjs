import tsParser from "@typescript-eslint/parser";
import globals from "globals";

// Placeholder ESLint config for @enterprise-search/desktop.
//
// Phase 0 scaffolds the package; Phase 1 (electron-shell, ipc-transport, …)
// writes the main / preload / renderer code and the per-process boundary
// rules that go with it (e.g. no `fetch` / `Transport` in main, no `node:*`
// in renderer). This file exists only so `npm run lint` resolves and so
// Phase 1 has a single place to extend.

export default [
  {
    files: ["main/**/*.ts", "preload/**/*.ts"],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        ...globals.node,
        ...globals.es2022,
      },
    },
  },
  {
    files: ["renderer/**/*.{ts,tsx}"],
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
];
