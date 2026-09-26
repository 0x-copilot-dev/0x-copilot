# apps/website — 0xcopilot.tech

Marketing site for 0xCopilot. Astro, static output, deployed to GitHub Pages.

```
src/pages/index.astro        home — pitch, end-to-end run journey, six surfaces, local/BYOK
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

Hand-authored links are relative (`./install.html`, `./media/…`) so they resolve
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

`public/media/app-run.png` is **deleted**. It was an empty composer — none of
the three frames above — and it was the image the root `README.md` shipped as
the project's hero.

Two frames now exist, captured against the real packaged app at 3800x1888 by
[`tools/desktop-journeys/_marketing_capture.py`](../../tools/desktop-journeys/_marketing_capture.py):

- `public/media/studio-before.png` — **before**, the goal composed and not yet sent.
- `public/media/studio-run.png` — **after**, the finished document artifact on
  the canvas beside its transcript, run summary, and context meter. This is what
  the root README now embeds.

**During is still missing, and not for want of trying.** For an artifact-
producing task the canvas stays on its "Nothing to review yet" empty state until
the artifact publishes, which happens as the run completes — so "during" and
"after" collapse into the same frame. Capturing frame 2 as described above
(live tool events with an action waiting at a gate) needs a task that _stops at
an approval_, not one that simply takes a while. Re-run the capture script
against a write-gated connector task to get it.

Two traps that cost a capture each, recorded so the next person skips them:

- `stage.mjs` **copies** `apps/frontend/dist`, it does not build it. Re-staging
  alone republishes stale UI under fresh timestamps. Build the frontend first.
- The composer renders the model name, so every Studio capture names a model.
  External copy is not supposed to. Crop the composer's right edge or accept it
  deliberately — but decide, rather than discovering it after publishing.
- **Capture wider than the screen.** With the canvas open the composer sits in a
  rail about a third of the window, and below roughly 1900 CSS px the composer's
  bottom controls wrap onto two lines — `[+ tools mode]` above
  `[context model mic send]`. macOS fullscreen does not fix this, because it caps
  the window at the display (1512 logical here) which is already too narrow.
  `setContentSize` accepts a window LARGER than the screen and the screenshot is
  of the renderer, so `CAPTURE_WIDTH=1900` is not limited by the panel it runs
  on. The script asserts the row is unwrapped, and self-checks that assertion by
  squeezing to 1200 and confirming it trips.

## No token content

The site carries no token content and does not link to the Virtuals listing.
`src/layouts/Base.astro` keeps the invisible `virtual-protocol-site-verification`
meta tag so that listing's ownership check of the domain keeps passing; visitors
never see it.
