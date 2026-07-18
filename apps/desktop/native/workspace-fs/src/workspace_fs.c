/*
 * workspace-fs — native N-API helper for TOCTOU-safe, root-confined file opens.
 *
 * The capability broker (main/capabilities/host-fs.ts) must open an untrusted,
 * agent-supplied path that is *proven* to live beneath a grant root WITHOUT
 * being rac_ed by a mid-flight symlink/junction swap of any intermediate path
 * component. macOS already gets this atomically from `O_NOFOLLOW_ANY`, so the
 * pure-Node path is atomic there. Linux and Windows do NOT: `O_NOFOLLOW` only
 * guards the final component, so the Node path falls back to a *non-atomic*
 * post-open realpath recheck. This addon closes that residual with the kernel's
 * own handle-relative, reparse-refusing open primitives:
 *
 *   - Linux   : openat2(2) with RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS |
 *               RESOLVE_NO_MAGICLINKS. The kernel walks the path relative to the
 *               root dir fd and fails (EXDEV / ELOOP) the instant any component
 *               would escape the root or is a symlink — atomically, inside the
 *               single syscall. This is the Linux analogue of O_NOFOLLOW_ANY.
 *   - macOS   : openat(2) relative to the root dir fd with O_NOFOLLOW_ANY (a
 *               symlink in ANY component => ELOOP). Provided for completeness;
 *               host-fs keeps using the equally-atomic pure-Node darwin path.
 *   - Windows : NtCreateFile walked ONE component at a time relative to the
 *               parent handle with FILE_OPEN_REPARSE_POINT, refusing any
 *               intermediate reparse point (junction / symlink). RootDirectory-
 *               relative names cannot contain separators or "..", so the walk
 *               cannot escape. The final handle is converted to a CRT fd so the
 *               Node side can fstat/read/close it like any other descriptor.
 *
 * The single exported primitive is:
 *   openBeneath(root: string, rel: string, directory: bool, write: bool) -> fd
 * It returns an OS file descriptor (an integer usable by node:fs) on success,
 * or throws an Error whose `.code` is the POSIX-style errno name
 * (ELOOP / EXDEV / ENOENT / ENOTDIR / EISDIR / EACCES / EPERM / ENOSYS / EIO)
 * so host-fs can map it to a stable FsError. ENOSYS signals "primitive not
 * available on this kernel" and makes host-fs fall back to the Node recheck.
 */

#include <node_api.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* ------------------------------------------------------------------ *
 * Per-platform openBeneath. Each returns an OS fd (>= 0) or -1 with
 * *code set to a static errno-name string.
 * ------------------------------------------------------------------ */

#if defined(__linux__)

#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <sys/syscall.h>

/* openat2 is syscall 437 on every Linux architecture. */
#ifndef __NR_openat2
#define __NR_openat2 437
#endif

/* Resolve flags (from linux/openat2.h) — defined defensively so the addon
 * builds against older UAPI headers. The kernel ignores unknown resolve bits
 * only by rejecting them, so we set exactly the three we rely on. */
#ifndef RESOLVE_NO_MAGICLINKS
#define RESOLVE_NO_MAGICLINKS 0x02
#endif
#ifndef RESOLVE_NO_SYMLINKS
#define RESOLVE_NO_SYMLINKS 0x04
#endif
#ifndef RESOLVE_BENEATH
#define RESOLVE_BENEATH 0x08
#endif

struct wfs_open_how {
  uint64_t flags;
  uint64_t mode;
  uint64_t resolve;
};

static const char *errno_code(int e) {
  switch (e) {
    case ELOOP: return "ELOOP";
    case EXDEV: return "EXDEV";
    case ENOENT: return "ENOENT";
    case ENOTDIR: return "ENOTDIR";
    case EISDIR: return "EISDIR";
    case EACCES: return "EACCES";
    case EPERM: return "EPERM";
    case ENOSYS: return "ENOSYS";
    default: return "EIO";
  }
}

static int wfs_open_beneath(const char *root, const char *rel, int directory,
                            int write, const char **code) {
  int rootfd = open(root, O_PATH | O_DIRECTORY | O_CLOEXEC);
  if (rootfd < 0) {
    *code = errno_code(errno);
    return -1;
  }
  struct wfs_open_how how;
  memset(&how, 0, sizeof how);
  how.flags = (uint64_t)(O_CLOEXEC | (write ? O_RDWR : O_RDONLY) |
                         (directory ? O_DIRECTORY : 0));
  how.resolve = RESOLVE_NO_SYMLINKS | RESOLVE_BENEATH | RESOLVE_NO_MAGICLINKS;
  /* "" denotes the root itself — walk to "." (still beneath the root). */
  const char *path = (rel && rel[0]) ? rel : ".";
  long fd = syscall(__NR_openat2, rootfd, path, &how, sizeof how);
  int e = errno;
  close(rootfd);
  if (fd < 0) {
    *code = errno_code(e);
    return -1;
  }
  return (int)fd;
}

#elif defined(__APPLE__)

#include <fcntl.h>
#include <unistd.h>
#include <errno.h>

/* macOS 10.15+: refuse a symlink in ANY component atomically during the walk. */
#ifndef O_NOFOLLOW_ANY
#define O_NOFOLLOW_ANY 0x20000000
#endif

static const char *errno_code(int e) {
  switch (e) {
    case ELOOP: return "ELOOP";
    case ENOENT: return "ENOENT";
    case ENOTDIR: return "ENOTDIR";
    case EISDIR: return "EISDIR";
    case EACCES: return "EACCES";
    case EPERM: return "EPERM";
    case ENOSYS: return "ENOSYS";
    default: return "EIO";
  }
}

static int wfs_open_beneath(const char *root, const char *rel, int directory,
                            int write, const char **code) {
  int rootfd = open(root, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  if (rootfd < 0) {
    *code = errno_code(errno);
    return -1;
  }
  int flags = O_CLOEXEC | O_NOFOLLOW_ANY | (write ? O_RDWR : O_RDONLY) |
              (directory ? O_DIRECTORY : 0);
  /* The caller always passes a normalized, ".."-free rel (host-fs guarantees
   * it), so a relative open confined by O_NOFOLLOW_ANY cannot escape: the only
   * escape vector — a symlink component — is refused with ELOOP. */
  const char *path = (rel && rel[0]) ? rel : ".";
  int fd = openat(rootfd, path, flags);
  int e = errno;
  close(rootfd);
  if (fd < 0) {
    *code = errno_code(e);
    return -1;
  }
  return fd;
}

#elif defined(_WIN32)

/*
 * Windows reparse-safe walk. UNTESTED ON A WINDOWS HOST in this environment —
 * implemented against the documented NtCreateFile contract and flagged as a
 * packaging/validation follow-up. The design mirrors openat2(RESOLVE_BENEATH):
 * open the root, then resolve one component at a time RELATIVE to the parent
 * handle with FILE_OPEN_REPARSE_POINT, refusing any intermediate that is a
 * reparse point (junction / symlink). RootDirectory-relative object names
 * cannot contain "\\" or "..", so the walk is confined to the subtree.
 */

#include <windows.h>
#include <io.h>
#include <fcntl.h>
#include <winternl.h>

#ifndef FILE_OPEN
#define FILE_OPEN 0x00000001
#endif
#ifndef FILE_DIRECTORY_FILE
#define FILE_DIRECTORY_FILE 0x00000001
#endif
#ifndef FILE_OPEN_REPARSE_POINT
#define FILE_OPEN_REPARSE_POINT 0x00200000
#endif
#ifndef FILE_SYNCHRONOUS_IO_NONALERT
#define FILE_SYNCHRONOUS_IO_NONALERT 0x00000020
#endif
#ifndef OBJ_CASE_INSENSITIVE
#define OBJ_CASE_INSENSITIVE 0x00000040
#endif
#ifndef STATUS_SUCCESS
#define STATUS_SUCCESS ((NTSTATUS)0x00000000L)
#endif

typedef NTSTATUS(NTAPI *PFN_NtCreateFile)(
    PHANDLE, ACCESS_MASK, POBJECT_ATTRIBUTES, PIO_STATUS_BLOCK, PLARGE_INTEGER,
    ULONG, ULONG, ULONG, ULONG, PVOID, ULONG);

static const char *win_status_code(NTSTATUS s) {
  switch ((ULONG)s) {
    case 0xC0000035: return "EEXIST";       /* OBJECT_NAME_COLLISION  */
    case 0xC0000034: return "ENOENT";       /* OBJECT_NAME_NOT_FOUND  */
    case 0xC000003A: return "ENOENT";       /* OBJECT_PATH_NOT_FOUND  */
    case 0xC0000022: return "EACCES";       /* ACCESS_DENIED          */
    case 0xC0000280: return "ELOOP";        /* REPARSE_POINT_ENCOUNTERED */
    default: return "EIO";
  }
}

/* Open one component relative to `parent`. On success returns a HANDLE via
 * *out and STATUS_SUCCESS; refuses to traverse a reparse point. */
static NTSTATUS open_component(PFN_NtCreateFile NtCreateFile_, HANDLE parent,
                              const wchar_t *name, int as_dir, int write,
                              int is_final, HANDLE *out) {
  UNICODE_STRING us;
  us.Length = (USHORT)(wcslen(name) * sizeof(wchar_t));
  us.MaximumLength = us.Length;
  us.Buffer = (PWSTR)name;

  OBJECT_ATTRIBUTES oa;
  InitializeObjectAttributes(&oa, &us, OBJ_CASE_INSENSITIVE, parent, NULL);

  IO_STATUS_BLOCK iosb;
  HANDLE h = NULL;
  ACCESS_MASK access = (write && is_final)
                           ? (FILE_GENERIC_READ | FILE_GENERIC_WRITE)
                           : FILE_GENERIC_READ;
  ULONG opts = FILE_SYNCHRONOUS_IO_NONALERT | FILE_OPEN_REPARSE_POINT |
               (as_dir ? FILE_DIRECTORY_FILE : 0);
  NTSTATUS st = NtCreateFile_(&h, access | SYNCHRONIZE, &oa, &iosb, NULL,
                              FILE_ATTRIBUTE_NORMAL,
                              FILE_SHARE_READ | FILE_SHARE_WRITE, FILE_OPEN,
                              opts, NULL, 0);
  if (st != STATUS_SUCCESS) return st;

  /* Refuse a reparse point anywhere along the path (junction / symlink). */
  BY_HANDLE_FILE_INFORMATION fi;
  if (GetFileInformationByHandle(h, &fi) &&
      (fi.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) {
    CloseHandle(h);
    return (NTSTATUS)0xC0000280; /* treat as ELOOP */
  }
  *out = h;
  return STATUS_SUCCESS;
}

static int wfs_open_beneath(const char *root, const char *rel, int directory,
                            int write, const char **code) {
  PFN_NtCreateFile NtCreateFile_ =
      (PFN_NtCreateFile)GetProcAddress(GetModuleHandleW(L"ntdll.dll"),
                                       "NtCreateFile");
  if (!NtCreateFile_) {
    *code = "ENOSYS";
    return -1;
  }

  /* Widen root + rel (UTF-8 -> UTF-16). */
  int rootw_len = MultiByteToWideChar(CP_UTF8, 0, root, -1, NULL, 0);
  int relw_len = MultiByteToWideChar(CP_UTF8, 0, rel, -1, NULL, 0);
  wchar_t *rootw = (wchar_t *)malloc((size_t)rootw_len * sizeof(wchar_t));
  wchar_t *relw = (wchar_t *)malloc((size_t)relw_len * sizeof(wchar_t));
  if (!rootw || !relw) {
    free(rootw);
    free(relw);
    *code = "EIO";
    return -1;
  }
  MultiByteToWideChar(CP_UTF8, 0, root, -1, rootw, rootw_len);
  MultiByteToWideChar(CP_UTF8, 0, rel, -1, relw, relw_len);

  HANDLE parent =
      CreateFileW(rootw, FILE_LIST_DIRECTORY | GENERIC_READ,
                  FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING,
                  FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
                  NULL);
  free(rootw);
  if (parent == INVALID_HANDLE_VALUE) {
    free(relw);
    *code = "ENOENT";
    return -1;
  }

  /* Walk rel one '/'-separated component at a time. host-fs never passes ".."
   * or absolute segments, so each name is a single safe child. */
  const char *result_code = NULL;
  int fd = -1;
  HANDLE cur = parent;
  if (relw[0] != L'\0') {
    wchar_t *save = NULL;
    /* Tokenize on both separators, though host-fs sends POSIX '/'. */
    for (wchar_t *tok = wcstok_s(relw, L"/\\", &save); tok != NULL;
         tok = wcstok_s(NULL, L"/\\", &save)) {
      int is_final = (*save == L'\0');
      int as_dir = is_final ? directory : 1;
      HANDLE next = NULL;
      NTSTATUS st = open_component(NtCreateFile_, cur, tok, as_dir, write,
                                   is_final, &next);
      CloseHandle(cur);
      if (st != STATUS_SUCCESS) {
        result_code = win_status_code(st);
        cur = NULL;
        break;
      }
      cur = next;
    }
  }
  free(relw);

  if (cur != NULL) {
    /* Hand the final HANDLE to the CRT as an fd node:fs can use. */
    fd = _open_osfhandle((intptr_t)cur, write ? 0 : _O_RDONLY);
    if (fd < 0) {
      CloseHandle(cur);
      *code = "EIO";
      return -1;
    }
    return fd;
  }
  *code = result_code ? result_code : "ENOENT";
  return -1;
}

#else /* unsupported platform */

static const char *errno_code(int e) {
  (void)e;
  return "ENOSYS";
}

static int wfs_open_beneath(const char *root, const char *rel, int directory,
                            int write, const char **code) {
  (void)root;
  (void)rel;
  (void)directory;
  (void)write;
  *code = "ENOSYS";
  return -1;
}

#endif

/* ------------------------------------------------------------------ *
 * N-API glue.
 * ------------------------------------------------------------------ */

static char *get_string(napi_env env, napi_value v) {
  size_t len = 0;
  if (napi_get_value_string_utf8(env, v, NULL, 0, &len) != napi_ok) return NULL;
  char *buf = (char *)malloc(len + 1);
  if (!buf) return NULL;
  size_t written = 0;
  if (napi_get_value_string_utf8(env, v, buf, len + 1, &written) != napi_ok) {
    free(buf);
    return NULL;
  }
  return buf;
}

static int get_bool(napi_env env, napi_value v) {
  bool b = false;
  napi_get_value_bool(env, v, &b);
  return b ? 1 : 0;
}

static napi_value OpenBeneath(napi_env env, napi_callback_info info) {
  size_t argc = 4;
  napi_value argv[4];
  napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
  if (argc < 3) {
    napi_throw_error(env, "EINVAL",
                     "openBeneath(root, rel, directory[, write])");
    return NULL;
  }
  char *root = get_string(env, argv[0]);
  char *rel = get_string(env, argv[1]);
  int directory = get_bool(env, argv[2]);
  int write = (argc >= 4) ? get_bool(env, argv[3]) : 0;
  if (!root || !rel) {
    free(root);
    free(rel);
    napi_throw_error(env, "EINVAL", "root and rel must be strings");
    return NULL;
  }

  const char *code = "EIO";
  int fd = wfs_open_beneath(root, rel, directory, write, &code);
  free(root);
  free(rel);

  if (fd < 0) {
    napi_throw_error(env, code, "openBeneath failed");
    return NULL;
  }
  napi_value result;
  napi_create_int32(env, fd, &result);
  return result;
}

static napi_value Init(napi_env env, napi_value exports) {
  napi_value fn;
  napi_create_function(env, "openBeneath", NAPI_AUTO_LENGTH, OpenBeneath, NULL,
                       &fn);
  napi_set_named_property(env, exports, "openBeneath", fn);
  return exports;
}

NAPI_MODULE(NODE_GYP_MODULE_NAME, Init)
