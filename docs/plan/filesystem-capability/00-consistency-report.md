# Consistency and honesty pass — filesystem capability program

**Baseline:** `main@b349aca2` (2026-07-29). Every PRD in this directory was
drafted independently against [README.md](README.md). This document records what
a cross-PRD reconciliation pass changed, what is still genuinely open, and what
it found and deliberately did **not** fix.

A previous partial pass had already folded spine **D4** (trash location) and
**D5** (no clock in the helper) into the README, and had reconciled FS-01 and
FS-02. This pass finished the job across FS-03…FS-09 and re-checked FS-01/FS-02
against them. Nothing below was taken on the strength of a PRD's own assertion:
every code claim cited was re-read at the baseline commit.

**§1-§8 predate FS-08.** FS-08 did not exist when they were written (§4.5) and
was drafted afterwards, in isolation, against the README spine only. **§9 is a
separate, later pass** that reconciled it with the other eight; read §4.5 and §7
item 10 through §9, which supersedes them.

**§10 is a third pass — the ownership pass.** §1-§9 found two gaps they could not
close because each needed a **product call, not an edit**: cross-volume Windows
grants (§4.4) and FS-08's consent surface (§9.4). Both calls have now been made,
and §10 records what was decided, where it landed, and — importantly — what was
_not_ closed by them. Findings are superseded in place, never deleted: §4.4 and
§9.4 keep their original text with a superseding block, because the shape they
name (_the routing was to a reader, not to a document_) is the reusable part.

**§11 is a fourth pass — adversarial, and it tried to break §10 rather than
confirm it.** A pass that announces two gaps closed is the cheapest place in a
program spec for a false all-clear, so §11 attacked both closures. §10.2 held
(one stale sentence). §10.1 did **not**: it closed the create path and claimed
all paths, and two further doors to the same defect were open — read §10.1's
"there is no mint-then-fail path left" through §11.2, which supersedes it. §11
also found two ship-gate spikes that no register listed.

**FS-01 §2/§8 is the seam's normative text in every case below.** Where a
downstream PRD disagreed with it, the downstream PRD was changed.

---

## 1. Contradictions found and fixed

### 1.1 FS-06's Windows preimage was a hard link into the staging directory — three rules broken at once

**The biggest find in this pass.** FS-06 D2 had already been corrected (by the
earlier pass) so that macOS puts the displaced original in
`<root>/.0xcopilot/trash/`. **That correction was never propagated to D5**, so
the Windows lane still captured the preimage with
`NtSetInformationFile(target, …, FileLinkInformationEx)` into the _app-private
staging directory_. Three independent rules say no:

1. **Spine D4 / FS-04 D1** put the preimage in the trash. A second location is
   invisible to FS-04's GC, budget, marker check and `listRestorablePreimages` —
   the preimage would exist and be unrecoverable, which is worse than not
   capturing one.
2. **FS-04 D6 rejects hard links explicitly**, with the reason: a hardlink shares
   the inode, so a later edit to the restored file silently mutates the preimage.
   Here it was worse — the "preimage" would have shared an inode with the object
   the replacement was about to displace, so its recorded identity and digest
   (FS-04 D3's integrity anchor) would describe a live, mutable object.
3. It made Windows the only platform whose preimage is not in the trash: one
   verb, two recovery models — the divergence FS-01 D1 draws the seam to prevent.

**Fixed** in FS-06 D5 step 3: Windows uses FS-04's portable `stage_preimage`
(Strategy B) into the trash, with `staged_before_effect = 1`. The cost is stated
rather than hidden: Windows loses the single-OS-call atomicity a hard link would
have had and gains the same crash window macOS has, which is FS-07 D3.5's loud
case. A guardrail was added forbidding hardlinked preimages and preimages outside
the trash.

### 1.2 `fs_commit_replace`'s signature could not implement `fs_commit_replace`

FS-06 declared `int fs_commit_replace(fs_handle staged, fs_handle parent, const
char *leaf)`, mirroring `fs_commit_create`. That shape works for create because
`fclonefileat` takes a source FD. `RENAME_SWAP` is **name-based on both sides**,
so the POSIX body needs the staging directory handle and the stage leaf — which
the declaration did not carry. FS-06's own D2 step 7 then read back the displaced
object at `staging_run/stage_name`, values the seam member had no way to know.

Meanwhile the Win32 body needs the opposite input (the staged `HANDLE` it renames
over the leaf), and after FS-06's other correction the two platforms leave the
displaced object in _different places_: at the stage name on POSIX (after the
swap), already in the trash on Win32 (before the effect).

**Fixed:** the declaration now carries all four inputs plus an
`enum fs_replace_displaced` out-parameter naming where the displaced object is.
One declaration, two honest bodies. A FOURTH NOTE in FS-01 §2's effects block
records the shape so an implementer copying `fs_commit_create` is stopped.

### 1.3 FS-04 declared its own disposition vocabulary twice, incompatibly

FS-04 §3 declared `WorkspacePreimageDisposition = "retained" | "restored" |
"collected" | "indeterminate"`; FS-04 §6 declared `"none" | "retained" |
"restored" | "collected" | "unknown"` and called itself "likewise the only one".
Four members vs five, and a fourth member that differs. §3's version also
collided conceptually with `WorkspaceCollectOutcome`, which legitimately has an
`indeterminate` member.

**Fixed:** §3 now imports §6's union instead of redeclaring it. FS-04's test plan
and DoD, which said a crashed restore leaves the row `indeterminate`, now say
`PREIMAGE_UNKNOWN`.

### 1.4 FS-06 kept the retired `preimage_state` vocabulary in eight places

FS-06's interface section correctly said `enum preimage_state` is **deleted** and
`PREIMAGE_UNVERIFIED` becomes FS-04's `PREIMAGE_UNKNOWN = 4` — and then D3, D4,
D6, D7, D8, D13, the test plan and the DoD went on using `preimage_state`,
`PREIMAGE_UNVERIFIED` and `"unverified"`. This is a wire-level conflict, not a
naming preference: FS-06's `3 = UNVERIFIED` collides with FS-04's `3 = COLLECTED`.

**Fixed:** all fourteen occurrences conformed to FS-04's `enum
preimage_disposition` / `preimageDisposition` / `"unknown"`. A DoD item now
greps for `UNVERIFIED`.

### 1.5 FS-06's test plan asserted the preimage lands in the staging directory

Directly contradicted FS-06's own D2 correction. **Fixed** to
`<root>/.0xcopilot/trash/pre_<hex>`.

---

## 2. Seam drift fixed (FS-01 is normative)

| PRD                | Drafted as                                                                                                                   | Now conforms to                                                                                                                                                                                                                                                  |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FS-03 §4           | `fs_open_root` refuses non-NTFS/remote volumes and returns the identity                                                      | Provider **opens**; the volume gate is portable `supported_root_handle` over `fs_volume_supported`, and identity comes from `fs_stat_handle(...).id`. Folding policy into the provider puts it below the seam and lets the two gates drift.                      |
| FS-04 impl step 4  | `mkdir → open → private_dir_fd → marker O_EXCL`, "bounded readdir"                                                           | `fs_mkdir_at` → `fs_open_dir_at` → `private_dir_handle` over `fs_dir_is_app_private` → `fs_open_new_exclusive`; `fs_dir_for_each`. The raw spellings cannot appear in the portable TU (FS-01 §5) and `check-seam.mjs` fails the build on them.                   |
| FS-04 D6           | raw `renameatx_np` / `NtSetInformationFile` for restore                                                                      | `fs_rename_noreplace`; the raw calls are labelled as provider bodies.                                                                                                                                                                                            |
| FS-04 D9           | Win32 trash created with one `NtCreateFile(FILE_OPEN_IF)`                                                                    | `fs_mkdir_at` then `fs_open_dir_at`. The collapse is not cosmetic: `fs_mkdir_at` is contractually "fails if the name exists", which is what makes "we created it" distinguishable from "we adopted it" — and D3's whole adoption rule rests on that distinction. |
| FS-05 D3.2         | `fs_identity_of(source_pin, …)`                                                                                              | `fs_stat_handle(h, &meta)` then `meta.id` (FS-05's own conformance table already said so; the design body had not been updated).                                                                                                                                 |
| FS-05 D3.1         | `entry->parent_fd`                                                                                                           | `entry->parent` (FS-01's permitted-edit table renames every `*_fd` field).                                                                                                                                                                                       |
| FS-05 D3.5         | `entry->source.dev == prepared->root_dev`                                                                                    | `fs_identity_same_volume(&entry->source.id, &prepared->root_id)` — `struct snapshot` carries `struct fs_identity`, so comparing a bare `dev` is not expressible.                                                                                                 |
| FS-05 D2           | helper reads `ATTR_VOL_CAPABILITIES` directly                                                                                | `fs_volume_supports_rename_excl(root)`; `fgetattrlist` is the POSIX body.                                                                                                                                                                                        |
| FS-05 D2 / D9      | `SetFileInformationByHandle(FileRenameInfo, ReplaceIfExists = FALSE)` as a call site                                         | FS-04's `fs_rename_noreplace`, whose Win32 body is `FileRenameInfoEx` without `FILE_RENAME_REPLACE_IF_EXISTS`; non-`Ex` is a recorded fallback, not a second primary path.                                                                                       |
| FS-06 D2 steps 4-7 | `fsync` / `renameatx_np` / `fstatat` / `openat` in the portable TU; `stage_fd`, `parent_fd`, `sealed_stat`, `source.dev/ino` | `fs_durable_barrier`, `fs_commit_replace`, `fs_stat_at`, `fs_open_read_at`, `fs_identity_equal`; `stage`, `parent`, `sealed_meta`, `source.id`.                                                                                                                  |
| FS-06 D4           | rollback via raw `renameatx_np(RENAME_SWAP)`                                                                                 | `fs_commit_replace` — a rollback using a different primitive than the effect is a second write path.                                                                                                                                                             |
| FS-06 D8           | `fchmod` + `fcopyfile(COPYFILE_ACL\|COPYFILE_XATTR)` in the portable TU, no seam member                                      | `fs_carry_metadata(from, to)` — the spelling FS-01 §2 reserved for exactly this. Without a seam member, a Win32 provider could have shipped `replace` with the carry-over silently absent, i.e. a permission downgrade on one platform only.                     |
| FS-06 D11          | `volume_supports_swap(int root_fd)`, darwin-only, `fgetattrlist` in portable code                                            | `fs_volume_supports_swap(fs_handle)` on both providers.                                                                                                                                                                                                          |
| FS-09 D17          | Recheck calls `observeReconciliation`                                                                                        | `WorkspaceReconciler.recheck(claimId, "user_recheck")`; `observeReconciliation` is `LocalWorkspaceAuthority`'s internal entry point and the renderer never names it.                                                                                             |

---

## 3. Dependency and ownership errors fixed

- **FS-06 ↔ FS-05 both rewrite `commit_entry`, and neither depends on the other.**
  FS-05 defines a new signature (`struct prepared *`, index, entry,
  `uint32_t *reason_out`); FS-06 adds a `REPLACE` branch and its own
  `commit_replace_entry`. Nothing in the set said who wins. **Fixed:** FS-06 now
  carries the same ownership rule FS-03 D1 uses — whichever lands first writes
  the signature, the second adopts it and adds only its branch.
- **FS-06 never stated the FS-07 sequencing constraint.** The spine requires
  FS-07 with-or-ahead of FS-06 (FS-06's crash faults 9/10 are unreadable without
  FS-07's `c2e-` log, and D2's swap→relocation window is resolvable only by
  FS-07 D3.5). **Fixed** in FS-06's dependency note and DoD.
- **FS-05's Windows tests were attributed to "FS-03's runner".** FS-02 owns
  turning `native-workspace-commit-helper.test.ts:33` into a two-platform
  predicate and adding the `windows-latest` leg; FS-03 adds a separate
  confinement job. **Fixed.**
- **FS-04 said "Three seam members FS-04 itself adds" and listed four.** Fixed;
  D12 and the implementation plan already said four.
- **FS-02 D11 said "four private capabilities" and delivered five.** Fixed to
  match `fs_bootstrap_acquire`'s five (FS-01 §2).

No dependency **cycle** exists. The graph is: FS-01 → {FS-02, FS-03, FS-04} →
{FS-05, FS-06, FS-07} → FS-09, with FS-07's constraint against FS-05/FS-06 being
a shipping-order constraint, not an import edge, and FS-06's on FS-02 being a
real edge the README's nearest-edge column omits (FS-06 already documents that).

---

## 4. Gaps found

### 4.1 Fixed — the replaced-file preimage was un-restorable, and nobody said so

FS-04 D6's restore precondition is `{ exists: false }`. That is right for a
deleted file (its name is free) and **wrong for a replaced one** (its name is
occupied by the replacement). So every `RETAINED` row FS-06 produces would be
offered by `listRestorablePreimages` and refused at commit with
`PRECONDITION_DRIFT` — the receipt says "your previous version is kept" and the
button next to it always fails.

The README already flagged the shape; **no PRD carried it**. FS-04's Out of scope
covered only the directory case (FS-05). **Fixed:** written into FS-04's Out of
scope and into FS-06 as a Phase 1b release blocker, with two admissible
resolutions — (a) a `REPLACE`-from-preimage arm that takes its own preimage of
the replacement, or (b) `prepareLocalRestore` refuses an occupied `restorePath`
with a specific message — plus a DoD item. FS-06 picks; this pass does not.

### 4.2 Fixed — `WorkspaceConfinementEvidence.mechanism` could not name SPIKE-C2's outcome

FS-03 D3 ranks three Windows mechanisms and SPIKE-C2 asks whether a restricted
token is viable — but the evidence union was `"macos-seatbelt" |
"windows-appcontainer" | "windows-none"`. A successful SPIKE-C2 had nowhere to
report itself and would have had to masquerade as `windows-appcontainer`.
**Fixed:** added `"windows-restricted-token"`.

### 4.3 Fixed — the `FILE_ATTRIBUTE_HIDDEN` on `.0xcopilot` had no seam member

FS-04 D1 promises the trash is hidden on Win32 and D9 asserted the attribute,
but no seam member sets a file attribute and the portable trash-creation path
cannot use a `#ifdef` (FS-01 §5). **Fixed** by naming the choice explicitly in
FS-04 D9: either add `fs_dir_mark_hidden` (a POSIX no-op) or drop the hidden
attribute and let the dot prefix carry the cost. Marked unverified; FS-04 decides
before implementing. Not silently assumed to work.

### 4.4 ~~**Found and NOT fixed**~~ — **CLOSED by [FS-09 D19](PRD-FS-09-enablement-consent.md); superseded by §10.1.** A Windows grant on a second volume was silently ungrantable, and no PRD owned telling the user

> **Superseded, not deleted.** The finding below is exactly as this pass wrote
> it, because the shape it names — _the routing was to a reader, not to a
> document_ — is the reusable part and reappears in §9.4. What has changed is
> the disposition: the product call was made and FS-09 took it. The grant flow
> **refuses before minting**, naming the volume, offering read-only rather than
> imposing it, so no unusable grant is ever created and the approval sheet is
> unreachable for one. The larger half — per-volume app-private staging — is
> recorded as a separate future slice, with the helper invariant it would move.
> Where it landed, and what is still open from it, is **§10.1** — and **§11.2**,
> which found that refusing at mint time closed only one of three doors to this
> same defect, and closed the other two.

FS-02 D7's same-volume rule is correct and fails closed. Its consequence is that
on Windows — where staging lives under `%APPDATA%` on the system volume — a
folder picked on `D:` produces a grant that **looks granted**, passes
`listActive`, and fails at prepare with `workspace_write_unsupported` _after the
user has been shown an approval sheet_. That is precisely the failure mode FS-09
D18 rejects in its "grants that look granted and never work" row, arriving by a
different route.

FS-02 routed the follow-up "to FS-04/FS-09". **Neither document mentions
cross-volume grants anywhere.** The routing was to a reader, not to a document.

Not fixed here because it needs a product call, not an edit: the smaller, urgent
half (warn or refuse at grant time, before the grant is minted) belongs in FS-09
and would change its grant flow; the larger half (per-volume app-private staging)
moves where staged bytes live, which is a stated helper invariant
(`workspace_commit_helper.c:19-20`) and deserves its own slice. **Recorded** in
FS-02 D7 with an explicit "nobody owns it" note, in FS-02's open questions, and
as FS-09 open question 4 with a recommendation.

**Disposition (see §10.1).** The product call went the stricter way — **refuse**,
not warn — and [FS-09 D19](PRD-FS-09-enablement-consent.md) owns it. FS-02 D7's
ownership note and its open-question bullet now point at D19 and the same-volume
rule is unchanged; FS-09 open question 4 is closed in place; the staging half
sits in FS-09's Out of scope with the helper invariant it would move. The only
thing still open from this finding is **SPIKE-V1** (FS-09 open question 6), which
decides how `volumeId` is _spelled_ on Win32 — not whether the check happens.

### 4.5 **Found here, written later — FS-08 did not exist at the time of this pass**

**Superseded by §9.** When this pass ran, every other row of the README's PRD
table had a drafted document and FS-08 (local sandbox provider + patch-back) did
not. It is the _only_ thing that redeems locked decision **D1**, so until it was
written this program delivered filesystem reach with no execution story, and
D1's "`run_in_sandbox` is ~70% built, missing a local provider" claim was
unreviewed. Writing it was out of scope for this pass, and the finding is left
standing above rather than deleted, because a consistency pass that quietly
normalised a missing PRD would have been the wrong kind of tidy.

**FS-08 now exists** — [PRD-FS-08](PRD-FS-08-local-sandbox-provider.md), drafted
in isolation after this pass and reconciled against the other eight in a later
one. D1 is redeemed and its "~70% built" claim is reviewed and holds. See §9 for
what the later pass changed, including the fact that FS-08's own first-draft
dependency on FS-01 was wrong and that its consent surface was, at that point,
owned by nobody — **that last part is closed: §10.2 records the call, and FS-09
D20-D25 own it.**

### 4.6 Checked and genuinely covered — no verb, platform or failure mode is one-sided

Walked deliberately, looking for the "delete without its preimage" and "macOS
design with no Windows counterpart" shapes:

- Every verb has both providers, and FS-01 D9 makes that a link-time property.
- `PREIMAGE_LOCKED` is macOS-unreachable, but it is a _failure code_, not a verb;
  FS-04 D9 states this rather than papering over it.
- `open_holder_detection` differs by platform and FS-09 D15 reports per verb per
  platform rather than averaging.
- `FS_DIRECTORY_BARRIER_PROVEN` is 0 on Win32, and the consequence propagates
  intact: FS-01 §2 → FS-02 D8 → FS-05 D9 → FS-06 D2/D5 → FS-07 D4 → FS-09 C9.
- `fs_volume_supports_rename_excl` and `fs_volume_supports_swap` have **no
  capability bit to read on Win32**; both PRDs now say the Win32 answer is
  strictly weaker rather than claiming parity.

---

## 5. Overclaims downgraded

Every Win32 or macOS behavioural claim in the set was checked for grounding. The
macOS claims in FS-05 (probes 1-19) and FS-06 (verified facts 1-10) are
single-host observations and both PRDs already say so; FS-06 D1 exists precisely
because one host is not the support matrix. Those were left as they are.

Downgraded or given a named spike by this pass:

| Claim                                                                                                                          | Where                                                           | Now                                                                                                                                                                                                                                                                                                                   |
| ------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `fcopyfile(COPYFILE_ACL\|COPYFILE_XATTR)` between two descriptors carries an APFS ACL and every xattr without touching content | FS-06 D8 (stated as fact)                                       | `unverified`; added to D1's probe set. It must not change `dev`/`ino`/size/content or `sealed_stage_matches` breaks.                                                                                                                                                                                                  |
| Win32 `GetSecurityInfo`/`SetSecurityInfo` + `GetFileTime`/`SetFileTime` reproduce the displaced object's DACL                  | FS-06 D8/D5 (stated as an equivalent)                           | `unverified`; explicitly the _effective_ DACL vs the explicit one, which is FS-02 SPIKE-W2 asked from the other direction. A carry-over that drops inherited ACEs is the exact failure D8 exists to prevent.                                                                                                          |
| `FileRenameInformationEx` + `FILE_RENAME_POSIX_SEMANTICS` availability, `RootDirectory` honouring, separator rejection         | FS-05 D9 spike 1, FS-02 D2, FS-06 D5 (three partial statements) | One shared spike (FS-05 D9 spike 1), extended with the separator case — the confinement property — and the occupant-kind matrix FS-02 D2 property 3 needs. Two documented fallbacks in order: `NtSetInformationFile(FileRenameInformationEx)`, then non-`Ex` `FileRenameInfo`, recorded as taken rather than assumed. |
| `.0xcopilot` carries `FILE_ATTRIBUTE_HIDDEN`                                                                                   | FS-04 D1/D9                                                     | `unverified` — there is no seam member for it (see 4.3).                                                                                                                                                                                                                                                              |

Three **duplicated** spikes were consolidated rather than downgraded, because
running the same experiment three times is how three answers happen:

- Directory-entry durability on Win32: FS-04 spike 3 and FS-05 D9 spike 3 now
  both point at **FS-02 SPIKE-W1**, and both record that the _design_ does not
  depend on its outcome (FS-01 already fixed the position at "not provable"); the
  spike may only make the wording more cautious, never less.
- `FILE_ID_INFO` stability: FS-05 D9 spike 4 now points at **FS-02 SPIKE-W7**.
- `RootDirectory`-relative rename: as above, one spike, three consumers.

---

## 6. Duplication removed

- **FS-07 open questions 1, 2 and 3 were resolved decisions presented as open.**
  (1) the three-way trash-location disagreement — closed by spine D4; (2) the
  missing clock — closed by spine D5 (main stamps, helper compares, and the
  timestamp is labelled main-attested); (3) "the per-entry durable record is
  specified three times" — closed by FS-04 §6a defining one block. Rewritten as
  closed-with-reasoning rather than deleted, so the argument survives and so a
  reviewer does not re-open them. FS-07's Context and Out of scope were updated
  to match on the clock.
- **`ReplaceFileW` vs handle-relative displacement was argued in three places
  with three different statuses** (FS-04 D9 "the choice is still open", FS-06 D5
  "rejected as primary", FS-07 D3.5 "FS-06 should weigh that", README "the choice
  is still open"). FS-06 D5 made the decision on confinement grounds; the other
  three now say so and reduce to primary/fallback. `staged_before_effect` still
  models both because the fallback can still be taken.
- **Directory-barrier durability** was argued at length in FS-02 D8, FS-04 spike
  3, FS-05 D9 spike 3 and FS-06. It belongs to FS-01 §2 + FS-02 D8; the others
  now reference.

---

## 7. Still genuinely open (not defects — decisions and spikes)

These need answers before the PRDs that depend on them ship. None is a
consistency problem; all are recorded in their owning PRD.

1. **The Windows code-signing certificate.** Shared prerequisite of FS-02 D15 and
   FS-03 D9. Without it, FS-02 + FS-03 together ship a capability nobody can turn
   on. Product decision.
2. **SPIKE-C1 — can an AppContainer child serve and use loopback without
   elevation?** If not, Windows confinement is `mechanism_unavailable` and
   Windows writes stay off (FS-03 D3), which makes FS-02's helper reachable only
   from tests.
3. **SPIKE-W3 — extra-fd capability delivery on Windows.** Gates everything else
   in FS-02.
4. **FS-06 D1 (S1-S7) — `RENAME_SWAP` across the real support matrix.** FS-06's
   macOS design is contingent on it, and S1 failing on a shipped floor stops the
   PRD.
5. **The minimum supported Windows build.** Unstated by the project; it decides
   whether `FileRenameInfoEx` and `NtQueryDirectoryFileEx` are available and
   therefore which of three rename paths ships. FS-02 owns pinning it.
6. **The macOS CI runner budget** (FS-01 open question 1). Every later PRD
   inherits the answer; a golden transcript nobody runs is documentation.
7. **Directory-preimage restore** — FS-05's release blocker, two admissible
   resolutions, FS-05 picks.
8. **Replaced-file preimage restore** — FS-06's release blocker (§4.1), two
   admissible resolutions, FS-06 picks.
9. ~~**Cross-volume grants on Windows** — §4.4, currently unowned.~~ **Closed
   (§10.1):** owned, and the answer is _refuse at grant time_ —
   [FS-09 D19](PRD-FS-09-enablement-consent.md). What survives from it is
   **SPIKE-V1** (FS-09 open question 6), a spike about the Win32 spelling of
   `volumeId`, plus the per-volume-staging slice, which is deliberately not
   designed and is recorded as such in FS-09's Out of scope.
10. ~~**FS-08** — §4.5, not drafted.~~ **Closed:** FS-08 is drafted and
    reconciled (§9). It brought three new open items with it, listed below.
11. ~~**FS-08's consent surface is owned by nobody** — §9.4.~~ **Closed
    (§10.2):** FS-09 dropped the FS-08 exclusion and owns the consent surfaces as
    [D20-D25](PRD-FS-09-enablement-consent.md); FS-08 keeps the provider,
    runtime, isolation and patch mechanics. What survives is a **shipping-order**
    constraint, not an ownership gap: no FS-08 phase yields a user-reachable
    capability until FS-09's execution half lands — the same shape as item 1.
12. **Whether an agent-authored change set may commit without a
    `decisionLedgerId`** — §9.3, FS-08 open question 8, recommendation recorded.
    **Still open, and now unambiguously FS-08's:** FS-09 declines it by name
    (§10.2) because it is about what is recorded server-side, not about what a
    human is asked. FS-09 binds only the copy in the meantime.
13. **The `als` command-budget defect** — §9.2. Real today, on every provider;
    FS-08 D8(a) is the chosen fix and it edits a file FS-08 otherwise consumes.
14. **SPIKE-L2 — can a Windows container runtime observe all ten isolation
    controls?** Added to this list explicitly because it is the one spike in the
    program that decides whether a whole PRD ships: verbs land on both platforms
    or neither, so a Windows negative takes macOS execution with it (FS-08 D4).
    See the spike register in [README.md](README.md).

---

## 8. What this pass did not do

- It did not run any spike, and it did not upgrade any `unverified` marker.
- It did not re-litigate D1 (local sandbox), D2 (Windows first), D3 (off by
  default), D4 (trash location) or D5 (no clock).
- It did not change any source file. The only files touched are the PRDs in this
  directory and the README.
- It did not resolve items 7-10 of §7. Each has a named owner and admissible
  resolutions; picking for them would be the wrong kind of tidy.

---

## 9. FS-08 reconciliation (later pass)

FS-08 was drafted in isolation after §1-§8 were written, against the README spine
only, while the other eight PRDs had already been reconciled with each other.
This section records a later pass that conformed it to them. Same rules as
before: **FS-01 §2/§8 is normative for the seam**, FS-04 owns the preimage/trash
substrate, and every code claim below was re-read at `main@b349aca2` rather than
taken on FS-08's assertion.

The headline is uncomfortable and is stated first: **FS-08's declared dependency
was wrong, its C4a fix did not work, and the document it routed every consent
surface to disclaims it by name.**

### 9.1 Seam drift — FS-08 claimed a dependency FS-01 explicitly disclaims

FS-08's header read: "Depends on: FS-01 (read — the confined base-file read …
and the `fs_open_root`/`fs_stat_at` primitives behind it)", `Interfaces consumed`
listed "FS-01's seam, only through FS-01's own read surface", and D17.1 built the
kill switch's first authority on "base entries read through FS-01's confined
read".

`fs_open_root` and `fs_stat_at` are correct [FS-01 §2](PRD-FS-01-platform-seam.md)
spellings, so this was not a name drift — it was worse. Three facts:

1. **FS-01's Out of scope excludes the whole read path** — "The
   `native/workspace-fs` N-API read-side addon, `host-fs.ts`, and everything on
   the read path" — **and excludes FS-08 by name** on the next line. FS-01 has no
   read surface to depend on.
2. Those primitives live inside the **commit helper**: a spawned, single-purpose
   C process that speaks one MAC'd command protocol over a private channel to
   desktop main (FS-01 §2's `fs_bootstrap_acquire`). It has no read request type,
   nothing above the seam calls them, and a Python service cannot reach them at
   all.
3. The read surface FS-08 actually needs already exists and is the broker's:
   `/v1/fs/{stat,list,read,glob,grep}`
   ([broker.ts:80-94](../../../apps/desktop/main/capabilities/broker.ts)),
   consumed from ai-backend by
   [broker_client.py:88-92](../../../services/ai-backend/src/agent_runtime/capabilities/desktop/broker_client.py).

**Fixed** in the header, `Interfaces consumed`, D17.1, the implementation plan
and the README table. The dependency is now **FS-03**, hard, on Windows — because
`native/workspace-fs`'s Win32 walk "is built by no script, is carried by no
`extraResources` entry" ([README](README.md), FS-03 C3), so a packaged Windows
install reads base files through the non-atomic `realpath`-recheck fallback until
FS-03 lands. FS-08's first draft named FS-03 nowhere. FS-01 is now a **negative**
dependency, with a guardrail and a DoD item: no seam member, no verb in
`fs_platform.h`, no file under `apps/desktop/native/` touched.

Two further constraints were added to D17.1 that the first draft dropped:
`sandbox_snapshot_authority.py`'s own docstring says keeping the adapter in the
worker "makes it impossible for a model tool to select a live workspace manifest
**or a host filesystem path**", and `SandboxSnapshot`'s contract forbids a host
path, grant, broker handle, root identity or credential from appearing in it
([contracts.py:202-209](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py)).
Base entries therefore reach the sealed store as `(virtual_path, source_ref)`,
exactly as overlay entries do.

**Also fixed, smaller:** FS-08's `Interfaces consumed` claimed FS-04's `origin`
field as a declared union it could add a member to. FS-04 introduces `origin` in
prose only (§5's doc comment, D6) and declares no type for it; `WorkspaceChangeSet`
on `main@b349aca2` has no such field
([workspace-authority.ts:92-105](../../../apps/desktop/main/capabilities/workspace-authority.ts)).
FS-08 now carries the same first-lands-declares ownership rule FS-03 D1 and FS-06
use for `commit_entry`.

### 9.2 A second writer — into C1, not into the host — plus a verified defect in the fix that was supposed to prevent one

Checked first, because the spine forbids it. **The host write path is clean**:
D12's chain runs importer → C1 overlay revision → `prepareSandboxPatchImport` →
`uploadPreparedContent`/`sealPreparedContent` → `authorizeSandboxPatchImport` →
`commitPreparedChangeSet` → the helper. No bind mount (D8 rejects it and cites
`SandboxSnapshot`'s contract), no host path in the provider, no shell, no verb.
FS-08 emits the five verbs and lets FS-02/04/05/06 redeem them. That half was
right.

**The second writer is one layer up, and FS-08 did not notice it.**
`WorkspaceOverlayStorePort.append_revision`
([workspace/ports.py:63-70](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/ports.py))
has exactly one production caller today: `_WorkspaceOverlayMutationEngine`
([overlay.py:113-568](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/overlay.py)),
reachable only through the operation gateway
([operation_port.py](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/operation_port.py),
[effects.py:254](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/effects.py)).
`OverlaySandboxPatchImporter` called the store directly, which drops four things
that exist nowhere else:

- `_check_limits` (`overlay.py:601-617`) — entry-count and total-byte ceilings.
  Without it the import is the **only unbounded path into C1**.
- `_precondition_for_base` (`:570-592`) — which fills `opaque_generation`,
  `stable_file_id`, `byte_size` and `mtime_ns` besides `content_digest`.
- `_merged_entry_exists` (`:595-599`) — existence across overlay **and** base.
- the gateway's operation record and disposition.

The precondition half is the one that bites silently: `BasePrecondition`'s
validator
([workspace/contracts.py:200-241](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/contracts.py))
only _requires_ `entry_kind` and, for a file, `content_digest`. So FS-08's
mapping table — which had only `baseline_digest` to work with — was
**constructible and strictly weaker than every other proposal in the system**,
all the way through to the desktop's `WorkspacePrecondition.stableId` and
therefore to what the helper re-checks at commit. Nothing would have failed
loudly.

**Fixed:** a new Context section (FS-08 C10) establishes the monopoly; D12 gains
two more invariants (re-apply the ceilings for the whole batch before appending;
recover the full `BasePrecondition` from the retained baseline manifest, which
the importer already has via `baseline_overlay_ref`); the bypass is declared
rather than accidental, noting that `SandboxPatchImportPort` is a **pre-existing**
seam (`ports.py:329`) whose docstring intends exactly this shape, so the second
writer is sanctioned — but the operation record it cannot inherit is not
fabricated. Tests pin field-for-field precondition parity and a two-caller
`append_revision`.

**And the C4a fix was inert.** FS-08's D8 claimed that implementing `ls` natively
on `LocalContainerBackend` stops `als` charging the command budget. Traced:
`PolicyEnforcedSandboxBackend` **is itself a `BaseSandbox`**
([policy_backend.py:69](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/policy_backend.py))
and overrides `ls` only to `_guard_path` then `super().ls` (`:166-168`); pinned
deepagents' `BaseSandbox.als` is `await self.aexecute(_build_ls_cmd(path))`, not
the `to_thread(self.ls)` form FS-08 cited from `BackendProtocol`; and the
collector calls `als` on the **façade**
([patch_collector.py:63](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/patch_collector.py),
`ActiveSandbox.backend` at
[remote_execution_service.py:77-84, 169](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/remote_execution_service.py)).
The delegate's `ls`/`als` are dead code on that path and the budget is charged
regardless. FS-08's own regression test (`after 200 als calls, commands_used ==
0`) would have failed — the right test against the wrong design.

**Fixed:** C4a rewritten by symbol rather than by line number, D8 rewritten with
three admissible fixes and a pick — (a) the façade prefers a delegate-supplied
`ls`/`als` after `_guard_path`, using the duck-typed idiom it already uses for
`a_upload_files`/`a_download_files`/`prepare_execution` — plus its own
implementation step, its own tests (including that a delegate _without_ a native
`ls` still charges, and that a deep tree still terminates through
`max_upload_files`), and the withdrawal of D1's "no change to
`PolicyEnforcedSandboxBackend`". Option (c), raising `commands_per_session`, is
rejected twice: it raises a control to make collection work, and the field is
capped at 256 while `download_file_count` admits 10 000.

### 9.3 Overclaims downgraded

Every Win32/macOS/container behavioural claim in FS-08 was checked for grounding.
Downgraded or given a named spike:

| claim                                                                                     | where                                                                               | now                                                                                                                                                                              |
| ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Apple `container` is a microvm — "per-container lightweight VM", "narrowest host surface" | D2 table, §3, D6                                                                    | `unverified`. SPIKE-L5 gains an isolation-class half; if no bounded observation distinguishes the boundary, the driver declares `"container"` — accepted identically             |
| `isolation` is attested per-run                                                           | implicit in D6                                                                      | stated: `isolation_kind` is a **compile-time constant**, so the first term of `satisfies()` is the **one term the probe never observes**. Said in D2, D6, §3, §4 and a guardrail |
| podman is "rootless by default; no long-lived daemon on the host"                         | D2 table                                                                            | `unverified` and half-wrong on these two platforms — `podman machine` _is_ a long-lived host VM                                                                                  |
| "AppContainer / restricted token / low IL **+ Job**"                                      | D2 table                                                                            | "+ Job" removed. FS-03 D3 ranks exactly three mechanisms; a Job object is a resource control, not an isolation boundary                                                          |
| tmpfs pages are charged to the container memory limit                                     | D5 (load-bearing — it is the only reason `LocalContainerConfig`'s validator exists) | `unverified`, **SPIKE-L6**, with both outcomes stated                                                                                                                            |
| `--storage-opt size=` depends on the storage driver                                       | D5                                                                                  | `unverified`, folded into SPIKE-L2. A negative result changes nothing structural — it is only cited as a reason to prefer tmpfs                                                  |
| "No runtime flag reliably kills a long `exec` on all three runtimes"                      | D5                                                                                  | `unverified`, folded into SPIKE-L2. A positive result does not move the timer off the provider; it removes a justification sentence                                              |
| The D5 flag table reads as portable argv across three runtimes                            | D5                                                                                  | prefaced: these are docker/podman's documented spellings, `unverified` for `apple_container` (SPIKE-L5) and on the WSL2 backend (SPIKE-L2)                                       |
| "nothing in this program prompts for elevation"                                           | D3                                                                                  | kept, with the caveat it lacked: SPIKE-L2 expects `wsl --install` to need elevation once, performed by the user outside the app                                                  |

FS-08 already marked `sandbox-exec`'s deprecation `unverified` (SPIKE-L1) and
was left as it stood. Its macOS/Windows confinement statements cite FS-03 rather
than re-deriving, and were correct: FS-03 D3 does say low IL "denies reads?
**no**", and does reject Windows Sandbox/Hyper-V for "Pro/Enterprise and
virtualisation".

**One design gap found while checking the probe**, not an overclaim: D6's
observation 9 asserts a setuid escalation attempt fails, and D7's image
requirements listed four items — none of them a setuid binary. Without a subject,
"the escalation failed" is indistinguishable from "nothing was attempted", which
is the configured-not-observed failure D6 exists to prevent. The image
requirement list is now five and the DoD counts five.

### 9.4 Gaps found — the routing that went to a reader, not a document

**FS-09 disclaims FS-08 by name.** Its Out of scope: "The sandbox provider and
patch-back (FS-08)." It mentions no sandbox, container, image, runtime or
execution surface anywhere. FS-08 routed **six** surfaces to it:

readiness-reason rendering (D16); "what to install" (D3); image acquisition with
its size and consent (D7); review of an imported overlay revision before prepare
(D12); the import affordance (§7, Phase 6); and the pre-approval warning that a
patch's verbs cannot commit on this platform (Out of scope).

This is §4.4's shape exactly — _the routing was to a reader, not to a document_ —
and it is more consequential here, because a provider that clears all six
readiness gates still yields no user-reachable capability without them. **Not
fixed**, because it is a scope decision: either FS-09 drops the exclusion or a
tenth PRD takes them. **Recorded** in FS-08's dependency header, in D3, D7, D16,
Phase 6 (which now says plainly that it cannot be planned), a new "Unowned
surfaces" table in FS-08's open questions, a DoD item that cannot be ticked until
a document owns them, and §7 item 11 above.

> **Superseded — CLOSED by [FS-09 D20-D25](PRD-FS-09-enablement-consent.md); see
> §10.2.** The scope decision was made and it went the first way: FS-09 dropped
> the exclusion. Execution consent is consent, and splitting it across two
> documents would have produced two consent models. FS-09 grew an execution half
> (D20 switch, D21 reasons, D22 image download, D23 what leaves the folder, D24
> import review + verb pre-check, D25 revoke with a live sandbox) and took three
> surfaces FS-08 had not routed at all. FS-08 keeps the provider, runtime,
> isolation and patch mechanics. Every "unowned" marker listed above is updated:
> the dependency header, D3, D7, D12's chain, D16, Phase 6, the DoD item (now
> ticked, with a new unticked item for FS-09's execution half having shipped),
> and that table — which is retained, renamed **"Consent surfaces — routed, and
> where they landed"**, so the routing failure stays legible next to its fix.
> **Two things did not close and are not pretended to have:** FS-08 open question
> 8 (the `decisionLedgerId`) is declined by FS-09 by name and stays FS-08's, and
> whether an _imported_ revision reaches `projectWorkspaceStage` is still
> unverified (FS-09 open question 6 covers volume identity; **question 8** covers
> this one) — if it does not, the wiring is FS-08's mechanism.

**`baseline_overlay_ref` has no carrier.** FS-08 introduced the field on
`SandboxPatchImportRequest` and said `coordinator.import_patch` "gains the
matching parameter". But `import_patch(self, result: SandboxRunResult)`
([coordinator.py:213-232](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/coordinator.py))
builds the request from `result` alone, and `SandboxRunResult`
([contracts.py:626-638](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py))
carries no overlay ref — `WorkspacePatchManifest` has `baseline_manifest_sha256`,
a manifest digest, not a C1 version. **Fixed** by naming two resolutions and
picking: an explicit second argument, so the redaction-safe terminal projection
is not widened to carry C1 pointer state. D1's "one additive field" is corrected
to two contract edits.

**The unshipped-verb refusal was described in the wrong place and at the wrong
granularity.** FS-08's Out of scope said a patch whose verbs have not shipped "is
refused at commit by the existing helper … and the commit says what the platform
cannot do". `parse_entry` refuses `REPLACE`/`DELETE`/`MOVE` with a bare
`goto fail`
([workspace_commit_helper.c:801](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)),
and `parse_entry` runs inside **`command_prepare`**. So the refusal is at
prepare, it fails the **entire change set**, and it produces one undifferentiated
failure that says nothing about which verb. Combined with FS-08 D14's
no-partial-import rule, a single `delete` entry makes a whole import unusable on
a build where FS-05 has not landed — after the user has already seen a reviewable
proposal. **Fixed** in the text; the pre-approval warning it implies was one of
the six unowned surfaces, and is now
[FS-09 D24](PRD-FS-09-enablement-consent.md)'s verb pre-check — which **gates the
approve control rather than warning beside it**, because the whole set would
fail and a disabled-with-a-warning control would imply the user could clear it.

**An imported change set commits with no ledgered decision.**
`authorizeCommitFromUserDecision` binds a permit to `stageId` / `revision` /
`decisionLedgerId` by exact comparison
([workspace-authority.ts:601-627](../../../apps/desktop/main/capabilities/workspace-authority.ts)).
FS-08's `authorizeSandboxPatchImport` mints one from `{ confirmedByUser: true }`
alone, mirroring FS-04 D6's `authorizeLocalRestore`. **The mirror is not
symmetric**: a restore is one main-authored entry over a preimage row main owns,
whereas a sandbox patch is authored by code whose inputs include MCP-ingested
content. So an imported mutation would be the only agent-authored mutation in the
system reaching `commitPreparedChangeSet` without an approval row. FS-08's own
guardrail — "do not let … a local confirmation redeem an agent proposal" — says
so in its own words. **Still not fixed**: it needs a product call, and the §10
ownership pass did not make it — FS-09 declines it by name, because it is about
what is recorded server-side rather than what a human is asked, and binds only
the copy in the meantime (the review may not say "approved and recorded" when no
approval row exists). Written into FS-08
§7 and open question 8 with two admissible resolutions and a recommendation
((a): the desktop mints its own decision record and binds it, because the audit
questions this program is held to have no answer under (b)). §7 additionally now
requires the proposal identity (`changeSetDigest`/`targetDigest`/
`proposalDigest`) to be **derived** from the immutable overlay revision, and
`reviewedChangeSetDigest` to be re-checked against it, or the field is
decorative.

**"Existing review surface" is unverified.** D12's chain asserted one for an
imported overlay revision on desktop. The code's own docstring names "A4/A5
review" ([contracts.py:330-337](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py)),
but nothing in this program supplies it and this pass did not confirm one exists.
Marked in the chain diagram and listed among the unowned surfaces.
**Half-closed by §10.2:** [FS-09 D24](PRD-FS-09-enablement-consent.md) names the
surface — `TcWorkspaceStageSurface` via `projectWorkspaceStage`, the stage card
that already exists, not a second one — and requires it rather than a new
projection. What is **still unverified** is whether an _imported_ revision
reaches that projection at all; FS-09 open question 8 names the one-file check,
and if it does not, the wiring is FS-08's mechanism. The chain diagram now says
that, instead of "UNOWNED".

### 9.5 Duplication removed

- **README D1 was re-argued three times** — in D2's first rejection bullet, in
  D18's closing paragraph, and in D19's first bullet. D18 now references the
  spine and keeps only what is genuinely FS-08's: the six-bullet statement of
  what this sandbox cannot do, framed as invariants rather than defaults. D2
  keeps only the mechanism-specific point D1 does not cover (a process sandbox
  leaves the home directory readable unless every path is enumerated).
- FS-08's C8 was checked for the same problem and left alone: it cites FS-03 C2
  W1, FS-03 D2, FS-03 D8 and FS-03 D3 rather than re-deriving them, and the two
  facts it carries forward (observed denial; absolute main-computed launcher
  paths) are applied to new objects.

### 9.6 Checked and found sound

Not everything was wrong, and the parts that hold are the load-bearing ones:

- Every code citation in FS-08's Context was re-read. The seven gates, the
  `SandboxGuardedProvisioner` duck-typing hazard, `satisfies()`'s nine-control
  conjunction, `"process"` being declarable and refused, the unconditional
  `return None` kill switch, `deliverables=()` and `_publish_result`'s refusal,
  the reference-only snapshot, `provider_session_ref`'s no-slash pattern, the
  five-name verb vocabulary in three places, and the missing-mount divergence in
  `normalize_virtual_path` are all accurate as written.
- `SandboxPatchImportPort` really does already exist (`ports.py:329`), so FS-08's
  "no new `Protocol` in `ports.py`" survives.
- Neither `langsmith` nor `openai_hosted` has ever returned
  `isolation_ready == True`, so FS-08's claim to be the first is correct.
- D13's refusal to add a fourth `WorkspaceContentSource` member conforms to
  FS-04 §2, and §7's prepare/authorize pair is a faithful mirror of FS-04 §5's
  shape — the asymmetry is in _what may be redeemed_, not in the interface.
- No dependency cycle is introduced. FS-08 sits downstream of
  {FS-02, FS-03, FS-04, FS-05, FS-06, FS-07} and upstream of nothing.

### 9.7 What this pass did not do

- It ran no spike and upgraded no `unverified` marker; it added three
  (SPIKE-L5's isolation half, SPIKE-L6, SPIKE-L2's extensions) and downgraded
  nine claims.
- It did not decide the consent-surface owner (§9.4), the decision-ledger
  question (§9.3/§9.4), or whether D17.1's base-file exporter belongs to FS-08 at
  all — that last one now sits on FS-03's boundary rather than FS-01's, which
  strengthens the case for moving it. **§10 decided the first of those three.**
  The other two are still open and are still recorded where they were.
- It changed no source file. The files touched are `PRD-FS-08`, `README.md` and
  this report.
- It did not re-open FS-01…FS-07 or FS-09. Where FS-08 disagreed with them,
  **FS-08 was changed** — including where FS-08 was the only document that had
  noticed something, in which case the finding was kept and its owner named.
  **§10 is the pass that finally re-opened FS-09**, which is what closing §9.4
  required.

---

## 10. Ownership pass — the two product calls, and what they did not close

**Baseline:** unchanged (`main@b349aca2`); every code line cited below was
re-read at it. This pass ran after §9 and exists for one reason: §4.4 and §9.4
were both **"found and NOT fixed"** because both needed a product decision, and a
program spec that carries two unowned surfaces indefinitely is a spec that has
decided by default. Both were decided. This pass changed **no source file**; the
files touched are `PRD-FS-02`, `PRD-FS-08`, `PRD-FS-09`, `README.md` and this
report.

Two shapes recur below and are worth naming, because the second is the more
dangerous one:

- **Routing to a reader, not a document** (§4.4, §9.4) — PRD A says "B owns
  this", B has never heard of it. Detectable by grep, and both instances are now
  fixed by making B say it.
- **A refusal that arrives after the consent** — the actual user-visible defect
  behind §4.4. A grant that mints, lists, and passes an approval sheet before
  failing is worse than one that never mints, because the user has already been
  asked to agree to something that cannot happen.

### 10.1 Cross-volume grants — refused at grant time (closes §4.4)

**The call: refuse before minting.** Not "warn then mint", and not "silently
downgrade to read-only". [FS-09 D19](PRD-FS-09-enablement-consent.md) owns it,
and the shape is:

- The check runs **twice, both times before a grant row exists** — a probe in
  `CapabilityService.requestFolderGrant` (so the refusal is a typed choice that
  can offer read-only) and enforcement in `GrantStore.create` right after
  `assertGrantableRoot` (the store's own comment already calls that the
  authoritative choke point for a caller bypassing the native picker). Because
  no row is created, `listActive` cannot show one and the per-commit approval
  sheet is unreachable for one. ~~**There is no mint-then-fail path left.**~~
  **Overstated — corrected in §11.2 below.**
  These two sites close the path through which a grant is _minted_; two other
  doors to the same artifact were open, and D19 grew §8 and §9 for them.
- **Write modes only.** Reads never touch the commit helper, so a `read_only`
  grant on a second **supported** volume is minted normally and works
  completely. (On a volume the helper cannot open at all, read-only could not be
  minted either until §11.3 — same correction.)
- **No silent downgrade.** Read-only is _offered_ as an explicit second request,
  never imposed — a grant whose mode is not the mode the user chose is the same
  defect class as copy naming a verb the build cannot perform.
- **One producer of volume identity**, `NativeWorkspaceAuthority.rootIdentity`,
  already called by the store. `fs.statSync().dev` in main is banned as a second
  producer with a different encoding.
- **FS-02 D7 is unchanged.** The same-volume precondition at
  `workspace_commit_helper.c:850` stays exactly as specified and remains the
  enforcing check. D19 asks the same question earlier, not differently, and
  explicitly does not relax it to make a `D:` workspace writable.

**What this did NOT close, stated plainly:**

1. **Per-volume app-private staging is a separate future slice** — the larger
   half of the original finding. It moves where staged bytes live, which is a
   stated invariant of the helper's header
   (`workspace_commit_helper.c:19-21`, fd 4 at `:11`), and brings its own consent
   step and its own `fs_dir_is_app_private` proof on a volume the app does not
   own. It is recorded in FS-09's Out of scope and in FS-02 D7 item 2, and is
   **designed by neither**. Nothing in FS-02 or FS-09 depends on it existing.
2. **SPIKE-V1 is open** (FS-09 open question 6). On Win32 the comparison is of
   two 16-hex `FILE_ID_INFO.VolumeSerialNumber` values (FS-02 D6). Serial
   equality is _necessary_ for same-volume; it is not proven _sufficient_,
   because a cloned or imaged volume can present a duplicate serial — and that
   fails in the dangerous direction, letting a cross-volume grant through to die
   at prepare. If the spike forces the volume-GUID path, the encoding that
   changes is **FS-02 D6's**, because `volumeId` is persisted inside grants.
   Until it runs, every Win32 statement says "same volume **serial**".

**Where it is recorded:** FS-09 D19 (with C10, Interfaces §8, implementation
steps, tests, DoD, guardrails); FS-02 D7's ownership note, FS-02's open-question
bullet and Out of scope; FS-09 open question 4 closed in place; §4.4 above; §7
item 9; the README's spike register.

### 10.2 FS-08's consent surfaces — FS-09 owns them (closes §9.4)

**The call: execution consent is consent.** Splitting it across two documents
would produce two consent models, which is precisely what this program exists to
prevent. FS-09 dropped "The sandbox provider and patch-back (FS-08)" from its Out
of scope and grew an execution half; FS-08 keeps the mechanism.

| FS-09 owns (the ask)                                                   | FS-08 keeps (the mechanism)                                 |
| ---------------------------------------------------------------------- | ----------------------------------------------------------- |
| D20 — enabling execution: its own switch, not derived from the FS lane | the provider, the runtime and its drivers                   |
| D21 — the reason rendering, and "what to install" without installing   | D16's reason strings; §2's driver registry                  |
| D22 — the image-download ask, once, with the size, before a byte moves | D7's digest pin, expected size, driver argv                 |
| D23 — what leaves the granted root, stated before it leaves            | the snapshot exporter and D17.1's base entries              |
| D24 — the import review, and the unsupported-verb pre-check            | §7's prepare/authorize lane, the C1 importer, `parse_entry` |
| D25 — revoking while a sandbox is live                                 | D9's kill, `cleanup_pending`, `liveSessionCount`            |

The boundary is not a hand-off of a list: **FS-09 took three surfaces FS-08 had
never routed** (D20, D23, D25), which is the sign the line is drawn in the right
place. The dependency runs one way — FS-08's code depends on nothing in FS-09,
and nothing in FS-08 becomes user-reachable without it.

**What this did NOT close, stated plainly:**

1. **FS-08 open question 8 — the `decisionLedgerId` — is still open**, and FS-09
   **declines it by name** rather than absorbing it: it is a question about what
   is recorded server-side, not about what a human is asked. FS-09 binds one
   consequence in the meantime — the review may not tell the user the import was
   "approved and recorded" when no approval row exists. Item 12 of §7 above.
2. **One question was routed _back_ to FS-08.** D24 names the review surface
   (`TcWorkspaceStageSurface` via `projectWorkspaceStage`) and forbids a second
   projection, but whether an **imported** revision reaches that projection today
   is unverified (FS-09 open question 8). If it does not, the wiring is FS-08's
   mechanism.
3. **A shipping-order constraint replaces the ownership gap.** No FS-08 phase
   yields a user-reachable capability until FS-09's execution half lands. FS-08's
   DoD now carries an item for it that FS-08 cannot tick alone — the same shape
   as the Windows code-signing certificate in §7 item 1, and deliberately not
   dressed up as done.
4. **FS-09 D23 State 1 is pinned to a fact FS-08 will change.** The copy
   "nothing is copied from your folder" is true only while the snapshot is
   overlay-only, and its test **must fail when FS-08 D17.1 lands**. That failure
   is the signal to move the copy to State 2. Whoever implements D17.1 owns
   telling FS-09.
5. **SPIKE-L2 still decides whether any of this ships.** Ownership was never the
   binding constraint on FS-08; a Windows container runtime that cannot observe
   all ten isolation controls is, and by FS-08 D4 it takes macOS execution with
   it. §7 item 14, and the README's spike register.

### 10.3 What this pass did not do

- It ran no spike, upgraded no `unverified` marker, and added one spike id only
  because FS-09 D19 needed it (**SPIKE-V1**).
- It did not re-open FS-01, FS-03, FS-04, FS-05, FS-06 or FS-07. The only
  documents changed are the two that carried the stale routing (FS-02, FS-08),
  the one that took the ownership (FS-09), the README, and this report.
- It did not decide FS-08 open question 8, FS-08 open question 9 (whether
  D17.1's base-file exporter is FS-08's), or FS-09 open question 3 (whether the
  enable toggle is reachable before sign-in). Those remain where they were, with
  their recommendations.
- It changed no source file, and it did not implement, schedule or budget any of
  the surfaces it assigned an owner to.

---

## 11. Gap-closure verification (adversarial pass)

**This is the section the task called "§10 — Gap-closure verification".** §10 was
already taken by the ownership pass, and §10.1/§10.2 are cited by number from
[README.md](README.md), [PRD-FS-02](PRD-FS-02-windows-commit-helper.md) and
[PRD-FS-09](PRD-FS-09-enablement-consent.md), so renumbering would silently
repoint live citations. It is §11.

**Baseline:** unchanged (`main@b349aca2`); every code line cited below was re-read
at it. **No source file was changed** and no test was run — this pass edits
specs only. What it did was try to **break** §10's two closures rather than
confirm them, on the assumption that a pass which announces two gaps closed is
exactly when a false all-clear is cheapest to write.

**Verdict up front, because the summary is the part that can lie:**

| §10 claim                                              | verdict                                                                                        |
| ------------------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| FS-08's consent surfaces are owned by FS-09 (§10.2)    | **Holds.** One stale sentence found inside FS-08 and fixed; no routed surface is unanswered    |
| Cross-volume grants are refused before minting (§10.1) | **Held for the create path only — 2 of 3 doors.** Two further paths were open; both now closed |
| "There is no mint-then-fail path left"                 | **Was false.** Corrected in place at §10.1 and rewritten in FS-09 D19.1                        |
| The spike register is the index of every spike         | **Was incomplete.** Two ship-gate spikes appeared in no register, no PRD list, and no DoD      |

### 11.1 What was tried

Five attacks, run in this order:

1. **Enumerate every route through which a `Grant` can come into existence** —
   not just the one §10.1 fixed. `grep` over `apps/` and `packages/` for
   `new GrantStore`, `store.create`, `requestFolderGrant` and any second grant
   store.
2. **Follow every FS-09 reference inside FS-08** to the decision it names, and
   check the decision answers it — including the prose paragraphs the routing
   table does not cover.
3. **Read the refusal's copy as a user who cannot act on it**, looking for a
   sentence that states a limitation and stops.
4. **Diff every "spike" mention in all nine PRDs against the README register**,
   both directions.
5. **Re-check D19 against FS-01's seam and FS-04's trash substrate** for a
   contradiction the new text introduced.

### 11.2 What actually broke — cross-volume was closed on the create path only

**Three doors lead to the artifact §10.1 describes** ("a grant that looks
granted, passes `listActive`, and fails only at prepare, after the user has been
shown an approval sheet"). D19's first draft closed one and claimed all three.

**Door 1 — `CapabilityService.requestFolderGrant` → `GrantStore.create`.
Closed, and it is the only mint path.** This checked out: `create` has exactly
one caller ([service.ts:54-58](../../../apps/desktop/main/capabilities/service.ts)),
`requestFolderGrant` has exactly one IPC entry
([handlers.ts:416-423](../../../apps/desktop/main/ipc/handlers.ts)), and
`new GrantStore` appears once in the tree
([capabilities/index.ts:72](../../../apps/desktop/main/capabilities/index.ts)).
No second store, no seeding path, no re-grant. §10.1's two check sites do cover
minting.

**Door 2 — a grant already on disk. OPEN, and it is the same defect exactly.**
`GrantStore` is durable and survives an upgrade. `#ensureLoaded`
([grant-store.ts:233-248](../../../apps/desktop/main/capabilities/grant-store.ts))
and `coerceGrant`
([:343-397](../../../apps/desktop/main/capabilities/grant-store.ts)) rehydrate a
row without re-deriving anything from it. A `read_write` grant on a second volume
minted by a build **before** D19 therefore still:

- passes `listActive`, which filters only `status` and `expiresAt`
  ([:167-175](../../../apps/desktop/main/capabilities/grant-store.ts));
- passes `#liveGrants`
  ([workspace-authority.ts:796-815](../../../apps/desktop/main/capabilities/workspace-authority.ts)),
  which compares no volume — `:812` requires `rootIdentity` to be **present**,
  never that it names the right volume;
- passes `#assertPreparedLive`, because it compares the observed identity against
  the **recorded** one
  ([:950-968](../../../apps/desktop/main/capabilities/workspace-authority.ts),
  compare at `:960-965`) — it catches a root that _moved_, and a root that was
  always on the wrong volume matches itself;
- and dies at `command_prepare`
  ([workspace_commit_helper.c:850](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)),
  after the approval sheet.

This is not hypothetical or Windows-only: on macOS an external-volume grant is
mintable on the **current** build, and the default TTL is thirty days
([grant-store.ts:119](../../../apps/desktop/main/capabilities/grant-store.ts)),
so the population outlives the upgrade that adds the gate. D19's own §4 bullet
pointed at `#assertPreparedLive` as covering "a root that changes volume after
the grant was minted" — true, and it is not the same case.

**Fixed** as [FS-09 D19.8](PRD-FS-09-enablement-consent.md): the volume term
moves into `grantUnusableReason` — the predicate FS-09 D6 was **already**
extracting so that displayed and enforced capability cannot drift — as
`wrong_volume`, evaluated only under `requireWritable` and ordered last.
`#liveGrants` then never hands the grant to the write path, `writesAvailable`
reports it honestly on the Settings page, and `listActive` is untouched so reads
keep working. No migration, no rewritten row, no `crossVolume` field: the
condition is a fact about **this boot's** staging volume, not about the row,
which is also why the same predicate covers `userData` moving volumes — a case
nothing else in the program handles.

**Door 3 — a volume the helper refuses to open. OPEN, and it defeats the
refusal itself.** `open_root` embeds the volume gate:
`supported_root_fd` requires `f_fstypename ∈ {"apfs","hfs"}`
([workspace_commit_helper.c:358-363](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c),
called at
[:365-369](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)),
and `command_root_identity` opens through it
([:837-843](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)).
For a root on exFAT, FAT32 or an SMB/NFS share it returns `UNSUPPORTED`, which
the client turns into a **thrown**
`NativeWorkspaceCommitHelperError("workspace_write_unsupported")`
([native-workspace-commit-helper.ts:668-670](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.ts)).
`GrantStore.create` awaits that resolver at
[:138-141](../../../apps/desktop/main/capabilities/grant-store.ts) with **no
`catch`, and before it reads `mode`**. Two consequences:

- D19's refusal never runs — there is no `volumeId` to compare — so the user
  gets a raw error out of a **grant request** instead of the typed refusal, the
  named volume, and the read-only offer. The one case where the copy matters
  most is the one case it never renders.
- **`read_only` could not be minted either**, on any such volume. That makes
  D19 §2's claim — "`read_only` grants on a second volume work completely and
  are minted normally" — false for the second volume most users actually
  own: a USB stick or a network drive. It was true only for a second _supported_
  volume, which the text did not say.

**Fixed** as [FS-09 D19.9](PRD-FS-09-enablement-consent.md): a third
`GrantRefusalReason`, `unsupported_volume`, with its own copy and its own remedy
(no other folder on that drive will work either, so "choose a different folder"
means a different drive); and `create` catches that one call so `read_only` mints
unbound (`rootIdentity: undefined` — already the legal shape a grant takes when
`#resolveProfileId` fails, [:217-229](../../../apps/desktop/main/capabilities/grant-store.ts),
and already refused for writes at
[workspace-authority.ts:812](../../../apps/desktop/main/capabilities/workspace-authority.ts)),
while every write mode rethrows. The `catch` is scoped to the identity call and
explicitly never widens to a write mode — swallowing it there would mint a grant
that displays as writable and is not, which is the defect the whole decision
removes. The same wrap is applied to step 36's staging-volume resolution, which
could otherwise **throw during authority construction** and turn a volume
question into a failed boot.

**Also corrected, smaller:**

- D19.1 said the store check sits "immediately after `assertGrantableRoot`". It
  cannot — it consumes the `rootIdentity` resolved at `:138-141`. The
  implementation plan (step 35) already had it right; the decision text now
  matches it.
- The refusal copy said the product "can't do that across two disks". The case
  this decision exists for is `C:` and `D:` as two **partitions of one disk**,
  where that sentence is simply false. Changed to "two separate drives", which is
  true for a partition, an external disk and a mounted volume alike, and is the
  word Windows itself uses for `D:`.
- "Grant read-only access" was an offered next step whose wiring nobody
  specified, and the obvious wiring — the renderer hands back the picked root —
  is banned by FS-09's own guardrail. D19.5 now states it: the action re-invokes
  `requestFolderGrant` with `mode: "read_only"` and the picker **re-opens**; main
  keeps no picked root between IPC calls. The cost (one re-pick) is stated, and
  the alternative (a main-held one-use continuation) is recorded as rejected.

### 11.3 What actually broke — FS-08 → FS-09 routing

**§10.2 holds.** All 50 lines in FS-08 that mention FS-09 were followed to the
decision they name, and each is answered: D16's reason strings against D21's copy
table (all **seven** members of `SandboxReadinessReason`, FS-08 §5, have a row —
checked member by member, none missing), D7's four binding properties against
D22, D12's review surface against D24, §7's affordance against D24, and the
three surfaces FS-09 took that FS-08 never routed (D20, D23, D25).

**One stale sentence, and it is the one that matters.** FS-08's **Out of scope**
still ended the unsupported-verb paragraph with "**Nobody owns that today.**"
while FS-08's own routing table, twenty pages later, lists that exact surface as
FS-09 D24's. A reader arriving through Out of scope — which is where an
implementer scoping the work arrives — would have concluded the gap was still
open. **Fixed** in place, with the two facts D24 must honour (the refusal is
wholesale, and it happens at prepare) restated as the reason the pre-check gates
the approve control rather than warning beside it.

Nothing else was found unanswered. Notably **not** a defect: FS-09 declining
FS-08's open question 8 (`decisionLedgerId`) is a decline with a stated reason
and a bound consequence on the copy, which is a different thing from a gap.

### 11.4 What actually broke — the spike register

The register's own premise is that it answers "how many are there". It did not.
Two spikes are **ship gates** in their owning PRD's text and appeared in **no**
register, no numbered spike list, and no DoD:

1. **FS-02 D2 property 3** — the status a `Flags = 0` rename returns when the
   destination leaf is occupied, specifically by **a junction or a file
   symlink**. FS-02 marks it "_unverified — spike required_" inside a prose
   paragraph and says to run it alongside SPIKE-W3, and names the consequence: a
   reparse-point occupant that is _followed_ rather than colliding gives the
   final component a symlink-follow hazard, requiring an explicit
   `FILE_ATTRIBUTE_REPARSE_POINT` refusal before the rename. That is a
   **confinement** property, which the program's guardrails treat as
   non-negotiable — and it had no name, so nothing tracked it.
2. **FS-06 D5's Windows mirror** — (a) is `FileRenameInformationEx` +
   `FILE_RENAME_POSIX_SEMANTICS` available on the pinned minimum build, (c) the
   exact status when the target is held with an incompatible share mode, (d)
   does `fs_carry_metadata`'s Win32 body reproduce the **effective** DACL. FS-06
   says these "must be answered before D5 is implemented". FS-06 has **no open
   questions section at all**, so the requirement lived only inside a blockquote.
   (c) is the error D6's entire "Windows detects the open holder" claim rests on;
   (d) failing means a metadata carry-over silently drops inherited ACEs, the
   exact failure FS-06 D8 exists to prevent.

**Fixed:** both are rows in the README's "PRD-local spikes with no program id"
table, labelled **BLOCKS — ship gate**, and both now have a DoD line in the PRD
that owns them, so the register row points at something. The register's preamble
says plainly that these were found in its own blind spot: a spike is tracked only
if someone gave it a name. The host-budget paragraph was corrected to book time
for them.

One consolidation observation was strengthened rather than made: FS-02 SPIKE-W2,
FS-04 spike 5 and FS-06 D5(d) ask the **same** Win32 inherited-ACE question from
three directions. They are still not consolidated — they are not obviously one
experiment — but three askers is a stronger argument for running it early than
two was.

**Checked and found correctly labelled:** SPIKE-V1's "blocks a decision, **not**
whether D19 refuses" is right, and it was worth arguing about. If serial equality
is unsound on Win32 the gate fails **open** — the exact failure D19 exists to
prevent — but `command_prepare`'s check still fails closed behind it, so what is
lost is the early refusal, not the safety property. "Blocks a decision" plus the
register's explicit "fails in the dangerous direction if ignored" callout is the
honest pair. SPIKE-L2's **BLOCKS — the program** label is also correct and is the
only one of its kind.

### 11.5 Checked and found sound

- **No contradiction with FS-01's seam.** D19 declares no seam member, adds no
  verb to `fs_platform.h`, and names only members FS-01 §2 already defines
  (`fs_volume_supported`, `fs_dir_is_app_private`). The one producer of volume
  identity remains `NativeWorkspaceAuthority.rootIdentity` over
  `command_root_identity`; nothing added a second.
- **No contradiction with FS-04's trash substrate.** Spine D4 puts the trash at
  `<root>/.0xcopilot/trash/`, on the **grant root's** volume, same-volume by
  construction via `open_parent`'s `st_dev` refusal. D19 compares the root
  against the **staging** volume, which is a different pair; the trash is not
  what makes a cross-volume grant fail, and D19 says nothing about where it
  lives. The Out-of-scope note that per-volume staging would re-open the spine
  D4 / FS-04 D1-D3 argument is correct — it would make the app-private option
  same-volume by construction too, which was half of FS-04's reasoning.
- **FS-02 D7 is genuinely unchanged.** `workspace_commit_helper.c:850` remains
  the enforcing check; D19 adds a gate and removes none, and both documents now
  say so in the same words.
- **`sensitive_root` really is the same shape.** Projecting
  `assertGrantableRoot`'s existing throw into the refusal union gives the page
  one renderer instead of a refusal path plus a catch path — and with
  `unsupported_volume` added there are three reasons and still one renderer.

### 11.6 What remains genuinely open — no all-clear here

1. **SPIKE-V1 is unrun**, so every Win32 sentence in D19 still says "same volume
   **serial**". Until it returns, D19's Windows half is a check whose soundness
   is asserted, not measured. Unchanged from §10.1 item 2.
2. **The two newly registered spikes are unrun**, and both are ship gates. The
   register's total went up by two; nothing was answered.
3. **Per-volume app-private staging is still designed by nobody.** §10.1 item 1
   stands exactly as written. D19 refuses a cross-volume write grant; it does not
   make one work, and a Windows user whose data lives on `D:` still cannot use
   this capability for writes. That is a real product limitation, not a closed
   gap.
4. **D19.8 and D19.9 are specified, not implemented.** They are decisions in a
   PRD with steps, tests and DoD items — the same status as everything else in
   this program. Calling the cross-volume gap "closed" means the design covers
   all three doors, not that any code exists.
5. **The unsupported-volume copy names causes, not the contract.** It says USB
   sticks, memory cards and network drives because those are what users
   recognise; the actual refused set is `fs_volume_supported`'s, differs per
   platform, and will change if that gate widens. The copy is deliberately not
   an enumeration, and a reviewer should check it stays that way.
6. **FS-08 open question 8, FS-08 open question 9 and FS-09 open question 3 are
   untouched**, as are all outcomes of §10.2. This pass decided nothing new about
   execution.

### 11.7 What this pass did not do

- It ran no spike, upgraded no `unverified` marker, and invented no new spike
  id — the two spikes it registered stay un-idded because their owning PRDs do
  not name them, and the README is explicit that the PRD's text wins.
- It changed **no source file** and ran **no test**. Every code claim above was
  read at `main@b349aca2`.
- It did not re-open FS-01, FS-03, FS-04, FS-05 or FS-07. Documents changed:
  FS-02 (D7's note, its open-question bullet, one DoD item), FS-06 (one DoD
  item), FS-08 (one stale sentence), FS-09 (D19.8/D19.9 and their interfaces,
  steps, tests, DoD and guardrails; C10, C11's heading, D18's table, open
  question 4), README (the cross-volume paragraph and the spike register), and
  this report.
- It did not revisit §10.2's product call. The ownership boundary between FS-08
  and FS-09 was tested and held; only its bookkeeping was wrong.
