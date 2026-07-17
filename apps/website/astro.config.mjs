// @ts-check
import { defineConfig } from "astro/config";
import react from "@astrojs/react";

/* GitHub Pages serves a *project* site under /<repo> (here: /0x-copilot) until
 * a custom domain is attached, at which point it serves from the root instead.
 * Those two need different asset prefixes, and getting it wrong 404s every
 * stylesheet — the page renders as unstyled HTML.
 *
 * SITE_BASE picks the target. The default matches where we actually deploy
 * today (the project subpath). Once 0xcopilot.tech DNS is live and Pages has
 * the custom domain attached, set SITE_BASE=/ in the workflow — there is a
 * check in deploy-website.yml that fails the build if the emitted HTML and the
 * chosen base disagree, so this can't silently rot.
 *
 * Note this only governs Astro's own emitted assets (/_astro/*). Everything we
 * author by hand uses relative URLs (./media/..., ./token.html) which resolve
 * correctly under BOTH a root and a subpath deploy, because both pages sit at
 * the same directory level.
 */
const base = process.env.SITE_BASE ?? "/0x-copilot";

export default defineConfig({
  site: process.env.SITE_ORIGIN ?? "https://0xcopilot.tech",
  base,
  output: "static",
  integrations: [react()],
  build: { format: "file" }, // /token.html rather than /token/index.html
  devToolbar: { enabled: false },
});
