# Filesystem capability — program spine

Can the agent read and write the user's files, on macOS **and** Windows, without
dissolving the review/audit model the rest of the product is built on.

**Baseline:** `main@b349aca2` (2026-07-29).

**"C2"** throughout these PRDs — and in the helper's own comments
([build.mjs:10](../../../apps/desktop/native/workspace-commit-helper/build.mjs),
"Production writable C2") — means the workspace **broker-commit capability**
specified in
[PRD-C2](../generative-surfaces-v2-1/prds/PRD-C2-workspace-broker-commit.md): the
grant → prepare → approve → commit path that this program extends. `PRD-C2 D7`
(commit semantics by operation) and `PRD-C2 D9` (reconcile and recovery) are the
two sections downstream PRDs cite most.

## What is true today

Verified in code, not assumed:

| Capability                | macOS                    | Windows                        |
| ------------------------- | ------------------------ | ------------------------------ |
| Confined read             | ✅ `O_NOFOLLOW_ANY`      | ⚠️ **source only** — see below |
| Create file / mkdir       | ✅ `fclonefileat`        | ❌ **nothing**                 |
| Replace / delete / move   | ❌ refused in C          | ❌ **nothing**                 |
| Code execution with files | ❌ remote providers only | ❌ remote providers only       |

The Windows read cell was ✅ in the first draft of this spine and that was wrong.
[PRD-FS-03 C3](PRD-FS-03-windows-confinement.md) verified it: `native/workspace-fs`'s
Win32 `NtCreateFile` walk exists in source, is built by no script, is carried by no
`extraResources` entry, and therefore cannot be `require`d in a packaged app —
`loadNativeWorkspaceFs()` catches and returns `undefined`, so a packaged Windows
install falls back to the non-atomic `realpath`-recheck path. The cell flips to ✅ only
when the addon is actually built and packaged.

Two facts drive the whole program:

1. **[build.mjs:9](../../../apps/desktop/native/workspace-commit-helper/build.mjs)
   writes a non-executable sentinel on every non-darwin platform**, and
   [native-workspace-commit-helper.ts:172](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.ts)
   rejects `process.platform !== "darwin"` before spawn. Windows users are
   permanently read-only. This is deliberate and documented, not an oversight.
2. **The macOS helper refuses replace/delete/move** because macOS has no kernel
   compare-and-swap rename bound to inode+digest, and the author declined to
   "pretend advisory locks are a security primitive."

## Locked decisions

**D1 — Execution runs in a LOCAL sandbox, not a host shell.**
`run_in_sandbox` is ~70% built (25 modules: snapshot export, patch collector,
artifact publisher, workspace transfer, usage metering, readiness). What is
missing is a **local** provider — `providers/` holds only `langsmith.py` and
`openai_hosted.py`, both remote, which would ship a local-first user's granted
files off their machine.

Rejected: a free host shell. Not on general principle — it is what dev tools do
— but because this product ingests untrusted content through MCP connectors
(email, Discord, timelines, web), which turns "summarize this email" into a path
to arbitrary code execution; and because a shell writes files with no operation,
stage, preimage, or receipt, making the ledger's central question — _what did the
agent change?_ — unanswerable. Every control in this program becomes advisory the
moment `sh -c` exists beside it.

Accepted cost: a sandbox cannot run the user's toolchain against their real repo
in place. "Operate on my codebase" is a different product surface and is out of
scope here.

**D2 — Both platforms, Windows first.**
Windows is at zero writes while macOS already has create/mkdir, so Windows-first
maximises capability gained and stops POSIX assumptions baking into the seam.

**D3 — `RUNTIME_ENABLE_DESKTOP_FILESYSTEM` stays off, per-user opt-in.**
Fail-closed as today. An install never silently gains filesystem reach.

**D4 — the preimage/trash lives inside the granted root, at `<root>/.0xcopilot/trash/`.**
Added after the consistency pass: FS-04, FS-05 and FS-06 were each drafted against a
different location (inside the root; app-private outside the root; the app-private
staging run directory), which is not a stylistic difference — it changes whether
displacement is an O(1) rename or a copy, whether GC can run for a revoked grant, and
what `cleanup_prepared_stages` owns. FS-04 owns the substrate and its reasoning wins:
`open_parent` refuses any hop that crosses `st_dev`, so a trash under the root is
same-volume **by construction**, whereas `userData` is only same-volume by luck —
`command_prepare:850` already fails closed when it is not. The costs FS-05 raised are
real and are paid explicitly, not waved away: the directory is user-visible
(dot-prefix, `FILE_ATTRIBUTE_HIDDEN`, and a `.gitignore` of `*`), user-writable (so
integrity comes from the MAC'd journal row and the content digest, never from directory
permissions), and it must be unaddressable — the reserved segment is refused at the
gateway, in the helper, **and** on the read surface (list/glob/grep/stat/read), or the
trash becomes a read-around for content the agent may no longer read. FS-04 D1-D3 and
D10 carry the detail. FS-05 and FS-06 conform. (The `FILE_ATTRIBUTE_HIDDEN` half is
**unverified** and has no seam member yet — FS-04 D9.)

**D5 — the helper has no clock, and time is main-attested.**
There is no `time`/`clock_gettime`/`gettimeofday` call anywhere in
`workspace_commit_helper.c` and the FS-01 seam declares no time primitive. Rather than
add one, main stamps every wall-clock value (FS-04's `displaced_at_ms`, retention ages)
and the helper only _compares_ main-supplied numbers — which is what FS-04 D4 already
says about policy ("the helper only enforces arithmetic and never invents a number").
The consequence is stated wherever it matters: such a timestamp is **main-attested, not
helper-attested**, so it is not evidence against a hostile main, only against drift.
Adding `fs_time_ms()` to the seam is a real alternative and is deliberately not taken —
it would put a value the helper cannot verify inside the MAC'd record and imply an
attestation that is not there.

## The architectural keystone: one protocol, two platforms

`commit_entry()` in the native helper is ~15 lines. Everything around it — parse,
seal/verify, journal, IPC framing, MAC, attestation, permits, conservative
restart — is portable C that already exists and is already reviewed.

So this program is **not** "write a Windows helper". It is:

1. name the platform seam explicitly (the primitives, not the protocol);
2. move macOS behind it with zero behaviour change;
3. implement the same seam on Win32;
4. add verbs once, above the seam, so both platforms gain them together.

The seam sketch below is **informal and is not the contract**. It is kept only to
show the shape. [PRD-FS-01 §2 and §8](PRD-FS-01-platform-seam.md) hold the binding
names and signatures; where this sketch and FS-01 disagree, FS-01 wins, and a
downstream PRD that quotes a sketch name is quoting an alias, not an API.

```
open_confined(root, relpath)      -> handle      # openat+O_NOFOLLOW_ANY | NtCreateFile
identity_of(handle)               -> file_id     # dev+ino | FILE_ID_INFO (128-bit)
stage_content(bytes)              -> staged      # private staging dir, sealed+digested
commit_create(staged, parent, leaf)              # fclonefileat | staged-handle rename
commit_mkdir(parent, leaf)                       # mkdirat | NtCreateFile(FILE_CREATE|
                                                 #           FILE_DIRECTORY_FILE)
commit_replace(staged, parent, leaf, expected)   # RENAME_SWAP+verify | rename-by-handle
commit_delete(parent, leaf, expected, trash)     # renameatx_np(RENAME_EXCL)->trash
                                                 #   | rename-by-handle -> trash
commit_move(src, dst, expected)                  # renameatx_np(RENAME_EXCL)
                                                 #   | rename-by-handle
durable_barrier(handle)                          # fsync | FlushFileBuffers (see below)
```

Three corrections the drafting PRDs earned, folded in so nobody implements the
sketch: `CreateDirectoryW` takes a path string and has no parent-handle-relative
form, so using it would reintroduce a path-string write path (FS-02 D3); plain
`renameat` **silently clobbers an occupied destination** — probe-verified in FS-05
(probe 3) — so delete/move must use the kernel's own exclusivity primitive; and
`MoveFileExW` re-resolves a path and discards the identity the walk just proved,
so Windows renames by handle (FS-05 D2, FS-06 D5).

**Windows is favourable for the verbs macOS refuses.** Mandatory share-mode locking
means "Excel has the file open" fails cleanly with a specific error instead of
racing, which is the exact hazard the macOS helper refuses over.
`GetFileInformationByHandleEx(FileIdInfo)` supplies the `dev+ino` analogue.
`ReplaceFileW` additionally writes the displaced content to a backup file — replace
plus preimage in one documented call — but it takes three **paths**, so FS-06 D5
does not put it on the primary effect path. FS-04 D9 models both capture
strategies; the primary is FS-04's Strategy B (handle-relative displace into the
trash, then rename in) on **both** platforms, and `ReplaceFileW` survives only as
FS-06's reported fallback if the rename information class is unavailable on the
minimum supported Windows build. The `staged_before_effect` byte on the preimage
row is what keeps the journal honest about which one ran.

Windows is _harder_ in two places. Create: NTFS has no `fclonefileat` equivalent, so
create becomes a private staged file renamed by handle rather than a clone. And
durability: **Windows has no documented `fsync(dirfd)` analogue.** `FlushFileBuffers`
on a directory handle is undocumented, and the volume-handle barrier needs
administrator rights a per-user app does not have. So `durable_barrier` on a
directory is _provable_ on POSIX and _not provable_ on Win32. That asymmetry is not
hidden behind a uniform return code — FS-01 exposes it as a compile-time capability
constant, and the consequence propagates: on Windows `applied` means
**observed-applied, not power-loss-durable**, which is why FS-07 re-observes rather
than trusting a terminal journal state. Whether this bites in practice is FS-02's
SPIKE-W1; the wording must match the measurement and must never be upgraded to a
guarantee.

## The guarantee, restated

The macOS refusal treats the race as a **security** problem, correct against a
hostile concurrent writer. In `single_user_desktop` the realistic adversary is
_Excel holding the file open_ — a **data-loss** problem, solved by preimage +
detection + restore rather than by CAS.

So the guarantee this program commits to is:

> Act atomically where the platform allows. Verify what was displaced. Roll back
> or retain the preimage. Never claim an outcome that was not observed.

That is achievable on both platforms and is honest about what it does not
promise.

## PRDs

| PRD   | Scope                                                   | Depends on                                                  | Status                             |
| ----- | ------------------------------------------------------- | ----------------------------------------------------------- | ---------------------------------- |
| FS-01 | Platform seam + macOS moved behind it, zero behaviour Δ | —                                                           | specified                          |
| FS-02 | Windows commit helper — create + mkdir                  | FS-01                                                       | specified                          |
| FS-03 | Windows confinement + attestation parity                | FS-01 (parallel to FS-02)                                   | specified                          |
| FS-04 | Preimage + trash, both platforms                        | FS-01                                                       | specified                          |
| FS-05 | delete + move, both platforms                           | FS-01, FS-04 (+FS-02/03)                                    | specified                          |
| FS-06 | replace, both platforms (+ `RENAME_SWAP` spike)         | FS-01, FS-02, FS-04                                         | specified                          |
| FS-07 | Post-crash reconciliation, both platforms               | FS-01, FS-04                                                | specified                          |
| FS-08 | Local sandbox provider + patch-back                     | FS-03 (Windows read), FS-02/04/05/06, FS-07 — **not FS-01** | specified; consent surface unowned |
| FS-09 | Enablement, consent UX, capability-honest reporting     | FS-02 (soft: 04/05/06/07)                                   | specified                          |

**FS-08 exists** ([PRD-FS-08](PRD-FS-08-local-sandbox-provider.md)). D1 — the
locked decision that execution runs in a local sandbox — is therefore no longer
unredeemed, and the "~70% built, missing a local provider" claim is now reviewed
and holds. FS-08 was drafted after the other eight and reconciled against them in
a later pass; [00-consistency-report.md §9](00-consistency-report.md) records what
that pass changed and what it left open.

Two things about its row are deliberate:

- **It does not depend on FS-01 for reads.** FS-01's Out of scope excludes "the
  `native/workspace-fs` N-API read-side addon, `host-fs.ts`, and everything on
  the read path" _and_ excludes FS-08 by name. FS-08's first draft named FS-01 as
  its read dependency via `fs_open_root`/`fs_stat_at`; those are commit-helper
  primitives inside a spawned C process that no Python service can call. The real
  read surface is the broker's `/v1/fs/*`, whose Windows half is FS-03's — and is
  today's unpackaged, non-atomic `realpath`-recheck fallback until FS-03 lands.
  FS-08 depends on FS-01 only negatively: it adds no seam member and declares no
  verb in `fs_platform.h`.
- **Its consent surface has no owner.** FS-09's Out of scope says "The sandbox
  provider and patch-back (FS-08)", so the six surfaces FS-08 routes to FS-09 —
  readiness-reason rendering, "what to install", image acquisition, review of an
  imported overlay revision, the import affordance, and the pre-approval warning
  that a patch's verbs cannot commit on this platform — are owned by nobody. A
  provider that clears every readiness gate still yields no user-reachable
  capability without them. Either FS-09 drops the exclusion or a tenth PRD takes
  them. Left open deliberately, in the same spirit as the cross-volume-grant gap.

**Ordering constraints that are not visible from the dependency column:**

- Neither FS-02 nor FS-03 alone makes Windows writable. FS-02 registers `win32` in
  the helper registry; FS-03 supplies the confinement probe. `createProductionWorkspaceAuthority`
  needs both, and that is pinned by a test in FS-03 T7.32.
- FS-07 must ship **with or ahead of** FS-05 and FS-06. Those PRDs create the crash
  points; landing a destructive verb without its reconciliation lane is how a
  half-finished commit becomes silent data loss.
- FS-04 must land before FS-05 and FS-06 (they cannot displace bytes without a
  preimage), and its portable `stage_preimage` must exist on **both** platforms
  first. (`fs_stage_preimage` was the name in FS-04's first draft; it is not a
  seam member — see [FS-01 §2](PRD-FS-01-platform-seam.md)'s effects block.)
- **Every verb that fills the trash must land with a way to empty it back.**
  FS-04's restore is a `CREATE`-from-preimage change set whose precondition is
  `exists: false` (FS-04 D6), which covers exactly one shape: a file whose name is
  now free. Two shapes are **not** covered and each is a release blocker for the
  PRD that first produces them — FS-05 for a deleted **directory** (`CREATE` cannot
  materialise a directory), FS-06 for a **replaced file** (its name is occupied by
  the replacement, so `exists: false` fails). Each is now written into the PRD that
  owns it, with two admissible resolutions and a DoD item: FS-05's implementation
  plan and FS-06's Phase 1b. Neither is _solved_ here — the owning PRD picks.
  [00-consistency-report.md](00-consistency-report.md) records how they were found.

**Protocol and journal version ladder.** Four PRDs change the helper wire and each
was drafted assuming it went first. One ladder, here, so there is never a second
number in flight (helper and main ship in one artifact, so there is no
mixed-version window and no compatibility shim is needed):

| Version             | Owner | Change                                                                                                                                       |
| ------------------- | ----- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `PROTOCOL 2`        | today | baseline                                                                                                                                     |
| `PROTOCOL 3`        | FS-04 | `content_source` on prepare; requests 13-15; `PREIMAGE_*` failures; **and** the per-entry commit-result block that FS-05 and FS-06 both need |
| `JOURNAL_VERSION 4` | FS-04 | preimage trailer; `journal_load` accepts v3 **and** v4                                                                                       |
| `PROTOCOL 4`        | FS-07 | `RECONCILE_OBSERVE = 16`; two more bytes on the per-entry block; no journal bump                                                             |

The per-entry commit-result block is **one** structure, not two. FS-05 needs a
`reason` per entry and FS-06 needs a preimage reference per entry; they are fields
of the same repeat, defined once in FS-04's §6 wire section and populated by
whichever PRD lands. Whoever implements first writes it; the others populate it and
bump nothing. `RECONCILE_CLAIM` and the no-`prepared` branch of `command_commit`
always emit `entry_result_count = 0` — a reconciliation that cannot enumerate
entries never fabricates them.

**Crash-fault id ladder** (the `test_crash_boundary` selector on private fd 7,
faults 1-4 today — verified at
[workspace_commit_helper.c:610-612](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)
and `:931`): FS-04 takes 5 (`preimage_lease`) and 6 (`preimage_effect`); FS-07
takes 7 (`after ARMED, before effect`) and 8 (`after effect, before OBSERVED`);
FS-06's replace boundaries take 9 (`after swap, before preimage record`) and 10
(`after preimage record, before swap`); **FS-05 takes 11 (`delete_pre_rename`) and
12 (`delete_post_rename`)**. Whoever lands first takes the next free pair and
updates this table rather than reusing a number. The ladder is allocated here
precisely so an unlanded PRD's numbers are not squatted by whoever ships first.

## Guardrails for every PRD

- Do **not** add a second write path. Every mutation goes through the operation
  gateway, the stage, and the commit protocol.
- Do **not** weaken confinement to make a verb work.
- Do **not** report an outcome that was not observed — indeterminate is a valid,
  required result.
- Do **not** let the model choose a path. Grants are user-issued and durable;
  **permits** are one-use. The two are different objects and collapsing them in
  copy or in code is how a consent surface lies (FS-09 Context).
- Do **not** implement a verb on one platform only. Above the seam, verbs land on
  both or neither — and therefore do not declare a verb in `fs_platform.h` before
  the PRD that implements it everywhere (FS-01 D9).
- Do **not** assert Win32 or macOS API behaviour that no host has executed. Every
  such claim carries an explicit `unverified` marker and a named spike with the
  outcome that would change the design.
