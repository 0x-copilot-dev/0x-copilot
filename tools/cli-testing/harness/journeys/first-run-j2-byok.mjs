#!/usr/bin/env node
// Journey J2 — BYOK (~30s). FTUE P7 verification. (JOURNEYS.md §J2)
//
//   gate → "Add a key" → provider → paste key → Connect → composer with the
//   real model → send → ack "Starting your first run" → workspace.
//
// ASSERTED (runnable-shaped):
//   1. sign-in → FTUE gate → the key card ("Bring your own key").
//   2. "Add a key" reveals the inline KeyForm (provider tri-toggle + password
//      input + the "stored in your OS keychain — never uploaded" note).
//   3. (key present) paste + Connect → PUT /v1/settings/provider-keys → State B
//      composer with a real (non-local) model in the pill.
//   4. (key present) Send → State C ack titled "Starting your first run".
//   5. (key present) handoff → workspace (destination outlet).
//
// BLOCKED-UNTIL:
//   • Steps 3–5 need a REAL, working provider key so the server live-check +
//     run-create succeed. Supply one via env; without it the journey asserts
//     the keyless prefix (steps 1–2) and marks 3–5 blocked. NEVER hardcode a
//     key — it is read from the environment only and never logged.
//       FIRST_RUN_BYOK_PROVIDER=anthropic|openai|openrouter
//       FIRST_RUN_BYOK_KEY=sk-...
//
// Hermetic: own userData subdir. Prereq: staged runtime (COPILOT_HOME).

import {
  COPY,
  SEL,
  makeReport,
  makeRunDir,
  signInLocal,
  startDriver,
} from "./firstRunHarness.mjs";

const PORT = Number(process.env.CTL_PORT ?? "8793");
const BYOK_PROVIDER = process.env.FIRST_RUN_BYOK_PROVIDER ?? "";
const BYOK_KEY = process.env.FIRST_RUN_BYOK_KEY ?? "";
const HAVE_KEY = BYOK_PROVIDER !== "" && BYOK_KEY !== "";

const runDir = makeRunDir("j2-byok");
const subdir = `journey-j2-${Date.now()}`;
const rep = makeReport(runDir, "Journey J2 — BYOK");

let result = "PASS";
let session = null;
try {
  session = await startDriver({ subdir, port: PORT, runDir });

  rep.log("## 1. Sign-in → FTUE gate → key card");
  await session.waitForSignIn();
  await signInLocal(session);
  await session.waitFor(SEL.firstRunSurface, session.timeouts.workspace);
  await session.waitFor(SEL.keyCard);
  await session.shot("state-a-gate");
  rep.pass('State A gate with the "Bring your own key" card');

  rep.log('## 2. "Add a key" reveals the inline KeyForm');
  await session.rpc("click", { selector: SEL.addKey });
  await session.waitFor(SEL.keyForm);
  await session.waitFor(SEL.keyInput);
  await session.waitFor(SEL.keyNote);
  await session.shot("keyform");
  rep.pass(
    "KeyForm revealed (provider toggle + password input + keychain note)",
  );

  if (!HAVE_KEY) {
    rep.blocked(
      "paste key → Connect → save → composer → send → ack → workspace " +
        "(set FIRST_RUN_BYOK_PROVIDER + FIRST_RUN_BYOK_KEY to a real key)",
    );
    result = "PARTIAL";
  } else {
    rep.log(
      "## 3. Provider + paste + Connect → save → State B composer (real model)",
    );
    // If the provider isn't the default (anthropic), pick it in the tri-toggle.
    if (BYOK_PROVIDER !== "anthropic") {
      // SegmentedControl options carry the provider label; click by role/text.
      await session.rpc("click", {
        selector: `[role="radio"]:has-text("${labelFor(BYOK_PROVIDER)}")`,
      });
    }
    await session.rpc("fill", { selector: SEL.keyInput, value: BYOK_KEY });
    await session.rpc("click", { selector: SEL.keyConnect });
    // Success → surface flips to State B; failure → an inline key error.
    await session.waitFor(SEL.composer, session.timeouts.workspace);
    const pill = await session.evalJs(
      `document.querySelector('[data-testid="composer-model-toggle"]')?.textContent ?? ""`,
    );
    if (pill.includes(COPY.modelPresetName)) {
      throw new Error(
        `BYOK composer pill unexpectedly shows the local model: ${JSON.stringify(pill)}`,
      );
    }
    await session.shot("state-b-composer");
    rep.pass(`State B composer with a real ${BYOK_PROVIDER} model in the pill`);

    rep.log('## 4. Send → State C ack "Starting your first run"');
    await session.rpc("fill", {
      selector: SEL.composerTextarea,
      value: "Say hello in one short sentence.",
    });
    await session.rpc("click", { selector: SEL.composerSend });
    await session.waitFor(SEL.ack);
    const ackTitle = await session.evalJs(
      `document.querySelector('[data-testid="first-run-ack-title"]')?.textContent ?? ""`,
    );
    if (ackTitle !== COPY.ackStarting) {
      throw new Error(
        `ack title reads ${JSON.stringify(ackTitle)} (want ${JSON.stringify(COPY.ackStarting)})`,
      );
    }
    await session.shot("state-c-ack-starting");
    rep.pass('State C ack titled "Starting your first run"');

    rep.log("## 5. Handoff → workspace");
    await session.waitFor(SEL.destinationOutlet, session.timeouts.workspace);
    await session.shot("workspace");
    rep.pass("landed in the workspace (destination outlet)");
  }
} catch (err) {
  result = "FAIL";
  rep.log(`\nRESULT: FAIL — ${err instanceof Error ? err.message : err}`);
  try {
    await session?.shot("failure");
  } catch {
    /* window may be gone */
  }
  process.exitCode = 1;
} finally {
  if (result !== "FAIL")
    rep.log(`\nRESULT: ${result} (blocked steps: ${rep.blockedCount})`);
  await session?.stop();
  rep.save(result);
}

/** Provider slug → tri-toggle label (SPEC §Data / FIRST_RUN_KEY_PROVIDERS). */
function labelFor(provider) {
  switch (provider) {
    case "openai":
      return "OpenAI";
    case "openrouter":
      return "OpenRouter";
    default:
      return "Anthropic";
  }
}
