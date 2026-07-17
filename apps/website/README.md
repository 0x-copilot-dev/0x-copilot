# apps/website — 0xcopilot.tech

Marketing site for 0xCopilot. Astro + React (TSX), static output, deployed to
GitHub Pages.

```
src/pages/index.astro   home — thesis, the demo, surfaces, run-it, trust FAQ
src/pages/token.astro   the 50 / 25 / 25 split
src/components/*.tsx    Nav, Shot, Demo — all TSX
src/layouts/Base.astro  head, fonts, sticky-nav script
public/media/           real screenshots + the recorded demo
public/CNAME            custom domain
```

## Local

```bash
npm run dev   --workspace @0x-copilot/website   # http://localhost:4321
npm run build --workspace @0x-copilot/website   # → apps/website/dist
```

## Why Astro, and why it ships almost no JS

Every route is a real HTML document — a true MPA, no client router. React
components render to HTML at build time and ship **zero** JS unless they opt in
with a `client:` directive. Exactly one does: `<Demo>`, because `playbackRate`
can't be set from markup. `token.html` ships no JavaScript at all.

## Deploying

Live at **https://0x-copilot-dev.github.io/** (org Pages repo
[`0x-copilot-dev.github.io`](https://github.com/0x-copilot-dev/0x-copilot-dev.github.io)).

Push to `main` with anything under `apps/website/**` changed.
[`deploy-website.yml`](../../.github/workflows/deploy-website.yml) builds with
`SITE_BASE=/`, verifies every linked asset, then force-pushes `dist/` into that
repo via the `PAGES_DEPLOY_KEY` deploy key. Path-filtered, so product changes
never trigger a site deploy. `workflow_dispatch` is a one-click manual deploy.

Routes are real HTML files (`index.html`, `token.html`) with relative nav links
(`./token.html`), so home ↔ token works at the domain root.

### One-time setup (custom domain)

1. **DNS for `0xcopilot.tech`:**

   | Type    | Host  | Value                       |
   | ------- | ----- | --------------------------- |
   | `A`     | `@`   | `185.199.108.153`           |
   | `A`     | `@`   | `185.199.109.153`           |
   | `A`     | `@`   | `185.199.110.153`           |
   | `A`     | `@`   | `185.199.111.153`           |
   | `CNAME` | `www` | `0x-copilot-dev.github.io.` |

   GoDaddy ships a parked-domain `A` record on `@` — delete it, or the apex
   keeps resolving to their landing page.

2. On **`0x-copilot-dev.github.io` → Settings → Pages → Custom domain:**
   `0xcopilot.tech`, wait for the DNS check, then tick **Enforce HTTPS**.
   `public/CNAME` keeps the domain attached across deploys — don't delete it.

## Design

**Light, on purpose.** Every competitor in this category — VEX, MyClaw, Vantis,
most of crypto/AI — is near-black with a neon accent. Dark _is_ the house style,
which is why this isn't. Two things follow: it matches the thesis (the product
is about receipts and an audit trail; a document should look like a document),
and since the app is dark, **dark product screenshots pop against cool paper** —
so the images carry the page instead of prose.

| Token  | Value     | Note                                              |
| ------ | --------- | ------------------------------------------------- |
| paper  | `#f6f7f9` | cool, biased toward the signal — not neutral grey |
| ink    | `#14161a` |                                                   |
| signal | `#0b6f6a` | deep teal                                         |
| clay   | `#d97757` | the _app's_ accent; only ever labels product bits |

Teal is roughly the complement of clay (`#d97757`, the app's accent, visible in
every screenshot), so it frames the shots instead of fighting them — and it
sidesteps the three colours already taken in this category: Claude orange, VEX
blue, MyClaw red.

Type is the **IBM Plex superfamily** — Serif (display), Sans (body), Mono (data).
One family, three roles, natively harmonious, and not the AI-default Inter.

## Every image here is real

No mockups, no renders. `public/media/*.png` are captures of the running app;
`demo.webm` is a recorded session — real prompt, real run against a real
provider key, real streamed answer — filmed at 1x and played at 2x.

Re-capture after UI changes: boot the stack (`make dev`), then drive it with
Playwright (already a repo dep). The captures are 1600×1000 @2x.

## Before launch — unresolved

`token.astro` ships deliberate `FILL:` markers, styled amber so they look
unfinished. **They are not placeholders to quietly delete** — each is a number a
buyer would rely on, and none were known when this was written:

- fixed supply
- ESOP protocol lock period
- ESOP protocol vest window and shape
- ESOP early-vest FDV trigger (if the Virtuals framework applies one)
- ACF starting FDV, step size, ceiling
- trade-fee split and the operations share

Fill them from **your** Virtuals launch parameters. Don't copy them from another
project's page — those are that project's numbers.

Also unresolved: the 50 / 25 / 25 split has no veVIRTUAL airdrop line. Virtuals
launches typically allocate a slice to veVIRTUAL stakers under the protocol
framework. If that's mandatory, the real split isn't 50 / 25 / 25 and this page
needs a fourth bucket before it goes live.
