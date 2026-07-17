/* Capture crisp product shots (2x) for the site. Real running app, real data. */
import { chromium } from "playwright";
const OUT = process.argv[2];
const APP = "http://127.0.0.1:5173/";
const b = await chromium.launch({ channel: "chrome" });
const p = await b.newPage({
  viewport: { width: 1600, height: 1000 },
  deviceScaleFactor: 2,
  colorScheme: "dark",
});
await p.goto(APP, { waitUntil: "networkidle" });
await p.waitForTimeout(3500);

// 1. The new-chat welcome screen (rich suggestion cards) — clean, nothing typed.
await p.getByRole("button", { name: "Chats", exact: true }).first().click();
await p.waitForTimeout(500);
await p
  .getByRole("button", { name: /New chat|new thread/i })
  .first()
  .click();
await p.waitForTimeout(1200);
await p.screenshot({ path: `${OUT}/welcome.png` });
console.log("welcome");

// 2. A finished run with the streamed answer visible.
await p
  .getByRole("button", { name: /Compare seat-based/i })
  .first()
  .click();
await p.waitForTimeout(1400);
await p.screenshot({ path: `${OUT}/answer.png` });
console.log("answer");

// 3. Projects (two real projects).
await p.getByRole("button", { name: "Projects", exact: true }).first().click();
await p.waitForTimeout(1400);
await p.screenshot({ path: `${OUT}/projects.png` });
console.log("projects");

await b.close();
