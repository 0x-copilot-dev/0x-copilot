# PRD-FS-02 — Windows commit helper: create and mkdir

**Status:** specified
**Depends on:** FS-01 (platform seam + macOS moved behind it, zero behaviour Δ)

## Implementer brief

Windows has no write capability at all today: the helper build emits a
non-executable sentinel and the client refuses to spawn on any non-darwin
platform. This PRD implements the Win32 half of the FS-01 seam for exactly two
verbs — `create` and `mkdir` — plus the build, packaging, signature-verification,
and CI path that produces and admits the binary. Every protocol layer above the
seam (framing, MAC, sequence, claim binding, journal state machine, conservative
restart) is reused unchanged. Read the program spine
(`docs/plan/filesystem-capability/README.md`) first; D1/D2/D3 are locked.

## Context

What is true today, verified in code.

**There is no Windows write path, by construction.**
`apps/desktop/native/workspace-commit-helper/build.mjs:9-17` short-circuits on
`process.platform !== "darwin"` and writes the string `"unsupported platform\n"`
to `bin/workspace-commit-helper` with mode `0o400` — deliberately not
executable. `native-workspace-commit-helper.ts:171-181` then rejects
`process.platform !== "darwin"` before `spawn`, throwing
`workspace_write_unsupported`. A second, independent gate exists in
`workspace-production-authority.ts:83-91`, which returns `null` (no writable
authority at all) unless `platform === "darwin" && packaged && production`.

**The helper is macOS-specific in its primitives, not in its protocol.**
`workspace_commit_helper.c` is 1003 lines, of which the actual filesystem effect
is `commit_entry()` at lines 752-766: `fclonefileat(stage_fd, parent_fd, leaf,
CLONE_NOFOLLOW)` + `fsync(parent_fd)` for `CREATE`, and `mkdirat(parent_fd,
leaf, 0700)` + `fsync(parent_fd)` for `MKDIR`. Everything else — the framed
authenticated channel (`verify_frame`, l.962; `respond`, l.768), the sequence
counter (l.985), the claim-binding digest (`compute_prepared_binding`, l.279),
the HMAC-protected journal (`journal_store`, l.460; `journal_load`, l.499), the
`O_EXCL` claim-exclusion primitive (`journal_store_no_replace`, l.482), the
state machine (`claim_transition_allowed`, l.542), and the conservative restart
(`journal_reconcile_startup`, l.626) — is portable logic that happens to be
written against POSIX calls.

**The verbs beyond create/mkdir are refused in C.** `parse_entry` at
`workspace_commit_helper.c:801` rejects any operation that is not `CREATE` or
`MKDIR`, with the reason stated in the comment at l.797-800: macOS has no kernel
compare-and-swap rename bound to an observed inode+digest. The wire enum already
carries `REPLACE`/`DELETE`/`MOVE` (l.64) and the TS client already encodes them
(`native-workspace-commit-helper.ts:48-54`, `613-628`), so adding them later is
an above-the-seam change, not a protocol change. FS-05/FS-06 own that.

**Windows native code already exists on the read side, and is untested.**
`apps/desktop/native/workspace-fs/src/workspace_fs.c:186-348` implements a
reparse-refusing, parent-handle-relative `NtCreateFile` walk. Its own header
comment at l.189-190 says "UNTESTED ON A WINDOWS HOST in this environment". Its
`binding.gyp:9-13` already links `ntdll` and defines `UNICODE`. That file is the
proof that the walk primitive is expressible; it is not proof that it works.

**Packaging already reserves a slot for the helper.**
`electron-builder.yml:44-47` copies `native/workspace-commit-helper/bin` to
`<resourcesPath>/workspace-commit-helper` with the literal filter
`"workspace-commit-helper"` (no extension).
`resolveNativeWorkspaceCommitHelperPath` (`native-workspace-commit-helper.ts:482-500`)
joins that path with no `.exe` suffix. `build/sign-nested.js:23-45` signs the
nested macOS helper with the pinned identifier
`com.0x-copilot.workspace-commit-helper`, which
`verifyPackagedWorkspaceCommitHelper` (`native-workspace-commit-helper.ts:507-515`)
then re-verifies with `/usr/bin/codesign --verify --strict -R 'anchor apple
generic and identifier "…"'` before the helper is granted a channel.

**Windows releases are currently unsigned.**
`.github/workflows/release-desktop.yml:147-149` emits a warning and builds
unsigned when `WIN_CSC_LINK` is absent, and `electron-builder.yml:79-82`
installs per-user (`perMachine: false`) under `%LOCALAPPDATA%`. Both facts
change what a signature check can promise on Windows; see D15.

**PR CI never compiles native code.** `ci-desktop.yml:61` runs the whole desktop
job on `ubuntu-latest`, so `build:workspace-commit-helper`
(`apps/desktop/package.json:11`) only ever produces the sentinel in CI. The
helper's own test suite is skipped off-darwin
(`native-workspace-commit-helper.test.ts:32`).

## Interfaces consumed

- **FS-01's platform seam** — `src/fs_platform.h` and `src/fs_crypto.h`. FS-02
  implements the Win32 side of it in `src/fs_platform_win32.c` and
  `src/fs_crypto_bcrypt.c`. **FS-01 is normative for every name, signature and
  type**; §1 below gives the Win32 _semantics_ for the confinement- and
  effect-critical members only, and is not a second header. FS-01's rule 1 —
  "implement every declaration; a partial provider must not link" — means the
  Win32 provider owes bodies for all of `fs_handle_valid`, `fs_close`, `fs_dup`,
  `fs_identity_binding`, `fs_identity_equal`, `fs_identity_same_volume`,
  `fs_identity_text_volume`, `fs_identity_text_file`, `fs_stat_at`,
  `fs_stat_handle`, `fs_open_root`, `fs_open_dir_at`, `fs_open_read_at`,
  `fs_open_new_exclusive`, `fs_open_new_stage`, `fs_mkdir_at`,
  `fs_rename_replace`, `fs_unlink_at`, `fs_dir_for_each`, `fs_read_some`,
  `fs_write_some`, `fs_read_exact`, `fs_write_all`, `fs_seek_begin`,
  `fs_durable_barrier`, `fs_volume_supported`, `fs_dir_is_app_private`,
  `fs_commit_create`, `fs_commit_mkdir`, `fs_chan_read_exact`,
  `fs_chan_write_all`, `fs_bootstrap_acquire`, `fs_abort_immediate`, and the
  five `fs_crypto.h` members — not only the dozen named below.
  [FS-01 §8](PRD-FS-01-platform-seam.md) tabulates every place this PRD's first
  draft drifted (wide-char leaves, `HANDLE` typedef, `fs_digest_handle`,
  `fs_private_dir`, the identity binding encoding) and how each conforms.
- **Protocol v2**, unchanged: `PROTOCOL = 2`
  (`workspace_commit_helper.c:45`, `native-workspace-commit-helper.ts:28`),
  request enum (l.58-63), operation enum (l.64), outcome enum (l.65-66), failure
  enum (l.67), journal states (l.68-71).
- **`NativeWorkspaceAuthority`** (`workspace-authority.ts:222-253`) — unchanged.
- **`NativeWorkspaceCommitHelperConfig`**
  (`native-workspace-commit-helper.ts:86-126`) — extended, not replaced (see
  below).
- **`assertNativeWorkspaceCanonicalPath`** (`workspace-authority.ts:17-29`), the
  above-the-seam ASCII segment rule that mirrors `path_is_safe`
  (`workspace_commit_helper.c:313-332`).
- **`classifyForbiddenRoot`** (`path-validation.ts:356+`), already Windows-aware:
  `isFilesystemRoot` (l.340-343) recognises `C:\`, and `splitPathSegments`
  (l.327-329) splits on both separators.

## Interfaces exposed

### 1. Win32 seam implementation (C)

**FS-02 declares no header.** [FS-01 §2 and §3](PRD-FS-01-platform-seam.md) are the
only declarations of `fs_platform.h` / `fs_crypto.h`; FS-02 writes
`src/fs_platform_win32.c` and `src/fs_crypto_bcrypt.c` against them verbatim.
This section gives the Win32 **semantics** for the confinement- and
effect-critical members, in FS-01's names and signatures. Where an earlier draft
of this PRD wrote its own header, [FS-01 §8](PRD-FS-01-platform-seam.md) tabulates
every difference and FS-01 wins.

The four rules from FS-01 §2 that bind hardest here: the handle is a by-value
`struct fs_handle { void *raw; }` and never a bare `HANDLE`; every `leaf` is
`const char *` UTF-8, converted inside the provider, failing closed on invalid
UTF-8 (`MB_ERR_INVALID_CHARS`); the identity binding is fixed-width
`FS_IDENTITY_BINDING_BYTES` and not length-delimited; and `fs_handle_valid` must
reject `NULL` **and** `INVALID_HANDLE_VALUE`.

| FS-01 member                             | Win32 semantics FS-02 owes                                                                                                                                                                                  |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `fs_open_root(const char*, fs_handle*)`  | Grammar-check, convert, `NtCreateFile` per FS-03 §4. **FS-03 is normative for the flags**; FS-02 supplies the body.                                                                                         |
| `fs_open_dir_at(dir, leaf, out)`         | One component, `oa.RootDirectory = dir.raw`, D4's four refusals. The volume comparison stays **above** the seam in portable `open_parent`.                                                                  |
| `fs_stat_handle(h, &meta)`               | `GetFileInformationByHandleEx(FileIdInfo)` → `meta.id`; `FileStandardInfo`/`FileBasicInfo` → `kind`, `size`, `link_count`, `mode_bits` (D6).                                                                |
| `fs_open_new_stage(dir, leaf, out)`      | `FILE_CREATE`, `FILE_NON_DIRECTORY_FILE\|FILE_OPEN_REPARSE_POINT`, access incl. `DELETE\|WRITE_DAC`, share **0**. `STATUS_OBJECT_NAME_COLLISION` → `FS_EXISTS`.                                             |
| `fs_open_new_exclusive(dir, leaf, out)`  | Same disposition, write-only; this is the journal's exclusion primitive (D9).                                                                                                                               |
| `fs_rename_replace(sd, sl, dd, dl)`      | Open `sl` under `sd` with `DELETE`, then `FileRenameInfoEx` with `FILE_RENAME_REPLACE_IF_EXISTS \| FILE_RENAME_POSIX_SEMANTICS`, `RootDirectory = dd.raw`. Used **only** inside the journal directory (D9). |
| `fs_commit_create(staged, parent, leaf)` | `FileRenameInfoEx`, `Flags = 0` (no replace), `RootDirectory = parent.raw` (D2).                                                                                                                            |
| `fs_commit_mkdir(parent, leaf)`          | `NtCreateFile(FILE_CREATE \| FILE_DIRECTORY_FILE)` (D3). Never `CreateDirectoryW`.                                                                                                                          |
| `fs_volume_supported(h)`                 | NTFS + local (D7). `fs_volume_supports_*` capability queries are FS-04/05/06's and are not implemented here.                                                                                                |
| `fs_dir_is_app_private(h)`               | Owner SID == token user SID, and the DACL grants no SID outside `{owner, SYSTEM, BUILTIN\Administrators}`.                                                                                                  |
| `fs_durable_barrier(h)`                  | `FlushFileBuffers`. Defines `FS_DIRECTORY_BARRIER_PROVEN 0` — see D8.                                                                                                                                       |
| `fs_dir_for_each(dir, visit, ctx)`       | `NtQueryDirectoryFileEx`, honouring FS-01 D8's at-least-once contract. SPIKE-W6.                                                                                                                            |
| `fs_bootstrap_acquire(out)`              | D11. This is the member with no POSIX analogue and it gates everything else.                                                                                                                                |

Two members are **not** FS-02's, and were in the first draft:
`fs_digest_handle` (digesting is a portable anti-TOCTOU control, FS-01 D5) and
any `fs_identity_of` (use `fs_stat_handle(...).id`).

One member FS-04 will add is called out here because FS-02 owns the Win32
provider file and FS-01 rule 1 makes a partial provider a link error:
`fs_volume_free_bytes`. Its Win32 body is **unverified** —
`GetDiskFreeSpaceExW` is path-based and therefore inadmissible;
`NtQueryVolumeInformationFile(FileFsFullSizeInformation)` on a directory handle
is the expected form. SPIKE-W5.

### 2. Root-identity wire format

`command_root_identity` (`workspace_commit_helper.c:837-843`) returns two
strings. macOS emits decimal `st_dev` / `st_ino` and **must not change** — the
strings are persisted in the grant store (`grant-store.ts:138-151`) and compared
on every prepare (`workspace-authority.ts:960-965`), so a format change silently
invalidates every existing grant. Windows adds a new, stable format:

| field      | macOS (unchanged) | Win32 (new)                                    |
| ---------- | ----------------- | ---------------------------------------------- |
| `volumeId` | decimal `st_dev`  | 16 lowercase hex chars of `VolumeSerialNumber` |
| `fileId`   | decimal `st_ino`  | 32 lowercase hex chars of `FILE_ID_128`        |

The strings are opaque to every consumer (`GrantRootIdentity`, `types.ts:38-41`,
is `{ volumeId: string; fileId: string }`), so no caller changes. Grants never
migrate between platforms.

### 3. TypeScript client changes

FS-01 D10 already replaced the `process.platform !== "darwin"` literal with a
**closed registry** whose entries cannot exist without a signature verifier.
FS-02 therefore adds exactly one entry and one verifier — it does **not**
introduce a parallel `SUPPORTED_HELPER_PLATFORMS` set, which would be a second
membership test that can be widened without writing a verifier.

```ts
// native-workspace-commit-helper.ts
const WORKSPACE_HELPER_IDENTIFIER = "com.0x-copilot.workspace-commit-helper";
/** Authenticode subject CN pinned for the Windows helper. */
const WORKSPACE_HELPER_WINDOWS_SUBJECT = "0x Copilot"; // exact CN: see D14

// FS-01 §6's registry gains its second entry. `verifyPackagedExecutable` is a
// REQUIRED, non-nullable member of HelperPlatformProfile, so this line is not
// writable without the Authenticode verifier below — that is the point of D10.
HELPER_PLATFORM_PROFILES.set("win32", {
  platform: "win32",
  executableName: "workspace-commit-helper.exe",
  verifyPackagedExecutable: verifyPackagedWorkspaceCommitHelperWin32,
  capabilityDelivery: /* SPIKE-W3 decides; see D11 */ "win32-inherited-crt-fd",
});

/** WinVerifyTrust + pinned signer CN. False when the addon is absent. */
function verifyPackagedWorkspaceCommitHelperWin32(path: string): boolean;
```

`HelperPlatformProfile.capabilityDelivery` is FS-01's discriminant and gains one
member — `"win32-inherited-crt-fd"` for D11 Path A, or
`"win32-stdin-prologue"` for Path B. SPIKE-W3 picks; FS-02 must not register the
platform with a value it did not measure.

`resolveNativeWorkspaceCommitHelperPath` needs no signature change: FS-01 already
gave it an optional `platform` and moved the filename onto the profile, so the
`.exe` suffix comes from `profile.executableName` and cannot be forgotten in one
of the two branches.

FS-01's tripwire test (`[...HELPER_PLATFORM_PROFILES.keys()]` deep-equals
`["darwin"]`) is updated to `["darwin", "win32"]` in this PR — the one edit FS-01
deliberately forced to be conscious.

`NativeWorkspaceCommitHelperConfig` gains no new required field. The existing
`verifyPackagedExecutable` test seam (l.113) keeps working because FS-01 made
`launch` read `config.verifyPackagedExecutable ?? profile.verifyPackagedExecutable`.

### 4. Authenticode verification addon

```ts
// apps/desktop/native/win-authenticode/index.d.ts
export interface WinAuthenticode {
  /**
   * WinVerifyTrust(WINTRUST_ACTION_GENERIC_VERIFY_V2, WTD_CHOICE_FILE) plus a
   * signer-subject-CN equality check. Returns false on any error. Never
   * throws; never performs network revocation lookups.
   */
  verifyAuthenticode(path: string, expectedSubjectCn: string): boolean;
}
export function loadNative(): WinAuthenticode | undefined;
```

### 5. Build outputs

| platform | build.mjs output                       | packaged path                                                         |
| -------- | -------------------------------------- | --------------------------------------------------------------------- |
| darwin   | `bin/workspace-commit-helper` (0500)   | `<resourcesPath>/workspace-commit-helper/workspace-commit-helper`     |
| win32    | `bin/workspace-commit-helper.exe`      | `<resourcesPath>\workspace-commit-helper\workspace-commit-helper.exe` |
| other    | `bin/workspace-commit-helper` sentinel | present but non-executable; client refuses it                         |

## Design

### D1. Scope is exactly the two verbs that cannot collide with an open handle

`create` and `mkdir` are both _no-replace_: they fail if anything already
occupies the leaf. Neither can be raced into destroying user data, and neither
needs a preimage, a trash, or a compare-and-swap. That is why they are the
correct first Windows slice, and why FS-02 can land before FS-04 (preimage +
trash) exists. `parse_entry`'s refusal at `workspace_commit_helper.c:801` stays
exactly as written; FS-02 does not touch it. Verb parity across platforms is
preserved: after FS-02, both platforms support `{create, mkdir}` and neither
supports the rest.

### D2. `create` is a private staged file renamed by handle into the retained parent

NTFS has no `fclonefileat` equivalent — no atomic "materialise this open file
under that directory handle at that name". The closest documented primitive with
the same shape is rename-by-handle:

```
SetFileInformationByHandle(stagedHandle, FileRenameInfoEx, &info, size)
  info.Flags         = 0                    /* NOT REPLACE_IF_EXISTS       */
  info.RootDirectory = retainedParentHandle /* destination is parent-relative */
  info.FileName      = leaf (UTF-16, FileNameLength in bytes, no NUL)
```

This is intended to preserve the three properties `fclonefileat` gave us. Property
1 follows from the call's own shape; properties 2 and 3 are **claims about Win32
semantics that no host in this program has executed**, and they are the load-bearing
half — so they are marked and spiked rather than asserted:

1. **The source is a handle, not a name.** The bytes that land are the bytes we
   sealed; there is no window in which a filename we staged under can be swapped
   (the hazard `cleanup_prepared_stages`, l.716-730, exists to detect).
2. **The destination is (parent handle, single leaf).** `RootDirectory` is
   intended to make the name parent-relative.
   _unverified — spike required:_ that `SetFileInformationByHandle(FileRenameInfoEx)`
   honours a directory `HANDLE` in `RootDirectory` at all, and that it **rejects**
   a `FileName` containing `\` or `/` rather than resolving it. The second half is
   the confinement property; a `RootDirectory`-relative name that accepted a
   separator would let the destination escape the walked subtree. Covered by
   [FS-05 D9 spike 1](PRD-FS-05-delete-move.md), extended here with the
   separator case, and it must run before this PRD's `create` is written — FS-05's
   `NtSetInformationFile(FileRenameInformation)` fallback applies identically.
3. **No-replace.** `Flags = 0` is intended to fail when the leaf exists.
   _unverified — spike required:_ the exact status returned when the occupant is
   (a) a regular file, (b) a directory, (c) a junction or a file symlink. The
   symlink/junction case is the one that matters: if a reparse-point occupant is
   _followed_ instead of colliding, the final component gains a symlink-follow
   hazard that create does not otherwise have. Run it alongside SPIKE-W3; a
   "followed" result means the leaf needs the same explicit
   `FILE_ATTRIBUTE_REPARSE_POINT` refusal the walk already applies, before the
   rename. Test plan assertion 8 is this experiment's acceptance form.

Requirements this imposes: the staged handle must be opened with `DELETE`
access, and source and destination must be on the same volume (see D7).

Exact call sequence for one `CREATE` entry:

| step                               | Win32                                                                                                                                                                                                                                | macOS analogue                                                     |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------ |
| stage open (`create_stage`)        | `NtCreateFile` rel. staging-run handle, `FILE_CREATE`, `FILE_NON_DIRECTORY_FILE\|FILE_OPEN_REPARSE_POINT\|FILE_SYNCHRONOUS_IO_NONALERT`, access `FILE_GENERIC_READ\|FILE_GENERIC_WRITE\|DELETE\|WRITE_DAC\|SYNCHRONIZE`, share **0** | `openat(…, O_RDWR\|O_CREAT\|O_EXCL\|O_NOFOLLOW_ANY, 0600)` (l.708) |
| write (`command_write`)            | `WriteFile` loop                                                                                                                                                                                                                     | `write` loop (l.878)                                               |
| seal (`command_seal`)              | rewind, hash with BCrypt SHA-256, `GetFileInformationByHandleEx(FileStandardInfo)` for size, identity re-read, then `FlushFileBuffers`                                                                                               | `regular_digest_fd` + `fsync` (l.886)                              |
| re-attest (`sealed_stage_matches`) | `FlushFileBuffers`, re-hash, compare identity + size + digest                                                                                                                                                                        | l.741-750                                                          |
| effect (`commit_entry`)            | `SetFileInformationByHandle(FileRenameInfoEx, Flags=0, RootDirectory=parent, FileName=leaf)`                                                                                                                                         | `fclonefileat(…, CLONE_NOFOLLOW)` (l.759)                          |
| ACL repair                         | see D10                                                                                                                                                                                                                              | none needed                                                        |
| durable barrier                    | `FlushFileBuffers(renamedHandle)` — see D8                                                                                                                                                                                           | `fsync(parent_fd)` (l.761)                                         |

Do **not** set `FILE_ATTRIBUTE_TEMPORARY` on the staged file. It instructs the
cache manager to avoid writing to disk, which directly contradicts the seal's
durability claim.

### D3. `mkdir` is `NtCreateFile(FILE_CREATE | FILE_DIRECTORY_FILE)`, never `CreateDirectoryW`

The spine sketches `commit_mkdir` as `mkdirat | CreateDirectory`. That is the
wrong Win32 call: `CreateDirectoryW` takes a path string and has no
parent-handle-relative form, so using it would reintroduce a path-string write
path — a second write path, and a guardrail violation. The correct primitive is
the same `NtCreateFile` already used for the walk:

```
NtCreateFile(&h, FILE_LIST_DIRECTORY | SYNCHRONIZE, &oa /* RootDirectory = parent */,
             &iosb, NULL, FILE_ATTRIBUTE_NORMAL,
             FILE_SHARE_READ | FILE_SHARE_WRITE,
             FILE_CREATE,
             FILE_DIRECTORY_FILE | FILE_OPEN_REPARSE_POINT |
             FILE_SYNCHRONOUS_IO_NONALERT,
             NULL, 0)
```

`FILE_CREATE` is atomic no-replace; a collision returns
`STATUS_OBJECT_NAME_COLLISION`. macOS creates the directory `0700`
(`workspace_commit_helper.c:763`); Windows passes `SecurityDescriptor = NULL` so
the directory inherits its parent's ACL. That divergence is deliberate: a
0700-equivalent owner-only DACL inside a folder the user shares would be a
surprising, invisible permission change, and the macOS mode is an artefact of
POSIX `mkdirat` needing _some_ mode argument.

### D4. Confinement: per-component walk, reparse-refusing, exact-long-name

`open_parent` (`workspace_commit_helper.c:372-398`) walks one component at a
time with `openat(… O_DIRECTORY | O_NOFOLLOW_ANY)`, checks `S_ISDIR`, and
requires `statbuf.st_dev == root_dev`. Before each hop it calls
`directory_has_exact_entry` (l.338-346), which enumerates the parent and demands
the exact requested bytes — because APFS can resolve a differently-cased name.

Windows needs the same shape plus one more ambiguity class. NTFS resolves names
case-insensitively **and** maintains 8.3 short names as additional directory
entries. So `fs_open_dir_at` (FS-01 §2 — an earlier draft of this section called
it `fs_open_child_dir`) is:

1. `NtCreateFile` relative to the parent handle, `FILE_OPEN`,
   `FILE_DIRECTORY_FILE | FILE_OPEN_REPARSE_POINT`, share
   `FILE_SHARE_READ | FILE_SHARE_WRITE` — deliberately **not**
   `FILE_SHARE_DELETE`, so the directory cannot be deleted or renamed while the
   transaction holds it. (This is strictly stronger than the POSIX fd, which
   pins the inode but not the name. Cost: a user renaming that folder during an
   in-flight prepare gets "in use". Prepares are sub-second and user-initiated;
   accepted.)
2. Reject if `GetFileInformationByHandle(...).dwFileAttributes` has
   `FILE_ATTRIBUTE_REPARSE_POINT` — the precedent at `workspace_fs.c:263-269`.
3. Reject if the volume differs from the root's — `fs_stat_handle(h, &meta)` then
   `fs_identity_same_volume(&meta.id, &root_id)`, the `st_dev` check at
   `workspace_commit_helper.c:392`. (There is no `fs_identity_of`; FS-01 §8.)
   The comparison itself lives **above** the seam in portable `open_parent`; the
   provider only supplies the facts.
4. **Exact-name check.** Enumerate the parent with `NtQueryDirectoryFileEx`
   (fallback `NtQueryDirectoryFile`) using `FileBothDirectoryInformation` and
   the requested name as the filter, then require _both_:
   - `FileNameLength == wcslen(name) * 2` and `memcmp(FileName, name, …) == 0`
     (rejects every case-folded spelling), and
   - `ShortNameLength == 0 || memcmp(ShortName, name, …) != 0` unless the long
     name already matched (rejects resolution via an 8.3 alias such as
     `PROGRA~1`).

Only if all four hold is the handle retained. The final component of a `CREATE`
or `MKDIR` is never opened — it must not exist — so no final-component symlink
check is needed.

### D5. Windows name rules layered on the existing ASCII rule

`path_is_safe` (`workspace_commit_helper.c:313-332`) already restricts every
segment to `[A-Za-z0-9._-]` and rejects `\`, `/`-leading, `.`, `..`, and empty
segments. That charset incidentally excludes every Win32-invalid character
(`: * ? " < > |`), the ADS separator `:`, `$`, and `~` (so no
literal 8.3-shaped name can be requested). Three Windows-only rules must still be
added, in the shared `path_is_safe` behind a platform predicate so both
platforms stay on one rule set:

1. **Reserved device names**, case-insensitively, with or without an extension:
   `CON PRN AUX NUL COM0..COM9 LPT0..LPT9`. `CON.txt` is reserved too. These pass
   the ASCII charset and would otherwise resolve to a device.
2. **Trailing `.`** — permitted by the charset, stripped by Win32 path APIs but
   _not_ by NT-native names, which would create a file the user's own tools
   cannot open. (Trailing space is already excluded: `0x20 < 0x21`, l.327.)
3. **Segment length** ≤ 255 UTF-16 units, and total root-relative length such
   that root + relative stays under the volume's `lpMaximumComponentLength` /
   path budget. The helper always resolves parent-handle-relative, so `MAX_PATH`
   does not apply to the walk, but it does apply to what the user's other tools
   can reach; reject rather than create an unreachable file.

Applying these on both platforms costs a macOS user nothing real (nobody needs a
file called `NUL` or `foo.`) and keeps one rule set — the guardrail.

### D6. Identity is `FILE_ID_INFO`, not `BY_HANDLE_FILE_INFORMATION`

`GetFileInformationByHandleEx(h, FileIdInfo, …)` yields
`FILE_ID_INFO { ULONGLONG VolumeSerialNumber; FILE_ID_128 FileId; }`. The older
`BY_HANDLE_FILE_INFORMATION` carries a 32-bit volume serial and a 64-bit file
index, which is not unique on ReFS and is truncated on large NTFS volumes. The
`struct snapshot` fields `dev_t dev; ino_t ino;` (`workspace_commit_helper.c:81-82`)
become `struct fs_identity`, which FS-01 D3 already widened.

**The binding encoding is fixed-width, not length-delimited.** This PRD's first
draft had `binding_snapshot` hash `volume`, then `file_bytes`, then `file_bytes`
bytes of `file`. That would insert a length byte into the hashed stream on macOS
too, changing `compute_prepared_binding`'s output and turning every in-flight
claim written by the previous app version into a hard `CLAIM_BINDING_MISMATCH`
after upgrade — FS-01 F2, the exact failure the seam was drawn to avoid.
FS-01 D3 is normative: `fs_identity_binding` writes a per-platform compile-time
`FS_IDENTITY_BINDING_BYTES`, POSIX MUST be 16 and MUST emit
`be64(volume) || file[0..7]` byte-for-byte as before.

`FS_IDENTITY_BINDING_BYTES` on Win32 is `24` — `be64(volume) || file[0..15]` —
and that number is a placeholder until SPIKE-W7 confirms `FILE_ID_INFO.FileId`
is stable across a within-volume rename and a close/reopen on NTFS **and** ReFS.
Nothing in FS-01 changes if it is not, because the constant is per platform; the
Win32 encoding does.

### D7. Volume gate: NTFS, local, and the same volume as staging

`supported_root_fd` (`workspace_commit_helper.c:358-363`) fails closed on
anything that is not `apfs` or `hfs`, with the reason stated in the comment:
"Network/removable/unproven semantics fail closed."

Windows equivalent, all handle-based:

- `GetVolumeInformationByHandleW` → filesystem name must be exactly `NTFS`.
  ReFS is excluded until its `FileRenameInfoEx` no-replace semantics are proven.
- `GetFileInformationByHandleEx(h, FileRemoteProtocolInfo, …)` must **fail** —
  it is documented as valid only for handles on remote volumes. See SPIKE-W4;
  an SMB share can report `NTFS` as its filesystem name, so the fs-name check
  alone is not sufficient.

And the same-volume precondition. `command_prepare` at
`workspace_commit_helper.c:850` already refuses when
`stage.st_dev != root.st_dev`, because `fclonefileat` cannot cross an APFS
volume. Windows has the identical constraint for a different reason:
`FileRenameInfoEx` across volumes fails with `STATUS_NOT_SAME_DEVICE`, and the
only cross-volume move Win32 offers (`MoveFileExW` + `MOVEFILE_COPY_ALLOWED`) is
copy-then-delete — not atomic, and therefore not admissible.

**Consequence, stated plainly.** The staging directory is
`join(userDataDir, "capabilities", "workspace-v2", "staging")`
(`workspace-production-authority.ts:102-106`), which on Windows lives under
`%APPDATA%` on the system volume. So after FS-02, a grant root on `D:` reports
`workspace_write_unsupported` at prepare. This is the same behaviour macOS has
for an external volume today, it fails closed, and it is honest — but it will
bite more Windows users than macOS users, and it is not a bug to be fixed by
weakening the rule. The follow-up (a per-volume app-private staging directory
established at grant time, with its own consent step) changes where staged bytes
live, which is a stated invariant of the helper
(`workspace_commit_helper.c:19-20`) and deserves its own decision.

**Ownership, resolved: the grant-time half is [FS-09 D19](PRD-FS-09-enablement-consent.md).**
An earlier version of this paragraph routed the follow-up "to FS-04/FS-09" when
neither document mentioned cross-volume grants anywhere — the routing was to a
reader, not to a document, which is what
[00-consistency-report.md §4.4](00-consistency-report.md) recorded. That is now
answered, and the two halves landed in different places:

1. **The refusal is visible at grant time, not at prepare time — FS-09 D19.**
   The product call is _refuse before minting_, not warn-then-mint: a grant
   whose `mode` is not `read_only` and whose root volume differs from the
   staging volume is refused in `CapabilityService.requestFolderGrant` (so the
   refusal is a typed choice that can offer read-only) **and** enforced in
   `GrantStore.create` immediately after `assertGrantableRoot` (so a caller
   bypassing the native picker is blocked at the same choke point G2 already
   uses). Both sites run before any grant row exists, so a **newly minted**
   grant can never be an unusable one. Read-only grants on a second **supported**
   volume keep working and are still minted — reads never reach this helper.
   **Nothing in D7 changes:** the same-volume
   precondition at `workspace_commit_helper.c:850` stays exactly as specified
   and remains the enforcing check; FS-09 asks the same question earlier, not
   differently, and does not weaken this rule to make a `D:` workspace writable.

   Two doors the mint-time gate does not reach were found by a later
   adversarial pass and are closed in the same decision, so this note does not
   read as more finished than it is: a grant **already persisted** by a build
   that predates D19 is rehydrated, not re-derived, and is caught instead by
   FS-09 D19.8's term in the grant-usability predicate; and a root on a volume
   the helper refuses to open makes `rootIdentity` **throw** before any
   comparison, which FS-09 D19.9 turns into a third typed refusal.
   [00-consistency-report.md §11](00-consistency-report.md) records both. The
   Win32 half of the second one is D7's own gate — `GetVolumeInformationByHandleW`
   reporting exactly `NTFS`, and `FileRemoteProtocolInfo` failing — so a folder
   on a ReFS volume or an SMB share reaches the user as a refusal with a stated
   remedy rather than as `workspace_write_unsupported` at prepare.

2. **Per-volume staging is still its own slice — and now it has a home.** FS-09
   does not design it, reserves no names for it and does not gate on it; it is
   recorded in [FS-09's Out of scope](PRD-FS-09-enablement-consent.md) together
   with the helper invariant it would move (the "staged bytes live only beneath
   the inherited private staging descriptor" comment cited above). Nothing in
   FS-02 or FS-09 depends on that slice existing.

**What FS-02 still owns here.** FS-09 D19 compares two `volumeId` strings, and
on Win32 that string is **D6's** 16-hex `FILE_ID_INFO.VolumeSerialNumber`.
Whether serial equality is a sound same-volume test is `unverified` — it is
FS-09's **SPIKE-V1** (FS-09 open question 6), and a duplicate serial on a cloned
or imaged volume would fail in the dangerous direction, letting a cross-volume
grant through to die at prepare. If that spike forces the comparison onto
`GetFinalPathNameByHandleW(VOLUME_NAME_GUID)`, the encoding that changes is
**D6's, in this document**, because `volumeId` is persisted inside grants — not
FS-09's rendering of it. SPIKE-V1 shares a Windows host with SPIKE-W7; run them
together.

### D8. Durability: what Windows can prove, and what it cannot

This is the one place where Windows is materially weaker than macOS, and the
weakness must be reflected in what the system _claims_, not papered over.

| thing                        | macOS                       | Win32                                                  | proven? |
| ---------------------------- | --------------------------- | ------------------------------------------------------ | ------- |
| staged file bytes            | `fsync(stage_fd)` (l.886)   | `FlushFileBuffers(stagedHandle)`                       | yes     |
| journal record **contents**  | `fsync(fd)` (l.469)         | `FlushFileBuffers` after a fixed-size in-place rewrite | yes     |
| journal record **existence** | `fsync(journal_fd)` (l.470) | no documented directory-flush API                      | **no**  |
| committed directory entry    | `fsync(parent_fd)` (l.761)  | no documented directory-flush API                      | **no**  |

Windows has no `fsync(dirfd)` analogue. `FlushFileBuffers` on a directory handle
is undocumented — it requires `GENERIC_WRITE`, which a directory open does not
normally grant, and where it appears to succeed it is not specified to flush the
directory entry. The only documented volume-wide barrier is `FlushFileBuffers`
on a volume handle (`\\.\C:`), which requires administrator privileges and is
therefore unavailable to a per-user app.

What the helper does instead, and what each step buys:

1. Open staged files `FILE_WRITE_THROUGH` and `FlushFileBuffers` before seal —
   the sealed bytes are on stable storage.
2. After the rename, `FlushFileBuffers` on the (still-held) renamed handle.
   MSDN says this flushes the file's buffered data and metadata; it does **not**
   say the containing directory's entry is included. Do it; do not claim it.
3. Do not attempt a directory flush and do not report one.

**Why the gaps are safe, in the direction that matters.**

- _Lost journal record existence_ → after a crash the claim simply is not found.
  `command_commit` (l.902-908) and `command_reconcile_claim` (l.943-952) both
  return `INDETERMINATE` when no record exists. The failure direction is toward
  indeterminate, never toward a false `APPLIED` and never toward a replay.
- _Lost directory entry after `APPLIED`_ → the journal can outlive the effect.
  This means **on Windows, `applied` means observed-applied, not
  power-loss-durable.** The rename returned success and a subsequent open
  confirmed the object; a later power loss can still erase it. FS-07's
  reconciliation must therefore _re-observe the target_ on Windows rather than
  trust the journal's terminal state, and the commit receipt must be able to
  carry that distinction. FS-02 does not invent a receipt field for it; it
  records the requirement and asserts the property in tests.

SPIKE-W1 measures how often this actually bites; see below.

### D9. The journal on Windows: `fs_rename_replace` below the seam, `FILE_CREATE` for exclusion

`journal_store` (l.460-474) writes a temp file, fsyncs it, renames it over the
target, and fsyncs the directory.

**`journal_store` stays portable and keeps the rename.** An earlier draft of this
PRD replaced it on Windows with an in-place `SetFilePointerEx` + `WriteFile`
rewrite, arguing that `struct journal_record` is fixed size (l.123-135) so there
is no torn-write risk. That argument is sound about tearing and wrong about
layering: `journal_store` is the journal's write path, which FS-01 D1 and D5 put
**above** the seam and compile once for every platform. A per-platform journal
write path is protocol logic below the seam — the thing the whole seam exists to
prevent — and it would also fork the crash semantics FS-07 reasons over, since
"rename published the new record" and "the record was overwritten in place" have
different torn states after a power cut.

So Windows implements `fs_rename_replace` (FS-01 §2) and `journal_store` is
untouched. The Win32 body: open the temp record under the journal-directory
handle with `DELETE` access, then `SetFileInformationByHandle(FileRenameInfoEx)`
with `FILE_RENAME_REPLACE_IF_EXISTS | FILE_RENAME_POSIX_SEMANTICS` and
`RootDirectory` = the destination directory handle. This is
handle-relative, so `fs_rename_replace`'s contract — "within the app-private
journal directory only, never a workspace handle" — is preserved.

_unverified:_ that `FileRenameInfoEx` accepts `FILE_RENAME_REPLACE_IF_EXISTS`
together with `FILE_RENAME_POSIX_SEMANTICS` against a destination held open by no
one, and that `POSIX_SEMANTICS` is available on the project's minimum Windows
build. Same spike family as FS-06 D5's; run it here, since FS-02 needs the
primitive first. If `FileRenameInfoEx` is unavailable, the fallback is
`FileRenameInfo` with `ReplaceIfExists = TRUE` (no POSIX semantics, so a
destination held open by another process fails rather than being unlinked-on-last-close)
— acceptable inside an app-private directory nothing else opens, and it must be
recorded as the taken path rather than assumed.

The two durability steps that have no Windows analogue (D8) remain missing and
remain reported as missing; `fs_durable_barrier` on the journal **directory**
handle returns 0-meaning-no-error, never 0-meaning-durable, and
`FS_DIRECTORY_BARRIER_PROVEN` is 0.

`journal_store_no_replace` (l.482-497) — the exclusion primitive whose comment at
l.476-481 is explicit that `O_EXCL`, not rename, is what selects a single owner —
maps to `NtCreateFile` with `FILE_CREATE` disposition relative to the retained
journal-directory handle. `STATUS_OBJECT_NAME_COLLISION` is "already exists"
(return 0); any other failure is "durability/error" (return -1). The durability
compensation on the error path (`unlinkat` + `fsync`, l.494) becomes
`SetFileInformationByHandle(FileDispositionInfo, DeleteFile = TRUE)` on the
handle we just created — delete-by-handle, so it can only remove the exact object
we created, never a replacement by name. That preserves the identity discipline
of `cleanup_prepared_stages` (l.712-730) for free.

### D10. A same-volume rename carries the staging directory's ACL — repair it

This is a Windows-specific defect with no macOS analogue and it must be handled,
not discovered later.

_unverified — this whole subsection is a model of NTFS behaviour, not an
observation; SPIKE-W2 is what turns it into one._ On NTFS a security descriptor is
stored with the object, and inheritable ACEs from a parent are believed to be
materialised into the child's DACL at creation time, flagged `INHERITED_ACE`, with
a same-volume rename **not** re-evaluating inheritance. If that holds, a file
created inside the app-private staging directory and renamed into the user's
folder arrives carrying the **staging directory's** permissions — plausibly
owner-only — while a sibling the user created by hand carries the folder's
inherited ACL. Silent, invisible, and wrong.

SPIKE-W2 measures both halves: whether the divergence occurs at all, and whether
the repair below removes it. If the first half is false there is nothing to repair
and D10 becomes a no-op — record that outcome rather than shipping a repair for a
problem that does not exist.

The intended repair is to re-apply the DACL as _unprotected_ with the inherited
ACEs removed, which should make the system recompute inheritance from the new
parent:

```c
SetSecurityInfo(renamedHandle, SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION | UNPROTECTED_DACL_SECURITY_INFORMATION,
                NULL, NULL, /*pDacl=*/explicitAcesOnly /* often NULL/empty */, NULL);
```

This requires `WRITE_DAC` on the handle, so the staged handle's access mask
includes it. The exact shape is SPIKE-W2; the acceptance criterion is
mechanical: a file committed by the helper and a file created by
`New-Item` in the same folder must produce identical `Get-Acl` output.

If SPIKE-W2 shows the repair is not reliably expressible, the fallback is **not**
to stage inside the user's folder. It is to document the divergence and surface
it, because moving staged bytes into a user-visible directory changes the
helper's stated invariant and is a separate decision.

### D11. Delivering the five private capabilities to the helper on Windows

The macOS launch contract (`workspace_commit_helper.c:5-13`,
`native-workspace-commit-helper.ts:195-216`) hands the child five things beyond
stdio: fd 3 = one-shot channel key, fd 4 = staging dir capability, fd 5 =
journal dir capability, fd 6 = journal HMAC key, fd 7 = test fault byte — the
five `fs_bootstrap_acquire` returns (FS-01 §2). **This is the single largest
unverified assumption in FS-02** and it gates everything else, so it is SPIKE-W3
and it runs first.

- **Path A (preferred, zero protocol change).** libuv passes extra `stdio`
  entries to a Windows child through the CRT's `lpReserved2` inherited-handle
  block; an MSVC-CRT child recovers them as CRT fds 3..N via
  `_get_osfhandle(n)`. If this holds, the Windows helper differs from macOS only
  in using `_get_osfhandle` instead of raw fd numbers.
- **Path B (fallback, if extra fds do not arrive).**
  - Keys (fd 3, fd 6) move to a **stdin prologue**: the client writes exactly 64
    bytes before the first frame; the helper reads exactly 64 bytes in `main`
    and never returns to that mode. This is as safe as a separate fd — the key
    can never be replayed into the framed protocol because the reader never goes
    back — and needs no extra handle.
  - Directory capabilities (fd 4, fd 5) become **path + expected identity** on
    the command line: main creates the directory, opens it, reads its
    `FILE_ID_INFO`, and passes `--staging <path> --staging-id <hex>` (same for
    the journal). The helper opens the path with `FILE_OPEN_REPARSE_POINT`,
    requires `fs_identity_equal` against the passed identity, and requires
    `fs_dir_is_app_private` (FS-01 §2; an earlier draft of this PRD called it
    `fs_private_dir`). A directory swapped between main's open and the helper's
    open is _detected_ and fails closed. This is weaker than an inherited handle
    (detection instead of impossibility) and the PRD says so; it is not a second
    write path, and it does not widen what the helper may touch.
  - The fault byte (fd 7) becomes a command-line flag accepted **only** when the
    binary is built with `-DWORKSPACE_HELPER_TEST_FAULTS`, which the packaging
    build never defines.

Two further Windows launch details:

- **`cwd: "/"`** (`native-workspace-commit-helper.ts:211`) resolves to the root
  of the current drive on Windows. Set it to an explicit main-owned directory.
- **`env: {}`** (l.212) is riskier on Windows than on POSIX: system library
  initialisation (notably CNG/RPC) reads `SystemRoot`. Pass exactly
  `{ SystemRoot, windir }` and nothing else, and confirm in SPIKE-W3 that a
  fully empty environment is in fact the thing that breaks, rather than
  cargo-culting the workaround.

### D12. Crypto: BCrypt replaces CommonCrypto

`CC_SHA256_*` and `CCHmac` (`workspace_commit_helper.c:25-26`) map to:

| use                                    | macOS                         | Win32                                                                                                 |
| -------------------------------------- | ----------------------------- | ----------------------------------------------------------------------------------------------------- |
| SHA-256 (digests, binding)             | `CC_SHA256_Init/Update/Final` | `BCryptOpenAlgorithmProvider(BCRYPT_SHA256_ALGORITHM, …, 0)` + `BCryptCreateHash/HashData/FinishHash` |
| HMAC-SHA256 (channel MAC, journal MAC) | `CCHmac`                      | same, opened with `BCRYPT_ALG_HANDLE_HMAC_FLAG` and a key                                             |
| CSPRNG                                 | `arc4random_buf` (l.437)      | `BCryptGenRandom(NULL, …, BCRYPT_USE_SYSTEM_PREFERRED_RNG)`                                           |

Algorithm providers are opened once at startup and reused; a failure to open one
aborts before any capability is accepted. The constant-time comparison loops
(`verify_frame`, l.962-969; `journal_load`, l.505-506) are already portable C and
do not change.

`bcrypt.dll` is not a KnownDLL, which matters for D14's DLL-hijack hardening.

### D13. Build: `cl.exe` located deterministically, no PATH, no new CI action

`build.mjs:36` runs `cc` with POSIX-only flags. Add a `win32` branch that keeps
the same shape (temp output → rename) and never resolves a compiler through
`PATH`:

1. `vswhere.exe` at
   `%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe` (a fixed,
   documented location) with
   `-latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`.
2. Run the compile through
   `cmd /c ""<installationPath>\Common7\Tools\VsDevCmd.bat" -arch=x64 -host_arch=x64 && cl …"`.
3. Fail with a precise, actionable message if either step fails. Never silently
   emit the sentinel on Windows — a missing toolchain on a Windows build is a
   build failure, exactly as a missing `cc` is on macOS.

This mirrors the "absolute, main-owned launcher; never resolve this through
PATH" discipline already applied to `sandbox-exec`
(`macos-workspace-confinement.ts:9-10`), and avoids adding a third-party CI
action to the pinned-SHA workflow set.

Compiler flags (the Windows counterpart of `build.mjs:22-34`):

```
/std:c11 /W4 /WX /O2 /GS /guard:cf /sdl /utf-8 /DUNICODE /D_UNICODE
/DWIN32_LEAN_AND_MEAN /Zi
link: /DYNAMICBASE /NXCOMPAT /HIGHENTROPYVA /CETCOMPAT /DEPENDENTLOADFLAG:0x800
libs: ntdll.lib advapi32.lib bcrypt.lib
```

`/DEPENDENTLOADFLAG:0x800` (`LOAD_LIBRARY_SEARCH_SYSTEM32`) constrains the
loader for statically-imported DLLs; see D14.

### D14. Packaging, DLL hardening, and signature verification before spawn

**Packaging.** `electron-builder.yml:44-47`'s filter is the literal string
`workspace-commit-helper`, which will not match `workspace-commit-helper.exe`.
Change it to `workspace-commit-helper*`. `resolveNativeWorkspaceCommitHelperPath`
appends `.exe` on win32 for both the packaged and development branches.

**Signing.** `build/sign-nested.js` (wired as `afterPack`,
`electron-builder.yml:86`) exists precisely because nested binaries under
`extraResources` are not covered by electron-builder's own signing on macOS. The
Windows situation is the mirror image and is **unverified**: confirm whether
electron-builder signs files under `extraResources` on Windows. If it does not —
the likely case — add `build/sign-nested-win.js`, invoked from the same
`afterPack` hook, that runs `signtool sign /fd SHA256 /tr <timestamp-url> /td
SHA256` over the staged helper using the same `CSC_LINK`/`CSC_KEY_PASSWORD`
material `release-desktop.yml:140-145` already exports, and no-ops with a
warning when no certificate is present (mirroring `sign-nested.js`'s
documented no-op behaviour).

**Verification before spawn.** The macOS check
(`native-workspace-commit-helper.ts:507-515`) shells to `/usr/bin/codesign` with
a designated requirement. Windows has no equivalent CLI that is guaranteed
present and safe to shell to (`Get-AuthenticodeSignature` drags in PowerShell,
`PATH`, and execution policy). Use `WinVerifyTrust` from a small N-API addon:

```
WINTRUST_FILE_INFO { cbStruct, pcwszFilePath = <helper>, hFile = NULL, pgKnownSubject = NULL }
WINTRUST_DATA {
  dwUIChoice          = WTD_UI_NONE
  fdwRevocationChecks = WTD_REVOKE_NONE        /* offline-first; see below */
  dwUnionChoice       = WTD_CHOICE_FILE
  dwStateAction       = WTD_STATEACTION_VERIFY (then _CLOSE)
  dwProvFlags         = WTD_SAFER_FLAG | WTD_CACHE_ONLY_URL_RETRIEVAL
}
WinVerifyTrust(NULL, &WINTRUST_ACTION_GENERIC_VERIFY_V2, &data) == ERROR_SUCCESS
```

then `CryptQueryObject` + `CertGetNameStringW(CERT_NAME_SIMPLE_DISPLAY_TYPE)` on
the signer certificate, requiring an exact match against the pinned subject CN.
Revocation checking is disabled deliberately: an offline-first desktop app must
not make launching a local helper depend on reaching a CRL/OCSP endpoint. The
pinned-subject check, not revocation, is what makes this the analogue of macOS's
designated requirement.

**DLL hijacking.** A Windows process searches its own directory for DLLs before
System32, and the helper lives in a per-user-writable install
(`electron-builder.yml:79-82`). `bcrypt.dll` is not a KnownDLL. So the helper
calls, as the first statements of `main()`, before touching any capability:

```c
SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32);
SetDllDirectoryW(L"");
```

together with `/DEPENDENTLOADFLAG:0x800` for statically-imported DLLs. macOS gets
the equivalent for free from the hardened runtime plus library validation
(`electron-builder.yml:62-65`); Windows does not, and must ask for it.

### D15. Fail closed on an unsigned packaged build — and say what that costs

`NativeWorkspaceCommitHelper.launch` verifies the executable only when
`config.packaged === true` (l.182-189). Windows keeps that shape exactly: a
packaged build whose helper fails `WinVerifyTrust` or whose signer CN does not
match throws `workspace_write_unsupported`, and the app remains read-only. No
new escape hatch is introduced — in particular, FS-02 does **not** widen
`unsafeDevWorkspaceTcb` (`workspace-authority.ts:43-48`) to cover an unsigned
production helper.

The cost is concrete: `release-desktop.yml:147-149` shows Windows builds are
currently produced **unsigned** when `WIN_CSC_LINK` is absent, so on the current
release configuration Windows writes will be unavailable to every user even
after FS-02 lands. Obtaining an Authenticode certificate (or configuring Azure
Trusted Signing, already noted at `release-desktop.yml:149` and
`docs/deployment/desktop-release.md:65-73`) is a hard prerequisite for FS-09's
enablement story, not a nice-to-have. It is called out as an open question rather
than resolved here.

## Implementation plan

**Step 0 — run the four spikes before writing production code.** SPIKE-W3
(capability delivery) determines the launch contract and therefore the shape of
almost everything else; run it first. All four are listed under "Open questions
and spikes".

1. **`apps/desktop/native/workspace-commit-helper/src/fs_platform_win32.c` (new).**
   Implements **every** declaration in FS-01's `fs_platform.h` — the full list is
   in Interfaces consumed, not only the dozen with interesting semantics. FS-01
   rule 1 makes a partial provider a link error, which is the check. Resolve
   `NtCreateFile` and `NtQueryDirectoryFileEx` via `GetProcAddress` on the
   already-loaded `ntdll.dll`, exactly as `workspace_fs.c:276-282` does.
   `fs_chan_read_exact` / `fs_chan_write_all` live **here**, not in a separate
   channel translation unit: they are members of the platform seam (FS-01 §8).
   They use `GetStdHandle(STD_INPUT_HANDLE)` / `STD_OUTPUT_HANDLE` with
   `ReadFile`/`WriteFile` — never CRT `_read`/`_write`, which would risk
   text-mode translation of binary frames — and treat `ERROR_BROKEN_PIPE` as EOF.
   `fs_abort_immediate` is `TerminateProcess(GetCurrentProcess(), code)`.
   `fs_dir_is_app_private` is the owner-SID + DACL check.

2. **`.../src/fs_crypto_bcrypt.c` (new).** BCrypt SHA-256, HMAC-SHA256, and CSPRNG
   behind FS-01 `fs_crypto.h` (D12). Providers opened once at startup; failure
   aborts before any capability is accepted. `fs_random_bytes` cannot return a
   failure — a `BCryptGenRandom` error calls
   `fs_abort_immediate(FS_ABORT_ENTROPY)` (FS-01 D6). Add the
   `_Static_assert(<BCrypt hash object size> <= FS_SHA256_CTX_BYTES)` FS-01
   requires, and the **KAT** FS-01 D6 obliges FS-02 to write, because Windows has
   no golden transcript to inherit: NIST SHA-256 for `""` and `"abc"`, RFC 4231
   HMAC cases 1 and 2, and a chunked-vs-one-shot equivalence over 1 MiB.

3. **_(folded into step 1)_** — there is no `win32_channel.c`. FS-01 §8: the
   channel is a seam member and lives in the platform `.c`.

4. **`.../src/workspace_commit_helper.c` (edit) — one change only: the D5 name
   rules in `path_is_safe`.** An earlier draft of this step also removed
   `arc4random_buf`, `_exit(86)`, `geteuid`, the raw `read`/`write` calls and
   `journal_store`'s rename. All of those are **already** behind the seam after
   FS-01 (see FS-01 §4's mapping table and its `check-seam.mjs` audit, which
   fails the build if any of them reappears in the portable object). Re-doing
   them here would either be a no-op or a regression. `commit_entry` is already
   two seam calls after FS-01. **`parse_entry`'s refusal of replace/delete/move
   (l.801) does not change.**

   Note the ownership overlap: FS-03 also edits `path_is_safe`, and its D4/D5
   supersede this PRD's D5 with the same intent and no relaxation. Whichever
   lands first writes the function; the second adopts it.

5. **`.../build.mjs` (edit).** Add the win32 branch of D13. Keep the sentinel for
   every other platform (l.9-17) verbatim, including its comment.

6. **`apps/desktop/native/win-authenticode/` (new).** `binding.gyp` (win-only
   target linking `wintrust.lib crypt32.lib`), `src/win_authenticode.c`,
   `index.cjs` (returns `undefined` when the addon is absent, mirroring
   `workspace-fs/index.cjs`), `index.d.ts`.

7. **`apps/desktop/main/capabilities/native-workspace-commit-helper.ts` (edit).**
   - Register the `win32` `HelperPlatformProfile` (§3) and update FS-01's
     tripwire test to `["darwin", "win32"]`. The `process.platform !== "darwin"`
     literal is already gone — FS-01 commit 3 replaced it with
     `helperPlatformProfile(config.platform) === undefined`, and both
     `launch` and `workspace-production-authority.ts:85` already read the
     registry. FS-02 adds an entry; it does not touch the gate.
   - Platform-conditional `stdio` (D11), minimal `env`, explicit `cwd`.
   - `verifyPackagedWorkspaceCommitHelperWin32` is the profile's
     `verifyPackagedExecutable`; it returns `false` when the addon is
     unavailable. No exported `verifyPackagedWorkspaceCommitHelper(path,
platform)` dispatcher is added — that would be a second place a platform can
     be admitted (FS-01 D10).

8. **`apps/desktop/electron-builder.yml` (edit).** Filter →
   `workspace-commit-helper*` (l.47). Add `build/sign-nested-win.js` to the
   `afterPack` chain (l.86) if step 9 is needed.

9. **`apps/desktop/build/sign-nested-win.js` (new, conditional on the
   electron-builder finding in D14).** `signtool` over the staged helper; no-op
   with a warning when unsigned, mirroring `sign-nested.js`.

10. **`.github/workflows/ci-desktop.yml` (edit).** Add a `windows-latest` matrix
    leg that runs `npm run build:workspace-commit-helper` and
    `vitest run main/capabilities/native-workspace-commit-helper.test.ts`. The
    existing ubuntu leg keeps doing typecheck + the full suite.

11. **`apps/desktop/main/capabilities/native-workspace-commit-helper.test.ts`
    (edit).** See test plan.

12. **`docs/plan/filesystem-capability/README.md` (edit).** Flip the
    "Create file / mkdir" Windows cell from ❌ to ✅ once the DoD is met. The
    seam-sketch correction (`CreateDirectory` → `NtCreateFile(FILE_CREATE |
FILE_DIRECTORY_FILE)`) is **already folded into the spine**; do not re-apply
    it.

## Test plan

**Enable the existing suite on Windows.** `native-workspace-commit-helper.test.ts:32`
becomes a two-platform predicate. All fifteen existing tests must pass unmodified
on `win32` except where noted:

- `"uses retained parent handles for a digest-sealed atomic create"` (l.107) —
  must pass as-is; this is the FS-02 keystone.
- `"rejects symlink traversal, parent replacement, and external create races"`
  (l.242) — `symlinkSync` (l.246) needs Developer Mode or admin on Windows. Split
  into a POSIX case and a Windows case that uses
  `symlinkSync(target, path, "junction")` (no privilege required) and asserts
  the walk refuses it via the `FILE_ATTRIBUTE_REPARSE_POINT` check.
- `"fails closed when packaged helper signature verification is not accepted"`
  (l.183) — passes through the injected `verifyPackagedExecutable` seam, so it is
  platform-neutral; add a win32 case asserting the real dispatch selects the
  Authenticode path.
- `"fails closed for non-CAS replace/delete/move rather than using an advisory
lock"` (l.360) — must pass on Windows too. Verb parity is a guardrail.
- The four crash-boundary/reconciliation tests (l.406, l.428, l.488, l.533,
  l.559, l.586) exercise `test_crash_boundary`, so they depend on D11's fault
  channel landing on Windows.
- `privateStore()` (l.57-70) passes `mode: 0o700`, which Node ignores on Windows.
  Assert instead that a directory under `%TEMP%` satisfies `fs_dir_is_app_private`, and
  add a negative test that a directory granted `Everyone:(R)` is rejected.

**New Windows-specific assertions.**

1. _Case-folded name is refused._ Prepare `create` at `notes/plan.md` in a
   workspace whose real directory is `Notes`; assert `workspace_conflict`, not a
   file created under `Notes`.
2. _8.3 alias is refused._ Create `Program Folder\` (which gets a short name),
   then request a create whose parent segment is the 8.3 alias; assert refusal.
   Skip with an explicit message if 8.3 generation is disabled on the CI volume
   (`fsutil 8dot3name query`) — a skip that says why, not a silent pass.
3. _Reserved device names._ `create` at `NUL`, `CON.txt`, `com1`, and `foo.`
   each fail at prepare with `workspace_conflict`; nothing is created (D5).
4. _No-replace holds for every occupant kind._ With the leaf already present as
   (a) a file, (b) a directory, (c) a junction, `commit` returns
   `precondition_drift` and the existing object is byte-identical afterwards.
5. _Cross-volume prepare fails closed._ Given a staging directory and a root on
   different volumes, `prepare` fails with `workspace_write_unsupported` and no
   staged file is created (D7). Skip with a stated reason if the runner has one
   volume.
6. _Non-NTFS root fails closed._ Format/mount a small FAT32 VHD, grant it, assert
   `workspace_write_unsupported`. Skip with a stated reason if VHD attach is
   unavailable on the runner.
7. _Committed ACL matches a natively created sibling._ Commit `a.txt` via the
   helper into a folder with distinctive inheritable ACEs; create `b.txt` there
   with `New-Item`; assert the two DACLs are equal (D10). This is the acceptance
   test for SPIKE-W2 and it must be a hard assertion, not a warning.
8. _Rename does not follow a reparse point at the leaf._ Place a junction at the
   target leaf pointing outside the root; assert `precondition_drift` and that
   nothing was written at the junction's target.
9. _Directory handle blocks deletion during a transaction._ While a prepare is
   outstanding, assert an external `rmdir` of a walked parent fails, and that
   `abort` releases it (D4's share-mode consequence — assert the documented
   behaviour so a future change to share flags is caught).
10. _Lost journal record degrades to indeterminate, never applied._ Delete the
    claim record file between commit and reconcile; assert `reconcileClaim`
    returns `indeterminate` (D8's safety argument, asserted rather than
    asserted-in-prose).
11. _Frame binary fidelity._ Send a payload containing `0x0A`, `0x0D`, and
    `0x1A` bytes; assert byte-exact round-trip (guards against CRT text-mode
    translation, step 3).

**Build/packaging assertions.**

12. `build.mjs` on win32 emits an executable PE at
    `bin/workspace-commit-helper.exe` and exits non-zero with a specific message
    when `vswhere` finds no VC tools.
13. A packaging test asserts the electron-builder filter matches the `.exe`
    (a unit test over the config, not a full `electron-builder` run).
14. `verifyPackagedWorkspaceCommitHelper` returns `false` for: an unsigned PE, a
    PE signed by a different subject, and a nonexistent path.

## Open questions and spikes

Each spike names the API, the exact experiment, and the outcome that would change
the design. None of these can be settled from a macOS host, and none of them
should be assumed.

**SPIKE-W3 — extra-fd delivery (run first; gates D11).**
_API:_ Node `child_process.spawn` `stdio` entries beyond index 2 on Windows;
libuv's `lpReserved2` CRT handle block; `_get_osfhandle`.
_Experiment:_ minimal Node parent + MSVC C child. Parent spawns with
`stdio: ["pipe","pipe","ignore","pipe", dirFd, dirFd2, "pipe"]`; child reports
`_get_osfhandle(3..6)` validity and calls `GetFileInformationByHandleEx` on each.
Also spawn once with `env: {}` and once with `{ SystemRoot, windir }` and record
which BCrypt calls fail.
_Changes the design if:_ extra fds do not arrive → Path B (stdin key prologue +
path-plus-expected-identity for the directories). If libuv restricts inheritance
via `PROC_THREAD_ATTRIBUTE_HANDLE_LIST`, Path B is mandatory.

**SPIKE-W2 — inherited ACL after a same-volume rename (gates D10).**
_API:_ `SetSecurityInfo(SE_FILE_OBJECT, DACL_SECURITY_INFORMATION |
UNPROTECTED_DACL_SECURITY_INFORMATION)`.
_Experiment:_ create a file in a private staging dir; rename it by handle into a
folder carrying a distinctive inheritable ACE; compare `Get-Acl` before and after
the repair against a `New-Item` sibling.
_Changes the design if:_ the repair does not produce an identical DACL → document
the divergence and surface it in the commit receipt; do **not** move staging into
the user's folder without a separate decision.

**SPIKE-W1 — directory-entry durability (gates the claim in D8, not the code).**
_API:_ `FlushFileBuffers` on a file handle after `FileRenameInfoEx`.
_Experiment:_ on an NTFS VM, run ≥200 create-commits, hard-power-cut the VM
(`Stop-VM -TurnOff` / QEMU `SIGKILL`) at a fixed delay after the rename returns
and `FlushFileBuffers` completes; on reboot, count names the journal calls
`APPLIED` that are absent on disk.
_Changes the design if:_ loss is observed → FS-07's reconciliation must
re-observe the target on Windows and the receipt must carry
`durability: observed_not_proven`. If loss is zero across the run, document
"observed durable in practice, not contractually guaranteed" — never upgrade it
to a guarantee.

**SPIKE-W4 — remote-volume detection (gates D7).**
_API:_ `GetFileInformationByHandleEx(FileRemoteProtocolInfo)`;
`GetVolumeInformationByHandleW`.
_Experiment:_ open a directory handle on (a) a local NTFS volume, (b) an SMB
share of an NTFS volume, (c) a mapped network drive; record the return value and
`GetLastError` of the `FileRemoteProtocolInfo` query and the reported filesystem
name for each.
_Changes the design if:_ the query succeeds on local volumes or fails on remote
ones → fall back to `GetDriveTypeW` on the volume path plus a `\\`-prefix check,
and state plainly that the remote test is then advisory rather than authoritative.

**SPIKE-W5 — free space by handle (gates FS-04 D4's Win32 body).**
_API:_ `NtQueryVolumeInformationFile(FileFsFullSizeInformation)` on a directory
handle, vs `GetDiskFreeSpaceExW`.
_Experiment:_ read available bytes for the grant root through a retained
directory handle and compare against `GetDiskFreeSpaceExW` on the same volume,
with and without a per-user quota in force.
_Changes the design if:_ the handle-based form is unavailable → `fs_volume_free_bytes`
cannot be implemented without a path, and FS-04 D4's `MIN(main's hint, the
helper's own reading)` collapses to main's hint alone on Windows. That is a real
weakening (main becomes the only source of a number the helper enforces
arithmetic on) and must be stated in FS-04, not absorbed.

**SPIKE-W6 — `NtQueryDirectoryFileEx` under concurrent renames (shared with FS-03).**
_API:_ `NtQueryDirectoryFileEx` / `NtQueryDirectoryFile`.
_Experiment:_ FS-01 D8 requires at-least-once delivery of every pre-existing name
while the caller renames into the same directory (`journal_reconcile_startup`
does exactly that). Enumerate 500 entries while a second thread renames temp
files over 100 of them; assert every pre-existing name is delivered at least once.
_Changes the design if:_ Windows cannot guarantee it → `journal_reconcile_startup`
must snapshot names before mutating. That is a **portable** change and must be
known before the Win32 iterator is written.

**SPIKE-W7 — `FILE_ID_INFO.FileId` stability (gates D6 and `FS_IDENTITY_BINDING_BYTES`).**
_API:_ `GetFileInformationByHandleEx(FileIdInfo)`.
_Experiment:_ read `FileId` before and after a within-volume rename, and across a
close/reopen, on NTFS and on ReFS.
_Changes the design if:_ unstable → the Win32 binding encoding changes and the
identity check in every verb loses its anchor. Nothing in FS-01 changes, because
the constant is per platform.

**Non-spike open questions.**

- **Windows code-signing certificate.** Without one, D15 makes FS-02 ship a
  capability nobody can turn on. Procure Authenticode or configure Azure Trusted
  Signing (`docs/deployment/desktop-release.md:65-73`). Product decision, not an
  engineering one.
- **Does electron-builder sign `extraResources` on Windows?** Determines whether
  step 9 is needed. One packaging experiment answers it.
- ~~**Cross-volume workspaces — currently unowned.**~~ **Closed — owned.** The
  same-volume rule (D7) still makes a `D:` workspace read-only, but the
  grant-time half is now [FS-09 D19](PRD-FS-09-enablement-consent.md): the grant
  is **refused before it is minted**, naming the volume, offering read-only
  rather than imposing it. Per-volume staging is a separate future slice and is
  recorded in FS-09's Out of scope. See D7's ownership note. What is still open
  from this item is only **SPIKE-V1** (FS-09 open question 6) — whether Win32
  `volumeId` serial equality is a sound same-volume test — which decides how D6
  spells `volumeId`, not whether the grant-time check happens.
- **Minimum Windows version.** `FileRenameInfoEx` and `NtQueryDirectoryFileEx`
  have Windows 10 1709/1703 floors. The app's real floor is whatever Electron 43
  (`apps/desktop/package.json:48`) supports; confirm and pin it, and specify the
  `FileRenameInfo` fallback only if the floor is genuinely below 1709.

## Definition of done

- [ ] SPIKE-W3 has run on a real Windows host and D11 names one chosen path with
      its evidence recorded in this file.
- [ ] SPIKE-W2 has run and D10's repair is either implemented or the divergence
      is documented here.
- [ ] SPIKE-W4 has run and D7's remote-volume test is either confirmed or
      replaced with a stated-advisory fallback.
- [ ] SPIKE-W1 has run and D8's durability wording matches the measurement.
- [ ] SPIKE-W5, W6 and W7 have run; D6's `FS_IDENTITY_BINDING_BYTES = 24`, the
      `fs_dir_for_each` iterator and `fs_volume_free_bytes` each match what was
      measured, or the divergence is recorded here.
- [ ] **D2 property 3's occupant experiment has run** — the exact status a
      `Flags = 0` rename returns when the leaf is occupied by a file, a
      directory, and **a junction or a file symlink**. If a reparse-point
      occupant is _followed_ rather than colliding, the leaf carries an explicit
      `FILE_ATTRIBUTE_REPARSE_POINT` refusal **before** the rename, and test-plan
      assertion 8 pins it. This is a confinement property, not a wording one, and
      it had no id and no DoD line until the adversarial pass added them
      ([00-consistency-report.md §11](00-consistency-report.md)); it is in the
      README's register under "PRD-local spikes with no program id". Run it on
      SPIKE-W3's host.
- [ ] `fs_platform_win32.c` defines **every** declaration in `fs_platform.h` and
      `fs_crypto_bcrypt.c` every declaration in `fs_crypto.h`;
      `tools/check-seam.mjs` passes for the win32 objects.
- [ ] The BCrypt KAT (NIST SHA-256, RFC 4231 HMAC, chunked-vs-one-shot over
      1 MiB) passes — Windows does not inherit macOS's golden-transcript evidence.
- [ ] `HELPER_PLATFORM_PROFILES` has exactly two entries and FS-01's tripwire
      test was updated deliberately; `grep -n 'SUPPORTED_HELPER_PLATFORMS'` over
      `apps/desktop` returns nothing.
- [ ] `journal_store` is byte-identical to FS-01's portable version;
      `grep -c 'SetFilePointerEx' src/workspace_commit_helper.c` is 0.
- [ ] `node build.mjs` on `windows-latest` produces an executable PE at
      `bin/workspace-commit-helper.exe`; on a Windows host without VC tools it
      exits non-zero with an actionable message and never emits the sentinel.
- [ ] `ci-desktop.yml` has a `windows-latest` leg that compiles the helper and
      runs the helper suite; it is required for merge.
- [ ] All fifteen pre-existing tests in
      `native-workspace-commit-helper.test.ts` pass on `win32` (with the three
      documented platform splits), and all fourteen new assertions in the test
      plan pass.
- [ ] A `create` committed by the Windows helper produces a file whose bytes
      equal the sealed digest and whose DACL equals a natively created sibling's.
- [ ] A `mkdir` committed by the Windows helper produces a directory whose DACL
      is inherited from its parent, created via
      `NtCreateFile(FILE_CREATE | FILE_DIRECTORY_FILE)`;
      `grep -c CreateDirectoryW src/` is 0.
- [ ] `replace`, `delete`, and `move` are refused on Windows with the same
      failure code as on macOS.
- [ ] `grep -nE '\b(CreateFileW|MoveFileExW|DeleteFileW|CreateDirectoryW|SHFileOperation)\b'`
      over the helper sources returns nothing outside the root open.
- [ ] A packaged, signed Windows build launches the helper; a packaged, unsigned
      Windows build fails closed with `workspace_write_unsupported` and the app
      stays read-only.
- [ ] The helper calls `SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32)`
      before touching any capability, and the binary links with
      `/DEPENDENTLOADFLAG:0x800`.
- [ ] macOS behaviour is byte-for-byte unchanged: the darwin suite passes and
      `command_root_identity` still emits decimal `st_dev`/`st_ino`.
- [ ] `README.md`'s capability table and seam sketch are corrected per step 12.

## Out of scope

- `replace`, `delete`, `move` on either platform (FS-04, FS-05, FS-06).
- Preimage capture and trash (FS-04). FS-02's verbs displace nothing, so neither
  is required.
- Windows process confinement for the supervised Python children — the Seatbelt
  analogue (`macos-workspace-confinement.ts`) — and lifting the
  `platform !== "darwin"` gate in `workspace-production-authority.ts:85`. Both
  are FS-03. Until FS-03 lands, the Windows helper is reachable from tests and
  from an explicit main-owned composition, **not** from the production authority.
- Post-crash reconciliation changes implied by D8 (FS-07).
- User-facing enablement, consent, and capability-honest reporting (FS-09),
  including telling a Windows user _why_ writes are unavailable — and including
  the grant-time refusal of a cross-volume write grant (FS-09 D19), which is
  FS-09's, not FS-02's, even though D7 is the rule it enforces.
- Cross-volume staging — a per-volume app-private staging directory. A separate
  future slice; recorded in FS-09's Out of scope, designed by neither PRD.
- ARM64 Windows. `release-desktop.yml:54-57` builds x64 only.
- Any change to the wire protocol version, the request/operation/outcome enums,
  or the macOS root-identity string format.

## Guardrails

- Do **not** add a second write path. Every Windows mutation goes through the
  same prepare → write → seal → claim → commit protocol; no `node:fs` write, no
  path-string Win32 mutation API, no `CreateDirectoryW`.
- Do **not** weaken confinement to make a verb work. Every open is relative to a
  retained handle, refuses reparse points, and requires an exact long-name match.
- Do **not** report an outcome that was not observed. On Windows, `applied` means
  observed-applied; say so, and never let a lost journal record become `applied`.
- Do **not** implement a verb on one platform only. After FS-02 both platforms
  support exactly `{create, mkdir}`.
- Do **not** let the model choose a path. Nothing in FS-02 introduces a new
  source of paths; grants remain user-issued.
- Do **not** ship a new escape hatch for unsigned Windows helpers. Fail closed and
  escalate the certificate as a prerequisite.
- Do **not** assert Win32 semantics that a spike has not confirmed. Every claim
  in this PRD marked unverified stays marked until a Windows host says otherwise.
- Do **not** change `command_root_identity`'s macOS output format — it would
  invalidate every stored grant.
- Do **not** relax the same-volume precondition to make `D:` workspaces work.
  That is a design change with its own decision, not a bug fix.
