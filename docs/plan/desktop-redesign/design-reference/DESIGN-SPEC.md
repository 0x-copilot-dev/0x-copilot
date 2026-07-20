# 0xCopilot Desktop — Design Spec (v2 "quiet")

Distilled from the Claude Design prototype (`copilot.css` v2 + `copilot-{app,data,settings,flows,workspace}.jsx`), with locked product decisions applied. **This is the design source of truth for PRD authoring.** Exact hex/px are authoritative for UI/UX acceptance checks. Where a decision overrides the prototype, it is flagged **[DECISION]**.

> Product framing: local-first agent workspace — give it a goal, it does multi-step work across your files and connected apps, and shows every step so you can watch, rewind, and stop it before it acts. Most users run it solo on their own machine with their own model key.

---

## 0. Tokens & dimensions (fold into `packages/design-system`)

**Fonts** — `--font-display` & `--font-sans` = system stack `-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, sans-serif`; `--font-mono` = `"JetBrains Mono", ui-monospace, SFMono-Regular, monospace` (metadata only). Base 13px / line-height 1.5. Headings `--font-display`, weight 600, letter-spacing −.01em.

**Dark neutrals** — `--ink #09090b` · `--ink2 #0d0d10` · `--panel #111114` · `--panel2 #16161a` · `--panel3 #1d1d23`; body backdrop `#050506`. **Hairline borders** `--line rgba(255,255,255,.06)` · `--line2 .10` · `--line3 .18`. Text `--tx #ececf1` · `--tx2 #d4d4db` · `--mut #98989f` · `--mut2 #64646d`.

**Light** — `--ink #f4f4f6` · `--panel #ffffff` · `--tx #141419` · lines `rgba(10,10,14,.07/.12/.22)`.

**Accent (ONE) [DECISION: single-accent discipline]** — `--sky #5fb2ec` is the only accent (`-lo #4593d8`, `-hi #8cc8f4`, `--accent-ink #08131d`, `--accent-soft rgba(95,178,236,.10)`, `--accent-line rgba(95,178,236,.35)`). Options via `[data-accent]`: jade `#57c785`, ember `#f0764f`, violet `#a98be0`. **Semantic-only colors:** jade = live/success, ember = destructive, amber `#e8b45e` = warning. **All decorative/brand color (connector logos, timeline lane colors) is neutralized to `--panel3`/`--tx2` monochrome.**

**Radii** `--r 8` · `--r-lg 12` · `--r-sm 6`. **Spacing** `--pad 13` · `--gap 10`; `[data-density=compact]` 10/7, `[data-density=spacious]` 18/14. `[data-reduce-motion=1]` zeroes animation/transition durations. Focus ring `2px solid var(--accent)` offset 2.

**Window/shell dims** — window `.mw` 1220×840, radius 12; titlebar `.mw-bar` 38px (traffic-light dots are prototype chrome — real app uses OS chrome, see [DECISION]). Rail 48px. Topbar 46px. Studio side panel 340px. Settings nav 216px. Modal 500px. Command palette 540px. List page max-width 960px, content max 620px.

---

## 1. Shell (Phase 2)

- **App rail** (48px, `--ink2`, right hairline): brand mark button (32×32, transparent, → Run) top; **icon-only** destination buttons (34×34, no text label — `.rl{display:none}`), active = `--panel2` bg + 2px accent bar on the left edge, optional badge (accent bg, mono, e.g. Run "1"); foot = Settings gear + avatar `.rail-me` (26px circle).
- **Destinations [DECISION: 6, profile-gated]** in order: **Run** (icon `run`, live badge), **Chats** (`chats`), **Projects** (`folder`), **Activity** (`activity`), **Tools** (`plug`), **Skills** (`skill`). Settings (`gear`) + avatar in foot. Team-profile adds Team/Members/Billing.
- **Topbar** (46px): title (13.5px semibold) + subtitle (11.5px muted) left; right = search/command trigger (`⌘K`, 250px). **Suppressed on Run and Settings** (they own full height).
- **Main** column fills remaining space; destination content mounts here.

---

## 2. Run cockpit (Phase 3) — the flagship

**Header `.ws-head`:** agent avatar (Mark), "ACTIVE RUN" mono kicker + goal `<h2>`, and a right-aligned **mode segmented control**.

**Modes [DECISION: Studio + Focus only — Auto dropped]:**

- **Studio** (default): `grid-template-columns: 1fr 340px` — work surface left, chat/plan side panel right, timeline at the bottom.
- **Focus**: side panel centered (max 780px), **work surface hidden**, **timeline minimized**; approvals appear as inline confirmation cards in the conversation.
- ~~Auto~~ **removed**: autonomy is a _run state_ (the agent runs and pauses for approvals regardless of view). The prototype's `[data-mode=auto]` surface-only view and `.ws-autobar` are dropped.

**Work surface `.ws-sheet`** (center): tab strip per active surface, then the surface renderer. Prototype example lanes were Safe/Sheets/X/Discord; real surfaces come from `surface-renderers` via `TcSurfaceMount` (URI-scheme-resolved). Per-artifact behaviors:

- **Structured table (e.g. a sheet):** columns header (`.sheet-h`, mono uppercase) + rows (`.srow`). Editable/staged rows get **inline approval**: pending row shows `Reject` / `Approve & sign` (or `Approve`); resolved → `✓ Signed` (jade) / `Rejected` (ember) / `Queued` (muted). Pending row is highlighted (`.ghost`, accent-soft, inset accent bar).
- **Streaming prose (e.g. a draft):** streams incrementally with a blinking cursor + a `streaming · N%` chip.
- **Viewing (scrubbed) state:** `.sheet-banner` "Viewing 11:43 · X thread — …. Return to live →"; approvals hidden while scrubbed.

**Side panel `.ws-side`** (chat/plan, right in Studio; center in Focus): agent header (name, "model · your key", `● working` chip); message list (`.msg.you` right bubble, `.msg.bot` with mono `WHO` kicker); **plan** (`.plan-step` done ✓ jade / pending spinner / future chevron); **Focus-mode confirmation card** `.conf-card` (header "Approve payout batch · N/M signed", per-row recipient/amount/status with inline Approve/Reject, footer note "The agent paused here — it won't sign until you approve"); compose box "Steer the run…".

**Timeline `.tl`** (bottom; minimized in Focus): head row = `LIVE`/`VIEWING` label + mono clock + subtitle; controls = Rewind (`⌘←`), Step forward (`⌘→`), **Live** (snap-to-now, `⌘L`), Minimize. Lanes row (`repeat(4,1fr)` mono labels — **lane color neutralized**). Track (96px) with **beads** per event (neutral; `.now` = jade pulse, `.cur` = accent + ring, `.future` = hollow), lane-lines, and a draggable **head-line** (2px accent). Scrub mechanics: click/drag sets playhead; `snapSet` snaps to a bead within threshold and switches the surface tab to that bead's surface; scrubbing off-now reveals the viewing banner and hides approvals.

**Parallel subagents [3 surfaces]:** inline **fleet card** ("Dispatched N subagents in parallel", nested child rows) in the conversation; **timeline lanes** (one live track per subagent/surface); **Agents** tab in the right rail (detail, live count).

**Right rail (Studio) [DECISION]:** tabbed `Chat · Sources · Agents · Approvals` (Chat default) — the production `WorkspacePane` tabs, since chat is the right rail and the artifact is center-stage.

**States to design (prototype gap):** Run **empty/idle** (no active run) and **multi-run** selection.

---

## 3. List destinations (Phase 4)

Shared surface: `.pg` (max 960) with `.pg-lead` intro; `.sect-h` mono uppercase section headers; `.rowlist`/`.lrow` (neutralized logo 30px or icon 28px, name 12.5px, mono sub, mono time); `.grid2`/`.grid3` cards; `.act-day` day dividers. All list destinations need the 4-state machine: **loading (skeleton) / error (+Retry) / empty (per-view copy) / ready**.

- **Chats** — pinned / recent / archived sections; row = title + status chip (running/done/paused/archived) + preview + mono model + time; "New chat" → Run. Reopen → Run.
- **Projects** — card grid (name, desc, N chats · N files) → detail (chats list + files list).
- **Activity [recast of audit log + agents + inbox]** — grouped by day; run rows (title, meta, time, status running/done/paused/stopped); live run → Run. Copy: "Everything the agent has done… Retention, export, and delete live in Settings → Privacy."
- **Tools (=connectors)** — connected list with per-tool segmented `Read / Read & act / Off`; "Connect a tool" → ConnectModal. Copy: "The apps the agent can read from and act through — a destination, not a settings tab. The approval _policy_ lives in Settings → Model & behavior." **[DECISION: generic-SaaS-first catalog — Notion/Linear/Slack/Google Calendar/Drive/GitHub/Stripe…; Safe/Dune are prototype dressing, not defaults.]**
- **Skills (=skill catalog)** — card grid (name, sub, N runs), Run/Edit, "New skill". Copy: "Saved multi-step workflows you can re-run in one click — their own place, not a settings tab."

---

## 4. Settings (Phase 5) — solo, profile-gated

**Layout `.set`:** 216px nav + content (max 620). Nav groups + items (icon, label, optional mono tag):

- **Account** — Profile (`user`) · Appearance (`sun`) · Shortcuts (`cmd`)
- **Models & keys** — Provider keys (`key`, tag "BYOK") · Local models (`chip`) · Model & behavior (`sliders`)
- **Data & privacy** — Privacy & retention (`shield`)
- **Notifications** — Notifications (`bell`)
- **Advanced** (collapsible) — Key storage & app lock (`lock`) · Developer tokens (`bolt`)
- **Footer [DECISION: profile gate]:** "Solo desktop mode. Workspace, members & billing appear only when 0xCopilot is deployed for a team."

Content chrome: `.set-card` (head h3 + meta) · `.set-note` (inset note w/ icon) · `.frow` (label/hint + control) · `.krow` (logo + name + sub + actions) · `.savebar` (dirty → "Unsaved changes" Discard/Save; action → toast). Controls: `.ctog` toggle, `.csel` select, `.cin` input, `.seg` segmented, `.swatch` accent dot, `.theme-tile`, `.bar` progress.

**Sections & fields:**

- **Profile** — Display name (input); Working hours (select 9:00–18:00 / Anytime / Custom…); Time zone (select); Cloud sync (toggle, **off by default**, "runs fully local; nothing leaves this device"; requires free account).
- **Appearance** — Theme tiles (Dark / Light / System "Match macOS"); Accent swatches (sky/jade/ember/violet); Density (Comfortable/Compact/Spacious); Reduce motion (toggle).
- **Shortcuts** — read-only grid of the shortcut set (§6).
- **Provider keys (BYOK)** — Connected list (logo, name + model chip, masked key, Rotate / Remove); Add-a-provider list (empty providers → "Add key"); "Another provider — **Any OpenAI-compatible endpoint works too.**" Note: "Keys are encrypted at rest in your local vault and never sent to a 0xCopilot server." _(amended: keys are TokenVault-encrypted in the local DB, not the macOS Keychain — keychain protection is the #124 opt-in)_ Providers: Anthropic, OpenAI, OpenRouter, Google AI, Groq, xAI.
- **Local models** — Installed list (jade chip logo, name·param, "default local" chip, size, Run / Delete); "Get another model → Download". Note: "Powered by your local runtime (**Ollama**). Inference uses your GPU/CPU — private and offline."
- **Model & behavior** — Default model (select, optgroups **Cloud · your keys** / **Local · your machine**); Reasoning depth (Auto/Quick/Standard/Deep); Web access (toggle). **Approval policy** (note: "_which_ tools = per-connector on the Connectors page"): Read-only actions (Auto-approve / Ask first); Write actions (Require approval / Ask first / Auto-approve / Block); On-chain, spend & destructive (Require approval / Block). **Spend guardrail:** Monthly API cap ($ input, "across all provider keys"); Pause runs at cap (toggle).
- **Privacy & retention** — note "every run/step recorded to local history; full record on the **Activity** page"; Keep run history for (Forever/90/30/7 days); "Open Activity" (button); Memory (toggle + "Review N memories →"); Export everything (→ `~/copilot/export`); Delete all history (danger).
- **Notifications** — per-event × channel grid (**desktop / sound / email**) for: Approval requested, Run finished, Run paused / needs input, Connector error, Spend threshold, Product updates; Quiet hours (toggle, "approval requests always break through"). Copy: "One place for every alert — replacing the three overlapping notification panels in the old build."
- **Key storage & app lock** — note "keys in macOS Keychain, encrypted at rest"; Encrypt local run history (toggle); Require Touch ID to open (toggle); Lock after (5 min / 15 min / 1 hour / Never).
- **Developer tokens** — local CLI token list (name, masked, last-used, Revoke); Create a token ("shown once, then keychain").

---

## 5. Modal / flow patterns (Phases 3–5)

`.scrim` + `.modal` (500px): head (logo + title + mono subtitle + close ×), body, foot (StepDots + actions). Reusable multi-step flows:

- **Add provider key** — [choose provider (if not preset)] → enter key (`sk-…`, validating spinner "Validating with {provider}…") → choose default model (per-provider `MODELS`) → Add. 3 StepDots.
- **Download local model** — pick from available (name·param, size·note, download icon) → progress bar (`%`) → "Ready to run locally" + "Use as default local model" toggle → Finish.
- **Connect a tool** — pick from catalog → OAuth spinner ("Authorizing with {tool}… approve in the window") → permission (Read only / Read & act) → Connect.

Command palette `.cmdk` (540px): search input + result rows (icon, label, mono key). Empty state "No matches."

---

## 6. Command palette + shortcuts (Phase 6)

Palette entries: Go to Run / Chats / Projects / Activity / Tools / Skills; New chat; Add a provider key; Download a local model; Connect a tool; Model & behavior; Appearance; Open Settings.

Shortcuts: New run `⌘N` · Command palette `⌘K` · Approve action `⌘↵` · Reject action `⌘⌫` · Pause run `⌘.` · Rewind timeline `⌘←` · Step forward `⌘→` · Jump to live `⌘L` · Switch mode `⌘M` · Local model picker `⌘⇧M` · Settings `⌘,` · Search activity `⌘⇧F`.

---

## 7. Icons & brand mark

Custom stroke icon set (24px grid, stroke 1.7, round caps): `run, activity, plug, skill, gear, search, plus, check, x, chevR, chevD, back, key, chip, sliders, lock, bell, user, sun, cmd, trash, download, external, warn, send, stepBack, stepFwd, globe, refresh, eye, folder, chats, doc, clock, shield, pause, play, dots, coin, bolt, arrowR`. **Mark** = 6-blade turbine/asterisk, sky gradient (`#9bd4ff→#4593d8`), dark center circle. Favicon = same mark.

---

## 8. Data entities (mock → real via transport)

Prototype `window` globals model the real domain (all served over transport in production, never hardcoded): `PROVIDERS` (BYOK), `LOCAL_INSTALLED`/`LOCAL_AVAILABLE` (models), `CONNECTORS`/`CONNECTOR_CATALOG`, `ACTIVITY` (runs), `NOTIF`, `SHORTCUTS`, `CHATS`, `PROJECTS`/`PROJECT_FILES`, and run internals `LANES`/`BEADS`/`STAGED`/`PLAN`. Treat these as the shape reference; real payloads come from the facade `/v1/*` endpoints.

---

## 9. Decisions overlay (authoritative over the prototype)

1. **6 destinations**, profile-gated (§1). 2. **Run its own destination**; **Auto dropped** → Studio/Focus (§2). 3. **design-system = single token source**; fold v2 values in (§0). 4. **Solo default**, team gated behind `ENTERPRISE_DEPLOYMENT_PROFILE`. 5. **Generic-SaaS connectors** (Safe/Dune/USDC = dressing). 6. **Tools/Skills/Connectors** renaming. 7. **Single accent** sky; jade=live/success, ember=destructive; neutralize decorative color. 8. **System fonts** (mono for metadata). 9. **(a) SSOT:** production chat components hoisted into `chat-surface`, consumed by web + desktop — no `apps/*`→`apps/*` imports; `chat-surface` stays framework-agnostic (ports only). 10. **Traffic-light chrome** is prototype-only; real app uses native OS window chrome.
