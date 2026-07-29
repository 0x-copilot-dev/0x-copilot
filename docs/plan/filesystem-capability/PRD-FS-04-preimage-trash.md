# PRD-FS-04 — Preimage and trash: the recovery substrate

**Status:** specified
**Depends on:** FS-01 (platform seam + macOS moved behind it)
**Unblocks:** FS-05 (delete + move), FS-06 (replace), FS-07 (post-crash reconciliation)

## Implementer brief

Build the substrate that makes the spine's guarantee — _"Verify what was displaced.
Roll back or retain the preimage"_ ([README.md](README.md), "The guarantee, restated")
— a mechanism rather than a promise. FS-04 ships the trash, the durable preimage
record, the admission/retention/GC policy, and the restore verb. It ships **no
destructive verb**: FS-05 and FS-06 are its first producers, and they cannot land
until `stage_preimage` exists on both platforms.

Read, in order: [README.md](README.md); FS-01 for the seam's file layout and
primitive names;
[`apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c`](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)
in full — especially `journal_store_no_replace` (:482-497), `journal_reconcile_startup`
(:626-651), and `cleanup_prepared_stages` (:716-730), whose reasoning this PRD reuses
verbatim rather than reinventing.

## Context

What is true today, verified against code:

**The helper has a durable, MAC'd, fsync'd journal, and it is fixed-size and
per-preparation.** `struct journal_record` (`workspace_commit_helper.c`:123-135) holds
magic/version/state/outcome/`cleanup_complete`/`entry_count`/handle/claim/`stage_dir`/
`binding_digest`/mac. There is **no per-entry data in the durable record at all** — the
per-entry `struct entry` (:88-108) with its `relative_path`, `leaf`, retained fds and
`struct snapshot` lives only in process memory and dies with the process. Any preimage
state must therefore arrive as a new durable structure; it cannot be squeezed into
what exists.

**Writes are fsynced and rename-published.** `journal_store` (:460-474) writes a temp
file, `fsync`s it, `renameat`s it over the target name, then `fsync`s the journal
directory. `journal_store_no_replace` (:482-497) is the `O_EXCL` variant whose comment
states the exclusion argument this PRD reuses: "a deterministic claim name is shared by
independently launched helpers … `O_EXCL` gives the journal directory the only authority
to select an owner."

**Restart is conservative and refuses to infer.** `journal_reconcile_startup`
(:626-651) scans only `c2j-`/`c2c-` names (:632), and downgrades `COMMITTING →
INDETERMINATE`, `AUTHORIZED → FAILED_BEFORE_EFFECT`, `PREPARED →
FAILED_BEFORE_EFFECT`. A single unloadable record aborts the scan (:633), `main`
(:977-978) then returns 1, and the helper refuses to boot — C2 becomes unavailable
rather than guessing. FS-04 must not break that property, and must not weaken it.

**No mutation is possible today except create and mkdir.** `parse_entry` refuses every
other operation (:801) with the reason recorded at :797-800. `commit_entry` (:752-766)
implements exactly `fclonefileat` and `mkdirat`. So there is currently **nothing that
displaces bytes**, and consequently no preimage anywhere in the repository. The
`RetentionCandidateKind.PREIMAGE` enum member
([retention.py:79](../../../services/ai-backend/src/agent_runtime/surfaces_v2/retention.py))
and the lifecycle family `workspace_overlay_preimage_prepared_journal_recovery`
([`lifecycle_reference_snapshots.py`:97-99](../../../services/ai-backend/src/agent_runtime/surfaces_v2/lifecycle_reference_snapshots.py))
exist with **zero producers**. FS-04 is their first.

**The displaced object's digest is already computed.** `snapshot_at` (:400-419) digests
every regular-file precondition target into `struct snapshot.digest` (:414) and
`compute_prepared_binding` (:279-302) folds that digest into the claim binding. A
preimage's integrity anchor is therefore free: it is the precondition digest the
approval already covered.

**Confinement already proves same-volume.** `open_parent` compares `statbuf.st_dev !=
root_dev` at every hop (:392) and refuses to descend across a device boundary, so no
reachable path under a granted root can be on a different volume from the root itself.

**App data is not reliably same-volume, and the helper already fails closed on that.**
`command_prepare` refuses when the staging directory's `st_dev` differs from the root's
(:850). The private staging and journal directories live under `userData`
([workspace-production-authority.ts:26,103-106](../../../apps/desktop/main/capabilities/workspace-production-authority.ts)),
i.e. `~/Library/Application Support/...`. FS-04 must not add a _second_ same-volume
dependency on app data.

**Nothing prunes the durable journal.** `unlinkat(journal_fd, …)` appears only in the
two error paths at :471 and :494. Journal records accumulate for the lifetime of the
installation. This is pre-existing; FS-04 does not fix it (see Out of scope) but must
not make it materially worse.

**The read surface has an exclusion precedent.** `SENSITIVE_ROOT_SEGMENTS` +
`segmentIsSensitiveDir` ([path-validation.ts:301-311,429-431](../../../apps/desktop/main/capabilities/path-validation.ts))
are consumed by the glob/grep walk ([host-fs.ts:745-751](../../../apps/desktop/main/capabilities/host-fs.ts)).
Note that `HostFs.list` (:316-339) applies **no** such filter today — it enumerates raw
dirents.

**Restore has a natural home in the existing approval machinery.**
`authorizeCommitFromUserDecision` (workspace-authority.ts:602-654) binds a permit to a
server stage/revision/decision triple; `WorkspaceApprovalNativeConfirmation`
(workspace-approval.ts:34-36) is the main-owned confirmation port. Neither is reachable
from `broker.ts`'s route table (:89-93) or `ADVERTISED_METHODS` (:97-112).

## Interfaces consumed

- **FS-01's platform seam**, by its real names — the spine's sketch names
  (`open_confined`, `identity_of`, `durable_barrier`) are aliases, not APIs, and
  [FS-01 §8](PRD-FS-01-platform-seam.md) is normative. FS-04 consumes
  `fs_open_root`, `fs_open_dir_at`, `fs_open_read_at`, `fs_open_new_exclusive`,
  `fs_mkdir_at`, `fs_stat_at`, `fs_stat_handle` (for identity — there is no
  `fs_identity_of`), `fs_identity_equal`, `fs_identity_same_volume`,
  `fs_dir_for_each`, `fs_unlink_at`, `fs_durable_barrier`, `fs_dir_is_app_private`,
  `fs_close`, and the `fs_crypto.h` members. **`fs_handle` throughout, never an
  `int`.** Files are `src/fs_platform.h` / `src/fs_platform_posix.c` /
  `src/fs_platform_win32.c`.
- **Four seam members FS-04 itself adds**, on both providers, per FS-01's
  reserved-spellings list: `fs_rename_noreplace` (+ `enum fs_rename_result`),
  `fs_rmdir_at`, `fs_volume_free_bytes`, and `fs_volume_supports_rename_excl`.
  See D12 for why these are the primitives and `stage_preimage` /
  `restore_preimage` / `collect_preimage` are not.
- The existing durable journal primitives: `journal_store`, `journal_store_no_replace`,
  `journal_load`, `journal_mac`, `claim_transition_allowed`, `journal_reconcile_startup`.
- `struct snapshot` (:78-86) — `dev`, `ino`, `mode`, `size`, `kind`, `digest`.
- `NativeWorkspaceAuthority` (workspace-authority.ts:222-253) and its two
  implementations (`native-workspace-authority.ts:45,114`).
- `EncryptedWorkspaceJournalStore` (workspace-journal.ts:41) for the local, encrypted,
  non-exportable side of the record.
- The one-use permit lifecycle (`WorkspaceCommitPermit`, workspace-authority.ts:154-170).
- `RetentionCandidateKind.PREIMAGE` (retention.py:79) as the accounting sink.

## Interfaces exposed

### 1. Reserved workspace segment (TypeScript)

```ts
// apps/desktop/main/capabilities/path-validation.ts
/** Root-relative directory the workspace authority owns. Never agent-addressable. */
export const WORKSPACE_RESERVED_SEGMENT = ".0xcopilot";

/** ASCII-case-insensitive: APFS and NTFS both resolve `.0XCOPILOT` to the same dir. */
export function segmentIsReservedWorkspaceDir(name: string): boolean;
```

### 2. Change-entry content source (TypeScript)

`WorkspaceChangeEntry` (workspace-authority.ts:77-86) gains a discriminated content
source. The three existing optional fields (`contentDigest`/`contentSize`/`contentSlot`)
are folded into it; `#validateChangeSet` (:874-941) is rewritten against the union.

```ts
export type WorkspaceContentSource =
  | { readonly kind: "none" }
  | {
      readonly kind: "upload";
      readonly slot: string; // /^[A-Za-z0-9_-]{1,120}$/u
      readonly digest: string; // 64 lowercase hex
      readonly size: number;
    }
  | {
      readonly kind: "preimage";
      readonly preimageRef: string; // /^workspace-preimage:\/\/[a-f0-9]{32}$/u
      readonly digest: string; // 64 lowercase hex, MUST equal the recorded row digest
      readonly size: number;
    };

export interface WorkspaceChangeEntry {
  readonly operation: WorkspaceOperation;
  readonly relativePath: string;
  readonly destinationRelativePath?: string;
  readonly content: WorkspaceContentSource;
  readonly precondition: WorkspacePrecondition;
  /** Exactly one legal value in FS-04. The helper refuses anything else. */
  readonly preimagePolicy?: "required";
}
```

### 3. Preimage record (TypeScript, encrypted-at-rest only)

`WorkspaceJournalRecord` (workspace-authority.ts:198-215) gains one array. This is the
**only** place the plaintext restore destination is stored, and it never leaves
`EncryptedWorkspaceJournalStore`; `pathTokens` (:208) remains the exportable projection.

```ts
// The vocabulary itself is declared once, in §6. This section only USES it.
// (An earlier draft declared a second, four-member union here whose fourth
// member was `indeterminate`; §6's is the one on the wire and in the journal
// row, and it has five members including `none`.)
import type { WorkspacePreimageDisposition } from "./workspace-authority"; // §6

export interface WorkspacePreimageRow {
  readonly preimageRef: string;
  readonly entryIndex: number;
  readonly kind: "file" | "directory";
  readonly sizeBytes: number;
  readonly digest: string; // "" for a directory
  readonly displacedAt: number; // epoch ms
  readonly disposition: WorkspacePreimageDisposition;
  /** Root-relative path to restore to. Encrypted at rest; never exported, never logged. */
  readonly restorePath: string;
  readonly grantId: string;
}

export interface WorkspaceJournalRecord {
  // …existing fields unchanged…
  readonly preimages?: readonly WorkspacePreimageRow[];
}
```

`WorkspaceJournalStore` gains one query:

```ts
listRestorablePreimages(grantId: string): Promise<readonly WorkspacePreimageRow[]>;
```

### 4. Native port additions (TypeScript)

```ts
export interface WorkspaceTrashPolicy {
  readonly capBytes: number;
  readonly maxItems: number;
  readonly retainMs: number;
  readonly minRetainMs: number;
  /**
   * Main's free-space observation. The helper takes
   * MIN(this, fs_volume_free_bytes(root_handle)) — never a raw statfs, which
   * FS-01 §5 forbids in the portable translation unit. See D4.
   */
  readonly freeBytesHint: number;
  /**
   * Main-stamped wall clock (spine D5 — the helper has no clock). Every age
   * comparison below is against this number, so it is main-attested, not
   * helper-attested.
   */
  readonly nowMs: number;
}

export interface WorkspaceTrashStatus {
  readonly budgetBytes: number;
  readonly usedBytes: number;
  readonly freeBytes: number;
  readonly retainedItems: number;
  readonly eligibleBytes: number; // GC-eligible right now
  readonly eligibleItems: number;
  readonly oldestRetainedAt: number; // epoch ms, 0 when empty
  readonly admit: boolean; // for the hypothetical in the request
}

export type WorkspaceCollectOutcome =
  | "collected"
  | "skipped_leased"
  | "skipped_not_eligible"
  | "skipped_identity_drift"
  | "already_collected"
  | "indeterminate";

export interface NativeWorkspaceAuthority {
  // …existing members unchanged…
  trashStatus(
    root: string,
    policy: WorkspaceTrashPolicy,
    hypothetical: { readonly bytes: number; readonly items: number },
  ): Promise<WorkspaceTrashStatus>;

  listPreimages(
    root: string,
    opts: { readonly max: number; readonly afterRef?: string },
  ): Promise<readonly NativePreimageSummary[]>;

  collectPreimages(
    root: string,
    policy: WorkspaceTrashPolicy,
    refs: readonly string[], // ≤ 64
  ): Promise<
    readonly {
      readonly preimageRef: string;
      readonly outcome: WorkspaceCollectOutcome;
    }[]
  >;
}

/** Helper-side view. Deliberately carries NO path — main joins that from its journal. */
export interface NativePreimageSummary {
  readonly preimageRef: string;
  readonly owningClaimId: string;
  readonly kind: "file" | "directory";
  readonly sizeBytes: number;
  readonly digest: string;
  readonly displacedAt: number;
  readonly disposition: WorkspacePreimageDisposition;
}
```

`UnavailableNativeWorkspaceAuthority` (native-workspace-authority.ts:45) implements all
three by throwing; `AddonNativeWorkspaceAuthority` (:114) and the key list in
`hasNativeWorkspaceV2Bindings` (:187-198) grow correspondingly.

### 5. Local restore authorization (TypeScript)

A restore is a mutation, so it takes the same permit lifecycle — but it is **not** a
server decision and must not borrow that method's shape.

```ts
export interface WorkspaceLocalRestoreRequest {
  readonly grantId: string;
  readonly preimageRef: string;
  readonly destinationRelativePath: string;
}

export class LocalWorkspaceAuthority {
  /** Main-only. Builds the change set itself; the caller supplies no entries. */
  prepareLocalRestore(
    request: WorkspaceLocalRestoreRequest,
  ): Promise<WorkspacePreparedEffect>;

  /**
   * Mints a one-use permit for a prepared state whose change set has
   * `origin === "local_restore"` and whose single entry is a create-from-preimage.
   * It refuses every other prepared state, and `authorizeCommitFromUserDecision`
   * symmetrically refuses a `local_restore` change set.
   */
  authorizeLocalRestore(
    preparedRef: string,
    confirmation: { readonly confirmedByUser: true },
  ): Promise<WorkspaceCommitPermit>;
}
```

### 6. Helper protocol (C) — PROTOCOL 3, JOURNAL_VERSION 4

```c
#define PROTOCOL 3                    /* was 2 (workspace_commit_helper.c:45) */
#define JOURNAL_VERSION 4             /* was 3 (:56); magic string unchanged  */
#define MAX_PREIMAGE_REF_BYTES 96u
#define MAX_COLLECT_REFS 64u
#define TRASH_SEGMENT ".0xcopilot"
#define TRASH_SUBDIR  "trash"
#define TRASH_MARKER  ".workspace-trash-v1"

enum request { /* 1..12 unchanged */
  TRASH_STATUS = 13, PREIMAGE_LIST = 14, TRASH_COLLECT = 15
};

enum content_source { CONTENT_NONE = 0, CONTENT_UPLOAD = 1, CONTENT_PREIMAGE = 2 };

enum failure {   /* 1..5 unchanged (:67) */
  PREIMAGE_UNAVAILABLE = 6,   /* trash unusable, or admission refused          */
  PREIMAGE_LOCKED = 7         /* another process holds the object (Win32)      */
};

/* THE ONE preimage vocabulary. FS-05, FS-06 and FS-07 use these values on the
 * wire, in the journal row, and in TypeScript. FS-06's first draft declared a
 * parallel `enum preimage_state` whose 3 meant UNVERIFIED where this enum's 3
 * means COLLECTED — a wire conflict, not a naming preference. PREIMAGE_NONE is
 * added here so a per-entry result can say "this entry displaced nothing"
 * without a second enum. */
enum preimage_disposition {
  PREIMAGE_NONE = 0,          /* the entry displaced nothing                  */
  PREIMAGE_RETAINED = 1,      /* held in the trash, restorable                */
  PREIMAGE_RESTORED = 2,      /* put back; the restore was OBSERVED           */
  PREIMAGE_COLLECTED = 3,     /* GC'd; absence of the leaf is proof           */
  PREIMAGE_UNKNOWN = 4        /* FS-06's "unverified"; never a success claim  */
};
enum preimage_origin { PREIMAGE_HELPER_DISPLACED = 1, PREIMAGE_OS_BACKUP = 2 };
```

TypeScript mirror (`workspace-authority.ts`), likewise the only one:

```ts
export type WorkspacePreimageDisposition =
  | "none"
  | "retained"
  | "restored"
  | "collected"
  | "unknown";
```

`enum journal_state` is **unchanged**. See D7.

### 6a. The per-entry commit-result block (PROTOCOL 3)

The spine's version ladder assigns this structure to FS-04 because FS-05 and
FS-06 both need it and each drafted its own. It is **one** repeat, defined once
here, populated by whichever PRD lands. `write_commit_result` (:891-894) today
emits four set-level fields and stops.

```text
u8    outcome                 # set level, unchanged semantics
str   receipt_ref
str   result_digest           # "" when absent
str   safe_message            # "" when absent
u32   entry_result_count      # == prepared->entry_count, or 0 (see below)
  repeat entry_result_count:
    u8   entry_outcome        # enum outcome
    u32  reason               # enum commit_reason; 0 = REASON_NONE   (FS-05)
    u8   preimage_disposition # enum preimage_disposition; 0 = none   (FS-04)
    str  preimage_ref         # "" iff preimage_disposition == 0      (FS-04)
    str  displaced_digest     # "" unless the displaced object was digested
```

FS-07 appends two more bytes (`observed_state`, `evidence`) to the **same**
repeat under `PROTOCOL 4`; it does not redefine it.

`entry_result_count = 0` on every path that cannot enumerate entries —
`RECONCILE_CLAIM`, and the no-`prepared` branch of `command_commit` (:902-909).
**A reconciliation that cannot enumerate entries never fabricates them.**

`enum commit_reason` is **FS-05's** to populate (its D-Wire lists codes 1-10);
FS-04 reserves the field and emits `REASON_NONE` everywhere. Adding the field
now, rather than letting FS-05 or FS-06 add it later, is what keeps
`PROTOCOL 3` a single bump.

FS-04's own commits are `create`/`mkdir` and the restore `create`, none of which
displaces anything except the restore's consumption of a trash leaf, so FS-04
populates `preimage_disposition` and leaves the rest at zero.

```c
/* Appended after struct journal_record. entry_count rows, one per prepared entry.
 * Field order puts every 64-bit member first so there is no implicit padding.
 * memset to zero before every fill, exactly as journal_record_for does (:531). */
struct journal_preimage_row {
  uint64_t displaced_size;
  uint64_t displaced_at_ms;
  uint64_t volume_id;        /* st_dev | FILE_ID_INFO.VolumeSerialNumber        */
  uint64_t file_id_low;      /* st_ino | low 64 bits of FILE_ID_128             */
  uint64_t file_id_high;     /* 0 on POSIX; high 64 bits of FILE_ID_128 on Win  */
  uint32_t entry_index;
  uint8_t  present;          /* 0 = this entry displaced nothing                */
  uint8_t  kind;             /* 1 regular file, 2 directory                     */
  uint8_t  origin;           /* enum preimage_origin                            */
  uint8_t  disposition;      /* enum preimage_disposition                       */
  uint8_t  staged_before_effect; /* 1 iff preimage existed before target touched */
  uint8_t  reserved[3];      /* MUST be zero                                    */
  char     leaf[40];         /* "pre_" + 32 hex + NUL                           */
  char     digest[65];       /* sha256 of displaced file; "" for a directory    */
  char     post_effect_digest[65]; /* what replaced it; "" when nothing did     */
  uint8_t  tail_pad[2];      /* MUST be zero                                    */
};
_Static_assert(sizeof(struct journal_preimage_row) == 224,
               "preimage row layout must be padding-free");
```

`struct journal_record` itself is **byte-identical to today's**; v4 only appends. A
frozen copy of today's definition is preserved as `struct journal_record_v3` for the
migration in D8 and must never be edited.

## Design

### D1. The preimage lives inside the granted root, not in app data

Layout, all names root-relative and reachable from the retained root fd:

```
<grant root>/.0xcopilot/                 mode 0700 (POSIX) / owner-only DACL + HIDDEN (Win32)
  README.txt                             plaintext, human-facing, written once
  .gitignore                             "*\n", written once
  trash/
    .workspace-trash-v1                  format marker, O_EXCL at creation
    pre_<32 hex>                         the displaced object (file or directory)
    pre_<32 hex>.origin                  plaintext sidecar: root-relative path + ISO ts
```

Reasons, in order of weight:

1. **Same volume by construction.** `open_parent` already refuses to descend across a
   `st_dev` boundary (:392), so nothing reachable under the root can be on a different
   volume from `<root>/.0xcopilot`. Displacement is therefore always an O(1) rename,
   never a copy, on both platforms — and `ReplaceFileW`'s documented requirement that
   the backup be on the replaced file's volume is satisfied without a probe.
2. **App data is already known not to be same-volume-safe.** `command_prepare` fails
   closed when the staging fd's `st_dev` differs from the root's (:850). Putting the
   trash in `userData` would make every delete a cross-volume copy — O(n), able to
   half-succeed, and doubling peak space — precisely the failure mode the guarantee is
   supposed to remove.
3. **No new filesystem reach.** The trash is inside a directory the user already
   granted. Restoring reads and writes inside the same grant. C2 gains no path it did
   not already have, which is the cheapest possible answer to "did adding recovery
   widen the blast radius?"
4. **The bytes survive the app.** A `userData` wipe, a reinstall, or an uninstall
   leaves the user's own files where the user can see them.
5. **The bytes follow the folder.** Move the workspace to another disk and its recovery
   items move with it.

Accepted costs, stated plainly:

- The directory is visible to the user's own tools. Mitigated by the dot prefix, the
  `FILE_ATTRIBUTE_HIDDEN` attribute on Win32, and a `.gitignore` containing `*`.
- Cloud sync (Dropbox / OneDrive / iCloud Drive) will upload preimages. It was already
  uploading the originals, so this is a duplication and quota cost, not a new exposure
  class. Bounded by D4's budget.
- The trash sits at the root even when the grant's `allowedPathPrefixes`
  (workspace-authority.ts:842-872) narrows the agent to a subtree. This is deliberate:
  `allowedPathPrefixes` constrains **what the agent may propose**, and the trash is
  never proposable (D2). One trash per grant keeps GC, budget, and the marker check
  single-rooted.

Rejected: the OS trash (`~/.Trash`, `/Volumes/X/.Trashes/<uid>`, the Recycle Bin). It is
outside the grant root, it needs path-based high-level APIs (`NSFileManager
trashItemAtURL:`, `SHFileOperation`) that rename to avoid collisions and cannot be
driven handle-relative, and on macOS it would pull Foundation into a TCB that today
links only CommonCrypto (:25-26).

### D2. The reserved segment is never addressable and never visible

Enforced in three independent places, in the same defence-in-depth shape
`assertNativeWorkspaceCanonicalPath` already uses (workspace-authority.ts:14-16 comment):

1. **Gateway** — `#validateChangeSet` (workspace-authority.ts:874-941) rejects any entry
   whose **first** segment matches `WORKSPACE_RESERVED_SEGMENT` case-insensitively,
   for `relativePath` and `destinationRelativePath`, with
   `WorkspaceAuthorityError("workspace_conflict")`.
2. **Helper** — `parse_entry` (:792-824) rejects the same, after `path_is_safe`. The
   comparison is ASCII-case-insensitive because APFS and NTFS both resolve
   `.0XCOPILOT` to the same directory; this is the identical hazard
   `directory_has_exact_entry` (:338-346) exists for.
3. **Read surface** — `segmentIsReservedWorkspaceDir` is applied in `HostFs.#walk`
   beside the existing G2 check (host-fs.ts:745-751), in `HostFs.list`
   (:316-339, which has no filter today), and in `#resolve` so `stat`/`read` on a
   reserved path returns `permission_denied`.

Without (3), a preimage of a file the agent may no longer read (prefix narrowed,
content revised) would be readable at a second address. The trash must not become a
read-around.

### D3. The journal, not the directory, is the integrity authority

A preimage is a file inside a folder the user and every local process can write. So its
trustworthiness comes from the MAC'd durable row, not from its permissions:

- `row.digest` is `entry->source.digest`, already computed by `snapshot_at` (:414) and
  already folded into the claim binding by `compute_prepared_binding` (:279-302). No new
  hashing at commit time.
- Restore re-digests the trash object through a **retained fd** and requires equality
  with `row.digest`, plus `dev`/`ino` equality with `row.volume_id`/`row.file_id_*`,
  plus `st_nlink == 1` — mirroring `sealed_stage_matches` (:741-750) and the nlink rule
  at :406. A tampered, truncated, or cloud-sync-mangled preimage fails closed.
- GC unlinks a leaf only when it matches the helper's own `pre_<32 hex>` grammar **and**
  a MAC-valid row names it **and** the retained-fd identity matches — the same
  reasoning as `cleanup_prepared_stages` (:716-730): _"retain/quarantine it rather than
  unlinking bytes we can no longer prove we created."_ GC can therefore never delete a
  user file, even one placed inside `.0xcopilot/trash` by hand.

Directory permissions remain hygiene, not the control: the trash dir must be a
directory, not a symlink/reparse point, owned by the effective user, with
`(mode & 0077) == 0` — the portable `private_dir_handle` over
`fs_dir_is_app_private` (FS-01 §4, replacing the pre-seam `private_dir_fd` at
:429-433). A trash that fails the check is **not repaired**; the helper returns
`PREIMAGE_UNAVAILABLE`.

**Adoption rule**, in seam members — the raw `mkdirat`/`openat` spellings an
earlier draft used cannot appear in the portable translation unit (FS-01 §5, and
`check-seam.mjs` fails the build on them): `fs_mkdir_at(root, ".0xcopilot")` then
the same for `trash`, then `fs_open_dir_at`, which is symlink- and
reparse-refusing by contract on both platforms. The marker file is created with
`fs_open_new_exclusive`. If `trash/` exists **with** a valid marker → adopt. If it
exists **without** one → fail closed with `PREIMAGE_UNAVAILABLE`; never adopt a
directory we did not create, never delete it. The marker is a plaintext format stamp
carrying no secret: integrity comes from the journal (above), and binding the marker to
the installation key would make a legitimately moved folder unusable.

**Preimages are not encrypted at rest.** Encryption would cost O(n) per displacement,
destroying the rename property that makes the whole design viable, and would make the
bytes unrecoverable after an app-data wipe — defeating the purpose. The preimage is a
copy of the user's own file, in the user's own folder, next to where it lived.

### D4. Admission, budget, and retention

Policy is main-supplied per request (main is TCB; the helper only enforces arithmetic
and never invents a number):

| Knob            | Default                 | Meaning                                            |
| --------------- | ----------------------- | -------------------------------------------------- |
| `capBytes`      | 5 GiB                   | absolute ceiling on retained preimage bytes        |
| `maxItems`      | 2000                    | ceiling on retained rows; bounds GC scan cost      |
| `retainMs`      | 14 days                 | age after which a retained preimage is GC-eligible |
| `minRetainMs`   | 1 hour                  | floor that budget pressure may never evict inside  |
| `freeBytesHint` | main's `statfs` reading | see below                                          |

```
free_bytes   = MIN(policy.free_bytes_hint, fs_volume_free_bytes(root_handle))
budget_bytes = MIN(policy.cap_bytes, (free_bytes + used_bytes) / 4)
used_bytes   = Σ row.displaced_size over rows with disposition == RETAINED
admit(n_bytes, n_items) ⟺ used_bytes + n_bytes ≤ budget_bytes
                        ∧ retained_items + n_items ≤ policy.max_items
```

`fs_volume_free_bytes(fs_handle, uint64_t *out)` is a **new seam member** FS-04
adds on both providers (D12). It is not a raw `fstatfs`: `<sys/mount.h>` cannot
be included by the portable translation unit (FS-01 §5), and Win32 has no POSIX
spelling — `GetDiskFreeSpaceExW` is path-based and therefore inadmissible, so the
expected body is `NtQueryVolumeInformationFile(FileFsFullSizeInformation)` on the
retained root handle. That Win32 body is **unverified**; FS-02 SPIKE-W5 settles
it, and if it cannot be done by handle then `MIN` collapses to main's hint alone
on Windows — a real weakening that must be stated in this section rather than
absorbed, because the whole point of the `MIN` is that neither side alone can
enlarge the budget.

Taking the MIN of main's hint and the helper's own reading means a wrong number from
either side can only make the budget _smaller_. The helper deliberately does not reach
for `NSURL volumeAvailableCapacityForImportantUsageKey`, which would pull Foundation
into a TCB that today links only CommonCrypto (:25-26). See spike 4.

**`displaced_at_ms` and every age comparison are main-attested, not
helper-attested** (spine D5). The helper has no clock — there is no
`time`/`clock_gettime`/`gettimeofday` anywhere in `workspace_commit_helper.c` and
FS-01 declares no time primitive — so `now`, `displaced_at_ms` and
`freeBytesHint` all arrive from main and the helper only compares them. A
timestamp inside a MAC'd row is therefore evidence against drift and reordering,
**not** evidence against a hostile main, and D11's audit facts and FS-07's
reports must not present it as the latter.

**The helper never runs GC inside a commit.** Admission is checked after the claim is
acquired and **before** the effect. If it fails, the commit returns `FAILED` /
`PREIMAGE_UNAVAILABLE` with zero effect. Reason: GC destroys bytes, and a commit is
authorized for exactly the effect the user approved — quietly deleting _other_
recoverable items to make room is an unapproved destructive act. Instead main sweeps
(a) at authority boot, (b) whenever a `trashStatus` probe during stage preview returns
`admit == false`, so a user rarely meets the refusal.

**GC eligibility.** A row is eligible when **all** hold:

- `disposition == RETAINED`;
- the owning journal record's `state == JOURNAL_APPLIED`. A preimage belonging to an
  `INDETERMINATE` commit is never collected: we do not know whether the effect landed,
  so the preimage may be the only copy;
- `now − displaced_at_ms > minRetainMs`;
- and either `now − displaced_at_ms > retainMs` (age), or the trash is over budget and
  this is among the oldest (pressure);
- and main's own precondition: no nonterminal `EncryptedWorkspaceJournalStore` record
  (`listNonterminal`, workspace-journal.ts:74-83) references it, and it carries no
  retention hold.

Eviction order under pressure is oldest `displaced_at_ms` first.

**Two keys.** Main decides _which_ refs are eligible (age, budget, holds, nonterminal
rows) and passes ≤ 64 of them; the helper independently re-verifies every invariant
above and refuses any ref that does not satisfy them. Neither side alone can destroy a
preimage.

### D5. GC and restore never race, and they fail asymmetrically

Both take a **lease** before touching bytes, using the primitive that already exists:
a durable journal record created with `journal_store_no_replace` (:482-497) under the
name `c2p-<64 hex>` where the hex is `HMAC(journal_key, preimage_ref)` — the same
construction as `claim_journal_name` (:454-458). The journal directory is the exclusion
authority, exactly as the comment at :476-481 argues. A loser observes `EEXIST`, reads
the winner's record, and reports the winner's outcome; it never proceeds.

The lease uses the **existing** state machine and therefore inherits the existing
conservative restart for free: `PREPARED` (acquired) → `COMMITTING` (about to touch
bytes) → `APPLIED` | `INDETERMINATE`, all already permitted by
`claim_transition_allowed` (:542-552).

Failure is asymmetric, and this matters:

- **GC is self-healing.** Absence of the leaf is proof of collection. A lease stuck at
  `COMMITTING` is re-examined on the next sweep; if the leaf is gone, the row moves to
  `COLLECTED`.
- **Restore is not.** Absence of the leaf is _not_ proof of restoration — GC or another
  installation could have removed it. A restore lease that restarts at `COMMITTING`
  sets the row to `PREIMAGE_UNKNOWN`, is never retried automatically, and is surfaced
  to the user. This is the same reasoning as the `COMMITTING → INDETERMINATE` rule at
  :634-637 and the comment at :934-939.

### D6. Restore is a change set, not a new write path

Restore is expressed as an ordinary prepared change set with one entry:

```
operation      = CREATE
relativePath   = <destination, root-relative>
precondition   = { exists: false }
content        = { kind: "preimage", preimageRef, digest, size }
```

It therefore reuses `parse_entry`, `open_parent`, `snapshot_at`, `disjoint_entries`,
the claim binding, `journal_acquire_claim`, and the whole commit lifecycle unchanged.
`compute_prepared_binding` (:279-302) is extended to fold `content_source` and, when
present, `preimage_ref` — otherwise one approval could be redeemed against a different
preimage.

**Restore consumes the preimage** (rename out of the trash), on both platforms —
through the **one** primitive, `fs_rename_noreplace(trash, leaf, pin, parent,
target_leaf)` (D12). The per-provider bodies, stated here only so a reviewer can
check the mapping, are:

- POSIX: `renameatx_np(trash, leaf, parent, target_leaf, RENAME_EXCL | RENAME_NOFOLLOW_ANY)`
- Win32: `SetFileInformationByHandle(pin, FileRenameInfoEx)` with `Flags` **not**
  carrying `FILE_RENAME_REPLACE_IF_EXISTS` and `RootDirectory` = the target
  parent handle. _unverified — FS-05 D9 spike 1 settles whether
  `FileRenameInfoEx` honours a directory handle in `RootDirectory` at all;
  `NtSetInformationFile(FileRenameInformationEx)` is the named fallback, and
  `FileRenameInfo` (non-`Ex`) the fallback below that if the project's minimum
  Windows build predates `Ex` (FS-02 D9)._

`restore_preimage` is portable and calls no platform API itself.

Why consume rather than clone/copy: it is O(1) on both platforms, needs no staging
slot and no budget, cannot half-succeed, preserves mode/xattrs/ACLs/timestamps because
the inode never changes, and puts the object back where it belongs. The cost — a
preimage can be restored exactly once — is correct behaviour, not a limitation.

Rejected: `fclonefileat` on macOS + copy on Win32. It is asymmetric (macOS O(1), Windows
O(n)) and leaves a stale duplicate. Rejected: `CreateHardLinkW` /
`FileLinkInformationEx` on Win32 — a hardlink shares the inode, so a later edit to the
restored file would silently mutate the preimage.

Preconditions checked at **prepare** and re-verified at **commit** through the retained
fd (mirroring `sealed_stage_matches`, :741-750): row exists and is `RETAINED`;
`row.digest == request.digest`; `row.displaced_size == request.size`; the trash object is
a regular file, not a symlink, `st_nlink == 1`, `dev`/`ino` match the row; and its
recomputed SHA-256 equals `row.digest`. Any mismatch is `PRECONDITION_DRIFT` with zero
effect. `disjoint_entries` (:826-835) additionally rejects two entries naming the same
`preimage_ref`.

**Who may trigger it.** Main only, and only after a user action. The model cannot
propose a restore: no trash or restore route is added to `ROUTES` (broker.ts:89-93) or
`ADVERTISED_METHODS` (:97-112), and `prepareLocalRestore` marks the change set
`origin: "local_restore"`. `authorizeLocalRestore` mints a permit **only** for such a
change set, and `authorizeCommitFromUserDecision` (workspace-authority.ts:602-654)
symmetrically refuses one — so a server "approval" can never redeem a restore, and a
local confirmation can never redeem an agent proposal. `#assertPreparedLive` (:950-968)
still runs, so a revoked or expired grant makes restore impossible (the bytes stay in
the user's folder with the README).

Restore is a `create`, so it is permitted under `read_write_no_delete`
(workspace-authority.ts:865-870) — restoring is never destructive. Consuming the
preimage is not a "delete" for grant purposes: the trash is authority-owned and not
agent-addressable (D2).

### D7. Preimage state joins the journal as an appended trailer, with no new states

`entry_count` rows of `struct journal_preimage_row` are appended after
`struct journal_record`. The MAC covers `record[0 .. offsetof(journal_record, mac)) ‖
rows[0 .. count*sizeof(row))`. `record->version` sits inside the MAC'd prefix, so a v3
record can never be reinterpreted as a v4 record with zero rows — the MACs differ.

Read path in `journal_load` (:499-510):

1. read exactly `sizeof(struct journal_record)`;
2. require magic and `version ∈ {3, 4}`;
3. **v4:** require `entry_count ≤ MAX_ENTRIES` _before_ allocating (bounded at
   256 × 224 = 57,344 B — the only value trusted pre-authentication is a bounded
   length), read the trailer, then verify the MAC over prefix ‖ trailer;
4. **v3:** verify the MAC over the prefix alone and synthesize zero rows;
5. require EOF afterwards — read one more byte and require 0.

Write path always emits v4. Every row is `memset` to zero before it is filled and
`reserved`/`tail_pad` are required to be zero on load, so the MAC never covers
indeterminate padding — the same discipline `journal_record_for` (:531) already uses.

Deliberately **no new `enum journal_state` members**: preimage lifecycle lives in
`row.disposition`, and lease records reuse the existing states. Consequences that fall
out for free: `claim_transition_allowed` (:542-552) needs no change; the conservative
restart at :626-651 needs no new rules; `journal_load`'s state range check (:508-509)
stays as written.

`journal_reconcile_startup` (:626-651) grows in exactly two ways:

- it also scans the `c2p-` prefix (today: `c2j-`/`c2c-` at :632);
- when a `c2p-` lease is downgraded `COMMITTING → INDETERMINATE`, it first durably sets
  the referenced row's `disposition = PREIMAGE_UNKNOWN` on the owning record, **then**
  writes the lease. A crash between the two re-derives the same conclusion next boot
  from the still-`COMMITTING` lease, so the sequence is idempotent.

### D8. Upgrade must not brick a working install

`journal_load` today rejects any `version != JOURNAL_VERSION` (:507), a failed load
aborts the startup scan (:633), and `main` then returns 1 (:977-978). Bumping the
version naively would make every existing installation's helper refuse to boot and C2
silently become unavailable.

So: **`journal_load` accepts v3 and v4.** A v3 record is parsed through the frozen
`struct journal_record_v3` and lifted into the v4 in-memory shape with zero preimage
rows. This loses nothing: a v3 helper could only perform `create` and `mkdir` (:801),
neither of which displaces anything. Any rewrite of a lifted record stores v4. There is
no down-migration and no dual-write.

`struct journal_record_v3` must be a verbatim copy of today's definition
(:123-135) — same field order, same types, same array sizes — because the on-disk
layout is the compiler's layout for that struct. It is never edited.

### D9. Two candidate capture strategies; FS-04's contract is the same under both

Both candidate replace strategies produce a preimage at **the same place with the same
name**, so FS-04's contract is strategy-independent.

**Status, corrected by the consistency pass: FS-06 D5 has chosen Strategy B for
the primary path on both platforms**, on the confinement ground that
`ReplaceFileW` takes three path strings and discards the walked, handle-retained
subtree at the moment of the effect. Strategy A survives only as FS-06's reported
fallback if the rename information class is unavailable on the minimum supported
Windows build. FS-04 keeps both modelled — `origin` and `staged_before_effect`
are what make the journal honest about which one ran — but "the choice is still
open" is no longer true, and the two strategies below should be read as
"primary" and "fallback", not as alternatives awaiting a decision.

**Strategy A — `ReplaceFileW`.** `lpBackupFileName` points **directly into our trash**:
the preimage is created by the OS, in exactly the location our GC and restore expect,
with no copy and no second write. The backup path is composed inside the helper from
`GetFinalPathNameByHandleW(trash_handle, FILE_NAME_NORMALIZED | VOLUME_NAME_GUID)` plus
the helper-generated `pre_<32 hex>` leaf — never from a caller string. Row is written
with `origin = PREIMAGE_OS_BACKUP`, `staged_before_effect = 0`.

`ReplaceFileW` is path-based; there is no `ReplaceFileByHandle`. That is a real tension
with "no path-string mutation after authorization" (PRD-C2 guardrails). The mitigation
is a genuine Win32 property: every ancestor directory handle is held **without**
`FILE_SHARE_DELETE`, which makes those directories un-renameable and un-deletable while
we hold them, pinning the path the composed string denotes. This is the mandatory-
sharing behaviour the spine cites as the reason Windows is favourable here.

**Strategy B — handle-relative displace, then rename in.** Open the victim with `DELETE`
access and `NtSetInformationFile(FileRenameInformationEx)`, `RootDirectory` =
`trash_handle`, `Flags = FILE_RENAME_POSIX_SEMANTICS`, **no** `REPLACE_IF_EXISTS` (so a
colliding leaf fails instead of clobbering). Then rename the staged replacement into
place. Row: `origin = PREIMAGE_HELPER_DISPLACED`, `staged_before_effect = 1`.

The journal is honest about the difference: when `staged_before_effect == 0`, a crash
inside the single OS call leaves the preimage's existence unknown, so reconciliation
treats the row as `PREIMAGE_UNKNOWN` rather than assuming presence. When it is 1, the
preimage provably predates any change to the target, and FS-07 can reason from that.

`ERROR_SHARING_VIOLATION` (32) / `STATUS_SHARING_VIOLATION` maps to
`PREIMAGE_LOCKED`, distinct from `PREIMAGE_UNAVAILABLE`, so the UI can say "another
application has this file open" instead of a generic failure. macOS has no equivalent
condition — POSIX rename does not fail on an open file — so a macOS helper never emits
`PREIMAGE_LOCKED`. That is stated rather than papered over; it is a failure code, not a
verb, so the both-platforms rule is not violated.

Trash creation on Win32 goes through the **same two seam members** D3's adoption
rule names — `fs_mkdir_at` then `fs_open_dir_at` — never a create-or-open in one
call. That ordering is not cosmetic: `fs_mkdir_at` is contractually "fails if the
name exists" (FS-01 §2), which is what makes "we created it" distinguishable from
"we adopted something we did not create", and the marker check in D3 depends on
that distinction. (An earlier draft of this section specified a single
`NtCreateFile(FILE_OPEN_IF)`, which collapses the two and cannot make the
distinction.)

The Win32 bodies FS-02/FS-03 owe for those two members, as they apply here:
`FILE_CREATE` disposition for `fs_mkdir_at` and `FILE_OPEN` for
`fs_open_dir_at`, both with `FILE_DIRECTORY_FILE | FILE_OPEN_REPARSE_POINT`,
share mode `FILE_SHARE_READ | FILE_SHARE_WRITE` (never `FILE_SHARE_DELETE`), and
an explicit `SECURITY_DESCRIPTOR` granting only the caller's SID and SYSTEM.
`FILE_ATTRIBUTE_HIDDEN` on `.0xcopilot` is set by the portable trash-creation
path through a Win32-only no-op on POSIX — **unverified**: there is no seam
member for a file attribute today, so either FS-04 adds one (`fs_dir_mark_hidden`,
a no-op on POSIX) or the hidden attribute is dropped and the dot prefix carries
the whole cost. Decide before implementing; do not smuggle a `#ifdef` into the
portable file to get it (FS-01 §5).

Same-volume is proven per hop above the seam by `fs_identity_same_volume`
against the root's identity — the `st_dev` analogue — plus refusal of
`FILE_ATTRIBUTE_REPARSE_POINT` inside `fs_open_dir_at`, which FS-03 owns and
FS-04 re-asserts for the trash handle.

### D10. Deterministic preimage naming makes retry idempotent

```
preimage_id  = first 16 bytes of HMAC(journal_key,
                 "workspace-preimage-id-v1" ‖ claim ‖ be32(entry_index))
leaf         = "pre_" ‖ hex(preimage_id)                     /* 36 chars + NUL */
preimage_ref = "workspace-preimage://" ‖ hex(preimage_id)    /* 53 chars       */
lease name   = "c2p-" ‖ hex(HMAC(journal_key, preimage_ref)) /* 68 chars, fits
                                                                the char[80] that
                                                                claim_journal_name
                                                                already uses (:454) */
```

Because the leaf is a pure function of the **approved claim** and the entry index, a
repeated commit of the same claim targets the same leaf, and the no-replace displacement
collides instead of creating a second preimage. Because it is keyed by `journal_key`
(derived per installation in `deriveAuthorityMaterial`,
workspace-production-authority.ts:172-186), another local process cannot predict a name
and plant bytes ahead of us.

Two installations sharing one workspace root have different `journal_key`s
(`secrets.vaultSecret`, boot-secrets.ts:98, lives under `userData`). Installation B can
see A's leaves but can MAC-verify none of A's records, so B's GC — which requires a
MAC-valid row (D3) — never touches them, and B's restore never offers them. The trash is
shared storage; the journal is the authority. The cost is a leak: an uninstalled
installation's preimages are never collected. They are surfaced to the user as
"unrecognised recovery items" with a byte count, and never auto-deleted.

### D11. Accounting and audit

Main emits, per preimage event, a `RetentionCandidateKind.PREIMAGE` candidate
(retention.py:79) — giving that enum member its first producer — plus local audit facts
`preimage_created`, `preimage_restored`, `preimage_collected`,
`preimage_budget_refused`. Each carries the keyed path token
(`#pathToken`, workspace-authority.ts:1033-1037), sizes, counts, dispositions, and
timestamps. **Never** a plaintext path, and never the `preimageRef` itself: the ref is
desktop-local and does not cross the broker or reach the ai-backend, so no new
`LifecycleReferenceScheme` (`lifecycle_refs.py`:68-121) is added.

### D12. The preimage verbs live above the seam; only the rename lives below it

FS-01 D1 draws the seam at **primitives, never at verbs**, because a verb carries
a precondition, a journal row and an outcome vocabulary, and a per-platform verb
lets those drift. `stage_preimage` is a verb: it re-verifies identity, digest and
`nlink`, writes a MAC'd row, and maps failures onto `PREIMAGE_UNAVAILABLE` /
`PREIMAGE_LOCKED`. Exactly one line of it is platform-specific — the rename.

So FS-04 adds `fs_rename_noreplace` (plus `fs_rmdir_at`, `fs_volume_free_bytes`
and `fs_volume_supports_rename_excl`) below the seam, and writes the three verbs
once above it. Two consequences worth stating because they were wrong in the
first drafts of three PRDs:

- **`fs_rename_noreplace` is FS-04's, not FS-05's.** FS-04 lands first — FS-05
  and FS-06 cannot displace bytes without a preimage — and FS-04's own
  displacement, restore and collect are no-replace renames. Reserving the
  primitive for FS-05 would have forced FS-04 to invent a second one, which is
  the duplication the seam exists to prevent. FS-05 consumes it unchanged.
- **`fs_rmdir_at` is a gap this pass found, not a rename.** FS-01's
  `fs_unlink_at` maps to `unlinkat(dir, leaf, 0)`, which refuses a directory.
  FS-05 supports deleting an **empty** directory, which produces a
  `kind = directory` preimage row; without `fs_rmdir_at` that row can never be
  collected and the trash accumulates directories forever, under a budget that
  counts their bytes as zero.

Directory preimages also need a **restore** path, and FS-04's restore is a
`CREATE`-from-preimage change set (D6), which cannot express a directory. That
half is **not** closed here: see Out of scope, where it is assigned to FS-05 with
the reason.

## Implementation plan

FS-01 splits the helper across the platform seam; the division below is by
responsibility. Where FS-01's filenames differ, follow FS-01.

1. **`path-validation.ts`** — add `WORKSPACE_RESERVED_SEGMENT` and
   `segmentIsReservedWorkspaceDir` next to `SENSITIVE_ROOT_SEGMENTS` (:301-311) and
   `segmentIsSensitiveDir` (:429-431).
2. **`host-fs.ts`** — apply the reserved filter in `#walk` beside the G2 check
   (:745-751), in `list` (:316-339, currently unfiltered), and in `#resolve` so
   `stat`/`read` return `permission_denied`.
3. **Helper, portable half** — bump `PROTOCOL` to 3 (:45) and `JOURNAL_VERSION` to 4
   (:56). Add `struct journal_preimage_row` with its `_Static_assert`; freeze
   `struct journal_record_v3`; rewrite `journal_mac`/`journal_store`/
   `journal_store_no_replace`/`journal_load` to carry the trailer; implement the v3→v4
   lift (D8).
4. **Helper, portable half** — add the trash lifecycle, in seam members only
   (FS-01 §5 and `check-seam.mjs` reject the raw spellings an earlier draft
   used): `trash_open_or_create` (`fs_mkdir_at` → `fs_open_dir_at` →
   `private_dir_handle` over `fs_dir_is_app_private` → marker via
   `fs_open_new_exclusive`), `trash_scan` (bounded `fs_dir_for_each`,
   `pre_<32 hex>` grammar only), `trash_used_bytes`, `trash_admit` as a pure
   function of (policy, used, retained, free, hypothetical).
5. **Helper, portable half** — add the lease: `preimage_lease_acquire` /
   `_transition` / `_reconcile`, all delegating to `journal_store_no_replace` (:482-497)
   and the existing state machine. Extend `journal_reconcile_startup` (:626-651) for the
   `c2p-` prefix and the row downgrade ordering in D7.
6. **Helper, seam** — add **four primitives** to `fs_platform.h` and implement
   both sides of each (D12). None of them is a verb:

   ```c
   /* No-replace rename. The destination-absent property comes from the KERNEL,
    * never from a check-then-rename. */
   enum fs_rename_result {
     FS_RENAME_OK = 0, FS_RENAME_EXISTS, FS_RENAME_ABSENT,
     FS_RENAME_BUSY, FS_RENAME_XDEV, FS_RENAME_ERROR
   };
   enum fs_rename_result fs_rename_noreplace(
       fs_handle src_dir, const char *src_leaf,
       fs_handle pinned,                 /* authoritative where the platform
                                          * renames by handle; carried anyway */
       fs_handle dst_dir, const char *dst_leaf);

   /* Removes an EMPTY directory. fs_unlink_at (FS-01) maps to
    * unlinkat(dir, leaf, 0) and cannot. */
   enum fs_status fs_rmdir_at(fs_handle dir, const char *leaf);

   /* Available bytes on the volume behind a directory handle. D4. */
   int fs_volume_free_bytes(fs_handle dir, uint64_t *out);

   /* 1 only when the volume advertises kernel no-replace rename as valid AND
    * set. Fail closed on anything else. */
   int fs_volume_supports_rename_excl(fs_handle root);
   ```

   POSIX bodies: `renameatx_np(..., RENAME_EXCL | RENAME_NOFOLLOW_ANY)` mapping
   `EEXIST → FS_RENAME_EXISTS`, `ENOENT → FS_RENAME_ABSENT`,
   `EXDEV → FS_RENAME_XDEV`; `unlinkat(dir, leaf, AT_REMOVEDIR)`; `fstatfs`;
   `fgetattrlist(ATTR_VOL_CAPABILITIES)` for `VOL_CAP_INT_RENAME_EXCL`. Win32:
   `FileRenameInfoEx` **without** `FILE_RENAME_REPLACE_IF_EXISTS`;
   `FileDispositionInfo` on a directory handle;
   `NtQueryVolumeInformationFile(FileFsFullSizeInformation)`; and a
   volume-capability probe that is **unverified** — Win32 has no
   `VOL_CAP_INT_*` analogue, so `fs_volume_supports_rename_excl` on Windows is
   expected to return 1 for a supported NTFS volume on the strength of
   `fs_volume_supported` alone, and that is a weaker claim than the macOS probe.
   Say so; do not present the two as equivalent.

6a. **Helper, portable half** — the three preimage verbs, written **once** above
the seam and compiled for both platforms:

```c
static int stage_preimage(fs_handle parent, const char *leaf,
                          const struct snapshot *observed, fs_handle trash,
                          struct journal_preimage_row *out_row);
static int restore_preimage(fs_handle trash, const char *leaf,
                            fs_handle parent, const char *target_leaf);
static int collect_preimage(fs_handle trash, const char *leaf,
                            const struct journal_preimage_row *row);
```

Each is `fs_rename_noreplace` (or `fs_unlink_at` / `fs_rmdir_at` for collect,
chosen by `row.kind`) plus the identity, digest and `nlink` re-verification in
D3 and D6, plus `fs_durable_barrier`. `collect_preimage` for
`kind == directory` calls `fs_rmdir_at`, which is why that primitive exists;
FS-05's empty-directory delete is what produces such a row, and until FS-05
lands FS-04 emits none.

7. **Helper, protocol** — `content_source` on the prepare entry wire; extend
   `compute_prepared_binding` (:279-302) with `content_source` and `preimage_ref`;
   extend `disjoint_entries` (:826-835) to reject duplicate refs; add `TRASH_STATUS`,
   `PREIMAGE_LIST`, `TRASH_COLLECT`; add `PREIMAGE_UNAVAILABLE` / `PREIMAGE_LOCKED`;
   route CREATE-from-preimage through `commit_entry` (:752-766) using
   `restore_preimage` instead of `fclonefileat`.
8. **`native-workspace-commit-helper.ts`** — `HELPER_PROTOCOL_VERSION = 3` (:28), new
   `Request` members (:33-46), new `NativeError` members (:64-70), new codes on the
   error union (:72-84), `contentSource` in `encodeEntry` (:595-611), new
   `toHelperError` branches (:668-676), and the three new methods.
9. **`native-workspace-authority.ts`** — extend the port, the `Unavailable` stub (:45),
   the `Addon` projection (:114), and the key list in `hasNativeWorkspaceV2Bindings`
   (:187-198).
10. **`workspace-authority.ts`** — `WorkspaceContentSource` union;
    `#validateChangeSet` (:874-941) rewritten against it plus the reserved-segment
    refusal; `WorkspacePreimageRow` on the journal record; `prepareLocalRestore` and
    `authorizeLocalRestore` with the mutual refusal against
    `authorizeCommitFromUserDecision` (:602-654); two new
    `WorkspaceAuthorityError` codes (:297-311).
11. **`workspace-journal.ts`** — persist `preimages` (already encrypted + MAC'd,
    :104-135), extend `isJournalRecord` (:217-237), add `listRestorablePreimages`.
12. **NEW `workspace-trash.ts`** — main-side policy driver: boot sweep, pre-stage
    `admit` probe, eligibility computation against `listNonterminal`
    (workspace-journal.ts:74-83), batching into ≤ 64-ref `collectPreimages` calls,
    retention-candidate emission, and the restore controller that pairs
    `prepareLocalRestore` with `WorkspaceApprovalNativeConfirmation.confirmApproval`
    (workspace-approval.ts:34-36).
13. **`broker.ts`** — add nothing to `ROUTES` (:89-93) or `ADVERTISED_METHODS`
    (:97-112); add the assertion test in the Test plan that proves it.
14. **Tests** — as below.

## Test plan

Native tests follow the existing shape in
`native-workspace-commit-helper.test.ts`: spawn the real binary against real temp
directories, `describe` on darwin and `describe.skip` elsewhere (:33).

### Journal v4 and migration

- A v4 record with N rows round-trips through `journal_store`/`journal_load` with every
  field byte-identical.
- Flipping one byte anywhere in the trailer makes `journal_load` reject the record.
- Truncating the file by one row byte makes `journal_load` reject it.
- Appending one extra byte after the trailer makes `journal_load` reject it (EOF check).
- A record whose `entry_count` is `MAX_ENTRIES + 1` is rejected, and the file is a
  header only (no trailer on disk) — proving the bound is checked before the read, not
  after. Assert the helper still answers a subsequent `PING`, i.e. it refused rather
  than died.
- A record with a nonzero byte in `reserved` or `tail_pad` is rejected.
- **Migration:** write a v3 record (frozen layout, v3 MAC) into the journal dir, boot the
  helper, assert it boots, assert a subsequent `reconcileClaim` for that claim returns
  the same outcome as before the upgrade, and assert the record on disk is v4 with zero
  rows after the next transition.
- A record with `version == 5` is rejected and the helper refuses to boot (the
  fail-closed property at :633 / :977-978 is preserved, not weakened).

### Trash lifecycle

- First use creates `.0xcopilot/`, `.0xcopilot/trash/`, the marker, `README.txt`, and
  `.gitignore` containing `*`; the two directories are mode 0700.
- Second boot adopts the existing trash without recreating the marker.
- A pre-existing `.0xcopilot/trash/` **without** a marker → `PREIMAGE_UNAVAILABLE`; the
  directory is left untouched (assert its contents are unchanged byte-for-byte).
- `.0xcopilot` replaced by a symlink → `PREIMAGE_UNAVAILABLE`, link not followed.
- `chmod 0777 .0xcopilot/trash` → `PREIMAGE_UNAVAILABLE`, not silently re-chmod'd.
- `trashStatus` on a fresh root returns `usedBytes == 0`, `retainedItems == 0`,
  `oldestRetainedAt == 0`.

### Reserved segment

- A change set with `relativePath = ".0xcopilot/x"` → `workspace_conflict` at the
  gateway, and the helper independently refuses the same entry when the gateway check is
  stubbed out in the test.
- `.0XCOPILOT/x`, `.0xCopilot/x` are refused identically (case-insensitive).
- `destinationRelativePath = ".0xcopilot/x"` on a move is refused.
- `HostFs.list` on the root does not include `.0xcopilot`; `glob("**/*")` returns no
  path under it; `stat(".0xcopilot")` and `read(".0xcopilot/trash/pre_…")` both throw
  `permission_denied`.

### Admission arithmetic (pure, no destructive verb required)

- `capBytes = 1000`, `usedBytes = 900`, hypothetical 50 → `admit == true`; 150 →
  `admit == false`.
- `freeBytes` small enough that `(free+used)/4 < capBytes` → the budget is the quarter,
  not the cap.
- `freeBytesHint` larger than the helper's own `statfs` reading → the smaller wins
  (assert `status.freeBytes` equals the helper's own reading).
- `retainedItems == maxItems` → `admit == false` even when bytes fit.

### Restore (drivable today — `create` exists)

Tests plant a preimage by hand: the test owns the journal key
(`journalKey: Buffer.alloc(32, 7)`, native-workspace-commit-helper.test.ts:68), so it can
write a MAC-valid v4 record with one `RETAINED` row and drop the matching
`pre_<hex>` file into the trash.

- Happy path: prepare + commit a create-from-preimage → outcome `applied`; the
  destination has the exact preimage bytes; the trash leaf is **gone**; the row's
  disposition is `restored`; the lease record is `APPLIED`.
- Destination already exists → `precondition_drift`, zero effect, trash leaf intact.
- Row digest ≠ actual trash bytes (test corrupts one byte) → refusal at prepare, zero
  effect, leaf intact.
- Trash leaf replaced by a symlink → refusal, link not followed.
- Trash leaf has `st_nlink == 2` (test hardlinks it) → refusal.
- Requested `digest` ≠ `row.digest` → refusal (the caller cannot redirect a restore).
- Two entries in one change set naming the same `preimageRef` → prepare fails
  (`disjoint_entries`).
- A `local_restore` prepared state passed to `authorizeCommitFromUserDecision` →
  `workspace_permit_denied`; a server-decision prepared state passed to
  `authorizeLocalRestore` → `workspace_permit_denied`.
- Grant revoked between prepare and commit → `workspace_capability_denied`, leaf intact.
- **Binding:** a permit minted for preimage A cannot commit a prepared state for
  preimage B — assert `CONFLICT` from the claim-binding mismatch path (:588-589).

### GC

- Eligible row → `collected`; leaf unlinked; row disposition `collected`.
- Row whose owning record is `INDETERMINATE` → `skipped_not_eligible`, leaf intact.
- Row younger than `minRetainMs`, under budget pressure → `skipped_not_eligible`.
- Leaf's `dev`/`ino` no longer match the row (test swaps the file) →
  `skipped_identity_drift`, **leaf not unlinked**.
- A stray file in the trash that matches no row → untouched by any sweep (assert it is
  still present after a full-budget-pressure sweep).
- A stray file matching no row **and** violating the `pre_<32 hex>` grammar → untouched.
- Ref list of 65 → request refused with `INVALID`.
- Second `collectPreimages` for the same ref → `already_collected`, idempotent.
- Foreign row: rewrite a row's MAC with a different key → the helper refuses to load the
  record and never unlinks the leaf.

### Lease and crash boundaries

Extend the existing `testCrashBoundary` fault channel (fd 7,
`native-workspace-commit-helper.ts`:119-123, `workspace_commit_helper.c`:610-612,975)
with fault **5 = `preimage_lease`** and fault **6 = `preimage_effect`** — the
pair the spine's crash-fault ladder allocates to FS-04. Faults 1-4 exist today;
7/8 are FS-07's, 9/10 are FS-06's, 11/12 are FS-05's. Take the allocated pair and
update the ladder rather than reusing a number.

- Crash after lease `PREPARED`, before `COMMITTING` → restart downgrades the lease to
  `FAILED_BEFORE_EFFECT`; row stays `retained`; the preimage is still restorable.
- Crash after lease `COMMITTING`, during restore → restart sets the row to
  `unknown` (`PREIMAGE_UNKNOWN`, §6 — **not** `indeterminate`, which is a
  `WorkspaceCollectOutcome` member and not a disposition); the helper never
  re-runs the rename; `listPreimages` reports `unknown`.
- Crash after lease `COMMITTING`, during GC, leaf already gone → the next sweep
  observes absence and moves the row to `collected` (GC self-heals).
- Crash after lease `COMMITTING`, during GC, leaf still present → the next sweep
  re-acquires and completes.
- Two helpers launched against one journal dir, both restoring the same ref: exactly one
  performs the rename; the other reports the winner's outcome; the destination has one
  file.

### Non-reachability (adversarial)

- No route matching `/trash`, `/preimage`, or `/restore` exists on the broker: assert by
  enumerating `ROUTES` (broker.ts:89-93) and `ADVERTISED_METHODS` (:97-112) — a planted
  canary route in the test fixture must make the assertion fail.
- A worker holding a valid host session and a valid read capability cannot obtain a
  restore permit through any exported method.
- The `preimageRef` string appears in no broker response body, no audit record, and no
  `WorkspaceJournalRecord.pathTokens` entry — assert by scanning serialized outputs.
- `restorePath` never appears outside `EncryptedWorkspaceJournalStore`: assert the
  broker projection and audit payloads for the change set carry only path tokens.

### Cross-platform contract

Three tiers, because FS-02 and FS-03 are parallel to FS-04, not upstream of it:

1. **Platform-independent** (journal v4, migration, admission arithmetic, ref/leaf
   derivation) — runs on **both** runners from the day FS-04 lands. These are pure
   functions and file I/O against a temp dir; they need no confinement primitive.
2. **macOS end-to-end** (trash lifecycle, restore, GC, leases, crash boundaries) — runs
   on the darwin runner from the day FS-04 lands, driving the real helper binary.
3. **Windows end-to-end** — the Win32 half of the three seam primitives is written and
   compiled by FS-04, but it cannot be exercised until FS-03 has landed the Win32
   confined-open and identity primitives it calls. Until then the Windows job runs tier
   1 and asserts the Win32 seam members are **present and non-stub** by symbol, not by
   behaviour. This is stated so a green Windows job is not mistaken for a proven
   Windows path.

Once tier 3 is live, on Windows additionally:

- The parent-chain handles are held without `FILE_SHARE_DELETE`: an attempt to rename an
  ancestor while a transaction is prepared fails with a sharing violation.
- A target held open by another process with an exclusive share mode yields
  `PREIMAGE_LOCKED`, distinct from `PREIMAGE_UNAVAILABLE`, with zero effect.
- `.0xcopilot` carries `FILE_ATTRIBUTE_HIDDEN` and a DACL with no non-owner write ACE.
- A junction planted at `<root>/.0xcopilot` is refused (`FILE_OPEN_REPARSE_POINT` +
  attribute check), never traversed.

## Definition of done

- [ ] `.0xcopilot/trash/` is created, adopted, and verified handle-relative under the
      grant root on both platforms, and a trash that fails any hygiene check yields
      `PREIMAGE_UNAVAILABLE` without being repaired or deleted.
- [ ] `journal_load` accepts v3 and v4; an installation upgraded from v3 boots, and its
      pre-upgrade claims still reconcile to the same outcome.
- [ ] Preimage rows are MAC'd as part of the journal record; a one-byte mutation
      anywhere in header or trailer is rejected.
- [ ] `enum journal_state` is unchanged, and `claim_transition_allowed` and
      `journal_reconcile_startup`'s existing rules are unmodified except for the `c2p-`
      prefix and the row-downgrade ordering in D7.
- [ ] `stage_preimage`, `restore_preimage` and `collect_preimage` are **portable**
      — written once, `static`, containing no `#ifdef`, and absent from
      `fs_platform.h` (D12). `check-seam.mjs` shows no new undefined symbol from
      the portable object beyond `_fs_*`.
- [ ] `fs_rename_noreplace`, `fs_rmdir_at`, `fs_volume_free_bytes` and
      `fs_volume_supports_rename_excl` are declared in `fs_platform.h` and
      **defined by both providers** — none is a stub, a `#error`, or a
      returns-unsupported placeholder on either platform.
- [ ] `enum preimage_disposition` (with `PREIMAGE_NONE = 0`) is the only preimage
      vocabulary in the tree: `grep -rn 'enum preimage_state' src/` is empty.
- [ ] The `PROTOCOL 3` per-entry commit-result block (§6a) is on the wire with
      its `reason` field reserved, and `entry_result_count == 0` on
      `RECONCILE_CLAIM` and the no-`prepared` commit branch.
- [ ] Restore is expressed as an ordinary change set, goes through the existing
      prepare/claim/commit lifecycle, and is bound into `compute_prepared_binding`.
- [ ] A permit minted for one preimage cannot commit a restore of another.
- [ ] Restore and GC cannot both act on one preimage: the `O_EXCL` lease is proven by a
      two-helper race test.
- [ ] A crashed restore leaves the row `PREIMAGE_UNKNOWN` and is never retried
      automatically; a crashed GC self-heals from the absence of the leaf.
- [ ] GC never unlinks a leaf without a MAC-valid row, matching identity, and the
      `pre_<32 hex>` grammar — proven by the stray-file tests.
- [ ] Admission is a pure function, is enforced before the effect, and the helper never
      runs GC inside a commit.
- [ ] The reserved segment is refused by the gateway **and** independently by the
      helper, case-insensitively, and is absent from `list`, `glob`, `grep`, `stat`, and
      `read`.
- [ ] No trash, preimage, or restore route reaches `broker.ts`; the canary test proves
      the assertion is live.
- [ ] `restorePath` exists only inside `EncryptedWorkspaceJournalStore`; no plaintext
      path and no `preimageRef` appears in any audit, broker, or ledger payload.
- [ ] `RetentionCandidateKind.PREIMAGE` has a producer.
- [ ] Standard DoD: `npm run typecheck --workspace @0x-copilot/desktop`, the desktop
      suite, and the native suite pass on a darwin runner; contract tiers 1 and 2 pass;
      the Windows job runs tier 1 and the non-stub symbol assertion, and the PR text
      says plainly that the Windows path is compiled but not yet behaviourally proven.

## Out of scope

- **Any destructive verb.** `delete` and `move` are FS-05; `replace` is FS-06. FS-04
  ships the substrate and the restore verb only.
- **Directory preimages — half closed here, half assigned to FS-05, explicitly.**
  Rows model `kind = directory` and FS-04 displaces none, because it ships no
  destructive verb. Of the two missing halves:
  - **Collect** is closed by this PRD: `fs_rmdir_at` (D12) plus the `row.kind`
    branch in `collect_preimage`. FS-05 deletes only **empty** directories
    (its D6), so no tree remover is needed and the earlier "needs a bounded
    identity-checked tree remover" framing was wrong.
  - **Restore is not closed anywhere, and FS-05 must close it.** FS-04's restore
    is a `CREATE`-from-preimage change set (D6) and `CREATE` cannot materialise a
    directory. So the moment FS-05 lands, a user can delete an empty directory
    and the resulting `RETAINED` row is un-restorable — the trash holds the
    object, `listRestorablePreimages` offers it, and `prepareLocalRestore` cannot
    build a change set for it. FS-05 owns the fix (a `MKDIR`-from-preimage arm,
    or refusing directory delete until one exists) and it is a **release
    blocker for FS-05, not for FS-04**, because FS-04 alone can never produce
    such a row.
- **The replaced-file preimage is un-restorable, and FS-06 must close it.** The
  same shape, one PRD over, found by the consistency pass and not previously
  written down in either PRD. D6's restore precondition is `{ exists: false }`,
  which is exactly right for a deleted file (its name is free) and exactly wrong
  for a replaced one (its name is occupied by the replacement). So the moment
  FS-06 lands, every `RETAINED` row it produces is offered by
  `listRestorablePreimages` and refused by the commit with `PRECONDITION_DRIFT`.
  Two admissible resolutions, and FS-06 must pick one before it ships:
  **(a)** a `REPLACE`-from-preimage arm — a restore that is itself a replace,
  with its own preimage of the replacement, so the round trip is symmetric; or
  **(b)** `prepareLocalRestore` refuses a row whose `restorePath` is occupied and
  says so, and the user deletes or moves the replacement first. (a) is the
  better product answer and the larger change; (b) is honest and shippable.
  Shipping neither means the sentence "your previous version is kept" is true
  and the button next to it does not work. **Release blocker for FS-06, not for
  FS-04**, for the same reason as the directory case.
- **A user-waived preimage (`purge`).** `preimagePolicy` has exactly one legal value in
  this PRD and the helper refuses any other. A waiver is a destructive capability and
  needs its own review.
- **Post-crash reconciliation of the _target_.** FS-04 reconciles preimage and lease
  state only; deciding whether a half-applied replace should be rolled back is FS-07.
- **Pruning the durable journal.** Nothing prunes `c2j-`/`c2c-` today (only the two
  error paths at :471 and :494 unlink), and FS-04 adds `c2p-` to that set. Bounded
  journal pruning belongs to FS-07.
- **Collecting an orphaned installation's preimages.** Surfaced as a byte count, never
  auto-deleted.
- **Emptying the trash for a revoked grant.** Without a live grant there is no root fd,
  so GC cannot run; the README explains hand-deletion.
- **Restoring from an unverified `.origin` sidecar.** The sidecar is written for humans
  and is never an input to an automatic restore.
- **Any ai-backend-visible preimage reference.** No new `LifecycleReferenceScheme`.

## Guardrails

- Do **not** put the trash in app data, `~/.Trash`, or the Recycle Bin — the same-volume
  property is the whole design.
- Do **not** let the model address, read, list, glob, grep, or propose anything under
  the reserved segment.
- Do **not** trust the trash directory's permissions as an integrity control; the MAC'd
  journal row and the content digest are the control.
- Do **not** adopt or delete a `.0xcopilot` directory the helper did not create.
- Do **not** unlink any byte without a MAC-valid row, a matching retained-fd identity,
  and the helper's own name grammar.
- Do **not** run GC inside a commit; a commit is authorized for one effect, not for
  destroying other recoverable items.
- Do **not** proceed with a destructive commit when admission fails — no preimage, no
  destructive effect.
- Do **not** add a journal state, a broker route, or an `ADVERTISED_METHODS` entry for
  restore or GC.
- Do **not** let a server approval redeem a local restore, or a local confirmation
  redeem an agent proposal.
- Do **not** treat the absence of a preimage as proof that a restore happened.
- Do **not** implement `stage_preimage`, `restore_preimage`, or `collect_preimage` on
  one platform only.
- Do **not** emit a plaintext path or a `preimageRef` into audit, ledger, or any broker
  response.

## Spikes required before FS-05 / FS-06 land

These are the platform semantics this design rests on that are **not** grounded in this
repository. Each names the API, the test, and what a negative result changes.

1. **`renameatx_np(fromfd, from, tofd, to, RENAME_EXCL)` on APFS.** Test: atomicity
   under concurrent creation of the destination; `EEXIST` when the destination exists;
   behaviour when the destination is a dangling symlink; behaviour with fds obtained via
   `O_NOFOLLOW_ANY`; availability on the minimum supported macOS. **If it fails:**
   restore becomes `fclonefileat` from the preimage fd (the primitive already used at
   :759), the preimage is copied rather than consumed, and the disposition model gains a
   `restored_copy` state plus a second GC trigger.
2. **`ReplaceFileW` writing its backup into a subdirectory of the replaced file's
   directory**, while every ancestor handle is held without `FILE_SHARE_DELETE`. Test:
   does the call succeed; what is the exact error when the target is open elsewhere;
   does `REPLACEFILE_WRITE_THROUGH` provide a durability guarantee; are the replaced
   file's ACL/timestamps preserved on the backup or transferred to the replacement.
   **If it fails:** FS-06 takes Strategy B, or adopts the backup with a second rename —
   losing the "for free" property but not the contract.
3. **Directory-entry durability on Win32 — _do not run a third copy of this._**
   This is **FS-02 SPIKE-W1**, and it is the same experiment FS-05 D9 spike 3
   names. It is listed here only because FS-04's budget/GC reasoning consumes the
   answer. The position the spine has already taken is not "unknown" but "not
   provable": FS-01 sets `FS_DIRECTORY_BARRIER_PROVEN 0` on Win32 and requires
   callers to read a 0 return from a directory barrier as "no error", never as
   "durable". So the design does **not** depend on the spike's outcome — Windows
   `applied` already means observed-applied and FS-07 already re-observes. The
   spike measures how often it bites, and its only permitted effect on wording is
   to make the wording more cautious, never less.
4. **APFS free-space reporting.** `fstatfs`'s `f_bavail` can misreport because of
   purgeable and snapshot space. Test: compare against
   `volumeAvailableCapacityForImportantUsageKey` on a volume with Time Machine local
   snapshots. **If it diverges materially:** the budget denominator changes; the helper
   must not gain a Foundation dependency, so main becomes the authoritative source and
   the helper keeps only its `MIN` sanity check.
5. **Win32 inherited-ACE behaviour on same-volume move.** Moving a file within a volume
   preserves its ACL including previously-inherited ACEs, which can leave a restored file
   with permissions from the trash directory. Test: displace and restore a file in a
   directory with distinct inheritable ACEs and diff the effective DACL. **If it
   diverges:** displacement must record and re-apply the original security descriptor,
   which is new state on the row and a metadata-policy decision for PRD-C2 D7.
