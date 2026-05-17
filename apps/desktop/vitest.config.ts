import { defineConfig } from "vitest/config";

// Phase 1-C minimal config. Tests run in Node (main-process code only at
// this phase; renderer-side IpcTransport unit tests live in the
// chat-transport package). Agent 1-A may extend this with a renderer
// workspace if/when renderer-side desktop tests show up.
export default defineConfig({
  test: {
    environment: "node",
    globals: false,
    include: ["main/**/*.test.ts"],
  },
});
