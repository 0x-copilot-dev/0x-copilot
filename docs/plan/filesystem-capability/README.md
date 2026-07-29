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

| PRD   | Scope                                                   | Depends on                                                  | Status                                                                        |
| ----- | ------------------------------------------------------- | ----------------------------------------------------------- | ----------------------------------------------------------------------------- |
| FS-01 | Platform seam + macOS moved behind it, zero behaviour Δ | —                                                           | specified                                                                     |
| FS-02 | Windows commit helper — create + mkdir                  | FS-01                                                       | specified                                                                     |
| FS-03 | Windows confinement + attestation parity                | FS-01 (parallel to FS-02)                                   | specified                                                                     |
| FS-04 | Preimage + trash, both platforms                        | FS-01                                                       | specified                                                                     |
| FS-05 | delete + move, both platforms                           | FS-01, FS-04 (+FS-02/03)                                    | specified                                                                     |
| FS-06 | replace, both platforms (+ `RENAME_SWAP` spike)         | FS-01, FS-02, FS-04                                         | specified                                                                     |
| FS-07 | Post-crash reconciliation, both platforms               | FS-01, FS-04                                                | specified                                                                     |
| FS-08 | Local sandbox provider + patch-back                     | FS-03 (Windows read), FS-02/04/05/06, FS-07 — **not FS-01** | specified; consent surface = FS-09 D20-D25; ships only if SPIKE-L2 passes     |
| FS-09 | Enablement, consent UX, capability-honest reporting     | FS-02 (soft: 04/05/06/07); FS-08 for the execution half     | specified; also owns cross-volume refusal (D19) + execution consent (D20-D25) |

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
- **Its consent surface is FS-09's** — decided, after a pass where it was owned
  by nobody. FS-09's Out of scope used to disclaim FS-08 by name while FS-08
  routed six surfaces to it. The call: **execution consent is consent**, and
  splitting it across two documents produces two consent models, which is what
  this program exists to prevent. FS-09 dropped the exclusion and owns every
  surface where a human is asked to agree to any of it — the execution switch
  (D20), the reason rendering and "what to install" (D21), the image-download ask
  (D22), what leaves the granted folder (D23), the import review and the
  unsupported-verb pre-check (D24), and revoking while a sandbox is live (D25).
  FS-08 keeps the provider, the runtime and its drivers, the isolation probe and
  attestation, the image contract, transfer, cancellation and teardown, the C1
  importer, and the prepare/authorize lane. The dependency runs one way: FS-08's
  code depends on nothing in FS-09, and nothing in FS-08 is user-reachable
  without it — a shipping-order constraint, recorded in FS-08's DoD, of the same
  kind as the Windows code-signing certificate.
  [00-consistency-report.md §10.2](00-consistency-report.md) records the call and
  what it did **not** close: FS-08's `decisionLedgerId` question, which FS-09
  declines by name; whether an imported revision reaches `projectWorkspaceStage`
  at all, which is routed back to FS-08 as a wiring question; and SPIKE-L2.

**Cross-volume grants are also decided**, and the same way — by naming an owner
rather than a reader. A folder picked on a second volume used to mint a grant
that looked granted, passed `listActive`, and failed only at prepare, after the
user had been shown an approval sheet. [FS-09 D19](PRD-FS-09-enablement-consent.md)
now **refuses at grant time, before the grant exists**, names the volume, and
offers read-only rather than imposing it. FS-02 D7's same-volume rule is
unchanged and is still the enforcing check; D19 asks the same question earlier.
Per-volume app-private staging — which would make such a folder writable — is a
separate future slice, deliberately not designed, because it moves where staged
bytes live and that is a stated invariant of the helper's header.
[00-consistency-report.md §10.1](00-consistency-report.md) records it.

**A grant-time gate alone did not close it, and the first draft said it had.**
An adversarial re-read found two doors that a check on the create path cannot
reach, and both produce the same artifact the decision exists to remove: a grant
already **persisted** by a build that predates D19 is rehydrated rather than
re-derived, so it still displays as writable and still dies at prepare; and a
root on a volume the helper refuses to open makes `rootIdentity` **throw**, so
the refusal path never runs, the user gets a raw error instead of the copy, and
`read_only` — which needs no helper at all — could not be minted either. D19
grew §8 and §9 for them: the volume term moves into the grant-usability
predicate FS-09 was already extracting, and a third typed refusal covers the
unopenable volume. [00-consistency-report.md §11](00-consistency-report.md)
records what was tried, what broke, and what is still open.

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

## Spike register

Spikes were scattered across nine PRDs, which made two questions unanswerable
from any single document: _how many are there_, and _which of them can stop a
PRD_. This is the index. **The owning PRD's own text is normative** — it names
the API, the exact experiment and the outcome; nothing here restates an
experiment, and where this table and a PRD disagree the PRD wins.

Every row answers the one question a plan needs: **does this block
implementation, or does it only refine what the document is allowed to claim?**
Three values, and they mean different things:

- **BLOCKS — ship gate.** The PRD (or the program) cannot ship, and in some cases
  cannot start, until this returns.
- **blocks a decision.** One named decision's code cannot be written until this
  returns; the PRD ships either way, via a documented fallback.
- **refines wording.** The design does not depend on the outcome. By rule the
  answer may only make the text **more** cautious, never less.

> **THE DECISIVE ONE — SPIKE-L2, which decides whether a whole PRD exists.**
>
> **Can a Windows container runtime observe all ten isolation controls?** The
> spine's guardrail is that a verb lands on **both platforms or neither**, and
> FS-08 D4 applies it: if a control cannot be observed on Windows, the local
> provider is unavailable on Windows **and therefore does not ship on macOS
> either**. So this is not "the Windows half is late" — it is the spike that
> decides whether FS-08 ships at all, and with it the only redemption locked
> decision D1 has. It also carries the elevation question (`wsl --install` is
> expected to need elevation once, done by the user outside the app), which if
> judged unacceptable product-wise has the same consequence. Nothing else in this
> register can end a PRD. Book the Windows host **before FS-08 is scheduled**,
> not before it merges.

### Executed — measured on a macOS host

Recorded in [SPIKE-RESULTS.md](SPIKE-RESULTS.md) (host: macOS 15.6.1, arm64,
APFS). These were **run**, not reasoned about.

| id      | owner | what it tested                                                           | result                                                                                                   |
| ------- | ----- | ------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------- |
| SPIKE-A | FS-06 | `renameatx_np(RENAME_SWAP)` semantics + `RENAME_EXCL` `EEXIST`           | ✅ all assertions passed; an fd opened before the swap still reads the original inode. **One host only** |
| SPIKE-B | FS-01 | `sizeof(struct journal_record)` / `offsetof(mac)` for two static asserts | ✅ 358 / 325 exactly as hand-derived; 1 byte tail padding                                                |
| SPIKE-C | FS-01 | that the helper builds here, so the golden transcript has a baseline     | ✅ builds; `Mach-O arm64`, mode `0500`                                                                   |

**SPIKE-A does not discharge FS-06 D1.** It establishes the primitive on one
machine, one OS version, one volume type — which is exactly what D1's S1-S7
matrix exists to go beyond, and FS-06's DoD still requires a `## Spike result`
section that does not exist yet. It also does not prove the absence of a race:
the spine's wording (_act atomically where the platform allows, verify what was
displaced_) is what this result supports, and nobody may upgrade FS-06's language
to "compare-and-swap" on the strength of it.

**Naming collision, flagged rather than silently lived with:** `SPIKE-C` (above)
and FS-03's `SPIKE-C1…C4` are different things one character apart. Rename the
executed one before a reader conflates "SPIKE-C passed" with "the confinement
spikes passed".

### Open — Windows commit helper (FS-02)

| id       | tests                                                                                           | a result that changes the design                                                                                                            | blocks?                                                           |
| -------- | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| SPIKE-W3 | do extra `stdio` fds beyond index 2 survive Node→MSVC-child spawn on Windows                    | fds do not arrive → Path B (stdin key prologue + path-plus-expected-identity). D11 must not name a path before this returns                 | **BLOCKS — ship gate.** Gates everything else in FS-02; run first |
| SPIKE-W2 | does a same-volume rename-by-handle + `SetSecurityInfo` repair reproduce the inherited DACL     | repair not reliably expressible → document the divergence and surface it in the receipt; do **not** move staging into the user's folder     | blocks a decision (D10)                                           |
| SPIKE-W1 | directory-entry durability after `FlushFileBuffers` under hard power-cut, ≥200 commits          | loss observed → FS-07 re-observes on Windows and the receipt carries `durability: observed_not_proven`. Zero loss → "observed in practice"  | refines wording — never upgrades to a guarantee                   |
| SPIKE-W4 | is `FileRemoteProtocolInfo` a sound local/remote test                                           | it succeeds locally or fails remotely → fall back to `GetDriveTypeW` + `\\` check, and say the remote test is advisory                      | blocks a decision (D7)                                            |
| SPIKE-W5 | free space by **handle** (`FileFsFullSizeInformation`) vs `GetDiskFreeSpaceExW`                 | handle form unavailable → FS-04 D4's `MIN(main, helper)` collapses to main's hint alone on Win32. A real weakening; must be stated in FS-04 | blocks a decision (FS-04 D4's Win32 body)                         |
| SPIKE-W6 | `NtQueryDirectoryFileEx` at-least-once delivery while the caller renames into the same dir      | no guarantee → `journal_reconcile_startup` must snapshot names before mutating — a **portable** change, so it must be known first           | blocks a decision (FS-01 D8's iterator)                           |
| SPIKE-W7 | `FILE_ID_INFO.FileId` stability across within-volume rename and close/reopen, NTFS **and** ReFS | unstable → the Win32 identity encoding changes and every verb's identity check loses its anchor; ReFS is refused by `fs_volume_supported`   | blocks a decision (D6 / `FS_IDENTITY_BINDING_BYTES`)              |

### Open — Windows confinement (FS-03)

| id       | tests                                                                                     | a result that changes the design                                                                                               | blocks?                                                                                        |
| -------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| SPIKE-C1 | can an AppContainer child **serve and connect on loopback** without elevation             | either direction fails → `mechanism_unavailable`, Windows writes stay off, and FS-02's helper is reachable only from tests     | **BLOCKS — ship gate.** Run before the launcher is written                                     |
| SPIKE-C2 | does CPython load under a restricted token; does System32 grant `NT AUTHORITY\RESTRICTED` | clean load → a second viable mechanism that does not touch network isolation. Otherwise the option is recorded **closed**      | conditional — the fallback if C1 fails, though a clean result may displace AppContainer anyway |
| SPIKE-C3 | `RtlDosPathNameToNtPathName_U` vs a `\\?\` prefix for the root conversion                 | ntdll export unusable → the `\\?\` prefix, sound only because D4's grammar already canonicalised the input; say so in the code | blocks a decision (D4's conversion step)                                                       |
| SPIKE-C4 | which of `OBJ_DONT_REPARSE` / `FILE_OPEN_REPARSE_POINT` actually fires                    | if `OBJ_DONT_REPARSE` never fires, the attribute check is the **only** control and the comment must say so                     | refines wording — both flags stay either way                                                   |

### Open — local sandbox (FS-08)

| id       | tests                                                                                                          | a result that changes the design                                                                                                                                        | blocks?                                                                                                    |
| -------- | -------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| SPIKE-L2 | Windows: `wsl --install` elevation, the whole flag matrix, and whether the probe observes **all ten** controls | a control cannot be observed → the provider is unavailable on Windows, and by D4 it does not ship on macOS either                                                       | **BLOCKS — the program.** See the callout above                                                            |
| SPIKE-L5 | Apple `container`: (a) the flag spellings, (b) whether a per-container VM boundary is observable at all        | (a) a control has no equivalent → the Apple driver is dropped and macOS ships podman/docker only. (b) not observable → the driver declares `"container"`, not `microvm` | (a) blocks a decision (driver registry); (b) blocks one **claim** — `satisfies()` accepts both identically |
| SPIKE-L4 | does `blob_store.stat(result_digest)` resolve for bytes published by the patch collector                       | it does not → the importer resolves through the artifact service; changes the overlay entry's `content_ref` construction, nothing else                                  | blocks a decision (D12's importer path)                                                                    |
| SPIKE-L6 | are tmpfs pages charged to the container memory limit                                                          | not charged → the `workspace + tmp + 256 MiB ≤ memory` validator is dropped as over-restrictive and the defaults are re-derived                                         | refines — decides one validator's existence; structure unchanged                                           |
| SPIKE-L3 | is a CPU quota's **enforcement** observable in a bounded probe, or only its acceptance                         | observable → `cpu_quota_accepted` becomes `cpu_quota_enforced`. Not → the field keeps its meaning and the docstring says so                                             | refines — attestation vocabulary only                                                                      |
| SPIKE-L1 | `sandbox-exec`'s deprecation status on the minimum supported macOS                                             | deprecated with a removal date → **nothing in FS-08 changes** (it never uses it), but FS-03/FS-09's confinement story inherits a clock                                  | refines — and FS-08 must not be read as evidence it is fine                                                |

### Open — consent (FS-09)

| id       | tests                                                                                                                                         | a result that changes the design                                                                                                                        | blocks?                                                                              |
| -------- | --------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| SPIKE-V1 | is equality of the 16-hex `FILE_ID_INFO.VolumeSerialNumber` a sound same-volume test — incl. a cloned/imaged volume and a mounted-folder path | duplicate serials on a clone → the comparison moves to `GetFinalPathNameByHandleW(VOLUME_NAME_GUID)` and **FS-02 D6's** persisted encoding is revisited | blocks a decision (how `volumeId` is spelled on Win32) — **not** whether D19 refuses |

**SPIKE-V1 fails in the dangerous direction if ignored:** a duplicate serial reads
as "same volume" and lets a cross-volume grant through to die at prepare, which
is the exact defect D19 exists to prevent. It **shares a Windows host with
SPIKE-W7** — run them together rather than booting Windows twice. Until it runs,
every Win32 statement says "same volume _serial_", never "same volume".

### Open — PRD-local spikes with no program id

These are cited across documents by their local number and are real spikes; they
simply never got an id.

**The last two rows were found by an adversarial re-read and were in no register
at all** — not here, not in an owning PRD's numbered spike list, not in a DoD
([00-consistency-report.md §11](00-consistency-report.md)). Both are ship gates,
and both are the kind that disappears quietly: FS-02 D2's is written inside a
prose paragraph as "_unverified — spike required_" with no number, and FS-06 D5's
lives inside a blockquote in a PRD that has **no** open-questions section, so it
had nowhere to be listed. That is the failure mode this register exists to
prevent, arriving in the register's own blind spot: a spike is only tracked if
someone gave it a name.

| where                            | tests                                                                                                                                                                                                                                                                                       | a result that changes the design                                                                                                                                                                                                                                   | blocks?                                                                                                                                                                                                  |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **FS-06 D1, S1-S7**              | `RENAME_SWAP` across the **real support matrix**: oldest shipped floor, HFS+, durability barrier, `RENAME_OPENFAIL` variants, case-insensitive volumes, active snapshots, cost                                                                                                              | S1 failing on a shipped floor ⇒ **stop and re-plan** — raise the floor or gate `replace` by OS version. Never ship a verb that fails at runtime on a supported OS                                                                                                  | **BLOCKS — ship gate.** "Everything below D1 is contingent on D1"                                                                                                                                        |
| **FS-05 D9, spike 1**            | does `FileRenameInfoEx` honour a directory `HANDLE` in `RootDirectory`, and does it **reject** a `FileName` containing a separator                                                                                                                                                          | failure → `NtSetInformationFile(FileRenameInformationEx)`, then non-`Ex` `FileRenameInfo`, recorded as taken. **Never** `MoveFileExW`                                                                                                                              | **BLOCKS — ship gate** for the Windows half of FS-05/FS-06. Shared with FS-02 D2 and FS-06 D5; the separator clause **is** the confinement property                                                      |
| **FS-05 D9, spike 2**            | does renaming a directory handle fail when a descendant is open                                                                                                                                                                                                                             | if it succeeds, nothing changes — D6 already refuses non-empty directories                                                                                                                                                                                         | refines — recorded because macOS allows it and a reviewer will ask                                                                                                                                       |
| **FS-04, spike 1**               | `renameatx_np(RENAME_EXCL)` on APFS: atomicity under concurrent creation, dangling symlink, `O_NOFOLLOW_ANY` fds, availability on the minimum macOS                                                                                                                                         | failure → restore becomes `fclonefileat` from the preimage fd, the preimage is **copied not consumed**, and the disposition model gains `restored_copy` + a second GC trigger                                                                                      | blocks a decision (FS-04's restore/disposition model). SPIKE-A confirmed only the `EEXIST` clause, on one host                                                                                           |
| **FS-04, spike 2**               | `ReplaceFileW` writing its backup into a subdirectory while ancestors are held without `FILE_SHARE_DELETE`                                                                                                                                                                                  | failure → FS-06 keeps Strategy B, or adopts the backup with a second rename                                                                                                                                                                                        | blocks a decision — **FS-06's fallback only**; D5 already chose Strategy B as primary on confinement grounds                                                                                             |
| **FS-04, spike 4**               | APFS `f_bavail` vs `volumeAvailableCapacityForImportantUsageKey` with local snapshots present                                                                                                                                                                                               | material divergence → main becomes the authoritative source and the helper keeps only its `MIN` sanity check (no Foundation dependency in the helper)                                                                                                              | blocks a decision (the budget denominator)                                                                                                                                                               |
| **FS-04, spike 5**               | Win32 inherited-ACE behaviour on a same-volume move — the **restore** direction                                                                                                                                                                                                             | divergence → displacement must record and re-apply the original security descriptor: new state on the row, plus a metadata-policy decision for PRD-C2 D7                                                                                                           | blocks a decision (the preimage row's shape)                                                                                                                                                             |
| **FS-02 D2, property 3**         | the exact status `FileRenameInfoEx` with `Flags = 0` returns when the destination leaf is occupied by (a) a regular file, (b) a directory, (c) **a junction or a file symlink**                                                                                                             | (c) _followed_ instead of colliding → the leaf gains a symlink-follow hazard `create` does not otherwise have, and needs the same explicit `FILE_ATTRIBUTE_REPARSE_POINT` refusal the walk applies, **before** the rename                                          | **BLOCKS — ship gate** for FS-02's `create`. It is a confinement property, not a wording one. Run alongside SPIKE-W3                                                                                     |
| **FS-06 D5, the Windows mirror** | (a) is `FileRenameInformationEx` + `FILE_RENAME_POSIX_SEMANTICS` available on the pinned minimum build; (c) the exact status when the target is held with an incompatible share mode; (d) does `fs_carry_metadata`'s Win32 body reproduce the **effective** DACL, not just the explicit one | (a) unavailable → `replace` is gated by OS version or the floor is raised; (c) is the error the whole "Windows detects the open holder" claim (D6) rests on; (d) failing means a carry-over silently drops inherited ACEs — the exact failure D8 exists to prevent | **BLOCKS — ship gate** for FS-06's Windows half: "must answer (a), (c) and (d) **before D5 is implemented**". (b) is FS-05 D9 spike 1, already listed; (d) is SPIKE-W2's question from a third direction |

**One observation this register makes visible:** FS-02 **SPIKE-W2**, **FS-04
spike 5** and **FS-06 D5(d)** ask the same Win32 question — what an inheritable
ACE does across a same-volume rename — from three directions (staging→user
folder, trash→restore, and replace's metadata carry-over). The consistency pass
consolidated three duplicated spikes but not this family. They are not obviously
the same experiment, so they are **not** consolidated here either; whoever books
the Windows host should decide whether one session answers all three. Three
askers of one question is a stronger signal than two, and it is the argument for
running it early rather than per-PRD.

### Consolidated — do not run a second copy

Recorded by [00-consistency-report.md §5](00-consistency-report.md), because
running one experiment three times is how three answers happen:

- **FS-04 spike 3** and **FS-05 D9 spike 3** are both **SPIKE-W1**.
- **FS-05 D9 spike 4** is **SPIKE-W7**.
- `RootDirectory`-relative rename has three consumers (FS-02 D2 property 2/3,
  FS-05 D9 spike 1, FS-06 D5) and **one** experiment: FS-05 D9 spike 1.

### Not spikes — decisions with the same blocking power

Listed so nobody waits for an experiment that is not coming. Full list and
reasoning in [00-consistency-report.md §7](00-consistency-report.md):

- **The Windows code-signing certificate** (FS-02 D15 + FS-03 D9). Without it,
  FS-02 and FS-03 together ship a capability nobody can turn on. Product call.
- **The minimum supported Windows build.** Decides which of three rename paths
  ships. FS-02 owns pinning it.
- **Which execution image, and who builds and signs it** (FS-08 open question 7).
  Must be settled before FS-08 phase 2 — the probe cannot run without an image.
- **The macOS CI runner budget** (FS-01 open question 1). Every later PRD
  inherits the answer, and a golden transcript nobody runs is documentation.
- **Whether an agent-authored change set may commit without a `decisionLedgerId`**
  (FS-08 open question 8). Recommendation recorded; not decided.

**Host budget, since it is the real constraint:** the open program-id spikes need
**one Windows host** (W1-W7, C1-C4, L2, V1 — W1 additionally needs a VM it is
allowed to hard-power-cut), plus the un-idded Windows rows on the same host
(FS-05 D9 spikes 1-2, FS-04 spikes 2 and 5, FS-02 D2 property 3, and FS-06 D5's
mirror — the last two only just entered this register, and both are ship gates,
so book time for them rather than discovering them on the day),
**one macOS 26 / Apple-silicon host** (L5), and a
**macOS support matrix** for FS-06 D1 (10.15 x64 and 11.x arm64 VMs, an HFS+
image, a case-insensitive APFS volume). L3 and L6 need any host with a working
container runtime; **L4 needs no runtime at all** — it is a blob-store
resolution test and can run today.

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
  outcome that would change the design — and the spike appears in the **spike
  register** above, with an honest answer to "does this block or refine".
- Do **not** route a follow-up to a PRD that has not agreed to take it. "See
  FS-0x" is only a routing if FS-0x says so in its own text; otherwise it is a
  gap with a citation on top. Both instances of this in the program's history are
  recorded in [00-consistency-report.md §4.4 and §9.4](00-consistency-report.md).
