# Filesystem access — implementation state

Independent verification of four concurrent agents' work, plus the fixes that
verification forced. Every number below was measured by running the suite in
this worktree; nothing is ticked on the strength of an agent's report.

Worktree: `.claude/worktrees/wonderful-dhawan-966900`, branch
`claude/revise-stale-recovery`, base `9f25634e`. Nothing committed.

---

## The headline: the defect WAS still live as delivered, and is now fixed

The four agents built the whole apparatus correctly and **left the one line that
connects it unapplied**, because `agent_runtime/execution/factory.py` was in no
agent's file-set. Delivered state, reproduced by driving the real production
composition:

```
claims_path("/Users/parthpahwa/Downloads") = True     # the backend claims it
composite = CompositeBackend
composite.default = StateBackend                       # ...and memory answers it
```

`_composed_deep_backend` built `CompositeBackend(default=StateBackend(), …)`. A
host-absolute path is not a prefix of any route, so it landed on the default —
agent memory — which holds nothing at that path and answers `ls` with an **empty
listing as a success**. That is the original defect, unchanged, behind 9151
passing tests. The suite was green because the one test that pinned it was
marked `KNOWN FAILING` with an accurate note saying the adoption line was out of
scope.

**Fix applied** (`services/ai-backend/src/agent_runtime/execution/factory.py`):

```python
return CompositeBackend(
    default=guarded_default(StateBackend(), workspace_backend),
    routes=routes,
)
```

`guarded_default` returns the default untouched when there is no workspace
backend, so every non-desktop run composes byte-for-byte as before (pinned by
`test_non_desktop_composition_is_unchanged`).

### Answer to the central question

**No — an ungranted host-absolute path can no longer produce an empty-success.**
Measured after the fix, through `_composed_deep_backend`, with zero grants:

| scenario                                 | asked? | entries             | error                                             | empty-success |
| ---------------------------------------- | ------ | ------------------- | ------------------------------------------------- | ------------- |
| ungranted, user approves (with root)     | yes    | `/downloads/q4.csv` | none                                              | **no**        |
| ungranted, user declines                 | yes    | `None`              | "The user did not grant access…"                  | **no**        |
| ungranted, production resume shape       | yes    | `None`              | "Access to that folder could not be established…" | **no**        |
| already granted, host path, no root echo | yes    | `None`              | "The user did not grant access…"                  | **no**        |
| zero grants, `ls /`                      | n/a    | `None`              | "…nothing has been granted…"                      | **no**        |

Every path is a real listing, an ask, or an explicit refusal. There is no
remaining lane that answers empty.

---

## Second defect found: the grant card could never render

`runtime_api/schemas/events.py::_approval_requested_payload` projects approval
payloads through a strict **allow-list**. `workspace_grant` was absent, so the
block the client keys its card on was **stripped before reaching the client**.
The producer stamped it correctly and the client parsed it correctly; the
projection in between deleted it. The run would have parked on a generic
approval card with no folder in it and no Grant button.

Fixed by adding `_workspace_grant_payload`, which re-validates the block key by
key (the treatment `presentation` already gets, and for the same reason: it
reaches the client and drives what the user reads before granting). `path` is
required and length-capped to the producer's own bound; a block without it is
dropped rather than rendered half-built. Measured:

```
workspace_grant = {"path": "/Users/parthpahwa/Downloads",
                   "folder_name": "Downloads",
                   "platform": "posix", "mode": "read_only"}
block survives: True    no path -> dropped: None
not a dict -> dropped: None    long path capped: 1024
```

---

## Suite numbers I measured

| suite                 | command                                   | result                                        |
| --------------------- | ----------------------------------------- | --------------------------------------------- |
| ai-backend unit       | `pytest tests/unit -q`                    | **9154 passed, 109 skipped, 0 failed**        |
| chat-surface          | `vitest run --root packages/chat-surface` | **3392 passed, 1 failed** (323 files)         |
| apps/desktop          | `vitest run --fileParallelism=false`      | **1275 passed, 1 todo, 0 failed** (118 files) |
| api-types             | `vitest run`                              | **120 passed** (16 files)                     |
| native workspace-fs   | `node --test *.test.mjs`                  | **42 passed, 0 failed**                       |
| tools/desktop-runtime | `node --test *.test.mjs`                  | **16 passed, 0 failed**                       |

Typechecks — all `tsc --noEmit`, exit 0, zero output:

- `packages/chat-surface`, `packages/api-types`, `apps/desktop`.

Lint: `ruff check src/ tests/` clean; `ruff format --check` — 1579 files already
formatted.

The single chat-surface failure is the pre-declared exception:
`src/destinations/run/canvasLifecycle.test.ts` needs an ai-backend venv at a path
this environment does not have. Not related to this work.

### Two measurement traps worth recording

**The desktop suite cannot be verified naively.** This worktree has no
`node_modules`, so `@0x-copilot/chat-surface` resolves through the npm-workspace
symlink to the **main checkout**, which lacks the in-flight port. A plain run
therefore measures the wrong code — that is why one agent reported 3 failures in
`destinationBinders.workspaceGrant.test.tsx` for "exports that don't exist".
They exist; its run could not see them. Verifying honestly needs, in temporary
configs (which I created, used, and deleted):

- vitest: alias **both** `@0x-copilot/chat-surface` → `src/index.ts` **and** the
  subpath prefix `@0x-copilot/chat-surface/` → the package dir (`bootstrap.tsx`
  deep-imports the composer CSS, so a bare-specifier alias alone fails at
  collection); invoke from `apps/desktop`, not the repo root.
- tsc: the same two `paths` entries plus `"rootDir": "../.."`.

With those, desktop is **1275/1275 green and typechecks clean against the
worktree's chat-surface** — which is the real seam evidence.

**`native-workspace-commit-helper.test.ts` is order-dependent.** Reported flaky
by two agents on unmodified code. My sequential run was clean; I did not
reproduce the flake and did not investigate it. Not part of this change.

---

## Verified on macOS by running it

- **The defect is gone.** Reproduced before the fix, re-measured after, through
  the real `_composed_deep_backend` — not a hand-built composite.
- **Both grant lanes traced end to end** over a fake broker: ask → approve →
  mount created → real listing served; and ask → decline → explicit refusal.
- **The security property holds, by reading the code and by measurement.** Every
  broker call is `self._client.<op>(resolution.mount.grant_id,
resolution.relative, …)` — `grant_id` plus a root-relative POSIX path, nothing
  else. `broker_client` posts only `{grant_id, path}`; its grant projection
  allow-lists `{grant_id, mount, mode, label, status}` — no path field exists.
  `HostRootIndex.cover` returns `target.relative_to(root)`. Host roots enter
  only from the grant flow and never travel back. My probes assert the recorded
  broker traffic contains neither `/Users` nor `Downloads`: **both False**.
- **Default-on is not default-access.** `feature-gate.test.ts` boots the real
  capability service and broker over HTTP with an empty grant store and a real
  temp folder that demonstrably contains a file, then asserts `/v1/fs/list`,
  `/v1/fs/read` and `/v1/fs/stat` all return **403 `grant_required`** — including
  for a host-absolute path. A `200 {entries: []}` there would be the original
  defect. I read this test and it runs green. On the backend side, zero grants
  makes `ls /` an explicit "nothing has been granted" refusal, not an empty list.
- **Traversal / device / reserved shapes fail closed BEFORE any ask**
  (`_aresolve` refuses `HostPathKind.UNSAFE` first), so a grant request can never
  launder an escape.
- **The read gate un-gating was the right call and was load-bearing.** The broker
  read credential used to be withheld unless the macOS-only writable authority
  existed, so every Windows and dev run got
  `RUNTIME_ENABLE_DESKTOP_WORKSPACE=false` and no `/workspace/` mount at all.
  The write lane is untouched and still fails closed off macOS.
- **Native addon**: builds under `-Werror`, reads real bytes, and refuses a real
  symlink (`ELOOP`), `..` and an absolute path (`EPERM`). The ABI claim is
  measured, not assumed: a Node 25 build loads under Electron 43.

---

## Rests only on a CI job — Windows is unverified

This host is macOS/arm64. The Win32 `NtCreateFile` per-component walk in
`native/workspace-fs/src/workspace_fs.c` has **never been compiled or executed**.
The new `native-workspace-fs` matrix job on `windows-latest` is the first thing
that will. `/WX` is deliberately off there until a green log exists. Open
questions it settles: whether MSVC accepts the translation unit at all; whether
`InitializeObjectAttributes` comes from the SDK or the added fallback macro;
whether the reparse-point refusal actually fires; and whether
`int is_final = (*save == L'\0')` reads MSVC's `wcstok_s` context correctly.

### Windows filesystem access does not work today, for a reason upstream of all of this

I measured `deepagents.backends.utils.validate_path` directly:

```
'C:\\Users\\p\\Downloads'   -> REJECTED ValueError
                               "Windows absolute paths are not supported"
'/Users/p/Downloads'        -> '/Users/p/Downloads'
'\\\\server\\share\\rep'    -> '//server/share/rep'      (rewritten to POSIX)
```

It is called in `deepagents/middleware/filesystem.py` — the **tool** layer,
upstream of any backend — so a drive-absolute path never reaches our guard. The
tool returns `status="error"`, so this is an honest refusal and **not** an
empty-success; but product decision #4 (macOS **and** Windows) is **not
satisfied through the built-in file tools**, and no amount of backend work will
satisfy it. The fix belongs at the tool/middleware layer. The classifier itself
handles Windows shapes correctly; it is simply never given the chance.

---

## Gaps that remain

**1. The mid-run grant cannot complete in production. Fails closed; blocks the
feature.** This is the most important open item. Measured with the resume shape
production actually sends:

```
resume = {"decisions": [{"type": "approve"}]}
-> error = "Access to that folder could not be established.
            Ask the user to grant the folder again from the workspace settings."
```

`runtime_worker/handlers/approval.py::_resume_payload` (line ~1303) has no
`workspace_grant` branch, so a folder decision falls to the MCP-tool default,
which carries neither `grant_id` nor `root`. `WorkspaceGrantGate.request` then
refuses with `UNBOUND`. Compounding it, `RunDestination.tsx` wires
`onGranted: (approvalId) => handleApprove(approvalId)` and **discards the
`grant` argument**, so the `grantId` the port just returned never leaves the
renderer. `grant_id` is actually recoverable server-side (`_resolve_grant`'s
"exactly one newly-appeared grant" rule); **`root` is the genuinely missing
value.**

I did not close this, and deliberately did not close it by assuming the asked
folder is the granted root. The broker projection is path-free, so that
assumption is unverifiable: if the user granted a _parent_ of the folder they
were asked about, every later relative path would resolve against the wrong
directory and return the **wrong file** silently — worse than the empty listing
this work exists to remove. It needs a real root echo, which spans five places
and one product decision:

- forward `input.path` to the picker as `defaultPath` (`RequestFolderGrantParamsSchema`
  is `.strict()` on `{mode, label?}`; `FolderPicker` has no `defaultPath`);
- return the chosen root to the renderer — **this is the product decision**:
  `WorkspaceGrant` is currently path-free by construction and compile-time
  guarded by `type PathFree<T>` in `WorkspaceGrantPort.test.ts`;
- forward `grant.grantId` + root from `onGranted` into the decision POST;
- an approval-decision API field to carry them;
- a `workspace_grant` branch in `_resume_payload`.

The reconciliation permits this — a host path may be an input to the GRANT flow —
but it inverts a deliberate, test-enforced design decision, so it should not be
landed by a verifier without review.

**2. The same blocker re-asks for folders already granted.** Measured: a folder
granted proactively via the composer is readable at its virtual path
(`/downloads/q4.csv` → OK) but a host-absolute read of the same folder asks
again, because mounts built from the path-free broker snapshot have
`host_root=None` and `_cover` cannot match them. `_bind_grant` is already
written to adopt a root onto such a mount — it just never receives one, for
reason (1).

**3. `mount` is set to `grantId` on desktop** (`RendererGrant` carries no mount;
only the broker holds the derivation salt). Same identity, no host path,
non-reversible — but the port doc's "two grants on one tree share a mount"
property does not hold on desktop. Fix: project the broker mount onto
`RendererGrant` in `main/capabilities/types.ts`.

**4. `.cc__caption` margin exposure, knowingly left.** `.cc__path` was fixed
(desktop imports no `p` reset, so the UA's `margin-block: 1em` landed mid-card).
`.cc__caption` has the identical exposure but is shared with the shipped
`ConnectorConsentCard`, so zeroing it is a visual change to a surface with a
design-parity baseline. Should be measured with `tools/design-parity`, not
slipped in here. Affects three states of the new card too.

**5. `node-gyp` is only a transitive hoisted devDependency** (`12.4.0`, declared
nowhere). `build.mjs` fails loudly with remediation if absent, but it should be
an explicit devDependency of `@0x-copilot/desktop` — a `package-lock.json` edit
that was correctly refused while agents ran concurrently.

**6. `ENOSYS` runtime hole in `host-fs.ts`.** A loadable addon whose kernel
primitive answers `ENOSYS` yields `available: true`, and `host-fs.ts:928` maps
`ENOSYS`/`ENOTSUP` to `"unsupported"` → silent non-atomic fallback.
`selfcheck.cjs` closes this at build time; it does not close it for a user on an
older kernel than the build host.

**7. The CLI channel is single-host for prebuilds.** One npm tarball carries only
binaries the assembling machine compiled. A Windows user installing from a
macOS-assembled publish gets no binary and fail-closes with a stated reason.
`release-desktop.yml` has a multi-OS matrix; the CLI publish does not.

**8. `workspace_fs.c`, flagged and unfixed**: no `MB_ERR_INVALID_CHARS` on either
`MultiByteToWideChar`; `win_status_code` has no `STATUS_NOT_A_DIRECTORY` mapping
so it degrades to `EIO`; the empty-`rel` path hands a read-only root HANDLE to
`_open_osfhandle(..., 0)` as if writable.

**9. `main/index.ts` has no unit test** (Electron entry, not importable). The
testable seam is `feature-gate.test.ts`.

---

## Files I changed (beyond the four agents' work)

- `services/ai-backend/src/agent_runtime/execution/factory.py` — adopt
  `guarded_default` as the composite's default. **This is the fix for the live
  defect.**
- `services/ai-backend/src/runtime_api/schemas/events.py` — add
  `_workspace_grant_payload` + its `_Fields` keys and bounds, so the grant block
  survives the projection to the client.
- `services/ai-backend/tests/unit/runtime_worker/test_workspace_backend_wiring.py`
  — replace the stale `KNOWN FAILING` test.

On that last one: the old test caught `RuntimeError` and read _any_ such error as
"handed to memory". After the fix the path reaches the grant gate, which parks on
the real `langgraph.interrupt` and raises a `RuntimeError` **outside a graph** —
so the old assertion could not distinguish the fixed behaviour from the broken
one and could never pass. I did not weaken it; I made it stronger and more
specific: the consent seam is now injected so _who answered_ is observed rather
than inferred from an exception type, and the file went from 1 assertion of
absence to three tests covering approve, decline, and the non-desktop
composition being unchanged. `test_workspace_backend_wiring.py`: 14 passed.
