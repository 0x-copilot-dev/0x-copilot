# PRD-FS-05 — Delete and move, both platforms

**Status:** specified
**Depends on:** FS-01 (platform seam), FS-04 (preimage + trash). FS-02 / FS-03
must have landed for the Windows half of this PRD to be _exercisable_; the code
this PRD adds sits above the seam and is written once for both.

## Implementer brief

`delete` and `move` are the two verbs that need no staging, no content upload and
no digest sealing. Both are a single no-replace rename, and both platforms have a
rename that is atomic. The work is not the rename — it is proving _which object_
was displaced, and making the answer survive the case where it is not the object
that was approved. Read [README.md](README.md) first for the locked decisions and
the guarantee; this PRD does not restate them.

Two corrections to the seam sketch in the spine, both grounded below: macOS must
use `renameatx_np(..., RENAME_EXCL)` and **not** `renameat` (plain `renameat`
silently clobbers the destination — probe-verified), and Windows must rename **by
handle** via `SetFileInformationByHandle(FileRenameInfo)` and **not** `MoveFileExW`
(which re-resolves a path and throws away the identity we just proved).

## Context

Verified against `main@b349aca2`. All file:line references are to that tree.

### The helper already carries most of a move

`parse_entry` in
[workspace_commit_helper.c:792](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)
is further along than the capability table suggests. For `MOVE` it already:

- reads and validates `destination_relative_path` (`:802`, `:804`);
- resolves a **retained destination parent descriptor** and a separate
  destination leaf through the same confined walk used for the source
  (`:809`, via `open_parent` at `:372`);
- snapshots the destination with `expected_exists = 0`, i.e. requires the
  destination to be absent (`:809`);
- refuses a `MOVE` with no destination and a non-move with a destination
  (`:808`, `:811`);
- requires the source to exist for `REPLACE`/`DELETE`/`MOVE` (`:813`);
- refuses a content slot for `DELETE`/`MOVE`/`MKDIR` (`:819`).

`free_entry` already closes `destination_parent_fd` (`:683`).
`disjoint_entries` (`:826`) already cross-checks source against destination
across every pair of entries, so a chained or swapping change set is already
impossible. `compute_prepared_binding` (`:279`) already folds
`has_destination`, `destination_relative_path` and the destination snapshot into
the claim binding digest (`:291`–`:293`), so a delete/move claim is already
bound to its exact approved effect. `entry_live` (`:736`) already re-verifies
_both_ the source and the destination snapshot immediately before commit.

Exactly two things stop `MOVE` working, and one stops `DELETE`:

1. **`workspace_commit_helper.c:801`** — `if (entry->operation != CREATE && entry->operation != MKDIR) goto fail;` refuses the verb during
   `PREPARE`, before any of the destination code above is reachable. Every line
   cited in the previous paragraph is dead code today.
2. **`commit_entry` (`:752`–`:766`)** has a `CREATE` branch, a `MKDIR` branch,
   and `else return 0`. There is no rename branch at all.
3. There is no trash. The helper's only `unlinkat` call sites are `:471` and
   `:494` (journal directory) and `:727` (private staging run directory). It has
   never removed anything from a workspace, and this PRD does not change that:
   see D5.

### Everything above the helper already speaks delete and move

- TypeScript client:
  `NativeOperation.Delete = 3` / `Move = 4`
  ([native-workspace-commit-helper.ts:48](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.ts)),
  `operationCode` maps both (`:613`), `encodeEntry` already emits the
  destination (`:595`–`:611`).
- Authority: `#validateChangeSet` requires a destination for `move` and forbids
  one otherwise
  ([workspace-authority.ts:920](../../../apps/desktop/main/capabilities/workspace-authority.ts)),
  and `#assertGrantAllowsChangeSet` refuses `delete`/`move` unless the grant mode
  is `read_write` (`:866`).
- Broker parse: identical rules at
  [broker.ts:1020](../../../apps/desktop/main/capabilities/broker.ts) and `:1056`.
- AI backend: `WorkspaceChangeEntry.operation` includes both, with the same
  destination invariant at
  [workspace_authority.py:108](../../../services/ai-backend/src/agent_runtime/capabilities/desktop/workspace_authority.py);
  the effect gate refuses `delete`/`move` on a non-`read_write` grant at
  [effects.py:182](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/effects.py).
- Approval: `WorkspaceApprovalHost.decide` already requires a **main-owned native
  confirmation for every approve**, deliberately not trusting a renderer-supplied
  destructive bit
  ([workspace-approval.ts:174](../../../apps/desktop/main/capabilities/workspace-approval.ts),
  `:201`). No new gate is needed for delete.

So the only missing layer is the helper. There is a test that pins today's
refusal — `"fails closed for non-CAS replace/delete/move rather than using an advisory lock"`
([native-workspace-commit-helper.test.ts:360](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.test.ts))
— which this PRD narrows to `replace` only (FS-06 owns that verb).

### Confinement facts this PRD relies on

- `open_parent` (`:372`) asserts `statbuf.st_dev != root_dev` on **every**
  directory hop (`:392`), so both parents of a move are provably on the root's
  volume. It also requires an exact byte-for-byte directory entry at each hop
  (`directory_has_exact_entry`, `:338`) because APFS/HFS resolve case- and
  normalization-insensitively.
- `snapshot_at` (`:400`) refuses symlinks and refuses a regular file with
  `st_nlink != 1` (`:405`–`:406`).
- `supported_root_fd` (`:358`) restricts roots to `apfs`/`hfs`.
- `command_prepare` (`:850`) already fails `UNSUPPORTED` when the private staging
  directory is not on the same volume as the root. The trash inherits exactly
  this constraint (D5).

### Probe evidence

Run on macOS 15.6.1 (build 24G90, Darwin 24.6.0), APFS, by two scratch C
programs written for this PRD; each row is one call against a temp APFS
directory, and every call used is spelled out in D2, D3 and D4, so the probes
are reconstructible from this document alone.

| #   | What was probed                                                    | Result                                                  |
| --- | ------------------------------------------------------------------ | ------------------------------------------------------- |
| 1   | `renameatx_np(EXCL\|NOFOLLOW_ANY)` across two dir fds, free name   | `0`                                                     |
| 2   | same, destination occupied                                         | `-1 EEXIST`                                             |
| 3   | plain `renameat` onto an occupied destination                      | `0` — **destination content silently lost**             |
| 4   | `RENAME_NOFOLLOW_ANY` when the **source leaf** is a symlink        | `0` — the symlink itself is renamed                     |
| 5   | `RENAME_NOFOLLOW_ANY` through a **symlinked intermediate**         | `-1 ELOOP`                                              |
| 6   | `renameatx_np(EXCL)` on a **non-empty** directory                  | `0` — a whole tree moves in one call                    |
| 7   | `renameatx_np(EXCL)` while the file is held open by another fd     | `0` — macOS has no share-mode refusal                   |
| 8   | `openat(parent, leaf, O_NOFOLLOW_ANY)` on a symlink leaf           | `-1 ELOOP`                                              |
| 9   | fd pin vs. an external write+rename re-bind of the same name       | pinned ino ≠ named ino, divergence detectable           |
| 10  | rename-by-name after such a re-bind                                | displaced the **foreign** inode, observable post-effect |
| 11  | compensating `RENAME_EXCL` back into the vacated name              | `0`, same inode restored                                |
| 12  | same, after the vacated name was re-taken                          | `-1 EEXIST` — compensation fails closed                 |
| 13  | `renameatx_np(RENAME_RESOLVE_BENEATH)`                             | `-1 EINVAL` — not usable on this release                |
| 14  | rename of a file with `nlink == 2`                                 | `0`, the sibling link survives                          |
| 15  | `VOL_CAP_INT_RENAME_EXCL` on APFS via `getattrlist`                | supported = 1, valid = 1                                |
| 16  | `VOL_CAP_INT_RENAME_OPENFAIL` on APFS                              | supported = 0, valid = 1                                |
| 17  | case-only rename `Doc.txt` → `doc.txt` with `RENAME_EXCL`          | `0`                                                     |
| 18  | `RENAME_EXCL` onto a case-variant of a **different** existing file | `-1 EEXIST`                                             |
| 19  | `fstatat` for both `doc.txt` and `Doc.txt` after 17                | both succeed — names alias on this volume               |

Declarations verified in the local SDK:
`renameatx_np(int, const char *, int, const char *, unsigned int)` since macOS
10.12 — `/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/usr/include/sys/stdio.h:53`;
`RENAME_SWAP 0x2`, `RENAME_EXCL 0x4`, `RENAME_NOFOLLOW_ANY 0x10`,
`RENAME_RESOLVE_BENEATH 0x20` at `sys/stdio.h:36`–`:40`;
`VOL_CAP_INT_RENAME_EXCL 0x00080000` at `sys/attr.h:395`.

**Nothing in this PRD's Win32 half is probe-verified.** No Windows host was
available. Every Win32 claim is marked and every one that needs a spike is named
in D9 with the test that settles it.

## Interfaces consumed

### From FS-01 — the platform seam

FS-01 owns the header and [FS-01 §2 and §8](PRD-FS-01-platform-seam.md) are
normative for every name and signature. This PRD's first draft wrote its own
sketch and drifted in four ways; all four conform:

| drafted here                                       | real                                                                                       |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `typedef struct fs_handle fs_handle;` (incomplete) | the complete by-value struct in FS-01 §2                                                   |
| `fs_identity_of(h, out)`                           | `fs_stat_handle(h, &meta)` then `meta.id`                                                  |
| `fs_open_confined(root, relpath, out)`             | portable `open_parent()` over `fs_open_root` + `fs_open_dir_at` + `fs_dir_for_each`        |
| `#define FS_ABSENT (-2)`                           | FS-01's `enum fs_status`, whose `FS_ABSENT` is **1**. There is no second status vocabulary |

FS-05 consumes `fs_stat_handle`, `fs_stat_at`, `fs_identity_equal`,
`fs_identity_same_volume`, `fs_dir_for_each`, `fs_durable_barrier`, `fs_close`,
`fs_dup`, and the portable `open_parent` built on them.

### From FS-04 — preimage and trash

FS-04 owns the trash **and the no-replace rename primitive**. FS-05 consumes
exactly the following, all of which FS-04 actually exposes — an earlier draft of
this section invented `fs_trash_dir`, `fs_trash_allocate_leaf`,
`struct fs_preimage_row` and `fs_preimage_put`, none of which exist in FS-04:

| FS-05 needs                                | FS-04 actually provides                                                                                                                                                                                            |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| the trash directory handle                 | opened per-prepare from the retained **root** handle at `<root>/.0xcopilot/trash/` (FS-04 D1, spine D4). Not an inherited fd 4/5-style capability, because it lives inside the grant.                              |
| a trash leaf name                          | `pre_<32 hex>` where the hex is `HMAC(journal_key, "workspace-preimage-id-v1" ‖ claim ‖ be32(entry_index))` (FS-04 D10) — **deterministic**, not random. See D4.                                                   |
| the durable per-entry row                  | `struct journal_preimage_row` (FS-04 §6), appended to the journal record as a MAC'd trailer under `JOURNAL_VERSION 4`. There is no separate `fs_preimage_put`; the row is written by the existing `journal_store`. |
| the displacement / restore / collect verbs | portable `stage_preimage` / `restore_preimage` / `collect_preimage` (FS-04 D12)                                                                                                                                    |
| the rename primitive                       | `fs_rename_noreplace` + `enum fs_rename_result` (FS-04 D12) — declared by FS-04, not by FS-05                                                                                                                      |
| the disposition vocabulary                 | `enum preimage_disposition` (FS-04 §6): `NONE / RETAINED / RESTORED / COLLECTED / UNKNOWN`. FS-05's D4 lanes map onto `RETAINED` and `RESTORED`; there is no `PENDING` / `DISPLACED` pair.                         |

The `PENDING → DISPLACED` states this PRD drafted are carried instead by FS-04's
`journal_preimage_row.staged_before_effect` plus FS-07's `effect_row.phase`
(`ARMED` / `OBSERVED`), which is where the effect frontier belongs. D10 is
rewritten against those.

### From the existing helper

`snapshot_at` (`:400`), `snapshot_matches` (`:421`), `regular_digest_fd`
(`:304`), `directory_has_exact_entry` (`:338`), `open_parent` (`:372`),
`journal_transition` (`:601`), `entry_live` (`:736`), `disjoint_entries`
(`:826`). All reused unchanged except where D6 adds a check.

## Interfaces exposed

### C — four additions to the platform seam

`enum fs_rename_result` and `fs_rename_noreplace` are **FS-04's**, not FS-05's
(FS-04 D12): FS-04 lands first and its own displacement, restore and collect are
no-replace renames, so reserving the primitive here would have forced FS-04 to
invent a second one. FS-05 consumes it. What FS-05 adds is the pinning and
inspection set below, per FS-01's reserved spellings.

```c
/* Pin the object at (parent, leaf) by handle. Refuses a symlink or reparse
 * point AT THE LEAF (intermediates are already refused by the retained parent).
 * `expect_dir` selects O_DIRECTORY / FILE_DIRECTORY_FILE. */
int fs_pin_target(fs_handle parent, const char *leaf, int expect_dir,
                  fs_handle *out);

/* Number of hard links to the pinned object. */
int fs_link_count(fs_handle pinned, uint32_t *out);

/* 1 if the pinned directory contains no entry other than "." / "..". */
int fs_directory_is_empty(fs_handle pinned_dir, int *empty_out);

/* Identity currently bound to (parent, leaf). Returns FS-01's enum fs_status:
 * FS_OK with *out filled, FS_ABSENT (== 1) when the name does not exist, or
 * FS_ERROR. Used for the post-effect check; never used to authorize. */
enum fs_status fs_identity_at(fs_handle parent, const char *leaf,
                              struct fs_identity *out);

/* Volume capability query, on BOTH providers (FS-01 rule 2). Declared by FS-04
 * because FS-04's displacement needs it first; listed here because D2's prepare
 * gate is its first caller for a workspace verb. */
int fs_volume_supports_rename_excl(fs_handle root);
```

### C — two portable commit paths, written once

```c
/* Both live above the seam in workspace_commit_helper.c and are compiled for
 * both platforms. Neither contains a single #ifdef. */
static enum outcome commit_delete(struct prepared *p, uint32_t i, struct entry *e);
static enum outcome commit_move  (struct prepared *p, uint32_t i, struct entry *e);
```

`commit_entry` gains the prepared context it needs for the trash and the
preimage ledger:

```c
/* was: static int commit_entry(struct entry *entry); */
static enum outcome commit_entry(struct prepared *prepared, uint32_t index,
                                 struct entry *entry, uint32_t *reason_out);
```

`struct entry` gains three fields:

```c
  fs_handle          source_pin;      /* FS_HANDLE_INVALID until commit      */
  struct fs_identity pinned_identity;
  char               trash_leaf[40];  /* FS-04's "pre_" + 32 hex + NUL       */
```

`free_entry` (`:680`) must close `source_pin` alongside `stage`,
`parent` and `destination_parent`. Per FS-01 D13, `source_pin` is initialised to
`FS_HANDLE_INVALID` by `entry_init` — never left to `calloc` zeroing, which is
the `close(0)` defect FS-01 commit 2 fixed.

`struct prepared` gains `fs_handle trash;` — the per-prepare handle to
`<root>/.0xcopilot/trash/`, opened from the retained root handle (D5), closed by
`destroy_prepared`. It is deliberately not a global: it is scoped to the root the
prepare walked, and there is one trash per grant root.

### Wire — `reason` is a field of FS-04's per-entry block, not a set-level trailer

`write_commit_result` (`:891`) today emits
`(outcome u8, receipt string, result_digest string, safe_message string)` and
sets a message only for `INDETERMINATE` (`:893`).

An earlier draft of this PRD appended a single set-level `reason u32`. That is
wrong for the same reason FS-06 gave for its own block: `command_commit` applies
entries in order and breaks on the first failure (`:927`), so a multi-entry set
can be partially applied and one `reason` cannot describe it. It is also a second
wire change competing with FS-06's.

**There is one block, defined once in [FS-04 §6a](PRD-FS-04-preimage-trash.md),
under `PROTOCOL 3`.** FS-05 populates its `reason` field and bumps nothing:

```text
u32  entry_result_count
  repeat entry_result_count:
    u8   entry_outcome
    u32  reason                # <- FS-05 fills this; 0 elsewhere
    u8   preimage_disposition  # FS-04's enum, incl. PREIMAGE_NONE = 0
    str  preimage_ref
    str  displaced_digest
```

FS-05 contributes the codes:

```c
enum commit_reason {
  REASON_NONE = 0,
  REASON_DISPLACED_FOREIGN_RESTORED = 1,  /* D4 — net effect zero            */
  REASON_DISPLACED_FOREIGN_RETAINED = 2,  /* D4 — object held in trash       */
  REASON_TARGET_HELD_BY_ANOTHER_PROCESS = 3,
  REASON_DESTINATION_OCCUPIED = 4,
  REASON_SOURCE_VANISHED = 5,
  REASON_DIRECTORY_NOT_EMPTY = 6,
  REASON_MULTIPLE_HARD_LINKS = 7,
  REASON_CROSS_VOLUME = 8,
  REASON_TRASH_UNAVAILABLE = 9,
  REASON_MOUNT_POINT_TARGET = 10
};
```

TypeScript:

```ts
export type NativeCommitReason =
  | "displaced_foreign_object_restored"
  | "displaced_foreign_object_retained"
  | "target_held_by_another_process"
  | "destination_occupied"
  | "source_vanished"
  | "directory_not_empty"
  | "multiple_hard_links"
  | "cross_volume"
  | "trash_unavailable"
  | "mount_point_target";

// `reason` is per ENTRY, on FS-06's NativeWorkspaceEntryResult / FS-04 §6a.
export interface NativeWorkspaceEntryResult {
  readonly outcome: WorkspaceCommitOutcome;
  readonly reason?: NativeCommitReason; // added by FS-05
  readonly preimageDisposition: WorkspacePreimageDisposition; // FS-04
  readonly preimageRef?: string;
  readonly displacedDigest?: string;
}

export interface NativeWorkspaceCommitResult {
  readonly outcome: WorkspaceCommitOutcome;
  readonly receiptRef: string;
  readonly resultDigest?: string;
  readonly safeMessage?: string;
  readonly entryResults: readonly NativeWorkspaceEntryResult[]; // may be []
}
```

`WorkspaceCommitResult`
([workspace-authority.ts:179](../../../apps/desktop/main/capabilities/workspace-authority.ts))
gains `entryResults`, `toCommitResult` (`:1040`) forwards it, and it lands in
`WorkspaceJournalRecord.result` (`:214`).

**Version coupling.** `PROTOCOL` (`:45`) and `HELPER_PROTOCOL_VERSION`
([native-workspace-commit-helper.ts:28](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.ts))
must move together. The helper binary is resolved out of the same packaged
artifact as main (`resolveNativeWorkspaceCommitHelperPath`, `:482`), so there is
no mixed-version window and no compatibility shim is needed. **FS-05 never bumps
the protocol**: FS-04 is an upstream dependency, FS-04 owns `PROTOCOL 3` and
defines the block, and FS-05 only fills in `reason`. If FS-05 finds itself
bumping a version, the dependency order has been violated.

## Design

### D1. One primitive, two verbs

`delete` is a rename into the trash. `move` is a rename to the approved
destination. Nothing else. The only differences are which destination parent is
used, what the destination-absent precondition means, and whether a preimage row
is written. Writing them as two thin callers of one `fs_rename_noreplace` is what
lets the verbs land on both platforms together, which the spine requires.

Rejected: `unlinkat` / `DeleteFileW` for delete. An unlink has no preimage, so
the guarantee's "verify what was displaced, roll back or retain the preimage" is
unachievable, and the divergence lane in D4 becomes unrecoverable rather than
recoverable. Delete is never a removal at the helper level; it is a relocation.
Physical removal is a retention decision made later, by the owner of
`RetentionCandidateKind.PREIMAGE`
([retention.py:79](../../../services/ai-backend/src/agent_runtime/surfaces_v2/retention.py)).

### D2. No-replace is the platform's own primitive, never a check-then-rename

Plain `renameat` **replaces the destination**. Probe 3: the destination's content
was silently lost with a `0` return. Any design that reads "check the destination
is absent, then `renameat`" has a window in which a file the user just created is
destroyed with no preimage and no signal. That is the single worst failure this
PRD could ship, so the destination-absent property must come from the kernel:

- **macOS** — `renameatx_np(src_parent, src_leaf, dst_parent, dst_leaf, RENAME_EXCL | RENAME_NOFOLLOW_ANY)`.
  `RENAME_EXCL` returns `EEXIST` on an occupied destination (probe 2).
  `RENAME_NOFOLLOW_ANY` refuses a symlinked **intermediate** component (probe 5,
  `ELOOP`) — it does **not** refuse a symlinked leaf (probe 4, the symlink was
  renamed). The leaf case is closed by D3's pinning open instead.
  Availability is real, not assumed: the declaration is in the SDK since 10.12,
  and APFS advertises `VOL_CAP_INT_RENAME_EXCL` as supported and valid (probe 15).
- **Windows** — `SetFileInformationByHandle(pinned, FileRenameInfoEx, ...)` with
  `Flags` **not** carrying `FILE_RENAME_REPLACE_IF_EXISTS`, and `RootDirectory`
  set to the retained destination parent handle. Collision surfaces as
  `STATUS_OBJECT_NAME_COLLISION` / `ERROR_ALREADY_EXISTS`. This is **FS-04's**
  `fs_rename_noreplace` body, not a second call site — FS-05 calls the primitive
  and never the API. (An earlier draft of this bullet named the non-`Ex`
  `FileRenameInfo` with `ReplaceIfExists = FALSE`; that is the documented
  fallback if the project's minimum Windows build predates `FileRenameInfoEx`,
  which FS-02's "minimum Windows version" open question must pin, not a second
  primary path.)

Neither spelling is probe-verified; see D9 spike 1.

`RENAME_RESOLVE_BENEATH` (`sys/stdio.h:40`) would be a stronger confinement flag
than `RENAME_NOFOLLOW_ANY`, but it returns `EINVAL` on macOS 15.6.1 / APFS
(probe 13). Not used. Revisit only with a probe showing it accepted on the
minimum supported OS.

**Capability probe, not assumption.** At `PREPARE`, when a change set contains a
`DELETE` or a `MOVE`, the portable path calls
`fs_volume_supports_rename_excl(root)` — FS-04's seam member (FS-04 D12), on
both providers — and refuses the whole change set with `UNSUPPORTED` when it
returns 0, before the set can be approved. Same fail-closed posture as
`supported_root_handle` over `fs_volume_supported`, and one probe per prepare.

The raw call belongs to the **provider**, not to this PRD: the POSIX body is
`fgetattrlist(ATTR_VOL_INFO | ATTR_VOL_CAPABILITIES)` requiring
`VOL_CAP_INT_RENAME_EXCL` in both `capabilities` and `valid` (probe 15 shows APFS
sets both). The Win32 body has **no capability bit to read** — there is no
`VOL_CAP_INT_*` analogue — so it is expected to answer from `fs_volume_supported`
alone (FS-04 D12). That is a strictly weaker claim than the macOS one and the
capability report must say so rather than showing one number for both (FS-09 D15).

### D3. The identity check is handle-pinned, and the two platforms differ in how binding that is

The check that matters is not "does the name still hash to the approved digest" —
`entry_live` (`:736`) already does that by name. It is "**is the object I am
about to displace the object that was approved**". A name is not an identity.

The sequence, in both `commit_delete` and `commit_move`, immediately before the
rename and after `entry_live` has passed:

1. `fs_pin_target(entry->parent, entry->leaf, expect_dir, &entry->source_pin)`
   — `entry->parent`, not `entry->parent_fd`: FS-01's permitted-edit table renames
   every `*_fd` field when the type becomes `fs_handle`.
   On macOS this is `openat(parent, leaf, O_RDONLY | O_CLOEXEC | O_NOFOLLOW_ANY)`
   (`| O_DIRECTORY` for a directory), which returns `ELOOP` for a symlinked leaf
   (probe 8) — this is what closes the gap `RENAME_NOFOLLOW_ANY` leaves open.
   On Windows this is one `NtCreateFile` relative to the parent handle with
   `FILE_OPEN_REPARSE_POINT` and a post-open `FILE_ATTRIBUTE_REPARSE_POINT`
   refusal, exactly as `open_component` already does in
   [workspace_fs.c:239](../../../apps/desktop/native/workspace-fs/src/workspace_fs.c),
   requesting `DELETE | FILE_READ_ATTRIBUTES | FILE_READ_DATA | SYNCHRONIZE`.
2. `fs_stat_handle(source_pin, &meta)` then `entry->pinned_identity = meta.id`
   — there is no `fs_identity_of` (FS-01 §8, and the conformance table above).
   For a regular file, `regular_digest_handle(source_pin, ...)` (FS-01's portable
   rename of `regular_digest_fd`, `:304`, reusable as-is — it re-stats before and
   after and refuses if the object changed underneath).
3. Compare against `entry->source`: identity, mode, size, digest. Any mismatch →
   `PRECONDITION_DRIFT`, no effect, journal `FAILED_BEFORE_EFFECT`.
4. `fs_link_count(source_pin)` must be 1 for a regular file. `snapshot_at`
   already refuses `st_nlink != 1` at prepare (`:406`), but link count is mutable
   between prepare and commit and probe 14 shows a rename leaves the sibling link
   intact — so "deleted" would be a false claim. Mismatch →
   `PRECONDITION_DRIFT` + `REASON_MULTIPLE_HARD_LINKS`.
5. `fs_identity_same_volume(&entry->source.id, &prepared->root_id)` must hold.
   (`struct snapshot` carries `struct fs_identity id` after FS-01 D3/D4, not
   `dev_t dev`; comparing a bare `dev` is not expressible.) `open_parent` proves
   the _parents_ are on the root volume (`:392`) but never checks the leaf, and a
   leaf can itself be a mount point. Mismatch → `PRECONDITION_DRIFT` +
   `REASON_MOUNT_POINT_TARGET`. (Checked at prepare too, as `UNSUPPORTED`, so it
   never reaches approval — see D8.)

**Windows: the pin is the rename.** `SetFileInformationByHandle` renames the
object the handle refers to. The name is not re-resolved between step 2 and the
rename, so the identity verified in step 2 is provably the identity displaced.
The divergence lane in D4 is **unreachable on Windows**, and its tests are
darwin-only.

**macOS: the pin is evidence, not a lock.** There is no macOS primitive that
binds a rename to an observed inode. Between step 2 and `renameatx_np`, the name
can be re-bound — an editor's write-temp-then-rename save is the ordinary way
this happens, not an exotic attack. Probe 9 shows the pinned inode and the named
inode diverging; probe 10 shows the rename then displacing the foreign inode.
The window is small and the check is not useless — it converts "we might have
moved the wrong file and will never know" into "we moved a specific wrong file
and can say which". That is what D4 is for.

This asymmetry is the concrete form of the spine's claim that Windows is
favourable for the verbs macOS refuses, and it is why the verb ships on both
platforms with one honest outcome vocabulary rather than two guarantees.

### D4. Post-effect verification, and why divergence is recoverable rather than fatal

After `fs_rename_noreplace` returns `FS_RENAME_OK`, the helper reads back which
object landed at the destination:

```
fs_identity_at(dst_parent, dst_leaf, &landed)
```

This read is sound, not another race, for a specific reason: for `DELETE` the
destination is `<root>/.0xcopilot/trash/` under a 128-bit **keyed** leaf
(FS-04 D10, `HMAC(journal_key, …)`), which no process without the installation's
`journal_key` can predict or occupy, and which the reserved-segment refusal keeps
out of every change set and every read call (D5); for `MOVE` the destination is
within the granted root and could in
principle be re-bound again, which is why the `MOVE` divergence branch resolves
to `INDETERMINATE` rather than attempting a second compensation.

Three cases:

**(a) `landed == pinned_identity`.** The approved object was displaced. Outcome
`APPLIED`, `REASON_NONE`. This is the overwhelmingly common path and the only one
that reports success.

**(b) `landed != pinned_identity` — a foreign object was displaced.** The
approved effect did not happen and an unapproved one did. Reporting
`PRECONDITION_DRIFT` here without acting would be a lie by omission: the user's
file _is_ gone from its name. For `DELETE`, the helper makes **exactly one**
compensating attempt:

```
fs_pin_target(prepared->trash, entry->trash_leaf, is_dir, &back_pin);
fs_rename_noreplace(prepared->trash, entry->trash_leaf, back_pin,
                    entry->parent, entry->leaf);
fs_identity_at(entry->parent, entry->leaf, &restored);  /* must == landed */
```

- Success **and** `restored == landed` → the foreign object is back under its own
  name, nothing net changed. Outcome `PRECONDITION_DRIFT`,
  `REASON_DISPLACED_FOREIGN_RESTORED`. Probe 11 confirms the restore lands the
  same inode back. The second identity read is not optional: a restore whose
  result was not observed is not a restore.
- `FS_RENAME_EXISTS` — the vacated name was taken in the meantime (probe 12,
  `EEXIST`) — or any other result → the object **stays in the trash**, its
  preimage row is marked `RETAINED`, and the outcome is `INDETERMINATE` with
  `REASON_DISPLACED_FOREIGN_RETAINED`.

The compensation is not "recovery" in the PRD-C2 D9 sense and does not weaken
"never force": it is bounded to one attempt, it is `RENAME_EXCL` so it can never
overwrite anything, it is journaled before it is attempted (D10), it is never
retried, and its own outcome is verified by a second `fs_identity_at`. It undoes
an effect that was never authorized rather than reversing one that was.

For `MOVE`, case (b) is reported as `INDETERMINATE` +
`REASON_DISPLACED_FOREIGN_RETAINED` with no compensation. The displaced object is
sitting at an approved, user-visible destination path rather than in a private
trash, and a second rename to put it back would be a third mutation racing the
same writer. The receipt names both identities; recovery is FS-07's.

**This is why the preimage is load-bearing rather than a nicety.** Without it,
case (b) is indistinguishable from case (a) and the helper would report
`APPLIED` for having deleted a file nobody approved. With it, the wrong object is
intact, identified, and either restored or retrievable — a data-loss problem
converted into a reversible one, which is exactly the framing the spine commits
to.

`FS_RENAME_ABSENT` (the source name vanished before the rename) is a no-effect
case: `PRECONDITION_DRIFT` + `REASON_SOURCE_VANISHED`, verified by
`fs_identity_at(dst) == FS_ABSENT`. `FS_RENAME_ERROR` is `INDETERMINATE` — the
outcome was not observed, and per the spine that is a required, valid result.

### D5. Delete is a rename into the trash at `<root>/.0xcopilot/trash/`

**The trash is inside the granted root.** This PRD's first draft said the
opposite — "not in the granted root", app-private, inherited like the staging and
journal capabilities — and FS-04 D1 and FS-06 D2 each said a third thing. The
spine settled it as **D4** and FS-04 owns the substrate; FS-05 conforms. The
reason FS-04's argument wins is the one that matters for _this_ PRD: `open_parent`
refuses any hop that crosses `st_dev` (`:392`), so a trash under the root is
same-volume **by construction**, which makes displacement an O(1) rename on both
platforms. An app-private trash under `userData` is same-volume only by luck —
`command_prepare:850` already fails closed when it is not — and on a machine
where it is not, every delete becomes a cross-volume copy: O(n), able to
half-succeed, doubling peak space. That is precisely the failure the guarantee
exists to remove.

The objections this PRD raised were real and are **paid**, not waived (FS-04 D1
and D2 carry the detail):

| objection raised here                                      | how FS-04 pays it                                                                                                                                                                            |
| ---------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| visible to the model through the read capability           | `.0xcopilot` is refused on the **read** surface too — `list`, `glob`, `grep`, `stat`, `read` (FS-04 D2.3). Without that the trash is a read-around for content the agent may no longer read. |
| targetable by a later change set                           | refused at the gateway **and** independently in `parse_entry`, ASCII-case-insensitively (FS-04 D2.1, D2.2)                                                                                   |
| indistinguishable from user content in the overlay         | same reserved-segment filter; plus dot prefix, `FILE_ATTRIBUTE_HIDDEN`, and a `.gitignore` of `*`                                                                                            |
| user-writable, so permissions are not an integrity control | integrity comes from the MAC'd journal row and the content digest, never from the directory mode (FS-04 D3)                                                                                  |

FS-05 therefore constrains the trash in exactly two ways:

1. **Same volume as the root — now by construction, and still asserted.** The
   `trash.st_dev != root.st_dev → UNSUPPORTED` check this PRD proposed is kept as
   a cheap assertion rather than a load-bearing gate, because `fs_open_dir_at`
   from the root handle cannot reach another volume. It fires only if the
   confinement walk has a bug, which is exactly when you want it.
2. **A leaf no other writer can occupy.** D4's post-effect read is sound only
   because nothing else can take the trash leaf. FS-04 D10's name is
   **deterministic** — `pre_<hex(HMAC(journal_key, …‖claim‖entry_index))>` — not
   random. That still satisfies D4: the name is unpredictable to any process
   without `journal_key` (derived per installation,
   `workspace-production-authority.ts:172-186`), and the determinism is a
   _feature_, because a repeated commit of the same claim collides on the
   no-replace displacement instead of minting a second preimage. What it does
   **not** survive is a second local process that has the key, which is the app
   itself; FS-04 D5's `c2p-` lease is what excludes that.

Note for the reader coming from `PRD-C2` D7 ("move to private same-volume trash
by default"): that default is superseded for this program by spine D4. The
"same-volume" half is honoured more strongly than PRD-C2 asked; the "private"
half becomes reserved-and-unaddressable rather than outside-the-grant.

The multi-volume-grant question this PRD raised **dissolves** under D4: the trash
is per-root, so two grants on two volumes have two trashes and neither needs to
reach the other. Removed from Open questions.

### D6. What delete may target

| Target                      | Behaviour                                                         |
| --------------------------- | ----------------------------------------------------------------- |
| regular file, `nlink == 1`  | supported                                                         |
| regular file, `nlink > 1`   | refused — D3 step 4, `REASON_MULTIPLE_HARD_LINKS`                 |
| symlink                     | refused — `snapshot_at:405` at prepare, `fs_pin_target` at commit |
| empty directory             | supported                                                         |
| non-empty directory         | refused — `REASON_DIRECTORY_NOT_EMPTY`                            |
| mount point / reparse point | refused — D3 step 5, `REASON_MOUNT_POINT_TARGET`                  |
| socket, fifo, device        | already refused — `snapshot_at:409`                               |

Non-empty directories are refused because probe 6 shows a single
`renameatx_np` relocating an entire tree: one approved entry would displace an
unbounded number of unapproved objects, and the change set the user saw would not
describe what happened. PRD-C2 already scopes recursive implicit deletes out
(`PRD-C2:356`), and the legacy read path already refuses a non-empty directory
delete ([host-fs.ts:624](../../../apps/desktop/main/capabilities/host-fs.ts)),
so this is consistent rather than new.

Emptiness is checked twice: once at prepare (so the user never approves something
that will fail) and once at commit from the **pinned directory handle**, not by
path. On macOS `fs_directory_is_empty` reuses the `directory_has_exact_entry`
pattern (`:338`) — `openat(pinned, ".", O_RDONLY | O_DIRECTORY | O_NOFOLLOW_ANY)`
then `fdopendir`, because `fdopendir` consumes the descriptor and the pin must
survive. A directory that becomes non-empty between the check and the rename is
caught by D4: the moved directory is re-enumerated in the trash and a non-empty
result takes the compensating-restore lane, because the entry approved was
"delete an empty directory" and what was displaced was not that.

### D7. Move is no-replace, within-root, and there is no overwrite variant

`entry->destination` is snapshotted with `expected_exists = 0` (`:809`) and
re-verified by `entry_live` (`:738`) at commit; `RENAME_EXCL` /
`ReplaceIfExists = FALSE` then makes the absence a kernel guarantee rather than a
checked assumption.

An overwriting move is **not representable**: `WorkspaceChangeEntry` has no
overwrite field on either side of the wire
([workspace-authority.ts:77](../../../apps/desktop/main/capabilities/workspace-authority.ts),
[workspace_authority.py:54](../../../services/ai-backend/src/agent_runtime/capabilities/desktop/workspace_authority.py)),
so the helper cannot receive one. The chat surface _does_ model one —
`isDestructiveWorkspaceOperation` treats `move` + `overwrite === true` as
destructive and labels it `"move · overwrite"`
([workspaceStageProjection.ts:245](../../../packages/chat-surface/src/thread-canvas/workspaceStageProjection.ts),
`:258`) — but nothing populates that flag from a native change set. FS-05 does not
add it. Recorded in Open questions.

**Case-only renames are not supported.** Probe 17 shows `RENAME_EXCL` itself
permits `Doc.txt` → `doc.txt`, but probe 19 shows `fstatat` succeeds for both
spellings on a case-insensitive APFS volume, so `snapshot_at`'s destination-absent
requirement (`:404`) fails the entry at prepare. The refusal is correct — a
case-insensitive volume genuinely cannot distinguish "destination is free" from
"destination is the source" through `fstatat` — and it is better to refuse than
to special-case identity comparison into the absence check. Probe 18 confirms the
important half: `RENAME_EXCL` refuses a case-variant of a **different** file with
`EEXIST`, so no case-folding collision can silently destroy anything.

### D8. Cross-volume move is refused, not implemented

Refused at **prepare**, with `UNSUPPORTED` and `REASON_CROSS_VOLUME`, so it never
reaches an approval sheet.

It is already structurally unreachable through the normal path. `open_parent`
refuses any directory hop whose `st_dev` differs from the root (`:392`), and the
Windows walk refuses reparse points including volume mount points
([workspace_fs.c:266](../../../apps/desktop/native/workspace-fs/src/workspace_fs.c)),
so both parents are provably on the root volume. The only residual is a leaf that
is itself a mount point, which D3 step 5 refuses explicitly. `FS_RENAME_XDEV` is
therefore a defence-in-depth code, not an expected path.

Why refuse rather than implement journalled copy + verify + delete:

1. **It is a second write path.** The spine's first guardrail forbids exactly
   that. Copy+verify+delete has its own staging, its own byte accounting, its own
   partial-copy failure mode and its own multi-phase journal — none of which the
   commit protocol has today, all of which would then need to be reviewed
   alongside the rename path forever.
2. **It cannot honour the guarantee.** "Act atomically where the platform allows"
   has an answer here: the platform does not allow it. A copy is observable
   half-done; there is a window where the file exists at both paths, and a crash
   inside it is indistinguishable from a duplicate. The honest response is to say
   the platform cannot do it, not to synthesise something weaker and call it move.
3. **The cost of refusing is near zero.** A grant is one tree on one volume. A
   user who wants to move between volumes can be told so, in a specific message,
   before approving.
4. **It was already scoped out.** PRD-C2 lists cross-volume move under out of
   scope at launch (`PRD-C2:220`, `:355`). FS-05 does not reopen it.

`FS_RENAME_XDEV` at commit time (which should be unreachable) is `FAILED`, not
`INDETERMINATE`: a cross-device rename fails before any effect.

**Unverified:** that macOS returns `EXDEV` and Windows `STATUS_NOT_SAME_DEVICE`
for these calls is the documented POSIX/NT behaviour but was not probed — no
second writable volume was available on the host. It affects only which of two
refusal codes is emitted on a path that D3 step 5 already blocks.

### D9. The Windows call sequence

**Every claim in this section is unverified against a Windows host.** Each item
marked _spike_ has the test that settles it and what changes if it fails.

Delete and move, identically, differing only in the destination handle:

1. Open the target relative to the retained parent handle, one component,
   `NtCreateFile` with `FILE_OPEN_REPARSE_POINT`, desired access
   `DELETE | FILE_READ_ATTRIBUTES | FILE_READ_DATA | SYNCHRONIZE`, share mode
   `FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE`,
   `FILE_DIRECTORY_FILE` for a directory. Refuse
   `FILE_ATTRIBUTE_REPARSE_POINT` after the open, as `open_component` already
   does (`workspace_fs.c:266`).
2. `GetFileInformationByHandleEx(h, FileIdInfo, ...)` →
   `FILE_ID_INFO { ULONGLONG VolumeSerialNumber; FILE_ID_128 FileId; }` — the
   `dev + ino` analogue named in the spine's seam.
3. `GetFileInformationByHandleEx(h, FileStandardInfo, ...)` →
   `NumberOfLinks` must be 1 for a regular file (D6).
4. Digest the handle for a regular file.
5. `fs_rename_noreplace(...)` — FS-04's primitive. Its Win32 body is
   `SetFileInformationByHandle(h, FileRenameInfoEx, &info, size)` with `Flags`
   omitting `FILE_RENAME_REPLACE_IF_EXISTS`, `info.RootDirectory = <retained
destination parent handle>`, `info.FileName = <leaf as UTF-16, no separators>`,
   `info.FileNameLength = bytes`. FS-05 never writes this call itself.
6. `fs_identity_at(dst_parent, dst_leaf)` and compare — same portable code as
   macOS, expected always to match (D3).

**The open in step 1 is where Windows earns its advantage.** It requests `DELETE`
access. If another process holds the file open without granting
`FILE_SHARE_DELETE` — the ordinary Office/Excel case — the open fails with
`ERROR_SHARING_VIOLATION` **before any effect**, and the helper reports `FAILED`
with `REASON_TARGET_HELD_BY_ANOTHER_PROCESS` and a specific safe message. macOS
has no equivalent: probe 7 renamed a held-open file successfully, and APFS
reports `VOL_CAP_INT_RENAME_OPENFAIL` as _not_ supported (probe 16). Same verb,
strictly better behaviour on Windows, and the difference is visible in the
receipt rather than hidden.

_Spike 1 — `RootDirectory`-relative rename._ **Shared: FS-02 D2 property 2 and
FS-06 D5 both depend on this experiment and neither runs a second copy of it.**
Does `SetFileInformationByHandle(FileRenameInfoEx)` honour a directory `HANDLE`
in `RootDirectory` with a separator-free relative `FileName`, and does it
**reject** a `FileName` containing `\` or `/` rather than resolving it? This is
the documented `FILE_RENAME_INFORMATION` semantics at the NT layer and the Win32
wrapper is expected to pass it through, but it is the single load-bearing
assumption of the Windows half — and the separator clause is the confinement
property: a `RootDirectory`-relative name that accepted a separator would let a
destination escape the walked subtree.

**Test:** rename a file between two subdirectories of a temp root using only
handles, no path strings, and assert the result; then repeat with `sub\file` as
`FileName` and assert it is refused. Record the exact status for a destination
occupied by (a) a file, (b) a directory, (c) a junction — FS-02 D2 property 3
needs the third, because a followed reparse point at the leaf would give `create`
a symlink-follow hazard it does not otherwise have.

**If it fails:** fall back to `NtSetInformationFile(FileRenameInformationEx)`
obtained from `ntdll.dll` the way `workspace_fs.c:276` already resolves
`NtCreateFile` — the same call one layer down, which definitely accepts
`RootDirectory`. If `…Ex` itself is unavailable on the project's minimum Windows
build (FS-02's open question owns pinning it), the next fallback is the non-`Ex`
`FileRenameInfo` with `ReplaceIfExists = FALSE`, recorded as the taken path
rather than assumed. Do **not** fall back to `MoveFileExW`: it takes paths,
re-resolves the name, and discards the identity proven in step 2, which is the
whole point of the design.

_Spike 2 — directory rename with open descendants._ Does renaming a directory
handle fail when a descendant is open? Expected `STATUS_ACCESS_DENIED`. **Test:**
open a file inside a directory, rename the directory by handle. **If it
succeeds:** nothing changes — D6 already refuses non-empty directories, so the
case is only reachable for a directory that became non-empty after the check, and
D4 already handles that. Recorded because macOS _does_ allow it (probe: rename of
a directory with an open descendant returned 0) and a reviewer will ask.

_Spike 3 — metadata durability of the rename._ **This is FS-02 SPIKE-W1; do not
run a second copy.** There is no documented Windows equivalent of `fsync(dirfd)`,
and `FlushFileBuffers` on a volume handle requires elevation — which is why FS-01
sets `FS_DIRECTORY_BARRIER_PROVEN` to 0 on Win32 and requires callers to read a
0 return from a directory barrier as "no error", never "durable".
**Position taken, and it does not depend on the spike's outcome:** the helper
claims durability only for the journal and the preimage rows, which are ordinary
files flushed on a file handle, and does **not** claim the rename is durable at
return. On Windows `applied` therefore means observed-applied. NTFS journals
metadata, so the rename is FS-recoverable, but that is the filesystem's promise,
not ours. D10's ordering is written so that the durable record always precedes
the effect, which is what reconciliation actually needs, and FS-07 re-observes
rather than trusting a terminal journal state. The spike measures how often the
gap bites; its only permitted effect on wording is to make it more cautious.

_Spike 4 — `FILE_ID_INFO` stability on the target volume._ **This is FS-02
SPIKE-W7; do not run a second copy.** ReFS and some network redirectors report
unstable file ids, and every identity check in D3 and D4 is anchored on this.
**If unstable:** the volume is refused by `fs_volume_supported` — FS-02 D7
already restricts Win32 to local NTFS, the same fail-closed shape
`supported_root_fd` (`:358`) uses for apfs/hfs — and the Win32
`FS_IDENTITY_BINDING_BYTES` encoding changes (FS-02 D6). Nothing in FS-05
changes either way; it is listed here because FS-05's identity lane is the
loudest consumer of the answer.

### D10. Durability ordering

The existing lifecycle is `PREPARED → AUTHORIZED → COMMITTING → APPLIED |
INDETERMINATE`, each transition fsynced by `journal_store` (`:460`) and gated by
`claim_transition_allowed` (`:542`). `journal_reconcile_startup` (`:626`) turns a
surviving `COMMITTING` into `INDETERMINATE` and never replays.

`struct journal_record` (`:123`) carries no per-entry data, so today a crash mid
`COMMITTING` leaves no way to know _what_ was moved. For delete and move that is
not acceptable: an object would sit in the trash under a keyed name with
no pointer back to where it came from. FS-05 therefore requires, per entry, in
this exact order:

This is a **special case of [FS-07 D3](PRD-FS-07-crash-reconciliation.md)**, which
defines the canonical per-entry ordering for every verb. FS-05 does not define a
second one; it names which steps its verbs use:

1. `journal_transition(..., JOURNAL_COMMITTING, ...)` — existing, already fsynced.
   (FS-07 C0.)
2. `effect_log_append(row_i, phase = ARMED)` — FS-07's append-only `c2e-` log,
   durable on return, carrying the approved `source`/`destination` snapshots and
   the pinned identity. (FS-07 C1.)
3. `stage_preimage(...)` — FS-04's portable verb: `fs_rename_noreplace` into
   `<root>/.0xcopilot/trash/` under the deterministic `pre_<hex>` leaf, then the
   `journal_preimage_row` with `disposition = RETAINED` and
   `staged_before_effect = 1`, durable via `journal_store`. (FS-07 C2-C3.)
   For `DELETE` this **is** the effect; for `MOVE` there is no preimage, because
   nothing is displaced — the object moves to an approved destination that
   `RENAME_EXCL` proved absent.
4. The rename (for `MOVE`) — FS-07 C4 — then `fs_durable_barrier` (C5).
5. `fs_identity_at` post-effect read (FS-07 C6), then
   `effect_log_append(row_i, phase = OBSERVED)` (C7). After the D4 compensation
   the preimage row's disposition is updated to `RESTORED` or left `RETAINED`.
6. `journal_transition(..., JOURNAL_APPLIED, ...)` for the whole change set.
   (FS-07 C8.)

There is **no `fs_preimage_put`** and no `PENDING`/`DISPLACED` state pair: the
"has the effect been reached?" question is answered by FS-07's
`phase = ARMED | OBSERVED`, and the "what is true of the preimage?" question by
FS-04's `enum preimage_disposition`. Two records, two questions, no third shape.

A crash anywhere between 2 and 6 leaves a `COMMITTING` change-set record, an
`ARMED`-without-`OBSERVED` row for exactly one entry, and a preimage row naming
the trash leaf. That is exactly the evidence FS-07 needs, and FS-05's obligation
is to guarantee it exists, not to consume it. Because a rename **preserves the
inode**, FS-07 D3.4 can classify these two verbs _conclusively_ — the recorded
source identity is either at the original leaf or under the trash leaf, and
nothing else can put it in either place.

**Crash faults.** FS-05 takes ids **11** (`delete_pre_rename` — after the preimage
row, before the displacement) and **12** (`delete_post_rename` — after the
displacement, before the `OBSERVED` row) from the spine's ladder. It does not
reuse 5/6 (FS-04), 7/8 (FS-07) or 9/10 (FS-06).

The existing multi-entry semantics are unchanged and are correct for these verbs:
`command_commit` (`:927`) applies entries in order and breaks to `INDETERMINATE`
on the first failure, so a partially applied change set is never reported
`APPLIED`. `disjoint_entries` (`:826`) already guarantees no entry's destination
is another entry's source, so ordering within a change set carries no hidden
dependency.

### D11. Outcome vocabulary is not extended

No new `enum outcome` value. The five existing outcomes cover every case; the new
`reason` field carries the detail. This matters because `outcomeFromCode`
([native-workspace-commit-helper.ts:651](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.ts))
maps anything unrecognised to `"indeterminate"` — conservative by construction —
and adding outcomes would put pressure on every consumer to interpret them
correctly. `reason` is advisory: the TS decoder maps an unknown code to
`undefined` and **never** lets a reason influence an outcome.

Mapping:

| Situation                                   | Outcome              | Reason                              |
| ------------------------------------------- | -------------------- | ----------------------------------- |
| approved object displaced                   | `applied`            | none                                |
| foreign object displaced, restored (delete) | `precondition_drift` | `displaced_foreign_object_restored` |
| foreign object displaced, retained          | `indeterminate`      | `displaced_foreign_object_retained` |
| identity/digest/mode/size mismatch at pin   | `precondition_drift` | none                                |
| `nlink > 1` at commit                       | `precondition_drift` | `multiple_hard_links`               |
| source name vanished                        | `precondition_drift` | `source_vanished`                   |
| destination occupied                        | `precondition_drift` | `destination_occupied`              |
| target held by another process (Windows)    | `failed`             | `target_held_by_another_process`    |
| directory not empty (commit-time)           | `precondition_drift` | `directory_not_empty`               |
| trash not same-volume / unavailable         | `failed`             | `trash_unavailable`                 |
| cross-volume                                | `failed`             | `cross_volume`                      |
| leaf is a mount point                       | `precondition_drift` | `mount_point_target`                |
| rename returned an unclassified error       | `indeterminate`      | none                                |

`write_commit_result` (`:891`) currently emits a safe message only for
`INDETERMINATE`. It gains one for each `FAILED` reason above, drawn from a fixed
table in the helper. The messages name no path — the existing constraint that
physical paths never leave main is unchanged.

### D12. Above the seam

Small, and mostly already done.

- **Sensitive leaves.** `host-fs.ts` refuses to create, overwrite, delete or move
  a well-known secret file regardless of grant mode
  ([host-fs.ts:1124](../../../apps/desktop/main/capabilities/host-fs.ts),
  `isSensitiveFileName` at
  [path-validation.ts:463](../../../apps/desktop/main/capabilities/path-validation.ts)).
  The C2 authority path does **not**. Delete is the verb where that gap is worth
  most, so `#validateChangeSet`
  ([workspace-authority.ts:874](../../../apps/desktop/main/capabilities/workspace-authority.ts))
  gains a leaf check on `relativePath` and `destinationRelativePath` for `delete`
  and `move`, throwing `workspace_conflict`. Scoped to these two verbs; extending
  it to `create`/`replace` is FS-06's call.
- **Grant mode.** Unchanged. `read_write` is already required for both verbs
  (`workspace-authority.ts:866`, `effects.py:182`).
- **Approval.** Unchanged. Native confirmation already fires for every approve
  (`workspace-approval.ts:198`).
- **Reason plumbing.** `toCommitResult` (`:1040`) forwards `reason`;
  `WorkspaceJournalRecord.result` carries it; nothing new is exported to the
  renderer beyond the existing safe-message channel.

## Implementation plan

1. **`apps/desktop/native/workspace-commit-helper/src/fs_platform.h`** (FS-01's
   header) — add `fs_pin_target`, `fs_link_count`, `fs_directory_is_empty`,
   `fs_identity_at`. `enum fs_rename_result`, `fs_rename_noreplace`,
   `fs_rmdir_at` and `fs_volume_supports_rename_excl` are **already there** —
   FS-04 added them (FS-04 D12). Do not redeclare them, and do not add a second
   `FS_ABSENT`: FS-01's `enum fs_status` already has one, valued 1.
2. **`.../src/fs_platform_posix.c`** — implement the four: `openat` +
   `O_NOFOLLOW_ANY` (+`O_DIRECTORY`) for the pin; `fstat` for identity and link
   count; `openat(pinned, ".")` + `fdopendir` for emptiness (do not consume the
   pin); `fstatat` for `fs_identity_at`, `ENOENT → FS_ABSENT`.
3. **`.../src/fs_platform_win32.c`** (FS-02/FS-03's file) — implement the same
   four against the sequence in D9. Reuse the `ntdll.dll` `GetProcAddress`
   pattern from `workspace_fs.c:276`.
4. **`.../src/workspace_commit_helper.c`**
   - `struct entry` (`:88`) — add `source_pin`, `pinned_identity`, `trash_leaf`.
   - `free_entry` (`:680`) — close `source_pin`.
   - `parse_entry` (`:801`) — narrow the refusal to `REPLACE` only; add, for
     `DELETE` and `MOVE`: source-on-root-volume, empty-directory, `RENAME_EXCL`
     volume-capability and same-volume-trash checks, each mapping to a distinct
     `*failure` + reason.
   - `command_prepare` (`:845`) — open `prepared->trash` from the retained root
     handle via FS-04's `trash_open_or_create`, and assert
     `fs_identity_same_volume(trash, root)` (D5.1 — an assertion now, not a gate).
   - a `MKDIR`-from-preimage restore arm (see the note below).
   - new `commit_delete` / `commit_move` implementing D3 → D4 → D10.
   - `commit_entry` (`:752`) — new signature, dispatch `DELETE`/`MOVE`, keep
     `CREATE`/`MKDIR` byte-identical, keep `else` refusing `REPLACE`.
   - `command_commit` (`:927`) — populate `reason` in FS-04 §6a's per-entry
     block. Do **not** change the block's shape and do **not** bump `PROTOCOL`
     (FS-04 owns 3; FS-05 is downstream of it).
   - the `FAILED` safe-message table in `write_commit_result` (`:891`).

**The directory-restore obligation, stated as a blocking item.** FS-05 is the
first PRD that can put a **directory** in the trash (D6 allows deleting an empty
one). FS-04's restore is a `CREATE`-from-preimage change set
([FS-04 D6](PRD-FS-04-preimage-trash.md)) and `CREATE` cannot materialise a
directory, so without an arm here the row is `RETAINED`, offered by
`listRestorablePreimages`, and un-restorable —
`prepareLocalRestore` cannot build a change set for it. FS-04's Out of scope
assigns this to FS-05 explicitly. Two admissible resolutions, and FS-05 must pick
one before it ships:

- **(a)** add a `MKDIR`-from-preimage arm: `restore_preimage` renames the trash
  directory back, and `prepareLocalRestore` emits `operation = MKDIR` with
  `content = {kind:"preimage"}`. `fs_rename_noreplace` already handles a
  directory (probe 6 shows a whole tree moves in one call — here the tree is
  empty by construction).
- **(b)** refuse `DELETE` of a directory at `parse_entry` until (a) exists,
  narrowing D6's table by one row.

Shipping neither is the failure mode: it produces user-visible recovery items the
product cannot recover. 5. **`apps/desktop/main/capabilities/native-workspace-commit-helper.ts`** — do
**not** bump `HELPER_PROTOCOL_VERSION` (`:28`); FS-04 already moved it to 3.
Extend `decodeCommitResult` (`:634`) to read `reason` out of the per-entry
block and map unknown codes to `undefined`; add `NativeCommitReason`. 6. **`apps/desktop/main/capabilities/workspace-authority.ts`** — add `reason` to
`NativeWorkspaceEntryResult`; forward `entryResults` in `toCommitResult`
(`:1040`); add the sensitive-leaf check for `delete`/`move` in
`#validateChangeSet` (`:874`). 7. **Tests** — see below. Narrow the existing
`"fails closed for non-CAS replace/delete/move"` test
([native-workspace-commit-helper.test.ts:360](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.test.ts))
to `replace`, and add delete/move suites beside it. 8. **`docs/plan/filesystem-capability/README.md`** — the seam-sketch correction
(plain `renameat` clobbers; `MoveFileExW` discards the proven identity) is
**already folded into the spine**, citing this PRD's probe 3. Do not re-apply
it. This step is a no-op beyond flipping the capability table's
"Replace / delete / move" cells when the DoD is met.

## Test plan

Native tests follow the existing harness in
`native-workspace-commit-helper.test.ts`: `describeNative` skips off-darwin
(`:33`), `privateStore()` supplies real staging/journal fds (`:57`), and the
helper is driven end-to-end over its authenticated channel. Windows-only
assertions run behind the two-platform predicate **FS-02** turns `:33` into (its
test plan, "Enable the existing suite on Windows") on the `windows-latest` leg
FS-02 adds to `ci-desktop.yml`; FS-03 supplies the confinement half of that job,
not the guard.

### Delete — happy path and identity

- delete of a regular file: `applied`; the name is gone from the workspace; a
  file with the **same digest and inode** exists in `<root>/.0xcopilot/trash/`; the
  workspace root contains no other change.
- the helper never calls `unlinkat` against a workspace descriptor — assert by
  reading the trash after delete, and keep the source-level invariant that the
  only `unlinkat` call sites remain the journal and staging ones (`:471`, `:494`,
  `:727`).
- delete of an empty directory: `applied`, directory present in trash.
- delete where the file was modified after prepare: `precondition_drift`, file
  still present at its original path, trash empty.
- delete where the file was replaced by a **different inode with the same bytes**
  after prepare: `precondition_drift` (identity, not just digest, is checked).
- delete of a file that gained a second hard link after prepare:
  `precondition_drift` + `multiple_hard_links`; both links still present.
- delete of a non-empty directory: rejected at `prepare` with
  `workspace_conflict`; tree untouched.
- delete of a directory that became non-empty between prepare and commit:
  `precondition_drift`; directory back at its original path with its new child.
- delete where the leaf was swapped for a symlink after prepare:
  `precondition_drift`; symlink still present, target file untouched.

### Delete — the divergence lane (darwin only)

Drive it deterministically with the existing `testCrashBoundary` fd pattern
(`:119`, `:975`) extended with a `pre-rename` pause, or by an in-test
write-temp-then-rename between `prepare` and `commit` (probe 9's exact sequence).

- foreign object displaced and restored: outcome `precondition_drift`, reason
  `displaced_foreign_object_restored`; the foreign file is back at its name with
  its original inode; the trash is empty; the approved file is untouched.
- foreign object displaced, name re-taken before compensation: outcome
  `indeterminate`, reason `displaced_foreign_object_retained`; the foreign object
  is still in the trash; its preimage row is `RETAINED` and names its identity.
- exactly one compensation attempt is made — assert the trash leaf count and that
  no second rename occurs after the `EEXIST`.
- a crash injected between the preimage row and the rename (fault 11) leaves a
  `COMMITTING` change-set record, a `RETAINED` preimage row naming the trash
  leaf, and an `ARMED`-without-`OBSERVED` effect row for that entry. There is no
  `PENDING` disposition — FS-04's enum has none, and the "has the effect been
  reached?" question is FS-07's `phase`, not a disposition (see Interfaces
  consumed). Restart reconciles to `indeterminate` and performs **no** second
  mutation (mirrors `:559` and `:586`).

### Move

- move within the same directory: `applied`; source absent, destination present
  with the identical inode.
- move across directories inside the root: `applied`.
- move onto an occupied destination: rejected at `prepare`.
- move where the destination is created between prepare and commit:
  `precondition_drift` + `destination_occupied`; **the pre-existing destination
  content is byte-identical afterwards** — this is the regression test for probe
  3, and it must fail if anyone ever swaps `renameatx_np` for `renameat`.
- move where the destination is created with a **case-variant** name between
  prepare and commit: `precondition_drift`, destination content intact (probe 18).
- case-only rename `Doc.txt` → `doc.txt`: rejected at `prepare`; file untouched.
- move of a symlink: rejected at `prepare`.
- move chain `a→b` and `b→c` in one change set: rejected by `disjoint_entries`
  (`:826`) at `prepare`.
- move where the source is re-bound after prepare: `indeterminate` +
  `displaced_foreign_object_retained`; the foreign object is at the destination,
  the receipt names both identities, and **no compensating rename occurred**.

### Confinement and volume

- a `..`, absolute, backslashed or non-ASCII source or destination is rejected by
  `path_is_safe` (`:313`) at `prepare` — assert for `delete` and `move`
  specifically, both the source and the destination slot.
- a symlinked intermediate directory on the destination path is rejected
  (`open_parent` + `RENAME_NOFOLLOW_ANY`, probe 5).
- a root on a volume without `VOL_CAP_INT_RENAME_EXCL`: `prepare` fails
  `workspace_write_unsupported` for a change set containing delete or move, and
  **succeeds** for a create-only change set (the capability probe must not
  regress create).
- a trash that cannot be opened or adopted (FS-04's marker missing, mode widened,
  `.0xcopilot` replaced by a symlink): `prepare` fails `workspace_write_unsupported`
  - `trash_unavailable`, and FS-04's rule that a failing trash is **never repaired
    and never deleted** holds. The cross-volume case is not testable through the
    normal path any more (spine D4 makes the trash same-volume by construction);
    assert instead that the `fs_identity_same_volume(trash, root)` assertion exists
    and fires when the trash handle is injected from another volume in a unit test.
- a leaf that is a mount point: `prepare` fails; assert with a real second volume
  in the platform smoke suite, skipped where none is mountable.

### Claim, idempotency and journal (regression, not new behaviour)

- a delete claim replayed after the transaction is released returns
  `already_applied` and performs no second rename (mirrors `:406`).
- two helpers racing the same delete claim: exactly one `applied`, the other
  `already_applied` or `indeterminate`, and exactly one object in the trash
  (mirrors `:428`).
- a delete claim bound to a different effect is a `workspace_conflict`, not an
  idempotency shortcut (mirrors `:488`).
- a tampered preimage row fails closed like a tampered journal record (`:533`).

### Above the seam

- `#validateChangeSet` rejects a `delete` or `move` whose source or destination
  leaf is sensitive (`.env`, `id_rsa`, `*.pem`) with `workspace_conflict`.
- a `read_write_no_delete` grant still rejects both verbs
  (`workspace-authority.ts:866`) — assert it did not regress.
- `reason` survives the round trip into `WorkspaceJournalRecord.result`.
- an unknown reason code decodes to `undefined` and leaves the outcome untouched.

### Windows (the `windows-latest` leg — FS-02's, with FS-03's confinement job beside it)

- delete and move produce identical outcomes to macOS for every non-divergence
  case above.
- a file opened by another process **without** `FILE_SHARE_DELETE` yields
  `failed` + `target_held_by_another_process`, before any effect, with the file
  intact — the case macOS cannot detect (probes 7 and 16).
- the divergence-lane tests are asserted **unreachable**: the rename is by
  handle, so an external re-bind of the name between pin and rename must leave
  the approved object displaced and the foreign object untouched.

## Definition of done

- [ ] `delete` and `move` are accepted by `parse_entry` and committed by
      `commit_entry`; `replace` is still refused with the existing message.
- [ ] Both verbs are implemented once above the platform seam; neither
      `commit_delete` nor `commit_move` contains a `#ifdef`.
- [ ] macOS uses `renameatx_np(RENAME_EXCL | RENAME_NOFOLLOW_ANY)`; a test proves
      an occupied destination is left byte-identical.
- [ ] Windows renames by handle through FS-04's `fs_rename_noreplace`, whose
      Win32 body is `FileRenameInfoEx` without `FILE_RENAME_REPLACE_IF_EXISTS`
      (the non-`Ex` `FileRenameInfo` only if FS-02's minimum-Windows-version
      question forces it, recorded as the taken path); `MoveFileExW` appears
      nowhere in the tree.
- [ ] Delete never removes anything: the only `unlinkat`/`DeleteFile` call sites
      remain the private journal and staging directories.
- [ ] Every delete and move writes a durable preimage row naming the trash leaf
      **before** the rename, and a crash between the two is reconcilable.
- [ ] Every rename is followed by a post-effect identity read, and no `applied`
      is reported without it.
- [ ] Divergence on macOS resolves to restored (`precondition_drift`) or retained
      (`indeterminate`); it never reports `applied` and never loses the object.
- [ ] Cross-volume move is refused at `prepare`, never at commit, and no
      copy-based fallback exists.
- [ ] Non-empty directory delete, hard-linked delete, symlink delete/move,
      mount-point target and case-only rename are each refused with a distinct
      reason.
- [ ] `reason` is carried end-to-end into the journal record **as a field of
      FS-04 §6a's per-entry block**; an unknown code never changes an outcome;
      `grep -n 'reason' src/workspace_commit_helper.c` shows no set-level
      trailer.
- [ ] A directory preimage is restorable — either the `MKDIR`-from-preimage arm
      exists, or directory delete is refused at `parse_entry`. Shipping delete of
      a directory with no restore path is a **release blocker**, not a follow-up.
- [ ] `PROTOCOL` is **unchanged by this PR** (FS-04 owns 3); a diff touching
      `#define PROTOCOL` means the dependency order was violated.
- [ ] The trash is `<root>/.0xcopilot/trash/` and `grep -rn 'userData' ` over
      FS-05's diff shows no trash path; the reserved-segment refusal is asserted
      for `delete` and `move` sources **and** destinations.
- [ ] Crash faults 11 and 12 are used; 5-10 are untouched.
- [ ] The volume-capability probe gates delete/move only, and a create-only
      change set on the same volume still succeeds.
- [ ] `npm run test --workspace @0x-copilot/desktop` green on macOS; the FS-03
      Windows runner green for the shared cases.
- [ ] `README.md`'s seam sketch corrected for both platforms.

## Open questions

Recorded rather than guessed. None blocks starting the macOS half.

1. ~~**Multi-volume grants and the trash.**~~ **Closed** by spine D4: the trash is
   per-root at `<root>/.0xcopilot/trash/`, so two grants on two volumes have two
   trashes and neither needs to reach the other. The "one inherited trash
   descriptor" this question assumed does not exist.
   1a. **Directory restore.** Not an open question — a **blocking item** for FS-05,
   with two admissible resolutions listed in the implementation plan. Recorded
   here so it is not read as optional: FS-05 is the only PRD that can create a
   directory preimage, and no PRD can currently restore one.
2. **Windows `RootDirectory`-relative `FileRenameInfo`** — D9 spike 1. The single
   load-bearing unverified assumption of the Windows half. A one-file spike on a
   Windows runner settles it; the `NtSetInformationFile` fallback is specified.
3. ~~**Rename metadata durability on Windows.**~~ **Not an open question for
   FS-05.** It is FS-02 SPIKE-W1, the position is fixed by FS-01
   (`FS_DIRECTORY_BARRIER_PROVEN 0` on Win32) and FS-07 is already written so
   that classification never depends on the answer (FS-07 open question 6).
   FS-05's obligation is only to keep saying observed-applied, never durable.
4. **The chat surface's `overwrite` flag** (`workspaceStageProjection.ts:50`)
   describes an operation the native layer cannot express. Either it is populated
   from a non-native workspace path that this program has not audited, or it is
   dead. Worth resolving before the UI labels a stage `"move · overwrite"` for an
   effect that is structurally impossible.
5. **Sensitive-leaf coverage.** D12 adds the check for `delete`/`move` only. The
   asymmetry with `create`/`replace` on the C2 path is deliberate scoping, not a
   considered policy; FS-06 should decide the whole-path policy.
6. **Whether `move` should require `read_write` rather than
   `read_write_no_delete`.** A move confined to the root loses nothing, and the
   mode's own comment says "no delete/unlink/**move-out**"
   ([types.ts:20](../../../apps/desktop/main/capabilities/types.ts)), which a
   within-root move is not. Left unchanged here because it is a grant-policy
   change, not a helper change.

## Out of scope

- `replace` — FS-06, including the `RENAME_SWAP` spike.
- Post-crash reconciliation and user-facing recovery of a retained preimage —
  FS-07. FS-05 only guarantees the evidence exists.
- Trash retention, expiry and physical deletion — FS-04 and the retention owner.
- Overwriting move. Not representable in `WorkspaceChangeEntry`; the chat
  surface's `overwrite` flag stays unpopulated.
- Recursive / tree delete.
- Case-only rename.
- Cross-volume move in any form, including copy+verify+delete.
- Extending the sensitive-leaf refusal to `create`/`replace`.
- Any change to grant modes, the approval sheet, or the permit contract.

## Guardrails

- Do **not** use plain `renameat` / `MoveFileExW`. Probe 3 shows exactly what is
  lost.
- Do **not** implement delete as `unlinkat`, `remove`, `DeleteFileW` or
  `FileDispositionInfo`. Delete is a relocation.
- Do **not** check that a destination is absent and then rename. Exclusivity
  comes from the kernel or the verb does not ship.
- Do **not** report `applied` without the post-effect identity read.
- Do **not** retry a compensating restore, and do **not** let it replace anything.
- Do **not** add an outcome code; carry detail in `reason`, and never let a
  reason change an outcome.
- Do **not** move the trash out of `<root>/.0xcopilot/trash/` (spine D4, FS-04
  D1). An earlier version of this guardrail said the opposite; the same-volume
  property is the whole design, and it is guaranteed only inside the root.
- Do **not** address, read, list, glob, grep, or propose anything under
  `.0xcopilot` — the reserved-segment refusal is what pays for keeping the trash
  inside the grant, and delete is the verb that fills it.
- Do **not** land either verb on one platform only.
- Do **not** relax `path_is_safe`, `directory_has_exact_entry`, `open_parent`'s
  per-hop device check, or `snapshot_at`'s symlink and hard-link refusals to make
  a case work.
- Do **not** claim any Win32 behaviour in code comments or docs that the FS-03
  runner has not executed.
