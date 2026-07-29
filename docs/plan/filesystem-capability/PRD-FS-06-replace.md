# PRD-FS-06 — replace, both platforms

**Status:** specified
**Depends on:** FS-01 (platform seam), FS-02 (Windows commit helper), FS-04 (preimage + trash)

> The dependency column in [README.md](README.md) lists only the nearest edge
> (FS-04). FS-04 itself depends on FS-01. FS-06 additionally requires FS-02,
> because the spine's own guardrail forbids landing a verb on one platform only
> and there is no Windows helper to add `replace` to until FS-02 exists.
>
> **Two ordering facts that are not dependencies and must not be read as
> optional.** (1) The spine requires FS-07 to ship **with or ahead of** FS-06:
> `replace` creates the crash points FS-07 classifies (D10's faults 9 and 10 are
> unreadable without FS-07's `c2e-` log), and D2's swap→relocation window is
> resolvable only by FS-07 D3.5. (2) FS-05 and FS-06 both change
> `commit_entry`'s signature and both add a branch to it — FS-05 defines
> `static enum outcome commit_entry(struct prepared *, uint32_t index,
struct entry *, uint32_t *reason_out)`, and neither PRD depends on the other.
> **Whichever lands first writes that signature; the second adopts it and adds
> only its branch.** This is the same ownership rule FS-03 D1 uses for
> `path_is_safe` and `fs_platform_win32.c`, and it is recorded here because
> nothing else in the set records it.

## Implementer brief

`replace` is the verb the macOS helper explicitly refuses, at exactly one line
([workspace_commit_helper.c:801](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)).
The refusal is correct about the _security_ question (macOS has no kernel
compare-and-swap rename) and wrong about the _product_ question, which the spine
already reframed: in `single_user_desktop` the realistic adversary is Excel
holding the file, a data-loss problem. This PRD does not work around the refusal
— it retires it by delivering something the author could honestly have signed:
act atomically, verify what was displaced, roll back or retain the preimage.
Design item **D1 is a spike**; the macOS design below is contingent on it.

## Context

Everything below is verified against the tree at `main@b349aca2`.

### `replace` is plumbed end to end and dies at one `goto`

| Layer               | File:line                                                                                                                                | State                                                             |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| Agent-facing model  | [workspace_authority.py:57](../../../services/ai-backend/src/agent_runtime/capabilities/desktop/workspace_authority.py)                  | `Literal["create","replace","delete","move","mkdir"]`             |
| Content rule        | [workspace_authority.py:98](../../../services/ai-backend/src/agent_runtime/capabilities/desktop/workspace_authority.py)                  | `needs_content = operation in {"create","replace"}`               |
| Broker JSON parse   | [broker.ts:1018](../../../apps/desktop/main/capabilities/broker.ts), [broker.ts:1036](../../../apps/desktop/main/capabilities/broker.ts) | accepts `"replace"`, requires content triple                      |
| TS domain type      | [workspace-authority.ts:64](../../../apps/desktop/main/capabilities/workspace-authority.ts)                                              | `WorkspaceOperation` includes `"replace"`                         |
| TS change-set check | [workspace-authority.ts:910](../../../apps/desktop/main/capabilities/workspace-authority.ts)                                             | `(create \|\| replace) !== hasContent` → conflict                 |
| TS wire encode      | [native-workspace-commit-helper.ts:619](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.ts)                       | `"replace" → NativeOperation.Replace = 2`                         |
| C enum              | [workspace_commit_helper.c:64](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)                       | `REPLACE = 2`                                                     |
| C validation        | [workspace_commit_helper.c:813-814,819](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)              | already encodes `replace` rules (must exist, kind==file, content) |
| **C refusal**       | **[workspace_commit_helper.c:797-801](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)**              | **`if (op != CREATE && op != MKDIR) goto fail;`**                 |
| C commit switch     | [workspace_commit_helper.c:752-766](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)                  | `CREATE` / `MKDIR` / `else return 0;`                             |

The refusal comment, verbatim, is the thing this PRD answers:

> macOS exposes atomic no-replace creation but no kernel compare-and-swap
> rename bound to an observed inode+digest. Replace/delete/move would have an
> uncloseable external-write race, so this helper refuses them instead of
> pretending advisory locks are a security primitive.

An existing test pins the refusal and must be rewritten, not deleted:
[native-workspace-commit-helper.test.ts:360-384](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.test.ts)
(`"fails closed for non-CAS replace/delete/move rather than using an advisory lock"`).

### Guards `replace` inherits for free

These already exist and are load-bearing for this design. Do not re-implement
them, do not relax them:

- [`snapshot_at:400-419`](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)
  — requires the exact directory-entry bytes, refuses `S_ISLNK`, refuses
  `S_ISREG && st_nlink != 1`, enforces `kind`, and enforces the caller's
  expected digest. The `nlink != 1` refusal is what makes the D2 swap safe: a
  hard-linked target would leave the second alias pointing at stale content
  (verified below).
- [`snapshot_matches:421-427`](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)
  — re-observation pinned to `dev+ino+mode+size+digest`.
- [`entry_live:736-739`](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c),
  called from [`command_commit:918`](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)
  — the last look before the effect.
- [`sealed_stage_matches:741-750`](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)
  — re-attests staged bytes by inode + digest immediately before the effect.
- [`open_parent:372-398`](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)
  — retained parent fd; every effect is `*at()`-relative to it, never a path.
- [`disjoint_entries:826-835`](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)
  — no two entries in a set touch the same spelling.
- Journal lifecycle
  [`journal_transition:601-614`](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)
  and startup reconciliation
  [`journal_reconcile_startup:626-651`](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c):
  `PREPARED → AUTHORIZED → COMMITTING → APPLIED | INDETERMINATE`, with
  `COMMITTING` conservatively becoming `INDETERMINATE` after a crash.

### Facts verified on the authoring host, not assumed

Host: macOS 15.6.1 (24G90), APFS, Apple clang, SDK 26.0, compiled with the
project's exact flag set from
[build.mjs:22-35](../../../apps/desktop/native/workspace-commit-helper/build.mjs).
These are single-host observations; D1 exists precisely because one host is not
the support matrix.

1. `renameatx_np(int, const char*, int, const char*, unsigned int)` is declared
   `__OSX_AVAILABLE(10.12)` in `<sys/stdio.h>` (SDK line 53); `RENAME_SWAP` is
   `0x00000002` (SDK line 36). Both are behind `__DARWIN_C_LEVEL >=
__DARWIN_C_FULL`, and `-std=c11` **does** satisfy that on Apple clang
   (`__DARWIN_C_LEVEL == 900000` measured under the project's flags). No build
   flag change is needed to see the symbol. `nm -u` shows `_renameatx_np` as a
   normal, non-weak undefined symbol.
2. `VOL_CAP_INT_RENAME_SWAP` (`0x00040000`, `<sys/attr.h>` line 394) is a
   **per-volume** capability, not an fstype guarantee. On the host's APFS data
   volume: `valid=1, supported=1`.
3. `VOL_CAP_INT_RENAME_OPENFAIL` on the same volume: `valid=1, **set=0**`. APFS
   does not fail renames on open files. This is the empirical form of the thing
   the refusal comment is about.
4. `RENAME_SWAP` swaps the two names' objects atomically (measured: inodes
   `87527706 ↔ 87527707`, sizes `4 ↔ 6`), works dirfd-relative, and works
   **across two different directory fds on the same volume**.
5. `RENAME_SWAP` **succeeds while another descriptor is open on the
   destination** — no error, no signal, no notification. The holder keeps
   writing to the displaced inode.
6. `RENAME_SWAP` returns `ENOENT` when the destination does not exist. It is not
   a create.
7. `RENAME_SWAP` **does not enforce type equality**: a file↔directory swap
   succeeded (the file name became a directory and vice versa). Type safety is
   ours to enforce.
8. `RENAME_SWAP` does not follow a symlink at either final component (a
   symlink-to-`/etc/passwd` destination was itself swapped; `/etc/passwd` was
   untouched).
9. `RENAME_SWAP` succeeds against an `nlink == 2` destination, after which the
   surviving hard link aliases the _old_ content. `snapshot_at`'s `nlink != 1`
   refusal is what keeps this out of reach.
10. `fgetattrlist(int, ...)` exists since 10.6 (`<unistd.h>` line 751) — the
    volume capability probe can be **fd-relative**, so it costs no confinement.

### The macOS deployment floor is currently unstated, and it is not 10.15

[`workspace-fs/binding.gyp:20`](../../../apps/desktop/native/workspace-fs/binding.gyp)
sets `MACOSX_DEPLOYMENT_TARGET: "10.15"` — but that gyp governs **only** the
separate N-API read module. The commit helper is built by
[build.mjs](../../../apps/desktop/native/workspace-commit-helper/build.mjs),
which passes **no** `-mmacosx-version-min`. Measured consequence: a helper built
on the authoring host carries `LC_BUILD_VERSION minos 15.0`. The helper's real
floor today is _whichever machine built it_ — `macos-14` (arm64) and
`macos-15-intel` (x64) per
[release-desktop.yml:47-56](../../../.github/workflows/release-desktop.yml).

Measured when the pin is added (helper source copied to a scratch dir, built
with the project's exact flags plus `-mmacosx-version-min=10.15`):

- `x86_64` → compiles clean under `-Werror`, `minos 10.15`.
- `arm64` → compiles clean under `-Werror`, `minos **11.0**` (the toolchain
  clamps; arm64 macOS does not exist below 11.0).

So the honest floor is **10.15 on x64, 11.0 on arm64**, and both are above
`renameatx_np`'s 10.12 availability. This matters to D1: the question is not
"does 10.15 have the symbol" (it does) but "does every volume we accept
implement the capability".

### Windows is at zero, and CI never runs any of this

- [build.mjs:9-17](../../../apps/desktop/native/workspace-commit-helper/build.mjs)
  writes a non-executable sentinel on every non-darwin platform;
  [native-workspace-commit-helper.ts:172](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.ts)
  rejects `process.platform !== "darwin"` before spawn.
- [ci-desktop.yml:61](../../../.github/workflows/ci-desktop.yml) has exactly one
  job, `runs-on: ubuntu-latest`.
  [apps/desktop/package.json:10](../../../apps/desktop/package.json) runs
  `build:workspace-commit-helper` before vitest, which on Linux produces the
  sentinel, and
  [native-workspace-commit-helper.test.ts:32](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.test.ts)
  is `process.platform === "darwin" ? describe : describe.skip`.
  **Zero native commit-helper assertions execute in CI today.** A macOS runner
  exists in the org (`macos-14`, `macos-15-intel` in `release-desktop.yml`); it
  is simply not wired to `ci-desktop`.

### One pre-existing policy hole this PRD must close

[`#assertGrantAllowsChangeSet:866-870`](../../../apps/desktop/main/capabilities/workspace-authority.ts)
refuses `delete` and `move` under `read_write_no_delete`, but **not**
`replace`. The mode is documented as "read + create/modify, but no
delete/unlink/move-out"
([types.ts:20](../../../apps/desktop/main/capabilities/types.ts)). Today that
is harmless because the helper refuses `replace` outright. The moment `replace`
works, `read_write_no_delete` silently permits destroying a file's entire
contents. See D9.

## Interfaces consumed

**From FS-01 — the platform seam.** [FS-01 §2 and §8](PRD-FS-01-platform-seam.md)
are normative for every name and signature; the spine's sketch names are aliases,
not APIs. FS-06 consumes the portable `open_parent()` (over `fs_open_root` +
`fs_open_dir_at` + `fs_dir_for_each`), `fs_stat_handle(...).id` for identity, the
portable `create_stage`/`command_write`/`command_seal` staging path,
`fs_commit_create` and `fs_durable_barrier`.

**FS-01 declares no replace slot, deliberately** (FS-01 D9): an undeclared slot
cannot be half-filled, whereas a declared one invites one platform to implement
it and the other to stub it. So FS-06 does not "fill in a slot FS-01 declared";
it **adds** two members in one change, implemented on both providers:

```c
/* Added by FS-06, on BOTH providers, in the same PR.
 *
 * SIGNATURE NOTE (consistency pass). An earlier draft mirrored
 * fs_commit_create's shape — (staged, parent, leaf) — which cannot express the
 * macOS body: RENAME_SWAP is name-based on BOTH sides, so the POSIX provider
 * needs the staging directory handle and the stage leaf, not just an open
 * handle on the staged object. The Win32 body needs the opposite (it renames
 * the staged HANDLE over the leaf). Carrying all four inputs is what lets one
 * declaration have two honest bodies; dropping either pair would have forced a
 * per-platform verb, which FS-01 D1/D9 forbid.
 *
 * `staged` is the sealed stage handle; (`stage_dir`, `stage_leaf`) is where it
 * currently lives. On success the DISPLACED object is at
 * (`stage_dir`, `stage_leaf`) on POSIX and is unlinked-but-handle-live on Win32
 * — see D5's note; that asymmetry is why *displaced_out reports which. */
enum fs_replace_displaced {
  FS_REPLACE_DISPLACED_AT_STAGE_NAME = 0, /* POSIX swap: look at stage_leaf   */
  FS_REPLACE_DISPLACED_PRESERVED     = 1  /* Win32: caller already staged it  */
};
int fs_commit_replace(fs_handle staged, fs_handle stage_dir,
                      const char *stage_leaf, fs_handle parent,
                      const char *leaf, enum fs_replace_displaced *displaced_out);

/* Capability query. Both providers (FS-01 rule 2). */
int fs_volume_supports_swap(fs_handle root);

/* Carry mode/ACL/xattrs/timestamps from the displaced object onto the staged
 * one before the effect. FS-01 §2 reserves this spelling for FS-06 precisely
 * because D8's first draft wrote raw fchmod + fcopyfile in the PORTABLE
 * translation unit, which FS-01 §5 and check-seam.mjs reject, and declared no
 * seam member — which would have shipped `replace` with a silent permission
 * downgrade on whichever platform got it second. 0 on ANY failure, so the
 * caller aborts before the effect (D8). Both bodies unverified; D1/D5 spikes. */
int fs_carry_metadata(fs_handle from, fs_handle to);
```

**From FS-04 — preimage + trash.** FS-06 stores nothing itself and adds **no**
journal field FS-04 did not define. What it consumes, in FS-04's actual shapes:

- `struct journal_preimage_row` (FS-04 §6) — the durable, MAC-covered, per-entry
  record, written _before_ the effect as a journal trailer under
  `JOURNAL_VERSION 4`. Its fields cover everything this PRD's draft asked for:
  `entry_index`, `leaf`, `volume_id`, `file_id_low/high`, `digest`,
  `displaced_size`, `origin`, `staged_before_effect`, `post_effect_digest`. If a
  field is genuinely missing, that is an FS-04 change, not a second record here.
- `enum preimage_disposition` (FS-04 §6) — **the one vocabulary.** FS-06's draft
  declared a parallel `enum preimage_state` whose `3 = UNVERIFIED` collides with
  FS-04's `3 = COLLECTED`; that is a wire conflict, not a naming preference.
  FS-06's "unverified" is FS-04's `PREIMAGE_UNKNOWN = 4`, and "none" is
  `PREIMAGE_NONE = 0`.
- the trash at `<root>/.0xcopilot/trash/` and the deterministic `pre_<32 hex>`
  leaf (FS-04 D1/D10, spine D4) — **not** the app-private staging run directory.
  See D2, which is rewritten around this.
- `preimage_ref` — opaque, never a workspace-relative or absolute path, matching
  the tokenization rule at
  [workspace-authority.ts:1033-1037](../../../apps/desktop/main/capabilities/workspace-authority.ts).
- the retention/expiry policy and the explicit "retained, not deleted" state.

**From FS-02 — the Windows helper**: a running Win32 commit helper with the same
framed, MAC'd protocol, retained parent `HANDLE`s, a durable journal, and
`create`/`mkdir` already landed.

## Interfaces exposed

### Protocol

`PROTOCOL` is `2` in both
[the C helper (line 45)](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)
and
[the TS client (line 28)](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.ts).
The commit-result body must grow a per-entry block, because a multi-entry set
can now be partially applied with per-entry preimages. **FS-04 owns
`PROTOCOL 3` and defines the block** — [FS-04 §6a](PRD-FS-04-preimage-trash.md) —
and FS-04 is an upstream dependency of FS-06, so FS-06 **populates the block and
bumps nothing**. The "if FS-04 has not bumped, FS-06 performs the bump" branch in
this PRD's draft is removed: FS-06 cannot land before FS-04, so the branch was
unreachable, and keeping it invited two numbers in flight.

The block, for reference (FS-04 is the definition of record):

```text
u8    outcome                 # set-level, unchanged semantics
str   receipt_ref
str   result_digest           # "" when absent
str   safe_message            # "" when absent
u32   entry_result_count      # == prepared->entry_count
  repeat entry_result_count:
    u8   entry_outcome        # APPLIED|ALREADY_APPLIED|PRECONDITION_DRIFT|FAILED|INDETERMINATE
    u32  reason               # FS-05's enum commit_reason; 0 = REASON_NONE
    u8   preimage_disposition # FS-04's enum: 0 none|1 retained|2 restored|3 collected|4 unknown
    str  preimage_ref         # "" iff preimage_disposition == 0
    str  displaced_digest     # "" unless the displaced object was digested
```

FS-06 populates `entry_outcome`, `preimage_disposition`, `preimage_ref` and
`displaced_digest`, and leaves `reason` at 0 (FS-05 owns those codes). FS-07
appends `observed_state` and `evidence` to the same repeat under `PROTOCOL 4`.

Read-only requests (`RECONCILE_CLAIM`, and the no-`prepared` recovery path at
[`command_commit:902-909`](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c))
emit `entry_result_count = 0`. A reconciliation that cannot enumerate entries
must not fabricate them.

### C — the portable replace verb

`enum preimage_state` is **deleted**; FS-04's `enum preimage_disposition` is the
one vocabulary (Interfaces consumed). `PREIMAGE_UNVERIFIED` becomes
`PREIMAGE_UNKNOWN = 4`.

```c
/* Non-zero on success. On any failure *disposition tells the caller what is
 * true of the target now; it is never left unset. Never returns 1 with
 * *disposition == PREIMAGE_UNKNOWN. Portable: no #ifdef, compiled once. */
struct replace_result {
  enum preimage_disposition disposition;   /* FS-04 §6                       */
  char preimage_leaf[40];                  /* FS-04's pre_<32 hex> + NUL     */
  char displaced_digest[65];
  int  applied;                            /* 1 iff approved bytes at leaf   */
};

static int commit_replace_entry(struct prepared *prepared, uint32_t index,
                                struct entry *entry,
                                struct replace_result *out);
```

### C — volume capability gate

Declared above with the other two additions. `fs_volume_supports_swap(fs_handle
root)` is a platform **primitive** and must exist on both providers (FS-01 rule 2) — the draft's darwin-only `volume_supports_swap(int root_fd)` is not
expressible. Handle-relative, so it costs no confinement. Returns 1 only when the
volume both reports the capability as valid and sets it.

_unverified (Win32):_ there is no `VOL_CAP_INT_*` analogue on Windows, so the
Win32 body has no capability bit to read and is expected to answer from
`fs_volume_supported` plus D5's rename-information-class probe. That is a weaker
claim than the macOS one and must be reported as such, not presented as parity.

### TypeScript

```ts
// native-workspace-commit-helper.ts — FS-04's vocabulary, not a second one.
export interface NativeWorkspaceEntryResult {
  readonly outcome: WorkspaceCommitOutcome;
  readonly reason?: NativeCommitReason; // FS-05
  readonly preimageDisposition: WorkspacePreimageDisposition; // FS-04
  /** Opaque handle. Never a workspace-relative or host path. */
  readonly preimageRef?: string;
  readonly displacedDigest?: string;
}

export interface NativeWorkspaceCommitResult {
  readonly outcome: WorkspaceCommitOutcome;
  readonly receiptRef: string;
  readonly resultDigest?: string;
  readonly safeMessage?: string;
  readonly entryResults: readonly NativeWorkspaceEntryResult[]; // new, may be []
}
```

`WorkspaceCommitResult`
([workspace-authority.ts:179-184](../../../apps/desktop/main/capabilities/workspace-authority.ts))
gains the same `entryResults` field; `toCommitResult`
([workspace-authority.ts:1040-1049](../../../apps/desktop/main/capabilities/workspace-authority.ts))
carries it; `workspaceCommitWire`
([broker.ts:1137-1145](../../../apps/desktop/main/capabilities/broker.ts))
projects it as `entry_results` with snake_case members.

## Design

### D1. SPIKE — `renameatx_np(RENAME_SWAP)` across the real support matrix

**Everything below D1 is contingent on D1.** Do not start D2 until it returns.
Timebox: 1 day.

The authoring-host observations in Context are one machine, one OS, one volume
type. What must be established before committing to the D2 design:

| #   | Question                                                                                                                                                                      | Method                                                                                                                   | Result that changes the design                                                                                                                                                                    |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| S1  | Does `renameatx_np(dirfd, a, dirfd2, b, RENAME_SWAP)` succeed on the **oldest floor we actually ship** (x64 `minos 10.15`, arm64 `minos 11.0`)?                               | Build the probe with each pin; run on a 10.15 x64 VM and an 11.x arm64 VM.                                               | `EINVAL`/`ENOTSUP` on a shipped floor ⇒ raise the floor and say so in FS-09's capability report, or gate `replace` by OS version. Do **not** ship a verb that fails at runtime on a supported OS. |
| S2  | Does **HFS+** set `VOL_CAP_INT_RENAME_SWAP`? [`supported_root_fd:358-363`](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c) accepts `hfs`. | `fgetattrlist(ATTR_VOL_CAPABILITIES)` on an HFS+ disk image.                                                             | Not set ⇒ D11's per-volume gate is mandatory (it is specified as mandatory regardless; a negative result just means it fires in practice, not only in theory).                                    |
| S3  | Is the swap durable, and against what barrier? After swap + `fsync(parent_fd)`, does a power-cut leave exactly one of the two pre-states?                                     | `fsync` the parent dir after swap; inspect ordering. Where available, cross-check with `F_BARRIERFSYNC` / `F_FULLFSYNC`. | If `fsync(dirfd)` does not order the swap, `durable_barrier` must use `F_FULLFSYNC` on both parents and the cost must be measured before it lands.                                                |
| S4  | Does APFS ever set `VOL_CAP_INT_RENAME_OPENFAIL` (encrypted / FileVault / external / case-sensitive variants)?                                                                | Probe each variant.                                                                                                      | If some APFS variant _does_ set it, macOS gains the Windows-style open-holder detection there, and D6's asymmetry becomes conditional rather than absolute. Report it; do not assume it.          |
| S5  | Behaviour on a **case-insensitive** APFS volume when the two leaves differ only in case.                                                                                      | Swap `Report.md` ↔ `report.md`.                                                                                          | Anything other than a clean, exact-byte outcome ⇒ keep replace confined to the existing ASCII-exact-entry rule and add an explicit same-leaf-spelling assertion.                                  |
| S6  | Does a Time Machine / snapshot-active volume change any of S1–S5?                                                                                                             | Repeat S1 with a local snapshot present.                                                                                 | A failure mode here ⇒ document it as an `UNSUPPORTED` refusal, not a silent partial.                                                                                                              |
| S7  | Cost: swap vs. the current create path on a 100 MB file.                                                                                                                      | Measure `fclonefileat` + swap vs. plain create.                                                                          | If a clone is needed and is not COW on some volume, D2's "no byte copy" claim is false and the PRD's cost section must say so.                                                                    |

The spike ships as a standalone probe under
`apps/desktop/native/workspace-commit-helper/spike/` **and is deleted in the
same PR that lands D2**. It is not a second write path and must never be
reachable from the helper binary.

Record the result in this file under a `## Spike result` heading with the host
matrix, before D2 is written. If S1 fails on a shipped floor, stop and re-plan;
do not route around it.

### D2. macOS: swap the sealed stage with the target, then verify what was displaced

Chosen over the alternative of cloning into the target's parent and swapping the
clone, because swapping the sealed stage directly (a) performs one filesystem
effect instead of two and (b) never creates an unapproved name inside the user's
folder.

**Where the displaced original ends up — corrected.** This PRD's draft said "(c)
lands the displaced original inside the app-private staging directory, which is
exactly where FS-04's preimage store wants it." That was true of an earlier draft
of FS-04 and is not true now: spine **D4** and FS-04 D1 put the trash at
`<root>/.0xcopilot/trash/`, and FS-07 surfaced the three-way disagreement between
FS-04, FS-05 and FS-06 as its Open question 1. FS-04 owns the substrate, so FS-06
conforms, and the cost is paid explicitly rather than argued away:

`RENAME_SWAP` swaps two **names**, so after step 5 the displaced original is at
`staging_run/<stage_name>` and cannot be anywhere else — the swap's other name is
the stage's. So FS-06 adds a step: after step 7 verifies **what** was displaced,
relocate it with `fs_rename_noreplace(staging_run, stage_name, prepared->trash,
pre_<hex>)` — FS-04's primitive, its deterministic leaf, its row. Then
`fs_durable_barrier` on both directories.

That relocation is a second rename, and it opens a window this PRD must name
rather than hide:

- The preimage row is written **before** the swap and names the **trash** leaf,
  so a crash between the swap and the relocation leaves a row pointing at a leaf
  that does not exist yet while the object sits in staging. FS-04's
  `staged_before_effect` byte is set to **0** for this strategy — the same value
  FS-04 D9 assigns to `ReplaceFileW`, and for the same reason: the preimage's
  location is not provable from the row alone.
- FS-07's reconciliation must therefore look in **both** places for the recorded
  source identity: under `prepared->trash/<pre_hex>` and under
  `staging_run/<stage_name>` (which FS-07 can already name — its Context §
  "`stage_dir` and the stage names are derivable" is exactly this hook). If it is
  in staging, the swap happened and the relocation did not: the entry is
  `applied`, the disposition is `RETAINED`, and the object is relocated by the
  next sweep, not by reconciliation (FS-07 D1 forbids reconciliation mutating).
  Recording that obligation here is FS-06's job; FS-07 D3.5 carries the lattice.
- The relocation can fail (`FS_RENAME_EXISTS` from a colliding leaf — only
  possible on a repeat of the same claim, which is idempotent by FS-04 D10; or
  `FS_RENAME_ERROR`). It is attempted **once**, never retried, and a failure
  leaves the object in staging with `disposition = PREIMAGE_UNKNOWN`. It does
  **not** downgrade the entry outcome: the swap was verified, so the replace
  itself is `APPLIED`. What is unknown is where the previous version is, and
  saying "applied, previous version location unconfirmed" is more truthful than
  either alternative.

Rejected: staging the replacement **inside** `<root>/.0xcopilot/trash/` so the
swap lands the original there directly, in one effect. It would remove the window
entirely, and it is the cleanest design if it survives review — but it moves
staged, not-yet-approved bytes inside the user's granted tree, which contradicts
the helper's stated invariant that staged content lives in an app-private
directory (`workspace_commit_helper.c:19-20`) and would need its own decision.
Recorded in Open questions rather than taken silently.

Exact sequence, per entry, executed inside the existing `COMMITTING` journal
window in
[`command_commit`](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c):

Written in FS-01's names throughout. Every step below is **portable** — there is
no raw POSIX call anywhere in `commit_replace_entry`, and no `_fd`-suffixed field
(FS-01's permitted-edit table renamed `stage_fd`→`stage`, `parent_fd`→`parent`,
`sealed_stat`→`sealed_meta`, and replaced `snapshot.dev`/`.ino` with
`snapshot.id`). An earlier draft of this section spelled steps 4-7 as
`fsync`/`renameatx_np`/`fstatat`/`openat` in the portable translation unit, which
FS-01 §5 forbids and `check-seam.mjs` fails the build on.

1. `sealed_stage_matches(entry)` — existing, line 741. Re-attest the staged
   inode + digest. Failure ⇒ zero effect, `FAILED`.
2. `entry_live(entry)` — existing, line 736, already called at line 918 for the
   whole set. The **last look**: target still identity+mode+size+digest as
   observed at prepare.
3. `fs_carry_metadata(target_probe, entry->stage)` — carry the displaced file's
   metadata onto the staged inode so the swap does not silently downgrade the
   user's file (D8). `target_probe` is a handle on the target taken at step 2 and
   verified against `entry->source`. Returns 0 on any failure ⇒ abort before the
   effect.
4. `fs_durable_barrier(entry->stage)` — the metadata carry-over must be durable
   before the effect.
5. **The effect**:
   `fs_commit_replace(entry->stage, prepared->staging_run, entry->stage_name,
entry->parent, entry->leaf, &displaced_where)`.
   The POSIX body is
   `renameatx_np(stage_dir, stage_leaf, parent, leaf, RENAME_SWAP)` with no
   `RENAME_NOFOLLOW_ANY` and no `RENAME_RESOLVE_BENEATH` (D12), reporting
   `FS_REPLACE_DISPLACED_AT_STAGE_NAME`; the Win32 body is D5.
   - target absent (POSIX `ENOENT`) ⇒ the target vanished between step 2 and
     here. Zero effect; `PRECONDITION_DRIFT`.
   - any other failure ⇒ zero effect _asserted only if_ a post-check confirms it
     (step 7 runs regardless); otherwise `INDETERMINATE`.
6. `fs_durable_barrier(entry->parent)` and
   `fs_durable_barrier(prepared->staging_run)` — both directory entries changed,
   so both parents need the barrier (subject to S3). On Win32 both return
   0-meaning-no-error, never 0-meaning-durable: `FS_DIRECTORY_BARRIER_PROVEN` is
   0 there (FS-01 §2), so a Windows `applied` is observed-applied and FS-07
   re-observes rather than trusting it.
7. **Verify what was displaced**, without re-resolving any path. On the
   `FS_REPLACE_DISPLACED_AT_STAGE_NAME` lane:
   - `fs_stat_at(prepared->staging_run, entry->stage_name, &displaced)` — this
     name now holds whatever was at the target.
   - Require `fs_identity_equal(&displaced.id, &entry->source.id)`,
     `displaced.kind == FS_KIND_FILE`, and `displaced.link_count == 1`.
   - `regular_digest_handle` on an
     `fs_open_read_at(prepared->staging_run, entry->stage_name, &h)` and require
     it equals `entry->source.digest`.
   - `fs_stat_at(entry->parent, entry->leaf, &landed)` and require
     `fs_identity_equal(&landed.id, &entry->sealed_meta.id)`.
8. On all of step 7 passing: `applied = 1`,
   `disposition = PREIMAGE_RETAINED`, `displaced_digest = entry->source.digest`.
   Then **relocate** the displaced object into FS-04's trash —
   `fs_rename_noreplace(prepared->staging_run, entry->stage_name, pin,
prepared->trash, pre_<hex>)`, once, never retried — and write FS-04's
   `journal_preimage_row` with `staged_before_effect = 0`.
   `preimage_leaf` is FS-04's deterministic `pre_<32 hex>`, never the stage name.
   `cleanup_prepared_stages` must **skip** this entry (D10).
9. On any step-7 mismatch: **roll back exactly once** (D4).

One consequence a reviewer will find, so it is stated: the step-8 relocation is
staging-run → trash, i.e. **app data → granted root**. FS-04's Context warns
against adding "a _second_ same-volume dependency on app data" — this is not a
second one, it is the existing one: `command_prepare:850` already refuses a
prepare whose staging directory is not on the root's volume, so the relocation is
same-volume exactly when the prepare that produced the stage succeeded. It does
mean `replace` inherits FS-02 D7's Windows consequence (a grant root on `D:` is
refused at prepare) rather than escaping it.

The preimage is a retained object, never an auto-deleted one. The verb's
promise is "your previous version is kept", not "trust me".

### D3. What this guarantees, and the residual window, stated exactly

The guarantee is the spine's, restated for `replace`:

> Act atomically. Verify what was displaced. Roll back or retain the preimage.
> Never claim an outcome that was not observed.

It is **not** compare-and-swap. There is no kernel primitive that binds a rename
to an observed inode+digest on macOS, and this PRD does not pretend otherwise.
Two residual windows exist and are named, not hidden:

**Residual W1 — the observe→swap gap.** Between `entry_live()` (step 2) and the
`renameatx_np` syscall (step 5), another process can replace the target by
unlink+create or by its own rename. `RENAME_SWAP` will succeed and displace the
impostor. We detect this in step 7 (the displaced inode is not the one we
verified) and roll back per D4. Width: a handful of instructions plus one
`fsync`. It is **detected after the fact, never prevented**.

**Residual W2 — the open-holder gap, unclosable on macOS.** A process holding
the target open across the swap keeps its descriptor bound to the _displaced_
inode. Its subsequent writes land in the preimage; the user sees the new content
at the path. Verified fact 5 shows the swap does not fail, and verified fact 3
shows APFS does not set `VOL_CAP_INT_RENAME_OPENFAIL`, so **macOS gives us no
signal at all**. This is exactly the Excel case, and it is exactly why the
preimage is _retained_ rather than deleted: the holder's writes remain
recoverable, and the receipt carries `preimage_disposition = RETAINED` so the ledger
and FS-09's UX can say so honestly.

We do **not** attempt to close W2 with advisory locks (`flock`, `O_EXLOCK`,
`fcntl(F_SETLK)`). The original refusal was right about that and this PRD keeps
its position. We also do not sniff application lockfiles (`~$doc.docx`,
`.~lock.*#`): they are spoofable, application-specific, and would dress a
heuristic up as a control.

Windows closes W2. See D6. That asymmetry is reported, not smoothed over.

### D4. Rollback: exactly once, then stop

On any step-7 mismatch, attempt restoration **once**, through the same seam
member that performed the effect — a rollback that used a different primitive
than the effect would be a second write path:

```c
fs_commit_replace(entry->stage, prepared->staging_run, entry->stage_name,
                  entry->parent, entry->leaf, &displaced_where)
```

then re-verify with `fs_stat_at` + `fs_identity_equal`: the leaf must again hold
`entry->source.id` with `entry->source.digest`, and `entry->stage_name` must
again hold `entry->sealed_meta.id`.

On Windows the rollback is not a second swap — the displacement already happened
at D5 step 3, so it is `restore_preimage` out of the trash, and it can fail with
`FS_RENAME_EXISTS` where the macOS swap-back cannot (D5 step 8). Both lanes obey
the same once-only rule and the same "verified or `PREIMAGE_UNKNOWN`" outcome.

- Restore verified ⇒ `applied = 0`, `disposition = PREIMAGE_RESTORED`, entry outcome
  `PRECONDITION_DRIFT`. The impostor's inode is back at the leaf, untouched. We
  never delete it: we did not create it and cannot prove what it is.
- Restore fails or cannot be verified ⇒ `applied = 0`, `disposition =
PREIMAGE_UNKNOWN`, entry outcome `INDETERMINATE`, and the set outcome is
  `INDETERMINATE`. The durable `COMMITTING` record remains the evidence, exactly
  as it does today for a failed create
  ([command_commit:934-939](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)).

**Never loop.** A retry loop against an actively racing writer is how a
data-loss bug becomes a data-destruction bug. One attempt, then report what is
true.

### D5. Windows: handle-relative rename, not `ReplaceFileW`

The spine notes `ReplaceFileW` "performs replace _and_ writes the displaced
content to a backup file — replace plus preimage in one documented call". That
is true and attractive. It is nonetheless rejected as the **primary** primitive,
for one reason that outranks convenience:

`ReplaceFileW(lpReplacedFileName, lpReplacementFileName, lpBackupFileName, ...)`
takes **three paths**. Every path is re-walked by the kernel from scratch. FS-03
and [workspace_fs.c:186-348](../../../apps/desktop/native/workspace-fs/src/workspace_fs.c)
exist to eliminate exactly that: a component-at-a-time `NtCreateFile` walk with
`FILE_OPEN_REPARSE_POINT`, refusing any intermediate reparse point, producing a
retained parent `HANDLE`. Handing a path back to the kernel at the moment of the
effect discards the entire walk. That is the spine's "do not weaken confinement
to make a verb work", and it is not negotiable for a convenience win.

Primary Windows sequence (mirrors D2 step for step, all handle-relative,
`RootDirectory = parentHandle`, `FileName` a single leaf with no separator):

1. Open the target with the **detection** share mode (D6). This handle is the
   preimage anchor.
2. `GetFileInformationByHandleEx(target, FileIdInfo, ...)` → `FILE_ID_INFO
{ VolumeSerialNumber, FileId(128) }`, and digest the contents. Require both
   to equal the prepare-time snapshot. This is `entry_live`'s Windows twin.
3. **Capture the preimage** — `stage_preimage(...)`, FS-04's portable verb, which
   is `fs_rename_noreplace` of the target into `prepared->trash` under FS-04's
   deterministic `pre_<32 hex>` leaf, with `origin = PREIMAGE_HELPER_DISPLACED`
   and `staged_before_effect = 1`. This is FS-04 D9 Strategy B, and it is the
   **only** admissible capture here.

   > **Corrected by the consistency pass.** An earlier draft of this step used
   > `NtSetInformationFile(target, …, FileLinkInformationEx)` to hard-link the
   > target into the **private staging directory**. Three separate rules say no,
   > and none of them is stylistic:
   >
   > 1. **Spine D4 / FS-04 D1** put the preimage at
   >    `<root>/.0xcopilot/trash/`. A second location means FS-04's GC, budget,
   >    `listRestorablePreimages` and marker check never see it — the preimage
   >    would exist and be unrecoverable, which is worse than not capturing one.
   > 2. **FS-04 D6 rejects hard links explicitly**, and gives the reason: a
   >    hardlink shares the inode, so a later edit to the restored file silently
   >    mutates the preimage. Here it is worse still — the "preimage" would share
   >    an inode with a file the replacement is about to displace, so its
   >    identity and digest (the two things FS-04 D3 makes the integrity anchor)
   >    describe a live object, not a frozen one.
   > 3. It would have made Windows the **only** platform whose preimage is not in
   >    the trash, i.e. one verb with two recovery models — the divergence FS-01
   >    D1 draws the seam to prevent.
   >
   > The cost of the correction is real and is paid: Strategy B's capture is a
   > second effect, so Windows loses the single-OS-call atomicity a hard link
   > would have had, and gains the same crash window macOS has (FS-07 D3.5's
   > third bullet, the loud case). That window is the price of one recovery
   > model, and it is the one FS-07 is written to classify.

4. Carry metadata (D8) via `fs_carry_metadata(target_probe, staged)`. The Win32
   body is `GetSecurityInfo`/`SetSecurityInfo` for the DACL and
   `GetFileTime`/`SetFileTime` for timestamps — **unverified**, see D8.
5. **The effect** — `fs_commit_replace`'s Win32 body:
   `NtSetInformationFile(stagedHandle, …, FileRenameInformationEx)` with
   `FILE_RENAME_REPLACE_IF_EXISTS | FILE_RENAME_POSIX_SEMANTICS`,
   `RootDirectory = parentHandle`, `FileName` = the target leaf. Because step 3
   already moved the original into the trash, the leaf is normally **free** by
   this point and `REPLACE_IF_EXISTS` is defence in depth against a racing
   creator rather than the mechanism. It reports
   `FS_REPLACE_DISPLACED_PRESERVED`: unlike the POSIX swap, nothing lands back at
   the stage name, because the displacement already happened at step 3.
6. `fs_durable_barrier(parent)`. On Win32 that is `FlushFileBuffers`, which
   returns 0-meaning-no-error and **not** 0-meaning-durable —
   `FS_DIRECTORY_BARRIER_PROVEN` is 0 (FS-01 §2, FS-02 D8). Do not report it as
   durable.
7. Verify by handle: the leaf now resolves to the staged `FILE_ID_INFO`; the
   trash leaf resolves to the captured `FILE_ID_INFO` with the expected digest.
8. Mismatch ⇒ roll back once by `restore_preimage` (FS-04's portable verb —
   `fs_rename_noreplace` of the trash leaf back over the target name), with the
   same once-only rule as D4. Note this rollback can fail with
   `FS_RENAME_EXISTS` where macOS's swap-back cannot, because the target name may
   have been taken; that is `INDETERMINATE` + `PREIMAGE_UNKNOWN`, not a retry.

`ReplaceFileW` remains a documented fallback **only** for the case where step 5
returns a status indicating the rename information class is unavailable on the
minimum supported Windows build. If that fallback is ever taken, it must be
recorded in the receipt as a distinct, reported degradation — not silently
substituted.

> **unverified:** every NTSTATUS, information class, and flag in D5 is written
> against the documented Win32/NT contract. No Windows host was available while
> writing this PRD. Specifically unconfirmed: (a) that
> `FileRenameInformationEx` + `FILE_RENAME_POSIX_SEMANTICS` is available on the
> project's minimum supported Windows build — the project has not stated one,
> and FS-02's "minimum Windows version" open question owns pinning it;
> (b) that `FileRenameInformationEx` honours a directory `HANDLE` in
> `RootDirectory` and rejects a separator in `FileName` — this is
> **FS-05 D9 spike 1**, shared, not a second experiment;
> (c) the exact status returned when the target is held with an incompatible
> share mode; and (d) that `fs_carry_metadata`'s Win32 body
> (`GetSecurityInfo`/`SetSecurityInfo` + `GetFileTime`/`SetFileTime`) reproduces
> the displaced object's effective DACL — the same question FS-02 SPIKE-W2 asks
> from the other direction. A Windows spike mirroring D1 must answer (a), (c) and
> (d) before D5 is implemented, and FS-02 is the natural place for the harness.

### D6. Windows detects the open holder; macOS cannot. Report the difference

Before any effect, open the target with:

```text
DesiredAccess = FILE_READ_DATA | FILE_READ_ATTRIBUTES | DELETE | SYNCHRONIZE
ShareAccess   = FILE_SHARE_READ          /* deny WRITE, deny DELETE */
```

If another process holds the file with write access — Excel, Word, a build tool
— this open fails with `STATUS_SHARING_VIOLATION`. Windows' share modes are
**mandatory**, not advisory, so this is a real control rather than a heuristic,
and it is obtained inside the handle-relative model with no path involved. This
is the property the spine credits `ReplaceFileW` with; taking it from our own
open means we get it _without_ importing `ReplaceFileW`'s path arguments.

Mapping:

- sharing violation ⇒ entry outcome `FAILED`, `preimage_disposition = PREIMAGE_NONE`, zero
  effect, and a safe message distinguishing "another application has this file
  open" from a generic failure. This is a _good_ outcome: it is the honest
  answer, and the user can close the app and retry.
- macOS: no equivalent exists (verified facts 3 and 5). Replace proceeds and
  retains the preimage.

The two platforms therefore have the same _guarantee_ but different _strength_,
and the capability report (FS-09) must state it rather than advertise one number
for both. Concretely: macOS `replace` reports `open_holder_detection: false`,
Windows reports `true`.

### D7. No cross-entry atomicity, and no pretence of it

[`command_commit:927`](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)
loops entries and breaks on the first failure. A set of N replaces is N
independent atomic swaps; there is no transaction across them, and there never
was for create/mkdir either.

Decision: **do not add cross-entry rollback.** Unwinding k already-applied
swaps introduces k new races and k new indeterminate outcomes to fix one. The
correct response is honesty, which is what the `PROTOCOL 3` per-entry result
block buys:

- entries `0..k-1` report `APPLIED` with their own `preimage_ref`;
- entry `k` reports its actual outcome;
- entries `k+1..N-1` report `FAILED` with `preimage_disposition = PREIMAGE_NONE` (no effect);
- the **set** outcome stays `INDETERMINATE`, unchanged from today.

The ledger can then say exactly which files changed and exactly which previous
versions are retained. That is strictly more truthful than a set-level
`INDETERMINATE` with no detail.

### D8. Metadata carry-over is mandatory and fail-closed

The staged inode is created `0600`
([create_stage:708](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)).
Swapping it onto a user document that was `0644` with an explicit ACL would
silently change the file's permissions and drop its extended attributes. A
security-relevant change that the ledger does not report is the kind of thing
this codebase's compliance rules exist to prevent.

Before the effect, one **seam** call on the retained `entry->stage`:

```c
if (!fs_carry_metadata(target_probe, entry->stage)) { /* FAILED, zero effect */ }
```

`target_probe` is a handle on the target taken at step 2 and verified against
`entry->source`.

This is a seam member, not two raw calls. An earlier draft wrote
`fchmod(entry->stage_fd, entry->source.mode & 07777)` plus
`fcopyfile(probe, stage, NULL, COPYFILE_ACL | COPYFILE_XATTR)` **in the portable
translation unit**, which FS-01 §5 forbids and `check-seam.mjs` fails the build
on — and, worse, declared no seam member for it, so a Win32 provider could have
shipped `replace` with the carry-over silently absent. Those two calls are now
the _POSIX body_ of `fs_carry_metadata`; the Win32 body is `GetSecurityInfo` /
`SetSecurityInfo` for the DACL and `GetFileTime` / `SetFileTime` for timestamps.

> **unverified, both bodies.** That `fcopyfile(COPYFILE_ACL | COPYFILE_XATTR)`
> between two open descriptors carries an APFS ACL and every xattr without
> touching content is the documented contract but has not been executed here;
> add it to D1's probe set. That the Win32 pair reproduces the _effective_ DACL —
> as opposed to the explicit one, dropping inherited ACEs — is the same question
> FS-02 SPIKE-W2 asks about a renamed file, and it must be answered before this
> ships, because a carry-over that silently drops inherited ACEs is the exact
> failure this section exists to prevent.

Neither body may change `dev`/`ino`, size, or content, so `sealed_stage_matches`
still holds and the approval digest still describes the bytes. A provider whose
implementation would change any of those must return 0 instead.

If it returns 0: **abort before the effect**. Entry outcome `FAILED`,
`preimage_disposition = PREIMAGE_NONE`, zero effect. Do not proceed with degraded
metadata and do not "best-effort" it — a silent permission downgrade is worse
than a refused write.

### D9. `read_write_no_delete` must refuse content-destroying replace

Close the hole named in Context.
[`#assertGrantAllowsChangeSet:866-870`](../../../apps/desktop/main/capabilities/workspace-authority.ts)
gains:

```ts
if (grant.mode === "read_write_no_delete" && entry.operation === "replace") {
  throw new WorkspaceAuthorityError("workspace_capability_denied");
}
```

Rationale: a mode whose documented meaning is "no delete/unlink/move-out"
([types.ts:20](../../../apps/desktop/main/capabilities/types.ts)) cannot
coherently permit an operation that destroys a file's entire prior contents.
`replace` requires `read_write`.

Considered and rejected: allowing `replace` under `read_write_no_delete`
_because_ the preimage is retained. Rejected because it makes a user-facing
authority level depend on an internal retention policy the user cannot see, and
because FS-04's retention window is finite. The mode boundary must be legible
from the mode's name.

The native helper enforces nothing here — grant modes are Electron-main policy —
but the helper's own refusal remains the backstop for anything that gets past
policy.

### D10. Journal, cleanup, and crash semantics

- The FS-04 preimage record is written **before** `renameatx_np` and while the
  state is `COMMITTING`. A crash between the swap and the record would otherwise
  leave a preimage in the staging directory with nothing describing it.
- [`cleanup_prepared_stages:716-730`](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)
  unlinks a stage entry only when the held fd's `dev+ino` still match the name.
  After a successful swap, `entry->stage_fd`'s inode is at the _target_ and
  `entry->stage_name` holds the _preimage_, so the identity check fails and the
  function returns 0. That is currently correct-but-wrong-reason behaviour: it
  would mark `cleanup_complete = 0` forever. Add an explicit
  `entry->stage_consumed` flag set by a successful replace; `cleanup_prepared_stages`
  **skips** consumed entries, and ownership of the preimage object transfers to
  FS-04's store. It must not be unlinked by the generic cleanup path.
- Crash while `COMMITTING`: unchanged. `journal_reconcile_startup:634-637`
  rewrites it to `INDETERMINATE`. Reconciliation for a replace must be able to
  answer "was the effect applied?" from the FS-04 preimage record plus a fresh
  observation of the leaf — and when it cannot, it stays `INDETERMINATE`. It
  must **never** replay the swap.
- `test_crash_boundary`
  ([line 148](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c),
  faults 1-4) gains fault **9 = after the swap, before the preimage record is
  fsynced** and fault **10 = after the preimage record, before the swap** — the
  pair the spine's crash-fault ladder allocates to FS-06. (An earlier draft took
  5 and 6, which are FS-04's; 7/8 are FS-07's and 11/12 are FS-05's.) Both
  are denial-only inputs on a private inherited fd, wired to no production
  composition — the existing rule at
  [native-workspace-commit-helper.ts:116-123](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.ts).

### D11. Per-volume capability gate, fd-relative

`replace` is refused unless the root volume advertises the capability.
`supported_root_fd`'s fstype allowlist (`apfs`/`hfs`, line 358-363) is
**necessary but not sufficient**: `VOL_CAP_INT_RENAME_SWAP` is a per-volume
property (verified fact 2).

At `ROOT_IDENTITY` and `PREPARE`, the portable path calls
`fs_volume_supports_swap(root)` — the seam member, taking `fs_handle`, on both
providers. A volume that does not report the capability as valid **and** set is
treated as not supporting it: fail closed.

The POSIX body is `fgetattrlist(root, ...)` with
`ATTR_VOL_INFO | ATTR_VOL_CAPABILITIES`, requiring both
`valid[VOL_CAPABILITIES_INTERFACES] & VOL_CAP_INT_RENAME_SWAP` and
`capabilities[VOL_CAPABILITIES_INTERFACES] & VOL_CAP_INT_RENAME_SWAP`.
(An earlier draft called this `volume_supports_swap(int root_fd)` and spelled the
`fgetattrlist` in portable code — a darwin-only signature FS-01 rule 2 forbids.)

Refusal is `UNSUPPORTED` and is **scoped to the entry**: a change set containing
only `create`/`mkdir` still works on such a volume. Do not degrade the whole
root.

Windows analogue: probe the volume for the rename-information class support
established by D5's spike; same fail-closed rule.

### D12. Deployment floor: pin it, assert it, do not pass newer flags

- `build.mjs` gains `-mmacosx-version-min=10.15` (measured: x64 → `minos
10.15`, arm64 → clamped to `minos 11.0`, both compiling clean under the
  project's `-Werror` flag set). This makes "replace is available" a claim about
  a known floor rather than about the build machine.
- `renameatx_np` is `__OSX_AVAILABLE(10.12)` and links non-weak, so at that
  floor no `dlsym`/weak-link guard is needed. Do not add one; a guard implies a
  runtime uncertainty that does not exist.
- Do **not** pass `RENAME_NOFOLLOW_ANY` (0x10) or `RENAME_RESOLVE_BENEATH`
  (0x20). They carry no availability annotation on the constants, are kernel-side
  flags newer than the floor, and would risk `EINVAL` on an older kernel. They
  buy nothing here: the operation is already dirfd-relative on a single
  validated leaf, and verified fact 8 shows rename does not follow a final
  symlink anyway.
- A test asserts the shipped helper's `LC_BUILD_VERSION minos` matches the
  pinned floor for the built architecture, so the floor cannot silently drift
  with the build machine again.

### D13. Outcome mapping

| Situation                                              | Entry outcome        | `preimage_disposition` | Set outcome                      |
| ------------------------------------------------------ | -------------------- | ---------------------- | -------------------------------- |
| Swap applied, displaced inode verified                 | `APPLIED`            | `retained`             | `APPLIED` if all entries applied |
| `entry_live` false before any effect                   | `PRECONDITION_DRIFT` | `none`                 | `PRECONDITION_DRIFT` (unchanged) |
| `sealed_stage_matches` false                           | `FAILED`             | `none`                 | `INDETERMINATE`                  |
| Metadata carry-over failed (D8)                        | `FAILED`             | `none`                 | `INDETERMINATE`                  |
| Volume lacks `RENAME_SWAP` (D11)                       | rejected at prepare  | —                      | request fails `UNSUPPORTED`      |
| Windows: target held with incompatible share mode (D6) | `FAILED`             | `none`                 | `INDETERMINATE`                  |
| Swap returned `ENOENT` (target vanished)               | `PRECONDITION_DRIFT` | `none`                 | `PRECONDITION_DRIFT`             |
| Displaced inode ≠ snapshot, restore verified           | `PRECONDITION_DRIFT` | `restored`             | `PRECONDITION_DRIFT`             |
| Displaced inode ≠ snapshot, restore failed             | `INDETERMINATE`      | `unknown`              | `INDETERMINATE`                  |
| Crash after swap, before preimage record               | (reconcile)          | `unknown`              | `INDETERMINATE`                  |
| Claim already `APPLIED` (idempotent repeat)            | `ALREADY_APPLIED`    | as recorded            | `ALREADY_APPLIED` (unchanged)    |

`FAILED` may be reported **only** when zero effect is proven. Anything else that
might have crossed the effect boundary is `INDETERMINATE`. This is the existing
rule at
[command_commit:934-939](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)
and FS-06 does not soften it.

## Implementation plan

**Phase 0 — spike (D1).** No production file is touched.

1. `apps/desktop/native/workspace-commit-helper/spike/rename_swap_probe.c` —
   S1–S7. Standalone, not linked into the helper, deleted in step 6's PR.
2. Record results in a `## Spike result` section of this file. **Gate:** if S1
   fails on a shipped floor, stop and re-plan.

**Phase 1 — macOS helper.**

3. `apps/desktop/native/workspace-commit-helper/build.mjs` — add
   `-mmacosx-version-min=10.15` to the `args` array (D12).
4. `.../src/workspace_commit_helper.c`
   - add `fs_volume_supports_swap(fs_handle)` to `fs_platform.h` **and both
     providers** (D11); call it from portable `open_root` / `command_prepare`,
     per-entry not per-root.
   - **delete the refusal at lines 797-801**, replacing the comment with one
     that states the new guarantee and names residual W1 and W2 (D3). Do not
     leave the old comment in place; it will be read as still-true.
   - add `struct replace_result` and `commit_replace_entry` (D2/D4). Do **not**
     add `enum preimage_state`; use FS-04's `enum preimage_disposition`.
   - extend `struct entry` with `stage_consumed`, `preimage_leaf[40]`,
     `displaced_digest[65]`, `preimage_disposition`.
   - wire `REPLACE` into `commit_entry` (line 752).
   - `cleanup_prepared_stages` (line 716) skips `stage_consumed` entries (D10).
   - `write_commit_result` (line 891) populates FS-04 §6a's per-entry block;
     it does not redefine it.
   - `test_crash_boundary` faults 9 and 10 (D10).
   - the post-swap relocation of the displaced object into
     `<root>/.0xcopilot/trash/` via FS-04's `fs_rename_noreplace` (D2 step 8).
5. Reuse, do not duplicate: `snapshot_at`, `snapshot_matches`, `entry_live`,
   `sealed_stage_matches`, `regular_digest_handle`, `open_parent`.

**Phase 1b — close the restore hole this PRD opens.** FS-04's restore is a
`CREATE`-from-preimage change set with precondition `{ exists: false }`
([FS-04 D6](PRD-FS-04-preimage-trash.md)). A replaced file's name is **occupied
by the replacement**, so every `RETAINED` row FS-06 produces is offered by
`listRestorablePreimages` and refused at commit with `PRECONDITION_DRIFT`. FS-06
is the only PRD that can produce such a row, so FS-06 must close it, and must
pick one before shipping:

- **(a)** a `REPLACE`-from-preimage arm — the restore is itself a replace, taking
  its own preimage of the replacement, so the round trip is symmetric and
  repeatable;
- **(b)** `prepareLocalRestore` refuses a row whose `restorePath` is occupied,
  with a specific message, and the user moves or deletes the replacement first.

(a) is the right product answer and the larger change; (b) is honest and
shippable. Shipping neither means the receipt says "your previous version is
kept" and the restore button next to it always fails. **Release blocker**, the
same shape as FS-05's directory-restore obligation.

**Phase 2 — Windows helper.** (Requires FS-02.)

6. Write the Win32 bodies of the three seam members FS-06 adds
   (`fs_commit_replace`, `fs_volume_supports_swap`, `fs_carry_metadata`) in
   FS-02's `fs_platform_win32.c`. There is **no** separate Win32 verb: steps 1-9
   of D2 are the portable `commit_replace_entry` and run on both platforms. What
   is Windows-specific is the share-mode detection open (D6), the
   `FileRenameInformationEx` effect and the `FlushFileBuffers` barrier (D5) —
   all inside provider bodies. The preimage is captured by FS-04's portable
   `stage_preimage` into `<root>/.0xcopilot/trash/`, **not** by a hard link into
   the staging directory (D5 step 3's correction).
7. Delete `spike/`.

**Phase 3 — TypeScript / broker.**

8. `native-workspace-commit-helper.ts` — `PROTOCOL 3`,
   `NativeWorkspaceEntryResult`, extend `decodeCommitResult` (line 634).
9. `workspace-authority.ts` — `WorkspaceCommitResult.entryResults` (line 179),
   `toCommitResult` (line 1040), and the D9 `read_write_no_delete` refusal
   (line 866).
10. `broker.ts` — `workspaceCommitWire` (line 1137) projects `entry_results`.

**Phase 4 — CI, so that none of this is theatre.**

11. `.github/workflows/ci-desktop.yml` — add a `macos-14` leg running the
    desktop suite, so `describeNative` actually executes. (FS-01 may land this
    first; if so, this step is a no-op and the DoD checkbox is satisfied by
    FS-01.)
12. Add a repo test asserting `describeNative` did not skip when
    `process.platform === "darwin"`, so a future runner change cannot silently
    re-hide the suite.

## Test plan

Native tests extend
[native-workspace-commit-helper.test.ts](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.test.ts),
which already spawns the real helper against real temp roots.

### Rewrite the refusal test

`"fails closed for non-CAS replace/delete/move rather than using an advisory
lock"` (line 360) becomes two tests: `delete`/`move` still reject with
`workspace_conflict`; `replace` no longer does. Do **not** delete the assertion
that no advisory lock is taken.

### Happy path

- `replace` of a 4 KB file: the leaf's content equals the staged bytes; the
  leaf's inode equals the sealed stage inode; the entry result is `APPLIED` with
  `preimageDisposition = "retained"`; the preimage object exists at
  `<root>/.0xcopilot/trash/pre_<hex>` — FS-04's trash, **not** the private
  staging directory (D2's correction) — with the original inode and
  `displacedDigest` equal to the original's sha256.
- The workspace directory contains **no** file other than the target — i.e. no
  temp leaf was ever created in the user's tree.
- The original's mode is preserved: `chmod 0644` the target before the run,
  assert `statSync(target).mode & 0o777 === 0o644` after (D8). Without the
  `fchmod` carry-over this assertion fails at `0o600`, which is the point.
- An xattr set on the original (`xattr -w com.test.k v`) is present on the
  replaced file.

### Residual W1 — the observe→swap race, detected and rolled back

- Using the existing fd-passing technique already used at test line 204
  ("never commits attacker bytes after a sealed stage name is renamed"), replace
  the target's directory entry with a _different_ inode between prepare and
  commit. Assert: entry outcome `PRECONDITION_DRIFT`, `preimageDisposition =
"restored"`, the impostor's inode is back at the leaf, the impostor's bytes
  are unmodified, and the staged bytes were **not** committed.
- Assert exactly one restore attempt: instrument by making the restore
  impossible (remove the staging entry) and assert the result is
  `INDETERMINATE` / `"unknown"` rather than a hang or a loop.

### Residual W2 — the open holder, honestly reported

- Open the target with a second descriptor, hold it, commit the replace, write
  through the held descriptor. Assert: the swap **succeeded** (verified fact 5),
  the leaf holds the staged bytes, the holder's write landed in the preimage
  object, and the result carries `preimageDisposition = "retained"`. This test
  documents the limitation as behaviour; if a future macOS starts failing the
  swap, this test tells us.

### Guards that `RENAME_SWAP` does not give us

- A hard-linked target (`nlink == 2`) is refused at prepare by `snapshot_at`
  (verified fact 9 shows why). Assert `workspace_conflict`, zero effect.
- A symlink target is refused at prepare. Assert `workspace_conflict`.
- A target that is a directory when `kind == "file"` is refused (verified fact 7
  shows `RENAME_SWAP` would otherwise happily swap a file with a directory).
- Digest drift between prepare and commit ⇒ `PRECONDITION_DRIFT`, zero effect,
  original bytes intact.

### Multi-entry

- Two replaces in one set, second one's target deleted after prepare: assert
  entry 0 `APPLIED` with a preimage ref, entry 1 `PRECONDITION_DRIFT`, set
  outcome `INDETERMINATE`, entry 0's target actually changed on disk, and **no**
  attempt was made to unwind entry 0 (D7).

### Crash boundaries

- Fault 9 (after swap, before preimage record): relaunch against the same
  private store; assert `reconcileClaim` returns `indeterminate` and never
  re-applies. Assert the target on disk holds the staged bytes (the effect did
  happen) while the reported outcome remains `indeterminate` — the "never claim
  an outcome that was not observed" rule cuts in the conservative direction.
- Fault 10 (after preimage record, before swap): relaunch; assert
  `indeterminate`, the target still holds the original bytes, and no replay.

### Volume gate

- Attach a disk image whose volume does not advertise
  `VOL_CAP_INT_RENAME_SWAP` (candidates from S2/S6). Assert `replace` is refused
  `workspace_write_unsupported` while `create` and `mkdir` on the same root
  still succeed.

### Policy

- `LocalWorkspaceAuthority` unit test in
  [workspace-authority.test.ts](../../../apps/desktop/main/capabilities/workspace-authority.test.ts):
  a `read_write_no_delete` grant rejects a `replace` change set with
  `workspace_capability_denied`; a `read_write` grant accepts it (D9).

### Protocol

- `decodeCommitResult` round-trips a `PROTOCOL 3` body with 0, 1, and N entry
  results.
- A `PROTOCOL 2` response causes `workspace_helper_failed`, not a silent
  mis-parse — the existing version check at
  [native-workspace-commit-helper.ts:457](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.ts)
  must still fire.

### Windows

Mirror the happy path, the share-mode detection case, the multi-entry case, and
both crash boundaries against the Win32 helper on a `windows-latest` runner. The
share-mode test is the distinctive one: hold the target open for writing from a
second process and assert the entry outcome is `FAILED` with the
"another application has this file open" safe message and **zero effect** —
the case macOS cannot produce.

## Definition of done

- [ ] `## Spike result` is filled in with the D1 host matrix and S1–S7 outcomes,
      and D2 is either confirmed or amended against it.
- [ ] The refusal at `workspace_commit_helper.c:797-801` is gone and its comment
      is replaced by one that states the new guarantee and names residuals W1
      and W2.
- [ ] `replace` succeeds on macOS: the leaf holds the sealed stage inode and the
      preimage holds the verified original inode + digest.
- [ ] A displaced inode that does not match the snapshot triggers exactly one
      restore attempt; verified restore reports `precondition_drift` /
      `restored`; failed restore reports `indeterminate` / `unknown`.
- [ ] No temporary or unapproved name is ever created inside the user's
      workspace tree during a replace (asserted by directory listing).
- [ ] The replaced file retains the original's mode, ACL, and xattrs; a
      carry-over failure produces `FAILED` with zero effect.
- [ ] `replace` is refused with `UNSUPPORTED` on a volume that does not advertise
      `VOL_CAP_INT_RENAME_SWAP`, while `create`/`mkdir` on that root still work.
- [ ] `read_write_no_delete` grants reject `replace` with
      `workspace_capability_denied`.
- [ ] `replace` works on **Windows** through the same seam, with share-mode
      detection producing `FAILED` + zero effect when another process holds the
      file. Neither platform ships without the other.
- [ ] Windows does not use `ReplaceFileW` on the primary path; if the documented
      fallback is taken it is reported as a distinct degradation.
- [ ] Per-entry results are on the wire and reach `broker.ts`'s
      `workspaceCommitWire`; a partially applied set reports which entries
      applied and which preimages are retained.
- [ ] FS-06 introduced no journal field that FS-04 did not define, and no second
      preimage store.
- [ ] `build.mjs` pins `-mmacosx-version-min`, and a test asserts the shipped
      helper's `LC_BUILD_VERSION minos` matches the pinned floor.
- [ ] `ci-desktop` runs the native suite on a macOS runner and a Windows runner;
      a test fails if `describeNative` skips on a matching platform. No DoD item
      above is allowed to be satisfied only by a locally-run test.
- [ ] Crash faults 9 and 10 both reconcile to `indeterminate` and never replay.
- [ ] The displaced original ends up in `<root>/.0xcopilot/trash/` under FS-04's
      deterministic leaf, with `staged_before_effect = 0`; a crash between the
      swap and the relocation leaves it in the staging run directory and FS-07
      finds it there. `grep -n 'preimage' src/` shows no second store.
- [ ] `enum preimage_state` does not exist; FS-04's `enum preimage_disposition`
      is the only preimage vocabulary on the wire and in TypeScript, and
      `grep -rn 'UNVERIFIED' src/` is empty.
- [ ] A replaced file's preimage is **restorable** — either the
      `REPLACE`-from-preimage arm exists, or `prepareLocalRestore` refuses an
      occupied `restorePath` with a specific message. Offering a restore that
      always fails `precondition_drift` is a **release blocker**, not a
      follow-up.
- [ ] The Windows preimage is in `<root>/.0xcopilot/trash/` under FS-04's
      deterministic leaf, captured by `stage_preimage`;
      `grep -rn 'FileLinkInformation' src/` is empty, and no preimage is ever a
      hard link to a live object.
- [ ] `fs_carry_metadata`, `fs_commit_replace` and `fs_volume_supports_swap` are
      declared in `fs_platform.h` and **defined by both providers** — none is a
      stub or a returns-unsupported placeholder; `commit_replace_entry` contains
      no `#ifdef` and no raw POSIX call, and `check-seam.mjs` passes.
- [ ] FS-07 ships in the same build or ahead of this PRD; faults 9 and 10
      reconcile through FS-07's `c2e-` log rather than being unreadable.
- [ ] `PROTOCOL` is **unchanged by this PR** — FS-04 owns 3 and defines the
      per-entry block; FS-06 populates it.

## Out of scope

- `delete` and `move` — FS-05. They stay refused at the same parse check, which
  must keep refusing them after the `replace` branch is opened.
- Preimage storage, retention policy, expiry, trash UX, and restore-from-preimage
  — FS-04 owns all of it; FS-06 only produces the object and the reference.
- Cross-entry transactionality (D7).
- Detecting or negotiating with an open holder on macOS (D3, residual W2). No
  advisory locks, no lockfile heuristics.
- Non-ASCII path segments. `path_is_safe`
  ([line 313-332](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c))
  stays ASCII-only; broadening it is a separate, security-reviewed change.
- Network, removable, and unproven volumes — `supported_root_fd` keeps failing
  closed.
- Any user-facing surfacing of "your previous version is retained" — FS-04 and
  FS-09.
- Linux. The seam is macOS + Windows per D2 of the spine.

## Guardrails

- Do **not** implement `replace` on macOS only. Above the seam, verbs land on
  both platforms or neither.
- Do **not** use `ReplaceFileW`, or any other path-taking API, on the primary
  Windows effect path. Confinement is handle-relative or it is nothing.
- Do **not** add a second write path, a second staging area, or a second
  preimage store. Every mutation goes through the operation gateway, the stage,
  and the commit protocol.
- Do **not** retry a failed swap more than once, and never in a loop.
- Do **not** report `FAILED` unless zero effect is proven; `INDETERMINATE` is a
  valid and required result.
- Do **not** delete a preimage, and do **not** delete an inode you did not
  create — including an impostor found at the target after a rolled-back swap.
- Do **not** weaken `snapshot_at`'s symlink, `nlink != 1`, or exact-entry checks
  to make `replace` reach more files. Each one is load-bearing here (verified
  facts 7, 8, 9).
- Do **not** reintroduce advisory locks (`flock`, `O_EXLOCK`, `fcntl(F_SETLK)`)
  or lockfile sniffing as a substitute for the detection macOS lacks.
- Do **not** capture a preimage as a hard link, and do **not** put it anywhere
  but `<root>/.0xcopilot/trash/`. A hardlinked "preimage" shares an inode with a
  live object, so its recorded identity and digest describe something that can
  still change (FS-04 D3, D6); a preimage outside the trash is invisible to GC,
  to the budget and to `listRestorablePreimages`.
- Do **not** write a raw platform call in `commit_replace_entry`. Every effect,
  barrier, stat, digest and metadata carry-over goes through a seam member, on
  both providers, or the verb is not portable.
- Do **not** proceed past a metadata carry-over failure. A silent permission or
  ACL downgrade is a worse outcome than a refused write.
- Do **not** claim parity between macOS and Windows in the capability report.
  Windows detects the open holder; macOS does not. Say so.
- Do **not** leave the spike probe in the tree after D2 lands.
- Do **not** mark any DoD item complete on the strength of a locally-run macOS
  test while CI still runs `ubuntu-latest` only.
