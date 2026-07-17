#!/usr/bin/env node
/* Verify every asset the built pages actually REQUEST resolves — served from
 * the same path GitHub Pages will serve them from.
 *
 * This exists because of a real bug: the first deploy shipped root-absolute
 * asset URLs (/_astro/..., /media/...) while Pages served the site at
 * /0x-copilot/. Every stylesheet 404'd and the site rendered as unstyled HTML.
 * It passed my manual check because I fetched the URLs I constructed by hand
 * rather than the ones the page asks for. Don't verify what you assume; verify
 * what the browser fetches.
 *
 * Usage: node scripts/check-links.mjs <distDir> <basePath>
 *   node scripts/check-links.mjs dist /0x-copilot
 *   node scripts/check-links.mjs dist /
 */
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { join, extname, resolve } from "node:path";

const dist = resolve(process.argv[2] ?? "dist");
const base = (process.argv[3] ?? "/").replace(/\/$/, "") || "";

const MIME = {
  ".html": "text/html",
  ".css": "text/css",
  ".js": "text/javascript",
  ".png": "image/png",
  ".webm": "video/webm",
};

// Serve dist under `base`, exactly as Pages does for a project site.
const server = createServer(async (req, res) => {
  let p = decodeURIComponent(new URL(req.url, "http://x").pathname);
  if (base && p.startsWith(base)) p = p.slice(base.length) || "/";
  else if (base) return void res.writeHead(404).end("outside base");
  if (p.endsWith("/")) p += "index.html";
  try {
    const file = join(dist, p);
    await stat(file);
    res.writeHead(200, {
      "content-type": MIME[extname(file)] ?? "application/octet-stream",
    });
    res.end(await readFile(file));
  } catch {
    res.writeHead(404).end("not found");
  }
});

await new Promise((r) => server.listen(0, r));
const origin = `http://127.0.0.1:${server.address().port}`;
const root = `${origin}${base}/`;

const pages = ["", "token.html"];
let failed = 0;
let checked = 0;

for (const page of pages) {
  const pageUrl = new URL(page, root).href;
  const res = await fetch(pageUrl);
  if (!res.ok) {
    console.log(`FAIL  page ${pageUrl} -> ${res.status}`);
    failed++;
    continue;
  }
  const html = await res.text();

  // Pull every URL the page will actually fetch or link to.
  const refs = [...html.matchAll(/(?:href|src|poster)="([^"#][^"]*)"/g)]
    .map((m) => m[1])
    .filter((u) => !/^(https?:|data:|mailto:|#)/.test(u));

  for (const ref of new Set(refs)) {
    const abs = new URL(ref, pageUrl).href;
    const r = await fetch(abs, { method: "GET" });
    checked++;
    if (!r.ok) {
      console.log(
        `FAIL  ${page || "index.html"} -> ${ref}  (${abs}) ${r.status}`,
      );
      failed++;
    }
  }
}

server.close();
console.log(
  failed
    ? `\n✗ ${failed} broken reference(s) of ${checked} checked at base "${base || "/"}"`
    : `✓ all ${checked} referenced assets resolve at base "${base || "/"}"`,
);
process.exit(failed ? 1 : 0);
