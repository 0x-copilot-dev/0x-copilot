# Distribution readiness audit — the desktop app

**Scope.** What exists today for shipping the 0xCopilot desktop app to end users,
and what is still missing before a real published install works end to end.
Audit-only: this document does **not** attempt actual signing, notarization, or
publishing — those need credentials/secrets that must never live in the repo.

**Two independent distribution channels.** They share the staged-runtime
substrate (`tools/desktop-runtime/stage.mjs`) but diverge on packaging, signing,
and delivery:

| Channel                            | Artifact                            | Delivery                                | Signing model                                         |
| ---------------------------------- | ----------------------------------- | --------------------------------------- | ----------------------------------------------------- |
| **A. electron-builder installers** | `.dmg` / `.zip` (mac), `.exe` (win) | GitHub Releases + electron-updater feed | real Developer ID + Apple notarization / Authenticode |
| **B. `copilot` CLI (npm/bun)**     | `@0x-copilot/cli` npm package       | `npm i -g` / `bun add -g`               | **credential-free ad-hoc** (`stage.mjs --adhoc-sign`) |

Channel B is the primary advertised path (website `apps/website/src/pages/docs.astro`,
`tools/cli/README.md`); Channel A is the "classic installer" path for users who
want a `.app`/`.exe` and background auto-update.

---

## 1. What EXISTS (with evidence)

### Runtime staging (shared substrate)

- `tools/desktop-runtime/stage.mjs` — downloads + **sha256-verifies** pinned
  CPython + PostgreSQL (`tools/desktop-runtime/manifest.json`), pip-installs all
  three services per-arch (`--require-hashes` for backend/facade), prunes, and
  (opt-in `--adhoc-sign`) strips + ad-hoc-signs every bundled Mach-O so unsigned
  arm64 binaries run on Apple Silicon. Idempotent (sha256 + requirements
  stamps). Writes `staging-manifest.json` as the "runnable" marker.
- `tools/desktop-runtime/run-local.mjs` + `tools/desktop-runtime/run-supervised.mjs`
  — headless boot smoke and the from-source one-command GUI runner
  (`make desktop-supervised`). Prove the staged tree boots the supervised
  topology before it is ever packaged.
- CI: `.github/workflows/desktop-supervised-boot-drill.yml` (macos-14, on a
  weekly schedule and path-triggered) boots the real supervised stack and drives
  a hermetic run→stream. This is the only automated exercise of the topology.

### Channel A — electron-builder installers

- `apps/desktop/electron-builder.yml` — `appId com.0x-copilot.app`, `productName
0xCopilot`, `dmg`+`zip` (mac) / `nsis` (win) targets, `extraResources` maps
  `resources/runtime` → `<resourcesPath>/runtime`, hardened runtime +
  `build/entitlements.mac.plist`, `afterPack: build/sign-nested.js`, `publish`
  provider `github` (`0x-copilot-dev/0x-copilot`).
- `apps/desktop/build/sign-nested.js` — pre-signs bundled python/postgres Mach-O
  with the hardened runtime before electron-builder signs the outer `.app`;
  no-ops cleanly when no identity is configured.
- `apps/desktop/package.json` `dist:mac:arm64` / `dist:mac:x64` / `dist:win` —
  local stage→build→package scripts (`--publish never`).
- `.github/workflows/release-desktop.yml` — tag-triggered (`v*`) matrix build
  (macos-14 arm64, macos-15-intel x64, windows-latest x64). **Signing degrades
  gracefully**: every signing step is guarded on secret _presence_
  (`MAC_CSC_LINK`, `APPLE_API_KEY`+`APPLE_API_KEY_ID`+`APPLE_API_ISSUER`,
  `WIN_CSC_LINK`); absent ⇒ unsigned build + a loud `::warning::`.
  `workflow_dispatch` is an unsigned, non-publishing dry-run.
- `apps/desktop/main/updater.ts` — electron-updater client against the GitHub
  Releases feed (checks on ready + every 4h, installs on quit); hard no-op when
  unpackaged.
- Runbook: `docs/deployment/desktop-release.md` — secret names/formats, cut-a-
  release, dry-run, auto-update contract, rollback, Azure-Trusted-Signing
  alternative for Windows.

### Channel B — the `copilot` CLI

- `tools/cli/` (`@0x-copilot/cli` v0.1.3) — `bin/copilot.mjs` +
  `lib/{stage,launch,mac-shell,doctor,repair,uninstall,paths,ui}.mjs`. On run it
  stages into `~/.0xcopilot` via the same `stage.mjs` with `--adhoc-sign`, clones
  Electron.app into a branded `0xCopilot.app` (mac), and spawns it with
  `COPILOT_RUNTIME_DIR` set (`COPILOT_PRODUCTION=1` → production posture).
- `tools/cli/scripts/assemble-payload.mjs` (prepack) — mirrors the monorepo
  subset (`services/*` source + requirements, shared packages, the built
  Electron app under `payload/desktop/`) into `payload/`. **Site-packages are
  NOT bundled** — pip-installed on the user's machine at first run.
- CI: `.github/workflows/ci-cli.yml` — syntax `--check`, dependency-free
  version/help smoke, and `scripts/pack-manifest-check.mjs` (validates the
  `npm pack` file list) on ubuntu/macos/windows.
- Docs: `tools/cli/README.md`, `tools/cli/TROUBLESHOOTING.md`; website install
  copy in `apps/website/src/pages/docs.astro`.

### Version / runtime-stamp coupling (partial)

- The runtime is pinned in `tools/desktop-runtime/manifest.json` (python 3.13.x,
  postgres 17.x) and stamped into `staging-manifest.json` per stage.
- Channel A: `release-desktop.yml` is tag-triggered, and
  `docs/deployment/desktop-release.md §2` requires the tag to equal
  `apps/desktop/package.json` `version` (electron-updater compares semver).
- Channel B: the CLI re-stages when its own version marker in `~/.0xcopilot`
  changes across `npm i -g …@latest` (documented in `tools/cli/README.md`).

---

## 2. What is MISSING for a real published `copilot` install

Ranked; **P0** blocks the advertised install path, **P1** is
credibility/supply-chain, **P2** is polish.

### P0 — no npm-publish wiring for `@0x-copilot/cli`

The website and `tools/cli/README.md` both advertise `npm install -g
@0x-copilot/cli` (and the README carries an npmjs.com npm-version badge), but
**no workflow runs `npm publish`**. `ci-cli.yml` only validates; `release-desktop.yml`
publishes electron-builder artifacts to GitHub _Releases_ — a different channel.
`grep -rn "npm publish\|NPM_TOKEN\|--provenance"` across `.github/` and `tools/cli/`
returns nothing.

Concrete gaps to close (all product wiring, not secrets):

- [ ] A `release-cli` workflow (tag- or dispatch-triggered) that runs
      `prepack` (assemble payload) then `npm publish --access public` for
      `tools/cli`. Prefer `npm publish --provenance` (needs
      `id-token: write`) for a signed provenance attestation.
- [ ] An `NPM_TOKEN` (automation token) repo/environment secret consumed by that
      workflow — **deployment control, must not be in the repo**; only the
      wiring that reads it belongs here.
- [ ] Decide the publish trigger and its coupling to `tools/cli/package.json`
      `version` (mirror the `desktop-release.md §2` tag==version rule).
- [ ] A pre-publish `npm pack --dry-run` gate that fails if `payload/` was not
      assembled (today `pack-manifest-check.mjs` validates the manifest but is
      not gating a publish).

Until this lands, `npm i -g @0x-copilot/cli` fails for end users even though the
whole runtime substrate works.

### P1 — signing / notarization contract is split and only partially evidenced

- **Channel A (installers).** The _wiring_ to sign+notarize exists
  (`release-desktop.yml`, `sign-nested.js`, entitlements), but the certs and
  Apple API key are **deployment controls not evidenced in the repo** — with no
  secrets configured, `release-desktop` ships **unsigned** DMGs/EXEs (Gatekeeper
  right-click-Open / SmartScreen friction) and macOS auto-update **cannot apply**
  (Squirrel.Mac rejects an unsigned/failed-signature `zip` — see
  `desktop-release.md §4`). Checklist:
  - [ ] Provision `MAC_CSC_LINK`+`MAC_CSC_KEY_PASSWORD`,
        `APPLE_API_KEY`+`APPLE_API_KEY_ID`+`APPLE_API_ISSUER`, and
        `WIN_CSC_LINK`+`WIN_CSC_KEY_PASSWORD` (or Azure Trusted Signing).
  - [ ] One signed+notarized dry-run per platform before the first real tag.
- **Channel B (CLI).** Deliberately avoids code-signing via ad-hoc signing +
  the non-quarantine property of npm/curl-staged files (a spawned process, not a
  distributed bundle, so Gatekeeper/SmartScreen never gate it). This is sound but
  the reasoning is **scattered** across `stage.mjs` header, `apps/desktop/README.md`,
  and `tools/cli/README.md`. Checklist:
  - [ ] Write a single "ad-hoc-sign contract" note (why no Developer ID is
        needed, the exact Apple-Silicon requirement it satisfies, and the
        quarantine/Mark-of-the-Web assumption) and link the three sources to it.
  - [ ] Confirm the assumption on a clean machine: a freshly `npm i -g`'d CLI
        launches without a Gatekeeper prompt (the drill stages `--adhoc-sign` but
        does not exercise the npm-download → launch path end to end).

### P1 — no supply-chain provenance for desktop artifacts

`release-images.yml` produces SBOMs, cosign keyless signatures, provenance
attestations, and Trivy gating for the **web service images**. The desktop
artifacts (DMG/EXE/`zip`) and the npm CLI package have **none** of this. For a
regulated buyer this is a gap. Checklist:

- [ ] SBOM for the packaged desktop app (bundled python/postgres + wheels).
- [ ] Provenance/signature for the release assets (or `npm publish --provenance`
      for Channel B).
- [ ] A published checksum/signature manifest users can verify before install.

### P2 — distribution surface gaps

- [ ] **No `curl | bash` install for the CLI.** `deploy/self-host/install.sh`
      exists for the **web** stack only; the CLI is npm/bun-only. If a curl
      one-liner is intended (referenced in project notes), it is not present.
- [ ] **App version decoupled from CLI version.** `apps/desktop/package.json`
      (`0.1.0`) and `tools/cli/package.json` (`0.1.3`) drift independently; the
      runtime-stamp coupling is documented only for Channel A. Define one
      source-of-truth version policy across both channels + the runtime manifest.
- [ ] **Linux / Windows-ARM unsupported** (`manifest.json` ships darwin +
      win32-x64 only). Documented in `tools/cli/README.md`; listed here as a
      known distribution boundary, not a defect.

---

## 3. Readiness checklist (summary)

| Control                                | Status | Evidence / gap                                                        |
| -------------------------------------- | ------ | --------------------------------------------------------------------- |
| Runtime staging (verified, idempotent) | ✅     | `stage.mjs`, `staging-manifest.json`, supervised-boot-drill CI        |
| One-command supervised run from source | ✅     | `run-supervised.mjs`, `make desktop-supervised`                       |
| Installer packaging (electron-builder) | ✅     | `electron-builder.yml`, `dist:*`, `sign-nested.js`                    |
| Installer release pipeline             | ✅     | `release-desktop.yml` (tag-triggered, signing-graceful)               |
| Auto-update client + contract          | ✅     | `main/updater.ts`, `desktop-release.md §4`                            |
| CLI package + first-run staging        | ✅     | `tools/cli/`, `assemble-payload.mjs`, `ci-cli.yml`                    |
| **CLI npm publish wiring**             | ❌     | **P0** — advertised but no `npm publish` workflow / `NPM_TOKEN`       |
| macOS/Windows signing certs configured | ⚠️     | **P1 deployment** — wiring exists, secrets not evidenced in repo      |
| ad-hoc-sign contract (single doc)      | ⚠️     | **P1** — reasoning scattered; not consolidated                        |
| Desktop SBOM / provenance / signatures | ❌     | **P1** — web images have it; desktop + npm package do not             |
| curl install for the CLI               | ❌     | **P2** — npm/bun only; no CLI `install.sh`                            |
| Unified version / runtime-stamp policy | ⚠️     | **P2** — app vs CLI versions drift; coupling only doc'd for Channel A |

**Bottom line.** The runtime substrate, the supervised topology, both packaging
tracks, and the auto-update contract are all built and (for the topology)
CI-verified. The single blocker for the _advertised_ `npm i -g @0x-copilot/cli`
path is the missing npm-publish workflow (P0). Signing/notarization is a
deployment control (provision secrets, then a signed dry-run), and supply-chain
provenance for the desktop artifacts is the main remaining credibility gap for a
regulated buyer.
