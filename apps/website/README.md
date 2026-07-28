# apps/website — 0xcopilot.tech

Marketing site for 0xCopilot. Astro, static output, deployed to GitHub Pages.

```
src/pages/index.astro        home — pitch, end-to-end run journey, six surfaces, local/BYOK, token
src/pages/token.astro        $CPILOT tokenomics — 45.56 / 29.19 / 25 / 0.25
src/pages/install.astro      install — copilot CLI (npm/bun), first-run, platforms
src/pages/docs.astro         documentation index — live install guide + upcoming chapters
src/pages/moodboard.astro    local visual lab — directions and run-card palette trials
src/components/Nav.astro     shared desktop/mobile routes + "Get the app" CTA
src/layouts/Base.astro       head, fonts, favicons
src/styles/site.css          one dark design system, shared by both pages
public/media/                retained product captures + social previews
public/favicon.svg           turbine mark
public/CNAME                 custom domain — 0xcopilot.tech (shipped into dist/)
```

## Local

```bash
npm run dev   --workspace @0x-copilot/website   # http://localhost:4321
npm run build --workspace @0x-copilot/website   # → apps/website/dist
```

## Deploying

Live at **https://0xcopilot.tech/** — custom domain in front of the org Pages repo
[`0x-copilot-dev.github.io`](https://github.com/0x-copilot-dev/0x-copilot-dev.github.io)
(the `0x-copilot-dev.github.io` URL 301-redirects to the apex).

Push to `main` with anything under `apps/website/**` changed.
[`deploy-website.yml`](../../.github/workflows/deploy-website.yml) builds with
`SITE_BASE=/`, verifies every linked asset with `scripts/check-links.mjs`
(fetches what the pages actually request, so a root-vs-subpath base mistake
fails the build instead of shipping unstyled), then force-pushes `dist/` into
that repo. Path-filtered, so product changes never trigger a site deploy.

Hand-authored links are relative (`./token.html`, `./media/…`) so they resolve
under both a root and a subpath deploy.

### Custom domain

`0xcopilot.tech` is live. DNS at GoDaddy: the four GitHub Pages `A` records
(`185.199.108–111.153`) and four `AAAA` (`2606:50c0:8000–8003::153`) on `@`,
plus a `CNAME` on `www` → `0x-copilot-dev.github.io.`. `public/CNAME` holds the
apex and is copied into `dist/`, so every deploy re-asserts the custom domain on
the force-pushed Pages branch. **Don't delete `public/CNAME`** — GitHub drops the
custom domain (and its HTTPS cert) on the next force-push without it.

## Design — Operator Manual

The marketing pages use the Operator Manual direction: warm paper, black
registration rules, compressed grotesk display type, serif interruptions, and
mono control labels. Cobalt, acid, coral, and pink behave like flat printed
signal inks rather than software gradients. The turbine retains its original
sky-blue gradient; it is a brand asset, not a theme accent.

| Role         | Face                | Note                             |
| ------------ | ------------------- | -------------------------------- |
| display      | Bricolage Grotesque | compressed operational headlines |
| interruption | Instrument Serif    | human/editorial emphasis         |
| mono         | DM Mono             | labels, sequences, commands      |

| Token  | Value     | Use                        |
| ------ | --------- | -------------------------- |
| paper  | `#f2eddf` | primary ground             |
| ink    | `#111111` | type, rails, dark sections |
| cobalt | `#2447ff` | primary signal             |
| acid   | `#d7ff3f` | approvals and annotations  |
| coral  | `#ff5b36` | action and emphasis        |
| pink   | `#f4b8ff` | supporting printed field   |

## Product capture plan

The stale empty-state Run screenshot is intentionally no longer rendered on the
homepage. The next real capture set should show the complete user journey:

1. **Before — Run / goal composer:** a concrete outcome, attached context, and
   selected tools. This explains how work starts.
2. **During — active Run + approval:** live tool events, work taking shape, and
   a consequential action waiting at a gate. This is the product's proof point.
3. **After — result + Activity:** the finished artifact beside its sources and
   durable run receipt. This proves the work survives the chat.

`public/media/app-run.png` is retained only as historical source material until
the current three-image capture set replaces it.

## Post-launch — the numbers on the page

`$CPILOT` is **live** on Virtuals Protocol, on Robinhood Chain
([listing](https://app.virtuals.io/virtuals/113720)). The old amber `FILL:`
markers are gone — `token.astro` now carries the real launch parameters, read
off the live listing:

| Bucket                      | Share  | Note                                             |
| --------------------------- | ------ | ------------------------------------------------ |
| Liquidity pool              | 45.56% | fixed supply, live at launch                     |
| Automated Capital Formation | 25.00% | Limit Order Program, 2M → 160M FDV               |
| Team vesting                | 25.00% | Virtuals default team vesting                    |
| Sniper-tax buyback (team)   | 2.19%  | locked 3 mo, then 9 mo linear                    |
| Team initial buy            | 2.00%  | bought on the open curve — **disclosed on-page** |
| veVIRTUAL airdrop           | 0.25%  | to veVIRTUAL holders                             |

The page groups the three team-associated lines (25 + 2 + 2.19) as one **29.19%
Team & contributors** bucket, and the veVIRTUAL airdrop is the fourth bucket the
old 50/25/25 split was missing. Total supply is a fixed **1,000,000,000**.

Deliberately **not** on the page: the live price and the "unlocks in N days"
countdown (both volatile — link out to the listing), and a token **contract
address** (verification points to the Virtuals listing so there's one canonical
source). Paste a verified address here only if you want it rendered on-page.
