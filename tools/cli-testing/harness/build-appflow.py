#!/usr/bin/env python3
"""Build an ffconcat file from CDP screencast frames + their per-frame
timestamps (markers.json), so variable-rate screencast frames play back at
real wall-clock timing. Emits concat to stdout path arg.

Usage: build-appflow.py <run_dir> <t_start> <t_end> <out_concat>
"""
import json
import os
import sys

run_dir, t_start, t_end, out = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
meta = json.load(open(os.path.join(run_dir, "markers.json")))
frames = meta["meta"]  # [{n, t}, ...], t = wall-clock seconds
sel = [f for f in frames if t_start <= f["t"] <= t_end]
if not sel:
    print("no frames in range", file=sys.stderr)
    sys.exit(1)

lines = ["ffconcat version 1.0"]
for i, f in enumerate(sel):
    fp = os.path.join(run_dir, "frames", f"f-{f['n']:05d}.jpg")
    if not os.path.exists(fp):
        continue
    # duration until next selected frame (cap so a static hold isn't absurd)
    if i + 1 < len(sel):
        dur = max(0.016, min(2.8, sel[i + 1]["t"] - f["t"]))
    else:
        dur = 0.2
    lines.append(f"file '{os.path.abspath(fp)}'")
    lines.append(f"duration {dur:.3f}")
# concat demuxer needs the last file repeated w/o duration
last_fp = os.path.abspath(os.path.join(run_dir, "frames", "f-%05d.jpg" % sel[-1]["n"]))
lines.append(f"file '{last_fp}'")
open(out, "w").write("\n".join(lines) + "\n")
print(f"wrote {out}: {len(sel)} frames, {sel[0]['t']:.2f}->{sel[-1]['t']:.2f}s")
