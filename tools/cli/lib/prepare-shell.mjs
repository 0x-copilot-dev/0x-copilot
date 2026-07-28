#!/usr/bin/env node
// Child-process entry point used by `copilot install` and cold `copilot start`.
// Electron 42 downloads its native binary lazily on first require. Running that
// require in a child lets the Electron fetch and macOS shell preparation overlap
// the independent Python/Postgres/service staging critical path.

import path from "node:path";
import { fileURLToPath } from "node:url";

import { ensureBrandedShell } from "./mac-shell.mjs";
import { resolveElectronBinary, resolveRoots } from "./paths.mjs";

const packageRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const roots = resolveRoots(packageRoot);
const electronBinary = resolveElectronBinary(roots.electronBases);
ensureBrandedShell({
  electronBinary,
  appDir: roots.appDir,
});
