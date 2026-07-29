# Spike results — measured, not asserted

Several PRD claims were marked "unverified — spike required". These are the ones
runnable on a macOS host. Each was **executed**, not reasoned about.

**Host:** macOS 15.6.1, arm64, APFS root volume. **Baseline:** `main@6d515523`.
**Toolchain:** Apple `cc`, SDK `MacOSX.sdk` at `/Library/Developer/CommandLineTools`.

Windows spikes are **not** covered here — they need a Windows host. The decisive
one (can a Windows container runtime observe all ten isolation controls) remains
open and still gates FS-08.

---

## SPIKE-A — `renameatx_np(RENAME_SWAP)` — ✅ CONFIRMED

**Owner:** FS-06. **Question:** the macOS helper refuses `replace` because there
is no kernel compare-and-swap rename bound to inode+digest. FS-06 claims the same
_outcome_ is reachable via swap → verify-the-displaced-inode → swap back. Does the
primitive support that?

**Result — all five assertions passed:**

| Assertion                                                  | Result |
| ---------------------------------------------------------- | ------ |
| `renameatx_np(RENAME_SWAP)` returns 0 on APFS              | ✅     |
| the target name holds the replacement bytes after the swap | ✅     |
| **an fd opened BEFORE the swap still reads the ORIGINAL**  | ✅     |
| **that fd's inode is unchanged by the swap**               | ✅     |
| swapping back restores the original                        | ✅     |
| `RENAME_EXCL` refuses an occupied destination (`EEXIST`)   | ✅     |
| `RENAME_SWAP` fails `ENOENT` when the source is absent     | ✅     |

The two bold rows are the load-bearing ones. Because the swap exchanges
_directory entries_ and an open fd is bound to the _inode_, the displaced content
stays readable and identifiable through a handle retained across the swap. That is
what makes "act, then verify what you displaced, then roll back" implementable.

**Availability is not a constraint.** `renameatx_np` is `__OSX_AVAILABLE(10.12)`
(`MacOSX.sdk/usr/include/sys/stdio.h`). Its `__DARWIN_C_LEVEL >= __DARWIN_C_FULL`
guard looked like a problem for a `-std=c11` build, but a minimal program using
only `<stdio.h>`, `<fcntl.h>` and `<unistd.h>` — all already included by the
helper — compiles under `-std=c11 -Werror=implicit-function-declaration` with no
diagnostic. **No `_DARWIN_C_SOURCE` define is needed.**

**What this spike does NOT prove.** It establishes availability and semantics, not
the absence of a race. A window remains between the swap and the verification, and
between the verification and a rollback. The spine's guarantee is worded for
exactly that — _act atomically where the platform allows, verify what was
displaced, roll back or retain the preimage_ — and this result supports that
wording, not a stronger one. Anyone tempted to upgrade FS-06's language to
"compare-and-swap" on the strength of this file should not.

**A caution on method.** The first run of this spike reported a FAILURE on the
load-bearing assertion. That was a defect in the spike, not a finding: the fd was
opened `O_WRONLY`, so `read()` returned `EBADF` and the helper left the previous
call's bytes in the buffer, which the assertion then compared. The corrected spike
zeroes the buffer and surfaces read errors as text. A spike that can silently
compare stale memory can also silently pass.

## SPIKE-B — `journal_record` struct layout — ✅ CONFIRMED EXACT

**Owner:** FS-01. FS-01 hand-derived `sizeof == 358` and `offsetof(mac) == 325`
for its two `_Static_assert`s and flagged them "the compiler is the authority".

Measured on this host, with the constants read from the source
(`KEY_BYTES 32`, `MAC_BYTES 32`, `MAX_CLAIM_BYTES 160`, `MAX_STAGE_DIR_BYTES 48`):

```
sizeof(struct journal_record) = 358   (asserted 358)  ✅
offsetof(mac)                 = 325   (asserted 325)  ✅
tail padding                  = 1
```

Both correct. The one byte of tail padding is worth knowing: this struct is an
**on-disk format**, so if a future compiler or ABI disagrees, the fix is to use
the compiler's numbers — never to "tidy" the struct.

## SPIKE-C — the helper builds on this host — ✅ CONFIRMED

**Owner:** FS-01 (its DoD needs a known-good baseline before the seam refactor).

`node build.mjs` produces `bin/workspace-commit-helper`, a 70,016-byte
`Mach-O 64-bit executable arm64`, mode `0500`. The build path works, so FS-01's
golden-transcript proof — record from the pre-seam binary, replay after — has a
binary to record from. `bin/` is gitignored, so nothing is committed by building.

---

## Consequences for the program

**FS-06 is unblocked and its sizing holds.** The mechanism it specifies is real on
this platform. Its remaining cost is the rollback path and the preimage, not
uncertainty about the primitive.

**FS-01's assertions are safe to write as `_Static_assert`.** They will compile.

**Nothing here touches Windows.** FS-02, FS-03 and the FS-08 runtime question are
untouched by these results, and FS-08's Windows spike still decides whether the
local-sandbox decision survives — verbs land on both platforms or neither.

## Reproducing

Sources are in the session scratchpad (`spike_rename_swap.c`, `offsets.c`). They
are deliberately not committed: they are throwaway probes, and a committed probe
that nobody runs is documentation, not a control. If these need to become
standing checks, they belong in the native test suite with the CI job FS-01 D15
asks for — today **zero CI jobs execute a single line of the helper**.
