#!/usr/bin/env node
// assemble-payload.mjs — build the published package's self-contained payload.
//
// The `copilot` CLI reuses tools/desktop-runtime/stage.mjs, which reads the
// service source + shared packages from a monorepo-shaped tree (REPO_ROOT/
// services/*, REPO_ROOT/packages/*, REPO_ROOT/tools/desktop-runtime). An
// end user has no monorepo, so at prepack we mirror exactly that subset into
// `payload/` (which then becomes REPO_ROOT for the bundled stage.mjs), plus the
// built Electron app under `payload/desktop/`.
//
// Site-packages are NOT bundled — they're host-specific and get pip-installed
// on the user's machine at first run. Only source + requirements ship.

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PKG_ROOT = path.resolve(HERE, "..");
const REPO_ROOT = path.resolve(PKG_ROOT, "..", "..");
const PAYLOAD = path.join(PKG_ROOT, "payload");

// Mirror tools/desktop-runtime/stage.mjs SERVICES: the dirs each service needs
// plus its requirements.txt.
const SERVICES = [
  { name: "backend", dirs: ["src", "migrations", "scripts"] },
  { name: "backend-facade", dirs: ["src"] },
  {
    name: "ai-backend",
    dirs: ["src", "migrations", "scripts", "config", "skills"],
  },
];
const SHARED_PACKAGES = ["service-contracts", "audit-chain"];

const EXCLUDE = new Set([
  "__pycache__",
  ".venv",
  "node_modules",
  "dist",
  "build",
  ".pytest_cache",
  ".mypy_cache",
  ".git",
]);

function log(msg) {
  process.stdout.write(`[assemble] ${msg}\n`);
}
function fail(msg) {
  process.stderr.write(`[assemble] ERROR: ${msg}\n`);
  process.exit(1);
}

function copyFiltered(from, to) {
  if (!fs.existsSync(from)) return false;
  fs.cpSync(from, to, {
    recursive: true,
    filter: (src) => {
      const base = path.basename(src);
      return (
        !EXCLUDE.has(base) &&
        !base.endsWith(".pyc") &&
        !base.endsWith(".egg-info")
      );
    },
  });
  return true;
}

function run(cmd, args, opts = {}) {
  const res = spawnSync(cmd, args, { stdio: "inherit", ...opts });
  if (res.error) fail(`${cmd}: ${res.error.message}`);
  if (res.status !== 0)
    fail(`${[cmd, ...args].join(" ")} exited ${res.status}`);
}

// --- 1. build the desktop app -------------------------------------------
const appMain = path.join(
  REPO_ROOT,
  "apps",
  "desktop",
  "out",
  "main",
  "index.js",
);
if (process.env.COPILOT_SKIP_BUILD === "1" && fs.existsSync(appMain)) {
  log("COPILOT_SKIP_BUILD=1 and app already built — skipping desktop build");
} else {
  log("building @0x-copilot/desktop");
  // Windows npm is npm.cmd; Node won't resolve a bare "npm" without .exe.
  const npm = process.platform === "win32" ? "npm.cmd" : "npm";
  run(npm, ["run", "build", "--workspace", "@0x-copilot/desktop"], {
    cwd: REPO_ROOT,
  });
}
if (!fs.existsSync(appMain)) fail(`desktop build produced no ${appMain}`);

// --- 1b. build the frontend web assets (SIWE wallet page) ----------------
// stage.mjs (stageWebAssets) stages apps/frontend/dist -> <runtime>/web so the
// supervised facade can serve wallet.html same-origin with /v1/auth/siwe/*.
// The published payload must ship this pre-built dist (in payload mode stage.mjs
// has no npm workspace to build from), so build it here at prepack.
const walletHtml = path.join(
  REPO_ROOT,
  "apps",
  "frontend",
  "dist",
  "wallet.html",
);
if (process.env.COPILOT_SKIP_BUILD === "1" && fs.existsSync(walletHtml)) {
  log(
    "COPILOT_SKIP_BUILD=1 and frontend already built — skipping frontend build",
  );
} else {
  log("building @0x-copilot/frontend");
  const npm = process.platform === "win32" ? "npm.cmd" : "npm";
  run(npm, ["run", "build", "--workspace", "@0x-copilot/frontend"], {
    cwd: REPO_ROOT,
  });
}
if (!fs.existsSync(walletHtml))
  fail(`frontend build produced no ${walletHtml}`);

// --- 2. reset payload ----------------------------------------------------
fs.rmSync(PAYLOAD, { recursive: true, force: true });
fs.mkdirSync(PAYLOAD, { recursive: true });

// --- 3. staging tool -----------------------------------------------------
const stageDest = path.join(PAYLOAD, "tools", "desktop-runtime");
fs.mkdirSync(stageDest, { recursive: true });
for (const f of ["stage.mjs", "manifest.json"]) {
  fs.copyFileSync(
    path.join(REPO_ROOT, "tools", "desktop-runtime", f),
    path.join(stageDest, f),
  );
}
log("copied tools/desktop-runtime (stage.mjs + manifest.json)");

// --- 4. service source + requirements ------------------------------------
for (const svc of SERVICES) {
  const from = path.join(REPO_ROOT, "services", svc.name);
  const to = path.join(PAYLOAD, "services", svc.name);
  fs.mkdirSync(to, { recursive: true });
  const req = path.join(from, "requirements.txt");
  if (!fs.existsSync(req)) fail(`missing ${req}`);
  fs.copyFileSync(req, path.join(to, "requirements.txt"));
  const copied = [];
  for (const d of svc.dirs) {
    if (copyFiltered(path.join(from, d), path.join(to, d))) copied.push(d);
  }
  log(`copied services/${svc.name}: requirements.txt, ${copied.join(", ")}`);
}

// --- 5. shared python packages -------------------------------------------
for (const pkg of SHARED_PACKAGES) {
  const from = path.join(REPO_ROOT, "packages", pkg);
  const to = path.join(PAYLOAD, "packages", pkg);
  if (!fs.existsSync(path.join(from, "pyproject.toml"))) {
    fail(`missing packages/${pkg}/pyproject.toml`);
  }
  copyFiltered(from, to);
  log(`copied packages/${pkg}`);
}

// --- 6. built desktop app ------------------------------------------------
const appDest = path.join(PAYLOAD, "desktop");
fs.mkdirSync(appDest, { recursive: true });
fs.cpSync(
  path.join(REPO_ROOT, "apps", "desktop", "out"),
  path.join(appDest, "out"),
  {
    recursive: true,
  },
);
const desktopVersion = JSON.parse(
  fs.readFileSync(
    path.join(REPO_ROOT, "apps", "desktop", "package.json"),
    "utf8",
  ),
).version;
// Minimal app manifest: everything the main/renderer need is bundled into
// out/ by esbuild (electron is the only external), so no dependencies here.
fs.writeFileSync(
  path.join(appDest, "package.json"),
  JSON.stringify(
    {
      name: "0xcopilot-desktop",
      productName: "0xCopilot",
      version: desktopVersion,
      main: "out/main/index.js",
    },
    null,
    2,
  ) + "\n",
);
log(`copied built desktop app (v${desktopVersion})`);

// --- 6b. bundled-default Google OAuth client -----------------------------
// Ships next to the app (app.getAppPath()) so "Continue with Google" works
// with zero user setup. Source of truth is a gitignored google-oauth.json
// (never in git — the repo is public); at publish/CI time it can instead be
// synthesized from GOOGLE_OAUTH_CLIENT_ID/SECRET in the env. Absent both, no
// default ships and Google sign-in stays unavailable (wallet/local still work).
const gOAuthSrc = path.join(REPO_ROOT, "apps", "desktop", "google-oauth.json");
const gOAuthDest = path.join(appDest, "google-oauth.json");
if (fs.existsSync(gOAuthSrc)) {
  fs.copyFileSync(gOAuthSrc, gOAuthDest);
  log("bundled Google OAuth default (from apps/desktop/google-oauth.json)");
} else if ((process.env.GOOGLE_OAUTH_CLIENT_ID || "").trim() !== "") {
  const clientSecret = (process.env.GOOGLE_OAUTH_CLIENT_SECRET || "").trim();
  fs.writeFileSync(
    gOAuthDest,
    JSON.stringify(
      {
        client_id: process.env.GOOGLE_OAUTH_CLIENT_ID.trim(),
        ...(clientSecret !== "" ? { client_secret: clientSecret } : {}),
      },
      null,
      2,
    ) + "\n",
  );
  log("bundled Google OAuth default (from publish env GOOGLE_OAUTH_CLIENT_ID)");
} else {
  log(
    "no Google OAuth default bundled (no google-oauth.json, no env) — " +
      "Google sign-in will be unavailable in this build",
  );
}

// --- 7. built frontend web assets ----------------------------------------
// stage.mjs (stageWebAssets) reads REPO_ROOT/apps/frontend/dist and copies it
// to <runtime>/web. In the payload REPO_ROOT is this dir, so mirror the dist —
// its presence makes stage.mjs skip the (unavailable) workspace build.
const feDest = path.join(PAYLOAD, "apps", "frontend", "dist");
fs.mkdirSync(feDest, { recursive: true });
fs.cpSync(path.join(REPO_ROOT, "apps", "frontend", "dist"), feDest, {
  recursive: true,
});
log("copied built frontend dist (wallet.html + assets)");

log(`payload assembled at ${path.relative(REPO_ROOT, PAYLOAD)}`);
