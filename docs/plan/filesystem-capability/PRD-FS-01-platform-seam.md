# PRD-FS-01 — Platform seam: one commit protocol, two platforms

**Status:** specified
**Depends on:** — (baseline `main@b349aca2`, 2026-07-29). Every other PRD in this program depends on FS-01.

## Implementer brief

The commit helper is 1003 lines of C of which roughly 15 are actually macOS. This PRD names the boundary explicitly: a small C header of platform **primitives** (handles, identity, confined open, metadata, staging, the two effects, durability, directory iteration, crypto, channel, bootstrap) and a portable **protocol** translation unit above it that owns framing, sealing, the claim binding, the journal state machine and the conservative restart. macOS moves behind the seam with **zero behaviour change**, proven three ways. The TypeScript client stops testing `process.platform !== "darwin"` and starts consulting a closed platform registry that cannot be extended without a signature verifier. No verb is added, no wire format changes, no Win32 code is written here. Read [README.md](README.md) first: D1/D2/D3 are locked and are not re-litigated below.

## Context

Everything in this section was verified against the code at `main@b349aca2`.

### The helper today

`apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c` is 1003 lines. It is a single translation unit compiled by `build.mjs:19-39` with `cc -std=c11 -Wall -Wextra -Werror -O2 -fstack-protector-strong -D_FORTIFY_SOURCE=2 -Wl,-dead_strip`, written to a temp name and `rename`d to `bin/workspace-commit-helper`, then `chmod 0500`. `bin/` is gitignored (`native/workspace-commit-helper/.gitignore:1`).

The actual filesystem effect is `commit_entry` at [workspace_commit_helper.c:752-766](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c):

```c
if (entry->operation == CREATE) {
  if (!sealed_stage_matches(entry) ||
      fclonefileat(entry->stage_fd, entry->parent_fd, entry->leaf, CLONE_NOFOLLOW) < 0 ||
      fsync(entry->parent_fd) < 0) return 0;
} else if (entry->operation == MKDIR) {
  if (mkdirat(entry->parent_fd, entry->leaf, 0700) < 0 || fsync(entry->parent_fd) < 0) return 0;
} else return 0;
```

Everything else is protocol. `parse_entry:801` refuses every operation that is not `CREATE` or `MKDIR`, for the reason given in the comment at :797-800 — macOS has no kernel compare-and-swap rename bound to an observed inode+digest.

### Where the platform actually leaks in

Classified by reading every call site. "Portable" means the function contains no OS-specific call at all.

| Concern                                                          | Lines                              | Class                       |
| ---------------------------------------------------------------- | ---------------------------------- | --------------------------- |
| big-endian codecs, writer/reader, `hex`, `is_hex_digest`         | 171-242, 348-356                   | portable                    |
| `path_is_safe`                                                   | 313-332                            | portable                    |
| claim binding (`binding_*`, `compute_prepared_binding`)          | 249-302                            | portable + crypto           |
| `snapshot_matches`, `entry_live`, `disjoint_entries`             | 421-427, 736-739, 826-835          | portable                    |
| journal record shape, state machine, claim index                 | 123-135, 512-552, 558-622          | portable                    |
| `command_*` orchestration, `main` loop                           | 837-960, 979-1001                  | portable                    |
| framing (`respond`, `verify_frame`)                              | 768-790, 962-969                   | portable + crypto + channel |
| SHA-256 / HMAC / CSPRNG (`CommonCrypto`, `arc4random_buf`)       | 25-26, 440, 463, 855               | **crypto primitive**        |
| confined open + walk (`open_root`, `open_parent`)                | 365-398                            | portable policy + **fs**    |
| exact-entry check + journal scans (`readdir`)                    | 338-346, 626-651, 656-678          | portable policy + **fs**    |
| metadata (`fstat`, `fstatat`, `dev`/`ino`/`mode`/`size`/`nlink`) | 304-311, 400-419, 429-433, 716-734 | portable policy + **fs**    |
| volume gate (`fstatfs` → `apfs`/`hfs`)                           | 358-363                            | **fs policy, per platform** |
| private-directory attestation (`geteuid`, `mode & 0077`)         | 429-433                            | **fs policy, per platform** |
| staging + journal file I/O (`openat`/`renameat`/`unlinkat`)      | 460-510, 703-730                   | portable policy + **fs**    |
| the two effects (`fclonefileat`, `mkdirat`) + `fsync`            | 752-766                            | **fs primitive**            |
| command channel (`read`/`write` on fd 0/1)                       | 150-169, 788, 981-983              | **channel primitive**       |
| bootstrap: fd 3/4/5/6/7, `close(STDERR)`, `dup`                  | 971-978, 435-447                   | **bootstrap primitive**     |
| `_exit(86)` crash-boundary faults                                | 612, 931                           | **process primitive**       |

### The two darwin literals that keep Windows read-only

1. [build.mjs:9-17](../../../apps/desktop/native/workspace-commit-helper/build.mjs) short-circuits on `process.platform !== "darwin"` and writes the literal string `"unsupported platform\n"` to `bin/workspace-commit-helper` with mode `0o400` — deliberately not executable.
2. [native-workspace-commit-helper.ts:171-181](../../../apps/desktop/main/capabilities/native-workspace-commit-helper.ts) rejects `process.platform !== "darwin"` before `spawn`, throwing `workspace_write_unsupported`.

A third, independent gate lives at [workspace-production-authority.ts:83-92](../../../apps/desktop/main/capabilities/workspace-production-authority.ts): `platform !== "darwin" || !packaged || !production || !safeStorage.isEncryptionAvailable() || confinement === undefined` returns `null` — no writable authority object is constructed at all.

### Three facts that constrain any refactor

**F1 — the journal record is an on-disk struct.** [`struct journal_record`:123-135] is written with `write_all(fd, record, sizeof *record)` (:469, :488) and read back with `read_all(fd, record, sizeof *record)` (:502). Its MAC covers `offsetof(struct journal_record, mac)` bytes (:450-451). Any change to field order, type, or padding makes every pre-existing journal unreadable; `journal_reconcile_startup` then returns 0 (:633) and `main` returns 1 (:977-978), which takes the entire writable authority offline for that install. The layout must not move.

**F2 — the claim binding digest is compared across processes and across restarts.** `compute_prepared_binding:279-302` hashes the root `dev`/`ino`, then per entry the operation, both path spellings, both snapshots (including `dev`, `ino`, `mode`, `size`, digest), the slot, the expected digest and size. The result is persisted in `journal_record.binding_digest` and compared byte-for-byte by `journal_acquire_claim:588`; a mismatch is `CLAIM_BINDING_MISMATCH` → `CONFLICT` (:915). Change the hashed bytes and an in-flight claim written by the previous app version becomes a hard conflict after upgrade.

**F3 — the root-identity strings are persisted in grants.** `command_root_identity:837-843` emits decimal `st_dev` and `st_ino` as two strings. `WorkspaceRootIdentity` (`workspace-authority.ts:57-60`) is `{volumeId: string; fileId: string}` — opaque to every consumer, and stored. Changing the darwin string format silently invalidates existing grants.

### One verified latent defect

`command_prepare` sets `prepared->entry_count = count` and then `calloc`s the entry array (:851). If `parse_entry` fails at index `i` (:853), `destroy_prepared` (:689-695) iterates **all** `entry_count` entries and calls `free_entry` (:680-687), which does `if (entry->stage_fd >= 0) close(entry->stage_fd); if (entry->parent_fd >= 0) close(entry->parent_fd); if (entry->destination_parent_fd >= 0) close(entry->destination_parent_fd);`. Entries at indices `> i` were never parsed, so they are `calloc`-zeroed and all three descriptors read `0`. `0 >= 0`, so the helper calls `close(0)` — the command pipe.

Consequence, traced through: the `CONFLICT` response is still written (`respond` uses `STDOUT_FILENO`), then the loop's `read_all(STDIN_FILENO, …)` (:981) returns -1, `main` breaks and returns 0. Every later request on that helper fails with `workspace_helper_failed`, and `createProductionWorkspaceAuthority` holds a dead helper for the rest of the app session. Reachable from ordinary product traffic: any multi-entry change set whose **non-final** entry fails validation (a missing parent directory, a case-mismatched segment, a pre-existing target).

A second, practically unreachable variant: if the `calloc` at :851 returns `NULL`, `destroy_prepared` dereferences `prepared->entries[i]` with `entries == NULL` and `entry_count == count` (:852).

Both were found by reading, not by executing. The test plan below pins the reproduction so the implementer confirms it before and after.

### What CI does and does not cover

`ci-desktop.yml:61` runs the whole desktop job on `ubuntu-latest`. `npm run test --workspace @0x-copilot/desktop` (`package.json:9`) first runs `build:workspace-commit-helper`, which on Linux produces only the sentinel; the 15 native tests are then skipped by `const describeNative = process.platform === "darwin" ? describe : describe.skip;` (`native-workspace-commit-helper.test.ts:33`). **No CI job anywhere executes a single line of the helper.** The 15 tests run only on a developer's macOS machine.

## Interfaces consumed

- **Protocol v2**, unchanged: `PROTOCOL = 2` (`workspace_commit_helper.c:45`, `native-workspace-commit-helper.ts:28`); request enum (:58-63); operation enum (:64); outcome enum (:65-66); failure enum (:67); journal states (:68-71); `JOURNAL_VERSION 3` (:56).
- **`NativeWorkspaceAuthority`** (`workspace-authority.ts:222-253`) — unchanged, not touched.
- **`WorkspaceChangeEntry`** (`workspace-authority.ts:77-86`) and `assertNativeWorkspaceCanonicalPath` (:17-29) — unchanged.
- **`NativeWorkspaceCommitHelperConfig`** (`native-workspace-commit-helper.ts:86-126`) — one new optional field, no field removed or made required.
- **`ProductionWorkspaceAuthorityConfig.platform`** (`workspace-production-authority.ts:62`) — already exists and is already injectable.
- **`WorkspaceConfinementProbe`** (`workspace-production-authority.ts:33-35`) and `MacosWorkspaceConfinement` (`macos-workspace-confinement.ts:43`, `verify()` at :88-97) — unchanged; FS-03 owns the Windows probe.
- **Packaging**: `electron-builder.yml:44-47` (`native/workspace-commit-helper/bin` → `<resourcesPath>/workspace-commit-helper`, filter `"workspace-commit-helper"`), `build/sign-nested.js:140-145` (nested signing at that exact path), `verifyPackagedWorkspaceCommitHelper` (`native-workspace-commit-helper.ts:507-515`). None of these change.

## Interfaces exposed

### 1. File layout

```
apps/desktop/native/workspace-commit-helper/
  build.mjs                          builder table (was: darwin-or-sentinel)
  README.md                          NEW — the seam contract, for FS-02's author
  src/
    fs_platform.h                    NEW — filesystem/handle/bootstrap seam
    fs_crypto.h                      NEW — digest/MAC/entropy seam
    fs_platform_posix.c              NEW — the macOS implementation
    fs_crypto_commoncrypto.c         NEW — the macOS crypto provider
    workspace_commit_helper.c        REDUCED — portable protocol only
  tools/
    check-seam.mjs                   NEW — mechanical seam-completeness check
    transcript.mjs                   NEW — record/replay driver for the golden set
    build-baseline.mjs               NEW — builds the pre-seam helper from a git ref
  test/golden/*.json                 NEW — recorded from the pre-seam binary
```

FS-02 adds `fs_platform_win32.c` + `fs_crypto_bcrypt.c` and one `build.mjs` entry. No other file in the repository learns about a platform.

### 2. `src/fs_platform.h`

```c
/*
 * The platform seam for the workspace commit helper.
 *
 * Everything declared here is a PRIMITIVE the operating system provides.
 * Everything that decides WHAT to do with a primitive — path grammar,
 * confinement policy, digests, framing, the claim binding, the journal state
 * machine, restart conservatism — lives above this header in
 * workspace_commit_helper.c and is compiled once for every platform.
 *
 * Rules for an implementation of this header:
 *   1. Implement every declaration. A partial provider must not link.
 *   2. Add nothing. A platform-only capability is not a seam member.
 *   3. Never follow a symlink, junction, reparse point or mount at any
 *      component of any operation declared here.
 *   4. Never allocate a path string from a caller-supplied relative path.
 *      Every operation is handle-relative and takes ONE leaf name.
 *   5. The helper is single-threaded. No member may block indefinitely.
 */
#ifndef FS_PLATFORM_H
#define FS_PLATFORM_H

#include <stddef.h>
#include <stdint.h>

/* ---------------------------------------------------------------- handles */

/* An owned, open kernel object. Passed and returned BY VALUE. A zeroed
 * fs_handle is NOT valid: struct members must be initialised explicitly with
 * FS_HANDLE_INVALID, never left to memset/calloc. Copies are aliases, not
 * duplicates; exactly one fs_close per successful open or fs_dup. */
typedef struct fs_handle {
#if defined(_WIN32)
  void *raw;
#else
  int raw;
#endif
} fs_handle;

#if defined(_WIN32)
#define FS_HANDLE_INVALID ((fs_handle){(void *)(intptr_t)-1})
#else
#define FS_HANDLE_INVALID ((fs_handle){-1})
#endif

/* 1 when the handle refers to an open object. Win32 must reject BOTH NULL and
 * INVALID_HANDLE_VALUE; POSIX is exactly `h.raw >= 0`. */
int fs_handle_valid(fs_handle h);

/* 0 on success, -1 on failure. The caller propagates failure exactly as the
 * pre-seam code propagated close(2) failure (see journal_store). */
int fs_close(fs_handle h);

/* 1 on success. The duplicate is independently owned and independently closed. */
int fs_dup(fs_handle h, fs_handle *out);

/* ------------------------------------------------------------- identities */

#define FS_IDENTITY_FILE_MAX 16

/* A filesystem object's stable identity.
 *
 * POSIX packing:  volume = (uint64_t)st_dev   (the cast is deliberate and
 *                 sign-extends, because that is what the pre-seam binding
 *                 hashed);  file[0..7] = be64(st_ino);  file_bytes = 8.
 * Win32 packing:  volume = FILE_ID_INFO.VolumeSerialNumber;
 *                 file[0..15] = FileId.Identifier;  file_bytes = 16.
 *
 * A zeroed fs_identity is the canonical "no object" identity and MUST encode
 * to all-zero binding bytes. */
struct fs_identity {
  uint64_t volume;
  uint8_t file[FS_IDENTITY_FILE_MAX];
  uint8_t file_bytes;
};

/* Number of bytes fs_identity_binding writes. A per-platform compile-time
 * constant, NOT a runtime value: the claim binding is never compared across
 * platforms (grants are not portable either), so a fixed-width per-platform
 * encoding is unambiguous.
 *
 * POSIX MUST be 16 and MUST emit be64(volume) || file[0..7], which is
 * byte-for-byte what compute_prepared_binding hashed before the seam. */
#if defined(_WIN32)
#define FS_IDENTITY_BINDING_BYTES 24
#else
#define FS_IDENTITY_BINDING_BYTES 16
#endif

void fs_identity_binding(const struct fs_identity *id,
                         uint8_t out[FS_IDENTITY_BINDING_BYTES]);

int fs_identity_equal(const struct fs_identity *a, const struct fs_identity *b);
int fs_identity_same_volume(const struct fs_identity *a,
                            const struct fs_identity *b);

/* Human-readable, stable, opaque-to-callers text for the grant store.
 * POSIX MUST emit decimal "%llu" of the packed values — the pre-seam format.
 * Buffers are caller-owned; both write a NUL-terminated string. */
#define FS_IDENTITY_TEXT_BYTES 40
void fs_identity_text_volume(const struct fs_identity *id,
                             char out[FS_IDENTITY_TEXT_BYTES]);
void fs_identity_text_file(const struct fs_identity *id,
                           char out[FS_IDENTITY_TEXT_BYTES]);

/* -------------------------------------------------------------- metadata */

enum fs_kind {
  FS_KIND_ABSENT = 0,
  FS_KIND_FILE = 1,   /* regular file. Wire-compatible with snapshot.kind */
  FS_KIND_DIR = 2,    /* directory.    Wire-compatible with snapshot.kind */
  FS_KIND_LINK = 3,   /* symlink / reparse point / junction               */
  FS_KIND_OTHER = 4   /* device, socket, fifo, anything else              */
};

struct fs_meta {
  enum fs_kind kind;
  struct fs_identity id;
  /* Platform-defined, stable bits hashed into the claim binding and compared
   * by snapshot_matches. POSIX: st_mode verbatim. An implementation MUST NOT
   * include any bit the platform mutates as a side effect of reading the
   * object or of an unrelated write elsewhere. */
  uint64_t mode_bits;
  uint64_t size;
  uint64_t link_count;
};

enum fs_status { FS_OK = 0, FS_ABSENT = 1, FS_EXISTS = 2, FS_ERROR = 3 };

/* Never follows the final component. FS_ABSENT is reserved for "the name does
 * not exist"; every other failure is FS_ERROR. */
enum fs_status fs_stat_at(fs_handle dir, const char *leaf, struct fs_meta *out);

/* 1 on success. */
int fs_stat_handle(fs_handle h, struct fs_meta *out);

/* ------------------------------------------------------- confined opening */
/*
 * `path` for fs_open_root is an absolute, UTF-8 host path taken from the wire.
 * It is the ONLY multi-component path in this header. An implementation owns
 * its own encoding conversion and MUST fail closed on invalid UTF-8.
 *
 * Every other `leaf`/`name` is a single component that has already passed
 * path_is_safe (workspace_commit_helper.c:313-332): ASCII, [A-Za-z0-9._-]
 * only. Implementations MUST NOT normalise, case-fold or extend it.
 */

/* Opens a directory refusing a symlink at ANY component. POSIX:
 * O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW_ANY. 1 on success. */
int fs_open_root(const char *path, fs_handle *out);

/* Handle-relative directory open, symlink-refusing. 1 on success. */
int fs_open_dir_at(fs_handle dir, const char *leaf, fs_handle *out);

/* Handle-relative read-only open of an existing file, symlink-refusing. */
int fs_open_read_at(fs_handle dir, const char *leaf, fs_handle *out);

/* Exclusive create, write-only, private mode, symlink-refusing. Returns
 * FS_EXISTS when the name is already taken — this is the exclusion primitive
 * the claim protocol depends on (journal_store_no_replace). */
enum fs_status fs_open_new_exclusive(fs_handle dir, const char *leaf,
                                     fs_handle *out);

/* Exclusive create, read/write, private mode, symlink-refusing. Used only for
 * private staging objects, which are written then re-read for sealing. */
enum fs_status fs_open_new_stage(fs_handle dir, const char *leaf,
                                 fs_handle *out);

/* ------------------------------------------------------------ directories */

/* 1 on success. Creates with the most private mode the platform offers
 * (POSIX 0700). Fails if the name exists. */
int fs_mkdir_at(fs_handle dir, const char *leaf);

/* Replacing rename WITHIN the app-private journal directory only. This is the
 * durable-publish step of journal_store; it is deliberately NOT a workspace
 * verb and must never be called with a workspace handle. 1 on success. */
int fs_rename_replace(fs_handle src_dir, const char *src_leaf,
                      fs_handle dst_dir, const char *dst_leaf);

/* FS_ABSENT when the name did not exist. */
enum fs_status fs_unlink_at(fs_handle dir, const char *leaf);

/* Return 1 to continue, 0 to stop. `name` is UTF-8 and is valid only until the
 * visitor returns. */
typedef int (*fs_dir_visitor)(void *context, const char *name);

/*
 * Enumerates `dir`. Returns 1 when the directory was opened and enumeration
 * ran to completion or was stopped by the visitor; 0 when the directory could
 * not be opened or enumerated.
 *
 * CONTRACT — read this before implementing it on a new platform. Callers
 * MUTATE the directory during iteration (journal_reconcile_startup rewrites
 * records while scanning). Therefore:
 *   - the visitor MAY be called more than once for the same name. Callers are
 *     required to be idempotent and are;
 *   - the visitor MAY be called for names created during the scan;
 *   - the visitor MUST be called at least once for every name that existed
 *     when fs_dir_for_each was entered and was not removed during the scan.
 * The third clause is load-bearing: a missed pre-existing record changes a
 * racing claim re-acquisition from FAILED to INDETERMINATE
 * (journal_acquire_claim:592-597).
 */
int fs_dir_for_each(fs_handle dir, fs_dir_visitor visit, void *context);

/* ------------------------------------------------------------------- i/o */

/* Byte counts. <0 is an error, 0 is end-of-file / a refused write. These
 * deliberately expose short transfers: the two call sites above the seam have
 * DIFFERENT retry policies and must keep them. */
ptrdiff_t fs_read_some(fs_handle h, void *out, size_t length);
ptrdiff_t fs_write_some(fs_handle h, const void *in, size_t length);

/* 1 = filled, 0 = clean EOF before completion, -1 = error. Retries EINTR. */
int fs_read_exact(fs_handle h, void *out, size_t length);

/* 0 on success, -1 on failure. Retries EINTR. */
int fs_write_all(fs_handle h, const void *in, size_t length);

/* Repositions to offset 0. 1 on success. */
int fs_seek_begin(fs_handle h);

/* Flushes this object's own data and metadata to stable media. POSIX: fsync.
 * 0 on success, -1 on failure.
 *
 * DIRECTORY DURABILITY IS NOT UNIFORM AND MUST NOT BE FAKED. On POSIX, applied
 * to a directory handle this makes that directory's entries durable. Win32 has
 * no documented equivalent: FlushFileBuffers on a directory handle is
 * undocumented and the only documented volume-wide barrier needs administrator
 * rights a per-user app does not have (see PRD-FS-02 D8).
 *
 * A provider that cannot prove directory durability MUST still return 0 when the
 * call raised no error — returning -1 would fail every commit — and MUST define
 * FS_DIRECTORY_BARRIER_PROVEN to 0. Callers above the seam MUST NOT read a 0
 * from a directory barrier as "durable" on such a platform; they read it as "no
 * error". The distinction is load-bearing: on a platform where it is 0,
 * `applied` means observed-applied, not power-loss-durable, and FS-07's
 * reconciliation re-observes the target rather than trusting a terminal journal
 * state. */
#if defined(_WIN32)
#define FS_DIRECTORY_BARRIER_PROVEN 0
#else
#define FS_DIRECTORY_BARRIER_PROVEN 1
#endif

int fs_durable_barrier(fs_handle h);

/* ------------------------------------------------------------- posture */

/* 1 only for a filesystem whose semantics this program has proven. POSIX:
 * apfs or hfs. Network, removable and unproven filesystems return 0. */
int fs_volume_supported(fs_handle h);

/* 1 only when the directory is reachable by this process's identity and by
 * no one else. POSIX: st_uid == geteuid() && (st_mode & 0077) == 0. */
int fs_dir_is_app_private(fs_handle h);

/* --------------------------------------------------------------- effects */
/*
 * A verb appears here ONLY in the PRD that implements it on every supported
 * platform. Declaring an unimplemented slot is how a one-platform verb gets
 * written, so nothing below is declared here.
 *
 * RESERVED SPELLINGS. Downstream PRDs add these names and no others; the list
 * exists so FS-04/05/06 do not each invent a different spelling for the same
 * primitive (they did, in their first drafts). Every one takes fs_handle, never
 * an int, and never a multi-component path:
 *
 *   FS-04  fs_rename_noreplace  (+ enum fs_rename_result)
 *          fs_rmdir_at / fs_volume_free_bytes
 *          fs_volume_supports_rename_excl
 *   FS-05  fs_pin_target / fs_link_count / fs_directory_is_empty
 *          fs_identity_at
 *   FS-06  fs_commit_replace / fs_volume_supports_swap
 *          fs_carry_metadata
 *
 * FOURTH NOTE, added by the second consistency pass, about fs_commit_replace's
 * SHAPE rather than its spelling. Do not copy fs_commit_create's
 * (staged, parent, leaf) signature onto it. fs_commit_create works with three
 * inputs because fclonefileat takes a source FD; RENAME_SWAP is name-based on
 * BOTH sides, so the POSIX body additionally needs the staging directory handle
 * and the stage leaf, and the Win32 body needs the staged HANDLE it renames.
 * FS-06 declares the four-input form plus an out-parameter saying where the
 * displaced object ended up, because that differs per platform (POSIX: at the
 * stage name, after the swap; Win32: already in the trash, before the effect).
 * One declaration, two honest bodies. Dropping either pair would have forced a
 * per-platform verb, which D1 and D9 forbid.
 *
 * THIRD NOTE, added by the same pass. fs_carry_metadata is new and is a gap the
 * pass found: FS-06 D8 makes metadata carry-over MANDATORY and fail-closed
 * (mode + ACL + xattrs from the displaced object onto the staged one, before the
 * effect), but FS-06's first draft spelled it as raw fchmod + fcopyfile in the
 * PORTABLE translation unit, which §5 and check-seam.mjs forbid, and declared no
 * seam member for it. A carry-over that exists on one provider only would ship
 * `replace` with a silent permission downgrade on the other. Its shape is
 *   int fs_carry_metadata(fs_handle from, fs_handle to);
 * returning 0 on any failure so the caller can abort before the effect. The
 * POSIX body is fchmod + fcopyfile(COPYFILE_ACL|COPYFILE_XATTR); the Win32 body
 * is GetSecurityInfo/SetSecurityInfo + GetFileTime/SetFileTime. Both are
 * unverified and FS-06 owns the spike.
 *
 * NOTE, added by the consistency pass: this list originally reserved
 * fs_commit_delete and fs_commit_move. It no longer does, because D1 draws the
 * seam at primitives and FS-05's own design is one primitive
 * (fs_rename_noreplace) with two PORTABLE callers (commit_delete / commit_move)
 * above the seam. Reserving a per-verb seam member would have invited exactly
 * the per-platform verb divergence D1 forbids. fs_volume_supports_rename_excl
 * is reserved because FS-05's first draft added a darwin-only
 * volume_supports_rename_excl(int), which rule 2 forbids: a capability query is
 * a platform primitive and must exist on both providers.
 *
 * SECOND NOTE, added by the same pass. Four ownership moves, all forced by D1:
 *
 *   1. fs_rename_noreplace moves from FS-05 to FS-04. FS-04 lands FIRST (FS-05
 *      and FS-06 cannot displace bytes without it), and FS-04's displacement,
 *      restore and no-replace collect ARE no-replace renames. Leaving the
 *      primitive with FS-05 would have forced FS-04 to invent a second one.
 *   2. fs_stage_preimage / fs_restore_preimage / fs_collect_preimage are NOT
 *      seam members and are no longer reserved. They are verbs — precondition,
 *      journal row, disposition — and by D1 they are PORTABLE callers over
 *      fs_rename_noreplace, fs_unlink_at and fs_rmdir_at, written once.
 *   3. fs_rmdir_at is new. fs_unlink_at above maps to unlinkat(dir, leaf, 0),
 *      which CANNOT remove a directory; FS-05 deletes empty directories, so
 *      collecting that preimage needs AT_REMOVEDIR / FILE_DIRECTORY_FILE
 *      disposition. Without it, a deleted directory's preimage is
 *      uncollectable — a gap the consistency pass found, not a rename.
 *   4. fs_volume_free_bytes is new. FS-04 D4's budget takes MIN(main's hint,
 *      the helper's own reading); the helper's reading was drafted as a raw
 *      fstatfs, which rule 2 forbids in portable code and which has no POSIX
 *      spelling on Win32 (GetDiskFreeSpaceExW is path-based;
 *      FileFsFullSizeInformation by handle is the likely Win32 body and is
 *      unverified — FS-02 SPIKE-W5).
 *
 * A verb still lands on both platforms or neither; these moves make that a
 * link-time property for FS-04 too, not only for FS-05.
 *
 * `enum fs_status` above is THIS header's, and FS_ABSENT is its member with the
 * value 1. A downstream PRD must not redefine FS_ABSENT as a sentinel of its
 * own (FS-05's first draft used `#define FS_ABSENT (-2)`), and must not add a
 * second status enum.
 *
 * NO TIME PRIMITIVE IS DECLARED, and none is reserved. Per spine D4/D5, wall
 * clock values are stamped by main and only compared by the helper, so a
 * timestamp inside a MAC'd record is main-attested rather than helper-attested
 * and must be labelled that way wherever it is used.
 */

/* Materialises the sealed staged object as a NEW entry `leaf` under `parent`.
 * Fails if anything already occupies `leaf`. Consumes the handle, never a
 * staging filename. Refuses a symlink destination. 1 on success.
 * POSIX: fclonefileat(staged, parent, leaf, CLONE_NOFOLLOW). */
int fs_commit_create(fs_handle staged, fs_handle parent, const char *leaf);

/* Creates a NEW private directory `leaf` under `parent`. Fails if anything
 * already occupies `leaf`. 1 on success. POSIX: mkdirat(parent, leaf, 0700). */
int fs_commit_mkdir(fs_handle parent, const char *leaf);

/* --------------------------------------------------------------- process */

/* The private command channel. POSIX: fd 0 / fd 1. Semantics match
 * fs_read_exact / fs_write_all. */
int fs_chan_read_exact(void *out, size_t length);
int fs_chan_write_all(const void *in, size_t length);

#define FS_KEY_BYTES 32

/* Everything the helper is given at launch and can never be given again. */
struct fs_bootstrap {
  uint8_t channel_key[FS_KEY_BYTES];
  uint8_t journal_key[FS_KEY_BYTES];
  fs_handle staging_parent; /* app-private staging directory */
  fs_handle journal_dir;    /* app-private durable journal directory */
  uint8_t test_fault;       /* 0 when the optional fault capability is absent */
};

/*
 * Acquires the five private capabilities, in this order, and fails closed:
 *   1. channel key   2. journal key   3. optional 1-byte fault selector
 *   4. close every capability carrier and the diagnostic channel
 *   5. duplicate the two inherited directory capabilities
 * Returns 1 on success. On 0 the caller exits non-zero without touching disk.
 * The POSIX implementation is fds 3, 6, 7, then close(3)/close(6)/close(7)/
 * close(2), then dup(4)/dup(5) — the pre-seam sequence at
 * workspace_commit_helper.c:974-978 and 437-439.
 */
int fs_bootstrap_acquire(struct fs_bootstrap *out);

/* Terminates immediately: no atexit handlers, no stdio flush, no destructors.
 * POSIX: _exit(code). Used only by the crash-boundary fault injector and by
 * an entropy failure. */
void fs_abort_immediate(int code);

#endif /* FS_PLATFORM_H */
```

### 3. `src/fs_crypto.h`

```c
/*
 * The digest / MAC / entropy seam. SHA-256 and HMAC-SHA-256 are protocol, not
 * platform: the same bytes must come out on every platform. Only the provider
 * is platform-specific (CommonCrypto, BCrypt).
 */
#ifndef FS_CRYPTO_H
#define FS_CRYPTO_H

#include <stddef.h>
#include <stdint.h>

#define FS_SHA256_BYTES 32

/* Opaque, caller-allocated, no heap, no cleanup call. The size is a
 * per-platform constant large enough for that provider's context; the
 * implementation static-asserts that its own context fits. */
#if defined(_WIN32)
#define FS_SHA256_CTX_BYTES 512
#else
#define FS_SHA256_CTX_BYTES 128
#endif

typedef struct fs_sha256_ctx {
  uint64_t alignment_;
  unsigned char opaque[FS_SHA256_CTX_BYTES];
} fs_sha256_ctx;

void fs_sha256_init(fs_sha256_ctx *ctx);
void fs_sha256_update(fs_sha256_ctx *ctx, const void *data, size_t length);
void fs_sha256_final(fs_sha256_ctx *ctx, uint8_t out[FS_SHA256_BYTES]);

/* One-shot HMAC-SHA-256 with a 32-byte key. */
void fs_hmac_sha256(const uint8_t key[32], const void *message, size_t length,
                    uint8_t out[FS_SHA256_BYTES]);

/* Cryptographically strong random bytes. This function CANNOT fail: an
 * implementation whose entropy source errors must call
 * fs_abort_immediate(FS_ABORT_ENTROPY) rather than return short or predictable
 * bytes. Callers therefore do not check a result, exactly as the pre-seam
 * arc4random_buf callers did not. */
void fs_random_bytes(void *out, size_t length);

#define FS_ABORT_ENTROPY 87

#endif /* FS_CRYPTO_H */
```

### 4. POSIX implementation mapping — the zero-delta contract

`fs_platform_posix.c` and `fs_crypto_commoncrypto.c` must implement each member with exactly the call and flags below. Deviating from this table is how behaviour changes silently.

| Seam member             | POSIX implementation                                                                                      | Replaces line(s)                                 |
| ----------------------- | --------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| `fs_handle_valid`       | `h.raw >= 0`                                                                                              | every `fd >= 0` / `fd < 0` test                  |
| `fs_close`              | `close(h.raw)`                                                                                            | 341, 397, 414, 469, 502, 681-683…                |
| `fs_dup`                | `dup(h.raw)`                                                                                              | 385, 438                                         |
| `fs_open_root`          | `open(path, O_RDONLY\|O_DIRECTORY\|O_CLOEXEC\|O_NOFOLLOW_ANY)`                                            | 366                                              |
| `fs_open_dir_at`        | `openat(dir, leaf, O_RDONLY\|O_DIRECTORY\|O_CLOEXEC\|O_NOFOLLOW_ANY)`                                     | 390, 444                                         |
| `fs_open_read_at`       | `openat(dir, leaf, O_RDONLY\|O_CLOEXEC\|O_NOFOLLOW_ANY)`                                                  | 413, 501                                         |
| `fs_open_new_exclusive` | `openat(dir, leaf, O_WRONLY\|O_CREAT\|O_EXCL\|O_CLOEXEC\|O_NOFOLLOW_ANY, 0600)`; `EEXIST` → `FS_EXISTS`   | 466, 485                                         |
| `fs_open_new_stage`     | `openat(dir, leaf, O_RDWR\|O_CREAT\|O_EXCL\|O_CLOEXEC\|O_NOFOLLOW_ANY, 0600)`                             | 707                                              |
| `fs_mkdir_at`           | `mkdirat(dir, leaf, 0700)`                                                                                | 443                                              |
| `fs_rename_replace`     | `renameat(sdir, sleaf, ddir, dleaf)`                                                                      | 470                                              |
| `fs_unlink_at`          | `unlinkat(dir, leaf, 0)`; `ENOENT` → `FS_ABSENT`                                                          | 471, 494, 727                                    |
| `fs_stat_at`            | `fstatat(dir, leaf, &st, AT_SYMLINK_NOFOLLOW)`; `ENOENT` → `FS_ABSENT`                                    | 404, 664, 722                                    |
| `fs_stat_handle`        | `fstat(h.raw, &st)`                                                                                       | 306, 309, 360, 721, 733                          |
| `fs_dir_for_each`       | `openat(dir,".",O_RDONLY\|O_DIRECTORY\|O_CLOEXEC\|O_NOFOLLOW_ANY)` + `fdopendir` + `readdir` + `closedir` | 338-346, 628-650, 665-677                        |
| `fs_read_some`          | `read(h.raw, out, length)`                                                                                | 308                                              |
| `fs_write_some`         | `write(h.raw, in, length)`                                                                                | 878                                              |
| `fs_read_exact`         | the `read_all` loop (EINTR retry)                                                                         | 150-159 (for file handles)                       |
| `fs_write_all`          | the `write_all` loop (EINTR retry)                                                                        | 161-169 (for file handles)                       |
| `fs_seek_begin`         | `lseek(h.raw, 0, SEEK_SET)`                                                                               | 306                                              |
| `fs_durable_barrier`    | `fsync(h.raw)`                                                                                            | 469, 470, 488, 490, 494, 729, 743, 761, 763, 886 |
| `fs_volume_supported`   | `fstatfs` + `f_fstypename ∈ {"apfs","hfs"}`                                                               | 359-362                                          |
| `fs_dir_is_app_private` | `st_uid == geteuid() && (st_mode & 0077) == 0`                                                            | 430-431                                          |
| `fs_commit_create`      | `fclonefileat(staged, parent, leaf, CLONE_NOFOLLOW)`                                                      | 759-760                                          |
| `fs_commit_mkdir`       | `mkdirat(parent, leaf, 0700)`                                                                             | 763                                              |
| `fs_chan_read_exact`    | `read_all(STDIN_FILENO, …)`                                                                               | 981, 982, 983                                    |
| `fs_chan_write_all`     | `write_all(STDOUT_FILENO, …)`                                                                             | 788                                              |
| `fs_bootstrap_acquire`  | fds 3/6/7 + closes + `dup(4)`/`dup(5)`                                                                    | 974-978, 437-439                                 |
| `fs_abort_immediate`    | `_exit(code)`                                                                                             | 612, 931                                         |
| `fs_sha256_*`           | `CC_SHA256_Init/Update/Final`                                                                             | 281-310, 862                                     |
| `fs_hmac_sha256`        | `CCHmac(kCCHmacAlgSHA256, …)`                                                                             | 450, 456, 776, 785, 966                          |
| `fs_random_bytes`       | `arc4random_buf` (cannot fail)                                                                            | 440, 463, 855                                    |

`#ifndef O_NOFOLLOW_ANY / #define O_NOFOLLOW_ANY 0x20000000` (:41-43) moves to `fs_platform_posix.c` verbatim.

### 5. The portable translation unit's include set

After the split, `workspace_commit_helper.c` may include exactly:

```c
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>    /* snprintf only */
#include <stdlib.h>   /* malloc/calloc/realloc/free */
#include <string.h>   /* mem*/str* — but NOT strdup, see D-permitted-edits */
#include "fs_crypto.h"
#include "fs_platform.h"
```

No `<fcntl.h>`, `<unistd.h>`, `<dirent.h>`, `<errno.h>`, `<sys/*>`, `<CommonCrypto/*>`, and no `#if defined(__APPLE__)` / `#if defined(_WIN32)` anywhere in it.

### 6. TypeScript — the platform registry

```ts
// native-workspace-commit-helper.ts

/** How a platform's helper is named, verified and given its capabilities. */
export interface HelperPlatformProfile {
  readonly platform: NodeJS.Platform;
  /** Filename inside bin/ and inside <resourcesPath>/workspace-commit-helper/. */
  readonly executableName: string;
  /**
   * Verifies that the on-disk executable is the one this application signed.
   * REQUIRED and non-nullable: a platform cannot be registered without one,
   * which is the structural reason adding a platform cannot open a hole.
   */
  readonly verifyPackagedExecutable: (path: string) => boolean;
  /**
   * How the five private capabilities reach the child process. A closed union
   * that platform PRDs EXTEND rather than replace: FS-01 declares the one
   * member it implements, and FS-02 adds exactly one Win32 member once
   * SPIKE-W3 says which (`"win32-inherited-crt-fd"` or `"win32-stdin-prologue"`,
   * PRD-FS-02 D11). Widening this union is the visible half of registering a
   * platform; it must never become `string`.
   */
  readonly capabilityDelivery: "posix-inherited-fd";
}

/** Closed registry. FS-01 registers darwin and nothing else. */
export const HELPER_PLATFORM_PROFILES: ReadonlyMap<
  NodeJS.Platform,
  HelperPlatformProfile
>;

/** undefined => this platform has no commit helper and never spawns one. */
export function helperPlatformProfile(
  platform?: NodeJS.Platform,
): HelperPlatformProfile | undefined;

/** Unchanged for darwin; `platform` defaults to process.platform. */
export function resolveNativeWorkspaceCommitHelperPath(input: {
  readonly packaged: boolean;
  readonly resourcesPath?: string;
  readonly appPath: string;
  readonly platform?: NodeJS.Platform;
}): string;

export interface NativeWorkspaceCommitHelperConfig {
  // …all existing fields unchanged…
  /** Test seam. Defaults to process.platform. */
  readonly platform?: NodeJS.Platform;
}
```

`NativeWorkspaceCommitHelper.launch` replaces the `process.platform !== "darwin"` term (`:172`) with `helperPlatformProfile(config.platform) === undefined`. The packaged-verification branch (`:182-189`) becomes `config.verifyPackagedExecutable ?? profile.verifyPackagedExecutable`, so the existing injected-verifier test still observes its spy.

`workspace-production-authority.ts:85` replaces `platform !== "darwin"` with `helperPlatformProfile(platform) === undefined`. Nothing else in that file changes.

### 7. `build.mjs` — a builder table

```js
const BUILDERS = {
  darwin: buildDarwin, // exactly today's cc invocation, three sources
  // FS-02 adds: win32: buildWin32
};

const build = BUILDERS[process.platform];
if (build === undefined) {
  await writeUnsupportedSentinel(); // unchanged: 0o400, "unsupported platform\n"
  process.exit(0);
}
await build();
```

`buildDarwin` keeps every flag and the temp-file → `rename` → `chmod 0o500` sequence, with `sources = [fs_platform_posix.c, fs_crypto_commoncrypto.c, workspace_commit_helper.c]` and the output filename taken from the darwin profile (`workspace-commit-helper`, unchanged). The sentinel branch is now explicitly "no registered builder", not "not darwin".

`build.mjs --check-seam` (or `tools/check-seam.mjs`) additionally compiles to objects and runs the symbol/include audit described in the test plan.

### 8. Spine sketch → real names

The seam sketch in [README.md](README.md) is informal. This is the binding mapping; downstream PRDs that quote the sketch names follow this table.

| Spine sketch                   | Real name(s)                                                                                                                                                   |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `open_confined(root, relpath)` | portable `open_parent()` over `fs_open_root` + `fs_open_dir_at` + `fs_dir_for_each`                                                                            |
| `identity_of(handle)`          | `fs_stat_handle(...).id`, plus `fs_identity_equal` / `fs_identity_same_volume`                                                                                 |
| `stage_content(bytes)`         | portable `create_stage` / `command_write` / `command_seal` over `fs_open_new_stage`, `fs_write_some`, `fs_durable_barrier`, portable SHA-256                   |
| `commit_create(...)`           | `fs_commit_create`                                                                                                                                             |
| `commit_mkdir(...)`            | `fs_commit_mkdir`                                                                                                                                              |
| `commit_replace(...)`          | not declared yet — FS-06 adds `fs_commit_replace` when both platforms land                                                                                     |
| `commit_delete/move(...)`      | **not seam members at all.** FS-04 adds one primitive (`fs_rename_noreplace`); FS-05 adds two portable callers (`commit_delete`, `commit_move`) above the seam |
| `durable_barrier(handle)`      | `fs_durable_barrier`                                                                                                                                           |

Downstream PRDs were drafted in parallel against the informal sketch and drifted from this header in ways a reviewer will otherwise hit at compile time. The drifts, and their resolution — **this header is normative in every case**:

| PRD   | Drafted as                                                | Conforms to                                                                                                                                                         |
| ----- | --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FS-02 | `typedef HANDLE fs_handle`                                | `struct fs_handle { void *raw; }`, by value (D2)                                                                                                                    |
| FS-02 | `const wchar_t *leaf`                                     | `const char *leaf`, UTF-8; the provider owns its own encoding conversion and fails closed on invalid UTF-8 (§2 header comment)                                      |
| FS-02 | `fs_open_child_dir(parent, name, expect_volume)`          | `fs_open_dir_at(dir, leaf, out)`; the volume comparison stays **above** the seam in portable `open_parent` via `fs_identity_same_volume`                            |
| FS-02 | `fs_identity_of(h, out)`                                  | `fs_stat_handle(h, &meta)` then `meta.id`                                                                                                                           |
| FS-02 | `fs_create_staged(dir, name) -> handle`                   | `fs_open_new_stage(dir, leaf, out) -> enum fs_status`                                                                                                               |
| FS-02 | `fs_digest_handle(...)`                                   | **not a seam member.** Digesting is an anti-TOCTOU control, portable by D5                                                                                          |
| FS-02 | `fs_private_dir(h)`                                       | `fs_dir_is_app_private(h)`                                                                                                                                          |
| FS-02 | binding = `volume ‖ file_bytes ‖ file[0..n]`              | fixed-width `FS_IDENTITY_BINDING_BYTES` (D3) — the length-delimited form would change the macOS hashed bytes and break F2                                           |
| FS-02 | `SUPPORTED_HELPER_PLATFORMS: ReadonlySet<Platform>`       | `HELPER_PLATFORM_PROFILES` + `helperPlatformProfile()` (§6, D10). A bare `Set` reopens the hole D10 closes: membership without a required signature verifier        |
| FS-02 | `verifyPackagedWorkspaceCommitHelper(path, platform)`     | `HelperPlatformProfile.verifyPackagedExecutable` (§6) — one verifier per profile, not a platform switch inside one exported function                                |
| FS-02 | Win32 `journal_store` rewritten in place, no rename       | `journal_store` is **portable** (D1/D5); Win32 supplies `fs_rename_replace`. A per-platform journal write path is protocol logic below the seam                     |
| FS-04 | `stage_preimage(int parent, …, int trash, …)`             | **not a seam member at all** — a portable caller over `fs_rename_noreplace`. See the second note in §2's effects block                                              |
| FS-04 | `fstatfs(root_fd)` for the free-space budget              | `fs_volume_free_bytes(fs_handle, uint64_t *out)`, reserved in §2. Raw `fstatfs` cannot appear in the portable TU (§5) and has no POSIX spelling on Win32            |
| FS-05 | `typedef struct fs_handle fs_handle;` (incomplete)        | the complete by-value struct in §2 (D2)                                                                                                                             |
| FS-05 | `fs_open_confined(root, relpath, out)`                    | portable `open_parent()` over `fs_open_root` + `fs_open_dir_at` + `fs_dir_for_each`                                                                                 |
| FS-05 | `#define FS_ABSENT (-2)`                                  | `enum fs_status`'s `FS_ABSENT = 1`; no second status vocabulary                                                                                                     |
| FS-05 | `fs_identity_of(h, out)`                                  | `fs_stat_handle(h, &meta)` then `meta.id` — the same drift as FS-02's                                                                                               |
| FS-05 | `volume_supports_rename_excl(int root_fd)`, darwin only   | `fs_volume_supports_rename_excl(fs_handle)` on **both** providers (rule 2)                                                                                          |
| FS-05 | `fs_rename_noreplace` introduced by FS-05                 | introduced by **FS-04**, which lands first and needs it; FS-05 consumes it                                                                                          |
| FS-06 | `volume_supports_swap(int root_fd)`                       | `fs_volume_supports_swap(fs_handle)` — a capability query is a platform primitive                                                                                   |
| FS-06 | "FS-06 fills in the slot FS-01 declared"                  | FS-01 declares **no** replace slot (D9); FS-06 adds `fs_commit_replace` on both platforms in one change                                                             |
| FS-06 | `enum preimage_state {NONE,RETAINED,RESTORED,UNVERIFIED}` | FS-04's `enum preimage_disposition` is the one vocabulary. FS-06's `3 = UNVERIFIED` collides with FS-04's `3 = COLLECTED` — a wire-level conflict, not a naming one |

File names drifted too. The seam's files are `src/fs_platform.h`, `src/fs_crypto.h`, `src/fs_platform_posix.c`, `src/fs_crypto_commoncrypto.c`, and FS-02 adds `src/fs_platform_win32.c` + `src/fs_crypto_bcrypt.c`. FS-02's draft named `platform_win32.c` / `win32_crypto.c` / `win32_channel.c` and FS-05's named `platform.h` / `platform_darwin.c`; all conform. There is no separate channel translation unit — `fs_chan_read_exact` / `fs_chan_write_all` are members of the platform seam and live in the platform `.c`.

## Design

### D1. The seam is drawn at primitives, never at verbs

A verb (`create`, `mkdir`, later `replace`) is a _protocol_ concept: it has a precondition, a claim binding, a journal lifecycle and an outcome vocabulary. A primitive is what the kernel offers. If the seam were drawn at verbs, each platform would own its own precondition checking and its own outcome mapping, and the two would drift — which is precisely the failure the spine's last guardrail forbids. Drawing it at primitives means `commit_entry` (:752-766) is the _only_ function that changes shape, and it keeps its `sealed_stage_matches` re-attestation and its `fs_durable_barrier` call above the seam where both platforms are forced through them.

Concretely, these stay portable and are therefore identical on every platform forever: the path grammar (`path_is_safe`), the exact-directory-entry rule (`directory_has_exact_entry`), the snapshot policy (symlink refusal, `nlink != 1` refusal, kind and digest matching), the sealed-stage re-attestation, SHA-256 of staged content, the frame layout and its MAC, the sequence counter, the claim binding, the journal state machine, the restart downgrades, and every outcome/failure mapping.

### D2. `fs_handle` is a by-value struct, not an `int`

`typedef int fs_handle` would make the macOS diff smaller — every existing `int fd` becomes a pure alias. It would also let a POSIX-only developer keep writing `fd < 0` and `fd = -1` in the portable file, which compiles on macOS and cannot compile on Win32. The spine's reason for going Windows-first is to stop POSIX assumptions baking into the seam; a transparent `int` alias bakes them in by construction.

A one-member struct passed by value costs nothing at `-O2` (it is passed in a register on both ABIs) and makes every handle comparison a compile error until it is routed through `fs_handle_valid` / `FS_HANDLE_INVALID`. The resulting diff is large but is _entirely compiler-driven_: there is no site the compiler lets you miss. Behaviour is proven separately (D14), not by diff size.

The zero value is deliberately **not** valid: `FS_HANDLE_INVALID` is `-1` on POSIX and `INVALID_HANDLE_VALUE` on Win32, and `fs_handle_valid` on Win32 must also reject `NULL`, because `HeapAlloc`/`calloc` zeroing is the common way a stale handle field ends up looking real. That does not by itself fix the `close(0)` defect (fd 0 is a legitimate POSIX descriptor); D13 does.

### D3. Identity is 128-bit-capable, and its binding encoding is frozen byte-for-byte

`struct snapshot` (:78-86) stores `dev_t dev; ino_t ino;`. `FILE_ID_INFO` is 64-bit volume + 128-bit file id, so the field must widen now — waiting until FS-02 would mean FS-02 has to touch the claim binding, which is exactly the change F2 says is dangerous.

Widening is safe only if the _hashed bytes_ do not move. `fs_identity_binding` is therefore specified, not implementation-defined: the POSIX encoding must be `be64(volume) || file[0..7]` with `volume = (uint64_t)st_dev` (sign-extending, as the pre-seam `binding_u64(context, (uint64_t)snapshot->dev)` did) and `file[0..7] = be64(st_ino)`. Those 16 bytes are bit-for-bit the pre-seam `binding_u64(dev); binding_u64(ino);` pair. `FS_IDENTITY_BINDING_BYTES` is a compile-time per-platform constant rather than a runtime length because a length prefix would itself change the hashed bytes; a fixed width is unambiguous within a build, and binding digests are never compared across platforms (neither are grants — see F3).

`fs_identity_text_volume/file` exist for the same reason on the grant side: POSIX must keep emitting `%llu` decimals so existing grants keep matching. FS-02 chooses its own stable Win32 text format.

The identity is _not_ a `uint64_t` pair with a "high half unused on POSIX", because a 128-bit file id is not two meaningful halves and comparing it as such invites a half-comparison bug in `fs_identity_equal`.

### D4. Metadata crosses the seam as `fs_meta`; every policy decision stays above it

`snapshot_at` (:400-419) makes eight separate policy decisions on top of one `fstatat`: existence vs expectation, exact-entry bytes, symlink refusal, hard-link refusal, kind matching, digest matching, and the two "unexpected kind" refusals. All eight stay in the portable file. The seam returns facts (`kind`, `id`, `mode_bits`, `size`, `link_count`) and one tri-state (`FS_OK` / `FS_ABSENT` / `FS_ERROR`) that replaces the three places the pre-seam code inspected `errno` (:404 `ENOENT`, :487 `EEXIST`, :723 `ENOENT`). Nothing else needs `errno`, which is why `<errno.h>` leaves the portable file entirely.

`struct snapshot` becomes:

```c
struct snapshot {
  int exists;
  int kind;              /* wire values 0/1/2, unchanged */
  struct fs_identity id; /* was dev_t dev; ino_t ino;    */
  uint64_t mode_bits;    /* was mode_t mode              */
  uint64_t size;         /* was off_t size               */
  char digest[65];
};
```

`mode_bits` carries an explicit contract (see the header): a platform must not include a bit that mutates as a side effect of reading, because `snapshot_matches` (:421-427) compares it for equality and would otherwise manufacture spurious `PRECONDITION_DRIFT`.

### D5. The digest, the framing and the journal encoding live above the seam

`regular_digest_fd` (:304-311) is not "compute a hash": it is an anti-TOCTOU control — stat, seek, hash, re-stat, and reject if `dev`/`ino`/`size` moved. If each platform implemented "digest a handle", that control could quietly differ. It becomes a portable function over `fs_stat_handle` + `fs_seek_begin` + `fs_read_some` + `fs_sha256_*`, keeping the 8192-byte buffer and the exact loop.

The same applies to `respond` / `verify_frame` (:768-790, :962-969) and to `struct journal_record` and its MAC input length. A platform provider supplies HMAC bytes; it never decides what is MAC'd.

To make F1 mechanically enforced rather than remembered, the portable file gains:

```c
_Static_assert(sizeof(struct journal_record) == 358, "journal record layout is on-disk");
_Static_assert(offsetof(struct journal_record, mac) == 325, "journal MAC input length is on-disk");
```

The two constants are derived by hand from the declaration at :123-135 (`magic[8]`, four `uint8_t`, `uint16_t` at 12, `handle[37]` at 14, `claim[161]` at 51, `stage_dir[48]` at 212, `binding_digest[65]` at 260, `mac[32]` at 325, tail padding to a 2-byte alignment). If the arithmetic is wrong the build fails immediately and the implementer substitutes the compiler's values — that is the point of asserting rather than asserting in prose.

### D6. Crypto is a platform provider, pinned by known answers rather than by trust

Hand-rolling SHA-256/HMAC into the portable file would delete a seam member and guarantee identical digests, at the cost of unreviewed crypto in the mutation TCB. Using the platform provider keeps the code auditable but makes "the two platforms agree" an assumption. The assumption is discharged by evidence, not by argument:

- on macOS, the golden transcript (D14) contains real content digests, real frame MACs and real HMAC-derived claim filenames produced under fixed keys, so replaying it is a full known-answer test of `fs_sha256_*` and `fs_hmac_sha256`;
- on Windows there is no baseline to diff against, so FS-02 **must** add an explicit KAT (NIST SHA-256 for `""` and `"abc"`, RFC 4231 HMAC cases 1 and 2, plus a chunked-vs-one-shot equivalence over 1 MiB). This PRD states that obligation so FS-02 cannot inherit macOS's evidence.

`fs_random_bytes` cannot return a failure because `arc4random_buf` cannot fail and its callers (`:440`, `:463`, `:855`) do not check. Rather than add error paths that are dead on macOS, the contract requires a failing provider to `fs_abort_immediate(FS_ABORT_ENTROPY)`. A helper that cannot generate an unpredictable staging name must not continue; terminating is the fail-closed outcome.

### D7. The channel and the bootstrap are seam members, not "just fds"

Windows has no fd inheritance in the POSIX sense, and FS-02's D11 already owns delivering the five capabilities. If the bootstrap stayed inline in `main`, FS-02 would have to fork `main`. `fs_bootstrap_acquire` therefore returns exactly the five things the helper is given at launch, and the header pins the POSIX order (read 3, read 6, optional read 7, close 3/6/7/2, dup 4/5) because the order is security-relevant: keys are consumed before any directory capability is usable, and the diagnostic channel is closed before the first request is parsed.

One reordering is accepted: pre-seam, `make_private_run_dir` validated fds 4 and 5 with `private_dir_fd` _before_ duplicating them (:437-439); post-seam the duplication happens in the bootstrap and the validation happens immediately after on the duplicates. The duplicate refers to the same object, so the predicate result is identical, and on failure the process exits before writing anything. This is listed in the permitted-edit table.

### D8. Directory iteration tolerates re-visits and forbids misses — with the proof

`journal_reconcile_startup` (:626-651) rewrites journal records while enumerating the journal directory (`journal_store` at :636/:639/:645 creates a temp and renames it over the name being scanned). POSIX leaves the visibility of such mutations unspecified, so a Win32 implementation cannot be told simply "match readdir".

The contract in the header is derivable, not arbitrary:

- **Re-visits are safe.** The three downgrade branches fire only on `COMMITTING`, `AUTHORIZED` and `PREPARED`. Their targets are `INDETERMINATE` and `FAILED_BEFORE_EFFECT`, neither of which matches any branch, so a second visit is a no-op. `index_claim` (:518-526) updates in place and is idempotent.
- **Misses are not safe, though they are conservative.** A missed record is still read later by `journal_lookup_claim` (:656-678), and `journal_outcome_for` (:616-622) maps `COMMITTING` to `INDETERMINATE`, so a reconcile query still cannot report a false success. But `journal_acquire_claim` (:592-597) reads the raw state and maps a stale `AUTHORIZED` to `INDETERMINATE`, whereas the downgraded `FAILED_BEFORE_EFFECT` yields `FAILED`. A miss therefore changes an observable outcome for a second helper reusing the same claim, which is why the contract requires at-least-once delivery for pre-existing names.

A Win32 implementation that buffers `NtQueryDirectoryFile` results satisfies this; one that restarts the scan after each write does too. One that silently truncates on buffer refill does not.

### D9. A verb is declared in the header only when both platforms implement it

The header deliberately does **not** declare `fs_commit_replace`, `fs_commit_delete` or `fs_commit_move`, even though the spine sketches them and FS-05/FS-06 will add them. An undeclared slot cannot be half-filled; a declared one invites a platform to implement it, a portable caller to route to it, and the other platform to stub it. The spine's guardrail "above the seam, verbs land on both or neither" becomes a link-time property instead of a review convention.

The reserved names are listed as a comment in the effects section so the later PRDs use the same spelling.

### D10. The TypeScript gate becomes a closed registry that cannot be extended carelessly

`process.platform !== "darwin"` is a correct gate and an unhelpful one: to add Windows you delete the check and hope you remembered the signature verifier, the executable suffix, and the second gate in `workspace-production-authority.ts`. The registry inverts that. A platform exists only if someone wrote a `HelperPlatformProfile`, and the type makes `verifyPackagedExecutable` required and non-nullable, so "register win32" is not expressible without also writing the Authenticode verifier. `executableName` lives on the profile too, so the `.exe` suffix cannot be forgotten in one of the two places `resolveNativeWorkspaceCommitHelperPath` builds a path.

One subtlety that must not be got wrong: `resolveNativeWorkspaceCommitHelperPath` must **not** throw or return `undefined` for an unregistered platform. `native-workspace-commit-helper.test.ts:29-32` calls it at module scope, and that module is _loaded_ on the Linux CI runner even though its `describe` is skipped (`:33`). It therefore falls back to the literal `"workspace-commit-helper"` — today's exact behaviour — because it produces a path string, not an authority decision. The authority decision is the registry lookup inside `launch`.

### D11. Five independent gates keep an unimplemented platform read-only

Removing the darwin literal does not create a hole, because the literal was never the only gate. After FS-01, a platform with no implementation is refused by all of:

1. **Registry miss** — `helperPlatformProfile(platform)` is `undefined`, so `launch` throws `workspace_write_unsupported` before generating a key or spawning anything (`:171-181` shape preserved).
2. **Profile construction** — a profile cannot be written without a packaged-signature verifier (type-enforced, D10).
3. **No confinement probe** — `createProductionWorkspaceAuthority` returns `null` when `confinement === undefined` (`:89`), and `main/index.ts:640-671` constructs only `MacosWorkspaceConfinement`, whose `verify()` returns `"unavailable"` off darwin (`macos-workspace-confinement.ts:56-58`, `:88-89`). Registering a platform in the helper registry therefore still yields **no writable authority** until FS-03 lands a probe.
4. **No executable** — `build.mjs` emits the non-executable sentinel for any platform without a registered builder; `existsSync` passes but `spawn` of a `0o400` file fails and the launch rejects.
5. **Feature flag** — `RUNTIME_ENABLE_DESKTOP_FILESYSTEM` is off by default and fails closed (`feature-gate.ts:14-29`; gated at `main/index.ts:641` and `:673`). Spine D3.

Gates 1 and 3 are load-bearing and are pinned by new tests. A **tripwire** test asserts `[...HELPER_PLATFORM_PROFILES.keys()]` deep-equals `["darwin"]`, so FS-02 must consciously change it rather than drift into it.

### D12. `build.mjs` gains a builder table; the sentinel becomes the "no builder" branch

Today the sentinel is the `else` of `darwin`. After FS-02 that is wrong: `win32` will have a builder, and an accidental fall-through must never place a non-executable file where the client expects a signed binary. Making the sentinel the explicit "no registered builder" branch keeps its stated purpose (`build.mjs:11-13` — letting cross-platform CI verify the resource layout) and removes the fall-through hazard. Mode `0o400` and the exact sentinel bytes do not change, because `ci-desktop.yml` and the packaging filter both depend on the file existing.

### D13. The one intentional behaviour delta is `entry_init`, and it ships as its own commit

The `close(0)` defect (Context) is caused by `struct entry` fields being left to `calloc` zeroing. The seam makes it visible — under D2 a zeroed handle is invalid, so the _type_ stops lying — but the actual repair is initialising the array:

```c
static void entry_init(struct entry *entry) {
  memset(entry, 0, sizeof *entry);
  entry->parent = FS_HANDLE_INVALID;
  entry->destination_parent = FS_HANDLE_INVALID;
  entry->stage = FS_HANDLE_INVALID;
}
```

called immediately after the `calloc` at `:851` for every index, and from `parse_entry` (`:794`) and `free_entry` (`:685-686`) which already do it by hand. `destroy_prepared` additionally guards `prepared->entries != NULL` before the loop, closing the `calloc`-failure variant.

This is a behaviour change and is treated as one: it lands in **commit 2**, alone, with its own regression test and its own golden-transcript scenario recorded from the fixed build and marked `delta: intentional`. Commit 1's zero-delta claim therefore remains literally checkable. Rolling the fix into the extraction would make the phrase "zero behaviour change" untrue and unreviewable, which is worse than the extra commit.

### D14. How zero behaviour change is proven — and what the proof does not cover

Three independent proofs, in increasing strength.

**P1 — the existing suites, unedited.** All 15 tests in `native-workspace-commit-helper.test.ts` and every test in `workspace-production-authority.test.ts` pass, and **neither file appears in the PR diff**. This is a real constraint on the design: it is why `NativeWorkspaceCommitHelperConfig.platform` is optional, why `resolveNativeWorkspaceCommitHelperPath` gains an optional parameter rather than a required one, and why `resolveNativeWorkspaceCommitHelperPath` must not throw on Linux.

**P2 — a golden transcript, byte-compared.** `tools/build-baseline.mjs <ref>` compiles the pre-seam source from git into a scratch binary. `tools/transcript.mjs` drives a helper over the real framed protocol with **fixed** channel and journal keys and records, per scenario: the ordered response frames (status, failure code, decoded body), the final workspace tree (relative path → kind, mode, sha256), the journal directory (filenames, decoded record fields, MAC validity), and the process exit code. The recording from the baseline binary is committed under `test/golden/`; a darwin-gated vitest replays it against the current binary.

Four values are genuinely non-deterministic and are handled explicitly rather than ignored:

| Value                                                             | Source                      | Handling                                   |
| ----------------------------------------------------------------- | --------------------------- | ------------------------------------------ |
| `nwh_<32 hex>` handle (and the `c2j-`/`s-` names derived from it) | `fs_random_bytes` (:855)    | normalised to `<TOKEN_HANDLE>`             |
| `c2-<32 hex>` staging run directory                               | `fs_random_bytes` (:440)    | normalised to `<TOKEN_STAGE>`              |
| `volumeId` / `fileId` strings                                     | the test tmpdir's dev/ino   | normalised to `<TOKEN_VOL>`/`<TOKEN_FILE>` |
| `binding_digest`                                                  | hashes the tmpdir's dev/ino | **recomputed independently in JS**         |

The last row matters most: normalising the binding digest would delete the check that F2 exists to protect. Instead the harness recomputes it from the observed inode facts and asserts equality with the value the helper stored. The exact input, transcribed from `compute_prepared_binding:279-302` and `binding_snapshot:269-277`:

```
SHA256(
  be32(26) || "workspace-commit-effect-v1"
  || be64(root_dev) || be64(root_ino)
  || be32(entry_count)
  || for each entry, in order:
       u8(operation)
       || be32(len(relative_path))            || relative_path
       || u8(has_destination ? 1 : 0)
       || be32(len(destination_relative_path)) || destination_relative_path   /* "" when absent */
       || snapshot(source) || snapshot(destination)
       || be32(len(slot))            || slot                                  /* "" when absent */
       || be32(len(expected_digest)) || expected_digest                       /* "" when absent */
       || be64(expected_size)                                                 /* 0 when absent */
)
snapshot(s) = u8(exists ? 1 : 0) || u8(kind)
              || be64(dev) || be64(ino) || be64(mode) || be64(size)
              || be32(len(digest)) || digest
```

Root `dev`/`ino` come from `statSync(root, { bigint: true })`. For a `create`/`mkdir` entry the source snapshot is all zeros (`snapshot_at:404` returns early with a zeroed struct when the target is absent), and the destination snapshot is all zeros for every non-`MOVE` entry.

**P3 — mechanical seam completeness.** `tools/check-seam.mjs` compiles each source to an object and asserts:

- every undefined symbol in `workspace_commit_helper.o` matches `^_fs_[a-z0-9_]+$` or is in a small libc allowlist (`_malloc _calloc _realloc _free _memchr _memcmp _memcpy _memmove _memset _snprintf _strchr _strcmp _strcpy _strlen _strncmp _strrchr`) or a fortify/stack-protector helper (`^___.*_chk$`, `^___stack_chk_`);
- specifically **absent**: `_openat _fclonefileat _mkdirat _renameat _unlinkat _fstatat _fstat _fsync _fstatfs _readdir _fdopendir _closedir _lseek _fcntl _geteuid _arc4random_buf _read _write _close _open _dup __exit _strdup` and every `_CC*`;
- `workspace_commit_helper.c`'s `#include` lines are a subset of the list in §5, and the file contains no `__APPLE__` / `_WIN32` token;
- every symbol declared in `fs_platform.h` / `fs_crypto.h` is defined by the platform objects (`nm -g` defined set ⊇ declared set), so a partial provider fails the check rather than the link.

**What none of this proves.** Byte-equal responses and byte-equal disk state are not a syscall-level equivalence: identical flags, identical `fsync` ordering, memory behaviour and timing are asserted by the mapping table in §4 and by review, not by the harness. Concurrency beyond the scripted two-helper claim race is not covered — that scenario's winner is non-deterministic, so it is asserted as an invariant (exactly one `applied`, exactly one `c2c-` file) rather than by byte equality. And nothing here says anything about Windows.

### D15. CI: the golden transcript is only load-bearing if something runs it

Today zero CI jobs execute the helper (Context). A committed golden transcript that only ever runs on the author's laptop protects FS-01 and nothing after it. FS-01 therefore adds a second, narrow job to `ci-desktop.yml`:

```yaml
native-helper-macos:
  runs-on: macos-latest
  steps:
    [
      checkout,
      setup-node 22,
      npm ci,
      npm run build:workspace-commit-helper,
      node apps/desktop/native/workspace-commit-helper/tools/check-seam.mjs,
      npx vitest run main/capabilities/native-workspace-commit-helper*.test.ts,
    ]
```

The existing `ubuntu-latest` job is unchanged and keeps running the full desktop suite. The cost is one macOS runner on desktop-path PRs, which are already path-filtered (`ci-desktop.yml:14-31`). If that cost is refused, the fallback is: keep every test, drop the job, and make the DoD require the author to paste the local run — weaker, and it should be a conscious choice rather than an omission. See Open questions.

### Permitted edits during extraction

Commit 1 is a move, not a rewrite. Exactly these mechanical edits are allowed; anything else is a behaviour change and belongs in a later commit.

| Edit                                                                                                                             | Why it is behaviour-preserving                                                                                                                                                  |
| -------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `int *_fd` fields/locals → `fs_handle`; `-1`/`>= 0` → `FS_HANDLE_INVALID`/`fs_handle_valid`                                      | Pure type substitution; POSIX `fs_handle_valid` is literally `h.raw >= 0`.                                                                                                      |
| Field renames `parent_fd`→`parent`, `stage_fd`→`stage`, `destination_parent_fd`→`destination_parent`                             | Identifiers only. Keeping `_fd` would bake a POSIX vocabulary into portable code.                                                                                               |
| `dev_t dev; ino_t ino;` → `struct fs_identity id;` in `struct snapshot` and `struct prepared`                                    | D3 freezes the hashed and printed encodings.                                                                                                                                    |
| `mode_t mode` → `uint64_t mode_bits`, `off_t size` → `uint64_t size`                                                             | Both were already widened to `uint64_t` at every use (`binding_u64`, comparisons).                                                                                              |
| `struct stat sealed_stat` → `struct fs_meta sealed_meta`                                                                         | Only `st_dev`/`st_ino`/`st_size` were ever read (`sealed_stage_matches:745-747`, `command_seal:886-888`).                                                                       |
| Delete the dead first `CCHmac` into `mac` in `respond` (`:776`) and the two unused `CC_SHA256_CTX` declarations (`:769`, `:779`) | `mac` is unconditionally overwritten by `memcpy(mac, joined_mac, MAC_BYTES)` (`:786`) before any use; the only intervening exit (`malloc` failure, `:783`) does not read `mac`. |
| Replace `strdup` (`:375`, `:379`, `:381`, `:524`) with a local `static char *copy_string(const char *)`                          | `strdup` is POSIX, not C11, and is `_strdup` on MSVC. `malloc`+`memcpy` with the same `NULL`-on-failure contract.                                                               |
| Move the `O_NOFOLLOW_ANY` fallback `#define` (`:41-43`) into `fs_platform_posix.c`                                               | Same constant, same translation unit as its only users.                                                                                                                         |
| Bootstrap validate-then-dup → dup-then-validate (D7)                                                                             | Same kernel object; on failure the process exits before any write.                                                                                                              |
| Add the two `_Static_assert`s on `struct journal_record`                                                                         | Compile-time only.                                                                                                                                                              |
| Split into three translation units; `static` → external linkage for seam members only                                            | Everything not declared in a seam header stays `static`. Compiler flags unchanged, including `-Wl,-dead_strip`.                                                                 |

Explicitly **not** permitted in commit 1: unifying the two write loops (`write_all` retries `EINTR`; `command_write:878` treats any `written <= 0` as fatal — they must stay different), adding `EINTR` retry to the digest read loop (`:308`), replacing the `joined` buffer in `respond`/`verify_frame` with an incremental HMAC (it changes the 128 MiB allocation profile), dropping the redundant `fstat(staging_run_fd)` in `main` (`:977`), or "tidying" any refusal.

## Implementation plan

### Commit 1 — `feat(workspace-helper): draw the platform seam, move macOS behind it`

1. **NEW `src/fs_platform.h`** — verbatim as §2.
2. **NEW `src/fs_crypto.h`** — verbatim as §3.
3. **NEW `src/fs_platform_posix.c`** — implement every declaration per the §4 table. Includes `<dirent.h> <errno.h> <fcntl.h> <sys/clonefile.h> <sys/mount.h> <sys/stat.h> <sys/types.h> <unistd.h>` and carries the `O_NOFOLLOW_ANY` fallback define. `fs_read_exact`/`fs_write_all`/`fs_chan_*` share one static `EINTR`-retrying loop lifted from `read_all`/`write_all` (`:150-169`).
4. **NEW `src/fs_crypto_commoncrypto.c`** — `<CommonCrypto/CommonDigest.h>`, `<CommonCrypto/CommonHMAC.h>`, `<stdlib.h>` for `arc4random_buf`. Add `_Static_assert(sizeof(CC_SHA256_CTX) <= FS_SHA256_CTX_BYTES, …)`.
5. **EDIT `src/workspace_commit_helper.c`** —
   - includes reduced to §5; header comment updated to describe the seam and to keep the fd map as a _POSIX_ detail, cross-referencing `fs_bootstrap_acquire`;
   - `struct snapshot`, `struct entry`, `struct prepared` migrated per the permitted-edit table;
   - `regular_digest_fd` → `regular_digest_handle` over `fs_stat_handle`/`fs_seek_begin`/`fs_read_some`/`fs_sha256_*`, buffer and re-stat guard unchanged;
   - `directory_has_exact_entry`, `journal_reconcile_startup`, `journal_lookup_claim` rewritten as `fs_dir_for_each` callers with a small context struct each (`{const char *name; int found;}`, `{int ok;}`, `{const char *claim; int found; int error;}`);
   - `open_root`/`supported_root_fd`/`private_dir_fd` → portable `open_root`, `supported_root_handle`, `private_dir_handle` over `fs_open_root`, `fs_stat_handle`, `fs_volume_supported`, `fs_dir_is_app_private`;
   - `open_parent` keeps its segment splitting and its per-hop `directory_has_exact_entry` + kind + volume checks, using `fs_dup`, `fs_open_dir_at`, `fs_stat_handle`, `fs_identity_same_volume`;
   - `journal_store`, `journal_store_no_replace`, `journal_load`, `create_stage`, `cleanup_prepared_stages`, `make_private_run_dir` rewritten over the file/dir seam members with identical ordering and identical `fs_durable_barrier` placement;
   - `commit_entry` becomes `fs_commit_create` / `fs_commit_mkdir` + `fs_durable_barrier`, keeping `sealed_stage_matches` first and the `else return 0;` refusal;
   - `_exit(86)` → `fs_abort_immediate(86)`;
   - `main` reduced to `fs_bootstrap_acquire` + the existing checks + the existing loop over `fs_chan_read_exact`;
   - the two `_Static_assert`s added next to `struct journal_record`.
6. **EDIT `build.mjs`** — builder table per §7; darwin builder compiles the three sources with today's flags and output handling.
7. **NEW `tools/check-seam.mjs`** — the P3 audit; exits non-zero with the offending symbols/includes listed.
8. **NEW `tools/build-baseline.mjs`** — `git show <ref>:apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c` into a scratch dir, compile with the pre-seam flags, print the binary path.
9. **NEW `tools/transcript.mjs`** — record/replay driver: frames the protocol with fixed keys, applies the §D14 normalisation, recomputes the binding digest, snapshots tree + journal, returns a JSON scenario record.
10. **NEW `test/golden/*.json`** — one file per scenario, recorded from the baseline binary.
11. **NEW `main/capabilities/native-workspace-commit-helper.golden.test.ts`** — darwin-gated replay.
12. **EDIT `package.json`** — `"test:native-seam": "node native/workspace-commit-helper/tools/check-seam.mjs"`; `test` unchanged.
13. **EDIT `.github/workflows/ci-desktop.yml`** — the `native-helper-macos` job (D15).
14. **NEW `native/workspace-commit-helper/README.md`** — the seam contract, the §4 table, the D8 iteration contract, and "how to add a platform" for FS-02's author.

### Commit 2 — `fix(workspace-helper): initialise every prepared entry before it can be freed`

15. **EDIT `src/workspace_commit_helper.c`** — `entry_init`; call it for every index right after the `calloc` at `:851`; call it from `parse_entry` and `free_entry` in place of the hand-written resets; guard `destroy_prepared` on `prepared->entries != NULL`.
16. **NEW `main/capabilities/native-workspace-commit-helper.multi-entry.test.ts`** — the regression, darwin-gated.
17. **NEW `test/golden/multi-entry-invalid-first.json`** — recorded from the fixed build, marked `"delta": "intentional"`, with the baseline behaviour recorded alongside for the reviewer.

### Commit 3 — `refactor(desktop): replace the darwin literal with a closed helper-platform registry`

18. **EDIT `main/capabilities/native-workspace-commit-helper.ts`** — `HelperPlatformProfile`, `HELPER_PLATFORM_PROFILES` (darwin only), `helperPlatformProfile`, optional `platform` on the config, `platform` on `resolveNativeWorkspaceCommitHelperPath` with the §D10 fallback, `launch` and the packaged-verification branch rewired.
19. **EDIT `main/capabilities/workspace-production-authority.ts`** — `:85` uses the registry.
20. **NEW `main/capabilities/native-workspace-commit-helper.platform.test.ts`** — runs on every platform.
21. **NEW `main/capabilities/workspace-production-authority.platform.test.ts`** — runs on every platform.

## Test plan

### T1 — the existing suites are untouched and green (P1)

- `git diff --name-only <base>..HEAD` contains neither `main/capabilities/native-workspace-commit-helper.test.ts` nor `main/capabilities/workspace-production-authority.test.ts`.
- On macOS, all 15 `it` blocks in `native-workspace-commit-helper.test.ts` pass. Named explicitly because each pins a distinct control: retained-handle create + `already_applied` (:107), authenticated replay rejection (:140), packaged-signature refusal (:183), sealed-stage swap (:204), symlink/case/NFD/create-race refusal (:242), digest+size enforcement (:310), identity-checked abort cleanup (:338), replace/delete/move refusal (:360), post-loss `indeterminate` (:386), durable `already_applied` across restart (:406), two-helper claim race (:428), claim-binding mismatch (:488), tampered-journal boot refusal (:533), `committing` crash boundary (:559), `effect` crash boundary (:586).
- `workspace-production-authority.test.ts` passes on every platform, including its four `it.each` fail-closed cases.

### T2 — golden transcript equality (P2)

Scenarios, each a fresh helper with fixed keys and a deterministic fixture:

1. `create-nested` — mkdir fixture, prepare 1 create, 2 chunked writes, seal, commit, commit again → `applied` then `already_applied`.
2. `mkdir-restart` — prepare mkdir, commit, close, relaunch, `reconcileClaim` → `already_applied`.
3. `multi-entry-valid` — 2 creates + 1 mkdir in one prepare, all seal, one commit → `applied`; all three objects present.
4. `precondition-drift` — seal, then an external write to the target, then commit → `precondition_drift`, target keeps the external bytes.
5. `seal-mismatch` — write wrong bytes, seal rejects (`CONFLICT`), commit → `precondition_drift`, no file created.
6. `abort-cleanup` — prepare, abort → staging run directory empty, no target.
7. `verb-refusal` — prepare `replace`, `delete`, `move` (three runs) → `CONFLICT` each, target untouched. **This scenario has a planned expiry**: FS-05 narrows the refusal to `replace` and FS-06 retires it entirely, so the golden file records behaviour those PRDs deliberately change. It is marked `"expires_with": ["FS-05", "FS-06"]` in the recording, and whichever lands first re-records it as an intentional delta (the D13 pattern) rather than deleting it.
8. `confinement-refusal` — symlinked intermediate, case-mismatched segment, NFD segment, absolute path, `..` segment, backslash → refusal each.
9. `root-identity` — `ROOT_IDENTITY` on the workspace → two non-empty decimal strings (normalised).
10. `protocol-abuse` — unknown request type → `INVALID`; trailing bytes after a well-formed body → `INVALID`; a frame with a corrupted MAC → helper exits with no response; a replayed sequence → helper exits 0.
11. `crash-boundaries` — fault 1, 2, 3, 4 (four runs) → each recorded with its exit code, resulting tree and journal state, then a relaunch's `reconcileClaim` outcome.
12. `journal-tamper` — flip a byte in a `c2j-` record, relaunch → exit 1, no response.
13. `two-helper-race` — **invariant only**, excluded from byte equality: exactly one `applied`; the other is `already_applied` or `indeterminate`; exactly one `c2c-` file; a third helper reconciles to `already_applied`.

For scenarios 1-12 the assertion is: normalised response frames, final tree (path → kind, mode, sha256), journal filenames, decoded journal record fields, per-record MAC validity under the fixed key, and exit code are all equal between the baseline binary and the current binary. Plus, for every prepare, the stored `binding_digest` equals the independently recomputed value from §D14.

### T3 — seam completeness (P3)

- `check-seam.mjs` exits 0 and, in a deliberately broken variant (a test that reintroduces `#include <fcntl.h>` into the portable source in a temp copy), exits non-zero naming the include.
- The undefined-symbol audit lists no POSIX filesystem symbol, no `_CC*`, and no `_strdup` in `workspace_commit_helper.o`.
- Every declaration in both seam headers has a definition in the platform objects.
- The two `_Static_assert`s compile (a failing build is the assertion).

### T4 — the intentional delta (commit 2)

- On the **baseline** binary: prepare `[{create, "missing/a.md", …}, {mkdir, "b"}]` → rejects `workspace_conflict`, and the immediately following `rootIdentity(workspace)` rejects `workspace_helper_failed`. Record this in the golden file as the prior behaviour.
- On the **fixed** binary: the same prepare rejects `workspace_conflict`, and the following `rootIdentity(workspace)` **resolves**; a subsequent valid prepare+commit still reaches `applied`.
- The failing index is varied: index 0 of 2, index 1 of 3, and the final index of 2 (which was never broken) — all three keep the helper alive.

### T5 — the platform registry (runs on every platform, including the Linux CI job)

- `helperPlatformProfile("darwin")` is defined with `executableName === "workspace-commit-helper"` and `capabilityDelivery === "posix-inherited-fd"`.
- `helperPlatformProfile("win32")` and `helperPlatformProfile("linux")` are `undefined`.
- Tripwire: `[...HELPER_PLATFORM_PROFILES.keys()]` deep-equals `["darwin"]`.
- `NativeWorkspaceCommitHelper.launch({ platform: "linux", randomBytes: spy, … })` rejects with `workspace_write_unsupported` **and** `spy` was never called — proving the refusal precedes key generation and `spawn` (`:190`).
- `resolveNativeWorkspaceCommitHelperPath({ packaged: true, resourcesPath: "/R", appPath: "/A", platform: "darwin" })` === `/R/workspace-commit-helper/workspace-commit-helper`; with `platform: "linux"` it returns the same shape (no throw, no `undefined`) — the Linux-CI module-load guarantee from D10.
- `createProductionWorkspaceAuthority({ platform: "win32", packaged: true, production: true, confinement: {verify: async () => "enforced"}, … })` → `null`, and `launchHelper` was not called.
- `createProductionWorkspaceAuthority({ platform: "darwin", confinement: undefined, … })` → `null`, `launchHelper` not called.

### T6 — packaging is unaffected

- `electron-builder.yml` and `build/sign-nested.js` are absent from the PR diff.
- On macOS, `npm run build:workspace-commit-helper` still produces `bin/workspace-commit-helper`, mode `0500`, and `file(1)` reports a Mach-O executable for the host arch.
- On Linux, the same command still produces the `0400` sentinel containing exactly `"unsupported platform\n"`.

## Open questions and spikes

1. **macOS CI runner budget (decision, not a spike).** D15 adds a `macos-latest` job. macOS runners bill at a higher multiplier than Linux. The alternative is author-run evidence pasted into the PR. This needs an explicit answer before FS-01 merges, because FS-02…FS-09 all inherit whichever choice is made. Recommendation: add the job — a golden transcript nobody runs is documentation, not a control.
2. **`FS_IDENTITY_BINDING_BYTES` on Win32 (spike, FS-02).** 24 is a placeholder for `be64(volume) || file[0..15]`. The spike is: does `FILE_ID_INFO.FileId` remain stable across a rename within a volume and across a close/reopen on NTFS and on ReFS? If it does not, the binding must fall back to a different Win32 encoding and FS-02 must say so; nothing in FS-01 changes either way, because the constant is per platform.
3. **`NtQueryDirectoryFile` under concurrent renames (spike, FS-02/FS-03).** D8 requires at-least-once delivery of pre-existing names while the caller renames into the same directory. The test is: enumerate a directory of 500 entries while a second thread renames temp files over 100 of them, and assert every pre-existing name is delivered at least once. If Windows cannot guarantee it, `journal_reconcile_startup` must snapshot names before mutating — a portable change, but one that must be known before FS-02 writes the iterator.
4. **`_Static_assert` values.** `sizeof(struct journal_record) == 358` and `offsetof(mac) == 325` are derived by hand here. The compiler is the authority; if it disagrees, use its numbers and note them in the PR. (They must not be "fixed" by changing the struct — see F1.)
5. **Splitting one TU into three and `-O2`.** Cross-TU inlining is lost, so the machine code differs. Nothing in the protocol depends on inlining, and the crash-boundary faults (`:612`, `:931`) fire on program order rather than instruction scheduling. Named because a reviewer will ask; the golden transcript's crash-boundary scenarios are the check.
6. **Sentinel collision after FS-02.** Once `win32` has a builder, no sentinel should ever be produced on Windows. FS-01 makes that the "no registered builder" branch (D12) but does not add a _positive_ assertion that a packaged Windows build contains a real executable. FS-02 owns that; flagged here so it is not lost.
7. **What P2 cannot see.** The harness compares outputs and disk state, not syscalls. If a reviewer wants stronger evidence, the option is a `ktrace`/`dtruss` comparison of the two binaries, which requires SIP configuration and is not proposed. The §4 mapping table plus review is the compensating control; that trade should be acknowledged rather than hidden.

## Definition of done

- [ ] `src/fs_platform.h` and `src/fs_crypto.h` exist, declare exactly the members in §2 and §3, and contain no platform-conditional logic beyond the `fs_handle` / constant definitions.
- [ ] `src/workspace_commit_helper.c` includes only the seven headers in §5, contains no `__APPLE__` / `_WIN32` token, and `nm -u` on its object shows only `_fs_*` plus the libc/fortify allowlist.
- [ ] `src/fs_platform_posix.c` + `src/fs_crypto_commoncrypto.c` define every declared seam member and implement each one exactly as the §4 mapping table specifies.
- [ ] No verb beyond `fs_commit_create` / `fs_commit_mkdir` is declared in `fs_platform.h`.
- [ ] `_Static_assert` pins `sizeof(struct journal_record)` and `offsetof(struct journal_record, mac)`; the build passes with them.
- [ ] `git diff --name-only <base>..HEAD` contains neither existing helper/authority test file.
- [ ] All 15 tests in `native-workspace-commit-helper.test.ts` pass on macOS, unmodified.
- [ ] Golden transcripts for scenarios 1-12 replay byte-identically (after the four documented normalisations) against the refactored binary; scenario 13 passes its invariants.
- [ ] Every recorded prepare's `binding_digest` equals the independently recomputed value, proving the identity binding encoding did not move.
- [ ] `tools/check-seam.mjs` exits 0 on the real tree and non-zero on a deliberately seam-violating copy.
- [ ] Commit 2 is a separate commit containing only `entry_init`, the `destroy_prepared` NULL guard, and their tests; the multi-entry regression fails on the baseline binary and passes on the fixed one.
- [ ] `HELPER_PLATFORM_PROFILES` has exactly one entry (`darwin`), and the tripwire test asserts it.
- [ ] `NativeWorkspaceCommitHelper.launch` and `createProductionWorkspaceAuthority` contain no string literal `"darwin"`; both consult `helperPlatformProfile`.
- [ ] `launch({platform:"linux"})` rejects `workspace_write_unsupported` without generating a channel key or spawning.
- [ ] `resolveNativeWorkspaceCommitHelperPath` still returns a path (no throw) for an unregistered platform, and the desktop test suite still loads on `ubuntu-latest`.
- [ ] `electron-builder.yml`, `build/sign-nested.js`, `workspace-authority.ts` and `main/index.ts` are absent from the PR diff.
- [ ] On Linux, `npm run build:workspace-commit-helper` still emits the `0400` sentinel with unchanged bytes; on macOS it still emits a `0500` Mach-O at the same path.
- [ ] `ci-desktop.yml` runs the seam check and the native helper tests on macOS (or, if the runner budget is refused, the PR carries the pasted local run and the decision is recorded here).
- [ ] `native/workspace-commit-helper/README.md` documents the seam, the §4 table, the D8 iteration contract, and the procedure for adding a platform.

## Out of scope

- Any Win32 implementation — `fs_platform_win32.c`, `fs_crypto_bcrypt.c`, Authenticode verification, capability delivery on Windows (FS-02), Windows confinement and attestation (FS-03).
- Any new verb: `replace`, `delete`, `move` (FS-05/FS-06), preimage and trash (FS-04), post-crash reconciliation beyond what exists (FS-07).
- Any protocol change: `PROTOCOL` stays 2, `JOURNAL_VERSION` stays 3, the request/operation/outcome/failure enums are untouched, no new request type.
- Relaxing `parse_entry`'s refusal of `REPLACE`/`DELETE`/`MOVE` (`:801`).
- A Linux build of the helper, or a test-only seam provider. Tempting — it would give PR CI real coverage of the portable half — but a second, non-shipping provider is a second implementation to keep honest, and the moment it diverges the coverage is worse than none.
- Generalising `WorkspaceConfinementProbe` beyond macOS (FS-03), or touching `RUNTIME_ENABLE_DESKTOP_FILESYSTEM` (spine D3, FS-09).
- Reducing the 128 MiB `joined` buffer in `respond`/`verify_frame` via incremental HMAC, and any other performance or memory work.
- The `native/workspace-fs` N-API read-side addon, `host-fs.ts`, and everything on the read path.
- The local sandbox provider (FS-08).

## Guardrails

- Do **not** add a second write path. Every mutation still goes through the gateway, the stage and this commit protocol.
- Do **not** weaken confinement to make the seam tidier: `path_is_safe`, `directory_has_exact_entry`, the symlink and hard-link refusals, the volume gate and the private-directory attestation keep their exact predicates.
- Do **not** report an outcome that was not observed; `INDETERMINATE` remains a required result and no restart path replays an effect.
- Do **not** let the model choose a path; nothing in this PRD touches grant issuance.
- Do **not** implement a verb on one platform only — and therefore do not declare a verb in `fs_platform.h` before the PRD that implements it everywhere.
- Do **not** change `PROTOCOL`, `JOURNAL_VERSION`, `struct journal_record`'s layout, the bytes hashed by `compute_prepared_binding`, or the darwin root-identity string format.
- Do **not** put `#if defined(__APPLE__)` or `#if defined(_WIN32)` in `workspace_commit_helper.c`.
- Do **not** implement a seam member above the seam, or protocol logic (digest, framing, journal encoding, outcome mapping) below it.
- Do **not** edit `native-workspace-commit-helper.test.ts` or `workspace-production-authority.test.ts`.
- Do **not** register a platform without a packaged-signature verifier and a confinement probe.
- Do **not** let `build.mjs` emit an executable for a platform with no registered builder.
- Do **not** "improve" behaviour while extracting; the single permitted delta is `entry_init`, in its own commit, with its own test.
