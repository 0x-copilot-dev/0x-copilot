# @0x-copilot/cli

**Put your day on autopilot.**

[![ci](https://github.com/0x-copilot-dev/0x-copilot/actions/workflows/ci-cli.yml/badge.svg)](https://github.com/0x-copilot-dev/0x-copilot/actions/workflows/ci-cli.yml)
[![npm](https://img.shields.io/npm/v/@0x-copilot/cli?logo=npm&color=cb3837)](https://www.npmjs.com/package/@0x-copilot/cli)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/0x-copilot-dev/0x-copilot/blob/main/tools/cli/LICENSE)
[![platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey)](#requirements)
[![local-first](https://img.shields.io/badge/local--first-bring_your_own_key-6f42c1)](https://github.com/0x-copilot-dev/0x-copilot#readme)

0xCopilot is a local-first AI assistant for your desktop. Give it a goal and it
plans, works across your files and the apps you connect, and pauses for your
approval before it acts. Everything runs on your own machine — your machine,
your key, your model.

This package installs and launches the app from your terminal. One command, and
it's yours.

```bash
npm install -g @0x-copilot/cli
copilot
```

Using Bun? Install with `bun add -g @0x-copilot/cli`. The `copilot` command is
also available as `0xcopilot`.

## Requirements

- Node.js 20+
- macOS on Apple Silicon or Intel, or Windows x64
- Internet access for the first launch

The first launch downloads a few hundred MB; allow additional disk space for the
app and its data. Linux and Windows on ARM are not supported.

## Quick start

1. **Run `copilot`.** The first launch sets everything up, then opens the app.
   Later launches start straight away.
2. **Sign in.** Connect a wallet, or continue with Google if it is enabled for
   your setup.
3. **Add your model key.** Open **Settings → Models & keys → Provider keys** and
   add your own OpenAI, Anthropic, or Google key. It stays on your machine.

That's it — give Copilot a goal and it gets to work.

## Commands

| Command             | Description                                                  |
| ------------------- | ------------------------------------------------------------ |
| `copilot`           | Prepare the runtime when needed, then launch the app.        |
| `copilot start`     | Launch the app, equivalent to `copilot`.                     |
| `copilot install`   | Set up or refresh everything without launching.              |
| `copilot doctor`    | Diagnose problems with your setup and print what it finds.   |
| `copilot repair`    | Recover from a stuck launch while keeping your data.         |
| `copilot uninstall` | Delete the app, its downloads, and all local 0xCopilot data. |
| `copilot help`      | Show command help.                                           |
| `copilot version`   | Print the installed version.                                 |

Flags:

- `--force`, `-f` — check and refresh the setup again before launch
- `--yes`, `-y` — skip the uninstall confirmation
- `--session` — make `copilot repair` also clear saved sign-ins
- `--help`, `-h` and `--version`, `-v` — show help or version information

## Update

```bash
npm install -g @0x-copilot/cli@latest  # (bun add -g @0x-copilot/cli@latest)
```

The next `copilot` re-checks the app and refreshes it if the version changed, so
everything stays in sync.

## Uninstall

Removal has **two independent layers** — do both for a complete uninstall:

```bash
copilot uninstall          # 1. delete the app, its downloads, and your local data
npm rm -g @0x-copilot/cli  # 2. remove the `copilot` / `0xcopilot` command itself
```

1. `copilot uninstall` clears everything the app _created_ on disk — the app
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
`copilot repair --session` to clear sign-ins, or `copilot --force` to run the
setup again. See the full
[troubleshooting guide](https://github.com/0x-copilot-dev/0x-copilot/blob/main/tools/cli/TROUBLESHOOTING.md)
for logs and recovery steps.

## How it works

You don't need any of this to use 0xCopilot — it's here if you're curious about
what the CLI does on your machine.

Setup happens when you run `copilot`, not during package installation. The CLI
downloads and SHA-256-verifies pinned CPython and PostgreSQL builds, installs the
bundled services, and signs native binaries on macOS. Later launches reuse the
staged setup; upgrades check it again to keep the app and services in sync.

On macOS the CLI also prepares a **branded app shell** — a copy-on-write clone
of Electron.app at `~/.0xcopilot/shell/0xCopilot.app` with the 0xCopilot name,
bundle id, and icon, re-signed ad-hoc — and launches through it so the Dock
shows **0xCopilot** instead of "Electron". It is rebuilt automatically when the
bundled Electron or the icon changes, and removed by `copilot uninstall`. If
preparing it ever fails, the launch falls back to the stock Electron binary
(everything works; only the Dock name is generic).

## Data locations

| Location                                   | Contents                             |
| ------------------------------------------ | ------------------------------------ |
| `~/.0xcopilot/`                            | App runtime and version marker       |
| `~/.cache/enterprise-desktop-runtime/`     | Verified download cache              |
| `~/Library/Application Support/0xCopilot/` | macOS app data, database, and logs   |
| `%APPDATA%\0xCopilot\`                     | Windows app data, database, and logs |

Set `COPILOT_HOME=/path` to move the app runtime. The download cache and app
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
