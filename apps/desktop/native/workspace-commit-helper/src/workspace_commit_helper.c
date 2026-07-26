/*
 * workspace-commit-helper
 *
 * The filesystem mutation TCB for the desktop workspace authority. This is a
 * deliberately small macOS-only process, launched by Electron main with:
 *
 *   fd 0  authenticated private command pipe (main -> helper)
 *   fd 1  authenticated private response pipe (helper -> main)
 *   fd 2  closed by the parent / never used for diagnostics
 *   fd 3  a one-time 32-byte channel key, then closed
 *   fd 4  app-private staging-directory capability (opened by Electron main)
 *   fd 5  app-private durable journal-directory capability
 *   fd 6  persistent 32-byte journal HMAC key, then closed
 *
 * It has no listener, no UDS name, no TMPDIR lookup, no shell, no inherited
 * environment, no Electron/Node API, and no service-visible file descriptor.
 * The helper accepts only root-relative segments during prepare and retains
 * root and parent descriptors until commit/abort. Staged bytes live only
 * beneath the inherited private staging descriptor, are retained by inode,
 * and are re-attested immediately before the no-replace effect. A process
 * crash is reconciled from an HMAC-protected durable journal; this helper
 * never replays an indeterminate mutation.
 */

#include <CommonCrypto/CommonDigest.h>
#include <CommonCrypto/CommonHMAC.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/clonefile.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#ifndef O_NOFOLLOW_ANY
#define O_NOFOLLOW_ANY 0x20000000
#endif

#define PROTOCOL 2
#define KEY_BYTES 32
#define MAC_BYTES 32
#define CHANNEL_SEQUENCE_BYTES 8
#define MAX_FRAME (128u * 1024u * 1024u)
#define MAX_ENTRIES 256u
#define MAX_PATH_BYTES 4096u
#define MAX_SLOT_BYTES 120u
#define MAX_CLAIM_BYTES 160u
#define MAX_STAGE_DIR_BYTES 48u
#define JOURNAL_MAGIC "C2JNLv2"
#define JOURNAL_VERSION 3

enum request {
  ROOT_IDENTITY = 1, PREPARE = 2, WRITE = 3, SEAL = 4, COMMIT = 5,
  RECONCILE_PREPARED = 6, RECONCILE_CLAIM = 7, ABORT = 8,
  PROPOSE_RECOVERY = 9, PROPOSE_RECOVERY_CLAIM = 10, CLOSE_HELPER = 11,
  PING = 12
};
enum operation { CREATE = 1, REPLACE = 2, DELETE = 3, MOVE = 4, MKDIR = 5 };
enum outcome { APPLIED = 1, ALREADY_APPLIED = 2, PRECONDITION_DRIFT = 3,
               FAILED = 4, INDETERMINATE = 5 };
enum failure { INVALID = 1, UNSUPPORTED = 2, CONFLICT = 3, DRIFT = 4, INTERNAL = 5 };
enum journal_state { JOURNAL_PREPARED = 1, JOURNAL_AUTHORIZED = 2,
  JOURNAL_COMMITTING = 3, JOURNAL_APPLIED = 4,
  JOURNAL_INDETERMINATE = 5, JOURNAL_FAILED_BEFORE_EFFECT = 6,
  JOURNAL_CLEANED = 7 };
enum claim_acquire_result { CLAIM_ACQUIRED = 1, CLAIM_EXISTS = 2,
  CLAIM_BINDING_MISMATCH = 3, CLAIM_ACQUIRE_ERROR = 4 };

struct reader { const uint8_t *data; size_t length; size_t offset; };
struct writer { uint8_t *data; size_t length; size_t capacity; };

struct snapshot {
  int exists;
  int kind;
  dev_t dev;
  ino_t ino;
  mode_t mode;
  off_t size;
  char digest[65];
};

struct entry {
  enum operation operation;
  char *relative_path;
  char *leaf;
  int parent_fd;
  char *destination_relative_path;
  char *destination_leaf;
  int destination_parent_fd;
  struct snapshot source;
  struct snapshot destination;
  int has_destination;
  char *slot;
  char *expected_digest;
  uint64_t expected_size;
  uint64_t bytes_written;
  int stage_fd;
  char stage_name[80];
  struct stat sealed_stat;
  char sealed_digest[65];
  int sealed;
};

struct prepared {
  char handle[37];
  int root_fd;
  dev_t root_dev;
  ino_t root_ino;
  char journal_name[48];
  char stage_dir[48];
  char binding_digest[65];
  struct entry *entries;
  uint32_t entry_count;
  struct prepared *next;
};

struct journal_record {
  char magic[8];
  uint8_t version;
  uint8_t state;
  uint8_t outcome;
  uint8_t cleanup_complete;
  uint16_t entry_count;
  char handle[37];
  char claim[MAX_CLAIM_BYTES + 1];
  char stage_dir[MAX_STAGE_DIR_BYTES];
  char binding_digest[65];
  uint8_t mac[MAC_BYTES];
};

struct claim { char *id; enum outcome outcome; enum journal_state state;
  struct claim *next; };

static uint8_t channel_key[KEY_BYTES];
static uint8_t journal_key[KEY_BYTES];
static struct prepared *prepared_head = NULL;
static struct claim *claim_head = NULL;
static int staging_parent_fd = -1;
static int staging_run_fd = -1;
static int journal_fd = -1;
static char staging_run_name[MAX_STAGE_DIR_BYTES];
static uint8_t test_crash_boundary = 0;

static int read_all(int fd, void *out, size_t length) {
  uint8_t *cursor = out;
  while (length > 0) {
    ssize_t count = read(fd, cursor, length);
    if (count == 0) return 0;
    if (count < 0) { if (errno == EINTR) continue; return -1; }
    cursor += (size_t)count; length -= (size_t)count;
  }
  return 1;
}

static int write_all(int fd, const void *input, size_t length) {
  const uint8_t *cursor = input;
  while (length > 0) {
    ssize_t count = write(fd, cursor, length);
    if (count < 0) { if (errno == EINTR) continue; return -1; }
    cursor += (size_t)count; length -= (size_t)count;
  }
  return 0;
}

static uint32_t read_be32(const uint8_t *value) {
  return ((uint32_t)value[0] << 24) | ((uint32_t)value[1] << 16) |
         ((uint32_t)value[2] << 8) | value[3];
}
static uint64_t read_be64(const uint8_t *value) {
  uint64_t result = 0; int i;
  for (i = 0; i < 8; i++) result = (result << 8) | value[i];
  return result;
}
static void write_be32(uint8_t *value, uint32_t input) {
  value[0] = (uint8_t)(input >> 24); value[1] = (uint8_t)(input >> 16);
  value[2] = (uint8_t)(input >> 8); value[3] = (uint8_t)input;
}
static void write_be64(uint8_t *value, uint64_t input) {
  int i; for (i = 7; i >= 0; i--) { value[i] = (uint8_t)input; input >>= 8; }
}

static int writer_reserve(struct writer *writer, size_t additional) {
  size_t required = writer->length + additional, capacity; uint8_t *next;
  if (required > MAX_FRAME) return 0;
  if (required <= writer->capacity) return 1;
  capacity = writer->capacity ? writer->capacity : 128;
  while (capacity < required) capacity *= 2;
  next = realloc(writer->data, capacity); if (!next) return 0;
  writer->data = next; writer->capacity = capacity; return 1;
}
static int writer_u8(struct writer *writer, uint8_t value) {
  if (!writer_reserve(writer, 1)) return 0; writer->data[writer->length++] = value; return 1;
}
static int writer_u32(struct writer *writer, uint32_t value) {
  if (!writer_reserve(writer, 4)) return 0; write_be32(writer->data + writer->length, value); writer->length += 4; return 1;
}
static int writer_u64(struct writer *writer, uint64_t value) {
  if (!writer_reserve(writer, 8)) return 0; write_be64(writer->data + writer->length, value); writer->length += 8; return 1;
}
static int writer_bytes(struct writer *writer, const void *input, uint32_t length) {
  if (!writer_u32(writer, length) || !writer_reserve(writer, length)) return 0;
  if (length) memcpy(writer->data + writer->length, input, length); writer->length += length; return 1;
}
static int writer_string(struct writer *writer, const char *value) {
  return writer_bytes(writer, value, (uint32_t)strlen(value));
}
static void writer_free(struct writer *writer) { free(writer->data); memset(writer, 0, sizeof *writer); }

static int reader_need(struct reader *reader, size_t length) {
  return length <= reader->length - reader->offset;
}
static int reader_u8(struct reader *reader, uint8_t *out) {
  if (!reader_need(reader, 1)) return 0; *out = reader->data[reader->offset++]; return 1;
}
static int reader_u32(struct reader *reader, uint32_t *out) {
  if (!reader_need(reader, 4)) return 0; *out = read_be32(reader->data + reader->offset); reader->offset += 4; return 1;
}
static int reader_u64(struct reader *reader, uint64_t *out) {
  if (!reader_need(reader, 8)) return 0; *out = read_be64(reader->data + reader->offset); reader->offset += 8; return 1;
}
static int reader_bytes(struct reader *reader, const uint8_t **out, uint32_t *length) {
  if (!reader_u32(reader, length) || !reader_need(reader, *length)) return 0;
  *out = reader->data + reader->offset; reader->offset += *length; return 1;
}
static char *reader_string(struct reader *reader, size_t maximum) {
  const uint8_t *raw; uint32_t length; char *out;
  if (!reader_bytes(reader, &raw, &length) || length > maximum || memchr(raw, '\0', length)) return NULL;
  out = calloc((size_t)length + 1, 1); if (!out) return NULL;
  if (length) memcpy(out, raw, length); return out;
}

static void hex(const uint8_t *input, size_t length, char *out) {
  static const char digits[] = "0123456789abcdef"; size_t i;
  for (i = 0; i < length; i++) { out[i * 2] = digits[input[i] >> 4]; out[i * 2 + 1] = digits[input[i] & 15]; }
  out[length * 2] = '\0';
}

/* The claim binding is a canonical, length-delimited description of the
 * approved effect. A claim ID is only a coordination key: this digest pins
 * the root identity, each operation, every target spelling, its observed
 * precondition, and the exact staged-content promise. Two helpers may never
 * use the same claim to authorize different filesystem effects. */
static void binding_u8(CC_SHA256_CTX *context, uint8_t value) {
  CC_SHA256_Update(context, &value, 1);
}

static void binding_u32(CC_SHA256_CTX *context, uint32_t value) {
  uint8_t encoded[4]; write_be32(encoded, value);
  CC_SHA256_Update(context, encoded, sizeof encoded);
}

static void binding_u64(CC_SHA256_CTX *context, uint64_t value) {
  uint8_t encoded[8]; write_be64(encoded, value);
  CC_SHA256_Update(context, encoded, sizeof encoded);
}

static void binding_string(CC_SHA256_CTX *context, const char *value) {
  size_t length = value ? strlen(value) : 0;
  binding_u32(context, (uint32_t)length);
  if (length) CC_SHA256_Update(context, value, (CC_LONG)length);
}

static void binding_snapshot(CC_SHA256_CTX *context, const struct snapshot *snapshot) {
  binding_u8(context, snapshot->exists ? 1 : 0);
  binding_u8(context, (uint8_t)snapshot->kind);
  binding_u64(context, (uint64_t)snapshot->dev);
  binding_u64(context, (uint64_t)snapshot->ino);
  binding_u64(context, (uint64_t)snapshot->mode);
  binding_u64(context, (uint64_t)snapshot->size);
  binding_string(context, snapshot->digest);
}

static int compute_prepared_binding(struct prepared *prepared) {
  static const char domain[] = "workspace-commit-effect-v1";
  CC_SHA256_CTX context; uint8_t digest[CC_SHA256_DIGEST_LENGTH]; uint32_t i;
  CC_SHA256_Init(&context);
  binding_string(&context, domain);
  binding_u64(&context, (uint64_t)prepared->root_dev);
  binding_u64(&context, (uint64_t)prepared->root_ino);
  binding_u32(&context, prepared->entry_count);
  for (i = 0; i < prepared->entry_count; i++) {
    const struct entry *entry = &prepared->entries[i];
    binding_u8(&context, (uint8_t)entry->operation);
    binding_string(&context, entry->relative_path);
    binding_u8(&context, entry->has_destination ? 1 : 0);
    binding_string(&context, entry->destination_relative_path);
    binding_snapshot(&context, &entry->source);
    binding_snapshot(&context, &entry->destination);
    binding_string(&context, entry->slot);
    binding_string(&context, entry->expected_digest);
    binding_u64(&context, entry->expected_size);
  }
  CC_SHA256_Final(digest, &context);
  hex(digest, sizeof digest, prepared->binding_digest);
  return 1;
}

static int regular_digest_fd(int fd, char out[65], struct stat *stat_out) {
  CC_SHA256_CTX context; uint8_t buffer[8192], digest[CC_SHA256_DIGEST_LENGTH]; ssize_t count; struct stat before, after;
  if (fstat(fd, &before) < 0 || !S_ISREG(before.st_mode) || lseek(fd, 0, SEEK_SET) < 0) return 0;
  CC_SHA256_Init(&context);
  while ((count = read(fd, buffer, sizeof buffer)) > 0) CC_SHA256_Update(&context, buffer, (CC_LONG)count);
  if (count < 0 || fstat(fd, &after) < 0 || before.st_dev != after.st_dev || before.st_ino != after.st_ino || before.st_size != after.st_size) return 0;
  CC_SHA256_Final(digest, &context); hex(digest, sizeof digest, out); if (stat_out) *stat_out = after; return 1;
}

static int path_is_safe(const char *path) {
  const char *cursor, *segment;
  if (!path || !path[0] || path[0] == '/' || strchr(path, '\\') || strlen(path) > MAX_PATH_BYTES) return 0;
  cursor = path; segment = cursor;
  while (1) {
    if (*cursor == '/' || *cursor == '\0') {
      size_t length = (size_t)(cursor - segment);
      if (length == 0 || (length == 1 && segment[0] == '.') || (length == 2 && segment[0] == '.' && segment[1] == '.')) return 0;
      if (*cursor == '\0') return 1; segment = cursor + 1;
      cursor++; continue;
    }
    /* Native writes deliberately support only ASCII portable segments. This
       rejects every Unicode normalization spelling and lets us require an
       exact byte-for-byte directory entry at every case-insensitive hop. */
    if ((unsigned char)*cursor < 0x21 || (unsigned char)*cursor > 0x7e ||
        !((*cursor >= 'a' && *cursor <= 'z') || (*cursor >= 'A' && *cursor <= 'Z') ||
          (*cursor >= '0' && *cursor <= '9') || *cursor == '.' || *cursor == '_' || *cursor == '-')) return 0;
    cursor++;
  }
}

/* APFS/HFS can resolve a differently-cased name. Before we retain a directory
 * descriptor, enumerate its parent and require the exact requested bytes.
 * Together with ASCII-only request paths this rejects normalization/case
 * ambiguity instead of silently canonicalizing an attacker-controlled path. */
static int directory_has_exact_entry(int parent_fd, const char *name) {
  DIR *dir; struct dirent *entry; int found = 0;
  int scan_fd = openat(parent_fd, ".", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW_ANY);
  dir = scan_fd < 0 ? NULL : fdopendir(scan_fd); if (!dir) { if (scan_fd >= 0) close(scan_fd); return 0; }
  while ((entry = readdir(dir)) != NULL) {
    if (strcmp(entry->d_name, name) == 0) { found = 1; break; }
  }
  closedir(dir); return found;
}

static int is_hex_digest(const char *digest) {
  size_t i;
  if (strlen(digest) != 64) return 0;
  for (i = 0; i < 64; i++) {
    char value = digest[i];
    if (!((value >= '0' && value <= '9') || (value >= 'a' && value <= 'f'))) return 0;
  }
  return 1;
}

static int supported_root_fd(int fd, struct stat *out) {
  struct statfs fs;
  if (fstat(fd, out) < 0 || !S_ISDIR(out->st_mode) || fstatfs(fd, &fs) < 0) return 0;
  /* Network/removable/unproven semantics fail closed. */
  return strcmp(fs.f_fstypename, "apfs") == 0 || strcmp(fs.f_fstypename, "hfs") == 0;
}

static int open_root(const char *path, struct stat *out) {
  int fd = open(path, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW_ANY);
  if (fd < 0 || !supported_root_fd(fd, out)) { if (fd >= 0) close(fd); return -1; }
  return fd;
}

/* Returns a retained parent descriptor and a separately allocated leaf. */
static int open_parent(int root_fd, dev_t root_dev, const char *path, char **leaf_out) {
  char *copy, *segment, *slash, *last; int current, next; struct stat statbuf;
  if (!path_is_safe(path)) return -1;
  copy = strdup(path); if (!copy) return -1;
  last = strrchr(copy, '/');
  if (last) {
    *last++ = '\0';
    *leaf_out = strdup(last);
  } else {
    *leaf_out = strdup(copy);
    copy[0] = '\0';
  }
  if (!*leaf_out) { free(copy); return -1; }
  current = dup(root_fd); if (current < 0) goto fail;
  segment = copy;
  while (*segment) {
    slash = strchr(segment, '/'); if (slash) *slash = '\0';
    if (!directory_has_exact_entry(current, segment)) goto fail;
    next = openat(current, segment, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW_ANY);
    close(current); current = -1;
    if (next < 0 || fstat(next, &statbuf) < 0 || !S_ISDIR(statbuf.st_mode) || statbuf.st_dev != root_dev) { if (next >= 0) close(next); goto fail; }
    current = next; segment = slash ? slash + 1 : segment + strlen(segment);
  }
  free(copy); return current;
fail:
  if (current >= 0) close(current); free(copy); free(*leaf_out); *leaf_out = NULL; return -1;
}

static int snapshot_at(int parent_fd, const char *leaf, int expected_exists, int expected_kind, const char *expected_digest, struct snapshot *out) {
  struct stat statbuf; int fd;
  memset(out, 0, sizeof *out);
  if (expected_exists && !directory_has_exact_entry(parent_fd, leaf)) return 0;
  if (fstatat(parent_fd, leaf, &statbuf, AT_SYMLINK_NOFOLLOW) < 0) return errno == ENOENT && !expected_exists;
  if (!expected_exists || S_ISLNK(statbuf.st_mode) ||
      (S_ISREG(statbuf.st_mode) && statbuf.st_nlink != 1)) return 0;
  if (expected_kind == 1 && !S_ISREG(statbuf.st_mode)) return 0;
  if (expected_kind == 2 && !S_ISDIR(statbuf.st_mode)) return 0;
  if (!S_ISREG(statbuf.st_mode) && !S_ISDIR(statbuf.st_mode)) return 0;
  out->exists = 1; out->kind = S_ISREG(statbuf.st_mode) ? 1 : 2;
  out->dev = statbuf.st_dev; out->ino = statbuf.st_ino; out->mode = statbuf.st_mode; out->size = statbuf.st_size;
  if (S_ISREG(statbuf.st_mode)) {
    fd = openat(parent_fd, leaf, O_RDONLY | O_CLOEXEC | O_NOFOLLOW_ANY);
    if (fd < 0 || !regular_digest_fd(fd, out->digest, NULL)) { if (fd >= 0) close(fd); return 0; }
    close(fd);
    if (expected_digest[0] && strcmp(expected_digest, out->digest) != 0) return 0;
  } else if (expected_digest[0]) return 0;
  return 1;
}

static int snapshot_matches(int parent_fd, const char *leaf, const struct snapshot *expected) {
  struct snapshot observed;
  if (!snapshot_at(parent_fd, leaf, expected->exists, expected->kind, "", &observed)) return 0;
  if (!expected->exists) return 1;
  if (observed.dev != expected->dev || observed.ino != expected->ino || observed.mode != expected->mode || observed.size != expected->size) return 0;
  return expected->kind != 1 || strcmp(observed.digest, expected->digest) == 0;
}

static int private_dir_fd(int fd, struct stat *out) {
  if (!supported_root_fd(fd, out) || out->st_uid != geteuid() ||
      (out->st_mode & 0077) != 0) return 0;
  return 1;
}

static int make_private_run_dir(void) {
  uint8_t random[16]; struct stat stage, journal;
  if (!private_dir_fd(4, &stage) || !private_dir_fd(5, &journal)) return 0;
  staging_parent_fd = dup(4); journal_fd = dup(5);
  if (staging_parent_fd < 0 || journal_fd < 0) return 0;
  arc4random_buf(random, sizeof random);
  snprintf(staging_run_name, sizeof staging_run_name, "c2-%s", "");
  hex(random, sizeof random, staging_run_name + 3);
  if (mkdirat(staging_parent_fd, staging_run_name, 0700) < 0) return 0;
  staging_run_fd = openat(staging_parent_fd, staging_run_name,
      O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW_ANY);
  return staging_run_fd >= 0 && private_dir_fd(staging_run_fd, &stage);
}

static void journal_mac(struct journal_record *record, uint8_t out[MAC_BYTES]) {
  CCHmac(kCCHmacAlgSHA256, journal_key, KEY_BYTES, record,
      offsetof(struct journal_record, mac), out);
}

static void claim_journal_name(const char *claim, char out[80]) {
  uint8_t digest[MAC_BYTES];
  CCHmac(kCCHmacAlgSHA256, journal_key, KEY_BYTES, claim, strlen(claim), digest);
  memcpy(out, "c2c-", 4); hex(digest, sizeof digest, out + 4);
}

static int journal_store(const char *name, struct journal_record *record) {
  uint8_t random[12], mac[MAC_BYTES]; char temporary[80]; int fd;
  journal_mac(record, mac); memcpy(record->mac, mac, sizeof mac);
  arc4random_buf(random, sizeof random);
  snprintf(temporary, sizeof temporary, ".tmp-%s", "");
  hex(random, sizeof random, temporary + 5);
  fd = openat(journal_fd, temporary,
      O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW_ANY, 0600);
  if (fd < 0) return 0;
  if (write_all(fd, record, sizeof *record) || fsync(fd) < 0 || close(fd) < 0 ||
      renameat(journal_fd, temporary, journal_fd, name) < 0 || fsync(journal_fd) < 0) {
    unlinkat(journal_fd, temporary, 0); return 0;
  }
  return 1;
}

/* Claim acquisition is intentionally not implemented with a rename. A
 * deterministic claim name is shared by independently launched helpers, so a
 * replacing rename would allow two helpers to each believe they own the same
 * approved effect. O_EXCL gives the journal directory the only authority to
 * select an owner; the file and directory are both fsynced before we report
 * success. Return 1=created, 0=already exists, -1=durability/error. */
static int journal_store_no_replace(const char *name, struct journal_record *record) {
  uint8_t mac[MAC_BYTES]; int fd; int saved_errno = 0;
  journal_mac(record, mac); memcpy(record->mac, mac, sizeof mac);
  fd = openat(journal_fd, name,
      O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW_ANY, 0600);
  if (fd < 0) return errno == EEXIST ? 0 : -1;
  if (write_all(fd, record, sizeof *record) || fsync(fd) < 0) saved_errno = errno ? errno : EIO;
  if (close(fd) < 0 && !saved_errno) saved_errno = errno;
  if (!saved_errno && fsync(journal_fd) < 0) saved_errno = errno;
  if (!saved_errno) return 1;
  /* We created this name but could not prove it durable. Remove only that
   * exact newly-created entry, then fsync the removal before failing closed. */
  if (unlinkat(journal_fd, name, 0) == 0) (void)fsync(journal_fd);
  errno = saved_errno;
  return -1;
}

static int journal_load(const char *name, struct journal_record *record) {
  uint8_t expected[MAC_BYTES], difference = 0; size_t i; int fd;
  fd = openat(journal_fd, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW_ANY);
  if (fd < 0 || read_all(fd, record, sizeof *record) != 1 || close(fd) < 0) {
    if (fd >= 0) close(fd); return 0;
  }
  journal_mac(record, expected);
  for (i = 0; i < MAC_BYTES; i++) difference |= (uint8_t)(expected[i] ^ record->mac[i]);
  return memcmp(record->magic, JOURNAL_MAGIC, 7) == 0 && record->version == JOURNAL_VERSION &&
      difference == 0 && record->state >= JOURNAL_PREPARED &&
      record->state <= JOURNAL_CLEANED;
}

static struct claim *find_claim(const char *id) {
  struct claim *cursor = claim_head;
  while (cursor) { if (strcmp(cursor->id, id) == 0) return cursor; cursor = cursor->next; }
  return NULL;
}

static int index_claim(const char *id, enum outcome outcome, enum journal_state state) {
  struct claim *claim;
  if (!id[0]) return 1;
  claim = find_claim(id);
  if (claim) { claim->outcome = outcome; claim->state = state; return 1; }
  claim = calloc(1, sizeof *claim); if (!claim) return 0;
  claim->id = strdup(id); if (!claim->id) { free(claim); return 0; }
  claim->outcome = outcome; claim->state = state; claim->next = claim_head; claim_head = claim; return 1;
}

static void journal_record_for(const struct prepared *prepared,
    enum journal_state state, enum outcome outcome, const char *claim,
    int cleanup_complete, struct journal_record *record) {
  memset(record, 0, sizeof *record);
  memcpy(record->magic, JOURNAL_MAGIC, 7); record->version = JOURNAL_VERSION;
  record->state = (uint8_t)state; record->outcome = (uint8_t)outcome;
  record->cleanup_complete = cleanup_complete ? 1 : 0;
  record->entry_count = (uint16_t)prepared->entry_count;
  snprintf(record->handle, sizeof record->handle, "%s", prepared->handle);
  snprintf(record->claim, sizeof record->claim, "%s", claim ? claim : "");
  snprintf(record->stage_dir, sizeof record->stage_dir, "%s", prepared->stage_dir);
  snprintf(record->binding_digest, sizeof record->binding_digest, "%s", prepared->binding_digest);
}

static int claim_transition_allowed(enum journal_state previous,
    enum journal_state next) {
  if (previous == next) return 1;
  if (previous == JOURNAL_PREPARED)
    return next == JOURNAL_AUTHORIZED || next == JOURNAL_FAILED_BEFORE_EFFECT;
  if (previous == JOURNAL_AUTHORIZED)
    return next == JOURNAL_COMMITTING || next == JOURNAL_FAILED_BEFORE_EFFECT;
  if (previous == JOURNAL_COMMITTING)
    return next == JOURNAL_APPLIED || next == JOURNAL_INDETERMINATE;
  return 0;
}

/* Updating a claim after acquisition is permitted only for the helper whose
 * durable no-replace record names the same preparation and binding. This
 * check is not used for exclusion; O_EXCL above is the exclusion primitive.
 * It prevents a foreign helper from rewriting the winner's lifecycle. */
static int journal_claim_update_owned(const struct prepared *prepared,
    const struct journal_record *next) {
  struct journal_record current; char name[80];
  claim_journal_name(next->claim, name);
  if (!journal_load(name, &current) || strcmp(current.claim, next->claim) != 0 ||
      strcmp(current.handle, prepared->handle) != 0 ||
      strcmp(current.binding_digest, prepared->binding_digest) != 0 ||
      !claim_transition_allowed((enum journal_state)current.state,
          (enum journal_state)next->state)) return 0;
  return journal_store(name, (struct journal_record *)next);
}

static enum claim_acquire_result journal_acquire_claim(
    const struct prepared *prepared, const char *claim, enum outcome *existing_outcome) {
  struct journal_record wanted, existing; char name[80]; int created;
  /* This is deliberately PREPARED, not AUTHORIZED: the O_EXCL claim is
   * acquired before the final mutable precondition check and only graduates
   * to AUTHORIZED once that check is still true. */
  journal_record_for(prepared, JOURNAL_PREPARED, FAILED, claim, 0, &wanted);
  claim_journal_name(claim, name);
  created = journal_store_no_replace(name, &wanted);
  if (created < 0) return CLAIM_ACQUIRE_ERROR;
  if (created == 1) {
    if (!index_claim(claim, FAILED, JOURNAL_PREPARED)) return CLAIM_ACQUIRE_ERROR;
    return CLAIM_ACQUIRED;
  }
  if (!journal_load(name, &existing) || strcmp(existing.claim, claim) != 0)
    return CLAIM_ACQUIRE_ERROR;
  /* Same claim with another root/effect is a conflict, never an idempotency
   * shortcut. The caller must create a fresh approval for its own binding. */
  if (strcmp(existing.binding_digest, prepared->binding_digest) != 0)
    return CLAIM_BINDING_MISMATCH;
  if (!index_claim(existing.claim, (enum outcome)existing.outcome,
      (enum journal_state)existing.state)) return CLAIM_ACQUIRE_ERROR;
  if (existing.state == JOURNAL_APPLIED) *existing_outcome = ALREADY_APPLIED;
  else if (existing.state == JOURNAL_COMMITTING ||
      existing.state == JOURNAL_INDETERMINATE ||
      existing.state == JOURNAL_AUTHORIZED ||
      existing.state == JOURNAL_PREPARED) *existing_outcome = INDETERMINATE;
  else *existing_outcome = (enum outcome)existing.outcome;
  return CLAIM_EXISTS;
}

static int journal_transition(struct prepared *prepared, enum journal_state state,
    enum outcome outcome, const char *claim, int cleanup_complete) {
  struct journal_record record;
  journal_record_for(prepared, state, outcome, claim, cleanup_complete, &record);
  if (!journal_store(prepared->journal_name, &record)) return 0;
  if (record.claim[0]) {
    if (!journal_claim_update_owned(prepared, &record)) return 0;
  }
  if (!index_claim(record.claim, outcome, state)) return 0;
  if ((state == JOURNAL_PREPARED && test_crash_boundary == 1) ||
      (state == JOURNAL_AUTHORIZED && test_crash_boundary == 2) ||
      (state == JOURNAL_COMMITTING && test_crash_boundary == 3)) _exit(86);
  return 1;
}

static int journal_outcome_for(const struct claim *claim, enum outcome *out) {
  if (!claim) return 0;
  if (claim->state == JOURNAL_APPLIED) *out = APPLIED;
  else if (claim->state == JOURNAL_COMMITTING || claim->state == JOURNAL_INDETERMINATE) *out = INDETERMINATE;
  else *out = claim->outcome;
  return 1;
}

/* At restart we make a durable conservative decision from the last fsynced
 * boundary. We never infer/replay an effect from a missing in-memory list. */
static int journal_reconcile_startup(void) {
  DIR *dir; struct dirent *entry;
  { int scan_fd = openat(journal_fd, ".", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW_ANY);
  dir = scan_fd < 0 ? NULL : fdopendir(scan_fd); if (!dir) { if (scan_fd >= 0) close(scan_fd); return 0; } }
  while ((entry = readdir(dir)) != NULL) {
    struct journal_record record;
    if (strncmp(entry->d_name, "c2j-", 4) != 0 && strncmp(entry->d_name, "c2c-", 4) != 0) continue;
    if (!journal_load(entry->d_name, &record)) { closedir(dir); return 0; }
    if (record.state == JOURNAL_COMMITTING) {
      record.state = JOURNAL_INDETERMINATE; record.outcome = INDETERMINATE;
      if (!journal_store(entry->d_name, &record)) { closedir(dir); return 0; }
    } else if (record.state == JOURNAL_AUTHORIZED) {
      record.state = JOURNAL_FAILED_BEFORE_EFFECT; record.outcome = FAILED;
      if (!journal_store(entry->d_name, &record)) { closedir(dir); return 0; }
    } else if (record.state == JOURNAL_PREPARED) {
      /* There is provably no effect boundary before AUTHORIZED. The staged
       * directory is private/quarantined; leave it if identity cannot be
       * proven instead of deleting a replacement by name. */
      record.state = JOURNAL_FAILED_BEFORE_EFFECT; record.outcome = FAILED;
      if (!journal_store(entry->d_name, &record)) { closedir(dir); return 0; }
    }
    if (!index_claim(record.claim, (enum outcome)record.outcome,
          (enum journal_state)record.state)) { closedir(dir); return 0; }
  }
  closedir(dir); return 1;
}

/* Reconciliation is deliberately disk-backed even after startup. This makes a
 * claim result independent of an in-memory index and detects a journal that
 * was replaced/corrupted after the helper booted. */
static int journal_lookup_claim(const char *claim) {
  DIR *dir; struct dirent *entry; int found = 0; char name[80]; struct journal_record direct;
  claim_journal_name(claim, name);
  if (journal_load(name, &direct)) {
    if (strcmp(direct.claim, claim) != 0 || !index_claim(direct.claim,
        (enum outcome)direct.outcome, (enum journal_state)direct.state)) return -1;
    return 1;
  }
  { struct stat named; if (fstatat(journal_fd, name, &named, AT_SYMLINK_NOFOLLOW) == 0) return -1; }
  { int scan_fd = openat(journal_fd, ".", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW_ANY);
    dir = scan_fd < 0 ? NULL : fdopendir(scan_fd); if (!dir) { if (scan_fd >= 0) close(scan_fd); return -1; } }
  while ((entry = readdir(dir)) != NULL) {
    struct journal_record record;
    if (strncmp(entry->d_name, "c2j-", 4) != 0) continue;
    if (!journal_load(entry->d_name, &record)) { closedir(dir); return -1; }
    if (strcmp(record.claim, claim) == 0) {
      if (!index_claim(record.claim, (enum outcome)record.outcome,
          (enum journal_state)record.state)) { closedir(dir); return -1; }
      found = 1; break;
    }
  }
  closedir(dir); return found;
}

static void free_entry(struct entry *entry) {
  if (entry->stage_fd >= 0) close(entry->stage_fd);
  if (entry->parent_fd >= 0) close(entry->parent_fd);
  if (entry->destination_parent_fd >= 0) close(entry->destination_parent_fd);
  free(entry->relative_path); free(entry->leaf); free(entry->destination_relative_path); free(entry->destination_leaf);
  free(entry->slot); free(entry->expected_digest); memset(entry, 0, sizeof *entry);
  entry->parent_fd = entry->destination_parent_fd = entry->stage_fd = -1;
}

static void destroy_prepared(struct prepared *prepared) {
  uint32_t i; struct prepared **cursor = &prepared_head;
  while (*cursor && *cursor != prepared) cursor = &(*cursor)->next;
  if (*cursor == prepared) *cursor = prepared->next;
  for (i = 0; i < prepared->entry_count; i++) free_entry(&prepared->entries[i]);
  if (prepared->root_fd >= 0) close(prepared->root_fd); free(prepared->entries); free(prepared);
}

static struct prepared *find_prepared(const char *handle) {
  struct prepared *cursor = prepared_head;
  while (cursor) { if (strcmp(cursor->handle, handle) == 0) return cursor; cursor = cursor->next; }
  return NULL;
}

static int create_stage(struct prepared *prepared, struct entry *entry, uint32_t index) {
  if (entry->stage_fd >= 0 || staging_run_fd < 0) return entry->stage_fd >= 0;
  snprintf(entry->stage_name, sizeof entry->stage_name, "s-%s-%u",
      prepared->handle + 4, index);
  entry->stage_fd = openat(staging_run_fd, entry->stage_name,
      O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW_ANY, 0600);
  return entry->stage_fd >= 0;
}

/* Cleanup is capability-relative and identity-checked. If a filename was
 * replaced after staging, retain/quarantine it rather than unlinking bytes we
 * can no longer prove we created. The durable record keeps cleanup_complete=0
 * for later inspected collection. */
static int cleanup_prepared_stages(struct prepared *prepared) {
  uint32_t i;
  for (i = 0; i < prepared->entry_count; i++) {
    struct entry *entry = &prepared->entries[i]; struct stat held, named;
    if (entry->stage_fd < 0) continue;
    if (fstat(entry->stage_fd, &held) < 0) return 0;
    if (fstatat(staging_run_fd, entry->stage_name, &named, AT_SYMLINK_NOFOLLOW) < 0) {
      if (errno == ENOENT) continue;
      return 0;
    }
    if (S_ISLNK(named.st_mode) || held.st_dev != named.st_dev || held.st_ino != named.st_ino ||
        unlinkat(staging_run_fd, entry->stage_name, 0) < 0) return 0;
  }
  return fsync(staging_run_fd) == 0;
}

static int root_matches(struct prepared *prepared) {
  struct stat root; return fstat(prepared->root_fd, &root) == 0 && root.st_dev == prepared->root_dev && root.st_ino == prepared->root_ino && S_ISDIR(root.st_mode);
}

static int entry_live(const struct entry *entry) {
  if (!snapshot_matches(entry->parent_fd, entry->leaf, &entry->source)) return 0;
  return !entry->has_destination || snapshot_matches(entry->destination_parent_fd, entry->destination_leaf, &entry->destination);
}

static int sealed_stage_matches(struct entry *entry) {
  char digest[65]; struct stat observed;
  if (entry->stage_fd < 0 || !entry->sealed || fsync(entry->stage_fd) < 0 ||
      !regular_digest_fd(entry->stage_fd, digest, &observed)) return 0;
  return observed.st_dev == entry->sealed_stat.st_dev &&
      observed.st_ino == entry->sealed_stat.st_ino &&
      observed.st_size == entry->sealed_stat.st_size &&
      strcmp(digest, entry->sealed_digest) == 0 &&
      strcmp(digest, entry->expected_digest) == 0;
}

static int commit_entry(struct entry *entry) {
  if (entry->operation == CREATE) {
    /* fclonefileat consumes the retained stage FD, not a mutable filename.
     * It creates a new destination and fails if a raced target already exists;
     * The source is an FD and destination is one validated leaf beneath a
     * retained parent; CLONE_NOFOLLOW refuses a symlink destination. */
    if (!sealed_stage_matches(entry) ||
        fclonefileat(entry->stage_fd, entry->parent_fd, entry->leaf,
          CLONE_NOFOLLOW) < 0 ||
        fsync(entry->parent_fd) < 0) return 0;
  } else if (entry->operation == MKDIR) {
    if (mkdirat(entry->parent_fd, entry->leaf, 0700) < 0 || fsync(entry->parent_fd) < 0) return 0;
  } else return 0;
  return 1;
}

static void respond(uint64_t sequence, struct writer *body, uint8_t status, uint8_t failure) {
  uint8_t length[4], mac[MAC_BYTES]; CC_SHA256_CTX unused;
  (void)unused;
  struct writer payload = {0};
  if (!writer_u64(&payload, sequence) || !writer_u8(&payload, PROTOCOL) || !writer_u8(&payload, status) || (status && !writer_u8(&payload, failure))) { writer_free(&payload); return; }
  if (body->length && (!writer_reserve(&payload, body->length))) { writer_free(&payload); return; }
  if (body->length) { memcpy(payload.data + payload.length, body->data, body->length); payload.length += body->length; }
  write_be32(length, (uint32_t)payload.length);
  CCHmac(kCCHmacAlgSHA256, channel_key, KEY_BYTES, length, sizeof length, mac);
  /* HMAC includes the payload as a second update by using a tiny joined buffer. */
  {
    CC_SHA256_CTX ctx; uint8_t joined_mac[MAC_BYTES];
    /* CommonCrypto CCHmac has no incremental public context portability issue;
       allocate the bounded framed input instead. */
    uint8_t *joined = malloc(sizeof length + payload.length);
    if (!joined) { writer_free(&payload); return; }
    memcpy(joined, length, sizeof length); memcpy(joined + sizeof length, payload.data, payload.length);
    CCHmac(kCCHmacAlgSHA256, channel_key, KEY_BYTES, joined, sizeof length + payload.length, joined_mac);
    free(joined); memcpy(mac, joined_mac, MAC_BYTES); (void)ctx;
  }
  if (write_all(STDOUT_FILENO, length, sizeof length) || write_all(STDOUT_FILENO, mac, sizeof mac) || write_all(STDOUT_FILENO, payload.data, payload.length)) { }
  writer_free(&payload);
}

static int parse_entry(struct reader *reader, struct prepared *prepared, uint32_t index, struct entry *entry) {
  uint8_t operation, has_dest, exists, kind, has_content; char *expected = NULL;
  memset(entry, 0, sizeof *entry); entry->parent_fd = entry->destination_parent_fd = entry->stage_fd = -1;
  if (!reader_u8(reader, &operation) || operation < CREATE || operation > MKDIR || !(entry->relative_path = reader_string(reader, MAX_PATH_BYTES)) || !reader_u8(reader, &has_dest)) goto fail;
  entry->operation = (enum operation)operation;
  /* macOS exposes atomic no-replace creation but no kernel compare-and-swap
     rename bound to an observed inode+digest. Replace/delete/move would have
     an uncloseable external-write race, so this helper refuses them instead of
     pretending advisory locks are a security primitive. */
  if (entry->operation != CREATE && entry->operation != MKDIR) goto fail;
  if (has_dest) { if (!(entry->destination_relative_path = reader_string(reader, MAX_PATH_BYTES))) goto fail; }
  if (!reader_u8(reader, &exists) || !reader_u8(reader, &kind) || !(expected = reader_string(reader, 64))) goto fail;
  if (!path_is_safe(entry->relative_path) || (entry->destination_relative_path && !path_is_safe(entry->destination_relative_path)) || kind > 2 || strlen(expected) > 64) goto fail;
  entry->parent_fd = open_parent(prepared->root_fd, prepared->root_dev, entry->relative_path, &entry->leaf); if (entry->parent_fd < 0) goto fail;
  if (!snapshot_at(entry->parent_fd, entry->leaf, exists, kind, expected, &entry->source)) goto fail;
  if (entry->operation == MOVE) {
    if (!entry->destination_relative_path) goto fail;
    entry->destination_parent_fd = open_parent(prepared->root_fd, prepared->root_dev, entry->destination_relative_path, &entry->destination_leaf); if (entry->destination_parent_fd < 0 || !snapshot_at(entry->destination_parent_fd, entry->destination_leaf, 0, 0, "", &entry->destination)) goto fail;
    entry->has_destination = 1;
  } else if (entry->destination_relative_path) goto fail;
  if ((entry->operation == CREATE || entry->operation == MKDIR) && entry->source.exists) goto fail;
  if ((entry->operation == REPLACE || entry->operation == DELETE || entry->operation == MOVE) && !entry->source.exists) goto fail;
  if (entry->operation == REPLACE && entry->source.kind != 1) goto fail;
  if (!reader_u8(reader, &has_content)) goto fail;
  if (has_content) {
    if (!(entry->slot = reader_string(reader, MAX_SLOT_BYTES)) || !(entry->expected_digest = reader_string(reader, 64)) || !reader_u64(reader, &entry->expected_size) || !is_hex_digest(entry->expected_digest)) goto fail;
  }
  if ((entry->operation == CREATE || entry->operation == REPLACE) != (has_content != 0)) goto fail;
  (void)prepared; (void)index; /* private stages are allocated after handle creation */
  free(expected); return 1;
fail:
  free(expected); free_entry(entry); return 0;
}

static int disjoint_entries(struct entry *entries, uint32_t count) {
  uint32_t i, j;
  for (i = 0; i < count; i++) for (j = i + 1; j < count; j++) {
    if (strcmp(entries[i].relative_path, entries[j].relative_path) == 0 ||
        (entries[i].destination_relative_path && strcmp(entries[i].destination_relative_path, entries[j].relative_path) == 0) ||
        (entries[j].destination_relative_path && strcmp(entries[i].relative_path, entries[j].destination_relative_path) == 0) ||
        (entries[i].destination_relative_path && entries[j].destination_relative_path && strcmp(entries[i].destination_relative_path, entries[j].destination_relative_path) == 0)) return 0;
  }
  return 1;
}

static void command_root_identity(struct reader *reader, struct writer *out, uint8_t *failure) {
  char *path = reader_string(reader, MAX_PATH_BYTES); struct stat root; int fd; char volume[32], file[32];
  if (!path || reader->offset != reader->length) { free(path); *failure = INVALID; return; }
  fd = open_root(path, &root); free(path); if (fd < 0) { *failure = UNSUPPORTED; return; } close(fd);
  snprintf(volume, sizeof volume, "%llu", (unsigned long long)root.st_dev); snprintf(file, sizeof file, "%llu", (unsigned long long)root.st_ino);
  if (!writer_string(out, volume) || !writer_string(out, file)) *failure = INTERNAL;
}

static void command_prepare(struct reader *reader, struct writer *out, uint8_t *failure) {
  char *root_path = reader_string(reader, MAX_PATH_BYTES); uint32_t count, i; struct stat root, stage; struct prepared *prepared = NULL; uint8_t random[16]; char digest[65]; CC_SHA256_CTX digest_ctx;
  if (!root_path || !reader_u32(reader, &count) || count == 0 || count > MAX_ENTRIES) { free(root_path); *failure = INVALID; return; }
  prepared = calloc(1, sizeof *prepared); if (!prepared) { free(root_path); *failure = INTERNAL; return; }
  prepared->root_fd = open_root(root_path, &root); free(root_path); if (prepared->root_fd < 0) { free(prepared); *failure = UNSUPPORTED; return; }
  if (staging_run_fd < 0 || fstat(staging_run_fd, &stage) < 0 || stage.st_dev != root.st_dev) { destroy_prepared(prepared); *failure = UNSUPPORTED; return; }
  prepared->root_dev = root.st_dev; prepared->root_ino = root.st_ino; prepared->entry_count = count; prepared->entries = calloc(count, sizeof *prepared->entries);
  if (!prepared->entries) { destroy_prepared(prepared); *failure = INTERNAL; return; }
  for (i = 0; i < count; i++) if (!parse_entry(reader, prepared, i, &prepared->entries[i])) { destroy_prepared(prepared); *failure = CONFLICT; return; }
  if (reader->offset != reader->length || !disjoint_entries(prepared->entries, count)) { destroy_prepared(prepared); *failure = CONFLICT; return; }
  arc4random_buf(random, sizeof random); strcpy(prepared->handle, "nwh_"); hex(random, sizeof random, prepared->handle + 4);
  snprintf(prepared->journal_name, sizeof prepared->journal_name, "c2j-%s", prepared->handle + 4);
  snprintf(prepared->stage_dir, sizeof prepared->stage_dir, "%s", staging_run_name);
  if (!compute_prepared_binding(prepared)) { destroy_prepared(prepared); *failure = INTERNAL; return; }
  /* Recreate deterministic private stage names only after the handle exists. */
  for (i = 0; i < count; i++) if (prepared->entries[i].slot &&
      !create_stage(prepared, &prepared->entries[i], i)) { destroy_prepared(prepared); *failure = INTERNAL; return; }
  CC_SHA256_Init(&digest_ctx); for (i = 0; i < count; i++) CC_SHA256_Update(&digest_ctx, prepared->entries[i].relative_path, (CC_LONG)strlen(prepared->entries[i].relative_path)); { uint8_t raw[CC_SHA256_DIGEST_LENGTH]; CC_SHA256_Final(raw, &digest_ctx); hex(raw, sizeof raw, digest); }
  if (!journal_transition(prepared, JOURNAL_PREPARED, FAILED, "", 0)) { destroy_prepared(prepared); *failure = INTERNAL; return; }
  if (!writer_string(out, prepared->handle) || !writer_string(out, digest)) { destroy_prepared(prepared); *failure = INTERNAL; return; }
  { uint32_t slots = 0; for (i = 0; i < count; i++) if (prepared->entries[i].slot) slots++; if (!writer_u32(out, slots)) { destroy_prepared(prepared); *failure = INTERNAL; return; }
    for (i = 0; i < count; i++) if (prepared->entries[i].slot && (!writer_string(out, prepared->entries[i].slot) || !writer_string(out, prepared->entries[i].expected_digest) || !writer_u64(out, prepared->entries[i].expected_size))) { destroy_prepared(prepared); *failure = INTERNAL; return; }
  }
  prepared->next = prepared_head; prepared_head = prepared;
}

static struct entry *find_slot(struct prepared *prepared, const char *slot) { uint32_t i; for (i = 0; i < prepared->entry_count; i++) if (prepared->entries[i].slot && strcmp(prepared->entries[i].slot, slot) == 0) return &prepared->entries[i]; return NULL; }

static void command_write(struct reader *reader, uint8_t *failure) {
  char *handle = reader_string(reader, 80), *slot = reader_string(reader, MAX_SLOT_BYTES); const uint8_t *data; uint32_t length; struct prepared *prepared; struct entry *entry; size_t offset = 0;
  if (!handle || !slot || !reader_bytes(reader, &data, &length) || reader->offset != reader->length) { free(handle); free(slot); *failure = INVALID; return; }
  prepared = find_prepared(handle); entry = prepared ? find_slot(prepared, slot) : NULL; free(handle); free(slot);
  if (!entry || entry->sealed || (uint64_t)length > entry->expected_size - entry->bytes_written) { *failure = CONFLICT; return; }
  while (offset < length) { ssize_t written = write(entry->stage_fd, data + offset, length - offset); if (written <= 0) { *failure = INTERNAL; return; } offset += (size_t)written; }
  entry->bytes_written += length;
}

static void command_seal(struct reader *reader, struct writer *out, uint8_t *failure) {
  char *handle = reader_string(reader, 80), *slot = reader_string(reader, MAX_SLOT_BYTES), digest[65]; struct prepared *prepared; struct entry *entry; struct stat statbuf;
  if (!handle || !slot || reader->offset != reader->length) { free(handle); free(slot); *failure = INVALID; return; }
  prepared = find_prepared(handle); entry = prepared ? find_slot(prepared, slot) : NULL; free(handle); free(slot);
  if (!entry || entry->sealed || entry->stage_fd < 0 || entry->bytes_written != entry->expected_size || !regular_digest_fd(entry->stage_fd, digest, &statbuf) || fsync(entry->stage_fd) < 0 || (uint64_t)statbuf.st_size != entry->expected_size || strcmp(digest, entry->expected_digest) != 0) { *failure = CONFLICT; return; }
  entry->sealed_stat = statbuf; snprintf(entry->sealed_digest, sizeof entry->sealed_digest, "%s", digest);
  entry->sealed = 1; if (!writer_string(out, digest) || !writer_u64(out, (uint64_t)statbuf.st_size)) *failure = INTERNAL;
}

static void write_commit_result(struct writer *out, enum outcome outcome, const char *claim) {
  char receipt[240]; snprintf(receipt, sizeof receipt, "workspace-receipt://%s", claim);
  writer_u8(out, (uint8_t)outcome); writer_string(out, receipt); writer_string(out, ""); writer_string(out, outcome == INDETERMINATE ? "The workspace change outcome could not be confirmed." : "");
}

static void command_commit(struct reader *reader, struct writer *out, uint8_t *failure) {
  char *handle = reader_string(reader, 80), *claim = reader_string(reader, MAX_CLAIM_BYTES); struct prepared *prepared; struct claim *existing; uint32_t i; enum outcome result = APPLIED, prior; enum claim_acquire_result acquired;
  if (!handle || !claim || reader->offset != reader->length) { free(handle); free(claim); *failure = INVALID; return; }
  prepared = find_prepared(handle);
  /* A repeat after its original transaction was released is a read-only
   * recovery query. It must never reconstruct a write from an old handle. */
  if (!prepared) {
    existing = find_claim(claim);
    if (!existing && journal_lookup_claim(claim) < 0) { free(handle); free(claim); *failure = INTERNAL; return; }
    existing = find_claim(claim);
    if (existing) { enum outcome known; journal_outcome_for(existing, &known); write_commit_result(out, known == APPLIED ? ALREADY_APPLIED : known, claim); }
    else write_commit_result(out, INDETERMINATE, claim);
    free(handle); free(claim); return;
  }
  if (!root_matches(prepared)) { write_commit_result(out, INDETERMINATE, claim); free(handle); free(claim); return; }
  /* Acquire the claim before observing mutable target entries. Otherwise a
   * loser that arrives just after the winner's create could report a local
   * drift rather than the winner's durable claim outcome. */
  acquired = journal_acquire_claim(prepared, claim, &prior);
  if (acquired == CLAIM_BINDING_MISMATCH) { free(handle); free(claim); *failure = CONFLICT; return; }
  if (acquired == CLAIM_ACQUIRE_ERROR) { free(handle); free(claim); *failure = INTERNAL; return; }
  if (acquired == CLAIM_EXISTS) { write_commit_result(out, prior, claim); free(handle); free(claim); return; }
  for (i = 0; i < prepared->entry_count; i++) if (!entry_live(&prepared->entries[i]) || ((prepared->entries[i].operation == CREATE || prepared->entries[i].operation == REPLACE) && !prepared->entries[i].sealed)) {
    journal_transition(prepared, JOURNAL_FAILED_BEFORE_EFFECT, PRECONDITION_DRIFT, claim, 0);
    if (cleanup_prepared_stages(prepared)) journal_transition(prepared, JOURNAL_FAILED_BEFORE_EFFECT, PRECONDITION_DRIFT, claim, 1);
    write_commit_result(out, PRECONDITION_DRIFT, claim); free(handle); free(claim); return;
  }
  if (!journal_transition(prepared, JOURNAL_AUTHORIZED, FAILED, claim, 0) ||
      !journal_transition(prepared, JOURNAL_COMMITTING, INDETERMINATE, claim, 0)) {
    write_commit_result(out, INDETERMINATE, claim); free(handle); free(claim); return;
  }
  for (i = 0; i < prepared->entry_count; i++) if (!commit_entry(&prepared->entries[i])) { result = INDETERMINATE; break; }
  if (result == APPLIED) {
    /* Crash fault boundary: the durable COMMITTING row remains the only
     * evidence after an effect that completed before APPLIED was fsynced. */
    if (test_crash_boundary == 4) _exit(86);
    if (!journal_transition(prepared, JOURNAL_APPLIED, APPLIED, claim, 0)) result = INDETERMINATE;
    else if (cleanup_prepared_stages(prepared)) journal_transition(prepared, JOURNAL_APPLIED, APPLIED, claim, 1);
  } else {
    /* The durable COMMITTING record remains proof that an effect might have
       * crossed its boundary. Marking indeterminate is conservative and is
       * never retried automatically. */
    journal_transition(prepared, JOURNAL_INDETERMINATE, INDETERMINATE, claim, 0);
  }
  write_commit_result(out, result, claim); destroy_prepared(prepared); free(handle); free(claim);
}

static void command_reconcile_claim(struct reader *reader, struct writer *out, uint8_t *failure) {
  char *claim = reader_string(reader, MAX_CLAIM_BYTES); struct claim *existing;
  if (!claim || reader->offset != reader->length) { free(claim); *failure = INVALID; return; }
  existing = find_claim(claim);
  if (!existing && journal_lookup_claim(claim) < 0) { free(claim); *failure = INTERNAL; return; }
  existing = find_claim(claim);
  if (existing) { enum outcome known; journal_outcome_for(existing, &known); write_commit_result(out, known == APPLIED ? ALREADY_APPLIED : known, claim); }
  else write_commit_result(out, INDETERMINATE, claim);
  free(claim);
}

static void command_abort_or_recovery(struct reader *reader, struct writer *out, uint8_t *failure, int recovery) {
  char *value = reader_string(reader, recovery ? MAX_CLAIM_BYTES : 80); struct prepared *prepared;
  if (!value || reader->offset != reader->length) { free(value); *failure = INVALID; return; }
  if (!recovery) { prepared = find_prepared(value); if (prepared) { journal_transition(prepared, JOURNAL_FAILED_BEFORE_EFFECT, FAILED, "", 0); if (cleanup_prepared_stages(prepared)) journal_transition(prepared, JOURNAL_FAILED_BEFORE_EFFECT, FAILED, "", 1); destroy_prepared(prepared); } }
  else { /* No automatic undo after a crash or external edit: always a conflict. */ if (!writer_u8(out, 0)) *failure = INTERNAL; }
  free(value);
}

static int verify_frame(const uint8_t length_bytes[4], const uint8_t mac[MAC_BYTES], const uint8_t *payload, uint32_t length) {
  uint8_t *joined, expected[MAC_BYTES]; int ok = 0; size_t i; uint8_t difference = 0;
  joined = malloc((size_t)4 + length); if (!joined) return 0;
  memcpy(joined, length_bytes, 4); memcpy(joined + 4, payload, length);
  CCHmac(kCCHmacAlgSHA256, channel_key, KEY_BYTES, joined, (size_t)4 + length, expected); free(joined);
  for (i = 0; i < MAC_BYTES; i++) difference |= (uint8_t)(expected[i] ^ mac[i]);
  ok = difference == 0; memset(expected, 0, sizeof expected); return ok;
}

int main(void) {
  uint8_t length_bytes[4], mac[MAC_BYTES]; uint64_t expected_sequence = 1;
  struct stat private_dir;
  if (read_all(3, channel_key, KEY_BYTES) != 1 || read_all(6, journal_key, KEY_BYTES) != 1) return 1;
  if (fcntl(7, F_GETFD) >= 0 && read_all(7, &test_crash_boundary, 1) != 1) return 1;
  close(3); close(6); if (fcntl(7, F_GETFD) >= 0) close(7); close(STDERR_FILENO);
  if (!make_private_run_dir() || fstat(staging_run_fd, &private_dir) < 0 ||
      !private_dir_fd(journal_fd, &private_dir) || !journal_reconcile_startup()) return 1;
  while (1) {
    uint32_t length; uint8_t *payload; struct reader reader; struct writer out = {0}; uint8_t version, request_type, failure = 0; uint64_t sequence; int read_result;
    read_result = read_all(STDIN_FILENO, length_bytes, sizeof length_bytes); if (read_result != 1) break;
    length = read_be32(length_bytes); if (length > MAX_FRAME || read_all(STDIN_FILENO, mac, sizeof mac) != 1) break;
    payload = malloc(length ? length : 1); if (!payload || read_all(STDIN_FILENO, payload, length) != 1 || !verify_frame(length_bytes, mac, payload, length)) { free(payload); break; }
    reader.data = payload; reader.length = length; reader.offset = 0;
    if (!reader_u64(&reader, &sequence) || sequence != expected_sequence++ ||
        !reader_u8(&reader, &version) || !reader_u8(&reader, &request_type) || version != PROTOCOL) { free(payload); break; }
    else switch (request_type) {
      case ROOT_IDENTITY: command_root_identity(&reader, &out, &failure); break;
      case PREPARE: command_prepare(&reader, &out, &failure); break;
      case WRITE: command_write(&reader, &failure); break;
      case SEAL: command_seal(&reader, &out, &failure); break;
      case COMMIT: case RECONCILE_PREPARED: command_commit(&reader, &out, &failure); break;
      case RECONCILE_CLAIM: command_reconcile_claim(&reader, &out, &failure); break;
      case ABORT: command_abort_or_recovery(&reader, &out, &failure, 0); break;
      case PROPOSE_RECOVERY: case PROPOSE_RECOVERY_CLAIM: command_abort_or_recovery(&reader, &out, &failure, 1); break;
      case PING: if (reader.offset != reader.length) failure = INVALID; break;
      case CLOSE_HELPER: if (reader.offset != reader.length) failure = INVALID; else { respond(sequence, &out, 0, 0); writer_free(&out); free(payload); return 0; } break;
      default: failure = INVALID; break;
    }
    respond(sequence, &out, failure ? 1 : 0, failure); writer_free(&out); free(payload);
  }
  return 0;
}
