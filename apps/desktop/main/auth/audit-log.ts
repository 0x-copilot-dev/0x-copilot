import { appendFile, mkdir, readFile } from "node:fs/promises";
import { dirname } from "node:path";

export type ServerKind = "backend" | "mcp" | "saas";
export type SignInMode = "dev-mint" | "oidc" | "google";

export type AuthAuditEvent =
  | {
      readonly kind: "sign-in-success";
      readonly workspaceId: string;
      readonly sub: string;
      readonly mode: SignInMode;
    }
  | {
      readonly kind: "sign-in-failure";
      readonly workspaceId: string;
      readonly mode: SignInMode;
      readonly reason: string;
    }
  | {
      readonly kind: "sign-out";
      readonly workspaceId: string;
    }
  | {
      readonly kind: "token-refresh-success";
      readonly workspaceId: string;
    }
  | {
      readonly kind: "token-refresh-failure";
      readonly workspaceId: string;
      readonly reason: string;
    }
  | {
      readonly kind: "unauthorized-retry";
      readonly workspaceId: string;
      readonly path: string;
    }
  | {
      readonly kind: "secret-storage-gate-violation";
      readonly claimedWorkspaceId: string;
      readonly sessionWorkspaceId: string;
      readonly serverKind: ServerKind;
      readonly serverId: string;
    };

export interface AuthAuditEntry {
  readonly ts: string;
  readonly event: AuthAuditEvent;
}

export interface AuthAuditLog {
  append(event: AuthAuditEvent): Promise<void>;
  readAll(): Promise<readonly AuthAuditEntry[]>;
}

export interface FileAuthAuditLogOptions {
  readonly filePath: string;
  readonly now?: () => Date;
}

export function createFileAuthAuditLog(
  options: FileAuthAuditLogOptions,
): AuthAuditLog {
  const { filePath } = options;
  const now = options.now ?? (() => new Date());
  let ensureDirPromise: Promise<void> | null = null;

  const ensureDir = (): Promise<void> => {
    if (!ensureDirPromise) {
      ensureDirPromise = mkdir(dirname(filePath), { recursive: true }).then(
        () => {},
      );
    }
    return ensureDirPromise;
  };

  return {
    async append(event: AuthAuditEvent): Promise<void> {
      await ensureDir();
      const entry: AuthAuditEntry = {
        ts: now().toISOString(),
        event,
      };
      await appendFile(filePath, `${JSON.stringify(entry)}\n`, {
        encoding: "utf8",
        flag: "a",
      });
    },
    async readAll(): Promise<readonly AuthAuditEntry[]> {
      let raw: string;
      try {
        raw = await readFile(filePath, "utf8");
      } catch (err) {
        if (isFileNotFound(err)) {
          return [];
        }
        throw err;
      }
      if (raw.length === 0) return [];
      const lines = raw.split("\n").filter((line) => line.length > 0);
      const entries: AuthAuditEntry[] = [];
      for (const line of lines) {
        const parsed: unknown = JSON.parse(line);
        entries.push(parsed as AuthAuditEntry);
      }
      return entries;
    },
  };
}

function isFileNotFound(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    "code" in err &&
    (err as { code: unknown }).code === "ENOENT"
  );
}
