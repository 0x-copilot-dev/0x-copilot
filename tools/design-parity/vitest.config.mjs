import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// Isolated config for the design-parity live-render harness. It renders the
// REAL chat-surface components (resolved via the workspace) to static HTML that
// the browser extractor then reads. Kept out of the app packages so running it
// never touches their test suites.
export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  test: {
    environment: "jsdom",
    include: ["lib/render-live.test.tsx"],
    // Long-ish: pulls the chat-surface barrel through esbuild once.
    testTimeout: 60000,
  },
});
