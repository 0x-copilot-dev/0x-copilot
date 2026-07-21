# First-Run — parity spec (distilled from canonical source)

**Canonical source of truth:** Claude Design project `Copilot` (`73f810d9-7b77-4849-9087-f7f8e366c48a`), files `copilot-firstrun.jsx`, `copilot-firstrun.css`, shared `copilot.css` / `copilot-v3.css`. Re-fetch via DesignSync for pixel checks. This file is the implementer-facing distillation; where they disagree, the DesignSync source wins.

> The mock renders inside a preview "mock window" (`.stage`/`.mw` with traffic-light chrome). That harness is **preview-only** — the real Electron app IS the OS window. Build only the inner `.fr` surface.

> **v1 scope note:** the **hosted-trial escape hatch and the "Haiku starter" trial model row are SHELVED** — omit them in v1. Everything else in this spec is in scope. If the trial is revived it becomes a ≥50k $CPILOT holder path (README §7.1), not the open no-key trial drawn here. The trial copy below is retained as the design reference.

## State machine (`FirstRunApp`)

State vars: `stage ∈ {choice, dl, ready}`, `engine ∈ {null, {kind:local}, {kind:key,name,color}, {kind:trial}}`, `pct` (download %), `keyOpen`, `draft`, `atts[]`, `open ∈ {null,attach,model,tools,key}`, `webOn` (default true), `conn[]`, `sent`.

- `choice` → renders **Gate**. `Start download` → `engine=local, pct=2, stage=dl`. `Add a key`→inline `KeyForm`; `Connect`→`engine=key, stage=ready`. Trial hatch → `engine=trial, stage=ready`.
- `dl` → renders **Composer** with model pill showing `Qwen 3 4B · N%`; `pct` ticks ~+1–2.7 every 240ms; at 100 → `stage=ready, engine=local`.
- `ready` → **Composer**.
- `sent` (after send, any ready/dl) → **Acknowledgment**; after ~1.5s → navigate to workspace.
- Top-bar `skip` → navigate to workspace immediately.

## Copy strings (verbatim — must match)

- Gate H1 `First, give it a model.` · sub `The only required choice — switch anytime.`
- Local card: `Download the local model` · meta `Qwen 3 4B · 5.6 GB · free forever` · body `Runs on this machine. Nothing you send ever leaves it.` · btn `Start download` · note `type your first prompt while it downloads`
- Key card: `Bring your own key` · meta `Anthropic · OpenAI · OpenRouter` · body `Frontier models, ready in ~30 seconds. Keys stay in your OS keychain.` · btn `Add a key`
- KeyForm: input placeholder `sk-…  paste your API key` · note `stored in your OS keychain — never uploaded` · btn `Connect`
- Trial hatch: `just exploring? hosted starter — 25 free runs, no key →`
- Composer H1 `What should we run first?` · textarea placeholder `Tell it what you want in plain words — "watch my wallet", "draft the thread"…` · hint `⏎ send · ⇧⏎ line`
- Ack: `Starting your first run` / `Queued — starts when the model lands`; lines `model — {name}[ · downloading N%| · on-device]`, `tools — {web search|none}[ · {connector}…]`, `key in your OS keychain` | `nothing leaves this machine`
- Footer left `v2.1.0 · local build`; right = `keys in OS keychain · runs via your provider` | `hosted starter · 25 free runs` | `nothing leaves this machine`
- Top bar: brand `0xCopilot` (`0x` in accent), wallet chip `0x7f3C…a92C` (jade dot), `skip — open the workspace →`

## Data

- BYO providers: `Anthropic → Claude Sonnet 4.5 (#d97757)`, `OpenAI → GPT-5.2 (#6aa88f)`, `OpenRouter → 200+ models (#9a7fd6)` (dot colors are swatches, not the app accent).
- Model popover groups: **Local** (`Qwen 3 4B` — states `5.6 GB · download from Settings` / `downloading · N%` / `on-device · ready`), **trial** (`Haiku starter · hosted · 25 free runs`), **Bring your own key** (3 providers → `add key →`).
- Tools popover: `Web search` (built-in, toggle, default on) · connectors `Safe{Wallet}` (propose & sign transactions) / `Google Sheets` (read & write workbooks) / `GitHub` (repos, issues, PRs) — each 1-click, `connected` on select · `Custom MCP server` (paste a JSON config). Header meta `{n} on · none required`, group note `1-click connect · you approve first use`.
- Attach popover: `Upload from computer` (any file up to 100 MB) · `Capture screenshot` · group `Project files appear here once you have a project`.
- Starter chips (icon, title, prompt) — see README §1; `Explain a CSV` pre-attaches `airdrop-claims.csv`.

## CSS class inventory (`fr-*`, values via token map)

`.fr` (column, scroll) · `.fr-top` (brand/`.fr-wchip`/`.fr-skiplink`) · `.fr-main` (`width:min(640px,92%)`, centered) · `.fr-hero h1` (`600 23px/1.2 --disp`, `-.015em`) · `.fr-gate` (2-col grid) · `.fr-gcard` (radius 12, `--panel`/`--line`, `.ic` accent, `.meta` mono 9.5, `.note` mono 9) · `.gbtn`/`.gbtn--pri` (primary = `--accent` bg, `#0b0a0e`… use `--color-accent-contrast`) · `.fr-try` (mono 10 ghost) · `.fr-chips`/`.fr-chip` (pill, accent svg) · `.fr-kf` (`.prov` tri-toggle, password input mono, `.knote`) · `.fr-ack` (centered, `.ln` mono 10.5 jade check) · `.fr-foot` (mono 9.5 space-between).
Composer/popover classes (`.cmp`, `.cmp-pill`, `.pop`, `.pop-row`, `.ctog--sm`, `.spin`) are shared app chrome in `copilot-v3.css`/`copilot.css` — reuse the real `AssistantComposer`/`ModelPill`/`ToolPicker`, do not re-author.

## Parity acceptance (per state)

Measured against `design-source` via `ui-design-reviewer`: hero size/weight/tracking, card radius/padding/hairline, primary-button contrast, pill/popover geometry, mono-label sizes, jade/accent usage, footer alignment. No second accent; sky-only.
