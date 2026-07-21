#!/usr/bin/env node
// Journey J4 — Skip. FTUE P7 verification. (JOURNEYS.md §J4)
//
//   gate → "skip — open the workspace" → lands in the Run cockpit; the
//   first-run flag is set → a relaunch (same userData) skips the gate.
//
// ASSERTED:
//   1. sign-in → FTUE gate (State A).
//   2. top-bar "skip — open the workspace →" → the workspace (destination
//      outlet), specifically the Run cockpit empty-state with the existing
//      "Set up your model" CTA (#158) when no model is configured.
//   3. RELAUNCH (same userData subdir) → the first-run gate NEVER renders; the
//      app goes straight to the workspace. Proves saveFirstRunComplete persisted
//      (userData/settings/first-run.json, keyed by workspaceId).
//
// BLOCKED-UNTIL: none — skip needs neither a model nor a key, so J4 is fully
// assertable today once the runtime is staged.
//
// Hermetic: own userData subdir (reused across both launches). Prereq: staged
// runtime (COPILOT_HOME).

import {
  SEL,
  makeReport,
  makeRunDir,
  signInLocal,
  startDriver,
} from "./firstRunHarness.mjs";

const PORT = Number(process.env.CTL_PORT ?? "8794");
const runDir = makeRunDir("j4-skip");
const subdir = `journey-j4-${Date.now()}`; // shared across both launches
const rep = makeReport(runDir, "Journey J4 — Skip");

let result = "PASS";
let session = null;
try {
  // ---- Launch #1: sign in, see the gate, skip into the workspace ----------
  session = await startDriver({ subdir, port: PORT, runDir });

  rep.log("## 1. Sign-in → FTUE gate (State A)");
  await session.waitForSignIn();
  await signInLocal(session);
  await session.waitFor(SEL.firstRunSurface, session.timeouts.workspace);
  await session.waitFor(SEL.firstRunSkip);
  await session.shot("state-a-gate");
  rep.pass("FTUE gate rendered with the top-bar skip link");

  rep.log('## 2. "skip — open the workspace" → Run cockpit');
  await session.rpc("click", { selector: SEL.firstRunSkip });
  await session.waitFor(SEL.destinationOutlet, session.timeouts.workspace);
  // The Run cockpit empty-state; with no configured model the "Set up your
  // model" CTA is shown (deep-links Settings → provider keys / local models).
  await session.waitFor(SEL.runEmptyState);
  const setupCta = await session.isPresent(SEL.runEmptySetupCta);
  if (!setupCta) {
    rep.blocked(
      'run-empty "Set up your model" CTA absent (a model may already be configured on this install)',
    );
  } else {
    rep.pass('Run cockpit empty-state with the "Set up your model" CTA');
  }
  await session.shot("workspace-after-skip");
  rep.pass("skip landed in the workspace (destination outlet)");

  await session.stop();
  session = null;

  // ---- Launch #2: same userData → the gate must be skipped ----------------
  rep.log("## 3. Relaunch (same userData) → the first-run gate never renders");
  session = await startDriver({ subdir, port: PORT, runDir });
  await session.waitForSignIn();
  await signInLocal(session);
  // A returning user drops straight to the shell — assert the workspace shows
  // and the FTUE surface is NOT present.
  await session.waitFor(SEL.destinationOutlet, session.timeouts.workspace);
  const gateShown = await session.isPresent(SEL.firstRunSurface);
  if (gateShown) {
    throw new Error(
      "first-run gate rendered on relaunch — the flag did not persist",
    );
  }
  await session.shot("relaunch-skips-gate");
  rep.pass("relaunch skipped the gate (first-run flag persisted)");
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
