# P5 — No off-by-default capability without an e2e path (the "no dark capabilities" gate)

Status: **Shipped (gate v1)** · Owner: runtime/platform · Phase P5 of the verification program (see [01-e2e-run-verification.md](./01-e2e-run-verification.md) §6).

---

## 1. Problem statement

Two production incidents share one root cause:

- **AC2b worker-gate bug.** The queued-run worker was silently disabled on desktop by a one-line topology guard. The file-native store was the desktop default, but the run executor never started, so every run hung on "Listening for run events". 1,900+ unit tests, typecheck, and an adversarial review all passed — because they all exercised the path where the worker _was_ running.
- **File-store citation data-loss bug.** The file store was "built, unit-correct, shipped off, and carried a latent citation data-loss bug." It was built but **never the live path** until a human flipped `RUNTIME_STORE_BACKEND=file` on.

The shared shape: **a capability selected by an off-by-default env flag, whose ON path no automated test ever drives.** Everything is green because the default (OFF) path is the only one under test. The capability is _dark_ — present in the binary, invisible to CI — until a human enables it in production and discovers the wiring defect.

This is not a bug you fix once; it is a **class** you must keep closed. Every new backend selector or opt-in capability re-opens it.

## 2. The standing gate

A capability is in scope when it is selected by:

- a **`RUNTIME_*_BACKEND`** flag — an implementation selector (`RUNTIME_STORE_BACKEND`, `RUNTIME_EVENT_BUS_BACKEND`, `RUNTIME_KMS_BACKEND`). Every non-default value is a distinct code path that ships dark unless a test drives it.
- a **`RUNTIME_ENABLE_*`** flag — the naming convention for an opt-in capability that is OFF by default (`RUNTIME_ENABLE_LOCAL_MODELS`, `RUNTIME_ENABLE_REMOTE_SANDBOX`, `RUNTIME_ENABLE_DESKTOP_BROWSER`, `RUNTIME_ENABLE_MONTY`). A whole feature hangs off it.

> **New opt-in capabilities must adopt the `RUNTIME_ENABLE_*` name** so they land in scope of the gate. Plain `<subsystem>_ENABLED` tuning booleans (e.g. `RUNTIME_DEFAULT_REASONING_ENABLED`) are intentionally out of scope: they tune an always-present subsystem whose default path is already exercised, so they are not the dark-capability shape.

**Rule.** A capability flag whose exact name is **never referenced by any test or e2e harness** fails CI. "Referenced" means the flag name appears anywhere under a reference root:

- `services/ai-backend/tests/**` — unit + integration, including the hermetic Tier A run→stream tests (`test_fake_model_run_stream.py`, `test_fake_model_run_stream_file.py`) that drive a real run over each store backend;
- `tools/desktop-runtime/**` — the Tier B supervised-boot harness (`run-local.mjs`);
- `tools/cli-testing/**` — the live-smoke Electron driver.

**Waiver.** A declaration line may carry `# dark-capability-waiver: <reason>` to exempt a flag (a spike, an unshippable experiment). The reason is reviewed in the PR diff. Keep waivers rare — each is a capability whose alternate path is, by admission, unverified.

## 3. What is enforced vs. documented (be honest)

The gate is a **floor, not a proof.**

- **Mechanically enforced** (`tools/check_dark_capabilities.py`, run by `.github/workflows/ci-dark-capabilities-gate.yml` on every relevant PR): you cannot add a new `RUNTIME_*_BACKEND` / `RUNTIME_ENABLE_*` flag without either wiring its name into a test/e2e harness or writing an explicit waiver. If _no test even mentions the flag_, its non-default path is unambiguously unexercised, and CI blocks the merge.
- **Not** mechanically enforced: that the referenced test actually _asserts the ON path_. A test could name the flag only to assert it stays off. Closing that gap is a **reviewer obligation** (the checklist below), not something a name-reference lint can prove. The gate makes skipping the checklist _visible_; it does not replace it.

This split is deliberate. A semantic "did you assert the ON behavior" check would need to model each capability's success criteria — far heavier than the value, and easy to fool. The cheap name-reference floor catches the actual incidents (both AC2b and the file store had **zero** ON-path test coverage) while the checklist carries the judgment.

## 4. Reviewer checklist (for any PR that adds/flips a capability flag)

For a new `RUNTIME_*_BACKEND` value or `RUNTIME_ENABLE_*` toggle, the reviewer confirms:

1. **An e2e path drives it ON.** Prefer a hermetic run→stream over the affected backend (Tier A, `RUNTIME_FAKE_MODEL=1`, no key/network) or, for supervision/topology-shaped capabilities, the Tier B supervised-boot harness. The test asserts the capability's _observable effect_, not merely that the process starts.
2. **Fail-closed default is asserted.** With the flag unset the behavior is byte-identical to before (the default path is unchanged), and — for anything that must never reach real users (fakes, test shims) — a test proves it is refused under a production posture.
3. **Both backends, where applicable.** A store/bus/KMS selector is green for _every_ value it can take, not just the new one.
4. **The waiver, if any, is justified in the PR description** and tracked to removal.

## 5. Extending the gate

- **Other services.** The scan is currently scoped to `services/ai-backend/src` (the `RUNTIME_*` surface where both incidents occurred). Add a prefix + src root in `tools/check_dark_capabilities.py` as `backend` / `backend-facade` grow implementation-selecting flags.
- **Local feedback.** The gate is pure stdlib; it is a good candidate for a `pre-commit` local hook alongside the other `tools/check_*.py` guards so violations surface before push. (Not wired in this change to keep the diff scoped to CI + tooling.)

## 6. Artifacts

| Artifact                                      | Path                                                                                                                 |
| --------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Gate script                                   | `tools/check_dark_capabilities.py`                                                                                   |
| Gate tests                                    | `tools/test_check_dark_capabilities.py`                                                                              |
| CI workflow                                   | `.github/workflows/ci-dark-capabilities-gate.yml`                                                                    |
| Tier A ON-path tests (referenced by the gate) | `services/ai-backend/tests/unit/runtime_adapters/file/test_fake_model_run_stream_file.py` and the in-memory analogue |
| Tier B supervised-boot harness                | `tools/desktop-runtime/run-local.mjs` + `.github/workflows/desktop-supervised-boot-drill.yml`                        |
