# Typography: a decision document

**Status:** open — awaiting a decision. Nothing here is implemented.
**Decision owner:** the user. This document does not recommend; it prices three options.
**Scope:** `packages/design-system/src/styles.css` type tier, and the two consumers
that mount it (`apps/desktop/renderer/bootstrap.tsx:1`, `apps/frontend/src/app/App.tsx:5`).

The trigger was a one-line complaint — _"the fonts look off, it's not aesthetic
enough."_ The response so far was to keep the native system stack and to discover
that six vendored `woff2` files cannot be deleted because two design-parity suites
`copyFileSync` them by name. Neither of those answered the complaint. This document
is the material for answering it.

Every claim below carries a `file:line`. Where I could not verify something, it says
**unverified** rather than guessing.

---

## 1. What ships today

### 1.1 Faces

Three role tokens, defined once, at `packages/design-system/src/styles.css:47-53`:

```css
--font-display:
  -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui,
  sans-serif;
--font-sans:
  -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui,
  sans-serif;
--font-mono: "JetBrains Mono", ui-monospace, SFMono-Regular, monospace;
```

`--font-display` and `--font-sans` are **byte-identical**. There is no display face:
headings (`styles.css:653-658`) resolve to the same family as body text.

**One vendored face.** JetBrains Mono only, self-hosted as a variable `woff2`, split
latin / latin-ext by `unicode-range`, `font-display: swap`
(`styles.css:18-40`). Six further `woff2` files sit in `packages/design-system/src/fonts/`
— four Instrument Sans, two Space Grotesk — with **no `@font-face` rule anywhere in
the repo referencing them** (verified: the only `space-grotesk`/`instrument-sans`
string hits outside `node_modules` are two parity harnesses and one audit finding).

**Usage split.** Counted with this exact command, from the repo root:

```bash
for f in mono sans display; do
  printf '%-8s %s\n' "$f" "$(grep -rho --include='*.css' --include='*.ts' --include='*.tsx' \
    --exclude-dir=node_modules --exclude-dir=dist \
    -- "var(--font-$f)" packages/*/src apps/desktop/renderer apps/frontend/src | wc -l)"
done
# mono 221   sans 63   display 19
```

`var(--font-mono)` **221** · `var(--font-sans)` **63** · `var(--font-display)` **19**
— 303 family references, 73% of them mono. Mono is not a code-block accent in this
product — it is the dominant chrome face. Metadata, section heads, chips, timestamps,
rail badges and ⌘K rows are all mono
(`styles.css:963, 1022, 1101, 1120, 1147, 1175, 1214, 1225, 1330, 1454` …).

**On the counting basis**, because an earlier draft of this document reported
241 / 66 / 25 and those numbers do not reproduce. The scope above is deliberate and
narrow: `packages/*/src` plus the two host renderers, `.css`/`.ts`/`.tsx` only,
`node_modules` and `dist` excluded, `.md` excluded, **test files included** (a
`.test.tsx` asserting `var(--font-mono)` is asserting about a real call site). One
knob moves these numbers materially and is worth knowing before quoting them:
counting the bare token `--font-mono` instead of the `var(…)` form adds the
definitions and the token-name string literals in tests (**250 / 68 / 26**,
re-run 2026-08-27). The `var(…)` form is the one that answers "how many places
consume this token."

An earlier draft also quoted a `dist/` variant (396 / 123 / 37). That number is
withdrawn rather than corrected: **no `dist/` directory exists** under
`packages/*/src`, `packages/*/`, `apps/desktop/renderer` or `apps/frontend/src`,
so including or excluding it changes nothing and the knob was never real. A
paragraph whose whole job is to establish the counting basis cannot afford a
number nobody can reproduce.

### 1.2 The scale

Sans ladder, `styles.css:69-77` — with the resolved pixel value. The `var()` uses
column is the same command as §1.1, one `sort | uniq -c` away:

```bash
grep -rho --include='*.css' --include='*.ts' --include='*.tsx' \
  --exclude-dir=node_modules --exclude-dir=dist \
  -E -- 'var\(--font-size-[a-z0-9-]+\)' packages/*/src apps/desktop/renderer apps/frontend/src \
| sort | uniq -c | sort -rn      # 829 total across all 16 rungs
```

| Token             | rem       | px        | `var()` uses |
| ----------------- | --------- | --------- | ------------ |
| `--font-size-3xs` | 0.5625rem | 9         | 23           |
| `--font-size-2xs` | 0.7rem    | **11.2**  | 184          |
| `--font-size-xs`  | 0.78rem   | **12.48** | 266          |
| `--font-size-sm`  | 0.8125rem | 13        | 167          |
| `--font-size-md`  | 0.875rem  | 14        | 49           |
| `--font-size-lg`  | 1rem      | 16        | 21           |
| `--font-size-xl`  | 1.125rem  | 18        | 9            |
| `--font-size-2xl` | 1.4rem    | **22.4**  | 11           |
| `--font-size-3xl` | 2rem      | 32        | 2            |

Those nine sans rungs are 732 of the 829; the remaining 97 are the mono and
whole-pixel rungs below (`mono-9-5` 35, `mono-10` 29, `mono-10-5` 14, `mono-8-5` 12,
`12` 3, `11` 3, `13` 1). The px column assumes a 16px rem anchor, which the body
comment states explicitly is preserved: _"It deliberately does NOT touch the rem
anchor (`html`/`:root` stays at the UA 16px)"_ (`styles.css:639-640`). Verified: no
`html` or `:root` rule in `packages/*/src` or either host renderer sets `font-size`,
and the compact-density block (`styles.css:182-188`) changes spacing and line-height
only.

A separate **mono micro-ladder** exists in half-pixel steps, named by pixel value
(`styles.css:93-96`): `8-5` / `9-5` / `10` / `10-5`. Plus three whole-pixel
composer/popover steps `13` / `12` / `11` (`styles.css:102-104`). Weights are the
standard four rungs (`styles.css:106-109`); tracking is an eight-step scale
(`styles.css:118-125`); line-height four steps (`styles.css:127-130`).

Body base is `--font-size-sm` = 13px, set on `body` at `styles.css:646` with a long
comment explaining that the token was **retuned from 0.85rem (13.6px) to 0.8125rem
(13px)** because 13.6 was "an approximation with no referent anywhere in the design,
and the single largest source of measured drift in the parity harness (55 rows
across five surfaces)" (`styles.css:626-636`). Hold that thought — §3.2 is the same
problem, unfixed, on three other rungs.

### 1.3 Bundle cost of the current system

Desktop inlines fonts as base64: `apps/desktop/esbuild.config.mjs:67-69` sets
`".woff2": "dataurl"`. On-disk sizes: `jetbrains-mono-latin.woff2` 40,404 B,
`jetbrains-mono-latin-ext.woff2` 15,196 B. Base64 inflates ~4/3, so the mono face
costs **≈74 KB inline** in the renderer bundle.

Note a wrinkle: with `dataurl`, the `unicode-range` split saves nothing on desktop —
both files are in the bundle whether or not a latin-ext glyph is ever rendered. On
web it does work: `apps/frontend/vite.config.ts` sets no `assetsInlineLimit`, so
Vite's 4096-byte default applies and both files exceed it, emitting as separate
fetchable assets. The split is web-only value carried as desktop-only cost.

---

## 2. The v2 argument, in its own words

This reasoning is real and deserves a fair hearing. `styles.css:1-4`:

> `v2 "quiet" system: --font-display and --font-sans resolve to the native platform UI stack (SF Pro Text on macOS, Segoe UI on Windows), so the app reads as calm, native chrome rather than a branded display face.`

And `packages/design-system/CLAUDE.md:83-91`:

> `The v2 "quiet" system (0xCopilot desktop redesign) is deliberately calm, native-feeling chrome rather than a branded display face` … `--font-display and --font-sans both resolve to the native platform UI stack (-apple-system, SF Pro Text, Segoe UI, system-ui) — there is no vendored display face.`

**The strongest form of this argument** — stronger than the comment makes it:

1. **The design mock agrees.** `tools/design-parity/design-kit/app-v3/copilot.css:33-39`
   defines `--disp` and `--body` as the identical native stack, and `--mono` as
   JetBrains Mono. The app is not deviating from the design. **The design is the
   native stack.** Any branded face is a deviation _from_ the committed spec, not a
   correction toward it.
2. **A desktop app that matches OS chrome disappears into the OS.** For a tool the
   user leaves open all day beside Finder and Terminal, that is a feature.
3. **Zero download, zero FOUT, zero licensing surface, zero bundle.** SF Pro is
   already resident, hinted for the display it is on, and has a genuine optical-size
   family — things a vendored webfont buys back at a cost.
4. **It is the only choice that cannot be wrong at 1x.** Any vendored face has to be
   hinted; the system face is hinted by the platform vendor for the platform.

That argument is sound and I do not think it is the thing that is broken. §3 argues
the complaint is about four other things, three of which survive the face choice.

---

## 3. What "the fonts look off" could actually mean

Four separable causes. Only one is the face.

### 3.1 The app renders text without antialiasing hinting; every design reference renders with it

`-webkit-font-smoothing: antialiased` is set on `body` in **every** design mock:

- `tools/design-parity/design-kit/app-v3/copilot.css:110`
- `tools/design-parity/design-kit/copilot.css:116`
- `tools/design-parity/surfaces/chat-tool-calls/design/copilot.css:110`
- `tools/design-parity/surfaces/generative-surfaces-v3/design/copilot.css:36`
- `tools/design-parity/surfaces/chat-tool-call-shell/design/copilot.css:36`
- `tools/design-parity/surfaces/surfaces-v2-canvas/design/copilot.css:110`

It is set in the desktop **boot splash** (`apps/desktop/renderer/BootProgress.css:34`).

It is **not set anywhere in `packages/design-system/src/styles.css`, `packages/chat-surface/src/*.css`, or `apps/frontend/src/styles.css`** — verified by
`grep -rn --exclude-dir={node_modules,dist,.git} font-smoothing .`, whose only hits
outside this document are the six mocks above, five `docs/plan/**` mock HTMLs
(`windowed-mode/mock-prd-01..03`, `generative-ui-floor/mock-status-strip` and
`-mock-email-and-quiet-chrome`), the boot splash, and four `apps/website`
stylesheets. **No shipped app stylesheet is among them.**

**The one consequence that is verifiable without running the app:** the desktop
renderer **changes its text rendering the moment the boot splash unmounts**, inside a
single document. `-webkit-font-smoothing` is inherited; the splash sets it on the
`.boot` wrapper (`BootProgress.css:7` opens the rule, `:34` sets the property),
`BootChrome` renders that wrapper as `<div className="boot">`
(`apps/desktop/renderer/BootProgress.tsx:163`), and once boot reaches `ready`
`BootGate` returns bare `children` with no wrapper
(`BootProgress.tsx:83-84`) into the same React root
(`apps/desktop/renderer/bootstrap.tsx:167-202`). Smoothed during boot, unsmoothed
after, same window. That is a real inconsistency at exact lines.

**What is _not_ verified is the part a reader would act on.** The usual story —
"subpixel-antialiased light-on-dark text is stem-darkened and reads heavier and
muddier" — is asserted from general knowledge and **I did not run the app or measure
a pixel**. Two things could make it much smaller than the story implies:

- macOS has not done subpixel (RGB) antialiasing system-wide since Mojave (10.14),
  so on any macOS this app actually ships to, `auto` may already resolve to
  grayscale, leaving `antialiased` little or nothing to change. What can still
  differ is Chromium's contrast/stem-darkening treatment, which is display- and
  OS-version-dependent.
- On Windows — the other shipping host, and the one running Segoe UI —
  `-webkit-font-smoothing` is a macOS-only Blink property and does **nothing at
  all**.

So §3.1 is a demonstrated divergence from the mock and from our own splash, with an
**unmeasured** aesthetic payoff. §6/B1 prices it on that basis and says how to
measure it before deciding.

**The parity gate cannot see this either.** `-webkit-font-smoothing` is not in
`DEFAULT_PROPS` (`tools/design-parity/lib/extract-computed.js:29-58`), so every
parity report to date has been blind to it.

### 3.2 The sans ladder cannot express the design, and 35 call sites already say so out loud

All 110 `font-size: <n>px` declarations in `design-kit/app-v3/copilot.css`, split by
whether the same rule block also declares `font-family: var(--mono)`. An earlier
draft printed only the **all faces** column and labelled it "the design's sans
sizes"; it is not, and the split matters, because the low half-pixel band turns out
to be almost entirely mono — which the design system **already models**.

```bash
perl -0777 -ne 'while (/\{([^{}]*)\}/g) { my $b = $1;
    next unless $b =~ /font-size:\s*([0-9.]+)px/;
    my $sz = $1;                                   # bind BEFORE the next match clobbers $1
    my $mono = ($b =~ /font-family:\s*var\(--mono\)/) ? "MONO" : "other";
    print "$mono\t$sz\n"; }' \
  tools/design-parity/design-kit/app-v3/copilot.css | sort | uniq -c
```

| px        | all faces | in a `var(--mono)` block | not mono |
| --------- | --------- | ------------------------ | -------- |
| 12        | 20        | 2                        | **18**   |
| 10.5      | 15        | 11                       | 4        |
| 11        | 14        | 6                        | **8**    |
| 12.5      | 12        | 0                        | **12**   |
| 10        | 10        | 8                        | 2        |
| 9         | 9         | 8                        | 1        |
| 11.5      | 8         | 3                        | **5**    |
| 9.5       | 7         | 7                        | 0        |
| 13.5      | 5         | 0                        | **5**    |
| 13        | 4         | 1                        | 3        |
| 8.5       | 2         | 1                        | 1        |
| ≥14       | 4         | 0                        | 4        |
| **total** | **110**   | **47**                   | **63**   |

Read the right-hand column, not the left. The 8.5 / 9 / 9.5 / 10 / 10.5 band is
mono, and `--font-size-mono-*` (`styles.css:93-96`) already covers it. **The design's
_non-mono_ register is 11 / 11.5 / 12 / 12.5 / 13 / 13.5, plus four sizes ≥14** —
still a half-pixel ladder, still one the rem t-shirt scale cannot express, but a
narrower and more specific claim than the earlier framing.

One caveat on the method, stated because it bounds the claim: "not mono" is an
**upper bound** on sans. A rule can inherit mono from an ancestor without
re-declaring `font-family`, so a few of those 63 are probably mono too. It cannot
run the other way — a block that names `var(--mono)` is mono.

Our sans ladder is a rem t-shirt scale whose stated derivation is _not the design_:
`styles.css:61` says **"Sizes hit the 5 most common values in the existing app (≈11/13/14/16/19 px)."** It was fitted to the app that preceded the redesign.

The mismatch is concrete:

- Design's most common size, **12px** (20 uses, 18 of them non-mono). Nearest sans
  rung: `xs` = **12.48px**. Off by 0.48px — above `compare.mjs`'s 0.4px threshold
  (`compare.mjs:337-343`), so it scores **MEDIUM** wherever an anchor lands on it.
- Design's **11px** (8 non-mono uses). Nearest rung `2xs` = **11.2px**. Off by 0.2px
  — _below_ the threshold, so it is real drift the gate is designed not to report.
- Design's **12.5px** (12 non-mono uses) is what `xs` currently renders, by accident
  rather than by declaration.
- Design's **11.5px** (5 non-mono uses) and **13.5px** (5 non-mono uses): **no rung
  exists.**

So components bail out to raw pixels. There are **35 raw-`px` `font-size` literals**
in shipped package CSS (`packages/design-system/src` + `packages/chat-surface/src`;
`.css` files only) — despite `packages/design-system/CLAUDE.md:32` mandating _"never
hard-code rems / pixels for `font-size`"_ and `SKILL.md:17` repeating it. Their
distribution matters, and separates two different problems: **ten are `11.5px`, the
one size on this list that no token covers**; the remaining 25 (`9` ×2, `9.5` ×2,
`10` ×5, `10.5` ×2, `11` ×2, `12` ×8, `13` ×4) all have a token already and are
discipline debt rather than a coverage gap. Only the first group is evidence that
the ladder cannot express the design.

**Eleven** of those 35 annotate the reason themselves — all eleven in
`packages/chat-surface/src/onboarding/onboarding.css`, at `:80, 162, 170, 176, 214,
456, 549, 667, 785, 804, 824`. The four that are `11.5px`:

```
packages/chat-surface/src/onboarding/onboarding.css:176   font-size: 11.5px; /* design-exact; off the type ladder */
packages/chat-surface/src/onboarding/onboarding.css:214   font-size: 11.5px; /* design-exact; off the type ladder */
packages/chat-surface/src/onboarding/onboarding.css:804   font-size: 11.5px; /* design-exact; off the type ladder */
packages/chat-surface/src/onboarding/onboarding.css:824   font-size: 11.5px; /* design-exact; off the type ladder */
```

The mono side **already solved this**. `styles.css:79-92` explains, verbatim, that
"the design's mono metadata register is a HALF-PIXEL ladder (8.5 / 9 / 9.5 / 10 /
10.5 / 11) that the sans t-shirt scale above cannot express, so these rungs sit
deliberately OFF the main ladder and are named by their px value." The sans side
never got the same treatment. Three whole-pixel escape hatches were later bolted on
for the composer and popover (`styles.css:102-104`) — the same fix, applied
piecemeal, twice.

**55.6% of all `var(--font-size-*)` references land on a rung that resolves to a
fractional pixel** — 266 `xs` + 184 `2xs` + 11 `2xl` = **461 of 829** (§1.2's
command). An earlier draft said 53% from 572 of 1,076; that arithmetic came from
counting the bare token name with tests excluded, and does not reproduce. The
conclusion is unchanged, and slightly stronger.

This is the cause a face swap does **not** fix.

### 3.3 The native stack silently swaps optical-size faces, and it has already caused a shipped bug

`packages/design-system/CLAUDE.md:69`:

> `That 100-weight gap reads as a different font family on macOS where SF Pro Text and SF Pro Display swap based on weight × size — root cause of the "+ menu vs GPT-5.4 Nano pill" mismatch.`

`-apple-system` is a _meta-family_. macOS chooses SF Pro Text or SF Pro Display by
optical size, and the app cannot pin it. In a UI whose entire type register lives in
the 8.5–13.5px band with weights spanning 400–700, that switch point is inside our
working range. The token discipline in `packages/design-system/CLAUDE.md:69` is a workaround for a property
of the face, not a bug in the components.

A vendored face with a single continuous variable axis has no switch point. That is
the strongest genuine argument _against_ native, and it is already documented in our
own repo as having cost us a visible defect.

### 3.4 The face itself

The mono-heavy register (221 `--font-mono` references vs 82 sans + display) plus a system UI
sans is a specific look: **terminal-adjacent, technically neutral, unbranded**. It
resembles a well-built internal tool. Whether that is "not aesthetic enough" is a
taste judgement, and the only one of the four that a face swap addresses.

I flag it as taste and do not argue it either way.

---

## 4. What the neighbours ship

### OpenCode (`/Users/parthpahwa/Documents/work/opencode`)

**Two type systems, and the newer one abandoned the OS stack.**

- v1: `packages/ui/src/styles/theme.css:2` —
  `--font-family-sans: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;`
  and `:4-5` a system-mono stack (`ui-monospace, SFMono-Regular, Menlo, Monaco, …`).
  Sizes are **whole pixels**, not rems: `--font-size-small: 13px` / `base: 14px` /
  `large: 16px` / `x-large: 20px` (`theme.css:8-11`).
- v2: `packages/ui/src/v2/styles/theme.css:143-144` —
  `--font-family-text: "Inter", sans-serif;` and
  `--v2-font-family-sans: "Inter", sans-serif;`

The v2 tokens are consumed by shipping app code
(`packages/app/src/components/prompt-project-selector.tsx`,
`dialog-connect-provider.tsx`, `dialog-select-model-unpaid-v2.tsx`).

They **self-host both faces**, and not carefully:
`packages/app/src/index.css:6-18` declares `@font-face` for
`"JetBrainsMono Nerd Font Mono"` → `/assets/JetBrainsMonoNerdFontMono-Regular.woff2`
and `"Inter"` → `/assets/Inter.ttf` at `font-weight: 100 900`. On disk:

- `packages/app/public/assets/Inter.ttf` — **874,708 B** (unsubsetted, uncompressed TTF)
- `packages/app/public/assets/JetBrainsMonoNerdFontMono-Regular.woff2` — **1,060,580 B**

That is ~1.9 MB of font for one desktop app. It is what "just ship Inter" looks like
when nobody subsets. Our two JetBrains files total 55,600 B.

### Hermes (`/Users/parthpahwa/Documents/work/hermes-agent/apps/desktop`)

**Native stack by default, with per-theme typography as a user-facing feature.**

- `src/themes/presets.ts:14-18` — `SYSTEM_SANS` is
  `"Segoe WPC", "Segoe UI", -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", system-ui, sans-serif` plus an emoji fallback;
  `SYSTEM_MONO` is `Menlo, Monaco, "SF Mono", "Courier Prime", monospace` plus emoji.
- `presets.ts:20` — `DEFAULT_TYPOGRAPHY = { fontSans: SYSTEM_SANS, fontMono: SYSTEM_MONO }`.
- Themes may override. `presets.ts:131` sets `fontMono: '"JetBrains Mono", …'`;
  `:168` sets `'"IBM Plex Mono", …'`; `:238-239` makes both sans and mono
  `"Courier New"` for a deliberately typewriter theme. Each carries a `fontUrl`
  pointing at Google Fonts (`presets.ts:132, 169, 96`) — **fonts fetched at runtime
  per theme**, not bundled.
- Bundled exception: `src/styles.css:38-57` self-hosts three static JetBrains Mono
  faces (Regular/Bold/Italic) with the reason stated inline —
  _"so bold/italic share the regular face's metrics instead of squeezing against a system fallback."_
- `src/styles.css:394` — `--dt-font-kbd` is pinned to the native stack with the
  comment _"Key caps always use the native UI face — never theme typography overrides."_
- `src/styles.css:561` — **`-webkit-font-smoothing: antialiased`**, inside the `body`
  block that opens at `:551`. (We do not. An earlier draft cited `:565`; re-verified.)
- Terminal font is a user setting (`src/app/settings/terminal-font-setting.tsx`).

### Read-across

|                    | faces                               | OS stack?         | bundled?                                             | scale unit    |
| ------------------ | ----------------------------------- | ----------------- | ---------------------------------------------------- | ------------- |
| **0xCopilot (us)** | native sans + JetBrains Mono        | **yes, for sans** | mono only, subsetted, 55.6 KB                        | rem           |
| **OpenCode v1**    | system sans + system mono           | yes               | no                                                   | **px**        |
| **OpenCode v2**    | Inter + JB Mono Nerd                | **no**            | yes, ~1.9 MB unsubsetted                             | px            |
| **Hermes**         | system sans + system mono (default) | **yes**           | JB Mono only; other faces via Google Fonts per theme | rem base + px |

Neither reference validates "everyone ships a branded face." One kept native and
made typography a theme setting; one moved to Inter for its newer surfaces and pays
1.9 MB for it. Both size in whole pixels or a whole-pixel base — neither has
fractional rungs.

**Both set `antialiased`, but not the same way**, and the difference matters for
B1. Hermes sets it once on `body` (`src/styles.css:561`) — global, the shape B1
proposes. OpenCode's desktop app sets it **per component**, in eight v2 kit
stylesheets (`packages/ui/src/v2/components/`: `button-v2.css:2`, `menu-v2.css:22`
and `:44`, `keybind-v2.css:2`, `tabs-v2.css`, `accordion-v2.css`,
`icon-button-v2.css`, `line-comment-v2.css`, `text-shimmer-v2.css`, each paired with
`-moz-osx-font-smoothing: grayscale`) — never on `body` or `:root` anywhere in
`packages/ui` or `packages/app`. So one neighbour is precedent for B1's exact
change; the other is precedent only for the property being worth setting at all.

---

## 5. The brand tension — surfaced, not resolved

This is the user's call and I will not decide it.

**The brand memory says Inter.** `project_0xcopilot_brand_and_promo.md:18`:

> `Type: **Inter** (display+body) + **JetBrains Mono** (chrome/mono). Logo = the six-spoke Turbine mark, sky gradient`

**The v2 system comment says the opposite** — "calm, native chrome rather than a
branded display face" (`styles.css:3-4`).

**And neither describes what actually ships.** There are, right now, four different
answers to "what is the 0xCopilot typeface" in this repository:

| Surface                                         | Display             | Body                | Mono                         | Citation                                                                                                                          |
| ----------------------------------------------- | ------------------- | ------------------- | ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Desktop + web app                               | native              | native              | JetBrains Mono               | `packages/design-system/src/styles.css:47-53`                                                                                     |
| Design mock (parity source of truth)            | native              | native              | JetBrains Mono               | `tools/design-parity/design-kit/app-v3/copilot.css:33-39`                                                                         |
| Website default layer                           | Space Grotesk       | IBM Plex Sans       | JetBrains Mono               | `apps/website/src/styles/site.css:32-34`                                                                                          |
| Website live pages (home, install, token, docs) | Bricolage Grotesque | Bricolage Grotesque | DM Mono (+ Instrument Serif) | `apps/website/src/styles/home.css:12-14`; loaded at `pages/index.astro:50`, `install.astro:19`, `token.astro:17`, `docs.astro:63` |

**Inter appears on none of them.** A repo-wide grep for `Inter` as a font family
across `packages/`, `apps/desktop/renderer/` and `apps/website/src/` returns nothing.

So the memory's "Inter + JetBrains Mono" is a **stated intent that was never
implemented anywhere**, and the app and the marketing site have since diverged from
each other as well as from it.

Three coherent ways to resolve this, all legitimate, none chosen here:

1. **The memory is stale — retire it.** v2 supersedes it; the app and the design mock
   agree on native; update the memory to say so and treat the website as an
   intentionally louder, separate marketing register.
2. **The memory is the brand and the app is the drift.** Adopt Inter in the app,
   and separately reconcile the website's three-way split.
3. **The brand is deliberately two-register** — loud marketing type, quiet product
   chrome — which is a real and common strategy (Linear, Vercel, Stripe all do a
   version of it). If so, say it explicitly somewhere, because right now the split
   reads as neglect rather than intent.

What makes this decidable is answering one question: **is the product supposed to
look like 0xCopilot, or like the operating system?** Everything else follows.

---

## 6. The options

All three assume the mono face is unchanged. Nobody has complained about JetBrains
Mono and it is the face doing 221 of the 303 family references.

---

### Option A — Keep native, change nothing else

**Token diff:** none.

**What it buys.** Zero risk, zero bundle, zero licensing surface, zero work. It keeps
the app byte-aligned with the committed design spec
(`design-kit/app-v3/copilot.css:33-38`), which means every parity report stays
comparable to every earlier one — no baseline re-cut, no re-litigating whether a
regression is real or a font change. It preserves the property that the app is hinted
by the platform for the platform, at every DPI, on both OSes, including displays
neither of us owns.

**The honest case for it, not a straw man.** The complaint was "not aesthetic
enough," and §3 argues that at least two of the four plausible causes — the missing
`antialiased` (§3.1) and the fractional ladder (§3.2) — are _not_ the face. If those
are the real defects, swapping the face changes the look without fixing either, and
the app is then differently-off rather than right, with a new webfont to maintain.
"The mock looks better than the build even though both use SF" is a strong signal
that the delta is not the family. The disciplined move when a symptom has four
candidate causes is to fix the cheap, certain, reversible ones first and re-ask the
question.

**What it costs.** It does not answer the complaint. If the user's reaction to the
current build is "this looks generic," that reaction is about §3.4 and Option A
declines to address it. It also leaves §3.3 standing: the SF Text/Display optical
switch has already produced one shipped visual bug documented at
`packages/design-system/CLAUDE.md:69`, and it will produce more, because our type
register straddles the switch point.

**Reversibility:** n/a.

---

### Option B — Keep native, fix the two face-independent defects

No new face. Two independent changes; either can ship without the other.

**Neither is a "rendering defect" in the sense of a browser drawing something
wrong**, and an earlier draft of this heading said so — wrongly. B1 is a
**divergence** (the mock and our own splash set a property the app does not); B2 is a
**spec-conformance and token-coverage** defect (the ladder cannot express the design,
so 35 call sites go around it). Both are demonstrated at exact lines. What each one
_looks like_ once fixed is, in both cases, unmeasured.

**B1 — Turn on grayscale smoothing.** One rule, at `styles.css:619-648`:

```diff
 body {
   background: var(--color-bg);
   color: var(--color-text);
   font-family: var(--font-sans);
+  /* Every design mock sets this on body (design-kit/app-v3/copilot.css:110);
+   * our own boot splash sets it on .boot (BootProgress.css:34), so text
+   * rendering currently changes when the splash unmounts (BootProgress.tsx:83).
+   * macOS-only property: no effect on the Windows host. */
+  -webkit-font-smoothing: antialiased;
+  -moz-osx-font-smoothing: grayscale;
   font-size: var(--font-size-sm);
   margin: 0;
 }
```

- **Verified (file:line).** Absent from every shipped stylesheet; present in all six
  design mocks at the lines listed in §3.1; present on `.boot`
  (`apps/desktop/renderer/BootProgress.css:34`) which `BootChrome` renders as
  `<div className="boot">` (`BootProgress.tsx:163`) and `BootGate` drops on `ready`
  (`BootProgress.tsx:83-84`), inside one React root
  (`apps/desktop/renderer/bootstrap.tsx:167-202`). Precedent: Hermes sets it on
  `body` (`hermes-agent/apps/desktop/src/styles.css:561`).
- **NOT verified — the visual payoff.** I did not run the app and did not measure a
  pixel. On macOS ≥ Mojave the OS no longer does subpixel antialiasing, so
  `antialiased` may change little or nothing there; on Windows the property is inert
  by definition. **The "it will look better" claim is unsupported.** Do not adopt B
  on B1's strength alone.
- **How to verify it cheaply, before deciding.** `__extractParity` merges a surface's
  `extraProps` into the captured set (`extract-computed.js:117`, `styles[p] = cs[p]`
  at `:127`), and no `anchors.json` uses `extraProps` today. Add
  `"webkitFontSmoothing"` to one surface's spec and run that surface's parity
  extraction with and without the two lines. The design side reports `antialiased`
  and the live side reports Blink's unset default, which makes the divergence a
  measured row instead of an argument; more usefully, the captured `width` rows show
  whether glyph advances actually moved on this machine. One afternoon, and it turns
  the unverified half of B1 into a number.
- **Cost — bundle:** zero.
- **Cost — licensing:** zero.
- **Cost — parity fixtures:** near-zero. `-webkit-font-smoothing` is not in
  `DEFAULT_PROPS` (`extract-computed.js:29-58`), so the property itself produces no
  report row and no baseline is re-cut. Glyph _widths_ may change; `width` and
  `height` **are** captured (`extract-computed.js:59-61`, labelled "layout size
  (noisy)") and **are** compared, but they are deliberately kept out of the `BOX` set
  — "not the measured `width`/`height` rows, which are container-dependent noise"
  (`compare.mjs:80-83`) — so they fall through to `severity: "low"`
  (`compare.mjs:371`). Expect a few extra LOW rows, not a gate change. Glyph
  advances can move at all only because extraction runs in a real browser —
  `chromium.launch(...)` at `tools/design-parity/lib/extract-playwright.mjs:149-152`
  — not in jsdom. It is _headless_ Chromium, which is one more reason its numbers do
  not settle what the shipped window looks like.
- **Cost — risk:** grayscale smoothing makes light text on dark _thinner_ wherever it
  takes effect. If any surface is currently relying on stem-darkening to hit its
  intended weight, it will read lighter. Reviewed on a real display, not in jsdom
  (jsdom performs no layout at all).
- **Reversibility:** delete two lines.

**B2 — Give the sans ladder whole-pixel rungs, the way mono already has them.**
This is the fix from `styles.css:626-636` — already applied once to `--font-size-sm`
— extended to the three rungs that still miss.

```diff
-  --font-size-2xs: 0.7rem; /* 11.2px — badge, kbd */
-  --font-size-xs: 0.78rem; /* 12.5px — hint, caption */
+  --font-size-2xs: 0.6875rem; /* 11px — badge, kbd (design: 11px ×8 non-mono) */
+  --font-size-xs: 0.75rem; /* 12px — hint, caption (design: 12px ×18 non-mono, its most common) */
   --font-size-sm: 0.8125rem; /* 13px */
   ...
-  --font-size-2xl: 1.4rem; /* 22.4px — page heading */
+  --font-size-2xl: 1.375rem; /* 22px — page heading */
+
+  /* SANS half-pixel steps — the design's non-mono register is a half-pixel
+   * ladder (11…13.5) like the mono one documented at :79. Named by px value,
+   * following --font-size-mono-10's precedent. 11-5 is the only one of the three
+   * that unblocks an existing literal: of the 35 raw-px font-size literals in
+   * shipped package CSS, TEN are 11.5px and no token covers that size. The other
+   * 25 (9 ×2, 9.5 ×2, 10 ×5, 10.5 ×2, 11 ×2, 12 ×8, 13 ×4) all already have a
+   * token and are discipline debt, not coverage gaps. 12-5 and 13-5 retire no
+   * existing literal — they exist so the design's 12.5px and 13.5px elements
+   * have a rung to land on at all. */
+  --font-size-11-5: 0.71875rem; /* 11.5px — design uses it ×5 non-mono */
+  --font-size-12-5: 0.78125rem; /* 12.5px — design uses it ×12 non-mono; see note */
+  --font-size-13-5: 0.84375rem; /* 13.5px — design uses it ×5 non-mono */
```

- **Cost — bundle:** zero.
- **Cost — licensing:** zero.
- **A complication the earlier draft hid, and the reason `12-5` appears above.**
  §3.2's split shows the design uses **both 12px (×18) and 12.5px (×12)** in non-mono
  blocks. Today's `xs` renders 12.48px, so it is already almost exactly right for the
  twelve 12.5px sites and 0.48px wrong for the eighteen 12px ones. Retuning `xs` to
  12px therefore does not straightforwardly "move toward the design" — it swaps which
  group is wrong, 18 sites gained against 12 lost, unless a `12-5` rung is added at
  the same time and the 12.5px sites are migrated onto it. **That migration is call-site
  work, not a token edit**, and it is not costed here.
- **Cost — parity fixtures:** **this one moves numbers.** `2xs` shifts 0.2px, below
  `compare.mjs`'s 0.4px cutoff (`compare.mjs:337-339`) — invisible either way. `xs`
  shifts 0.48px and will flag `MEDIUM` on every anchor that lands on it; whether that
  reads as improvement depends entirely on whether the anchor sits on a 12px or a
  12.5px design element, per the bullet above. **Net direction is unverified** — it is
  arithmetic over the design CSS, not a parity run, and the earlier draft's flat
  "expect the reports to get better" was not supportable. Do not merge B2 without a
  parity run, and read it per-anchor rather than as a total.
- **Cost — blast radius:** **450 call sites** change rendered size by 0.2–0.48px
  (266 `xs` + 184 `2xs`, §1.2's command), plus 11 on `2xl`. **Ten** raw-px literals
  become removable in follow-up — the `11.5px` ones, the only size in the 35 with no
  token today; the other 25 were already removable and simply were not removed. Line
  wrapping can change in tight containers. This is the change that needs a real look
  at the app, not a green test suite.
- **Reversibility:** the rung values revert cleanly; the new tokens can stay unused or
  be deleted if nothing adopted them yet. Note the asymmetry with B1 — reverting the
  _tokens_ is trivial, but any call sites migrated onto `11-5` / `12-5` / `13-5` have
  to be migrated back, so reversibility decays as adoption proceeds.

**What B buys overall.** It closes two divergences from the committed design that are
demonstrable rather than taste, at zero bundle and zero licensing cost, without
touching the design contract. If the complaint was §3.1 or §3.2, B answers it. If the
complaint was §3.4, B does nothing for it.

**Stated plainly, because it is the honest summary of B.** B1's _divergence_ is
verified and B1's _visual payoff_ is not; B2's _defect_ is verified and B2's _net
effect on parity_ is not, and B2 is bigger than the token diff makes it look once the
12 / 12.5 split is priced in. B is the cheapest option to try and the cheapest to
revert, which is a real argument for doing it first — but it is not the same as an
argument that it will fix the complaint, and this document does not make that claim.

**Sequencing note.** B is worth doing _before_ any face decision regardless of which
option wins, because §3.1 and §3.2 are face-independent and B2's ladder work has to
happen anyway if a new face is adopted. Doing B first also produces a clean
re-ask: look at the app after B and see whether "off" survives.

---

### Option C — Vendor one branded sans

Adopt a single branded face for `--font-display` + `--font-sans`. Mono unchanged.
Two variants, differing sharply in cost.

**Shared token diff shape** (`styles.css:47-52`), shown for variant C2:

```diff
+@font-face {
+  font-family: "Instrument Sans";
+  font-style: normal;
+  font-weight: 400 700;
+  font-display: swap;
+  src: url("./fonts/instrument-sans-latin.woff2") format("woff2");
+  unicode-range: U+0000-00FF, U+0131, U+0152-0153, /* …as the JB Mono block at :24-27 */;
+}
+/* + latin-ext, + the two italic faces — all four already on disk */
+
 --font-display:
-  -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui,
-  sans-serif;
+  "Instrument Sans", -apple-system, BlinkMacSystemFont, "SF Pro Text",
+  "Segoe UI", system-ui, sans-serif;
 --font-sans:
-  -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui,
-  sans-serif;
+  "Instrument Sans", -apple-system, BlinkMacSystemFont, "SF Pro Text",
+  "Segoe UI", system-ui, sans-serif;
```

Note the native stack stays as the fallback chain, so a failed font load degrades to
exactly today's rendering.

#### C1 — Inter (matches the brand memory)

- **Cost — bundle:** a latin-subset variable Inter `woff2` is **unverified** — I did
  not fetch one. In-repo comparables bracket it: `instrument-sans-latin.woff2` is
  30,092 B and `space-grotesk-latin.woff2` is 22,288 B for the same kind of subset,
  so 25–45 KB is the plausible band. Base64 on desktop → ×4/3. The naive approach
  costs vastly more: OpenCode ships `Inter.ttf` at **874,708 B**
  (`opencode/packages/app/public/assets/Inter.ttf`). Subsetting is not optional.
- **Cost — licensing:** Inter is distributed under the SIL Open Font License. **But
  note a gap that already exists:** there is no `LICENSE`, `OFL.txt` or `NOTICE` file
  anywhere under `packages/design-system/` for the six faces already vendored
  (verified by `find`). Adding a seventh vendored face makes an unaddressed
  attribution gap larger. Fix it either way.
- **Cost — parity fixtures:** smaller than it looks. `compare.mjs:323-331` only flags
  a **typeface _class_** change: `if (isMono(d) !== isMono(l))`. Sans → sans is
  explicitly discarded as "vendor-string noise." So the live side rendering Inter
  against a design side rendering SF produces **no `fontFamily` finding at all**.
  The two all-eight-file harnesses
  (`tools/design-parity/lib/render-live-surface-language.test.tsx:258-266` and
  `render-live-generative-surfaces-v3.test.tsx:265-272`) need the new filename added
  to their `copyFileSync` list or the live render silently falls back to SF — which
  the comparator would then also not flag. That is a **quiet failure mode**: the
  harness would measure the wrong face and report parity.
- **Cost — the design contract:** this is the real one. The design mock says native
  (`design-kit/app-v3/copilot.css:33-38`). Adopting Inter means the live app is
  deliberately no longer trying to match the committed spec on this axis, and either
  the mock gets re-cut from DesignSync with Inter, or every future parity reader has
  to know about a standing intentional divergence. `anchors.json` supports an
  `expectDivergence` mechanism, so this is expressible — but it must be written down
  or it becomes folklore.
- **What it buys:** brand identity; alignment with the stated brand memory; and the
  elimination of §3.3 — one continuous variable axis, no SF Text/Display optical
  switch, no more `packages/design-system/CLAUDE.md:69`-class bugs.
- **Reversibility:** high on the token (revert 6 lines), lower in practice — once a
  face ships, marketing assets, screenshots, the promo video and muscle memory
  accumulate around it.

#### C2 — Instrument Sans (already on disk)

Identical mechanics, one significant difference: **the files are already vendored and
the harnesses already copy them.**

`packages/design-system/src/fonts/` holds all four Instrument Sans faces —
`instrument-sans-latin.woff2` (30,092 B), `-latin-ext` (11,144 B), `-latin-italic`
(31,828 B), `-latin-ext-italic` (11,648 B) — and both eight-file harnesses already
name them (`render-live-surface-language.test.tsx:259-262`,
`render-live-generative-surfaces-v3.test.tsx:265-268`).

- **Cost — bundle:** upright latin only = 30,092 B → **≈40 KB base64** on desktop.
  Adding latin-ext and the italics is a further ~54,620 B on disk. Adopting only the
  upright latin face is the cheap version.
- **Cost — licensing:** same gap as C1, but **no new** third-party asset enters the
  repo — the exposure already exists and is unchanged.
- **Cost — parity fixtures:** **zero `copyFileSync` edits.** The two harnesses'
  eight-file lists become correct instead of vestigial, and the seven two-file
  harnesses (`render-live-composer.test.tsx:252-253`, `render-live-tools:258-259`,
  `render-live-activity:180-181`, `render-live-chats-topbar:220-221`,
  `render-live-chat-tool-calls:173-174`, `render-live-thinking:245-246`,
  `render-live-chat-tool-call-shell:572-573`) each need two lines added — mechanical,
  and the same edit C1 requires anyway.
- **What it buys:** everything C1 buys except brand-memory alignment, plus it
  **retires the dead-asset problem by using the assets instead of arguing about
  deleting them**. Instrument Sans was the v1 face; v2 removed the `@font-face` rules
  but kept the files (`styles.css:7-9`). This restores an already-chosen face rather
  than introducing a new one.
- **What it costs versus C1:** it contradicts the brand memory's "Inter." If §5 is
  resolved as "the memory is the brand," C2 is the wrong face and C1 is right.
- **Reversibility:** highest of the C variants — the files stay either way, so revert
  is purely the `@font-face` block plus two token lines.

**Correction to a stale in-repo comment while you are here.** `styles.css:14-15` says
_"Their five sibling harnesses already copy just the two jetbrains-mono files"_ — it
is **seven**, not five (enumerated above). Minor, but that comment is the reference a
future agent will act on when deciding whether the six files can be deleted.

---

## 7. A caveat that applies to every option

The parity gate is **blind, or nearly blind, to four of the things being discussed
here**:

| Property                    | How it is treated                                                                                                                                | Consequence                                                                                                      |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------- |
| `-webkit-font-smoothing`    | **not captured** (`extract-computed.js:29-58`)                                                                                                   | §3.1 was never measurable                                                                                        |
| `fontFamily`, sans→sans     | captured, then **discarded** as "vendor-string noise" (`compare.mjs:323-331`)                                                                    | a face swap produces no finding — and a _failed_ face load produces no finding either                            |
| `fontSize` deltas < 0.4px   | **suppressed** (`compare.mjs:337-339`)                                                                                                           | the 11.2 vs 11px drift is by-design invisible                                                                    |
| measured `width` / `height` | captured (`extract-computed.js:59-61`) and compared, but kept out of `BOX` (`compare.mjs:80-83`) so they fall through to LOW (`compare.mjs:371`) | re-wrapping from a face change surfaces only as low-severity rows, mixed with genuinely viewport-dependent noise |

Read that table twice before treating a green parity run as evidence that a
typography change was safe. Whichever option is chosen, verification has to include
looking at the running app on a real display — this repo has already been burned by
a green suite over a broken screen (jsdom performs no layout).

---

## 8. Decision table

|                               | A: nothing | B: fix defects                              | C1: Inter                  | C2: Instrument Sans        |
| ----------------------------- | ---------- | ------------------------------------------- | -------------------------- | -------------------------- |
| Answers §3.1 smoothing        | ✗          | **✓** _(payoff unmeasured; macOS only)_     | ✗ (unless bundled with B1) | ✗ (unless bundled with B1) |
| Answers §3.2 ladder           | ✗          | **✓** _(needs 12.5px migration too)_        | ✗                          | ✗                          |
| Answers §3.3 optical switch   | ✗          | ✗                                           | **✓**                      | **✓**                      |
| Answers §3.4 "generic"        | ✗          | ✗                                           | **✓**                      | **✓**                      |
| Bundle cost (desktop, base64) | 0          | 0                                           | ~35–60 KB _(unverified)_   | **~40 KB**                 |
| New third-party asset         | no         | no                                          | **yes**                    | **no**                     |
| `copyFileSync` list edits     | 0          | 0                                           | 9 harnesses                | 7 harnesses                |
| Diverges from design mock     | no         | no                                          | **yes**                    | **yes**                    |
| Matches brand memory          | no         | no                                          | **yes**                    | no                         |
| Reversibility                 | n/a        | trivial for B1; decays with adoption for B2 | moderate                   | high                       |

`B` is orthogonal to `A`/`C` and composes with either. `C1` and `C2` are mutually
exclusive.

Two ✓s in the B column are doing less work than a ✓ normally does, and §6/B says why:
B1 provably closes a divergence but its visual effect is unmeasured and is inert on
Windows; B2 provably closes a token-coverage gap but its net parity direction is
unverified and it is under-costed until the twelve 12.5px design sites have a rung to
land on. **Neither is a reason not to do B — both are reasons not to treat B as
having answered the complaint until someone looks at the built app.**

---

## 9. What I could not verify

- **The visual magnitude _or direction_ of §3.1.** I did not run the app and did not
  measure a pixel. The property is provably absent and the splash/app inconsistency is
  provable at exact lines (§6/B1), but the familiar "subpixel AA makes it heavier and
  muddier" story is asserted from general knowledge, not measured here — and it may
  not apply at all on macOS ≥ Mojave, which stopped doing subpixel antialiasing
  system-wide, or on Windows, where the property does nothing. §6/B1 gives a concrete
  way to measure it via `extraProps` before the decision is taken.
- **Inter's subset size.** No file fetched. Bracketed by in-repo comparables
  (22–30 KB) and OpenCode's unsubsetted 874,708 B upper bound.
- **Whether B2 improves or worsens parity scores.** Genuinely open, and more open than
  an earlier draft said: the design uses 12px ×18 _and_ 12.5px ×12 in non-mono blocks
  (§3.2), so retuning `xs` from 12.48px to 12px moves toward one group and away from
  the other. Not run.
- **How much of the design's "not mono" column is really sans.** §3.2's split keys on
  a rule block declaring `font-family: var(--mono)` itself; a block inheriting mono
  from an ancestor counts as "not mono". So 63 is an upper bound on the sans register,
  and every per-size non-mono count in §3.2 and §6/B2 is an upper bound too.
- **Font license terms.** Asserted from general knowledge, not citable from this
  repo — there is no OFL or NOTICE text vendored anywhere under
  `packages/design-system/`. That absence _is_ citable, and is a gap under any option.
- **Whether the design mock could be re-cut with a branded face.** `design-kit/REFRESH.md`
  documents re-pulling from DesignSync, but I did not open it or test whether a
  face change is expressible upstream.
- **The web host's behaviour under a new face.** `apps/frontend` mounts the same
  stylesheet (`apps/frontend/src/app/App.tsx:5`) and Vite would emit the woff2 as a
  separate asset (no `assetsInlineLimit` override in `apps/frontend/vite.config.ts`),
  but web is deprecated per the root `CLAUDE.md` and I did not check for a host
  stylesheet re-declaring the type tokens — a known failure mode in this codebase.

---

## 10. The two questions that decide it

Everything above collapses to these:

1. **Is the product meant to look like 0xCopilot, or like the operating system?**
   Native is a legitimate, deliberate answer, and it is what the committed design
   spec says. But it has to be _chosen_, because right now the app says one thing,
   the brand memory says another, and the website says two more.

2. **Do the §3.1 and §3.2 defects get fixed regardless?** They are face-independent,
   cheap, and reversible, and one of them (§3.2) is work a face swap would require
   anyway. If the answer is yes, do them first and re-ask question 1 while looking at
   the result.
