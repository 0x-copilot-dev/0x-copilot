#!/usr/bin/env node
// Regenerate the committed app-icon binaries from icon-source.svg:
//
//   build/icon.png   1024px master (runtime dock/taskbar icon)
//   build/icon.icns  macOS bundle icon (electron-builder + CLI branded shell)
//   build/icon.ico   Windows bundle icon (electron-builder)
//
// macOS-only on purpose: sips (resize) and iconutil (icns) are macOS tools.
// SVG rasterization uses rsvg-convert (librsvg, `brew install librsvg`) instead
// of the built-in qlmanage thumbnailer, because qlmanage flattens SVG
// transparency onto opaque WHITE — which shipped a white box behind the
// dock/taskbar icon. rsvg-convert honours the source alpha. The .ico container
// is written by hand — modern Windows accepts PNG-compressed entries for every
// size we ship.

import { spawnSync } from "node:child_process";
import {
  copyFileSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SOURCE = path.join(HERE, "icon-source.svg");

if (process.platform !== "darwin") {
  console.error("generate-icons.mjs needs macOS (sips/iconutil).");
  process.exit(1);
}

if (spawnSync("rsvg-convert", ["--version"], { stdio: "ignore" }).status !== 0) {
  console.error(
    "generate-icons.mjs needs rsvg-convert for transparent SVG rasterization.\n" +
      "Install librsvg first:  brew install librsvg",
  );
  process.exit(1);
}

function run(cmd, args) {
  const res = spawnSync(cmd, args, { stdio: ["ignore", "pipe", "pipe"] });
  if (res.status !== 0) {
    throw new Error(
      `${cmd} ${args.join(" ")} failed: ${res.stderr?.toString() ?? ""}`,
    );
  }
  return res.stdout?.toString() ?? "";
}

const work = mkdtempSync(path.join(tmpdir(), "copilot-icons-"));
try {
  // 1. SVG -> 1024 master PNG, preserving the source alpha (transparent margins
  //    + rounded-corner cutouts). rsvg-convert renders straight to the target
  //    size; unlike qlmanage it does not composite onto white.
  const master = path.join(work, "icon-1024.png");
  run("rsvg-convert", ["-w", "1024", "-h", "1024", SOURCE, "-o", master]);
  const width = run("sips", ["-g", "pixelWidth", master]);
  if (!/pixelWidth: 1024/.test(width)) {
    throw new Error(`master render is not 1024px:\n${width}`);
  }

  const resize = (size) => {
    const out = path.join(work, `icon-${size}.png`);
    if (size !== 1024)
      run("sips", ["-z", String(size), String(size), master, "--out", out]);
    return out;
  };

  // 2. icns via iconutil from the standard iconset layout.
  const iconset = path.join(work, "icon.iconset");
  run("mkdir", ["-p", iconset]);
  for (const size of [16, 32, 128, 256, 512]) {
    copyFileSync(resize(size), path.join(iconset, `icon_${size}x${size}.png`));
    copyFileSync(
      resize(size * 2),
      path.join(iconset, `icon_${size}x${size}@2x.png`),
    );
  }
  run("iconutil", ["-c", "icns", iconset, "-o", path.join(HERE, "icon.icns")]);

  // 3. .ico container with PNG-compressed entries.
  const icoSizes = [16, 24, 32, 48, 64, 128, 256];
  const blobs = icoSizes.map((s) => readFileSync(resize(s)));
  const header = Buffer.alloc(6 + icoSizes.length * 16);
  header.writeUInt16LE(0, 0); // reserved
  header.writeUInt16LE(1, 2); // type: icon
  header.writeUInt16LE(icoSizes.length, 4);
  let offset = header.length;
  icoSizes.forEach((size, i) => {
    const entry = 6 + i * 16;
    header.writeUInt8(size === 256 ? 0 : size, entry); // width (0 = 256)
    header.writeUInt8(size === 256 ? 0 : size, entry + 1); // height
    header.writeUInt8(0, entry + 2); // palette
    header.writeUInt8(0, entry + 3); // reserved
    header.writeUInt16LE(1, entry + 4); // color planes
    header.writeUInt16LE(32, entry + 6); // bpp
    header.writeUInt32LE(blobs[i].length, entry + 8);
    header.writeUInt32LE(offset, entry + 12);
    offset += blobs[i].length;
  });
  writeFileSync(path.join(HERE, "icon.ico"), Buffer.concat([header, ...blobs]));

  // 4. runtime master.
  copyFileSync(master, path.join(HERE, "icon.png"));
  console.log("wrote build/icon.png, build/icon.icns, build/icon.ico");
} finally {
  rmSync(work, { recursive: true, force: true });
}
