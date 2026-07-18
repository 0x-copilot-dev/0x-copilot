import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    // Run test files serially. Several main/* lifecycle tests await a real
    // async fs pipeline via a fixed event-loop-tick `flush()` (see
    // main/adapters/lifecycle.test.ts) rather than a completion signal; under
    // parallel-file CPU contention that tick budget can expire before the I/O
    // settles, so the suite flakes non-deterministically (worse on 2-core CI
    // runners). The suite is small (~4s), so serializing files makes it
    // deterministic locally and in CI at negligible cost. Removing this
    // requires making those pipelines' completion awaitable instead of
    // tick-counted.
    fileParallelism: false,
    // Default to jsdom — the renderer test uses it. Main/preload tests
    // override per-file with `// @vitest-environment node`.
    environment: "jsdom",
    globals: false,
    css: false,
    // Installs a working in-memory localStorage when the ambient one is
    // unusable (Node's experimental --localstorage-file global can shadow
    // jsdom's with a Storage missing getItem), so renderer components that
    // read globalThis.localStorage (e.g. the Run cockpit's useRunMode) mount.
    setupFiles: ["./vitest.setup.ts"],
    include: [
      "main/**/*.test.ts",
      "preload/**/*.test.ts",
      "renderer/**/*.test.ts",
      "renderer/**/*.test.tsx",
    ],
  },
});
