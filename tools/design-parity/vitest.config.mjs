import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// Isolated config for the design-parity live-render harness. It renders the
// REAL chat-surface components (resolved via the workspace) to static HTML that
// the browser extractor then reads. Kept out of the app packages so running it
// never touches their test suites.
export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  // Resolve the workspace package to THIS repo's source, not whatever the
  // (possibly-stale, MAIN-checkout) node_modules symlink points at — so the
  // live render reflects the working tree's chat-surface, which is the whole
  // point of a parity harness. (apps/frontend components are imported by
  // relative path in render-live-login.test.tsx, so they already use the tree.)
  resolve: {
    alias: {
      "@0x-copilot/chat-surface": fileURLToPath(
        new URL("../../packages/chat-surface/src/index.ts", import.meta.url),
      ),
    },
  },
  test: {
    environment: "jsdom",
    // Glob, not an enumerated list: every surface's live-render harness is
    // `lib/render-live[-<surface>].test.tsx`, so adding a surface never edits
    // this file (which would otherwise be a merge point between parallel
    // per-surface parity runs — exactly the conflict this line just resolved,
    // where main's enumerated list and this branch's five new surfaces collided).
    include: ["lib/render-live*.test.tsx"],
    // Long-ish: pulls the chat-surface barrel through esbuild once.
    testTimeout: 60000,
  },
});
