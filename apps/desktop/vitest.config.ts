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
    include: [
      "main/**/*.test.ts",
      "preload/**/*.test.ts",
      "renderer/**/*.test.ts",
      "renderer/**/*.test.tsx",
    ],
  },
});
