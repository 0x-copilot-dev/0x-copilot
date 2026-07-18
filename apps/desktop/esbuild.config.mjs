import { copyFile, mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { build } from "esbuild";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const outDir = join(__dirname, "out");

const mainTask = build({
  entryPoints: [join(__dirname, "main/index.ts")],
  outfile: join(outDir, "main/index.js"),
  bundle: true,
  platform: "node",
  format: "cjs",
  target: "node20",
  external: ["electron"],
  sourcemap: false,
  logLevel: "info",
});

const preloadTask = build({
  entryPoints: [join(__dirname, "preload/bridge.ts")],
  outfile: join(outDir, "preload/bridge.js"),
  bundle: true,
  platform: "node",
  format: "cjs",
  target: "node20",
  external: ["electron"],
  sourcemap: false,
  logLevel: "info",
});

const rendererTask = build({
  entryPoints: [join(__dirname, "renderer/bootstrap.tsx")],
  outfile: join(outDir, "renderer/bootstrap.js"),
  bundle: true,
  platform: "browser",
  format: "esm",
  target: "es2022",
  jsx: "automatic",
  loader: {
    ".tsx": "tsx",
    ".ts": "ts",
    ".css": "css",
    ".woff2": "dataurl",
    ".woff": "dataurl",
    ".ttf": "dataurl",
  },
  external: ["electron"],
  sourcemap: false,
  logLevel: "info",
});

const copyAssetsTask = (async () => {
  await mkdir(join(outDir, "renderer"), { recursive: true });
  await copyFile(
    join(__dirname, "renderer/index.html"),
    join(outDir, "renderer/index.html"),
  );
})();

await Promise.all([mainTask, preloadTask, rendererTask, copyAssetsTask]);
