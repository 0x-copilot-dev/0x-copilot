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
#include <stdio.h>

/* N-API string helpers are implemented in the generic glue below.  The
 * Darwin v2 bridge is defined before that glue so declare them explicitly. */
static char *get_string(napi_env env, napi_value v);
static int get_bool(napi_env env, napi_value v);

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
#include <sys/stat.h>
#include <CommonCrypto/CommonDigest.h>

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
 * Darwin workspace-v2 retained-handle authority.
 *
 * This is deliberately implemented only on the platform where the packaged
 * desktop can prove the complete primitive.  It never accepts a path after
 * preparation: the live root directory fd and all staged content fds remain
 * inside this addon.  The generic N-API export surface below is absent on
 * other platforms, so Electron main truthfully keeps C2 unavailable there.
 * ------------------------------------------------------------------ */

#if defined(__APPLE__)

#define WFS_MAX_ENTRIES 256
#define WFS_MAX_PATH 1024
#define WFS_MAX_SLOT 128
#define WFS_HANDLE_LEN 49

enum wfs_operation {
  WFS_CREATE,
  WFS_REPLACE,
  WFS_DELETE,
  WFS_MOVE,
  WFS_MKDIR
};

struct wfs_snapshot {
  int exists;
  dev_t dev;
  ino_t ino;
  mode_t mode;
  int has_digest;
  char digest[CC_SHA256_DIGEST_LENGTH * 2 + 1];
};

struct wfs_entry {
  enum wfs_operation operation;
  char *relative_path;
  char *destination_relative_path;
  struct wfs_snapshot snapshot;
  struct wfs_snapshot destination_snapshot;
  int has_destination_snapshot;
  char *slot;
  char *expected_digest;
  size_t expected_size;
  char *staged_name;
  int staged_fd;
};

struct wfs_prepared {
  char handle[WFS_HANDLE_LEN];
  int root_fd;
  struct wfs_entry *entries;
  size_t entry_count;
  struct wfs_prepared *next;
};

struct wfs_claim {
  char *claim_id;
  struct wfs_claim *next;
};

static struct wfs_prepared *wfs_prepared_head = NULL;
static struct wfs_claim *wfs_claim_head = NULL;

static void wfs_hex(const unsigned char *input, size_t length, char *output) {
  static const char digits[] = "0123456789abcdef";
  size_t i;
  for (i = 0; i < length; i++) {
    output[i * 2] = digits[(input[i] >> 4) & 0x0f];
    output[i * 2 + 1] = digits[input[i] & 0x0f];
  }
  output[length * 2] = '\0';
}

static int wfs_path_is_safe(const char *path) {
  const char *cursor;
  const char *segment;
  if (!path || !path[0] || path[0] == '/' || strchr(path, '\\')) return 0;
  cursor = path;
  segment = path;
  while (1) {
    if (*cursor == '/' || *cursor == '\0') {
      size_t length = (size_t)(cursor - segment);
      if (length == 0 || (length == 1 && segment[0] == '.') ||
          (length == 2 && segment[0] == '.' && segment[1] == '.')) return 0;
      if (*cursor == '\0') break;
      segment = cursor + 1;
    }
    cursor++;
  }
  return strlen(path) < WFS_MAX_PATH;
}

/* Open the parent directory without following any component.  The caller owns
 * the returned fd and leaf string. */
static int wfs_open_parent(int root_fd, const char *path, char **leaf_out) {
  char *copy = NULL;
  char *last = NULL;
  char *cursor = NULL;
  int current = -1;
  if (!wfs_path_is_safe(path)) return -1;
  copy = strdup(path);
  if (!copy) return -1;
  last = strrchr(copy, '/');
  if (!last) {
    *leaf_out = copy;
    return dup(root_fd);
  }
  *last = '\0';
  *leaf_out = strdup(last + 1);
  if (!*leaf_out) {
    free(copy);
    return -1;
  }
  current = dup(root_fd);
  cursor = copy;
  while (cursor && *cursor) {
    char *slash = strchr(cursor, '/');
    int next;
    if (slash) *slash = '\0';
    next = openat(current, cursor,
                  O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW_ANY);
    close(current);
    if (next < 0) {
      free(copy);
      free(*leaf_out);
      *leaf_out = NULL;
      return -1;
    }
    current = next;
    cursor = slash ? slash + 1 : NULL;
  }
  free(copy);
  return current;
}

static int wfs_file_digest_at(int parent_fd, const char *leaf, char digest[65]) {
  int fd = openat(parent_fd, leaf, O_RDONLY | O_CLOEXEC | O_NOFOLLOW_ANY);
  unsigned char buffer[8192];
  unsigned char output[CC_SHA256_DIGEST_LENGTH];
  CC_SHA256_CTX context;
  ssize_t count;
  if (fd < 0) return -1;
  CC_SHA256_Init(&context);
  while ((count = read(fd, buffer, sizeof buffer)) > 0)
    CC_SHA256_Update(&context, buffer, (CC_LONG)count);
  close(fd);
  if (count < 0) return -1;
  CC_SHA256_Final(output, &context);
  wfs_hex(output, sizeof output, digest);
  return 0;
}

static int wfs_snapshot_path(int root_fd, const char *path,
                             int expected_exists, const char *expected_kind,
                             const char *expected_digest,
                             struct wfs_snapshot *snapshot) {
  char *leaf = NULL;
  int parent = wfs_open_parent(root_fd, path, &leaf);
  struct stat st;
  int result = -1;
  memset(snapshot, 0, sizeof *snapshot);
  if (parent < 0) return -1;
  if (fstatat(parent, leaf, &st, AT_SYMLINK_NOFOLLOW) < 0) {
    if (errno == ENOENT && !expected_exists) {
      snapshot->exists = 0;
      result = 0;
    }
    goto done;
  }
  if (!expected_exists || S_ISLNK(st.st_mode)) goto done;
  if (expected_kind &&
      ((strcmp(expected_kind, "file") == 0 && !S_ISREG(st.st_mode)) ||
       (strcmp(expected_kind, "directory") == 0 && !S_ISDIR(st.st_mode))))
    goto done;
  snapshot->exists = 1;
  snapshot->dev = st.st_dev;
  snapshot->ino = st.st_ino;
  snapshot->mode = st.st_mode;
  if (expected_digest && expected_digest[0]) {
    if (!S_ISREG(st.st_mode) || wfs_file_digest_at(parent, leaf, snapshot->digest) < 0 ||
        strcmp(expected_digest, snapshot->digest) != 0) goto done;
    snapshot->has_digest = 1;
  }
  result = 0;
done:
  close(parent);
  free(leaf);
  return result;
}

static int wfs_snapshot_matches(int root_fd, const char *path,
                                const struct wfs_snapshot *snapshot) {
  char *leaf = NULL;
  int parent = wfs_open_parent(root_fd, path, &leaf);
  struct stat st;
  int result = 0;
  if (parent < 0) return 0;
  if (fstatat(parent, leaf, &st, AT_SYMLINK_NOFOLLOW) < 0) {
    result = !snapshot->exists && errno == ENOENT;
    goto done;
  }
  if (!snapshot->exists || S_ISLNK(st.st_mode) || st.st_dev != snapshot->dev ||
      st.st_ino != snapshot->ino || st.st_mode != snapshot->mode) goto done;
  result = 1;
done:
  close(parent);
  free(leaf);
  return result;
}

/* The line above cannot compare an in-place digest. Keep this separate to
 * avoid accepting content changed under the same inode. */
static int wfs_snapshot_digest_matches(int root_fd, const char *path,
                                       const struct wfs_snapshot *snapshot) {
  char observed[65];
  char *leaf = NULL;
  int parent;
  if (!snapshot->has_digest) return 1;
  parent = wfs_open_parent(root_fd, path, &leaf);
  if (parent < 0) return 0;
  if (wfs_file_digest_at(parent, leaf, observed) < 0) {
    close(parent); free(leaf); return 0;
  }
  close(parent); free(leaf);
  return strcmp(observed, snapshot->digest) == 0;
}

static struct wfs_prepared *wfs_find_prepared(const char *handle) {
  struct wfs_prepared *cursor = wfs_prepared_head;
  while (cursor) {
    if (strcmp(cursor->handle, handle) == 0) return cursor;
    cursor = cursor->next;
  }
  return NULL;
}

static int wfs_claim_exists(const char *claim_id) {
  struct wfs_claim *cursor = wfs_claim_head;
  while (cursor) {
    if (strcmp(cursor->claim_id, claim_id) == 0) return 1;
    cursor = cursor->next;
  }
  return 0;
}

static void wfs_record_claim(const char *claim_id) {
  struct wfs_claim *claim;
  if (wfs_claim_exists(claim_id)) return;
  claim = (struct wfs_claim *)calloc(1, sizeof *claim);
  if (!claim) return;
  claim->claim_id = strdup(claim_id);
  if (!claim->claim_id) { free(claim); return; }
  claim->next = wfs_claim_head;
  wfs_claim_head = claim;
}

static void wfs_destroy_prepared(struct wfs_prepared *prepared) {
  size_t i;
  struct wfs_prepared **cursor = &wfs_prepared_head;
  while (*cursor && *cursor != prepared) cursor = &(*cursor)->next;
  if (*cursor == prepared) *cursor = prepared->next;
  for (i = 0; i < prepared->entry_count; i++) {
    struct wfs_entry *entry = &prepared->entries[i];
    if (entry->staged_fd >= 0) close(entry->staged_fd);
    if (entry->staged_name) unlinkat(prepared->root_fd, entry->staged_name, 0);
    free(entry->relative_path); free(entry->destination_relative_path);
    free(entry->slot); free(entry->expected_digest); free(entry->staged_name);
  }
  close(prepared->root_fd);
  free(prepared->entries);
  free(prepared);
}

/* ------------------------------------------------------------------ *
 * N-API object helpers for the Darwin-only v2 bridge.
 * ------------------------------------------------------------------ */

static int wfs_named(napi_env env, napi_value object, const char *name,
                     napi_value *value) {
  bool exists = false;
  if (napi_has_named_property(env, object, name, &exists) != napi_ok || !exists)
    return 0;
  return napi_get_named_property(env, object, name, value) == napi_ok;
}

static char *wfs_optional_string(napi_env env, napi_value object,
                                 const char *name) {
  napi_value value;
  napi_valuetype type;
  if (!wfs_named(env, object, name, &value) ||
      napi_typeof(env, value, &type) != napi_ok || type == napi_undefined || type == napi_null)
    return NULL;
  if (type != napi_string) return NULL;
  return get_string(env, value);
}

static int wfs_required_bool(napi_env env, napi_value object, const char *name,
                             int *out) {
  napi_value value;
  napi_valuetype type;
  if (!wfs_named(env, object, name, &value) || napi_typeof(env, value, &type) != napi_ok || type != napi_boolean)
    return 0;
  *out = get_bool(env, value);
  return 1;
}

static int wfs_required_size(napi_env env, napi_value object, const char *name,
                             size_t *out) {
  napi_value value;
  double number;
  if (!wfs_named(env, object, name, &value) || napi_get_value_double(env, value, &number) != napi_ok ||
      number < 0 || number > 134217728 || number != (double)(size_t)number) return 0;
  *out = (size_t)number;
  return 1;
}

static int wfs_operation_from_string(const char *value, enum wfs_operation *out) {
  if (strcmp(value, "create") == 0) *out = WFS_CREATE;
  else if (strcmp(value, "replace") == 0) *out = WFS_REPLACE;
  else if (strcmp(value, "delete") == 0) *out = WFS_DELETE;
  else if (strcmp(value, "move") == 0) *out = WFS_MOVE;
  else if (strcmp(value, "mkdir") == 0) *out = WFS_MKDIR;
  else return 0;
  return 1;
}

static void wfs_throw(napi_env env, const char *code, const char *message) {
  napi_throw_error(env, code, message);
}

static napi_value wfs_string(napi_env env, const char *text) {
  napi_value value;
  napi_create_string_utf8(env, text, NAPI_AUTO_LENGTH, &value);
  return value;
}

static void wfs_set_string(napi_env env, napi_value object, const char *key,
                           const char *value) {
  napi_set_named_property(env, object, key, wfs_string(env, value));
}

static napi_value WorkspaceRootIdentity(napi_env env, napi_callback_info info) {
  size_t argc = 1;
  napi_value argv[1], result;
  char *root;
  struct stat st;
  int root_fd;
  char volume[32], file[32];
  napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
  if (argc != 1 || !(root = get_string(env, argv[0]))) {
    wfs_throw(env, "EINVAL", "workspaceRootIdentity(root)"); return NULL;
  }
  root_fd = open(root, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW_ANY);
  free(root);
  if (root_fd < 0 || fstat(root_fd, &st) < 0 || !S_ISDIR(st.st_mode)) {
    if (root_fd >= 0) close(root_fd);
    wfs_throw(env, "EIO", "workspace root identity failed"); return NULL;
  }
  close(root_fd);
  snprintf(volume, sizeof volume, "%llu", (unsigned long long)st.st_dev);
  snprintf(file, sizeof file, "%llu", (unsigned long long)st.st_ino);
  napi_create_object(env, &result);
  wfs_set_string(env, result, "volumeId", volume);
  wfs_set_string(env, result, "fileId", file);
  return result;
}

static int wfs_parse_entry(napi_env env, napi_value object, int root_fd,
                           struct wfs_entry *entry) {
  napi_value precondition;
  char *operation = NULL;
  char *kind = NULL;
  int exists;
  memset(entry, 0, sizeof *entry);
  entry->staged_fd = -1;
  operation = wfs_optional_string(env, object, "operation");
  entry->relative_path = wfs_optional_string(env, object, "relativePath");
  if (!operation || !entry->relative_path || !wfs_operation_from_string(operation, &entry->operation) ||
      !wfs_named(env, object, "precondition", &precondition) ||
      !wfs_required_bool(env, precondition, "exists", &exists)) goto fail;
  entry->destination_relative_path = wfs_optional_string(env, object, "destinationRelativePath");
  kind = wfs_optional_string(env, precondition, "kind");
  entry->expected_digest = wfs_optional_string(env, precondition, "sha256");
  if (!wfs_path_is_safe(entry->relative_path) ||
      (entry->destination_relative_path && !wfs_path_is_safe(entry->destination_relative_path)) ||
      (kind && strcmp(kind, "file") != 0 && strcmp(kind, "directory") != 0) ||
      wfs_snapshot_path(root_fd, entry->relative_path, exists, kind, entry->expected_digest, &entry->snapshot) < 0)
    goto fail;
  if (entry->operation == WFS_MOVE) {
    if (!entry->destination_relative_path ||
        wfs_snapshot_path(root_fd, entry->destination_relative_path, 0, NULL, NULL, &entry->destination_snapshot) < 0)
      goto fail;
    entry->has_destination_snapshot = 1;
  } else if (entry->destination_relative_path) goto fail;
  if (entry->operation == WFS_CREATE || entry->operation == WFS_REPLACE) {
    entry->slot = wfs_optional_string(env, object, "contentSlot");
    char *content_digest = wfs_optional_string(env, object, "contentDigest");
    if (!entry->slot || !content_digest || !wfs_required_size(env, object, "contentSize", &entry->expected_size) ||
        strlen(entry->slot) >= WFS_MAX_SLOT || strlen(content_digest) != 64) {
      free(content_digest); goto fail;
    }
    free(entry->expected_digest);
    entry->expected_digest = content_digest;
  } else if (entry->operation == WFS_DELETE || entry->operation == WFS_MOVE) {
    if (!entry->snapshot.exists) goto fail;
  } else if (entry->operation == WFS_MKDIR && entry->snapshot.exists) goto fail;
  free(operation); free(kind);
  return 1;
fail:
  free(operation); free(kind); free(entry->relative_path); free(entry->destination_relative_path);
  free(entry->slot); free(entry->expected_digest); memset(entry, 0, sizeof *entry); entry->staged_fd = -1;
  return 0;
}

/* A changeset with overlapping targets has order-dependent filesystem
 * semantics. Refuse it in the native authority even if an upstream validator
 * is compromised. */
static int wfs_entries_are_disjoint(const struct wfs_entry *entries,
                                    size_t count) {
  size_t i, j;
  for (i = 0; i < count; i++) {
    const struct wfs_entry *left = &entries[i];
    for (j = i + 1; j < count; j++) {
      const struct wfs_entry *right = &entries[j];
      if (strcmp(left->relative_path, right->relative_path) == 0 ||
          (left->destination_relative_path &&
           strcmp(left->destination_relative_path, right->relative_path) == 0) ||
          (right->destination_relative_path &&
           strcmp(left->relative_path, right->destination_relative_path) == 0) ||
          (left->destination_relative_path && right->destination_relative_path &&
           strcmp(left->destination_relative_path, right->destination_relative_path) == 0) ||
          (left->slot && right->slot && strcmp(left->slot, right->slot) == 0))
        return 0;
    }
  }
  return 1;
}

static napi_value WorkspacePrepare(napi_env env, napi_callback_info info) {
  size_t argc = 2, length = 0, i;
  napi_value argv[2], result, entries;
  char *root = NULL;
  struct wfs_prepared *prepared = NULL;
  napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
  {
    bool is_array = false;
    uint32_t entry_length = 0;
    if (argc != 2 || !(root = get_string(env, argv[0])) ||
        napi_is_array(env, argv[1], &is_array) != napi_ok || !is_array ||
        napi_get_array_length(env, argv[1], &entry_length) != napi_ok ||
        entry_length == 0 || entry_length > WFS_MAX_ENTRIES) {
      free(root); wfs_throw(env, "EINVAL", "workspacePrepare(root, entries)"); return NULL;
    }
    length = entry_length;
  }
  prepared = (struct wfs_prepared *)calloc(1, sizeof *prepared);
  if (!prepared) { free(root); wfs_throw(env, "ENOMEM", "workspace prepare allocation failed"); return NULL; }
  prepared->root_fd = open(root, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW_ANY);
  free(root);
  if (prepared->root_fd < 0) { free(prepared); wfs_throw(env, "EIO", "workspace root open failed"); return NULL; }
  prepared->entries = (struct wfs_entry *)calloc(length, sizeof *prepared->entries);
  prepared->entry_count = length;
  if (!prepared->entries) { close(prepared->root_fd); free(prepared); wfs_throw(env, "ENOMEM", "workspace entry allocation failed"); return NULL; }
  for (i = 0; i < length; i++) {
    napi_value entry;
    if (napi_get_element(env, argv[1], (uint32_t)i, &entry) != napi_ok ||
        !wfs_parse_entry(env, entry, prepared->root_fd, &prepared->entries[i])) {
      wfs_destroy_prepared(prepared); wfs_throw(env, "EINVAL", "workspace entry rejected"); return NULL;
    }
  }
  if (!wfs_entries_are_disjoint(prepared->entries, length)) {
    wfs_destroy_prepared(prepared);
    wfs_throw(env, "EINVAL", "workspace entries overlap");
    return NULL;
  }
  {
    unsigned char random_handle[16];
    arc4random_buf(random_handle, sizeof random_handle);
    strcpy(prepared->handle, "nwp_");
    wfs_hex(random_handle, sizeof random_handle, prepared->handle + 4);
  }
  prepared->next = wfs_prepared_head; wfs_prepared_head = prepared;
  napi_create_object(env, &result);
  wfs_set_string(env, result, "handle", prepared->handle);
  {
    char digest[65]; unsigned char state[CC_SHA256_DIGEST_LENGTH]; CC_SHA256_CTX ctx;
    CC_SHA256_Init(&ctx);
    for (i = 0; i < length; i++) CC_SHA256_Update(&ctx, prepared->entries[i].relative_path, (CC_LONG)strlen(prepared->entries[i].relative_path));
    CC_SHA256_Final(state, &ctx); wfs_hex(state, sizeof state, digest);
    wfs_set_string(env, result, "observedTargetDigest", digest);
  }
  napi_create_array(env, &entries);
  {
    uint32_t slot_index = 0;
    for (i = 0; i < length; i++) {
    struct wfs_entry *entry = &prepared->entries[i];
    if (entry->slot) {
      napi_value slot; napi_create_object(env, &slot);
      wfs_set_string(env, slot, "slot", entry->slot);
      wfs_set_string(env, slot, "digest", entry->expected_digest);
      napi_value size; napi_create_double(env, (double)entry->expected_size, &size);
      napi_set_named_property(env, slot, "size", size);
        napi_set_element(env, entries, slot_index++, slot);
      }
    }
  }
  napi_set_named_property(env, result, "slots", entries);
  return result;
}

/* Locate a staged entry by its opaque upload slot. */
static struct wfs_entry *wfs_find_slot(struct wfs_prepared *prepared, const char *slot) {
  size_t i;
  for (i = 0; i < prepared->entry_count; i++)
    if (prepared->entries[i].slot && strcmp(prepared->entries[i].slot, slot) == 0) return &prepared->entries[i];
  return NULL;
}

static int wfs_create_stage(struct wfs_prepared *prepared, struct wfs_entry *entry) {
  unsigned char random[16]; char hex[33];
  if (entry->staged_fd >= 0) return 1;
  arc4random_buf(random, sizeof random); wfs_hex(random, sizeof random, hex);
  entry->staged_name = (char *)malloc(strlen(".copilot-stage-") + strlen(hex) + 1);
  if (!entry->staged_name) return 0;
  sprintf(entry->staged_name, ".copilot-stage-%s", hex);
  entry->staged_fd = openat(prepared->root_fd, entry->staged_name,
                            O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW_ANY, 0600);
  return entry->staged_fd >= 0;
}

static napi_value WorkspaceWrite(napi_env env, napi_callback_info info) {
  size_t argc = 3, byte_offset = 0, length = 0; napi_value argv[3];
  char *handle = NULL, *slot = NULL; void *data = NULL; napi_typedarray_type type;
  struct wfs_prepared *prepared; struct wfs_entry *entry; ssize_t written; size_t offset = 0;
  napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
  if (argc != 3 || !(handle = get_string(env, argv[0])) || !(slot = get_string(env, argv[1])) ||
      napi_get_typedarray_info(env, argv[2], &type, &length, &data, NULL, &byte_offset) != napi_ok) {
    free(handle); free(slot); wfs_throw(env, "EINVAL", "workspaceWrite(handle, slot, bytes)"); return NULL;
  }
  prepared = wfs_find_prepared(handle); entry = prepared ? wfs_find_slot(prepared, slot) : NULL;
  free(handle); free(slot);
  if (!entry || !wfs_create_stage(prepared, entry)) { wfs_throw(env, "EIO", "workspace staged write rejected"); return NULL; }
  while (offset < length) {
    written = write(entry->staged_fd, (const unsigned char *)data + offset, length - offset);
    if (written <= 0) { wfs_throw(env, "EIO", "workspace staged write failed"); return NULL; }
    offset += (size_t)written;
  }
  return NULL;
}

static napi_value WorkspaceSeal(napi_env env, napi_callback_info info) {
  size_t argc = 2; napi_value argv[2], result; char *handle = NULL, *slot = NULL;
  struct wfs_prepared *prepared; struct wfs_entry *entry; struct stat st;
  unsigned char buffer[8192], output[CC_SHA256_DIGEST_LENGTH]; CC_SHA256_CTX ctx; ssize_t count; char digest[65];
  napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
  if (argc != 2 || !(handle = get_string(env, argv[0])) || !(slot = get_string(env, argv[1]))) {
    free(handle); free(slot); wfs_throw(env, "EINVAL", "workspaceSeal(handle, slot)"); return NULL;
  }
  prepared = wfs_find_prepared(handle); entry = prepared ? wfs_find_slot(prepared, slot) : NULL;
  free(handle); free(slot);
  if (!entry || entry->staged_fd < 0 || fsync(entry->staged_fd) < 0 || fstat(entry->staged_fd, &st) < 0 ||
      lseek(entry->staged_fd, 0, SEEK_SET) < 0) { wfs_throw(env, "EIO", "workspace seal failed"); return NULL; }
  CC_SHA256_Init(&ctx);
  while ((count = read(entry->staged_fd, buffer, sizeof buffer)) > 0) CC_SHA256_Update(&ctx, buffer, (CC_LONG)count);
  if (count < 0) { wfs_throw(env, "EIO", "workspace seal read failed"); return NULL; }
  CC_SHA256_Final(output, &ctx); wfs_hex(output, sizeof output, digest);
  napi_create_object(env, &result); wfs_set_string(env, result, "digest", digest);
  { napi_value size; napi_create_double(env, (double)st.st_size, &size); napi_set_named_property(env, result, "size", size); }
  return result;
}

static int wfs_entry_live(struct wfs_prepared *prepared, struct wfs_entry *entry) {
  if (!wfs_snapshot_matches(prepared->root_fd, entry->relative_path, &entry->snapshot) ||
      !wfs_snapshot_digest_matches(prepared->root_fd, entry->relative_path, &entry->snapshot)) return 0;
  if (entry->has_destination_snapshot &&
      !wfs_snapshot_matches(prepared->root_fd, entry->destination_relative_path, &entry->destination_snapshot)) return 0;
  return 1;
}

static int wfs_commit_entry(struct wfs_prepared *prepared, struct wfs_entry *entry) {
  char *leaf = NULL, *dest_leaf = NULL; int parent = -1, dest_parent = -1, result = -1;
  parent = wfs_open_parent(prepared->root_fd, entry->relative_path, &leaf);
  if (parent < 0) goto done;
  if (entry->operation == WFS_CREATE || entry->operation == WFS_REPLACE) {
    if (entry->staged_fd < 0 || fsync(entry->staged_fd) < 0) goto done;
    dest_parent = parent; parent = -1; dest_leaf = leaf; leaf = NULL;
    if (renameat(prepared->root_fd, entry->staged_name, dest_parent, dest_leaf) < 0) goto done;
    free(entry->staged_name); entry->staged_name = NULL; close(entry->staged_fd); entry->staged_fd = -1;
  } else if (entry->operation == WFS_DELETE) {
    if (unlinkat(parent, leaf, S_ISDIR(entry->snapshot.mode) ? AT_REMOVEDIR : 0) < 0) goto done;
  } else if (entry->operation == WFS_MKDIR) {
    if (mkdirat(parent, leaf, 0700) < 0) goto done;
  } else if (entry->operation == WFS_MOVE) {
    dest_parent = wfs_open_parent(prepared->root_fd, entry->destination_relative_path, &dest_leaf);
    if (dest_parent < 0 || renameat(parent, leaf, dest_parent, dest_leaf) < 0) goto done;
  }
  result = 0;
done:
  if (parent >= 0) close(parent); if (dest_parent >= 0) close(dest_parent);
  free(leaf); free(dest_leaf); return result;
}

static napi_value wfs_commit_result(napi_env env, const char *outcome, const char *claim_id) {
  napi_value result; char receipt[256];
  snprintf(receipt, sizeof receipt, "workspace-receipt://%s", claim_id);
  napi_create_object(env, &result); wfs_set_string(env, result, "outcome", outcome);
  wfs_set_string(env, result, "receiptRef", receipt); return result;
}

static napi_value WorkspaceCommit(napi_env env, napi_callback_info info) {
  size_t argc = 2, i; napi_value argv[2]; char *handle = NULL, *claim = NULL;
  struct wfs_prepared *prepared;
  napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
  if (argc != 2 || !(handle = get_string(env, argv[0])) || !(claim = get_string(env, argv[1]))) {
    free(handle); free(claim); wfs_throw(env, "EINVAL", "workspaceCommit(handle, claim)"); return NULL;
  }
  if (wfs_claim_exists(claim)) { free(handle); { napi_value v = wfs_commit_result(env, "already_applied", claim); free(claim); return v; } }
  prepared = wfs_find_prepared(handle); free(handle);
  if (!prepared) { napi_value v = wfs_commit_result(env, "indeterminate", claim); free(claim); return v; }
  for (i = 0; i < prepared->entry_count; i++) if (!wfs_entry_live(prepared, &prepared->entries[i])) {
    napi_value v = wfs_commit_result(env, "precondition_drift", claim); free(claim); return v;
  }
  for (i = 0; i < prepared->entry_count; i++) if (wfs_commit_entry(prepared, &prepared->entries[i]) < 0) {
      napi_value v = wfs_commit_result(env, "indeterminate", claim); free(claim); return v;
  }
  wfs_record_claim(claim);
  {
    napi_value v = wfs_commit_result(env, "applied", claim);
    free(claim);
    wfs_destroy_prepared(prepared);
    return v;
  }
}

static napi_value WorkspaceReconcile(napi_env env, napi_callback_info info) {
  size_t argc = 1; napi_value argv[1]; char *claim = NULL; napi_value value;
  napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
  if (argc != 1 || !(claim = get_string(env, argv[0]))) { free(claim); wfs_throw(env, "EINVAL", "workspaceReconcileClaim(claim)"); return NULL; }
  value = wfs_commit_result(env, wfs_claim_exists(claim) ? "already_applied" : "indeterminate", claim);
  free(claim); return value;
}

static napi_value WorkspaceAbort(napi_env env, napi_callback_info info) {
  size_t argc = 1; napi_value argv[1]; char *handle = NULL; struct wfs_prepared *prepared;
  napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
  if (argc != 1 || !(handle = get_string(env, argv[0]))) { free(handle); wfs_throw(env, "EINVAL", "workspaceAbort(handle)"); return NULL; }
  prepared = wfs_find_prepared(handle); free(handle); if (!prepared) return NULL; wfs_destroy_prepared(prepared); return NULL;
}

static napi_value WorkspaceRecovery(napi_env env, napi_callback_info info) {
  size_t argc = 1; napi_value argv[1]; char *value = NULL;
  napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
  if (argc != 1 || !(value = get_string(env, argv[0]))) { free(value); wfs_throw(env, "EINVAL", "workspace recovery input"); return NULL; }
  /* A live prepared handle can be retried; after process loss the native
   * process-local handle is intentionally not guessed. */
  { const char *outcome = wfs_find_prepared(value) ? "proposed" : "conflict"; free(value); return wfs_string(env, outcome); }
}

#endif /* __APPLE__ */

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
#if defined(__APPLE__)
  napi_create_function(env, "workspaceRootIdentity", NAPI_AUTO_LENGTH,
                       WorkspaceRootIdentity, NULL, &fn);
  napi_set_named_property(env, exports, "workspaceRootIdentity", fn);
  napi_create_function(env, "workspacePrepare", NAPI_AUTO_LENGTH,
                       WorkspacePrepare, NULL, &fn);
  napi_set_named_property(env, exports, "workspacePrepare", fn);
  napi_create_function(env, "workspaceWrite", NAPI_AUTO_LENGTH,
                       WorkspaceWrite, NULL, &fn);
  napi_set_named_property(env, exports, "workspaceWrite", fn);
  napi_create_function(env, "workspaceSeal", NAPI_AUTO_LENGTH,
                       WorkspaceSeal, NULL, &fn);
  napi_set_named_property(env, exports, "workspaceSeal", fn);
  napi_create_function(env, "workspaceCommit", NAPI_AUTO_LENGTH,
                       WorkspaceCommit, NULL, &fn);
  napi_set_named_property(env, exports, "workspaceCommit", fn);
  napi_create_function(env, "workspaceReconcile", NAPI_AUTO_LENGTH,
                       WorkspaceReconcile, NULL, &fn);
  napi_set_named_property(env, exports, "workspaceReconcile", fn);
  napi_create_function(env, "workspaceReconcileClaim", NAPI_AUTO_LENGTH,
                       WorkspaceReconcile, NULL, &fn);
  napi_set_named_property(env, exports, "workspaceReconcileClaim", fn);
  napi_create_function(env, "workspaceAbort", NAPI_AUTO_LENGTH,
                       WorkspaceAbort, NULL, &fn);
  napi_set_named_property(env, exports, "workspaceAbort", fn);
  napi_create_function(env, "workspaceProposeRecovery", NAPI_AUTO_LENGTH,
                       WorkspaceRecovery, NULL, &fn);
  napi_set_named_property(env, exports, "workspaceProposeRecovery", fn);
  napi_create_function(env, "workspaceProposeRecoveryClaim", NAPI_AUTO_LENGTH,
                       WorkspaceRecovery, NULL, &fn);
  napi_set_named_property(env, exports, "workspaceProposeRecoveryClaim", fn);
#endif
  return exports;
}

NAPI_MODULE(NODE_GYP_MODULE_NAME, Init)
