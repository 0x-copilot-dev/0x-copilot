# apps/website — 0xcopilot.tech

Marketing site for 0xCopilot. Astro, static output, deployed to GitHub Pages.

```
src/pages/index.astro        home — pitch, real app shot, gap/wedge, six surfaces, token, FAQ
src/pages/token.astro        $CPILOT tokenomics — 45.56 / 29.19 / 25 / 0.25
src/pages/docs.astro         install — copilot CLI (npm/bun), first-run, platforms
src/components/Nav.astro      turbine mark + wordmark + 3 links + "Get the app" CTA
src/layouts/Base.astro       head, fonts, favicons
src/styles/site.css          one dark design system, shared by both pages
public/media/                app-run.png (real Run-cockpit shot) + og-cover.png (social)
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

| Token | Value     |                        |
| ----- | --------- | ---------------------- |
| ink   | `#0b0a0e` | ground                 |
| sky   | `#5fb2ec` | primary signal         |
| jade  | `#57c785` | done / success         |
| ember | `#f0764f` | energy accent, sparing |
| amber | `#e8b45e` | waiting / steer        |

## Product shot (real app, not a mockup)

The home page shows the **real app**: `public/media/app-run.png` is a screenshot
of the running desktop app's Run cockpit (captured via the `tools/cli-testing`
driver in **production** posture), framed in the `.shot` container right under
the hero. Its visible left rail is the product's **six-surface** IA — Run ·
Chats · Projects · Activity · Tools · Skills — which the "Six surfaces, not one
text box" section lists in the same order.

The old bespoke "Time Machine" scrubbable demo (`TimeMachine.astro` +
`public/hero-demo.js`) and the stale `welcome.png` were removed — a launched
product's best demo is the product. To refresh the shot: re-run the driver, then
`sips --resampleWidth 1600 <shot>.png --out public/media/app-run.png`. (A block
of now-dead `.tm-*` rules remains in `site.css` — safe to prune.)

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
