// @ts-check
import { defineConfig } from "astro/config";
import react from "@astrojs/react";

/* The marketing site is published to the org Pages repo
 * (0x-copilot-dev.github.io) and served at the domain root:
 *   https://0x-copilot-dev.github.io/
 * (and later https://0xcopilot.tech once DNS is attached).
 *
 * SITE_BASE must be "/" for that. Getting it wrong 404s every stylesheet —
 * the page renders as unstyled HTML. deploy-website.yml sets SITE_BASE=/ and
 * check-links.mjs fails the build if the emitted HTML disagrees.
 *
 * Note this only governs Astro's own emitted assets (/_astro/*). Hand-authored
 * refs use relative URLs (./media/..., ./token.html) so they resolve under
 * both a root and a subpath deploy.
 */
const base = process.env.SITE_BASE ?? "/";

export default defineConfig({
  site: process.env.SITE_ORIGIN ?? "https://0xcopilot.tech",
  base,
  output: "static",
  integrations: [react()],
  build: { format: "file" }, // /token.html rather than /token/index.html
  devToolbar: { enabled: false },
});
