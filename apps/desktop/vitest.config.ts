import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
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
