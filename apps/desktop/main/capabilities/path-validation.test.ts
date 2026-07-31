// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  assertGrantableRoot,
  assertWithinRoot,
  classifyForbiddenRoot,
  FORBIDDEN_ROOT_MESSAGES,
  FS_LIMITS,
  FsError,
  type ForbiddenRootReason,
  modeSatisfies,
  normalizeVirtualPath,
  segmentIsSensitiveDir,
  virtualPathTraversesSensitiveDir,
} from "./path-validation";

// Control / confusable codepoints are spelled with \u escapes so NO literal
// control character is embedded in this source file (mirrors folder-picker's
// String.fromCharCode discipline).
const NUL = "\u0000";
const BELL = "\u0007";
const FULLWIDTH_SOLIDUS = "／"; // ／  NFKC → "/"
const FULLWIDTH_DOT = "．"; // ．  NFKC → "."
const LONE_SURROGATE = "\uD800"; // unpaired high surrogate

function code(fn: () => unknown): string | "no-throw" {
  try {
    fn();
    return "no-throw";
  } catch (err) {
    return err instanceof FsError ? err.code : `other:${String(err)}`;
  }
}

describe("normalizeVirtualPath — happy paths", () => {
  it("splits an ordinary relative path into segments", () => {
    expect(normalizeVirtualPath("a/b/c")).toEqual(["a", "b", "c"]);
  });
  it("treats empty / '.' / '/'-collapsed input as the grant root", () => {
    expect(normalizeVirtualPath("")).toEqual([]);
    expect(normalizeVirtualPath(".")).toEqual([]);
    expect(normalizeVirtualPath("./")).toEqual([]);
  });
  it("drops '.' no-op segments and collapses repeated separators", () => {
    expect(normalizeVirtualPath("a/./b")).toEqual(["a", "b"]);
    expect(normalizeVirtualPath("a//b")).toEqual(["a", "b"]);
  });
  it("accepts backslash as a separator too", () => {
    expect(normalizeVirtualPath("a\\b")).toEqual(["a", "b"]);
  });
  it("accepts ordinary names with spaces and dots inside", () => {
    expect(normalizeVirtualPath("My Notes/report.v2.txt")).toEqual([
      "My Notes",
      "report.v2.txt",
    ]);
  });
});

describe("normalizeVirtualPath — adversarial rejections", () => {
  it("rejects a NUL byte", () => {
    expect(code(() => normalizeVirtualPath(`a${NUL}b`))).toBe("invalid_path");
  });
  it("rejects a C0 control character", () => {
    expect(code(() => normalizeVirtualPath(`a${BELL}b`))).toBe("invalid_path");
  });
  it("rejects absolute POSIX paths", () => {
    expect(code(() => normalizeVirtualPath("/etc/passwd"))).toBe(
      "invalid_path",
    );
  });
  it("rejects UNC and backslash-absolute paths", () => {
    expect(code(() => normalizeVirtualPath("\\\\server\\share"))).toBe(
      "invalid_path",
    );
  });
  it("rejects Windows drive-letter paths", () => {
    expect(code(() => normalizeVirtualPath("C:\\Windows"))).toBe(
      "invalid_path",
    );
    expect(code(() => normalizeVirtualPath("c:/Windows"))).toBe("invalid_path");
  });
  it("rejects .. traversal in every position", () => {
    expect(code(() => normalizeVirtualPath(".."))).toBe("invalid_path");
    expect(code(() => normalizeVirtualPath("a/../b"))).toBe("invalid_path");
    expect(code(() => normalizeVirtualPath("a/.."))).toBe("invalid_path");
    expect(code(() => normalizeVirtualPath("../etc"))).toBe("invalid_path");
  });
  it("rejects a Unicode-confusable separator (fullwidth solidus)", () => {
    expect(code(() => normalizeVirtualPath(`a${FULLWIDTH_SOLIDUS}b`))).toBe(
      "invalid_path",
    );
  });
  it("rejects a Unicode-confusable '..' (fullwidth full stops)", () => {
    expect(
      code(() => normalizeVirtualPath(`${FULLWIDTH_DOT}${FULLWIDTH_DOT}`)),
    ).toBe("invalid_path");
  });
  it("rejects a lone surrogate (bad encoding)", () => {
    expect(code(() => normalizeVirtualPath(`a${LONE_SURROGATE}b`))).toBe(
      "invalid_path",
    );
  });
  it("rejects Windows reserved device names (with and without extension)", () => {
    for (const name of ["CON", "nul", "com3", "LPT9", "nul.txt", "COM1.log"]) {
      expect(code(() => normalizeVirtualPath(name))).toBe("invalid_path");
    }
  });
  it("rejects a reserved device name in an interior segment", () => {
    expect(code(() => normalizeVirtualPath("a/PRN/b"))).toBe("invalid_path");
  });
  it("rejects alternate-data-stream / colon segments", () => {
    expect(code(() => normalizeVirtualPath("file.txt:stream"))).toBe(
      "invalid_path",
    );
    expect(code(() => normalizeVirtualPath("a/b:c"))).toBe("invalid_path");
  });
  it("rejects trailing dot or space (Windows silently strips them)", () => {
    expect(code(() => normalizeVirtualPath("secret."))).toBe("invalid_path");
    expect(code(() => normalizeVirtualPath("secret "))).toBe("invalid_path");
    expect(code(() => normalizeVirtualPath("a/b./c"))).toBe("invalid_path");
  });
  it("rejects a non-string input", () => {
    expect(code(() => normalizeVirtualPath(42 as unknown))).toBe(
      "invalid_path",
    );
    expect(code(() => normalizeVirtualPath(null))).toBe("invalid_path");
  });
  it("rejects a path deeper than the depth ceiling", () => {
    const deep = Array.from(
      { length: FS_LIMITS.maxPathDepth + 1 },
      () => "x",
    ).join("/");
    expect(code(() => normalizeVirtualPath(deep))).toBe("invalid_path");
  });
  it("rejects an over-long segment", () => {
    expect(code(() => normalizeVirtualPath("x".repeat(256)))).toBe(
      "invalid_path",
    );
  });
  it("never echoes the offending input in the error message", () => {
    try {
      normalizeVirtualPath("/Users/secret-person/private");
      throw new Error("should have thrown");
    } catch (err) {
      expect((err as Error).message).not.toContain("secret-person");
    }
  });
});

describe("assertWithinRoot", () => {
  it("allows the root itself and any descendant", () => {
    expect(() => assertWithinRoot("/grant/root", "/grant/root")).not.toThrow();
    expect(() =>
      assertWithinRoot("/grant/root", "/grant/root/a/b"),
    ).not.toThrow();
  });
  it("rejects a sibling prefix (the /root vs /root-evil trap)", () => {
    expect(
      code(() => assertWithinRoot("/grant/root", "/grant/root-evil")),
    ).toBe("permission_denied");
  });
  it("rejects an unrelated path and a parent path", () => {
    expect(code(() => assertWithinRoot("/grant/root", "/etc/passwd"))).toBe(
      "permission_denied",
    );
    expect(code(() => assertWithinRoot("/grant/root", "/grant"))).toBe(
      "permission_denied",
    );
  });
});

describe("modeSatisfies (fail-closed grant gate)", () => {
  it("read_only is satisfied by every mode", () => {
    expect(modeSatisfies("read_only", "read_only")).toBe(true);
    expect(modeSatisfies("read_only", "read_write_no_delete")).toBe(true);
    expect(modeSatisfies("read_only", "read_write")).toBe(true);
  });
  it("a higher required mode denies a lower grant", () => {
    expect(modeSatisfies("read_write", "read_only")).toBe(false);
    expect(modeSatisfies("read_write_no_delete", "read_only")).toBe(false);
    expect(modeSatisfies("read_write", "read_write_no_delete")).toBe(false);
  });
  it("an unknown mode never satisfies anything (fail closed)", () => {
    expect(modeSatisfies("read_only", "bogus")).toBe(false);
    expect(modeSatisfies("bogus", "read_write")).toBe(false);
  });
});

describe("virtualPathTraversesSensitiveDir (G2, nested credential dirs)", () => {
  it("flags any segment naming a well-known credential directory", () => {
    expect(virtualPathTraversesSensitiveDir([".ssh"])).toBe(true);
    expect(virtualPathTraversesSensitiveDir(["a", ".aws", "config"])).toBe(
      true,
    );
    expect(virtualPathTraversesSensitiveDir(["a", ".gnupg", "x.gpg"])).toBe(
      true,
    );
    expect(virtualPathTraversesSensitiveDir(["Library", "Keychains"])).toBe(
      true,
    );
  });
  it("is case-insensitive (host fs may be case-insensitive)", () => {
    expect(virtualPathTraversesSensitiveDir([".SSH"])).toBe(true);
    expect(virtualPathTraversesSensitiveDir(["a", ".AWS"])).toBe(true);
  });
  it("allows ordinary paths that merely resemble a credential dir", () => {
    expect(virtualPathTraversesSensitiveDir([])).toBe(false);
    expect(virtualPathTraversesSensitiveDir(["src", "notes.txt"])).toBe(false);
    // Substring, not a whole segment — not a credential directory.
    expect(virtualPathTraversesSensitiveDir(["my.ssh-notes"])).toBe(false);
    expect(virtualPathTraversesSensitiveDir(["sshconfig"])).toBe(false);
  });
});

describe("segmentIsSensitiveDir (single leaf name)", () => {
  it("matches a credential dir leaf, case-insensitively", () => {
    expect(segmentIsSensitiveDir(".ssh")).toBe(true);
    expect(segmentIsSensitiveDir(".AWS")).toBe(true);
    expect(segmentIsSensitiveDir("keychains")).toBe(true);
  });
  it("does not match ordinary names", () => {
    expect(segmentIsSensitiveDir("src")).toBe(false);
    expect(segmentIsSensitiveDir("ssh")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// classifyForbiddenRoot — the grant denylist.
//
// It used to be exercised only through the native folder DIALOG, where the
// worst realistic input is a folder someone picked by accident. The mid-run
// "always allow" card changed the producer: the folder is now NAMED BY THE
// MODEL, printed on a card, and attached with one click and no OS dialog. So
// the question these tests ask is not "what might a person pick by mistake" but
// "what could a model name that a hurried person would approve" — which is a
// wider set, and most of it reads as innocuous.
//
// Every case below FAILS before the denylist was extended: each one was granted.
// ---------------------------------------------------------------------------

const HOME = "/Users/alice";
const USER_DATA = "/Users/alice/Library/Application Support/copilot";
const CTX = { homeDir: HOME, userDataDir: USER_DATA };

function reasonFor(root: string): ForbiddenRootReason | null {
  return classifyForbiddenRoot(root, CTX);
}

describe("classifyForbiddenRoot — system config and binaries", () => {
  it("refuses an OS-owned top-level tree, and everything under it", () => {
    for (const root of [
      "/etc",
      "/etc/ssl/private",
      "/var",
      "/var/log",
      "/usr",
      "/usr/local/bin",
      "/bin",
      "/sbin",
      "/opt/homebrew",
      "/dev/disk1",
      "/proc/self",
      "/sys/kernel",
      "/boot",
      "/root",
      "/System/Library",
      "/Library/Preferences",
    ]) {
      expect(reasonFor(root)).toBe("system_directory");
    }
  });

  it("refuses the macOS firmlink spelling of the same directories", () => {
    // `/etc` IS `/private/etc` — a denylist that knows only one spelling knows
    // neither, and `/private/…` is the one that survives a `realpath`.
    expect(reasonFor("/private/etc")).toBe("system_directory");
    expect(reasonFor("/private/var/db")).toBe("system_directory");
    expect(reasonFor("/private/tmp")).toBe("system_directory");
  });

  it("refuses a world-writable scratch tree", () => {
    // The cheapest place on the machine for anything at all to plant a folder
    // worth naming on a card.
    expect(reasonFor("/tmp")).toBe("system_directory");
    expect(reasonFor("/tmp/quarterly-reports")).toBe("system_directory");
  });

  it("refuses the Windows system trees, drive letter and all", () => {
    expect(reasonFor("C:\\Windows\\System32")).toBe("system_directory");
    expect(reasonFor("C:\\Program Files\\Thing")).toBe("system_directory");
    expect(reasonFor("C:\\ProgramData")).toBe("system_directory");
    expect(reasonFor("D:\\$Recycle.Bin")).toBe("system_directory");
  });

  it("still allows an ordinary folder whose NAME merely resembles one", () => {
    // First segment, not any segment: refusing `…/var` anywhere would take a
    // project's own `var` directory with it.
    expect(reasonFor("/Users/alice/project/var")).toBeNull();
    expect(reasonFor("/Users/alice/etc")).toBeNull();
    expect(reasonFor("D:\\Projects\\windows-build")).toBeNull();
  });

  it("exempts a home directory that lives inside one", () => {
    // A container or service account can legitimately be homed at
    // `/var/lib/<app>`; refusing every folder in that person's own home would
    // leave them with no grantable folder at all.
    const ctx = { homeDir: "/var/lib/copilot", userDataDir: "/var/lib/x/ud" };
    expect(classifyForbiddenRoot("/var/lib/copilot/reports", ctx)).toBeNull();
    // …and the rest of `/var` is refused exactly as before.
    expect(classifyForbiddenRoot("/var/log", ctx)).toBe("system_directory");
  });
});

describe("classifyForbiddenRoot — application bundles", () => {
  it("refuses the applications folder and any bundle inside it", () => {
    expect(reasonFor("/Applications")).toBe("application_bundle");
    expect(reasonFor("/Applications/Mail.app/Contents")).toBe(
      "application_bundle",
    );
  });

  it("refuses a bundle in the user's OWN home too", () => {
    // Inside home, so no other rule sees it — and it is still executable code
    // rather than a folder of documents.
    expect(reasonFor("/Users/alice/Applications/Thing.app")).toBe(
      "application_bundle",
    );
  });
});

describe("classifyForbiddenRoot — other accounts", () => {
  it("refuses another user's home, and anything inside it", () => {
    expect(reasonFor("/Users/bob")).toBe("other_user_home");
    expect(reasonFor("/Users/bob/Documents/2024")).toBe("other_user_home");
  });

  it("derives the rule from homeDir, not from a list of home-parent names", () => {
    const ctx = { homeDir: "/home/ada", userDataDir: "/home/ada/.ud" };
    expect(classifyForbiddenRoot("/home/kai/notes", ctx)).toBe(
      "other_user_home",
    );
    expect(classifyForbiddenRoot("/home/ada/notes", ctx)).toBeNull();
  });

  it("leaves this user's own folders alone", () => {
    expect(reasonFor("/Users/alice/Documents")).toBeNull();
  });
});

describe("classifyForbiddenRoot — volumes and devices", () => {
  it("refuses a mount parent and a whole volume, but not a folder on one", () => {
    expect(reasonFor("/Volumes")).toBe("volume_root");
    expect(reasonFor("/Volumes/Backup")).toBe("volume_root");
    expect(reasonFor("/Volumes/Backup/Invoices")).toBeNull();
    expect(reasonFor("/mnt/data")).toBe("volume_root");
    expect(reasonFor("/media/usb")).toBe("volume_root");
  });

  it("refuses a whole network share, but not a folder on one", () => {
    expect(reasonFor("\\\\server\\share")).toBe("volume_root");
    expect(reasonFor("\\\\server\\share\\reports")).toBeNull();
  });

  it("refuses a device namespace", () => {
    expect(reasonFor("\\\\.\\PhysicalDrive0")).toBe("device_path");
    expect(reasonFor("\\\\?\\Volume{9d2f}\\")).toBe("device_path");
    // The extended-length spelling of an ordinary path is refused too: it is a
    // second spelling for a folder that already has one, and a second spelling
    // is how a denylist gets walked around.
    expect(reasonFor("\\\\?\\C:\\Users\\alice")).toBe("device_path");
  });
});

describe("classifyForbiddenRoot — an unexpanded home path", () => {
  it("refuses `~` rather than letting it resolve against anything", () => {
    // Nothing in this process expands `~`, so a card showing `~/Downloads` and
    // a grant made from it are two different folders by construction.
    expect(reasonFor("~")).toBe("home_directory");
    expect(reasonFor("~/Downloads")).toBe("home_directory");
    expect(reasonFor("~alice/Downloads")).toBe("home_directory");
  });
});

describe("classifyForbiddenRoot — installed apps' saved state", () => {
  it("refuses the per-platform application-state trees under home", () => {
    // A NEIGHBOUR of this app's own userData, so the answer comes from the
    // app-state rule rather than from the userData rule that already refuses
    // `~/Library` as one of its ancestors.
    expect(reasonFor("/Users/alice/Library/Application Support/Slack")).toBe(
      "application_state_directory",
    );
    // With userData elsewhere, the tree itself is still refused on its own.
    const mac = { homeDir: HOME, userDataDir: "/Users/alice/.copilot-data" };
    expect(classifyForbiddenRoot("/Users/alice/Library", mac)).toBe(
      "application_state_directory",
    );
    const win = {
      homeDir: "C:\\Users\\alice",
      userDataDir: "C:\\Users\\alice\\AppData\\Roaming\\copilot",
    };
    expect(classifyForbiddenRoot("C:\\Users\\alice\\AppData\\Local", win)).toBe(
      "application_state_directory",
    );
    const xdg = { homeDir: "/home/ada", userDataDir: "/home/ada/.ud" };
    expect(classifyForbiddenRoot("/home/ada/.config/gh", xdg)).toBe(
      "application_state_directory",
    );
    expect(classifyForbiddenRoot("/home/ada/.local/share", xdg)).toBe(
      "application_state_directory",
    );
  });

  it("only applies directly under home", () => {
    // A project that happens to contain a `Library` folder is not app state.
    expect(reasonFor("/Users/alice/code/app/Library")).toBeNull();
  });
});

describe("classifyForbiddenRoot — paths that do not render as themselves", () => {
  it("refuses invisible and bidirectional control characters", () => {
    // A right-to-left override makes the tail of a path render backwards, so
    // the string on the card is not the string being granted. Consent to a
    // rendering is not consent to the path.
    expect(reasonFor("/Users/alice/\u202Estrop\u202C/x")).toBe(
      "deceptive_path",
    );
    expect(reasonFor("/Users/alice/Re\u200Bports")).toBe("deceptive_path");
    expect(reasonFor("/Users/alice/Rep\u00ADorts")).toBe("deceptive_path");
    expect(reasonFor("/Users/alice/x\u0007y")).toBe("deceptive_path");
  });

  it("refuses a compatibility form that BECOMES a separator or a traversal", () => {
    expect(reasonFor("/Users/alice/a\uFF0Fb")).toBe("deceptive_path");
    expect(reasonFor("/Users/alice/\uFF0E\uFF0E/etc")).toBe("deceptive_path");
  });

  it("leaves ordinary non-Latin folder names alone", () => {
    // The rule is about characters that HIDE what a path is, not about scripts.
    // Refusing a segment for mixing alphabets would take `Проект-v2` with it.
    expect(reasonFor("/Users/alice/書類")).toBeNull();
    expect(reasonFor("/Users/alice/Проект-v2")).toBeNull();
    expect(reasonFor("/Users/alice/مستندات")).toBeNull();
  });
});

describe("assertGrantableRoot — what the person is told", () => {
  it("throws a readable sentence, and carries the machine reason with it", () => {
    // A refusal the card cannot show is the silent no-op this gate exists to
    // avoid; a category name in place of a sentence is the same thing wearing
    // developer clothes.
    try {
      assertGrantableRoot("/Applications/Mail.app", CTX);
      throw new Error("expected a refusal");
    } catch (error) {
      expect(error).toBeInstanceOf(FsError);
      const fs = error as FsError;
      expect(fs.code).toBe("permission_denied");
      expect(fs.reason).toBe("application_bundle");
      expect(fs.message).toBe(FORBIDDEN_ROOT_MESSAGES.application_bundle);
      // A sentence, not a token.
      expect(fs.message).toMatch(/^[A-Z].*[.]$/u);
    }
  });

  it("never echoes the path, whichever rule refused it", () => {
    for (const root of [
      "/Users/bob/private-thing",
      "/Volumes/Very-Secret-Disk",
      "/private/etc",
      "/Applications/Very-Secret.app",
      "/Users/alice/Library/Application Support/Very-Secret",
    ]) {
      try {
        assertGrantableRoot(root, CTX);
        throw new Error(`expected a refusal for ${root}`);
      } catch (error) {
        const message = (error as Error).message;
        expect(message).not.toContain(root);
        expect(message.toLowerCase()).not.toContain("secret");
      }
    }
  });

  it("allows the ordinary case it exists to keep working", () => {
    expect(() =>
      assertGrantableRoot("/Users/alice/clients/acme/reports", CTX),
    ).not.toThrow();
  });
});
