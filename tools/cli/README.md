# @0x-copilot/cli

[![ci](https://github.com/0x-copilot-dev/0x-copilot/actions/workflows/ci-cli.yml/badge.svg)](https://github.com/0x-copilot-dev/0x-copilot/actions/workflows/ci-cli.yml)
[![npm](https://img.shields.io/npm/v/@0x-copilot/cli?logo=npm&color=cb3837)](https://www.npmjs.com/package/@0x-copilot/cli)
[![downloads](https://img.shields.io/npm/dw/@0x-copilot/cli?color=cb3837)](https://www.npmjs.com/package/@0x-copilot/cli)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)
[![node](https://img.shields.io/node/v/@0x-copilot/cli?logo=node.js&logoColor=white)](https://nodejs.org)
[![platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey)](#platforms)
[![local-first](https://img.shields.io/badge/local--first-BYOK-6f42c1)](https://github.com/0x-copilot-dev/0x-copilot#readme)

Install and launch the **0xCopilot** desktop app from your terminal. No DMG, no
`.exe` installer, no Apple Developer or Windows code-signing credentials.

```bash
npm install -g @0x-copilot/cli   # or: bun add -g @0x-copilot/cli
copilot                          # first run stages the runtime, then opens the app
```

`copilot` is also available as `0xcopilot`.

## Why a CLI instead of a DMG

0xCopilot is a fully-local desktop app: one Electron shell that boots a bundled
runtime (CPython + PostgreSQL + the app's services) on your machine. A signed
DMG/`.exe` would need Apple/Windows signing certificates. Distributing through
`npm`/`bun` sidesteps that entirely:

- macOS **Gatekeeper notarization** and Windows **SmartScreen** only trigger on
  files carrying a "downloaded from the internet" marker (`com.apple.quarantine`
  / Mark-of-the-Web). Browsers and Mail set it; **`npm`, `bun`, `curl`, and Node
  do not.** The same binaries that would show a scary dialog as a double-clicked
  DMG run silently when the CLI stages them — the reason Homebrew, `uv`, and
  `pyenv` work.
- Apple Silicon requires every binary to carry at least an **ad-hoc** signature.
  Staging applies one (`codesign --sign -`) at install time — **no credentials**.
- Launching Electron as a process (not a distributed `.app`) means there's no
  app-bundle Gatekeeper prompt at all.

## Commands

| Command             | What it does                                                       |
| ------------------- | ------------------------------------------------------------------ |
| `copilot`           | Stage the runtime if needed, then start the app.                   |
| `copilot start`     | Same as no command.                                                |
| `copilot install`   | Download + stage the runtime (and sign it on macOS); don't launch. |
| `copilot doctor`    | Report platform, source, Electron, staged runtime, signatures.     |
| `copilot uninstall` | Remove the staged runtime, download cache, and local app data.     |
| `copilot help`      | Usage.                                                             |
| `copilot version`   | CLI version.                                                       |

Flags: `--force` (re-stage from scratch), `--yes` (skip the uninstall prompt).

To remove the command itself: `npm rm -g @0x-copilot/cli`.

## What "first run" does

`copilot` (or `copilot install`) runs the staging step **lazily on first use**,
not in a `postinstall` hook (so it survives `--ignore-scripts` and bun's
untrusted-package script blocking). Staging:

1. Downloads + **sha256-verifies** a pinned CPython and PostgreSQL build (a few
   hundred MB, cached under `~/.cache/enterprise-desktop-runtime`).
2. `pip install`s the app's Python services on your machine.
3. On macOS, **ad-hoc code-signs** every bundled native binary and strips
   quarantine.

The result lands in `~/.0xcopilot/runtime/<platform>-<arch>/`. Subsequent runs
are fast (content-stamped; nothing re-downloads or re-signs unless it changed).

## Platforms

macOS (arm64 + x64) and Windows (x64). Linux is not distributed this way.

## On-disk locations

| Path                                                      | Contents                                     |
| --------------------------------------------------------- | -------------------------------------------- |
| `~/.0xcopilot/runtime/<plat>-<arch>/`                     | Staged runtime (Python, Postgres, services). |
| `~/.cache/enterprise-desktop-runtime/`                    | Verified download cache (shared).            |
| App data dir (`~/Library/Application Support/…` on macOS) | Secrets, embedded Postgres cluster, logs.    |

Override the runtime location with `COPILOT_HOME=/path`. `copilot uninstall`
clears all three.

## Sign-in

Once the app is up, sign-in (including **Connect wallet** / SIWE and Google) runs
through the app's local facade + the system browser. See
[apps/desktop/README.md](../../apps/desktop/README.md#sign-in).

## Development

In a monorepo checkout the CLI reads the app + staging tooling directly (no
`payload/`); run it with `node tools/cli/bin/copilot.mjs <command>`. At publish
time, `prepack` runs `scripts/assemble-payload.mjs`, which builds the desktop app
and mirrors the monorepo subset the staging needs into `payload/` so the
published tarball is self-contained.

The credential-free signing lives in
[`tools/desktop-runtime/stage.mjs`](../desktop-runtime/stage.mjs) behind the
opt-in `--adhoc-sign` flag; the electron-builder packaging path leaves it off and
signs with a real Developer ID instead.

## License

[MIT](./LICENSE) © 0xCopilot
