#!/usr/bin/env node
/* design-parity · build the surface-language DESIGN bundle
 * =========================================================================
 * Compiles `surfaces/surface-language/design/_mount.jsx` — which pulls in the
 * shared kit stubs and the three vendored Claude Design files — into ONE
 * self-contained classic script at `design/build/design.js`.
 *
 * Why a build step at all. The vendored files are JSX, so something has to
 * transform them. The other design harnesses (first-run, login, run-empty) do
 * it in the page with `@babel/standalone` + React UMD pulled from unpkg, which
 * means every parity run needs the network reachable from inside headless
 * chromium. esbuild and React are already in node_modules; using them makes the
 * design side hermetic and ~instant, at the cost of this one script.
 *
 * The JSX transform is CLASSIC on purpose (`React.createElement` against the
 * global), because that is the contract the vendored files are written to —
 * they declare `/* global React *\/` and never import it. Switching to the
 * automatic runtime would compile fine and then diverge from the design source
 * we are supposed to be measuring against.
 *
 * Output is gitignored (`surfaces/<name>/design/build/`): it is derived from
 * files that ARE tracked, so committing it would only create a second copy to
 * drift.
 *
 * Run: node tools/design-parity/lib/prepare-surface-language-design.mjs
 * ========================================================================= */
import { build } from "esbuild";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const TOOLS = resolve(HERE, "..");
const DESIGN = resolve(TOOLS, "surfaces/surface-language/design");

/* The files whose bytes ARE the design baseline. Anything else in `design/`
 * (index.html, the `_`-prefixed harness modules, the copilot-v3 shim) is
 * scaffolding this repo wrote and is deliberately not hashed as design source. */
const VENDORED = [
  "surface-lang.css",
  "surface-kit.jsx",
  "surface-specs.jsx",
  "surface-archetypes2.jsx",
];

mkdirSync(resolve(DESIGN, "build"), { recursive: true });

await build({
  entryPoints: [resolve(DESIGN, "_mount.jsx")],
  outfile: resolve(DESIGN, "build/design.js"),
  bundle: true,
  format: "iife",
  platform: "browser",
  target: ["chrome110"],
  jsx: "transform",
  jsxFactory: "React.createElement",
  jsxFragment: "React.Fragment",
  define: { "process.env.NODE_ENV": '"development"' },
  logLevel: "info",
});

const sha = (path) =>
  createHash("sha256")
    .update(readFileSync(resolve(DESIGN, path)))
    .digest("hex");

const provenance = {
  surface: "surface-language",
  source: {
    tool: "DesignSync",
    project: {
      name: "Copilot",
      projectId: "73f810d9-7b77-4849-9087-f7f8e366c48a",
    },
    page: "0xCopilot Surface Language",
    fetchedAt: "2026-07-29",
  },
  note: "SHA-256 of the vendored copies AS COMMITTED. Prettier reformats whitespace on commit (values unchanged), so these hashes are of the repo copy, not of the DesignSync response body. Re-vendor per design-kit/REFRESH.md.",
  files: Object.fromEntries(VENDORED.map((file) => [file, sha(file)])),
};
writeFileSync(
  resolve(DESIGN, "PROVENANCE.json"),
  `${JSON.stringify(provenance, null, 2)}\n`,
);

console.log(
  `[surface-language] design bundle → surfaces/surface-language/design/build/design.js`,
);
