import { existsSync } from "node:fs";
import { dirname } from "node:path";
import { spawnSync } from "node:child_process";

import type { WorkspaceConfinementProbe } from "../capabilities/workspace-production-authority";
import type { SpawnFn } from "./python-service";
import type { SupervisedServiceName } from "./runtime-paths";

/** Absolute, main-owned launcher. Never resolve this through PATH. */
export const MACOS_SANDBOX_EXEC = "/usr/bin/sandbox-exec";

export interface MacosWorkspaceConfinementConfig {
  readonly runtimeRoot: string;
  readonly webDir: string;
  /** Only the child-owned app-data roots; never the whole userData tree. */
  readonly childDataDirs: readonly string[];
  /** Main-derived process temporary root needed by Python and its libraries. */
  readonly temporaryDir: string;
  readonly pythonBin: string;
  readonly serviceDirs: readonly string[];
  readonly platform?: NodeJS.Platform;
  readonly sandboxExecPath?: string;
  readonly executableExists?: (path: string) => boolean;
  /** Test seam for the exact profile parser/self-test. */
  readonly runSelfTest?: (
    command: string,
    args: readonly string[],
  ) => { readonly status: number | null; readonly error?: Error };
}

export interface ConfinedCommand {
  readonly command: string;
  readonly args: readonly string[];
}

/**
 * Main-owned macOS Seatbelt launch gate for the supervised Python services.
 * Its filesystem profile deliberately omits every user-selected workspace
 * root: a service can reach such content only through Electron main's private
 * broker. Network access remains available because provider/MCP traffic is a
 * product requirement and is not filesystem authority.
 */
export class MacosWorkspaceConfinement implements WorkspaceConfinementProbe {
  readonly #sandboxExecPath: string;
  readonly #profile: string;
  readonly #available: boolean;
  readonly #runSelfTest: NonNullable<
    MacosWorkspaceConfinementConfig["runSelfTest"]
  >;
  #verified = false;
  readonly #spawned = new Set<SupervisedServiceName>();
  readonly #healthy = new Set<SupervisedServiceName>();

  constructor(config: MacosWorkspaceConfinementConfig) {
    this.#sandboxExecPath = config.sandboxExecPath ?? MACOS_SANDBOX_EXEC;
    const executableExists = config.executableExists ?? existsSync;
    this.#available =
      (config.platform ?? process.platform) === "darwin" &&
      executableExists(this.#sandboxExecPath);
    this.#profile = buildMacosWorkspaceSeatbeltProfile({
      runtimeRoot: config.runtimeRoot,
      webDir: config.webDir,
      childDataDirs: config.childDataDirs,
      temporaryDir: config.temporaryDir,
      pythonBin: config.pythonBin,
      serviceDirs: config.serviceDirs,
    });
    this.#runSelfTest =
      config.runSelfTest ??
      ((command, args) => {
        const result = spawnSync(command, args, {
          cwd: "/",
          env: {},
          stdio: "ignore",
        });
        return { status: result.status, error: result.error };
      });
  }

  get profileForTesting(): string {
    return this.#profile;
  }

  get verified(): boolean {
    return this.#verified;
  }

  async verify(): Promise<"enforced" | "unavailable"> {
    if (!this.#available) return "unavailable";
    const result = this.#runSelfTest(this.#sandboxExecPath, [
      "-p",
      this.#profile,
      "/usr/bin/true",
    ]);
    this.#verified = result.error === undefined && result.status === 0;
    return this.#verified ? "enforced" : "unavailable";
  }

  /** There is no raw-child fallback: an unverified profile cannot wrap a child. */
  wrap(command: string, args: readonly string[]): ConfinedCommand {
    if (!this.#verified) {
      throw new Error("workspace confinement is unavailable");
    }
    return {
      command: this.#sandboxExecPath,
      args: ["-p", this.#profile, command, ...args],
    };
  }

  spawnFor(name: SupervisedServiceName, spawnFn: SpawnFn): SpawnFn {
    return (command, args, options) => {
      this.#spawned.add(name);
      return spawnFn(command, args, options);
    };
  }

  noteHealthy(name: SupervisedServiceName): void {
    if (this.#spawned.has(name)) this.#healthy.add(name);
  }

  /** Evidence is diagnostic only; the authority was enabled by verify(). */
  healthyServices(): readonly SupervisedServiceName[] {
    return [...this.#healthy];
  }
}

interface ProfileInput {
  readonly runtimeRoot: string;
  readonly webDir: string;
  readonly childDataDirs: readonly string[];
  readonly temporaryDir: string;
  readonly pythonBin: string;
  readonly serviceDirs: readonly string[];
}

export function buildMacosWorkspaceSeatbeltProfile(
  input: ProfileInput,
): string {
  const readPaths = uniquePaths([
    "/System",
    "/usr",
    "/Library",
    "/dev",
    "/private/var/db/timezone",
    input.runtimeRoot,
    input.webDir,
    dirname(input.pythonBin),
    ...input.serviceDirs,
    ...input.childDataDirs,
    input.temporaryDir,
  ]);
  const readRules = readPaths
    .map((path) => `  (allow file-read* (subpath ${seatbeltString(path)}))`)
    .join("\n");
  const writeRules = uniquePaths([...input.childDataDirs, input.temporaryDir])
    .map((path) => `  (allow file-write* (subpath ${seatbeltString(path)}))`)
    .join("\n");
  return [
    "(version 1)",
    "(deny default)",
    '(import "system.sb")',
    "(allow process-exec)",
    "(allow file-read-metadata)",
    readRules,
    writeRules,
    // The confined child may talk to its loopback services and providers, but
    // this grants no host-file read/write capability.
    "(allow network*)",
  ].join("\n");
}

function seatbeltString(value: string): string {
  return `"${value.replaceAll("\\", "\\\\").replaceAll('"', '\\"')}"`;
}

function uniquePaths(paths: readonly string[]): readonly string[] {
  const result = new Set<string>();
  for (const path of paths) {
    if (path !== "") result.add(path);
    if (path.startsWith("/var/")) result.add(`/private${path}`);
    if (path.startsWith("/private/var/")) result.add(path.slice(8));
  }
  return [...result];
}
