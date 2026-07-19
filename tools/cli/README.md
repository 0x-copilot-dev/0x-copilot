# @0x-copilot/cli

**Put your day on autopilot.**

[![ci](https://github.com/0x-copilot-dev/0x-copilot/actions/workflows/ci-cli.yml/badge.svg)](https://github.com/0x-copilot-dev/0x-copilot/actions/workflows/ci-cli.yml)
[![npm](https://img.shields.io/npm/v/@0x-copilot/cli?logo=npm&color=cb3837)](https://www.npmjs.com/package/@0x-copilot/cli)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/0x-copilot-dev/0x-copilot/blob/main/tools/cli/LICENSE)
[![platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey)](#requirements)
[![local-first](https://img.shields.io/badge/local--first-BYOK-6f42c1)](https://github.com/0x-copilot-dev/0x-copilot#readme)

Install and launch the 0xCopilot desktop app from your terminal.

```bash
npm install -g @0x-copilot/cli
copilot
```

Using Bun? Install with `bun add -g @0x-copilot/cli`. The `copilot` command is
also available as `0xcopilot`.

## Requirements

- Node.js 20+
- macOS on Apple Silicon or Intel, or Windows x64
- Internet access for first-launch runtime downloads

The first launch downloads a few hundred MB; allow additional disk space for the
staged environment and app data. Linux and Windows on ARM are not supported.

## Quick start

Run `copilot`, sign in with a provider available in your deployment, then open
**Settings → Models & keys → Provider keys** to add the model you want to use.

## Commands

| Command             | Description                                                       |
| ------------------- | ----------------------------------------------------------------- |
| `copilot`           | Prepare the runtime when needed, then launch the app.             |
| `copilot start`     | Launch the app, equivalent to `copilot`.                          |
| `copilot install`   | Run staging checks and refresh the runtime without launching.     |
| `copilot doctor`    | Diagnose platform, runtime, Electron, and signing problems.       |
| `copilot repair`    | Recover from a stuck launch while keeping local data.             |
| `copilot uninstall` | Delete the runtime, download cache, and all local 0xCopilot data. |
| `copilot help`      | Show command help.                                                |
| `copilot version`   | Print the installed CLI version.                                  |

Flags:

- `--force`, `-f` — run staging checks again before launch
- `--yes`, `-y` — skip the uninstall confirmation
- `--session` — make `copilot repair` clear saved sign-in sessions
- `--help`, `-h` and `--version`, `-v` — show help or version information

## What happens on first launch

Setup happens when you run `copilot`, not during package installation. The CLI
downloads and SHA-256-verifies pinned CPython and PostgreSQL builds, installs the
bundled services, and signs native binaries on macOS. Later launches reuse the
staged runtime; CLI upgrades check it again to keep the app and services in sync.

## Update

```bash
npm install -g @0x-copilot/cli@latest  # (bun add -g @0x-copilot/cli@latest)
```

The next `copilot` re-checks the staged runtime and refreshes it if the CLI
version changed, so the app and services stay in sync.

## Uninstall

Removal has **two independent layers** — do both for a complete uninstall:

```bash
copilot uninstall          # 1. delete the staged runtime, download cache, and local app data
npm rm -g @0x-copilot/cli  # 2. remove the `copilot` / `0xcopilot` command itself
```

1. `copilot uninstall` clears everything the app _created_ on disk — the staged
   runtime (`~/.0xcopilot`), the download cache, and all app data (database,
   settings, keys, logs). It permanently deletes that data, so use
   `copilot repair` instead when you only need to recover a stuck launch (it
   keeps your data). Add `--yes` to skip the confirmation prompt. This step does
   **not** remove the `copilot` command — that is a separate npm package.
2. `npm rm -g @0x-copilot/cli` (or `bun rm -g @0x-copilot/cli`) removes the
   command. Order does not matter, but running `copilot uninstall` first lets it
   clean up while the command is still available.

### `copilot` still runs after `npm rm`?

If `copilot` still resolves in the **same** terminal after removal, the file is
gone but your shell cached its old location. Refresh the shell's command lookup:

```bash
hash -r        # zsh/bash: clear the cached command table (zsh also accepts `rehash`)
which copilot  # → should now report "not found"
```

Opening a new terminal window has the same effect. If `which -a copilot` still
finds a binary in a fresh shell, it is a different install (a separate npm
prefix, Bun, or an unrelated `copilot` on your `PATH`) — remove that one too.

## Troubleshooting

Start with `copilot doctor`, then `copilot repair` if the app is stuck. Use
`copilot repair --session` to clear sign-ins, or `copilot --force` to rerun
runtime setup. See the full
[troubleshooting guide](https://github.com/0x-copilot-dev/0x-copilot/blob/main/tools/cli/TROUBLESHOOTING.md)
for logs and recovery steps.

## Data locations

| Location                                   | Contents                             |
| ------------------------------------------ | ------------------------------------ |
| `~/.0xcopilot/`                            | Staged runtime and version marker    |
| `~/.cache/enterprise-desktop-runtime/`     | Verified download cache              |
| `~/Library/Application Support/0xCopilot/` | macOS app data, database, and logs   |
| `%APPDATA%\0xCopilot\`                     | Windows app data, database, and logs |

Set `COPILOT_HOME=/path` to move the staged runtime. The download cache and app
data remain in their platform-default locations.

## Links

[Project](https://github.com/0x-copilot-dev/0x-copilot#readme) ·
[Source](https://github.com/0x-copilot-dev/0x-copilot/tree/main/tools/cli) ·
[Desktop architecture](https://github.com/0x-copilot-dev/0x-copilot/blob/main/docs/architecture/desktop-app.md) ·
[Issues](https://github.com/0x-copilot-dev/0x-copilot/issues) ·
[Security](https://github.com/0x-copilot-dev/0x-copilot/blob/main/SECURITY.md)

## License

[MIT](https://github.com/0x-copilot-dev/0x-copilot/blob/main/tools/cli/LICENSE)
© 0xCopilot
