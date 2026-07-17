/* Re-record the hero demo at high resolution.
 *
 * The previous take was 1280×800 VP8 and looked soft. This records at
 * 1920×1200 — displayed at ~1100px on the site, that's ~1.7x supersampling,
 * which reads crisp — then ffmpeg (see encode step in the README) transcodes
 * to a high-bitrate H.264 mp4 (+ VP9 webm fallback).
 *
 * Everything filmed is real: a real prompt, a real run against a real provider
 * key, a real streamed answer. Filmed at 1x, played at 2x on the site.
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const OUT = process.argv[2];
const APP = "http://127.0.0.1:5173/";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ channel: "chrome" });
const ctx = await browser.newContext({
  viewport: { width: 1920, height: 1200 },
  colorScheme: "dark",
  recordVideo: { dir: OUT, size: { width: 1920, height: 1200 } },
});

// Warm-up page mints the dev session so the filmed page opens already signed
// in, not on "Loading session…".
const warm = await ctx.newPage();
await warm.goto(APP, { waitUntil: "networkidle" });
await warm.waitForTimeout(5000);
await warm.close();

const page = await ctx.newPage();
const beat = (ms) => page.waitForTimeout(ms);

await page.goto(APP, { waitUntil: "networkidle" });
await page
  .getByRole("button", { name: "Chats", exact: true })
  .first()
  .waitFor({ state: "visible", timeout: 15000 });
await beat(700);

await page
  .getByRole("button", { name: /New chat|new thread/i })
  .first()
  .click();
await beat(800);

const composer = page.getByRole("textbox", { name: /message/i }).first();
await composer.click();
await composer.type(
  "Draft a 3-bullet case for usage-based pricing over per-seat, for an AI agent product.",
  { delay: 38 },
);
await beat(600);
await page.keyboard.press("Enter");
await beat(9000); // the answer streams in through here

for (const dest of ["Todos", "Projects"]) {
  await page.getByRole("button", { name: dest, exact: true }).first().click();
  await beat(1900);
}
await page.getByRole("button", { name: "Chats", exact: true }).first().click();
await beat(1800);

const vp = await page.video().path();
await ctx.close();
await browser.close();
console.log("raw:", vp);
