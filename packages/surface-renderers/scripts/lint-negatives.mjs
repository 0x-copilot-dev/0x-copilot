#!/usr/bin/env node
// Lint negatives assertion script (Phase 4-A).
//
// Runs ESLint over src/__lint-negatives__/ and asserts every .ts/.tsx file
// in that directory produces at least one lint error. If any file passes
// clean, the ESLint rule has regressed and the corresponding ban no
// longer fires for tier-1 renderers.
//
// Exits 0 on success (every negative correctly errors). Exits 1 on any
// negative file passing clean or on internal errors.

import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const here = fileURLToPath(new URL(".", import.meta.url));
const negativesDir = join(here, "..", "src", "__lint-negatives__");

const targets = readdirSync(negativesDir)
  .filter((name) => /\.(ts|tsx)$/.test(name))
  .sort();

if (targets.length === 0) {
  console.error("lint-negatives: no .ts/.tsx files found under", negativesDir);
  process.exit(1);
}

let allFailedAsExpected = true;
const summary = [];

for (const file of targets) {
  const fullPath = join(negativesDir, file);
  const result = spawnSync(
    "npx",
    ["eslint", "--format", "json", "--no-error-on-unmatched-pattern", fullPath],
    { encoding: "utf8", cwd: join(here, "..") },
  );

  if (result.error) {
    console.error("lint-negatives: failed to spawn eslint:", result.error);
    process.exit(1);
  }

  let parsed;
  try {
    parsed = JSON.parse(result.stdout || "[]");
  } catch (e) {
    console.error("lint-negatives: failed to parse eslint output for", file);
    console.error(result.stdout);
    console.error(result.stderr);
    process.exit(1);
  }

  const errorCount = parsed.reduce(
    (acc, entry) => acc + (entry.errorCount ?? 0),
    0,
  );

  if (errorCount === 0) {
    allFailedAsExpected = false;
    summary.push(`  [FAIL] ${file}: expected at least 1 error, got 0`);
  } else {
    summary.push(`  [ok]   ${file}: ${errorCount} error(s)`);
  }
}

console.log("lint-negatives — assertions:");
for (const line of summary) console.log(line);

if (!allFailedAsExpected) {
  console.error(
    "\nlint-negatives: one or more negative files did NOT error. The ESLint rule has regressed.",
  );
  process.exit(1);
}

console.log(
  `\nlint-negatives: all ${targets.length} negative files errored as expected.`,
);
process.exit(0);
