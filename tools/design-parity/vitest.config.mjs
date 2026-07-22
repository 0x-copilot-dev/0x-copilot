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
    include: ["lib/render-live.test.tsx", "lib/render-live-login.test.tsx"],
    // Long-ish: pulls the chat-surface barrel through esbuild once.
    testTimeout: 60000,
  },
});
