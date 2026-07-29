# PRD-FS-07 — Post-crash reconciliation, both platforms

**Status:** specified
**Depends on:** FS-01 (platform seam), FS-04 (preimage + trash — FS-04 explicitly defers "post-crash reconciliation of the _target_" to this PRD, [PRD-FS-04:1018-1019](PRD-FS-04-preimage-trash.md)).
**Sequencing constraint:** FS-07 must be in the same shipping build as, or ahead of, FS-05 and FS-06. Those PRDs create the crash points enumerated in D3; landing a destructive verb without its reconciliation lane is how a half-finished commit becomes silent data loss. FS-05/FS-06 are **not** upstream dependencies — FS-07's substrate, its classification lattice and its create/mkdir lanes are implementable and testable against today's tree.

## Implementer brief

A commit that is interrupted between its durable `COMMITTING` record and its durable `APPLIED` record leaves the workspace in a state nobody has observed. Today the helper answers that honestly and bluntly: everything in that window is `INDETERMINATE`, forever, for the whole change set ([workspace_commit_helper.c:634-636](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)). That is correct and nearly useless, and once `delete`/`move`/`replace` exist it stops being merely useless — an interrupted `replace` can leave the user's file in a private trash with nothing on the main side that knows to say so.

FS-07 does three things and refuses a fourth.

1. **Narrow the unobserved window from a change set to a single entry**, by making the effect frontier durable per entry in an append-only, MAC'd evidence log.
2. **Classify what is left behind**, per entry, from durable evidence plus one fresh read-only observation — and name the cases where the honest answer is still `INDETERMINATE`.
3. **Report it**, at boot, before any new run, with a targeted refusal so a new change set cannot write over a path whose prior outcome is unknown.

It refuses to **repair**. No replay, no rollback, no auto-restore. The existing refusal at [workspace_commit_helper.c:958](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c) — _"No automatic undo after a crash or external edit: always a conflict"_ — is kept and hardened.

Read [README.md](README.md) first; D1/D2/D3 are locked and are not restated. Read [`workspace_commit_helper.c`](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c) in full, especially `journal_reconcile_startup` (:626-651) and its header comment (:624-625), whose reasoning this PRD extends rather than replaces.

## Context

Everything in this section was verified against `main@b349aca2`.

### The conservative restart decision, exactly as it stands

```c
/* At restart we make a durable conservative decision from the last fsynced
 * boundary. We never infer/replay an effect from a missing in-memory list. */
static int journal_reconcile_startup(void) { ... }
```

— [workspace_commit_helper.c:624-651](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)

Its properties, each load-bearing and each preserved by this PRD:

| Property                                                                               | Line(s)    |
| -------------------------------------------------------------------------------------- | ---------- |
| Scans only `c2j-` (preparation) and `c2c-` (claim) names; every other name skipped     | :632       |
| `COMMITTING → INDETERMINATE`, outcome `INDETERMINATE`                                  | :634-636   |
| `AUTHORIZED → FAILED_BEFORE_EFFECT`, outcome `FAILED`                                  | :637-639   |
| `PREPARED → FAILED_BEFORE_EFFECT` ("provably no effect boundary before AUTHORIZED")    | :640-646   |
| Each rewritten record is re-indexed into the in-memory claim table                     | :647-648   |
| A single MAC-invalid or version-mismatched record aborts the whole scan                | :633       |
| An aborted scan makes `main` return 1 — the helper refuses to boot                     | :977-978   |
| The scan runs **before** the request loop, so no command ever sees pre-downgrade state | :978, :979 |

The last row matters more than it looks: FS-04's GC eligibility rule requires `state == JOURNAL_APPLIED` ([PRD-FS-04:508-510](PRD-FS-04-preimage-trash.md)), and because the downgrade pass strictly precedes any `TRASH_COLLECT` request, a preimage belonging to a crashed commit is already protected by the time GC can be asked to run. FS-07 must not reorder this.

### The durable record cannot name what it changed

`struct journal_record` ([:123-135](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)) is `magic`, `version`, `state`, `outcome`, `cleanup_complete`, `entry_count`, `handle[37]`, `claim[161]`, `stage_dir[48]`, `binding_digest[65]`, `mac[32]`. It carries a **count** of entries (:129) and no entry. The per-entry `struct entry` (:88-108) — `relative_path`, `leaf`, retained parent fd, `struct snapshot source`, `sealed_stat`, `sealed_digest` — lives only in process memory and dies with the process.

Two consequences follow directly, and they are the reason this PRD exists:

- **The helper cannot re-observe the target after a restart.** It has no root path, no root identity, and no relative path. `compute_prepared_binding` folds root `dev`/`ino` (:284-285) into `binding_digest`, but a digest is not a handle. Any design in which the helper alone resolves a crashed commit is fiction.
- **The frontier is unknown.** `command_commit` applies entries in order and breaks on the first failure (:927), but nothing durable records how far it got. One interrupted entry therefore condemns all `entry_count` entries to `INDETERMINATE`.

### `stage_dir` and the stage names are derivable — this is the one durable per-entry hook that exists

`journal_record.stage_dir` (:132) is filled from `prepared->stage_dir` (:538), which is the per-process staging run directory name `c2-<32 hex>` created at boot (:441-443). `create_stage` names each entry's staged object deterministically:

```c
snprintf(entry->stage_name, sizeof entry->stage_name, "s-%s-%u",
         prepared->handle + 4, index);
```

— [:705-706](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)

So from a durable record alone, a later helper can name and open every staged object of a crashed preparation: `openat(staging_parent_fd, record.stage_dir, …)` then `s-<record.handle+4>-<i>`. This is exactly the hook FS-06's `RENAME_SWAP` design needs — after a successful swap that name holds the **displaced original** ([PRD-FS-06:370-382](PRD-FS-06-replace.md)).

### Nothing on the main side reconciles anything at boot

Traced through the whole tree:

- `LocalWorkspaceAuthority.reconcileCommit` ([workspace-authority.ts:719-730](../../../apps/desktop/main/capabilities/workspace-authority.ts)) is called from exactly one place: [broker.ts:765](../../../apps/desktop/main/capabilities/broker.ts), inside `#handleWorkspaceClaimRoute` (:746-769), reachable only over `/internal/workspace/v2/claims/{claim}/reconcile` ([broker.ts:1125-1131](../../../apps/desktop/main/capabilities/broker.ts)).
- That route is driven by the ai-backend: `workspace_reconcile` ([broker_client.py:672-679](../../../services/ai-backend/src/agent_runtime/capabilities/desktop/broker_client.py)) ← `WorkspaceAuthorityClient.reconcile` ([workspace_authority.py:306-307](../../../services/ai-backend/src/agent_runtime/capabilities/desktop/workspace_authority.py)) ← `WorkspaceEffectExecutor.reconcile` (:388-391) ← `EffectCoordinator.reconcile` ([coordinator.py:441-489](../../../services/ai-backend/src/agent_runtime/effects/coordinator.py)) ← `RuntimeEffectReconcileCommand` ← `RepairReconciliationExecutor` ([repair_execution.py:61-120](../../../services/ai-backend/src/runtime_worker/jobs/repair_execution.py)), which is gated by `REPAIR_EXECUTION_ENABLED` and **off by default** ([repair_execution.py:38-48](../../../services/ai-backend/src/runtime_worker/jobs/repair_execution.py)).
- `main/index.ts:380` constructs the authority ([workspace-production-authority.ts:80-170](../../../apps/desktop/main/capabilities/workspace-production-authority.ts)) and nothing sweeps the journal afterwards.

So today: the helper downgrades its own records at every boot; **main never learns**, and the user is never told. A crashed change set sits in `EncryptedWorkspaceJournalStore` as `committing` or `indeterminate` until a server-side repair job that is off by default happens to ask about it.

### Verified defect — main durably records a provably-failed commit as `applied`

```ts
const terminal: WorkspaceJournalState =
  result.outcome === "indeterminate" ? "indeterminate" : "applied";
```

— [workspace-authority.ts:726-727](../../../apps/desktop/main/capabilities/workspace-authority.ts) (`reconcileCommit`), and the identical shape at [:690-691](../../../apps/desktop/main/capabilities/workspace-authority.ts) (`commitPreparedChangeSet`).

Trace it: a crashed `AUTHORIZED` record is downgraded to `FAILED_BEFORE_EFFECT` with outcome `FAILED` (:637-639); `journal_outcome_for` returns `FAILED` (:616-622); `command_reconcile_claim` writes it out (:949); `outcomeFromCode` maps `4 → "failed"` ([native-workspace-commit-helper.ts:661-662](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.ts)); `reconcileCommit` stores journal state `"applied"`. `listNonterminal` treats `"applied"` as terminal ([workspace-journal.ts:74-83](../../../apps/desktop/main/capabilities/workspace-journal.ts), [workspace-authority.ts:286-294](../../../apps/desktop/main/capabilities/workspace-authority.ts)), so the record is never revisited. `precondition_drift` takes the same path.

This is the spine's forbidden move in the direction nobody watches for: a change the helper **proved did not happen** is durably recorded as having happened. FS-07 fixes it.

### The recovery API is dead, and its terminal state is a trap

- `command_abort_or_recovery`'s recovery branch always writes `0` (:958), and `decodeRecovery` maps `1 → "proposed"` ([native-workspace-commit-helper.ts:644-649](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.ts)). So `proposeRecovery` **structurally cannot** return `"proposed"`; `recovery_proposed` is unreachable.
- `LocalWorkspaceAuthority.proposeRecovery` (:742-752) and `proposeRecoveryForClaim` (:754-770) have **zero callers** anywhere in the repo (verified by grep across `.ts`/`.tsx`/`.py`, excluding tests and the port declarations).
- `proposeRecoveryForClaim` writes state `"recovery_conflict"`, which **is** in the terminal-exclusion list of both `listNonterminal` implementations ([workspace-journal.ts:81](../../../apps/desktop/main/capabilities/workspace-journal.ts), [workspace-authority.ts:292](../../../apps/desktop/main/capabilities/workspace-authority.ts)). Calling it on an indeterminate record would therefore **retire an unresolved change without resolving it**, guaranteed, since the helper always answers conflict.
- `"rolled_back"` ([workspace-authority.ts:194](../../../apps/desktop/main/capabilities/workspace-authority.ts)) has no producer at all.

### `PreparedState` does not survive a restart

`#prepared` is an in-memory `Map` ([workspace-authority.ts:361](../../../apps/desktop/main/capabilities/workspace-authority.ts)), and `#requirePrepared` (:943-948) throws `workspace_prepared_not_found` for anything it does not hold. After an Electron restart, `commitPreparedChangeSet`, `abortPreparedChangeSet`, `proposeRecovery(preparedRef)` and `reconcilePrepared` are all unreachable for pre-restart work. Only claim-keyed paths survive. (`NativeWorkspaceAuthority.reconcilePrepared` — [workspace-authority.ts:242-245](../../../apps/desktop/main/capabilities/workspace-authority.ts), wired to `Request.ReconcilePrepared = 6` — has no caller in `main/` outside tests either.)

### `WorkspaceJournalRecord` cannot address the workspace

Fields ([workspace-authority.ts:198-215](../../../apps/desktop/main/capabilities/workspace-authority.ts)): `preparedRef`, `state`, `runId`, `userId`, `deviceId`, `stageId`, `revision`, `decisionLedgerId`, `claimId?`, `pathTokens`, three digests, `createdAt`, `updatedAt`, `result?`. There is **no `grantId`** (it exists on `WorkspaceChangeSet` at :97 and is dropped by `#journalRecord` at :989-1018) and **no path** — `pathTokens` are one-way HMACs (:1033-1037). Main therefore cannot currently re-resolve the root or the targets of a crashed record either.

`isJournalRecord` validates `state` only as `typeof record.state === "string"` ([workspace-journal.ts:222](../../../apps/desktop/main/capabilities/workspace-journal.ts)), so an unrecognised persisted state loads and falls into the **nonterminal** bucket. That happens to be the fail-safe direction and FS-07 keeps it.

### The evidence-free growth already in the tree

- Journal records are never pruned. `unlinkat(journal_fd, …)` appears only at :471 and :494, both error paths of a failed store.
- Staging run directories are never removed. There is **no `rmdir` anywhere in the helper**; `make_private_run_dir` creates one `c2-<32 hex>` per launch (:443) and `cleanup_prepared_stages` unlinks only files inside it (:727). Every helper launch leaks one directory.

FS-04 explicitly hands bounded pruning here ([PRD-FS-04:1020-1022](PRD-FS-04-preimage-trash.md)).

### The helper has no clock — and by spine D5 it never gets one

There is no `time()`, `clock_gettime`, `gettimeofday` or equivalent anywhere in `workspace_commit_helper.c`, and `fs_platform.h` ([PRD-FS-01 §2](PRD-FS-01-platform-seam.md)) declares no time primitive and reserves none. Spine **D5** settles what follows: main stamps every wall-clock value (FS-04's `displaced_at_ms`, the retention ages) and the helper only _compares_ main-supplied numbers. So FS-04's age-based GC arithmetic rests on a main-supplied input, not on a missing seam member — and every such timestamp is **main-attested, not helper-attested**, which is evidence against drift and reordering but not against a hostile main. **FS-07 adds no time-based rule and no clock dependency**, and none of its classification depends on a timestamp.

### Fault injection already exists and is the right harness

`test_crash_boundary` (:148) fires at `PREPARED`/`AUTHORIZED`/`COMMITTING` (:610-612) and after the effect loop before `JOURNAL_APPLIED` (:931), delivered on private fd 7 ([native-workspace-commit-helper.ts:119-123,194-234,685-698](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.ts)). Two existing tests pin the current semantics: `"marks a crash after durable committing as indeterminate and never replays it"` ([native-workspace-commit-helper.test.ts:559-583](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.test.ts)) and `"records effect-boundary loss as indeterminate without a second mutation on restart"` (:585-616) — the latter asserting the file **is** on disk while the outcome **is** `indeterminate`. FS-07 keeps both outcomes and adds detail beside them.

### The reporting surfaces that already exist

- `projectReceiptV2` models `"indeterminate"` as a first-class effect status ([projectReceiptV2.ts:66,724-726](../../../packages/chat-surface/src/destinations/run/projectReceiptV2.ts)) and raises `effects_indeterminate` for unresolved stages (:489-497). It also already consumes an `effect.reconciled` ledger event (:95, projected at :248), which the coordinator emits (`record_reconciled`, [coordinator.py:487](../../../services/ai-backend/src/agent_runtime/effects/coordinator.py)).
- FS-09 wires the user-facing "Recheck" action to this PRD ([PRD-FS-09:431](PRD-FS-09-enablement-consent.md)) and forbids collapsing `indeterminate` into success or failure anywhere (:632).
- `CAPABILITY_CHANNELS` ([channels.ts:15-24](../../../apps/desktop/main/capabilities/channels.ts)) declares four channels; handlers at [ipc/handlers.ts:413-460](../../../apps/desktop/main/ipc/handlers.ts).

## Interfaces consumed

- **FS-01 seam members only**, all already declared: `fs_open_root`, `fs_open_dir_at`, `fs_open_read_at`, `fs_open_new_exclusive`, `fs_stat_at`, `fs_stat_handle`, `fs_identity_equal`, `fs_identity_binding`, `fs_dir_for_each`, `fs_read_exact`, `fs_write_all`, `fs_durable_barrier`, `fs_unlink_at`, `fs_close`. **FS-07 adds no seam member** (D2).
- **Existing helper internals**: `journal_load`/`journal_store`/`journal_store_no_replace` (:460-510), `claim_journal_name` (:454-458), `journal_lookup_claim` (:656-678), `journal_outcome_for` (:616-622), `claim_transition_allowed` (:542-552), `journal_reconcile_startup` (:626-651), `compute_prepared_binding` (:279-302), `binding_snapshot` (:269-277), `open_root`/`open_parent` (:365-398), `snapshot_at` (:400-419), `regular_digest_fd` (:304-311), `path_is_safe` (:313-332), `directory_has_exact_entry` (:338-346), `create_stage` naming (:705-706), `cleanup_prepared_stages` (:716-730).
- **FS-04**: `struct journal_preimage_row` (its `leaf`, `digest`, `volume_id`, `file_id_low/high`, `present`, `staged_before_effect`, `disposition`), the `c2p-` lease and its restart rule ([PRD-FS-04 D5, D7](PRD-FS-04-preimage-trash.md)), `PREIMAGE_*` dispositions, `NativePreimageSummary`, and `NativeWorkspaceAuthority.listPreimages`. FS-07 **reads** all of this and writes none of it except the disposition transitions D4 specifies.
- **FS-04** (not FS-06) for the wire: the `PROTOCOL 3` per-entry commit-result block is defined once in [FS-04 §6a](PRD-FS-04-preimage-trash.md), and `enum preimage_disposition` is the one preimage vocabulary. FS-06's draft `enum preimage_state` is retired — its `3 = UNVERIFIED` collided with FS-04's `3 = COLLECTED`. FS-07 appends two bytes to the block; it does not redefine it.
- **Main**: `LocalWorkspaceAuthority`, `WorkspaceJournalStore`/`EncryptedWorkspaceJournalStore`, `GrantProvider`, `NativeWorkspaceAuthority`, `WorkspaceAuthorityError` codes (:297-311), `#pathToken` (:1033-1037).

## Interfaces exposed

### 1. Helper protocol — `PROTOCOL 4`, one new request, no journal-version change

```c
#define PROTOCOL 4                 /* was 3 after FS-04; see the coupling note */

enum request {          /* 1..12 unchanged; 13..15 are FS-04's                */
  RECONCILE_OBSERVE = 16
};

enum effect_phase {
  EFFECT_PENDING  = 0,   /* row absent: the entry was never reached           */
  EFFECT_ARMED    = 1,   /* fsynced immediately before the effect syscall     */
  EFFECT_OBSERVED = 2,   /* fsynced after the helper read back the result     */
  EFFECT_SKIPPED  = 3    /* the loop reached the entry and refused it         */
};

enum observed_state {
  OBSERVED_UNKNOWN            = 0,
  OBSERVED_APPROVED_END_STATE = 1,  /* the workspace matches the approved post-state */
  OBSERVED_PRE_STATE_INTACT   = 2,  /* the workspace matches the approved pre-state  */
  OBSERVED_DIVERGENT          = 3,  /* it matches neither                            */
  OBSERVED_UNOBSERVABLE       = 4   /* nothing could be read (root/grant gone)       */
};

enum reconcile_evidence {
  EVIDENCE_NONE                 = 0,
  EVIDENCE_NOT_ARMED            = 1, /* no armed row: provably no effect            */
  EVIDENCE_OBSERVED             = 2, /* the helper's own post-effect read is durable */
  EVIDENCE_IDENTITY_APPLIED     = 3, /* the approved object is at the approved place */
  EVIDENCE_IDENTITY_NOT_APPLIED = 4, /* the approved object is still where it began  */
  EVIDENCE_END_STATE_PRESENT    = 5, /* post-state matches; causation unproven       */
  EVIDENCE_PRE_STATE_INTACT     = 6, /* pre-state matches; causation unproven        */
  EVIDENCE_DIVERGENT            = 7, /* neither state matches                        */
  EVIDENCE_ROOT_UNAVAILABLE     = 8,
  EVIDENCE_RECORD_UNREADABLE    = 9
};
```

**Version coupling.** `PROTOCOL` ([:45](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)) and `HELPER_PROTOCOL_VERSION` ([native-workspace-commit-helper.ts:28](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.ts)) move together in one commit; the helper is resolved from the same packaged artifact as main ([:482-500](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.ts)) so there is no mixed-version window. The ladder is **fixed in the spine**, not first-come: `PROTOCOL 3` is FS-04's (with requests 13-15 and the per-entry block), `PROTOCOL 4` is FS-07's (with `RECONCILE_OBSERVE = 16` and two more bytes on the block). FS-07's sequencing constraint is against FS-05/FS-06, not FS-04, and FS-04's substrate is an upstream dependency of this PRD, so the "if FS-07 lands first" branch in an earlier draft was unreachable and is removed. Request ids `9` and `10` are freed by D9 and are **not** reused.

**`JOURNAL_VERSION` is not bumped.** `struct journal_record` and FS-04's appended `journal_preimage_row` trailer are untouched (D5).

#### `RECONCILE_OBSERVE` request body

```text
str  claim_id                              # <= MAX_CLAIM_BYTES
str  root_path                             # absolute; main resolves it from the grant
u32  entry_count                           # MUST equal the durable record's entry_count
repeat entry_count, in the approved order:
  u8   operation                           # enum operation
  str  relative_path
  u8   has_destination
  str  destination_relative_path           # present iff has_destination
  u8   has_content
  str  slot                                # present iff has_content
  str  expected_digest                     # present iff has_content
  u64  expected_size                       # present iff has_content
```

The request deliberately carries **no observed state** — no `exists`, no `kind`, no precondition digest, no identity. Those come from the helper's own durable rows. See D6.

#### `RECONCILE_OBSERVE` response body (and the same trailing block on `COMMIT`)

Extends **FS-04 §6a's** `PROTOCOL 3` per-entry block — the single block FS-04
defines, FS-05 populates `reason` in and FS-06 populates the preimage fields in —
with two bytes:

```text
u8    outcome                     # set level
str   receipt_ref
str   result_digest               # "" here
str   safe_message
u32   entry_result_count          # entry_count, or 0 when nothing could be observed
  repeat entry_result_count:
    u8   entry_outcome            # APPLIED|ALREADY_APPLIED|PRECONDITION_DRIFT|FAILED|INDETERMINATE
    u32  reason                   # FS-05's enum commit_reason; 0 here
    u8   preimage_disposition     # FS-04's enum; 0 = PREIMAGE_NONE
    str  preimage_ref             # "" iff preimage_disposition == 0
    str  displaced_digest         # ""
    u8   observed_state           # NEW — enum observed_state
    u8   evidence                 # NEW — enum reconcile_evidence
```

`entry_result_count = 0` on every read-only path that cannot enumerate entries — `RECONCILE_CLAIM`, the no-`prepared` branch of `command_commit` (:902-909), and a `RECONCILE_OBSERVE` that could not open the root. **A reconciliation that cannot enumerate entries never fabricates them** (FS-06's rule, kept).

### 2. Helper — the append-only effect evidence log

One file per preparation, named `c2e-<32 hex>` where the hex is `record.handle + 4` (i.e. the same suffix `c2j-` uses, :856). Created lazily with `fs_open_new_exclusive` on the first armed row.

```c
#define EFFECT_LOG_PREFIX "c2e-"
#define EFFECT_ROW_MAGIC  0x43324501u          /* 'C','2','E',0x01 */
#define MAX_EFFECT_ROWS   MAX_ENTRIES          /* 256 (:50) */

/* Padding-free by construction: every 64-bit member first, then 32-bit, then
 * bytes, then char arrays, then explicit tail padding. memset to zero before
 * every fill, exactly as journal_record_for does (:531). */
struct journal_snapshot {                      /* mirrors struct snapshot (:78-86) */
  uint64_t volume_id;                          /* fs_identity.volume               */
  uint64_t file_id_low;
  uint64_t file_id_high;                       /* 0 on POSIX                       */
  uint64_t mode_bits;
  uint64_t size;
  uint8_t  exists;
  uint8_t  kind;                               /* wire values 0/1/2                */
  uint8_t  reserved[6];                        /* MUST be zero                     */
  char     digest[65];                         /* "" for a directory or absence    */
  uint8_t  tail_pad[7];                        /* MUST be zero                     */
};
_Static_assert(sizeof(struct journal_snapshot) == 120, "on-disk layout");

struct effect_row {
  uint64_t post_volume_id;                     /* identity at the target after the effect */
  uint64_t post_file_id_low;
  uint64_t post_file_id_high;
  uint64_t post_size;
  uint64_t stage_volume_id;                    /* sealed stage identity (create/replace)  */
  uint64_t stage_file_id_low;
  uint64_t stage_file_id_high;
  uint64_t stage_size;
  struct journal_snapshot source;              /* the approved precondition, as observed  */
  struct journal_snapshot destination;         /* zeroed unless has_destination           */
  uint32_t magic;                              /* EFFECT_ROW_MAGIC                        */
  uint32_t sequence;                           /* 0-based append index, strictly +1       */
  uint32_t entry_index;
  uint8_t  operation;                          /* enum operation                          */
  uint8_t  phase;                              /* enum effect_phase                       */
  uint8_t  post_kind;                          /* enum fs_kind observed after the effect  */
  uint8_t  conclusive;                         /* 1 iff this verb's evidence is identity-conclusive */
  uint8_t  has_destination;
  uint8_t  reserved[3];                        /* MUST be zero                            */
  char     handle[37];                         /* binds the row to its preparation        */
  char     post_digest[65];                    /* "" when not a regular file / not read   */
  char     stage_digest[65];                   /* the sealed digest; "" when no stage     */
  char     preimage_leaf[40];                  /* FS-04's pre_<32 hex>; "" when none      */
  uint8_t  tail_pad[5];                        /* MUST be zero                            */
  uint8_t  mac[MAC_BYTES];
};
_Static_assert(sizeof(struct effect_row) == 568, "on-disk layout");
_Static_assert(offsetof(struct effect_row, mac) == 536, "MAC input length is on-disk");
```

The two `_Static_assert` values are derived by hand from the declaration; **the compiler is the authority** — if it disagrees, use its numbers and note them in the PR, exactly as [PRD-FS-01 §D5](PRD-FS-01-platform-seam.md) requires. Do not "fix" a mismatch by reordering fields.

`row.mac = HMAC(journal_key, row[0 .. offsetof(mac)))`. Because `handle`, `sequence` and `entry_index` are inside the MAC'd prefix, a row cannot be moved between logs, reordered, or duplicated undetected.

Reader contract:

```c
/* Reads rows at fixed strides from offset 0. Stops at the first row that is
 * short, has the wrong magic, has a MAC mismatch, has sequence != expected,
 * or has a nonzero reserved/tail_pad byte. Everything at and after that point
 * is ignored: a torn tail is the only tear an append can produce.
 * Returns the number of accepted rows, or -1 if the file exists but could not
 * be opened or read at all. */
static int effect_log_load(const char *handle, struct effect_row *out,
                           uint32_t maximum);

/* Appends one row and makes it durable before returning. 1 on success. */
static int effect_log_append(struct prepared *prepared, struct effect_row *row);
```

### 3. Main — reconciliation types

```ts
// workspace-authority.ts

export type WorkspaceReconciliationSource =
  | "boot_sweep"
  | "user_recheck"
  | "server_repair";

export type WorkspaceObservedState =
  | "unknown"
  | "approved_end_state"
  | "pre_state_intact"
  | "divergent"
  | "unobservable";

export type WorkspaceReconciliationEvidence =
  | "not_armed"
  | "observed"
  | "identity_applied"
  | "identity_not_applied"
  | "end_state_present"
  | "pre_state_intact"
  | "divergent"
  | "root_unavailable"
  | "record_unreadable";

export interface WorkspaceEntryReconciliation {
  readonly entryIndex: number;
  readonly operation: WorkspaceOperation;
  /** Keyed token from #pathToken (:1033-1037). Never a plaintext path. */
  readonly pathToken: string;
  readonly outcome: WorkspaceCommitOutcome;
  readonly observedState: WorkspaceObservedState;
  readonly evidence: WorkspaceReconciliationEvidence;
  /** A MAC-valid RETAINED FS-04 row whose trash object still verifies. */
  readonly preimageAvailable: boolean;
}

export interface WorkspaceReconciliationReport {
  readonly preparedRef: string;
  readonly claimId: string;
  readonly source: WorkspaceReconciliationSource;
  readonly reconciledAt: number;
  /** Set-level. Never stronger than the weakest entry. */
  readonly outcome: WorkspaceCommitOutcome;
  readonly observedState: WorkspaceObservedState;
  readonly entries: readonly WorkspaceEntryReconciliation[];
  readonly indeterminateEntries: number;
  /** Entries that are not applied and whose previous version is restorable. */
  readonly recoverableEntries: number;
  readonly safeMessage?: string;
}
```

`WorkspaceJournalState` changes:

```ts
export type WorkspaceJournalState =
  | "prepared"
  | "authorized"
  | "committing"
  | "applied"
  | "failed_before_effect"
  | "indeterminate"
  | "acknowledged_indeterminate" // NEW — retired by an explicit user action only
  | "rolled_back"; // unchanged; FS-04's local restore is its producer
// REMOVED: "recovery_proposed", "recovery_conflict" (D9)
```

`WorkspaceJournalRecord` gains three fields. The first two are **encrypted-at-rest only**, following FS-04's precedent for `restorePath` ([PRD-FS-04 §3](PRD-FS-04-preimage-trash.md)): they live inside `EncryptedWorkspaceJournalStore` and never reach the broker, an audit row, or a renderer projection.

```ts
export interface WorkspaceJournalRecord {
  // …existing fields unchanged…
  /** Which grant backed this change set. Needed to re-resolve the root. */
  readonly grantId: string;
  /**
   * The approved entry descriptors, verbatim from the change set. Encrypted at
   * rest; never exported, never logged. `pathTokens` (:208) remains the
   * exportable projection.
   */
  readonly changeSpec?: readonly WorkspaceChangeEntry[];
  readonly reconciliation?: {
    readonly attempts: number;
    readonly lastAt: number;
    readonly lastSource: WorkspaceReconciliationSource;
    readonly entries: readonly WorkspaceEntryReconciliation[];
  };
}
```

`WorkspaceJournalStore` gains one query:

```ts
listUnresolved(): Promise<readonly WorkspaceJournalRecord[]>;
```

— records whose state is `"committing"` or `"indeterminate"`, or whose state string is not a recognised member of the union (the fail-safe bucket, D10).

### 4. Main — the reconciler

New file `apps/desktop/main/capabilities/workspace-reconciler.ts`.

```ts
export interface WorkspaceReconcilerConfig {
  readonly authority: LocalWorkspaceAuthority;
  readonly grants: GrantProvider;
  readonly journal: WorkspaceJournalStore;
  readonly native: NativeWorkspaceAuthority;
  readonly now?: () => number;
  /** Bounds boot cost. Default 64; the remainder is swept next boot. */
  readonly maxRecordsPerSweep?: number;
  readonly audit?: (fact: WorkspaceReconciliationAuditFact) => void;
}

export class WorkspaceReconciler {
  /**
   * Runs once per boot, before the capability broker accepts a run. Never
   * throws: an unreachable helper, a revoked grant, or a decode failure leaves
   * the record unresolved rather than resolving it optimistically.
   */
  sweep(): Promise<readonly WorkspaceReconciliationReport[]>;

  /** The FS-09 "Recheck" action and the server repair lane both land here. */
  recheck(
    claimId: string,
    source: "user_recheck" | "server_repair",
  ): Promise<WorkspaceReconciliationReport>;

  /** Explicit user retirement of an unresolved record. Mutates no bytes. */
  acknowledge(preparedRef: string): Promise<void>;

  /** Path tokens whose prior outcome is unknown. Used by the overlap refusal. */
  unresolvedPathTokens(): Promise<ReadonlySet<string>>;

  /** Renderer-safe: counts, tokens, outcomes. No paths, no refs, no digests. */
  listUnresolved(): Promise<readonly WorkspaceReconciliationReport[]>;
}
```

`LocalWorkspaceAuthority` gains the private-broker-invisible entry point the reconciler drives:

```ts
/**
 * Read-only. Re-supplies the approved, non-observed half of a crashed change
 * set and returns the helper's classification. Mints no permit, consumes none,
 * writes nothing to the workspace, and is unreachable from broker.ts.
 */
observeReconciliation(
  record: WorkspaceJournalRecord,
  source: WorkspaceReconciliationSource,
): Promise<WorkspaceReconciliationReport>;
```

`NativeWorkspaceAuthority` gains one member (implemented by all three implementations — `UnavailableNativeWorkspaceAuthority` at [native-workspace-authority.ts:45](../../../apps/desktop/main/capabilities/native-workspace-authority.ts) throws; `AddonNativeWorkspaceAuthority` at :114 projects; the key list in `hasNativeWorkspaceV2Bindings` (:187-198) grows):

```ts
reconcileObserve(
  claimId: string,
  root: string,
  entries: readonly WorkspaceChangeEntry[],
): Promise<NativeWorkspaceCommitResult>;
```

### 5. Main — capability channel and unavailability reason

```ts
// channels.ts
export const CAPABILITY_CHANNELS = {
  // …four existing channels unchanged…
  /** Renderer → main: unresolved workspace changes from a prior boot. */
  listUnresolvedWorkspaceChanges:
    "capability.list-unresolved-workspace-changes",
  /** Renderer → main: retire one unresolved record after the user reads it. */
  acknowledgeUnresolvedWorkspaceChange:
    "capability.acknowledge-unresolved-workspace-change",
} as const;
```

Payloads are `WorkspaceReconciliationReport[]` minus `preparedRef`/`claimId` — see D11. FS-09 owns the presentation.

```ts
// workspace-production-authority.ts
export type WorkspaceUnavailabilityReason =
  | "platform_unsupported"
  | "not_packaged"
  | "not_production"
  | "encryption_unavailable"
  | "confinement_unavailable"
  | "confinement_refused"
  | "helper_launch_failed"
  | "journal_integrity_failed" // the helper refused to boot (:633, :977-978)
  | "root_identity_invalid";

export interface ProductionWorkspaceAuthorityConfig {
  // …existing fields unchanged…
  /** Called exactly once when the factory returns null. */
  readonly onUnavailable?: (reason: WorkspaceUnavailabilityReason) => void;
}
```

## Design

### D1. Reconciliation observes; it never repairs

Every mechanism below produces **facts**. None of them changes a byte in the user's workspace. Concretely:

- `RECONCILE_OBSERVE` performs zero writes — not to the workspace, not to the staging directory, not even to the journal. This is structural, not a review convention: the function calls no write primitive, and T4 asserts the journal directory is byte-identical before and after.
- The startup pass keeps its existing three downgrades and adds only new **classification** rows to `c2j-` records and FS-04 disposition updates. It moves no workspace object.
- `command_abort_or_recovery`'s recovery branch keeps returning conflict (:958). FS-07 does not soften it; it deletes the caller (D9).

Rejected: auto-restoring a preimage when reconciliation finds a displaced object with no replacement (the FS-06 Strategy-B window in D3.5). The case is real and it is the worst one — the user's file is gone from its name and is sitting in the trash. The argument for auto-restore is that we know exactly where it is; the argument against, which wins, is threefold. It is a mutation nobody approved, at boot, racing whatever else touches that folder. It would be the program's first write that is not gated by a permit ([PRD-FS-04 D6](PRD-FS-04-preimage-trash.md) makes restore a permit-bearing, user-confirmed change set). And a restore whose result was not observed is not a restore, which means the auto-path needs its own verification, its own failure lane and its own indeterminate outcome — a second write path inside the PRD whose job is to prevent them.

The mitigation is that the report is surfaced **at boot, before any run**, and restore is one confirmation away through FS-04's existing `prepareLocalRestore` / `authorizeLocalRestore`.

### D2. FS-07 adds no platform seam member

Everything FS-07 needs — confined open, identity, metadata, directory iteration, file I/O, durability — is already declared in `fs_platform.h` ([PRD-FS-01 §2](PRD-FS-01-platform-seam.md)). The evidence log is an ordinary private file; classification is arithmetic over recorded and observed facts.

This is not a coincidence, it is the point. Reconciliation is the part of the program most tempting to solve with a platform trick ("ask the filesystem what happened"), and there is no such trick on either platform. Making FS-07 seam-neutral means it lands on macOS and Windows simultaneously by construction, and satisfies the spine's last guardrail without a Windows runner. A DoD item asserts `fs_platform.h` is absent from FS-07's diff.

### D3. The crash points, per verb, and the exact recovery action

This is the canonical per-entry ordering FS-07 **defines**; FS-05 D10 and FS-06 D10 are special cases of it.

```
C0  journal_transition(COMMITTING)                     existing (:924), fsynced
──── per entry i, strictly in order (:927) ───────────────────────────────────
C1  effect_log_append(row_i, phase = ARMED)            NEW, durable on return
C2  stage_preimage(i)                                  FS-04, delete/move/replace only
C3  preimage row (i) → RETAINED                        FS-04, durable on return
C4  the effect syscall                                 clone | mkdir | rename | swap
C5  fs_durable_barrier(parent)                         existing fsync(parent_fd)
C6  post-effect observation                            fs_stat_at + digest, no path
C7  effect_log_append(row_i, phase = OBSERVED)         NEW, durable on return
──── end per entry ───────────────────────────────────────────────────────────
C8  journal_transition(APPLIED)                        existing (:932)
C9  cleanup_prepared_stages                            existing (:933)
```

**The armed row is the effect frontier.** Because appends are strictly ordered and each entry's `OBSERVED` row is durable before the next entry's `ARMED` row, the log's tail is decisive:

- the highest `entry_index` with an `OBSERVED` row is the last **completed** entry;
- at most one entry can carry an `ARMED` row without an `OBSERVED` row — that is the interrupted entry;
- every entry above it has **no row at all** and therefore provably had no effect.

One interrupted commit therefore condemns exactly one entry, not `entry_count` entries. This invariant is the whole value of the log and T2 pins it.

#### D3.1 The four named crash points

| Crash point                                        | On disk afterwards                                         | Recovery action                                                                                                                                                                                                                        |
| -------------------------------------------------- | ---------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **staged-not-committed** (C0–C1)                   | `COMMITTING` record, no row for entry `i`                  | Entry `i` and all above: `failed_before_effect`, `evidence = not_armed`, `observed_state = pre_state_intact` (verified by observation). The staged object is left for identity-checked cleanup (:716-730); never unlinked by name.     |
| **committed-not-journalled** (C4–C7)               | `ARMED` row, no `OBSERVED` row                             | The window. Classification is verb-dependent — see D3.2. For `delete`/`move`/`replace` it is **decidable** by identity; for `create`/`mkdir` it is **not**, and the honest answer is `indeterminate` with an observed post-state (D4). |
| **preimage-written-not-linked** (C2–C3, and C3–C4) | FS-04 row present, target and trash in one of three states | Decided by identity: the recorded displaced identity is either at the target (not displaced) or under the trash leaf (displaced) or neither (`divergent`). See D3.5.                                                                   |
| **swapped-not-verified** (C4–C6, FS-06)            | swap done, no `OBSERVED` row                               | **Doubly** decidable: both the source identity and the sealed-stage identity were recorded before the effect. See D3.4.                                                                                                                |

#### D3.2 `create`

`fs_commit_create` is `fclonefileat(staged, parent, leaf, CLONE_NOFOLLOW)` (:759-760). A clone **mints a new inode** and leaves the staged object intact, so neither the target's identity nor the stage's presence distinguishes applied from not-applied.

| Observation at the target leaf                              | Entry outcome          | `observed_state`                 | `evidence`          |
| ----------------------------------------------------------- | ---------------------- | -------------------------------- | ------------------- |
| No `ARMED` row                                              | `failed_before_effect` | `pre_state_intact` (leaf absent) | `not_armed`         |
| `OBSERVED` row, leaf holds the recorded `post_*` identity   | `applied`              | `approved_end_state`             | `observed`          |
| `OBSERVED` row, leaf holds something else / is absent       | `applied`              | `divergent`                      | `observed`          |
| `ARMED` only, leaf absent                                   | `indeterminate`        | `pre_state_intact`               | `pre_state_intact`  |
| `ARMED` only, leaf is a regular file with `expected_digest` | `indeterminate`        | `approved_end_state`             | `end_state_present` |
| `ARMED` only, leaf exists with any other content/kind       | `indeterminate`        | `divergent`                      | `divergent`         |

Row 3 is not a contradiction: the effect provably happened (a durable `OBSERVED` row records the helper's own read-back), and the workspace has since changed. That is a fact about the user's folder, not about the commit.

Rows 4-6 are the honest gap and D4 argues them.

#### D3.3 `mkdir`

Identical shape to `create` with `kind = directory` and no digest; `mkdirat` also mints a fresh inode (:763). `expected_digest` is absent, so the `ARMED`-only "end state present" test is "a directory exists at the leaf", which is weaker still.

#### D3.4 `delete` and `move` (FS-05) and `replace` (FS-06) — the conclusive verbs

A rename **preserves the inode**. This inverts the intuition: the two verbs macOS already had reconcile worst, and the three it refused reconcile best.

`delete` — the recorded `source` identity is either at the original leaf or under FS-04's unguessable trash leaf, and nothing else can put it in either place:

| Where the recorded source identity is found | Entry outcome          | `observed_state`     | `evidence`             |
| ------------------------------------------- | ---------------------- | -------------------- | ---------------------- |
| under `row.preimage_leaf` in the trash      | `applied`              | `approved_end_state` | `identity_applied`     |
| at `parent/leaf`, unchanged                 | `failed_before_effect` | `pre_state_intact`   | `identity_not_applied` |
| neither                                     | `indeterminate`        | `divergent`          | `divergent`            |

`move` — the same test with the destination in place of the trash: source identity at `destination_parent/destination_leaf` ⇒ applied; at `parent/leaf` ⇒ not applied; neither ⇒ indeterminate + divergent.

`replace` (FS-06's `RENAME_SWAP`) — **two** identities were recorded before the effect, so the test is a pair and every mixed result is caught:

| Target leaf holds  | Stage name holds   | Entry outcome          | `evidence`             |
| ------------------ | ------------------ | ---------------------- | ---------------------- |
| `stage_*` identity | `source` identity  | `applied`              | `identity_applied`     |
| `source` identity  | `stage_*` identity | `failed_before_effect` | `identity_not_applied` |
| anything else      | anything else      | `indeterminate`        | `divergent`            |

The `conclusive` byte on the row records which lane applies, so the classifier never guesses which test to run.

#### D3.5 Preimage-written-not-linked, and the loud case

FS-04's row is written before the displacement and updated after ([PRD-FS-05 D10 steps 2-5](PRD-FS-05-delete-move.md)). Reconciliation resolves it purely by identity, never by presence:

- **Trash leaf absent, target holds the recorded source identity** ⇒ the displacement never happened. Set `present = 0`. Entry `failed_before_effect`.
- **Trash leaf holds the recorded source identity, target holds the approved post-state** ⇒ the whole verb completed. Disposition `RETAINED`, entry `applied`.
- **Trash leaf holds the recorded source identity, target is absent and no replacement landed** ⇒ FS-06 Strategy B's window. The user's file is not where they left it. Entry `indeterminate`, `observed_state = divergent`, `preimageAvailable = true`, and the report ranks this record first. **No auto-restore** (D1).
- **Trash leaf holds something the row does not describe** ⇒ identity drift. Disposition `PREIMAGE_UNKNOWN` (never `COLLECTED`), leaf never unlinked — the same reasoning as `cleanup_prepared_stages`' comment (:712-715) and FS-04 D3.

The third bullet is a real input to FS-04 D9's Strategy A vs B question, and **FS-06 has since decided it: Strategy B, on both platforms.** `ReplaceFileW` would have had no such window — it performs the displacement and the replacement in one OS call — but it takes three **paths**, so taking it would hand the walked, reparse-refusing, handle-retained subtree back to the kernel at the moment of the effect (FS-06 D5). FS-06 keeps it only as a reported degradation if the rename information class turns out to be unavailable on the project's minimum Windows build. So this window is **accepted**, not open, and classifying it is FS-07's job rather than an argument for changing the effect primitive. (`staged_before_effect` still distinguishes the two, because the fallback can still be taken.)

### D4. `INDETERMINATE` is kept where causation is unknown, and observation is reported beside it

The tempting move in D3.2 rows 4-6 is to call an `ARMED`-only create whose leaf now holds exactly the approved digest `applied`. It is tempting because it is almost always true, and it is wrong because "almost always" is not "observed" and because `outcome` is consumed by things that must not be wrong: the receipt ([projectReceiptV2.ts:724-726](../../../packages/chat-surface/src/destinations/run/projectReceiptV2.ts)), the effect ledger, the model's post-commit narration ([PRD-FS-09 D12](PRD-FS-09-enablement-consent.md)) and any compliance answer to "did this change land".

So FS-07 separates two questions the current enum conflates:

- **`outcome`** — did _this transaction_ cause the effect? Sometimes unknowable.
- **`observedState`** — what is _true of the workspace now_? Always knowable when the root can be opened.

`outcome` stays the existing five-value closed vocabulary; no code is added (FS-05 D11's rule). `observedState` and `evidence` carry the detail, are advisory, and **may never influence `outcome`** — the decoder maps an unknown code to `"unknown"` and leaves the outcome alone, exactly as FS-05 requires of `reason`.

Concretely, an interrupted create is reported as: outcome `indeterminate`, observed `approved_end_state`, and a safe message along the lines of _"The file now matches the approved contents. We could not confirm the change completed before the interruption."_ That is more useful than today's bare `indeterminate` and it does not assert anything unobserved.

**One demotion rule, for the conclusive verbs only.** If a `delete`/`move`/`replace` entry has an `OBSERVED` row but a fresh observation finds the **recorded source identity back at its original location**, the entry is demoted from `applied` to `indeterminate` + `divergent`. A completed rename cannot leave the same inode at the source, so exactly one of the two facts is wrong — and on Windows, where the durability of a directory-metadata change is unproven ([PRD-FS-05 D9 spike 3](PRD-FS-05-delete-move.md), [PRD-FS-04 spike 3](PRD-FS-04-preimage-trash.md)), a power-loss rollback is precisely how that happens. The rule is deliberately **not** applied to `create`/`mkdir`, where an absent leaf after a completed create is ordinary user behaviour, not a contradiction.

### D5. The evidence lives in an append-only log, not in the journal record

Rejected first, and why: appending a second fixed trailer to `struct journal_record` (FS-04's pattern) would require rewriting the whole record per entry, and `journal_store` is temp-create → write → `fsync(file)` → `renameat` → `fsync(dir)` (:460-474). With FS-04's 224-byte trailer plus a 448-byte one, a 256-entry record is ~172 KB; `journal_transition` writes it twice (:605-608). Per-entry updates would then cost ~44 MB of writes and ~1024 `fsync`s for one change set, and would bump `JOURNAL_VERSION` to 5 with a second migration lift on top of FS-04's D8.

The append-only log gives the same durability with O(1) cost per entry, no rewrite, no record-layout change, and **no journal version bump at all** — FS-04's v3→v4 lift stays the only migration in the program.

Consequences that fall out for free:

- `journal_reconcile_startup` already skips every name that is not `c2j-`/`c2c-` (:632), so `c2e-` files are invisible to the existing scan and cannot make it abort. FS-07 reads them in its own pass, after the downgrades.
- Torn writes are trivially bounded: appends only tear at the tail, and the reader stops at the first short/invalid/out-of-sequence row.
- Per-entry evidence never touches the `c2c-` claim record, so `journal_claim_update_owned`'s ownership check (:554-568) and `claim_transition_allowed` (:542-552) are untouched. The claim record's authority is the claim **lifecycle**; per-entry evidence is not lifecycle.
- Because the log is keyed by `handle`, and `handle` is on both the `c2j-` and `c2c-` records (:536), a reconciler reaches it from either. FS-07 adds `journal_find_preparation_for_claim(claim)` — a bounded `fs_dir_for_each` over `c2j-` names, the same shape as `journal_lookup_claim`'s fallback scan (:665-677) — because `journal_lookup_claim` returns early on the direct `c2c-` hit (:658-663) and never reaches the preparation record.

### D6. Separation of duties: main knows what was approved, the helper knows what was observed

The helper cannot re-open the target (no path). Main cannot know what the helper observed (no access to the private journal, and it must not be trusted to restate it). So a reconciliation requires both, and neither may be able to forge it alone.

`RECONCILE_OBSERVE` therefore splits the input:

- **Main supplies** the root path (resolved from the grant, never from a worker or a model) and the approved, **non-observed** half of every entry: operation, relative paths, slot, expected digest, expected size.
- **The helper supplies** the observed half from its own durable `effect_row.source` / `.destination` snapshots.
- The helper reassembles a `struct prepared` from the two halves, runs `compute_prepared_binding` (:279-302) **unchanged**, and requires the result to equal the stored `binding_digest` byte-for-byte. A mismatch is `CONFLICT`.

Three properties follow:

1. **The binding check is also the root-identity check.** `compute_prepared_binding` folds the root's `dev`/`ino` (:284-285), so a request naming a different root — or the same path after the folder was replaced — cannot produce a matching digest. No separate root-identity comparison is needed, and none is added.
2. **Main cannot fabricate a favourable pre-state.** The request carries no `exists`, `kind` or precondition digest; those bytes come only from the MAC'd log.
3. **The helper cannot fabricate a target.** It has no path until main gives it one, and `open_parent` (:372-398) re-walks it with the full confinement policy — `path_is_safe`, exact directory entry per hop, `O_NOFOLLOW_ANY`, per-hop device equality. Reconciliation is not a confinement exception.

This is the same defence-in-depth shape `journal_acquire_claim` already uses when it refuses a claim whose binding differs (:586-589).

### D7. The frontier rule, stated as an invariant the implementation must preserve

> The effect loop is strictly ordered, and entry `i`'s `OBSERVED` row is durable before entry `i+1`'s `ARMED` row is written.

`command_commit` already loops in order and breaks on the first failure (:927), so the only new obligation is the durability ordering. Everything in D3 depends on it, so it is asserted three ways: by construction (`effect_log_append` returns only after `fs_durable_barrier`), by a test that reads a log after each of the new fault points and checks `sequence` and `entry_index` monotonicity (T2), and by a DoD item.

An entry the loop reached and refused (a failed `sealed_stage_matches`, a `PRECONDITION_DRIFT` from `entry_live`) gets a row with `phase = EFFECT_SKIPPED`, so "no row" unambiguously means "never reached".

### D8. Cost, honestly

The added durable work is one append + one `fs_durable_barrier` at C1 and one at C7, per entry. For the overwhelmingly common single-entry change set that is 2 extra barriers on top of the existing 4 `fsync`s per `journal_transition` (:605-608 stores twice, each doing file + directory) — call it a ~30% increase in the durable-write count of a commit, on an operation the user already waited for an approval sheet to authorize.

No number is asserted here because none was measured. A DoD item requires the implementer to measure `commit` latency for 1, 8 and 64 entries before and after on an APFS volume, and to record it in the PR. If the 64-entry case regresses materially, the mitigation is to batch the `ARMED` rows for entries `i+1..n` into the C1 append of entry `i` (they are all known at C0) — which preserves the frontier rule at the cost of a coarser "no row" boundary, and must be a deliberate, recorded choice rather than a default.

### D9. Delete the dead recovery pair rather than leave it next to a real one

`proposeRecovery` / `proposeRecoveryClaim` cannot succeed (the helper writes `0` at :958), have no callers, and their `recovery_conflict` terminal state would silently retire an unresolved record (Context). A surface that can only ever mislead is worse than no surface, and this is the exact surface a compliance reviewer will read as "there is a recovery path".

Removed: `PROPOSE_RECOVERY` / `PROPOSE_RECOVERY_CLAIM` from `enum request` (:61) and `Request` ([native-workspace-commit-helper.ts:42-43](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.ts)); `command_abort_or_recovery`'s `recovery` parameter and branch (:954-960) — `command_abort` keeps the abort half verbatim; `proposeRecovery`/`proposeRecoveryClaim` from `NativeWorkspaceAuthority` (:249-252) and all three implementations; `proposeRecovery`/`proposeRecoveryForClaim` from `LocalWorkspaceAuthority` (:742-770); `decodeRecovery` (:644-649); `"recovery_proposed"` and `"recovery_conflict"` from `WorkspaceJournalState`.

Safe because helper and main ship in one artifact ([:482-500](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.ts)) — there is no mixed-version window (FS-05's version-coupling note). `"rolled_back"` stays: FS-04's local restore is its natural producer and giving it one is FS-04's job, not FS-07's.

Migration for already-persisted records carrying the removed strings is D10.

### D10. Unrecognised state means unresolved, never resolved

`isJournalRecord` accepts any string as `state` ([workspace-journal.ts:222](../../../apps/desktop/main/capabilities/workspace-journal.ts)). FS-07 makes the classification explicit rather than incidental:

```ts
const TERMINAL_STATES = new Set([
  "applied",
  "failed_before_effect",
  "rolled_back",
  "acknowledged_indeterminate",
]);
// listNonterminal / listUnresolved: a state that is NOT in TERMINAL_STATES is
// nonterminal — including "recovery_proposed", "recovery_conflict" and any
// value a future version wrote. Fail toward re-checking.
```

So an upgraded installation's `recovery_conflict` records become re-checkable instead of silently retired, and a downgrade cannot hide work. `isJournalRecord` additionally validates `state` against the known union **for the purpose of typing only** — an unknown value is retained verbatim and bucketed as nonterminal, never dropped, because dropping a record loses the only pointer to a preimage.

### D11. What is reported, and what never leaves main

Per unresolved record the user is shown: how many entries, each entry's operation, its outcome, its observed state, whether a previous version is restorable, and when it happened. Ranked so that `divergent` + `recoverable` sorts first — the loud case in D3.5.

Never in a report, an audit row, a broker response or an IPC payload:

- a plaintext path (only `pathToken`, keyed by the main-only `#journalTokenKey`, :1033-1037);
- a `preimageRef` (FS-04 D11's rule; main joins the ref locally when the user asks to restore);
- a content digest, a `dev`/`ino`, a trash leaf, or `changeSpec`.

`preparedRef` and `claimId` stay main-side; the renderer payload carries an opaque per-boot handle so acknowledgement can be routed back without exposing a durable reference.

Audit facts (local, main-emitted, alongside FS-04's `preimage_*` family): `workspace_reconciliation_started`, `workspace_reconciliation_resolved`, `workspace_reconciliation_indeterminate`, `workspace_reconciliation_unobservable`, `workspace_reconciliation_acknowledged`. Each carries counts, path tokens, outcomes and evidence codes.

The ai-backend contract is unchanged. `WorkspaceCommitResult`'s five-value outcome ([broker_client.py:427-441](../../../services/ai-backend/src/agent_runtime/capabilities/desktop/broker_client.py)) is untouched; FS-07 adds `observed_state` and `evidence` to FS-06's existing per-entry `entry_results` projection in `workspaceCommitWire` and **does not** send path tokens across the broker — they are keyed by a main-only key and the ai-backend has no use for them.

### D12. An unresolved outcome does not block writes; it blocks writes to that path

Blocking all workspace writes because one old record is unresolved is a denial of service on the user's own product. Ignoring it is how a second commit lands on top of a file whose first commit's outcome is unknown, destroying the only evidence of what happened.

So the refusal is targeted: `prepareChangeSet` ([workspace-authority.ts:457-506](../../../apps/desktop/main/capabilities/workspace-authority.ts)) gains `#assertNoUnresolvedOverlap(changeSet)`, which throws `workspace_conflict` when any entry's `relativePath` or `destinationRelativePath` tokenises to a path token held by an unresolved record. It fires at **prepare**, before an approval sheet is shown, so the user is never asked to approve something that will be refused.

The overlap set is computed from path tokens alone, so the check needs no plaintext and no root. It clears when the record resolves or when the user acknowledges it.

### D13. Bounded pruning, without a clock and without deleting evidence

FS-04 hands journal pruning here. The helper has no clock (Context), so nothing here is time-based.

**Prune `c2j-` preparation records, never `c2c-` claim records.** `journal_lookup_claim` answers a claim query from the direct `c2c-` name first (:658-663) and only falls back to scanning `c2j-` (:665-677). Pruning a terminal `c2j-` therefore loses nothing a claim query can observe; pruning a `c2c-` would turn a decided `already_applied` into an undecided `indeterminate` (:907, :950) — a regression from decided to unknown, which is the one direction this program never moves.

A `c2j-` record is prunable only when **all** hold:

- it loads MAC-valid;
- its state is `JOURNAL_APPLIED`, `JOURNAL_FAILED_BEFORE_EFFECT` or `JOURNAL_CLEANED`, and `cleanup_complete == 1`;
- its claim's `c2c-` record loads MAC-valid and is in the same terminal family;
- no surviving MAC-valid FS-04 preimage row on it has disposition `RETAINED` or `PREIMAGE_UNKNOWN`;
- the count of `c2j-` records exceeds `JOURNAL_SOFT_CAP` (2048).

Victims are taken in `fs_dir_for_each` order until the count is at or below the cap — arbitrary but bounded, and every victim is provably terminal. Its `c2e-` log is unlinked in the same pass, by exact name, after the record.

**Staging objects**: a `c2-<32 hex>` run directory is removed only when no surviving MAC-valid record names it in `stage_dir` **and** it is empty after an identity-checked pass. Never unlink an object the helper cannot prove it created — `cleanup_prepared_stages`' rule (:712-730) and FS-04 D3's rule, reused verbatim rather than restated as a new policy. This is also why pruning is location-independent: it follows references from MAC-valid records, never a directory layout.

Pruning runs at startup, after the downgrade pass and after classification, and a failure is non-fatal: pruning is hygiene, and a helper that cannot prune must still serve.

### D14. Boot ordering, bounds, and failing toward "still unresolved"

`WorkspaceReconciler.sweep()` runs in `main/index.ts` after `createProductionWorkspaceAuthority` (:380) and **before** `startCapabilitySubsystem`, so no run can begin against a workspace whose prior state is unknown and D12's overlap set is populated before the first `prepareChangeSet`.

It is bounded: at most `maxRecordsPerSweep` records per boot (default 64, remainder next boot), one `observeReconciliation` per record per boot, and each helper call inherits the client's `timeoutMs` ([native-workspace-commit-helper.ts:246](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.ts)).

It never throws. Every failure mode leaves the record unresolved:

| Failure                                            | Result                                                                                                      |
| -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Grant revoked, expired, or not for this device     | `observedState = "unobservable"`, `evidence = "root_unavailable"`, record stays `indeterminate`             |
| Root moved or replaced                             | helper returns `CONFLICT` on the binding; same as above                                                     |
| Record has no `grantId`/`changeSpec` (pre-upgrade) | `evidence = "record_unreadable"`; the record is reported as unresolvable and can only be acknowledged (D15) |
| Helper unavailable or throws                       | sweep records nothing and returns `[]`; retried next boot                                                   |
| `c2e-` log absent (crash before C1)                | every entry `not_armed` — a **decided** result, not a failure                                               |

A revoked-grant record is the honest "we cannot check": the user is told the folder access expired and that re-granting will let the check run.

### D15. Only the user retires an unresolved record

An unresolved record stays in `listUnresolved()` until it resolves to `applied` or `failed_before_effect`, or until the user acknowledges it — which moves it to `acknowledged_indeterminate` and out of the sweep and the D12 overlap set.

Acknowledgement changes nothing on disk and does **not** unlock GC of the entry's preimage: FS-04's eligibility requires the _helper's_ record to be `JOURNAL_APPLIED` ([PRD-FS-04:507-510](PRD-FS-04-preimage-trash.md)), and reconciliation never writes that state. So a user who dismisses the notice still has their previous version.

The alternative — auto-retiring after N boots — was rejected because the only thing distinguishing "the user knows" from "nobody ever saw it" is the user's own action, and a timer would make an unread data-loss notice disappear.

### D16. The `INDETERMINATE`-preserving fixes to the existing main-side mapping

Two changes, both narrow:

```ts
// commitPreparedChangeSet (:690-691) and reconcileCommit (:726-727)
const terminal: WorkspaceJournalState =
  mapped.outcome === "applied" || mapped.outcome === "already_applied"
    ? "applied"
    : mapped.outcome === "indeterminate"
      ? "indeterminate"
      : "failed_before_effect"; // precondition_drift | failed => zero effect proven
```

`failed` and `precondition_drift` mean zero effect by construction: the only producers are `journal_outcome_for` over a `FAILED_BEFORE_EFFECT` claim (:616-622, reached only via :637-646) and the pre-effect drift branch (:918-922). FS-05 D11 and FS-06 D13 both state the rule that `FAILED` may only be reported when zero effect is proven; FS-07 relies on it and a test pins it.

## Implementation plan

FS-01 splits the helper across the seam; where FS-01's filenames differ, follow FS-01. Where FS-04 has already bumped `PROTOCOL`, adopt the then-current number and bump once (D-Interfaces §1).

### Helper (portable half — no `fs_platform.h` change)

1. **`workspace_commit_helper.c`** — add `struct journal_snapshot`, `struct effect_row`, their two `_Static_assert`s, `enum effect_phase`, `enum observed_state`, `enum reconcile_evidence`, and `RECONCILE_OBSERVE = 16`. Bump `PROTOCOL` (:45).
2. Add `effect_log_name(handle, char out[80])` (mirroring `claim_journal_name`, :454-458), `effect_log_append` (open-or-create with `fs_open_new_exclusive` / `fs_open_read_at`, write at end, `fs_durable_barrier` on the file and on `journal_fd` for a first-time create), and `effect_log_load` with the stop-at-first-invalid-row contract.
3. Add `snapshot_to_row(const struct snapshot *, struct journal_snapshot *)` and its inverse `row_to_snapshot`, so `compute_prepared_binding` can be fed from durable rows without a second binding implementation.
4. Add `journal_find_preparation_for_claim(const char *claim, struct journal_record *out)` — a bounded `fs_dir_for_each` over `c2j-` names, the shape of :665-677.
5. **`command_commit` (:896-941)** — wrap the effect loop (:927) with the C1/C7 appends per D3, and emit `EFFECT_SKIPPED` rows for entries the loop reached and refused. Populate `post_*`/`post_digest`/`post_kind` from a post-effect `fs_stat_at` + `regular_digest_fd` through a handle, never a re-walked path. Populate `stage_*` from `entry->sealed_stat`/`sealed_digest` (:105-106) and `preimage_leaf` from FS-04's row.
6. **New `command_reconcile_observe`** — parse the body; look up the claim's `c2c-` record and its `c2j-` preparation (step 4); `open_root(root_path)`; rebuild a `struct prepared` from main's descriptors plus the log's snapshots; `compute_prepared_binding`; compare to `record.binding_digest`; on mismatch `CONFLICT`. On a root that will not open, respond `INDETERMINATE` with `entry_result_count = 0`. Otherwise `open_parent` per entry, observe, classify per D3, and emit the per-entry block. **Call no write primitive anywhere in this function.**
7. **`write_commit_result` (:891-894)** — emit the per-entry block (FS-06's four fields plus `observed_state` and `evidence`); `entry_result_count = 0` on `RECONCILE_CLAIM` and on the no-`prepared` branch (:902-909).
8. **`journal_reconcile_startup` (:626-651)** — keep the three downgrades and the fail-closed abort exactly as written; after the existing loop (and after FS-04's `c2p-` pass), add the FS-07 pass: for each `COMMITTING`-downgraded record, load its `c2e-` log and reconcile FS-04 preimage dispositions per D3.5. Then run D13's pruning. A pruning failure is logged-by-omission, not fatal.
9. **`command_abort_or_recovery` (:954-960)** — split; keep the abort half verbatim as `command_abort`, delete the recovery half and its two request ids (D9).
10. **`test_crash_boundary`** — add faults `7 = after the ARMED row, before the effect` and `8 = after the effect, before the OBSERVED row`, the pair the spine's crash-fault ladder allocates to FS-07. Do not take 5/6 (FS-04's), 9/10 (FS-06's) or 11/12 (FS-05's) even if those PRDs have not landed — the ladder is allocated in the spine precisely so an unlanded PRD's numbers are not squatted.

### Main

11. **`native-workspace-commit-helper.ts`** — `HELPER_PROTOCOL_VERSION` (:28) in lockstep; `Request.ReconcileObserve = 16`; delete `ProposeRecovery`/`ProposeRecoveryClaim` (:42-43), the two methods (:348-366) and `decodeRecovery` (:644-649); extend `decodeCommitResult` (:634-642) with the per-entry block, mapping unknown `observed_state`/`evidence` codes to `"unknown"`/`undefined` and **never** letting them change `outcome` (:651-666 unchanged); add `reconcileObserve`.
12. **`native-workspace-authority.ts`** — `reconcileObserve` on the port and all three implementations; drop the recovery pair; update `hasNativeWorkspaceV2Bindings` (:187-198).
13. **`workspace-authority.ts`** — the D16 terminal mapping at :690-691 and :726-727; `grantId`/`changeSpec`/`reconciliation` on `WorkspaceJournalRecord` and in `#journalRecord` (:989-1018); the `WorkspaceJournalState` change (D9/D15); `listUnresolved` on `WorkspaceJournalStore` + `InMemoryWorkspaceJournalStore` (:286-294); `observeReconciliation`; `#assertNoUnresolvedOverlap` called from `prepareChangeSet` (:457-506); delete `proposeRecovery`/`proposeRecoveryForClaim` (:742-770).
14. **`workspace-journal.ts`** — persist the three new fields; `TERMINAL_STATES` per D10 in `listNonterminal` (:74-83); `listUnresolved`; extend `isJournalRecord` (:217-237) to type-check `grantId` and `changeSpec` while retaining unknown `state` values verbatim.
15. **NEW `workspace-reconciler.ts`** — `sweep`, `recheck`, `acknowledge`, `unresolvedPathTokens`, `listUnresolved`; grant resolution, bounds, audit emission, ranking (D11).
16. **`workspace-production-authority.ts`** — `WorkspaceUnavailabilityReason` and the `onUnavailable` callback at each `return null` (:84-92, :99, :128, :134) and in the `catch` (:158-159).
17. **`capabilities/index.ts`** — construct the reconciler in `createCapabilityService` (:59-127) and expose it on `CapabilityService`.
18. **`main/index.ts`** — `await reconciler.sweep()` after :380 and before `startCapabilitySubsystem`; wire `onUnavailable` into the existing boot log.
19. **`channels.ts` + `ipc/handlers.ts`** — the two new channels and their handlers, with a `.strict()` schema for the renderer projection (D11).
20. **`broker.ts`** — add **nothing** to `ROUTES` (:79-94) or `ADVERTISED_METHODS` (:97-112); extend `workspaceCommitWire` (:1137) with `observed_state`/`evidence` on FS-06's `entry_results`; add the canary assertion in T7.

## Test plan

Native tests extend `native-workspace-commit-helper.test.ts`, which spawns the real binary against real temp roots with a fixed journal key (`Buffer.alloc(32, 7)`, :68) and `describeNative` skipping off-darwin (:33). The fixed key lets tests author MAC-valid records and rows by hand.

### T1 — the evidence log itself

- A log with N rows round-trips through `effect_log_append`/`effect_log_load` with every field byte-identical.
- Flipping one byte anywhere in a row makes that row and everything after it ignored; rows before it still load.
- Truncating the file mid-row: the preceding rows load, the partial row does not, and the helper still answers a subsequent `PING` (it refused, it did not die).
- A row with `sequence` out of order is rejected along with everything after it.
- A row with a nonzero `reserved` or `tail_pad` byte is rejected.
- A row whose `handle` names a different preparation is rejected (proving the handle is inside the MAC).
- A row MAC'd with a different key is rejected, and the helper does **not** unlink the log.
- `effect_log_load` on 256 rows allocates a bounded buffer: assert `MAX_EFFECT_ROWS + 1` rows are refused before the read, not after.

### T2 — the frontier invariant

Using faults 7 and 8 plus the existing fault 4 (:931), for a 3-entry change set:

- Crash at fault 7 on entry 1: entry 0 has `ARMED`+`OBSERVED`, entry 1 has `ARMED` only, entry 2 has **no row**. Assert exactly that, and assert `sequence` is `0,1,2` with no gaps.
- Crash at fault 8 on entry 1: same shape, and assert entry 1's effect **did** land on disk while its row is `ARMED` only.
- After each, a relaunch's `reconcileClaim` still returns `indeterminate` (unchanged set-level behaviour) and a `reconcileObserve` returns entry 0 `applied`, entry 1 `indeterminate`, entry 2 `failed_before_effect` / `not_armed`.
- Assert **no second mutation** occurs on relaunch: the workspace tree is byte-identical before and after the relaunch (this is the existing :585-616 property, generalised).

### T3 — classification, per verb

`create` / `mkdir` (drivable today):

- `ARMED`+`OBSERVED`, leaf present with the recorded identity → `applied` / `approved_end_state` / `observed`.
- `ARMED`+`OBSERVED`, leaf deleted by the test after the crash → `applied` / `divergent` / `observed`. **Assert the outcome is still `applied`** — a later deletion is not a failed commit.
- `ARMED` only, leaf absent → `indeterminate` / `pre_state_intact` / `pre_state_intact`.
- `ARMED` only, leaf present with `expected_digest` → `indeterminate` / `approved_end_state` / `end_state_present`. **Assert the outcome is not `applied`** — this is the case D4 exists for, and it must fail if anyone "optimises" it.
- `ARMED` only, leaf present with different bytes → `indeterminate` / `divergent` / `divergent`.

`delete` / `move` / `replace` (once FS-05/FS-06 land; until then, driven by hand-authored MAC-valid rows plus a hand-placed trash object, the technique FS-04's restore tests already use):

- recorded source identity under the trash leaf → `applied` / `identity_applied`.
- recorded source identity still at the original leaf → `failed_before_effect` / `identity_not_applied`. **Assert `failed_before_effect`, not `indeterminate`** — this is the entire value of the conclusive lane.
- neither → `indeterminate` / `divergent`.
- `replace`: leaf holds the sealed-stage identity and the stage name holds the source identity → `applied`; the reverse → `failed_before_effect`; any mixed pair → `indeterminate` / `divergent`.
- **D4's demotion rule**: an `OBSERVED` row for a `delete` whose recorded source identity is found back at the original leaf → demoted to `indeterminate` / `divergent`. The same setup for a `create` → stays `applied`.

### T4 — `RECONCILE_OBSERVE` is read-only and unforgeable

- Snapshot the journal directory (every filename, every byte, every mtime) and the staging parent before and after a `RECONCILE_OBSERVE`; assert byte-identical. Repeat for a request that returns `CONFLICT` and for one whose root cannot be opened.
- Snapshot the workspace tree before and after; assert byte-identical.
- A request naming a **different root** with otherwise identical entries → `CONFLICT` (the binding folds root `dev`/`ino`, :284-285). Do this by `mkdtemp`-ing a second root with the same relative layout.
- A request that alters one `relative_path` → `CONFLICT`.
- A request that alters `expected_digest` or `expected_size` → `CONFLICT`.
- A request whose `entry_count` differs from the record's → `INVALID`.
- A request for an unknown claim → `INDETERMINATE` with `entry_result_count == 0`.
- A request whose `c2e-` log is missing entirely → every entry `not_armed`, and the set outcome is `failed_before_effect`, not `indeterminate`.
- Confinement is not bypassed: a `relative_path` with `..`, a leading `/`, a backslash, a non-ASCII byte, or a symlinked intermediate is refused exactly as `PREPARE` refuses it.

### T5 — startup pass and pruning

- The three existing downgrades still fire, and `journal_reconcile_startup`'s scan still aborts (helper refuses to boot) on a MAC-invalid `c2j-`. This is the existing `"…refuses to boot"` behaviour at :533-557 of the test file; assert it did not regress.
- A `c2e-` file that is corrupt does **not** abort the startup scan (it is not a `c2j-`/`c2c-` name) and does not prevent boot.
- Pruning: with `JOURNAL_SOFT_CAP + 10` terminal `c2j-` records, a boot leaves at most the cap, deletes each victim's `c2e-`, and leaves **every** `c2c-` record. Assert `reconcileClaim` for a pruned preparation's claim still returns `already_applied` — the property D13 exists to protect.
- Pruning skips a record carrying a `RETAINED` or `PREIMAGE_UNKNOWN` preimage row, even under cap pressure.
- Pruning never removes a staging run directory named by a surviving record's `stage_dir`, and never removes a non-empty directory.
- A stray file in the journal directory matching no known prefix is untouched by any pass.

### T6 — main-side classification and the fixed mapping

In `workspace-authority.test.ts`, with a fake native authority:

- `reconcileCommit` on a claim the helper reports `failed` → journal state `"failed_before_effect"`, **not** `"applied"`. This test fails on today's code (:726-727) and is the regression pin for the verified defect.
- Same for `precondition_drift`, in both `reconcileCommit` and `commitPreparedChangeSet`.
- `already_applied` → `"applied"`. `indeterminate` → `"indeterminate"`.
- A record with an unrecognised persisted `state` string (`"recovery_conflict"`, `"nonsense"`) appears in `listUnresolved()` and `listNonterminal()`.
- `acknowledge` moves a record to `acknowledged_indeterminate`, removes it from `listUnresolved()`, and emits the audit fact.

### T7 — sweep, overlap refusal, and non-reachability

- `sweep()` with 3 unresolved records calls `reconcileObserve` exactly 3 times and returns 3 reports; with `maxRecordsPerSweep = 2` it calls twice and the third is swept on the next call.
- `sweep()` when the native authority throws returns `[]`, records stay unresolved, and it does not reject.
- `sweep()` with a revoked grant produces `observedState: "unobservable"` / `evidence: "root_unavailable"` and does **not** call `reconcileObserve`.
- `sweep()` with a pre-upgrade record (no `grantId`/`changeSpec`) produces `evidence: "record_unreadable"` and the record can still be acknowledged.
- `prepareChangeSet` for a path token held by an unresolved record throws `workspace_conflict`; the same change set succeeds after `acknowledge`.
- The refusal fires at **prepare**: assert `#native.prepare` was never called.
- **Non-reachability**: enumerate `ROUTES` (broker.ts:79-94) and `ADVERTISED_METHODS` (:97-112) and assert no entry matches `/reconcile-observe/`, `/unresolved/` or `/acknowledge/`; a planted canary route in the fixture must make the assertion fail.
- `changeSpec` and plaintext paths appear in no broker response, no IPC payload and no audit payload — assert by serialising every projection and scanning for the fixture's literal path strings.
- A worker holding a valid host session and read capability cannot reach `observeReconciliation` or `acknowledge` through any exported method.

### T8 — protocol

- `decodeCommitResult` round-trips a body with 0, 1 and 256 entry results.
- An unknown `observed_state` code decodes to `"unknown"`, an unknown `evidence` code to `undefined`, and **neither changes `outcome`**.
- A `PROTOCOL 3` response causes `workspace_helper_failed` via the existing version check ([native-workspace-commit-helper.ts:457-462](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.ts)), not a silent mis-parse.
- The removed request ids `9` and `10` are rejected as `INVALID` by the helper (default branch, :998).

### T9 — cross-platform

Two tiers, because FS-02/FS-03 are parallel to FS-07:

1. **Platform-independent** — T1's row layout and MAC rules, T6, T7, T8, and the classification lattice as pure functions. These run on **both** runners from the day FS-07 lands.
2. **macOS end-to-end** — T2, T3, T4, T5 against the real binary on the darwin runner.

There is no third tier: FS-07 adds no seam member, so there is no Win32 code to compile-but-not-exercise. The Windows job runs tier 1 and, once FS-02/FS-03 land, tier 2 unchanged. State this plainly in the PR rather than implying Windows coverage that a green tier-1 job does not represent.

## Definition of done

- [ ] `fs_platform.h` and `fs_crypto.h` are absent from the PR diff — FS-07 adds no seam member.
- [ ] `JOURNAL_VERSION` is unchanged, `struct journal_record` is byte-identical, and FS-04's preimage trailer is untouched.
- [ ] `journal_reconcile_startup`'s three downgrades, its `c2j-`/`c2c-` prefix filter, and its abort-the-scan-on-an-unloadable-record behaviour are unmodified; the helper still refuses to boot on a tampered journal.
- [ ] Every entry's `ARMED` row is durable before its effect syscall, and its `OBSERVED` row is durable before the next entry's `ARMED` row — proven by reading the log after faults 7, 8 and 4.
- [ ] After any single crash, at most **one** entry is `indeterminate`; entries above the frontier are `failed_before_effect` and entries below are decided.
- [ ] `delete`, `move` and `replace` reconcile **conclusively** by identity: a crashed-but-completed delete reports `applied`, a crashed-before-effect delete reports `failed_before_effect`, and only a divergent identity reports `indeterminate`.
- [ ] An interrupted `create` whose target now holds the approved digest reports `indeterminate` with `observed_state = approved_end_state` — not `applied`. A test fails if that is ever "optimised".
- [ ] `RECONCILE_OBSERVE` writes nothing: journal directory, staging parent and workspace tree are byte-identical before and after, including on the `CONFLICT` and root-unavailable paths.
- [ ] A `RECONCILE_OBSERVE` naming a different root, a different path, or a different content promise is refused by the claim-binding comparison, not by a bespoke check.
- [ ] `reconcileCommit` and `commitPreparedChangeSet` record `failed` and `precondition_drift` as `failed_before_effect`; the regression test fails on the pre-fix code.
- [ ] A boot sweep runs before the capability subsystem starts, is bounded, never throws, and leaves every record it could not resolve unresolved.
- [ ] An unresolved record blocks a **new** change set that targets the same path token, at prepare, and clears on resolution or acknowledgement.
- [ ] Only an explicit user acknowledgement retires an unresolved record, and acknowledgement does not make its preimage GC-eligible.
- [ ] `proposeRecovery`, `proposeRecoveryClaim`, `PROPOSE_RECOVERY`, `PROPOSE_RECOVERY_CLAIM`, `decodeRecovery`, `recovery_proposed` and `recovery_conflict` are gone; persisted `recovery_conflict` records load as **nonterminal**.
- [ ] Pruning removes only terminal `c2j-` records above the soft cap plus their `c2e-` logs; no `c2c-` record is ever removed, and a pruned preparation's claim still reconciles to `already_applied`.
- [ ] No preimage-bearing record is pruned, and no staging object the helper cannot prove it created is unlinked.
- [ ] No reconciliation route reaches `broker.ts`; the canary test proves the assertion is live.
- [ ] No plaintext path, `changeSpec`, `preimageRef`, digest or inode number appears in any broker response, IPC payload or audit row.
- [ ] `createProductionWorkspaceAuthority` reports a typed `WorkspaceUnavailabilityReason` at every `return null`, including the journal-integrity case.
- [ ] Commit latency for 1, 8 and 64 entries is measured before and after on APFS and recorded in the PR; if the 64-entry case regressed materially, D8's batching mitigation was taken deliberately and is documented here.
- [ ] Standard DoD: `npm run typecheck --workspace @0x-copilot/desktop`, the desktop suite, and the native suite pass on a darwin runner; tier 1 passes on the Windows runner and the PR says plainly that Windows behaviour is not yet exercised.

## Out of scope

- **Any repair.** No replay, no rollback, no auto-restore, no cross-entry unwind. FS-06 D7 already rejects cross-entry rollback; FS-07 does not reopen it.
- **The restore verb and the trash GC policy.** FS-04 owns `prepareLocalRestore`, `authorizeLocalRestore`, admission, budget and retention. FS-07 reports what is restorable and calls neither.
- **The verbs.** `delete`/`move` are FS-05; `replace` is FS-06. FS-07 classifies their crash points and adds none of them.
- **A clock in the helper.** Spine D5 settles it: main stamps the wall clock and the helper only compares. FS-07 adds no time-based rule and no seam member.
- **Presentation.** FS-09 owns the Settings surface, the Recheck button, the copy and the narration prompt. FS-07 ships the facts, the two channels and the audit family.
- **Per-entry detail across the ai-backend boundary beyond FS-06's `entry_results`.** No path tokens cross the broker.
- **Enabling `REPAIR_EXECUTION_ENABLED`.** The server repair lane stays as it is; FS-07 only makes what it calls correct.
- **Giving `rolled_back` a producer.** FS-04's local restore is its natural home.
- **Recovering from a lost or corrupted helper journal.** That stays fail-closed: the helper refuses to boot and C2 becomes unavailable with a typed reason.
- **Making `create` conclusive by changing its effect primitive.** See Open questions.

## Guardrails

- Do **not** turn an unobserved outcome into `applied`. `observedState` reports what is true of the workspace; `outcome` reports what this transaction caused, and the two are not the same field.
- Do **not** let `observed_state` or `evidence` influence `outcome` on either side of the wire.
- Do **not** mutate anything during reconciliation — no replay, no rollback, no auto-restore, no cleanup of workspace bytes.
- Do **not** write from `RECONCILE_OBSERVE`, including to the journal. Read-only is a structural property, not a convention.
- Do **not** weaken `journal_reconcile_startup`'s fail-closed abort, its prefix filter, or the rule that it runs before the first request.
- Do **not** re-walk a path outside `open_root`/`open_parent`, and do **not** relax `path_is_safe`, the exact-directory-entry rule, or the per-hop device check to make a reconciliation reach a target.
- Do **not** let main restate an observed pre-state; the observed half comes only from the MAC'd log.
- Do **not** trust a durable row alone — always re-observe, and let the pair decide.
- Do **not** delete a `c2c-` claim record, a preimage, or any object the helper cannot prove it created.
- Do **not** collapse an unresolved record with a timer, a boot counter, or any rule other than resolution or an explicit user action.
- Do **not** block all workspace writes because one record is unresolved; refuse only the overlapping path.
- Do **not** add an `enum outcome` member, a broker route, or an `ADVERTISED_METHODS` entry.
- Do **not** claim Windows behaviour that no Windows runner has executed.

## Open questions and cross-PRD conflicts

Recorded rather than guessed. The first three items were conflicts **between already-specified PRDs** that FS-07 surfaced because reconciliation has to find a preimage after a crash. **All three are now closed**, by the spine and by FS-04, and they are kept here as resolved rather than deleted so the reasoning survives — none of them is an open question any more, and none of them should be re-litigated in review.

1. ~~**The trash lives in three different places.**~~ **Closed by spine D4.** FS-04 D1's location — `<grant root>/.0xcopilot/trash/` — wins, and FS-05 D5 and FS-06 D2 now conform. The deciding argument is FS-04's: `open_parent` refuses any hop that crosses `st_dev` (`:392`), so a trash under the root is same-volume **by construction**, which makes displacement an O(1) rename on both platforms; `userData` is same-volume only by luck, and `command_prepare:850` already fails closed when it is not. The objections FS-05 raised were paid, not waived (reserved-and-unaddressable on the read surface as well as the write surface). Consequence for this PRD: D13's staging-pruning rule stands as written, and FS-06's `cleanup_prepared_stages` interaction is the `stage_consumed` skip in FS-06 D10.

2. ~~**The helper has no clock, and FS-04's GC needs one.**~~ **Closed by spine D5:** main stamps every wall-clock value and the helper only compares main-supplied numbers. `fs_time_ms()` was considered and deliberately **not** added to the seam, because it would put a value the helper cannot verify inside a MAC'd record and imply an attestation that is not there. The consequence is labelled wherever it matters: `journal_preimage_row.displaced_at_ms` is **main-attested, not helper-attested**, so it is evidence against drift and reordering and not evidence against a hostile main (FS-04 D4). FS-07 still adds no time-based rule and no clock dependency.

3. ~~**The per-entry durable record is specified three times.**~~ **Closed by [FS-04 §6a](PRD-FS-04-preimage-trash.md)**, which defines **one** per-entry commit-result block under `PROTOCOL 3`: FS-05 populates its `reason`, FS-06 populates its preimage fields, FS-07 appends `observed_state` and `evidence` under `PROTOCOL 4`. There is no `fs_preimage_row` and no third field list. FS-07's `effect_row` remains separate on purpose — it is append-only and per-effect, not per-preimage — and that separation is a design decision (D5), not an unreconciled duplicate.

4. **Should `create` be made conclusive by changing its primitive?** `fclonefileat` (:759-760) mints a fresh inode, which is the sole reason create and mkdir cannot reconcile conclusively while every rename-based verb can. `renameatx_np(staging_run_fd, stage_name, parent_fd, leaf, RENAME_EXCL)` would land the **sealed stage inode** at the target, making the post-state identity-checkable and closing D3.2 rows 4-6 entirely. It is atomic, no-replace, and same-volume is already guaranteed (:850). Costs and unknowns: it consumes the stage (changing `cleanup_prepared_stages`' contract, :716-730), it changes the created file's mode/ownership semantics (the stage is `0600`, :708 — though a clone already copies the stage's mode, so this may be a wash), it is a change to the primitive FS-01 explicitly froze in its zero-delta mapping table, and it would need FS-06's metadata carry-over (D8) applied to plain creates. **Recommendation:** a one-day spike measuring the mode/xattr/ACL delta between the two paths, then a decision in its own PRD. Do not fold it into FS-07.

5. **Should the boot sweep be allowed to run without a helper?** Today `createProductionWorkspaceAuthority` returns `null` in the unpackaged/dev posture (:84-92) and on any helper failure, so `sweep()` can do nothing and the user is told nothing. A read-only sweep that reports "there are N unresolved changes from a previous session, and we cannot check them in this posture" would need only main's encrypted journal. Small, and arguably belongs to FS-09's capability report rather than here.

6. **Windows rename durability (inherited spike).** [PRD-FS-05 D9 spike 3](PRD-FS-05-delete-move.md) and [PRD-FS-04 spike 3](PRD-FS-04-preimage-trash.md) both leave `FlushFileBuffers`-on-a-directory unresolved. FS-07 is written so classification never depends on it — the reconciler always re-observes, and D4's demotion rule catches a metadata rollback that contradicts an `OBSERVED` row. If the spike returns "not durable", nothing in FS-07 changes; if it returns "durable", the demotion rule becomes belt-and-braces rather than load-bearing on Windows. Either way the spike's answer belongs in FS-05, not here.

7. **Should an acknowledged-but-unresolved record eventually let its preimage be collected?** Today the answer is no, permanently, because FS-04 requires `JOURNAL_APPLIED` and reconciliation never writes it. That is safe and it means the trash can accumulate preimages that will never be eligible. FS-04's budget bounds the bytes, and D11's report bounds the surprise, but "permanently ineligible" deserves an explicit decision by whoever owns retention.
