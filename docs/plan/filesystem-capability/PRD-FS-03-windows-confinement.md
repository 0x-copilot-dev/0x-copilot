# PRD-FS-03 — Windows confinement and attestation parity

**Status:** specified
**Depends on:** FS-01 (platform seam). Parallel to FS-02 (Windows commit helper) — neither blocks the other, but a Windows write is unreachable from the production authority until **both** have landed.

## Implementer brief

FS-02 gives Windows the ability to _make_ a change. FS-03 gives Windows the ability to _prove the boundary around it_: the handle-relative confined-open primitive on Win32 and its complete refusal set; the Windows path grammar, which is strictly larger than POSIX's and is currently enforced in TypeScript but not in the C TCB; the process-confinement probe that gates `createProductionWorkspaceAuthority` — today a hard `platform !== "darwin"`; and a written contract for what `workspaceWriteIsolation: "enforced"` and `nativeWorkspacePrimitives: "available"` actually assert, made identical on both platforms by **observation** rather than by API availability. No verb is added, no wire format changes. Read [README.md](README.md) first: D1/D2/D3 are locked and are not re-litigated here.

## Context

Everything below was verified against the code at `main@b349aca2`.

### C1 — What "confinement" is today, exactly

`MacosWorkspaceConfinement` ([`apps/desktop/main/services/macos-workspace-confinement.ts:43`](../../../apps/desktop/main/services/macos-workspace-confinement.ts)) is the only implementation of `WorkspaceConfinementProbe` ([`workspace-production-authority.ts:33-35`](../../../apps/desktop/main/capabilities/workspace-production-authority.ts)). It does four separable things:

| #   | member                                         | lines      | what it does                                                                                                        |
| --- | ---------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------- |
| 1   | ctor `#available`                              | `:57-59`   | `(config.platform ?? process.platform) === "darwin" && executableExists("/usr/bin/sandbox-exec")`                   |
| 2   | `verify()`                                     | `:88-97`   | runs `sandbox-exec -p <profile> /usr/bin/true`; `enforced` iff `error === undefined && status === 0`                |
| 3   | `wrap()`                                       | `:99-108`  | throws unless `#verified`; returns `{command: "/usr/bin/sandbox-exec", args: ["-p", profile, command, ...args]}`    |
| 4   | `spawnFor` / `noteHealthy` / `healthyServices` | `:110-124` | bookkeeping only; the comment at `:121` says "Evidence is diagnostic only; the authority was enabled by `verify()`" |

The profile itself is `buildMacosWorkspaceSeatbeltProfile` (`:136-170`): `(deny default)`, `(import "system.sb")`, `(allow process-exec)`, `(allow file-read-metadata)`, `file-read*` for a fixed subpath list, `file-write*` for `childDataDirs ∪ temporaryDir`, and `(allow network*)`. **No user-granted workspace root is ever in that list** — that is the property the attestation exists to assert.

The supervisor consumes it at [`desktop-supervisor.ts:335-338`](../../../apps/desktop/main/services/desktop-supervisor.ts) (`wrap`), `:345-352` (`spawnFor`) and `:358-360` (`noteHealthy`). Its config field is typed with the **concrete macOS class**: `readonly workspaceChildConfinement?: MacosWorkspaceConfinement` (`:102`, imported at `:48`). `main/index.ts:791` passes the verified instance.

### C2 — Three verified weaknesses in what "enforced" means today

**W1 — `verify()` proves the profile _parses_, not that it _denies_.** `sandbox-exec -p <profile> /usr/bin/true` exits 0 whenever the profile is syntactically valid and a trivial binary under `/usr` can exec. A profile that accidentally allowed `file-read*` on `/` would pass identically. The attestation that reaches the ai-backend claims isolation is `enforced`; the evidence behind it is "the sandbox compiler accepted this s-expression."

**W2 — `nativeWorkspacePrimitives: "available"` is a literal, not a measurement.** [`workspace-production-authority.ts:107-110`](../../../apps/desktop/main/capabilities/workspace-production-authority.ts) constructs `Object.freeze({workspaceWriteIsolation: "enforced", nativeWorkspacePrimitives: "available"})` **before** the helper is launched, and passes it _into_ `NativeWorkspaceCommitHelper.launch` as a precondition (`:118-127`). `launch` then re-reads it (`native-workspace-commit-helper.ts:174-175`) and refuses if it is not that exact pair — i.e. the value is checked against itself. What actually grounds it is downstream: `helper.primitivesAvailable` (`:128`, a class constant at `native-workspace-commit-helper.ts:142`), the forced authenticated `Ping` inside `launch` (`:249`), and the `rootIdentity` round-trip at `:133-134`. The evidence exists; the _field_ does not carry it.

**W3 — the diagnostic path is honest and the attestation path is not.** `healthyServices()` records which supervised children were actually launched through `wrap()`. Nothing consumes it for the attestation. So "isolation is enforced" can be true while zero children have been launched confined.

None of these are exploitable today, because the whole surface is darwin-and-packaged-and-production-gated. They matter now because FS-03 is about to define what the same words mean on a second platform, and copying the current standard would export the weakness.

### C3 — The Windows read path: the source exists; nothing ships it

[`apps/desktop/native/workspace-fs/src/workspace_fs.c:186-348`](../../../apps/desktop/native/workspace-fs/src/workspace_fs.c) implements a Win32 `openBeneath`: `CreateFileW` on the root with `FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT` (`:299-302`), then a per-component `NtCreateFile` walk relative to the parent handle (`open_component`, `:239-272`) with `FILE_OPEN_REPARSE_POINT` and an explicit `FILE_ATTRIBUTE_REPARSE_POINT` refusal (`:263-269`), tokenising on both separators (`:318-319`), converting the final `HANDLE` to a CRT fd (`:338`). `binding.gyp:9-13` links `ntdll` and defines `UNICODE`/`_UNICODE`/`WIN32_LEAN_AND_MEAN` for `OS=="win"`. Its own header comment at `:188-196` says **"UNTESTED ON A WINDOWS HOST in this environment."**

What is _not_ true is that Windows has a confined read in a shipped build:

- No script builds it. `grep -rn "workspace-fs" apps/desktop/package.json apps/desktop/esbuild.config.mjs .github/workflows/` returns nothing; the only build entry points are `native/workspace-fs/package.json`'s own `build` / `build:electron` scripts, which nothing invokes.
- No packaging carries it. `electron-builder.yml:24-27` ships `out/**/*` + `package.json` into the asar; `extraResources` (`:36-47`) carries only `resources/runtime` and `native/workspace-commit-helper/bin`. There is no `asarUnpack`.
- Therefore `loadNativeWorkspaceFs()` ([`host-fs.ts:199-216`](../../../apps/desktop/main/capabilities/host-fs.ts)) — which resolves `["..","..","native","workspace-fs","index.cjs"]` relative to the emitted `out/main/index.js` — cannot resolve in a packaged app, catches, and returns `undefined`.

So on a packaged Windows install today the read path is the **non-atomic** fallback that `host-fs.ts:851-856` documents: `O_NOFOLLOW` (a no-op on Windows — see `openReadFlags`, `:1136-1146`) plus a post-open `fstat`-vs-`lstat` identity recheck and a `realpath` containment recheck (`:889-897`). That is a conservative denial, not an atomic one, and the README's capability table ("Confined read ✅ native module builds") is describing the source, not the product.

One further verified defect in that file: `MultiByteToWideChar(CP_UTF8, 0, …)` at `:285` and `:286` omits `MB_ERR_INVALID_CHARS`, so malformed UTF-8 is silently substituted with U+FFFD instead of failing closed.

### C4 — Path validation today: two layers, and they do not agree

**Layer 1, TypeScript, read + write:** `normalizeVirtualPath` ([`path-validation.ts:195-229`](../../../apps/desktop/main/capabilities/path-validation.ts)) rejects NUL (`:199-201`), over-long paths (`:202-204`), POSIX-absolute and UNC (`^[/\\]`, `:207-209`), drive-letter (`^[A-Za-z]:`, `:210-212`), and then per segment via `assertSegmentSafe` (`:148-183`): empty, over-long (255 bytes), non-well-formed UTF-16, and — checked against **both** the raw form and its NFKC form (`:161`) — `.`/`..`, embedded separators, `:` (ADS or drive), control characters, Windows reserved device names (`:176`), and trailing dot or space (`:181`). The NFKC pass is load-bearing and already closes a Windows quirk for free: `COM¹` (U+00B9) NFKC-folds to `COM1`, and `isReservedDeviceName` (`:137-140`) splits on the first `.` and lower-cases, so `COM¹.txt` is rejected.

`WINDOWS_RESERVED` (`:104-127`) contains `con prn aux nul com1..com9 lpt1..lpt9`. Microsoft's file-naming documentation additionally lists `COM0`, `LPT0`, `CONIN$` and `CONOUT$` as reserved. Those four are **absent** from the set.

**Layer 2, C, write only:** `path_is_safe` ([`workspace_commit_helper.c:313-332`](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c)) rejects empty, leading `/`, any `\`, `> MAX_PATH_BYTES` (4096), empty/`.`/`..` segments, and restricts every byte to `[A-Za-z0-9._-]` with an explicit comment (`:325-327`) that ASCII-only is what lets the helper require an exact directory entry at every case-insensitive hop.

**The divergence that matters.** `workspace-authority.ts:19-22` states the design intent explicitly: "The native helper independently enforces this rule and exact directory-entry bytes." It does not. `path_is_safe` accepts `NUL`, `CON.txt`, `com1`, `lpt3.log`, and `foo.` — every one of which the TypeScript layer rejects, and every one of which is a Windows device or a Windows-aliased name. On macOS this is harmless. On Windows the TCB would be relying on its caller for a control the comment says it does not rely on the caller for.

`path_is_safe` also never sees the **root** path. `command_root_identity` (`:837-843`) and `command_prepare` (`:845+`) hand the wire string straight to `open_root` (`:365-369`). On POSIX that is safe by construction: a relative root resolves against the child's cwd, which `native-workspace-commit-helper.ts:211` pins to `/`. Windows has no single cwd — `C:foo` resolves against the _per-drive_ current directory, `\foo` against the current drive — so the same string is an ambient-authority path there. Nothing in the code rejects it.

### C5 — `ConfinedCommand` is a macOS-shaped abstraction

`wrap()` returns `{command, args}` (`macos-workspace-confinement.ts:31-34`) because `sandbox-exec` applies a profile and then `exec`s in place: same pid, so `PythonService`'s `child.kill("SIGTERM")` (`python-service.ts:144`) reaches the real Python process, and its exit code (`:170-175`) is Python's.

Windows has no in-place confining exec. Every candidate mechanism (AppContainer, restricted token, integrity level) is applied at _process creation_ through `CreateProcessAsUserW` with a token and/or `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES` — which `node:child_process.spawn` cannot express. And `SpawnFn` (`python-service.ts:43-51`) is a fixed `(command, args, {cwd, env, stdio}) => ChildLike`, so the confinement cannot smuggle a token through it either.

### C6 — The signed attestation is a closed cross-language contract

`DesktopWorkspaceAttestationClaims` ([`workspace-attestation.ts:25-33`](../../../apps/desktop/main/capabilities/workspace-attestation.ts)) is serialised by `canonicalClaimsJson` (`:160-167`) and verified in Python by [`workspace_attestation.py:44-54`](../../../services/ai-backend/src/agent_runtime/capabilities/desktop/workspace_attestation.py), whose base `RuntimeContract` sets `extra="forbid"` ([`contracts.py:43`](../../../services/ai-backend/src/agent_runtime/execution/contracts.py)). `supports_workspace_commit` (`:72-81`) requires exactly `workspace_write_isolation == "enforced" and native_workspace_primitives == "available" and unsafe_dev_workspace_tcb is False`. **Adding a claim field breaks the verifier** unless both sides change in the same release. FS-03 therefore adds no claim field.

### C7 — CI compiles and runs none of this

`ci-desktop.yml:60-61` runs the single desktop job on `ubuntu-latest`. There is no Windows leg and no native compile. `macos-workspace-confinement.test.ts` is pure-unit against injected seams (`runSelfTest`, `executableExists`), so it passes on Linux without a sandbox ever existing.

## Interfaces consumed

- **`WorkspaceConfinementProbe`** (`workspace-production-authority.ts:33-35`) — the `verify(): Promise<"enforced" | "unavailable">` signature is **kept**, so `createProductionWorkspaceAuthority:99` does not change shape.
- **`ProductionWorkspaceAuthorityConfig`** (`:53-68`) — `confinement`, `platform`, `packaged`, `production` all already exist and are already injectable.
- **`WorkspaceWriteAttestation`** (`workspace-authority.ts:36-48`) and `writableAvailable()` (`:381-389`) — unchanged.
- **`DesktopWorkspaceAttestationClaims`** / `canonicalClaimsJson` (`workspace-attestation.ts:25-33`, `:160-167`) and the Python verifier — **unchanged**, per C6.
- **FS-01's platform seam** — `fs_open_root`, `fs_open_dir_at`, `fs_stat_at`, `fs_stat_handle`, `fs_dir_for_each`, `fs_identity_*`, `fs_volume_supported`, `fs_dir_is_app_private`. FS-03 writes the Win32 bodies for the four confinement-critical ones and specifies their refusal sets.
- **FS-02's Win32 artefacts** — `fs_platform_win32.c`, the Authenticode verifier (`win-authenticode`), `build/sign-nested-win.js`, and the `win32` entry in `HELPER_PLATFORM_PROFILES`. See D1 for the ownership split when the two PRDs land in either order.
- **`normalizeVirtualPath`** / `assertSegmentSafe` (`path-validation.ts:148-229`) and `assertNativeWorkspaceCanonicalPath` (`workspace-authority.ts:17-29`).
- **`SpawnFn`** (`python-service.ts:43-51`), **`DesktopSupervisorConfig`** (`desktop-supervisor.ts:95-103`), **`resolveRuntimePaths`** (`runtime-paths.ts:63-80`, which already resolves `python.exe` on win32).

## Interfaces exposed

### 1. `WorkspaceChildConfinement` — the platform-neutral supervisor contract

New file `apps/desktop/main/services/workspace-child-confinement.ts`. Extracted so `desktop-supervisor.ts` stops naming a macOS class.

```ts
import type { WorkspaceConfinementProbe } from "../capabilities/workspace-production-authority";
import type { SpawnFn } from "./python-service";
import type { SupervisedServiceName } from "./runtime-paths";

/** A command line that, when spawned, runs `command` under confinement. */
export interface ConfinedCommand {
  readonly command: string;
  readonly args: readonly string[];
}

/** What `verify()` observed, for logs and FS-09's reporting. Never signed. */
export interface WorkspaceConfinementEvidence {
  readonly mechanism:
    | "macos-seatbelt"
    | "windows-appcontainer"
    /** SPIKE-C2's outcome, if a restricted token turns out to be viable. The
     *  union must be able to NAME the mechanism that ships; without this member
     *  a successful SPIKE-C2 has nowhere to report itself and would be forced
     *  to masquerade as `windows-appcontainer` (D3). */
    | "windows-restricted-token"
    | "windows-none";
  /** The confined child was observed to READ an inside-the-boundary canary. */
  readonly positiveControl: "passed" | "failed" | "not-run";
  /** The confined child was observed to FAIL to read an outside canary. */
  readonly negativeControl: "passed" | "failed" | "not-run";
  /** Read-path confinement actually in force this boot (C3). Diagnostic. */
  readonly nativeReadConfinement: "atomic" | "fallback";
  /** Stable, path-free reason when `verify()` returned "unavailable". */
  readonly unavailableReason?:
    | "platform_unsupported"
    | "launcher_missing"
    | "launcher_signature_rejected"
    | "mechanism_unavailable"
    | "positive_control_failed"
    | "negative_control_failed"
    | "probe_error";
}

/**
 * The whole child-confinement surface the supervisor may depend on. Both
 * platform implementations satisfy it; `desktop-supervisor.ts` types on THIS,
 * never on a concrete class.
 */
export interface WorkspaceChildConfinement extends WorkspaceConfinementProbe {
  /** Throws unless a prior `verify()` returned "enforced". */
  wrap(command: string, args: readonly string[]): ConfinedCommand;
  /** Wraps the raw spawn so the implementation can record or extend it. */
  spawnFor(name: SupervisedServiceName, spawnFn: SpawnFn): SpawnFn;
  noteHealthy(name: SupervisedServiceName): void;
  healthyServices(): readonly SupervisedServiceName[];
  /** Populated by `verify()`; stable between calls. */
  evidence(): WorkspaceConfinementEvidence;
}
```

`MacosWorkspaceConfinement` gains `implements WorkspaceChildConfinement` and an `evidence()`; nothing else about it changes shape. `desktop-supervisor.ts:102` becomes `readonly workspaceChildConfinement?: WorkspaceChildConfinement;` and the `MacosWorkspaceConfinement` import at `:48` is deleted.

### 2. `WindowsWorkspaceConfinement`

New file `apps/desktop/main/services/windows-workspace-confinement.ts`.

```ts
export interface WindowsWorkspaceConfinementConfig {
  /** Absolute, main-owned. Never resolved through PATH or %PATH%. */
  readonly launcherPath: string;
  readonly runtimeRoot: string;
  readonly webDir: string;
  /** Only the child-owned app-data roots; never the whole userData tree. */
  readonly childDataDirs: readonly string[];
  readonly temporaryDir: string;
  readonly pythonBin: string;
  readonly serviceDirs: readonly string[];
  /** Main-created directory holding the two canary files (see D7). */
  readonly canaryDir: string;
  /** Main-created directory OUTSIDE every allowed subpath (see D7). */
  readonly forbiddenCanaryDir: string;
  readonly platform?: NodeJS.Platform;
  readonly executableExists?: (path: string) => boolean;
  /** Authenticode check for the launcher. Defaults to FS-02's verifier. */
  readonly verifyLauncher?: (path: string) => boolean;
  /** Test seam mirroring MacosWorkspaceConfinement.runSelfTest. */
  readonly runSelfTest?: (
    command: string,
    args: readonly string[],
  ) => {
    readonly status: number | null;
    readonly stdout?: string;
    readonly error?: Error;
  };
}

export class WindowsWorkspaceConfinement implements WorkspaceChildConfinement {
  constructor(config: WindowsWorkspaceConfinementConfig);
  verify(): Promise<"enforced" | "unavailable">;
  wrap(command: string, args: readonly string[]): ConfinedCommand;
  spawnFor(name: SupervisedServiceName, spawnFn: SpawnFn): SpawnFn;
  noteHealthy(name: SupervisedServiceName): void;
  healthyServices(): readonly SupervisedServiceName[];
  evidence(): WorkspaceConfinementEvidence;
  /** Test-only, mirrors `profileForTesting`. */
  get policyForTesting(): string;
}
```

### 3. The confinement launcher — `copilot-confine.exe`

New native component `apps/desktop/native/workspace-confine/`, built and packaged with exactly the discipline the commit helper already has.

```
apps/desktop/native/workspace-confine/
  build.mjs                 builder table; win32 only, sentinel elsewhere
  README.md                 the launcher contract
  src/confine_main.c        argv parsing, policy application, spawn, wait
  src/confine_policy.c      token / AppContainer profile construction
  src/confine_probe.c       --self-test child mode
  bin/                      gitignored build output
```

Command line — **positional, no shell, no environment input**:

```
copilot-confine.exe --policy-version 1
                    --allow-read  <abs-dir>      (repeatable)
                    --allow-write <abs-dir>      (repeatable)
                    --                            (end of policy)
                    <command> [args...]
```

Self-test mode, used only by `verify()`:

```
copilot-confine.exe --policy-version 1 --allow-read … --allow-write …
                    --self-test --probe-allow <abs-file> --probe-deny <abs-file>
```

In self-test mode the launcher applies the policy, re-execs **itself** as the confined child with `--probe-child`, and the child writes exactly two lines to stdout and exits 0:

```
probe-allow=<open|denied|error>
probe-deny=<open|denied|error>
```

Main — not the launcher — grades those two lines (D7). Exit codes: `0` the child ran and its status is forwarded verbatim; `10` policy parse failure; `11` the confinement mechanism was unavailable; `12` the child could not be created. Every failure path writes nothing to stdout, so a truncated stdout is never mistaken for a pass.

### 4. Win32 confined open — the normative refusal set

The bodies live in FS-02's `fs_platform_win32.c`; the semantics below are FS-03's and are the acceptance criteria for that file.

```c
/* Absolute host path from the wire (the ONLY multi-component path in the
 * seam). MUST reject every form in D4's grammar table before conversion, then
 * convert with RtlDosPathNameToNtPathName_U (fallback: an explicitly
 * "\\?\"-prefixed CreateFileW) and open with:
 *     NtCreateFile(&h, FILE_LIST_DIRECTORY | FILE_TRAVERSE | SYNCHRONIZE,
 *                  &oa,                       // Attributes = OBJ_DONT_REPARSE
 *                                             // (NOT OBJ_CASE_INSENSITIVE)
 *                  &iosb, NULL, 0,
 *                  FILE_SHARE_READ | FILE_SHARE_WRITE,   // NOT SHARE_DELETE
 *                  FILE_OPEN,
 *                  FILE_DIRECTORY_FILE | FILE_OPEN_REPARSE_POINT |
 *                  FILE_SYNCHRONOUS_IO_NONALERT,
 *                  NULL, 0)
 * then refuse on FILE_ATTRIBUTE_REPARSE_POINT.
 *
 * SCOPE NOTE (consistency pass). fs_open_root OPENS; it does not decide policy.
 * The non-NTFS / remote-volume refusal (FS-02 D7) is NOT in this body: it is the
 * portable `supported_root_handle` above the seam calling `fs_volume_supported`
 * (FS-01 §4's mapping table and its implementation plan), and the identity is
 * read by the portable caller via `fs_stat_handle(h, &meta).id`, not returned
 * from here. Folding either into the provider would put policy below the seam —
 * exactly what FS-01 D1 draws the seam to prevent — and would let the two
 * providers' volume gates drift. What FS-02 D7 owes is the *body* of
 * `fs_volume_supported` on Win32 (GetVolumeInformationByHandleW == "NTFS", plus
 * the FileRemoteProtocolInfo test of SPIKE-W4). */
int fs_open_root(const char *path, fs_handle *out);

/* ONE already-path_is_safe'd component, relative to a retained handle:
 *   oa.RootDirectory = dir.raw; oa.Attributes = OBJ_DONT_REPARSE;
 *   UNICODE_STRING name = the component, no separator, no NUL terminator.
 * Refuse, in this order, ALL of:
 *   1. any status other than STATUS_SUCCESS;
 *   2. FILE_ATTRIBUTE_REPARSE_POINT on the opened handle;
 *   3. volume serial != the root's (fs_identity_same_volume);
 *   4. the long-name / short-name enumeration check of D5. */
int fs_open_dir_at(fs_handle dir, const char *leaf, fs_handle *out);
```

### 5. The Windows name grammar, in C, applied on both platforms

```c
/* workspace_commit_helper.c — portable, compiled once, run everywhere.
 * Called by path_is_safe for EVERY segment, and by root_path_is_safe for
 * every segment of the root. Returns 1 when the name is safe on the strictest
 * supported platform. */
static int name_is_windows_safe(const char *name, size_t length);

/* NEW. Applied to the root path of ROOT_IDENTITY and PREPARE before
 * fs_open_root ever sees it. See D4. */
static int root_path_is_safe(const char *path);
```

### 6. TypeScript path-validation parity

```ts
// path-validation.ts — additions only, no signature changes.

/** Reserved basenames per Microsoft's file-naming rules. */
const WINDOWS_RESERVED: ReadonlySet<string>; // + com0, lpt0, conin$, conout$

/**
 * Grammar for an ABSOLUTE host root path (the grant root), as opposed to a
 * virtual in-grant path. Pure. Throws FsError('invalid_path').
 * Accepts exactly:  POSIX  `/…`
 *                   Win32  `<Drive>:\…` with a single colon at index 1.
 * Rejects: drive-relative (`C:foo`), root-relative (`\foo`), UNC (`\\srv\s`),
 * the `\\?\` and `\\.\` namespaces, any embedded NUL, and any segment that
 * fails `assertSegmentSafe`.
 */
export function assertAbsoluteHostRoot(
  raw: string,
  platform?: NodeJS.Platform,
): void;
```

### 7. Packaging and build outputs

| platform | `native/workspace-confine` output                                    | packaged path                                           |
| -------- | -------------------------------------------------------------------- | ------------------------------------------------------- |
| win32    | `bin/copilot-confine.exe`                                            | `<resourcesPath>\workspace-confine\copilot-confine.exe` |
| other    | `bin/copilot-confine` sentinel (`0o400`, `"unsupported platform\n"`) | present, non-executable, never spawned                  |

`electron-builder.yml` gains one `extraResources` entry mirroring `:44-47`:

```yaml
- from: native/workspace-confine/bin
  to: workspace-confine
  filter:
    - "copilot-confine*"
```

and `build/sign-nested-win.js` (FS-02 step 9) signs it alongside the commit helper.

## Design

### D1. FS-03 owns the boundary; FS-02 owns the effect — and the overlap is named, not negotiated

FS-02 and FS-03 both touch `fs_platform_win32.c` and both touch `path_is_safe`. That is not a conflict to discover during review, so it is resolved here:

- **FS-02 owns:** `fs_commit_create`, `fs_commit_mkdir`, staging, journalling, crypto, capability delivery, the build/sign/package path for `workspace-commit-helper.exe`, and the `win32` entry in `HELPER_PLATFORM_PROFILES`.
- **FS-03 owns:** `fs_open_root`, `fs_open_dir_at`, the name grammar (`name_is_windows_safe`, `root_path_is_safe`), the TS path-validation parity, the confinement probe, the launcher, and the attestation contract.
- **Ordering.** Whichever lands first writes the file; the second adopts it. FS-02's D5 ("three Windows-only rules") is a _subset_ of D4/D5 below and is superseded by them — with the same intent and no relaxation. FS-02's D4 ("per-component walk, reparse-refusing, exact-long-name") is the same design as D5 below; where the two texts differ in a flag or an order, **this PRD is normative** and FS-02's file is corrected to match, because FS-03 carries the test matrix that proves it.
- **Neither PRD alone unlocks Windows writes.** FS-02 registering `win32` in the helper registry makes `workspace-production-authority.ts:85` pass, but `main/index.ts:640-671` still constructs only `MacosWorkspaceConfinement`, whose `verify()` returns `"unavailable"` off darwin (`macos-workspace-confinement.ts:57-59`, `:89`), so `createProductionWorkspaceAuthority` returns `null` at `:99`. That ordering property is not incidental and is pinned by a test in T7.

### D2. The probe's contract becomes "observed denial", on both platforms

W1 says today's `verify()` proves the profile compiles. Exporting that standard to Windows would mean shipping `"enforced"` on the basis of "`CreateAppContainerProfile` returned success" — an API-availability claim, which is precisely what the spine's guarantee forbids ("Never claim an outcome that was not observed").

So `verify()` gains a fixed shape on **both** platforms, and it is the same shape:

1. **Positive control.** Launch a confined child that reads a main-created canary file _inside_ an allowed subpath. It must succeed. This is what catches an over-restrictive policy that would have taken the whole app down anyway — it converts a boot failure into an honest `"unavailable"`.
2. **Negative control.** Launch a confined child that attempts to read a main-created canary file _outside_ every allowed subpath. It must fail.
3. **Main grades both.** The child reports what it observed; main decides. A confined child that lies, or a launcher that silently applies no policy, produces `probe-deny=open` and the probe returns `"unavailable"`.

`"enforced"` is returned **only** when both controls pass. Anything else is `"unavailable"` with a stable `unavailableReason`.

Concretely on macOS (no new binary required — both tools are under `/usr`, which the profile already allows at `macos-workspace-confinement.ts:140-141`):

| control              | command                                                                                                           |
| -------------------- | ----------------------------------------------------------------------------------------------------------------- |
| existing parse check | `sandbox-exec -p <profile> /usr/bin/true` → status 0                                                              |
| positive             | `sandbox-exec -p <profile> /usr/bin/head -c 1 <temporaryDir>/…canary` → status 0                                  |
| negative             | `sandbox-exec -p <profile> /usr/bin/head -c 1 <userData>/capabilities/workspace-v2/canary/forbidden` → status ≠ 0 |

`userData` is deliberately correct as the forbidden location: `main/index.ts:654-657` grants the child only `userData/agent-data/v1` and `userData/model-catalog`, never the tree that holds the grant store and the token vault. If the negative control ever passes on macOS, the profile has regressed in exactly the way that matters.

**This is a behaviour change on macOS.** It is intentional and it is the "parity" in this PRD's title: after FS-03, `"enforced"` means the same observed thing on both platforms. The risk — a working install newly reporting `"unavailable"` — is bounded by the positive control, which fails loudly and with a named reason rather than silently; and by the fact that three independent gates already had to pass for the probe to run at all.

### D3. The mechanism is chosen by a spike, and a weaker mechanism does not get the word "enforced"

macOS's Seatbelt profile denies **read and write** outside its allow-list. A per-user, non-elevated Windows app has three candidate mechanisms and they do not all reach that bar. Stating the ranking honestly is more useful than picking one here.

| mechanism                                                                                      | denies reads?                                                                                             | denies writes? | known blocker                                                                                                                                                                                                                                                                                                                                                          |
| ---------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **AppContainer** (`CreateAppContainerProfile` + `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES`) | yes — deny-by-default; a file is reachable only if its DACL names the container SID or a capability SID   | yes            | _unverified:_ AppContainer processes are documented as blocked from loopback networking without a `CheckNetIsolation LoopbackExempt` entry, which requires elevation. Our three supervised children **are** loopback servers. SPIKE-C1.                                                                                                                                |
| **Restricted token** (`CreateRestrictedToken` with deny-only groups + a restricting SID set)   | yes, in principle                                                                                         | yes            | _unverified:_ every DLL the child loads for the whole process lifetime must be readable by the restricting SID. Whether `%SystemRoot%\System32` grants `NT AUTHORITY\RESTRICTED` read+execute on current Windows is not something this PRD asserts. CPython imports lazily forever, so the two-stage "lower the token after startup" pattern is unavailable. SPIKE-C2. |
| **Low integrity level** (`SetTokenInformation(TokenIntegrityLevel, S-1-16-4096)`)              | **no** — the default mandatory policy is `NO_WRITE_UP` only; a low-IL process still reads medium-IL files | yes            | none known; cheap, documented, does not affect loopback                                                                                                                                                                                                                                                                                                                |

The decision rule, not the decision:

- If SPIKE-C1 shows AppContainer can serve and connect on loopback without elevation, **AppContainer is the mechanism** and `mechanism: "windows-appcontainer"`.
- If it cannot, FS-03 ships `verify()` returning `"unavailable"` with `unavailableReason: "mechanism_unavailable"` on Windows, and **Windows writes stay off**. Low-IL is _not_ substituted, because low-IL is write-confinement only and the attestation the ai-backend consumes (`workspace_attestation.py:72-81`) is a single boolean pair with no room to say "half". Shipping low-IL under the label `enforced` would make the word mean two different things on two platforms, which is the exact failure this PRD exists to prevent.
- Low-IL is still applied _in addition_ whenever the mechanism is available, as defence in depth. It is never the mechanism.

Rejected outright, with reasons, so they are not re-proposed:

- **Setting `SYSTEM_MANDATORY_LABEL_NO_READ_UP` on the user's granted folders.** It would make low-IL sufficient, but it rewrites the security descriptor of the user's own data, persists after uninstall, and silently denies every _other_ low-IL process on the machine. Modifying user data to make our sandbox look better is not a confinement strategy.
- **Moving the loopback topology to named pipes to make AppContainer viable.** It is the real escape hatch if read-confinement on Windows is judged essential, and it is a re-plumb of all three services plus the broker. Out of scope; named so the option is on record.
- **Windows Sandbox / Hyper-V containers.** Requires Pro/Enterprise and virtualisation; not available to the consumer install this product targets.

### D4. The root path gets a grammar, because Windows has no single cwd

C4 established that the wire root path reaches `open_root` unvalidated, and that on Windows several spellings carry ambient authority. `root_path_is_safe` is added above the seam (portable, compiled on both platforms) and runs before `fs_open_root` in `command_root_identity` and `command_prepare`.

| form               | example                        | POSIX today                                    | rule                                                                          |
| ------------------ | ------------------------------ | ---------------------------------------------- | ----------------------------------------------------------------------------- |
| POSIX absolute     | `/Users/a/w`                   | accepted                                       | accepted on POSIX; rejected on Win32                                          |
| drive absolute     | `C:\Users\a\w`                 | n/a                                            | **the only** accepted Win32 form: `[A-Za-z]` `:` `\` then ≥1 segment          |
| drive relative     | `C:w`, `C:`                    | n/a                                            | rejected — resolves against the per-drive current directory                   |
| root relative      | `\Users\a\w`                   | rejected (`path[0]=='/'` is a different check) | rejected — resolves against the current drive                                 |
| UNC                | `\\srv\share\w`                | n/a                                            | rejected — also enforces FS-02 D7's local-volume rule at the syntax layer     |
| extended-length    | `\\?\C:\…`                     | n/a                                            | rejected on input; the helper adds the prefix itself if it needs one          |
| device namespace   | `\\.\C:`, `\\.\PhysicalDrive0` | n/a                                            | rejected                                                                      |
| forward slashes    | `C:/Users/a/w`                 | n/a                                            | rejected — one spelling only, so the exact-entry walk has one canonical input |
| trailing separator | `C:\Users\a\w\`                | accepted                                       | rejected — `\` and `\\` differ under NT-native naming                         |
| embedded `.`/`..`  | `C:\a\..\b`                    | rejected                                       | rejected (segment rule)                                                       |
| second colon       | `C:\a:b`                       | n/a                                            | rejected — ADS                                                                |
| over-length        | > `MAX_PATH_BYTES`             | rejected                                       | unchanged                                                                     |
| per-segment        | —                              | not applied to the root                        | **now applied**: every segment must pass `name_is_windows_safe`               |

Rejecting forward slashes and trailing separators on Windows is stricter than Win32 itself and is deliberate: the grant root is produced by `dialog.showOpenDialog` (`main/index.ts:399-411`) and stored, so there is exactly one producer and it emits one spelling. A second accepted spelling would mean two byte strings naming one root, and the root identity, the claim binding and the grant comparison are all byte comparisons.

The conversion after validation is `RtlDosPathNameToNtPathName_U` (an `ntdll` export, resolved by `GetProcAddress` on the already-loaded module exactly as `workspace_fs.c:276-282` does), which handles long paths and drive mapping uniformly and yields `\??\C:\…`. If SPIKE-C3 shows that export is unusable, the fallback is a `\\?\`-prefixed `CreateFileW` — which is only sound _because_ the grammar above has already guaranteed the path is canonical, since `\\?\` disables Win32 normalisation.

### D5. Per-component confined open: five refusals, and the enumeration is the load-bearing one

`fs_open_dir_at` is `NtCreateFile` with `oa.RootDirectory = dir`, a single-component `UNICODE_STRING`, and no separator — so the name cannot escape the parent, which is the same structural property `openat`+`O_NOFOLLOW_ANY` gives on macOS (`workspace_commit_helper.c:390`). On top of that:

1. **Reparse refusal, twice.** `oa.Attributes |= OBJ_DONT_REPARSE` asks the object manager to fail rather than follow, _and_ `CreateOptions |= FILE_OPEN_REPARSE_POINT` plus an explicit `FILE_ATTRIBUTE_REPARSE_POINT` check on the returned handle refuses it after the fact (the precedent at `workspace_fs.c:263-269`). Both are specified because their interaction is genuinely uncertain: `FILE_OPEN_REPARSE_POINT` means "do not traverse the final component", and a single-component relative open has no other component, so it is not obvious whether `OBJ_DONT_REPARSE` then fires at all. SPIKE-C4 settles which of the two actually fires; **both stay in the code regardless**, because the cost is one flag and one `GetFileInformationByHandle`.
2. **No `OBJ_CASE_INSENSITIVE`.** `workspace_fs.c:248` passes it; the write walk must not. But omitting it is _not_ a case-sensitivity guarantee: the kernel's `ObCaseInsensitive` setting forces case-insensitive lookup system-wide by default, so the flag is advisory here. Say so in the code comment rather than implying a guarantee.
3. **The enumeration check is authoritative.** Because (2) cannot be relied on, the exact-entry check is what actually rejects a case-folded spelling — the same role `directory_has_exact_entry` (`workspace_commit_helper.c:338-346`) plays on APFS. Windows needs one more clause than macOS: NTFS may maintain an 8.3 short name as a _second_ directory entry, so enumerate with `NtQueryDirectoryFileEx` (fallback `NtQueryDirectoryFile`) using `FileBothDirectoryInformation` and require, for the matched entry, `FileNameLength == wcslen(name)*2 && memcmp(FileName, name, …) == 0`. A match on `ShortName` alone is a refusal. (`~` is already outside the `path_is_safe` charset, so a _requested_ 8.3 alias cannot reach here; this clause defends against the on-disk entry, not the request.)
4. **Same volume.** `fs_identity_same_volume` against the root's `FILE_ID_INFO.VolumeSerialNumber` — the `st_dev` check at `workspace_commit_helper.c:392`. A mount point is a reparse point and is already refused by (1); this catches the rest.
5. **`FILE_SHARE_DELETE` is withheld**, so the walked directory cannot be renamed or deleted mid-transaction. This is strictly stronger than the POSIX fd, which pins the inode but not the name, and its cost — an external rename of a walked folder during a sub-second, user-initiated prepare fails with a sharing violation — is accepted and asserted in T4.21.

**Read path.** `workspace_fs.c`'s Win32 branch is brought to the same standard: `MB_ERR_INVALID_CHARS` on both `MultiByteToWideChar` calls (`:285-286`), `OBJ_DONT_REPARSE` alongside the existing `FILE_OPEN_REPARSE_POINT`, and `OBJ_CASE_INSENSITIVE` removed (`:248`). It does **not** gain the enumeration check, and the reason takes one correction first.

`#openAtomicNative` (`host-fs.ts:912-935`) deliberately performs **no** post-open recheck, on the stated grounds that the native open already proves containment (`:919-921`). That is true for containment. But it is fed `target.relPosix`, and `relPosix` is `segments.join("/")` (`:831`, and `:1018` for the write-target variant) — the **normalised request**, not anything derived from `targetReal`. So on Windows the object is _authorised_ under its `realpath`'d canonical name (`:794-809`, which is also what the sensitive-file check at `:351` reads) and then _opened_ under whatever spelling the caller sent. A case-folded or 8.3 spelling walks case-insensitively to the same object today, so this is not an escape — the walk is still root-confined and reparse-refusing. It is, however, two different names for one authorisation decision, and it is the kind of gap that becomes an escape the moment anything downstream keys on the requested name.

FS-03 closes it with the minimal change rather than by adding the enumeration check to the read walk: `#openAtomicNative` passes `relative(target.rootReal, target.targetReal)` with separators posixified — a value that _is_ realpath-derived, and therefore canonical in case and long-name form on Windows (`GetFinalPathNameByHandleW` semantics). `relPosix` keeps its current meaning for every other consumer (`:558`, glob/grep result paths), which must stay the requested spelling because it is what the caller sees. After that change the invariant "the native branch is only ever called with a realpath-derived relative path" is true, and it gets a comment saying so, because `#openAtomicNative`'s no-recheck decision rests on it.

### D6. One name grammar, compiled once, enforced in the TCB

FS-03 closes C4's divergence in the direction that removes the dependency on the caller. `name_is_windows_safe` is portable C called from `path_is_safe` for every segment on **every** platform:

| rule                                                                                                                      | rejects                              | why it is not macOS-only paranoia                                                                                 |
| ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ | ----------------------------------------------------------------------------------------------------------------- |
| reserved device basename, case-insensitive, before the first `.` — `CON PRN AUX NUL COM0..COM9 LPT0..LPT9 CONIN$ CONOUT$` | `NUL`, `CON.txt`, `com1`, `lpt3.log` | these resolve to devices on Windows; on macOS nobody needs a file called `NUL`                                    |
| trailing `.`                                                                                                              | `foo.`                               | Win32 path APIs strip it, NT-native names do not — the file becomes unopenable by the user's own tools            |
| trailing space                                                                                                            | `foo `                               | already excluded by the `< 0x21` test at `:327`; asserted, not re-implemented                                     |
| component length > 255 UTF-16 units                                                                                       | —                                    | NTFS component limit; `MAX_PATH_BYTES` is a whole-path byte budget and does not bound a component                 |
| leading space, `$` prefix (`$Mft`)                                                                                        | ` foo`                               | already excluded by the `[A-Za-z0-9._-]` charset; asserted so a future charset widening cannot silently reopen it |

Applying all of this on macOS costs a real user nothing and keeps exactly one rule set — the spine's guardrail. It is not a substitute for the TypeScript layer; it removes the TCB's reliance on it.

`WINDOWS_RESERVED` in `path-validation.ts:104-127` gains `com0`, `lpt0`, `conin$`, `conout$` so the two layers list the same set. `assertSegmentSafe` already checks the NFKC form (`:161`), which is what makes `COM¹` fold to `COM1`; that behaviour is pinned by a test rather than left as a happy accident.

### D7. `verify()`'s canaries are main-created, and neither canary lives in a user folder

**Two** files in **two different directories** — and the difference between the
two directories is the whole control, so they cannot share a path:

| config field         | directory                                                                                                                     | file        | expectation                       |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------- | ----------- | --------------------------------- |
| `canaryDir`          | `join(temporaryDir, "0xcopilot-confinement-canary")` — inside the profile's write list (`macos-workspace-confinement.ts:155`) | `allowed`   | the confined child reads it       |
| `forbiddenCanaryDir` | `join(userDataDir, "capabilities", "workspace-v2", "canary")` — **not** in `childDataDirs` (`main/index.ts:654-657`)          | `forbidden` | the confined child cannot read it |

Both are created by main with `mkdirSync(..., {recursive: true, mode: 0o700})` —
the same private-directory discipline as `openPrivateDirectory`
(`workspace-production-authority.ts:188-200`).

Passing the same path for both — which an earlier draft of D12's snippet did —
puts the positive canary inside the forbidden tree, so a _correctly_ confined
child fails the positive control and `verify()` returns `"unavailable"` on every
healthy install. A test asserts the two directories are not equal (T1.5a).

Both are written with 16 bytes of `randomBytes` per boot, so a cached or fabricated success cannot be replayed across runs — the probe compares the _bytes the child reports_ against what main wrote, not merely an exit code. No user-granted workspace root is ever touched, and no canary is placed in a directory the user can see.

The negative canary is deliberately in userData rather than in a temp scratch directory: userData is where the grant store and the token vault live, so the negative control tests the boundary the product actually depends on.

### D8. Windows confinement is a launcher, not a wrapper flag — and the grandchild is in a Job

C5 established that Node cannot spawn with a token. The launcher restores the `sandbox-exec` shape (`wrap()` keeps returning `{command, args}`) at the cost of one extra process:

```
node spawn -> copilot-confine.exe  (medium IL, main-owned, signed)
                 |  CreateProcessAsUserW(confined token / AppContainer attrs,
                 |    bInheritHandles = TRUE,
                 |    STARTUPINFOEX.hStdOutput/hStdError = its own std handles,
                 |    CREATE_SUSPENDED)
                 +-> python.exe -m uvicorn …   (confined)
```

Four properties this must preserve, each of which is a way it can go wrong:

1. **stdio.** `PythonService` reads the child's stdout/stderr (`python-service.ts:164-169`). The launcher forwards its own `GetStdHandle(STD_OUTPUT_HANDLE)` / `STD_ERROR_HANDLE` to the grandchild through `STARTUPINFO` and inherits nothing else — the handle list is pinned with `PROC_THREAD_ATTRIBUTE_HANDLE_LIST` so no stray inheritable handle leaks into a confined process.
2. **Lifetime.** `child.kill("SIGTERM")` on Windows is `TerminateProcess` for _every_ signal, so the launcher dies abruptly and the grandchild would survive. The launcher therefore creates a Job object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, assigns the grandchild before resuming it, and holds the only handle. (Graceful uvicorn shutdown is already unavailable on Windows for the same `TerminateProcess` reason; this is not a regression, and it is stated so nobody "fixes" it by removing the job.)
3. **Exit code.** The launcher `WaitForSingleObject`s the grandchild and exits with `GetExitCodeProcess`'s value, so `PythonService`'s crash-loop accounting (`:170-176`) sees the real code. Launcher-internal failures use 10/11/12, which are outside the range uvicorn produces and are logged distinctly.
4. **Cwd and environment.** The launcher passes `cwd` and the environment block through **verbatim** from what `PythonService` gave it (`python-service.ts:157-161`). It adds nothing, reads no `%PATH%`, and resolves no relative program name.

The launcher is only ever spawned from an absolute, main-computed path (`resolveWorkspaceConfinePath`, mirroring `resolveNativeWorkspaceCommitHelperPath`, `native-workspace-commit-helper.ts:481-500`), never through `PATH` — the same discipline `MACOS_SANDBOX_EXEC` documents at `macos-workspace-confinement.ts:9-10`.

### D9. The launcher is verified before it is trusted, and the canary catches it if verification is bypassed

`electron-builder.yml:79-82` installs per-user under `%LOCALAPPDATA%` (`perMachine: false`), so the install directory is writable by the very account the confinement is meant to constrain. A replaced `copilot-confine.exe` that simply `CreateProcessW`s its argument would produce an unconfined child and a `verify()` that returns `"enforced"` — unless something checks.

Two independent controls, deliberately not one:

1. **Authenticode before use.** `verifyLauncher` defaults to FS-02's Authenticode verifier — the same `WinVerifyTrust` + pinned-signer-CN function that FS-02 installs as `HELPER_PLATFORM_PROFILES.get("win32").verifyPackagedExecutable`, called directly rather than through the profile, because the launcher is not a helper and has its own pinned CN. A packaged build whose launcher fails verification returns `"unavailable"` with `unavailableReason: "launcher_signature_rejected"`. This mirrors `native-workspace-commit-helper.ts:182-189` exactly, including that it applies only when `packaged === true`.
2. **The negative canary.** A passthrough launcher makes `probe-deny=open`, which fails the probe regardless of signature. This is the control that does not depend on a certificate existing — which matters, because `release-desktop.yml:147-150` currently builds Windows unsigned when `WIN_CSC_LINK` is absent (FS-02 D15).

Consequence, stated plainly: on the current release configuration, Windows confinement will report `"unavailable"` for signature reasons even if the mechanism works. Obtaining an Authenticode certificate is a shared prerequisite of FS-02 and FS-03, and it is a product decision, not an engineering one.

### D10. The attestation's words get a written contract — and the wire does not change

C6 forbids a new claim field. So the two existing fields get a definition table instead, placed in the doc comment of `WorkspaceWriteAttestation` (`workspace-authority.ts:39-48`) and mirrored in `workspace_attestation.py`'s module docstring:

| field                       | value           | asserts, on **every** platform                                                                                                                                                                                                      |
| --------------------------- | --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `workspaceWriteIsolation`   | `"enforced"`    | A confined child process was **observed** this boot to read an in-boundary canary and to fail to read an out-of-boundary canary, under the exact policy every supervised service is launched with.                                  |
|                             | `"unavailable"` | Anything else, including "the mechanism exists but was not exercised".                                                                                                                                                              |
| `nativeWorkspacePrimitives` | `"available"`   | A signature-verified native helper for this platform was launched, completed an authenticated `Ping` (`native-workspace-commit-helper.ts:249`), and answered a `ROOT_IDENTITY` query (`workspace-production-authority.ts:133-134`). |
|                             | `"unavailable"` | Anything else.                                                                                                                                                                                                                      |
| `unsafeDevWorkspaceTcb`     | `true`          | Development escape hatch. Never launch evidence; `supports_workspace_commit` rejects it (`workspace_attestation.py:79-81`). Unchanged.                                                                                              |

W2's structural fix is small and worth making while the definition is being written: `createProductionWorkspaceAuthority` stops constructing the frozen literal at `:107-110` before the helper exists. It builds `{workspaceWriteIsolation: <verify() result>, nativeWorkspacePrimitives: "unavailable"}`, passes a helper-launch precondition object as today (so `launch`'s existing check at `:174-175` is untouched), and only after `helper.primitivesAvailable && isRootIdentity(identity)` (`:128-134`) does it freeze the pair that goes into the seed and the publisher. The observable output for a healthy macOS install is identical; the difference is that the value is now downstream of the observation instead of upstream of it.

### D11. Five gates, restated for Windows

After FS-01, FS-02 and FS-03, a Windows install with the feature flag on is writable only if **all** of these hold. This is the list a reviewer should walk:

1. `helperPlatformProfile("win32")` is registered — FS-01's closed registry, FS-02's entry, which is unconstructable without an Authenticode verifier.
2. `app.isPackaged && production && safeStorage.isEncryptionAvailable()` — `workspace-production-authority.ts:85-89`, unchanged.
3. `confinement !== undefined` **and** `verify()` returned `"enforced"` — `:89`, `:99`. On Windows this now means both canary controls passed under a verified launcher (D2, D7, D9).
4. `workspace-commit-helper.exe` exists, passes `WinVerifyTrust` with the pinned CN, spawns, and answers `Ping` + `ROOT_IDENTITY` — `native-workspace-commit-helper.ts:172-189`, `:249`; `workspace-production-authority.ts:128-134`.
5. `RUNTIME_ENABLE_DESKTOP_FILESYSTEM` is explicitly truthy — `feature-gate.ts:14-29`, gated at `main/index.ts:641` and `:673`. Spine D3.

FS-03 changes gate 3 and nothing else about the composition.

### D12. `main/index.ts` selects the probe by platform, and the selection is the only `"win32"` literal added

```ts
// main/index.ts, replacing :640-671
let workspaceConfinement: WorkspaceChildConfinement | null = null;
if (isDesktopFilesystemEnabled(process.env) && app.isPackaged) {
  try {
    const runtimePaths = resolveRuntimePaths({ … });   // unchanged
    const shared = { runtimeRoot, webDir, childDataDirs, temporaryDir,
                     pythonBin, serviceDirs };          // unchanged inputs
    workspaceConfinement =
      process.platform === "win32"
        ? new WindowsWorkspaceConfinement({
            ...shared,
            launcherPath: resolveWorkspaceConfinePath({
              packaged: true,
              resourcesPath: process.resourcesPath,
              appPath: process.cwd(),
            }),
            canaryDir,
            forbiddenCanaryDir,
          })
        : new MacosWorkspaceConfinement({ ...shared, canaryDir, forbiddenCanaryDir });
  } catch (err) { /* unchanged: log, leave null */ }
}

// D7: these are two DIFFERENT directories on both platforms. canaryDir must be
// inside the policy's write list; forbiddenCanaryDir must be outside every
// allowed subpath. Same value for both = the positive control fails on a
// correctly confined child.
const canaryDir = join(temporaryDir, CANARY_DIR_NAME);
const forbiddenCanaryDir = join(app.getPath("userData"), ...CANARY_ROOT);
```

`temporaryDir` currently reads `process.env.TMPDIR ?? "/tmp"` (`main/index.ts:658`). On Windows that resolves to the literal `/tmp`, which is not a path. It becomes `app.getPath("temp")` on every platform — a main-owned value that is correct on both and removes an environment-derived path from a security policy.

The `childDataDirs` list (`:654-657`) is unchanged and remains the _only_ part of userData a supervised child may write.

### D13. CI: a Windows leg that can actually fail

`ci-desktop.yml` gains a `windows-latest` job. It cannot run the packaged-app probe (no signed build, no installed runtime), so it must be honest about what it does cover:

```yaml
confinement-windows:
  runs-on: windows-latest
  steps: [
      checkout,
      setup-node 22,
      npm ci,
      node apps/desktop/native/workspace-confine/build.mjs,
      npx vitest run main/services/windows-workspace-confinement.test.ts
      main/capabilities/path-validation.test.ts,
      node apps/desktop/native/workspace-confine/tools/probe-selftest.mjs,
    ]
```

`probe-selftest.mjs` is the piece with teeth: it creates two canary directories, runs the freshly built `copilot-confine.exe --self-test` directly (no signature check, `packaged: false`), and asserts `probe-allow=open` **and** `probe-deny=denied`. If the chosen mechanism does not work on the runner, the job fails rather than skipping — a skip here would let the whole PRD ship untested. If the runner genuinely cannot host the mechanism (e.g. AppContainer profile creation is unavailable in the CI image), that is SPIKE-C1 output and the job asserts the _named_ `unavailableReason` instead, which is still a failing-if-it-changes assertion.

The existing `ubuntu-latest` job is unchanged.

### D14. What FS-03 does not do, and why each omission is deliberate

- **No verb.** `parse_entry`'s refusal of `REPLACE`/`DELETE`/`MOVE` (`workspace_commit_helper.c:801`) is untouched. FS-03 adds confinement, not capability.
- **No protocol change.** `PROTOCOL` stays 2, `JOURNAL_VERSION` stays 3, and no request, operation, outcome or failure enum moves.
- **No claim field.** C6.
- **No relaxation to make Windows work.** If the mechanism is unavailable, the answer is `"unavailable"`, not a smaller boundary.

## Implementation plan

**Step 0 — run SPIKE-C1 before writing the launcher.** It decides whether there is a Windows mechanism at all, and therefore whether steps 4-6 have a target. SPIKE-C3 and SPIKE-C4 can run in parallel; they only affect the C bodies.

1. **`apps/desktop/main/services/workspace-child-confinement.ts` (new).** `ConfinedCommand`, `WorkspaceConfinementEvidence`, `WorkspaceChildConfinement` per §1. Move `ConfinedCommand` here and re-export it from `macos-workspace-confinement.ts` so no import site breaks in the same commit.

2. **`apps/desktop/main/services/desktop-supervisor.ts` (edit).** Delete the `MacosWorkspaceConfinement` import (`:48`); retype `:102` to `WorkspaceChildConfinement`. No logic change at `:335-360`.

3. **`apps/desktop/main/services/macos-workspace-confinement.ts` (edit).** `implements WorkspaceChildConfinement`; add `canaryDir`/`forbiddenCanaryDir` to the config; rewrite `verify()` (`:88-97`) as parse-check → positive control → negative control, each through the existing `runSelfTest` seam, storing `WorkspaceConfinementEvidence`; add `evidence()`. `wrap()`, `spawnFor`, `noteHealthy`, `healthyServices` and `buildMacosWorkspaceSeatbeltProfile` are untouched.

4. **`apps/desktop/native/workspace-confine/` (new).** `build.mjs` (builder table, win32 branch resolving `cl.exe` through `vswhere` exactly as FS-02 D13 specifies, sentinel elsewhere); `src/confine_main.c` (argv, no shell, no env reads, `SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32)` + `SetDllDirectoryW(L"")` as the first statements, per FS-02 D14); `src/confine_policy.c` (the SPIKE-C1 mechanism + low-IL defence in depth + the Job object); `src/confine_probe.c` (`--probe-child`); `README.md`; `.gitignore` for `bin/`.

5. **`apps/desktop/native/workspace-confine/tools/probe-selftest.mjs` (new).** The CI harness of D13.

6. **`apps/desktop/main/services/windows-workspace-confinement.ts` (new).** Per §2. `verify()` = platform check → `executableExists(launcherPath)` → `verifyLauncher` (packaged only) → write canaries → run `--self-test` → compare reported bytes against what was written → set evidence. `wrap()` prefixes the launcher and the policy argv. `spawnFor` records the name, as macOS does.

7. **`apps/desktop/main/capabilities/native-workspace-commit-helper.ts` (edit).** Add `resolveWorkspaceConfinePath` next to `resolveNativeWorkspaceCommitHelperPath` (`:481-500`) — same packaged/dev branch shape, `.exe` on win32 — or, if FS-01's `HelperPlatformProfile` has landed, put the launcher's `executableName` on the profile. No other change.

8. **`apps/desktop/main/capabilities/workspace-production-authority.ts` (edit).** Build the attestation from the observed `verify()` result and the post-launch helper facts (D10). The two-gate structure at `:83-99` and the `finally` teardown at `:160-168` are unchanged.

9. **`apps/desktop/main/index.ts` (edit).** Platform-selected probe (D12); `temporaryDir` → `app.getPath("temp")`; create the canary directories with mode `0o700` before constructing the probe.

10. **`apps/desktop/main/capabilities/path-validation.ts` (edit).** `WINDOWS_RESERVED` += `com0 lpt0 conin$ conout$`; new `assertAbsoluteHostRoot` per §6.

11. **`apps/desktop/main/capabilities/workspace-authority.ts` (edit).** Call `assertAbsoluteHostRoot` where a grant root enters the write path; extend the `WorkspaceWriteAttestation` doc comment (`:39-48`) with D10's table. No signature change.

12. **`apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c` (edit).** `name_is_windows_safe` + call it from `path_is_safe`; `root_path_is_safe` + call it in `command_root_identity` (`:837-843`) and `command_prepare` (`:845+`) before `open_root`.

13. **`apps/desktop/native/workspace-commit-helper/src/fs_platform_win32.c` (edit; FS-02's file).** `fs_open_root` and `fs_open_dir_at` to §4 and D5.

14. **`apps/desktop/native/workspace-fs/src/workspace_fs.c` (edit).** `MB_ERR_INVALID_CHARS` at `:285-286`; `OBJ_DONT_REPARSE` and drop `OBJ_CASE_INSENSITIVE` at `:248`; a comment on `host-fs.ts`'s realpath-first invariant.

15. **`apps/desktop/main/capabilities/host-fs.ts` (edit).** `#openAtomicNative` (`:922-926`) passes `relative(target.rootReal, target.targetReal)`, posixified, instead of `target.relPosix`; a comment at `:912-921` states the invariant and why the no-recheck decision depends on it (D5). `ResolvedTarget.relPosix` (`:252`) keeps its current meaning and its other consumers are untouched.

16. **`apps/desktop/electron-builder.yml` (edit).** The `workspace-confine` `extraResources` entry of §7.

17. **`apps/desktop/build/sign-nested-win.js` (edit; FS-02's file).** Sign the launcher too.

18. **`.github/workflows/ci-desktop.yml` (edit).** The `confinement-windows` job of D13.

19. **`docs/plan/filesystem-capability/README.md` (edit).** The correction is **already folded into the spine** — its Windows "Confined read" cell reads "⚠️ source only" and cites this PRD's C3. Do not re-apply it. FS-03 flips that cell to ✅ only when step 14 **plus** the addon's build and packaging land, and the addon build is explicitly out of scope here, so this step is expected to be a no-op for FS-03's own merge.

## Test plan

### T1 — the probe contract, both platforms (unit, runs on every CI platform)

1. `verify()` returns `"enforced"` only when the parse check, the positive control and the negative control all pass; each of the three failing independently yields `"unavailable"` with `unavailableReason` equal to `probe_error`, `positive_control_failed`, `negative_control_failed` respectively.
2. A `runSelfTest` seam that reports the negative canary as **readable** yields `"unavailable"`, and `wrap()` then throws — i.e. an unconfined child cannot be launched by a probe that observed a leak.
3. The reported canary bytes must equal what main wrote: a seam returning a _stale_ `probe-allow` payload (last boot's random bytes) fails the positive control.
4. `evidence()` is stable across repeated `verify()` calls and never contains a host path.
5. `MacosWorkspaceConfinement` with `platform: "win32"` still returns `"unavailable"` (`:57-59` preserved), and `WindowsWorkspaceConfinement` with `platform: "darwin"` returns `"unavailable"` with `platform_unsupported`.
   5a. `canaryDir !== forbiddenCanaryDir` for both probes as constructed by `main/index.ts`, and a probe constructed with the two equal throws at construction rather than reporting `"unavailable"` at `verify()` — a misconfiguration must not be indistinguishable from a confinement failure (D7).

### T2 — the macOS profile still denies what it claims (macOS runner)

6. Against the **real** `sandbox-exec`: `head -c1` of a file in `temporaryDir` exits 0; of a file in `userData/capabilities/workspace-v2/canary/` exits non-zero. This is the assertion W1 was missing and is the reason the probe changed.
7. A deliberately widened profile (a test-only variant adding `(allow file-read* (subpath "/"))`) makes `verify()` return `"unavailable"` — proving the negative control has teeth rather than always passing.
8. The 15 tests in `native-workspace-commit-helper.test.ts` and every test in `workspace-production-authority.test.ts` still pass, with the latter's four `it.each` fail-closed cases unchanged.

### T3 — the name grammar (unit, every platform)

9. `path_is_safe` (exercised through a prepare) rejects each of `NUL`, `nul`, `CON.txt`, `com1`, `COM0`, `lpt0`, `conin$`, `foo.`, a 256-UTF-16-unit segment; and still accepts `plan.md`, `a-b_c.1`, `notes/plan.md`. Each rejection is `workspace_conflict` and creates nothing.
10. `normalizeVirtualPath` rejects `com0`, `lpt0`, `conin$`, `conout$` (the C4 gap) and continues to reject `COM¹.txt` via the NFKC pass — asserted explicitly so the fold is a pinned behaviour, not an accident.
11. `assertAbsoluteHostRoot` accepts `/Users/a/w` on darwin and `C:\Users\a\w` on win32; rejects, per platform, `C:w`, `C:`, `\Users\a`, `\\srv\share`, `\\?\C:\a`, `\\.\C:`, `C:/Users/a`, `C:\Users\a\`, `C:\a\..\b`, `C:\a:b`, and a path whose any segment fails `assertSegmentSafe`.
12. Every rejection message contains no host path (the path-oracle rule, `path-validation.ts:41-53`).

### T4 — Win32 confined open (Windows runner; several require FS-02's helper)

13. **Case-folded parent refused.** Prepare `create` at `notes/plan.md` where the real directory is `Notes` → `workspace_conflict`; nothing created under `Notes`.
14. **8.3 alias refused.** Create `Program Folder\`, then request a create whose parent segment is the generated short name → refusal. Skip **with an explicit stated reason** if `fsutil 8dot3name query` reports generation disabled on the runner volume; a silent pass is not acceptable.
15. **Junction at an intermediate component refused.** `symlinkSync(target, path, "junction")` (no privilege required, unlike a Windows symlink) on a walked directory → refusal, and the walk does not open the target.
16. **Junction at the leaf refused.** Nothing is written through it and nothing appears at its target.
17. **Cross-volume component refused.** A mount point (a reparse point) at a walked component → refusal; and a volume-serial mismatch, if constructible, is refused independently.
18. **`OBJ_DONT_REPARSE` vs `FILE_OPEN_REPARSE_POINT`.** A unit test in the launcher/helper harness records which of the two produced the refusal, so SPIKE-C4's answer is captured in CI rather than in a doc.
19. **Root grammar at the wire.** `ROOT_IDENTITY` and `PREPARE` with each rejected root form of T3.11 fail with `UNSUPPORTED`/`INVALID` before any handle is opened.
20. **Invalid UTF-8 root fails closed.** A root byte string that is not valid UTF-8 is refused, not silently U+FFFD-substituted (the `workspace_fs.c:285` defect, asserted on the write path too).
21. **Share mode.** While a prepare is outstanding, an external `rmdir`/rename of a walked parent fails; `abort` releases it. Asserted so a future change to the share flags is caught (D5.5).

### T5 — the launcher (Windows runner)

22. `probe-selftest.mjs` asserts `probe-allow=open` and `probe-deny=denied` against the freshly built launcher.
23. **Passthrough detection.** A stub launcher that spawns without applying any policy makes `verify()` return `"unavailable"` with `negative_control_failed` — the D9 control that does not depend on a certificate.
24. **Signature.** With `packaged: true` and a `verifyLauncher` seam returning `false`, `verify()` returns `"unavailable"` with `launcher_signature_rejected` and the launcher is never spawned.
25. **Lifetime.** Kill the launcher; assert the grandchild is gone within the kill timeout (`python-service.ts:146-149`) — the Job's `KILL_ON_JOB_CLOSE`.
26. **Exit code.** A grandchild exiting 3 makes the launcher exit 3; a policy parse failure exits 10 and writes nothing to stdout.
27. **stdio fidelity.** A grandchild writing 1 MiB with embedded `0x0A`, `0x0D` and `0x1A` bytes round-trips byte-exactly through the launcher (no CRT text-mode translation).
28. **No ambient input.** With `%PATH%` pointing at a directory containing a hostile `python.exe`, the launcher still executes the absolute command it was given.

### T6 — attestation semantics (unit, every platform)

29. `createProductionWorkspaceAuthority` with `confinement.verify()` → `"unavailable"` returns `null` and never launches a helper — unchanged behaviour, re-asserted because D10 moves the attestation construction.
30. With `verify()` → `"enforced"` but `helper.primitivesAvailable === false`, the returned authority is `null` and **no** attestation with `nativeWorkspacePrimitives: "available"` is ever constructed.
31. The published `DesktopWorkspaceAttestationClaims` field set is byte-identical to today's for a healthy install: `canonicalClaimsJson` output is compared against a frozen fixture, and the Python `DesktopWorkspaceAttestationClaims` model parses it with `extra="forbid"`. This is the C6 tripwire.

### T7 — composition gates (unit, every platform)

32. `createProductionWorkspaceAuthority({platform: "win32", packaged: true, production: true, confinement: <MacosWorkspaceConfinement with platform "win32">, …})` → `null`, and `launchHelper` was not called. This pins D1's ordering property: FS-02 landing alone does not open Windows.
33. With a `WindowsWorkspaceConfinement` whose `verify()` resolves `"enforced"` and a stub helper, the authority is constructed — proving gate 3 is the only thing FS-03 changed.
34. `RUNTIME_ENABLE_DESKTOP_FILESYSTEM` unset → no probe is constructed at all (`main/index.ts:641`), asserted through the existing index test harness.

### T8 — read-path honesty

35. On a non-darwin platform with the addon absent, `HostFs` still denies every escape through the Node fallback (existing `host-fs.test.ts` coverage, re-run to confirm step 14 changed nothing there).
36. `evidence().nativeReadConfinement` is `"fallback"` when `loadNativeWorkspaceFs()` returns `undefined` and `"atomic"` when it does not — so the C3 gap is _reported_ even before it is fixed.
37. **The native open is called with a canonical name.** With a fake `native` whose `openBeneath` records its `rel` argument, and a fixture whose `realpath` returns a differently-cased/long-name form of the requested path, assert the recorded `rel` equals `relative(rootReal, targetReal)` and **not** the requested spelling. The pre-change code fails this test; it is the regression pin for D5's read-path correction.
38. Every other consumer of `relPosix` (glob/grep result paths, `:475`, `:558`) still returns the requested spelling — asserted so the fix in T8.37 cannot silently change what the caller sees.

## Open questions and spikes

Each names the API, the exact experiment, and the outcome that changes the design. None can be settled from a macOS host.

**SPIKE-C1 — can an AppContainer child serve and use loopback? (run first; gates D3.)**
_API:_ `CreateAppContainerProfile`, `DeriveAppContainerSidFromAppContainerName`, `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES`, `CreateProcessAsUserW`; Windows Firewall network isolation; `CheckNetIsolation LoopbackExempt`.
_Experiment:_ a minimal launcher creates a per-install AppContainer profile with `internetClient` and `privateNetworkClientServer` capabilities, ACLs the staged runtime tree and `agent-data\v1` for the container SID, and runs the real bundled `python.exe -m uvicorn` bound to `127.0.0.1`. Then, from a medium-IL process, (a) connect to that port, and (b) from inside the container, connect to a medium-IL loopback listener. Record success/failure and `WSAGetLastError` for each direction, without elevation.
_Changes the design if:_ either direction fails → AppContainer is not viable, `verify()` returns `"unavailable"` with `mechanism_unavailable`, and Windows writes stay off pending a named-pipe re-plumb (D3). If both succeed → AppContainer is the mechanism, and the follow-on question is which ACEs the runtime tree needs.

**SPIKE-C2 — does a restricted token survive CPython? (fallback path for D3.)**
_API:_ `CreateRestrictedToken` with `DISABLE_MAX_PRIVILEGE`, deny-only groups, and a restricting SID set.
_Experiment:_ run the bundled `python.exe -c "import ssl, socket, sqlite3, ctypes"` under a token restricted to `{S-1-5-12}` plus a per-install capability SID; record every `LoadLibrary` failure with Process Monitor. Separately, `icacls %SystemRoot%\System32` and record whether `NT AUTHORITY\RESTRICTED` appears.
_Changes the design if:_ System32 is reachable and CPython loads cleanly → a restricted token is a second viable mechanism, possibly preferable to AppContainer because it does not touch network isolation. If not → the option is closed and should be recorded as closed.

**SPIKE-C3 — NT path conversion for the root. (gates D4's conversion step.)**
_API:_ `RtlDosPathNameToNtPathName_U` (ntdll, `GetProcAddress`), vs `CreateFileW` with a `\\?\` prefix.
_Experiment:_ for `C:\ok`, a 300-character path, a path containing a trailing-dot component, and a SUBST'd drive: convert with each method and open with `NtCreateFile`; record which succeed and whether the resulting handle's `FILE_ID_INFO` matches the one obtained via a plain `CreateFileW`.
_Changes the design if:_ the ntdll export is unavailable or misbehaves → use the `\\?\` prefix, which is only sound because D4's grammar has already canonicalised the input; say so in the code comment.

**SPIKE-C4 — `OBJ_DONT_REPARSE` on a single-component, RootDirectory-relative open. (gates D5.1's wording, not its code.)**
_API:_ `OBJECT_ATTRIBUTES.Attributes |= OBJ_DONT_REPARSE` combined with `FILE_OPEN_REPARSE_POINT`.
_Experiment:_ place (a) a directory junction, (b) a file symlink, (c) a mount point at a child name; open each with all four flag combinations and record the `NTSTATUS`.
_Changes the design if:_ `OBJ_DONT_REPARSE` fires (`STATUS_REPARSE_POINT_ENCOUNTERED`) → no handle is ever created on a reparse point and the attribute check is belt-and-braces. If it does not fire with `FILE_OPEN_REPARSE_POINT` set → the attribute check is the _only_ control and the comment must say so rather than implying two.

**Non-spike open questions.**

- **Windows code-signing certificate.** Shared with FS-02 D15. Without it, D9's first control fails and `verify()` returns `"unavailable"` on every packaged Windows install, so FS-02 + FS-03 together still ship a capability nobody can turn on. Product decision.
- **Packaging the read-side addon.** C3 shows `workspace-fs` is neither built nor packaged. Fixing it means building an N-API addon against the Electron ABI in release CI for win32-x64, which is a real piece of work with its own prebuild strategy. FS-03 makes the state _visible_ (`evidence().nativeReadConfinement`) and corrects the README; it does not commit to the build. Whoever picks it up should treat it as its own slice, because a half-shipped `.node` is worse than a documented fallback.
- **Does the AppContainer/restricted SID need ACEs on the _staged_ runtime tree at install time or at first boot?** Install-time is cleaner but the NSIS installer would own a security decision; first-boot is main-owned but must be idempotent and must not fight an antivirus product. Decide once SPIKE-C1 names the SID.
- **ARM64 Windows.** `release-desktop.yml:54-57` builds x64 only; the launcher inherits that.

## Definition of done

- [ ] SPIKE-C1 has run on a real Windows host; D3 names one mechanism (or names `mechanism_unavailable`) with its evidence recorded in this file.
- [ ] SPIKE-C3 and SPIKE-C4 have run and D4/D5's wording matches what was measured.
- [ ] `WorkspaceChildConfinement` exists; `grep -n "MacosWorkspaceConfinement" apps/desktop/main/services/desktop-supervisor.ts` returns nothing.
- [ ] `MacosWorkspaceConfinement.verify()` runs a positive **and** a negative control against the real `sandbox-exec` on macOS, and a deliberately widened profile makes it return `"unavailable"` (T2.7).
- [ ] `WindowsWorkspaceConfinement.verify()` returns `"enforced"` only when a confined child was observed to read the in-boundary canary and observed to fail on the out-of-boundary canary, with the canary bytes minted fresh this boot.
- [ ] A stub passthrough launcher makes `verify()` return `"unavailable"` with `negative_control_failed`, and `wrap()` then throws.
- [ ] A packaged build whose launcher fails Authenticode verification returns `"unavailable"` with `launcher_signature_rejected` and never spawns it.
- [ ] Killing the launcher kills the confined grandchild; a grandchild exit code is forwarded verbatim; 1 MiB of binary stdout round-trips byte-exactly.
- [ ] `name_is_windows_safe` is compiled on both platforms and rejects every entry in T3.9; `grep -c 'defined(_WIN32)' src/workspace_commit_helper.c` is 0 (FS-01's guardrail preserved).
- [ ] `root_path_is_safe` runs before `fs_open_root` in both `command_root_identity` and `command_prepare`, and every form in D4's table is refused with no handle opened.
- [ ] `WINDOWS_RESERVED` contains `com0`, `lpt0`, `conin$`, `conout$`; `assertAbsoluteHostRoot` passes every case in T3.11; no rejection message contains a host path.
- [ ] `fs_open_dir_at` on Win32 refuses, with a distinct test each: a reparse point, a case-folded spelling, an 8.3-alias spelling, a different volume, and a `FILE_SHARE_DELETE`-dependent rename.
- [ ] `workspace_fs.c` sets `MB_ERR_INVALID_CHARS` on both conversions, sets `OBJ_DONT_REPARSE`, and no longer passes `OBJ_CASE_INSENSITIVE`.
- [ ] `canonicalClaimsJson` output for a healthy install is byte-identical to the committed fixture, and the Python `DesktopWorkspaceAttestationClaims` model parses it under `extra="forbid"`.
- [ ] The attestation pair is constructed **after** `verify()` and after the helper's `Ping` + `ROOT_IDENTITY`, and `createProductionWorkspaceAuthority` still returns `null` on every fail-closed path in `workspace-production-authority.test.ts`.
- [ ] `createProductionWorkspaceAuthority({platform:"win32", confinement: <macOS probe>})` returns `null` without calling `launchHelper` (T7.32).
- [ ] `main/index.ts` derives `temporaryDir` from `app.getPath("temp")`; `grep -n '"/tmp"' apps/desktop/main/index.ts` returns nothing.
- [ ] `ci-desktop.yml` has a `windows-latest` job that builds the launcher and asserts `probe-allow=open` + `probe-deny=denied`, and it is required for merge.
- [ ] All 15 tests in `native-workspace-commit-helper.test.ts` and the whole `workspace-production-authority.test.ts` suite pass on macOS.
- [ ] `README.md`'s Windows "Confined read" cell states the C3 truth (source only, not built, not packaged) until the addon is actually shipped.
- [ ] `evidence().nativeReadConfinement` reports `"fallback"` on a packaged Windows build today, and the value is surfaced in the boot log.

## Out of scope

- Any new verb: `replace`, `delete`, `move` (FS-05/FS-06), preimage and trash (FS-04), post-crash reconciliation (FS-07). `parse_entry:801` is untouched.
- The Windows commit effect itself — `fs_commit_create`, `fs_commit_mkdir`, staging, journalling, BCrypt, capability delivery, `workspace-commit-helper.exe`'s build/sign/package path, and the `win32` entry in `HELPER_PLATFORM_PROFILES`. All FS-02.
- Building and packaging the `workspace-fs` N-API addon for Windows. FS-03 corrects the claim and reports the state; shipping the binary is its own slice (see Open questions).
- Re-plumbing the loopback service topology onto named pipes to make AppContainer viable.
- Any change to the signed attestation wire format, the request/operation/outcome enums, `PROTOCOL`, `JOURNAL_VERSION`, or the darwin root-identity string format.
- User-facing enablement and consent copy, including telling a Windows user _why_ writes are unavailable. That is FS-09; FS-03 only supplies the machine-readable `unavailableReason`.
- Procuring the Authenticode certificate.
- Linux confinement. Nothing here registers a Linux platform, and `helperPlatformProfile("linux")` stays `undefined`.
- ARM64 Windows.

## Guardrails

- Do **not** report `"enforced"` on the strength of an API returning success. Both canary controls must have been observed this boot.
- Do **not** substitute a weaker mechanism under the same word. Low integrity level is write-confinement only; it is defence in depth, never the mechanism.
- Do **not** weaken confinement to make a verb work. If the Windows mechanism is unavailable, Windows writes stay off.
- Do **not** add a field to `DesktopWorkspaceAttestationClaims`. The Python verifier is `extra="forbid"` and would reject the envelope.
- Do **not** add a second write path. FS-03 adds no filesystem mutation anywhere; the launcher spawns processes and the probe writes only main-owned canaries under `userData`.
- Do **not** let the model choose a path. Nothing here introduces a new source of paths; the root grammar only narrows what a user-issued grant may spell.
- Do **not** implement a rule on one platform only. `name_is_windows_safe` and the canary probe compile and run on both.
- Do **not** resolve the launcher, the compiler, or the confined command through `PATH` or `%PATH%`.
- Do **not** modify the security descriptor, ACL, or integrity label of any user file or granted folder.
- Do **not** rely on `OBJ_CASE_INSENSITIVE`'s absence as a case-sensitivity guarantee; the directory enumeration is the authoritative check.
- Do **not** call the native read path with a relative path that was not derived from a `realpath`'d target — `#openAtomicNative` performs no post-open recheck.
- Do **not** assert Win32 semantics a spike has not confirmed. Every claim marked _unverified_ in this PRD stays marked until a Windows host says otherwise.
