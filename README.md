# 0xCopilot

A desktop AI agent that runs on your own computer, with your own model keys.

[![ci](https://github.com/0x-copilot-dev/0x-copilot/actions/workflows/ci-cli.yml/badge.svg)](https://github.com/0x-copilot-dev/0x-copilot/actions/workflows/ci-cli.yml)
[![npm](https://img.shields.io/npm/v/@0x-copilot/cli?logo=npm&color=cb3837&label=%400x-copilot%2Fcli)](https://www.npmjs.com/package/@0x-copilot/cli)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey)](tools/cli#requirements)

Give 0xCopilot a goal. It plans the work, uses your files and the tools you
connect, asks before it does anything you have not allowed, and saves what it
makes as documents, code or data you can keep.

Most AI assistants run in someone else's cloud and charge you per seat.
0xCopilot runs the whole app on your machine. You pay your model provider
directly, at their own prices, or you run a local model and pay nothing. The
app, your chat history and your keys stay on your computer. Only what a task
needs goes to the model you chose.

![A 0xCopilot run that wrote a checklist document, with the chat alongside](apps/website/public/media/studio-run.png)

## Quickstart

You need Node.js 20 or newer, on macOS (Apple Silicon or Intel) or Windows x64.

```bash
npm install -g @0x-copilot/cli
copilot
```

Using Bun? Install with `bun add -g @0x-copilot/cli` instead.

The first launch sets up the app's local runtime. It downloads several hundred
MB and takes a few minutes, so allow about 2.5 GB of free disk space. Later
launches start straight away.

When the app opens:

1. **Sign in.** Choose **Use locally, no account** to keep everything on this
   device, or sign in with a wallet.
2. **Add a model.** Open **Settings → Models & keys → Provider keys** and paste
   an API key. To run a model on your own hardware instead, open
   **Local models**.
3. **Give it a goal.** For example: _"Draft a one-page pre-launch checklist for
   shipping a desktop app, grouped by phase, and save it as a document I can
   keep."_

## What it does

- **Works with the model you choose.** Bring a key from OpenAI, Anthropic,
  Google, OpenRouter or Virtuals, or connect any OpenAI-compatible endpoint.
  Local models run through [Ollama](https://ollama.com), with no key at all.
- **Makes cost visible.** The model list shows each model's price per million
  tokens, and a meter in the message box shows how much of the model's context
  a chat is using.
- **Connects to your tools.** 0xCopilot works with MCP (Model Context Protocol)
  servers, and handles the OAuth sign-in for servers that support it.
- **Asks before it acts.** You set a policy for three kinds of action: reading,
  writing, and anything that spends money or cannot be undone. Writes can run
  automatically, ask first, wait for approval, or be blocked. Spending and
  destructive actions always need your approval, or are blocked.
- **Keeps what it makes.** Documents, code and datasets are saved as artifacts
  that you can edit in place and download. Every revision is kept.
- **Keeps your work organised.** Save reusable skills, group chats into
  projects, and review everything a run did in the Activity view.

## Where your data goes

Nothing is hosted. The app runs its own database and services on your machine,
and there is no 0xCopilot server between you and your model provider.

- Your prompts and context leave your machine only for the model provider or
  tool that a task uses.
- Provider keys and tool sign-ins are encrypted on your disk.
- In Settings you can also encrypt your chat history, lock the app after a
  period of inactivity, and on a Mac require Touch ID to open it.

## Everyday commands

| Command             | What it does                                     |
| ------------------- | ------------------------------------------------ |
| `copilot`           | Start the app, setting it up first if needed.    |
| `copilot doctor`    | Check the install and report any problems.       |
| `copilot repair`    | Recover from a stuck launch. Keeps your data.    |
| `copilot uninstall` | Delete the app and all of its data on this disk. |

To update, run `npm install -g @0x-copilot/cli@latest`. To remove the
`copilot` command itself, run `npm rm -g @0x-copilot/cli` after
`copilot uninstall`. The [CLI guide](tools/cli/README.md) covers every command
and flag, and where the app keeps its files.

## Documentation

- [CLI guide](tools/cli/README.md): install, update, uninstall and data
  locations
- [Troubleshooting](tools/cli/TROUBLESHOOTING.md)
- [The desktop app, for developers](apps/desktop/README.md)
- [Architecture](docs/architecture/workspace-topology.md) and
  [service boundaries](docs/architecture/service-boundaries.md)
- [Google sign-in setup](docs/deployment/google-oauth-setup.md) and
  [wallet sign-in](docs/deployment/wallet-login.md), for builds you run
  yourself
- [Harness benchmark](tools/harness-bench/FINDINGS.md): what we measured,
  including the results that did not hold up

## Contributing

Pull requests go to the `dev` branch, not `main`.
[CONTRIBUTING.md](CONTRIBUTING.md) explains the branch flow, the checks a pull
request must pass, and how releases work. AI coding agents should read
[AGENTS.md](AGENTS.md) or [CLAUDE.md](CLAUDE.md).

Questions, bug reports and feature requests go to
[GitHub Issues](https://github.com/0x-copilot-dev/0x-copilot/issues). Please
report security problems privately, as the [security policy](SECURITY.md)
describes.

## From Kleos Research

0xCopilot is built by [Kleos Research](https://kleosresearch.xyz). Our other
project is [Kaleidoscope](https://memory.kleosresearch.xyz), memory for AI
agents that stays on your own disk. Its results are published in
[Optimising for memory recall](https://kleosresearch.xyz/research/optimising-for-memory-recall.pdf).

## License

[MIT](LICENSE) © 0xCopilot
