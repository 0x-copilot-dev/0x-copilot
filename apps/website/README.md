# apps/website — 0xcopilot.tech

Marketing site for 0xCopilot. Astro, static output, deployed to GitHub Pages.

```
src/pages/index.astro        home — pitch, Time Machine demo, gap/wedge, surfaces, token, FAQ
src/pages/token.astro        the 50 / 25 / 25 tokenomics
src/components/Nav.astro      turbine mark + wordmark + Discord/GitHub
src/components/TimeMachine.astro   the interactive demo (Studio + Focus, scrubbable timeline)
src/layouts/Base.astro       head, fonts, favicons
src/styles/site.css          one dark design system, shared by both pages
public/media/                welcome.png (real app shot) + og-cover.png (social)
public/favicon.svg           turbine mark
public/CNAME.example         custom domain — rename to CNAME once DNS is live
```

## Local

```bash
npm run dev   --workspace @0x-copilot/website   # http://localhost:4321
npm run build --workspace @0x-copilot/website   # → apps/website/dist
```

## Deploying

Live at **https://0x-copilot-dev.github.io/** (org Pages repo
[`0x-copilot-dev.github.io`](https://github.com/0x-copilot-dev/0x-copilot-dev.github.io)).

Push to `main` with anything under `apps/website/**` changed.
[`deploy-website.yml`](../../.github/workflows/deploy-website.yml) builds with
`SITE_BASE=/`, verifies every linked asset with `scripts/check-links.mjs`
(fetches what the pages actually request, so a root-vs-subpath base mistake
fails the build instead of shipping unstyled), then force-pushes `dist/` into
that repo. Path-filtered, so product changes never trigger a site deploy.

Hand-authored links are relative (`./token.html`, `./media/…`) so they resolve
under both a root and a subpath deploy.

### Custom domain (when DNS is ready)

DNS for `0xcopilot.tech` → the four GitHub Pages `A` records on `@`, plus a
`CNAME` on `www` → `0x-copilot-dev.github.io.`. Then rename
`public/CNAME.example` → `public/CNAME`. **Never publish a CNAME before DNS
resolves** — GitHub redirects the org URL to the custom domain and the site
404s until it does.

## Design — dark / sky / turbine

Adopted from the 0xCopilot brand kit. Dark ground, sky-blue signal, the turbine
mark, and one deliberate change: the body face is **IBM Plex Sans** rather than
the kit's Instrument Sans — so the type is a hybrid the way we wanted it.

| Role    | Face           | Note                                  |
| ------- | -------------- | ------------------------------------- |
| display | Space Grotesk  | geometric, crypto-native headlines    |
| body    | IBM Plex Sans  | the readable face carried from before |
| mono    | JetBrains Mono | labels, code, addresses               |

| Token | Value     |                           |
| ----- | --------- | ------------------------- |
| ink   | `#0b0a0e` | ground                    |
| sky   | `#5fb2ec` | primary signal            |
| jade  | `#57c785` | done / success            |
| ember | `#f0764f` | energy accent, sparing    |
| amber | `#e8b45e` | waiting / steer / `FILL:` |

## The Time Machine demo

`TimeMachine.astro` is the kit's signature interactive hero, ported: a
Launch-Week / TGE workspace over a **4-lane scrubbable timeline** (Safe /
Sheets / X thread / Discord). Drag the track to rewind; step with ◀ ▶; snap
to now. Behaviour is the kit's dependency-free vanilla JS, inlined via
`<script is:inline>` — no framework.

Simplified from the kit's four modes to **Studio + Focus** (Compose and Auto
were dropped as the redundant / non-working ones).

`welcome.png` is a real screenshot of the running app, shown under the surfaces
so the stylized demo is anchored to the actual product.

## Before launch — unresolved

`token.astro` ships deliberate amber `FILL:` markers. **Not placeholders to
quietly delete** — each is a number a buyer would rely on, unknown when written:
fixed supply · ESOP lock period · ESOP vest window/shape · ESOP early-vest FDV
trigger · ACF starting FDV / step / ceiling · fee split. Fill from **your**
Virtuals launch parameters.

Also unresolved: the 50 / 25 / 25 split has no veVIRTUAL airdrop line. If the
Virtuals framework mandates one, the real split isn't 50 / 25 / 25 and the page
needs a fourth bucket.
