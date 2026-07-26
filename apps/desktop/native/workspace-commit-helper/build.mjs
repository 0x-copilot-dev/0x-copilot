import { chmod, mkdir, rename, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const output = join(root, "bin", "workspace-commit-helper");

if (process.platform !== "darwin") {
  // There is intentionally no portable fallback. Production writable C2 is
  // unavailable outside the platform/primitive combination we can prove. Keep
  // a non-executable packaging sentinel so cross-platform CI can still verify
  // the exact resource layout; the main-process client rejects it before spawn.
  await mkdir(dirname(output), { recursive: true, mode: 0o700 });
  await writeFile(output, "unsupported platform\n", { mode: 0o400 });
  process.exit(0);
}

await mkdir(dirname(output), { recursive: true, mode: 0o700 });
const arch = process.env.npm_config_arch;
const temporary = `${output}.${process.pid}.tmp`;
const args = [
  "-std=c11",
  "-Wall",
  "-Wextra",
  "-Werror",
  "-O2",
  "-fstack-protector-strong",
  "-D_FORTIFY_SOURCE=2",
  "-Wl,-dead_strip",
  "-o",
  temporary,
  join(root, "src", "workspace_commit_helper.c"),
];
if (arch === "arm64" || arch === "x64") args.unshift("-arch", arch);
const result = spawnSync("cc", args, { stdio: "inherit" });
if (result.status !== 0) process.exit(result.status ?? 1);
await rename(temporary, output);
await chmod(output, 0o500);
