// Fast, build-free validation that @0x-copilot/cli is shippable.
//
// `npm pack --dry-run` would trigger `prepack` (assemble-payload → a full
// desktop-app build), which is far too heavy for a per-PR check. Instead we
// assert the publish manifest is coherent: every `bin` target and every
// declared `files` entry resolves, and the license + readme that the npm page
// renders are present. Exits non-zero (failing CI) on the first problem.

import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const pkg = JSON.parse(readFileSync(path.join(ROOT, "package.json"), "utf8"));

const problems = [];
const require_ = (rel, why) => {
  if (!existsSync(path.join(ROOT, rel)))
    problems.push(`missing ${rel} (${why})`);
};

// License must be a real OSI id and the file must ship.
if (pkg.license !== "MIT")
  problems.push(`license is "${pkg.license}", expected "MIT"`);
require_("LICENSE", "referenced in files[] and rendered on npm");
require_("README.md", "the npm package page");

// Every bin target must exist and start with a shebang.
for (const [name, target] of Object.entries(pkg.bin ?? {})) {
  if (!existsSync(path.join(ROOT, target))) {
    problems.push(`bin "${name}" -> ${target} does not exist`);
    continue;
  }
  const head = readFileSync(path.join(ROOT, target), "utf8").slice(0, 32);
  if (!head.startsWith("#!"))
    problems.push(`bin "${name}" (${target}) has no shebang`);
}

// Declared publish roots must exist (payload is built by prepack, so it's
// allowed to be absent in a source checkout).
for (const entry of pkg.files ?? []) {
  if (entry === "payload") continue;
  require_(entry, "declared in package.json files[]");
}

if (problems.length) {
  console.error("pack-manifest-check: FAIL");
  for (const p of problems) console.error(`  - ${p}`);
  process.exit(1);
}
console.log(
  `pack-manifest-check: OK (@0x-copilot/cli@${pkg.version}, license ${pkg.license})`,
);
