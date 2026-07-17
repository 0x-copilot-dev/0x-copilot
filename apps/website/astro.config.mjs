// @ts-check
import { defineConfig } from "astro/config";
import react from "@astrojs/react";

// Static output: every route is a real HTML document (a true MPA, no client
// router), which is what GitHub Pages serves and what crawlers read. React
// components render to HTML at build time and ship zero JS unless a component
// explicitly opts in with a client: directive.
export default defineConfig({
  site: "https://0xcopilot.tech",
  output: "static",
  integrations: [react()],
  build: { format: "file" }, // /token.html rather than /token/index.html
  devToolbar: { enabled: false },
});
