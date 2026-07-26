import assert from "node:assert/strict";
import test from "node:test";
import path from "node:path";

import { databaseStatus } from "../lib/doctor.mjs";

const USER_DATA = "/user-data";
const PGDATA = path.join(USER_DATA, "pgdata");

function classify(files, alive = new Set()) {
  const entries = new Map(Object.entries(files));
  return databaseStatus(USER_DATA, {
    exists: (filename) => entries.has(filename),
    readFile: (filename) => {
      const value = entries.get(filename);
      if (value === undefined) throw new Error("ENOENT");
      return value;
    },
    processAlive: (pid) => alive.has(pid),
  });
}

test("reports an app-owned live postmaster as running, not orphaned", () => {
  assert.deepEqual(
    classify(
      {
        [path.join(PGDATA, "postmaster.pid")]: "1001\n",
        [path.join(PGDATA, ".0xcopilot-owner.pid")]: "2002\n",
      },
      new Set([1001, 2002]),
    ),
    { kind: "owned", postgresPid: 1001, ownerPid: 2002 },
  );
});

test("reports a live postmaster with a dead owner marker as orphaned", () => {
  assert.deepEqual(
    classify(
      {
        [path.join(PGDATA, "postmaster.pid")]: "1001\n",
        [path.join(PGDATA, ".0xcopilot-owner.pid")]: "2002\n",
      },
      new Set([1001]),
    ),
    { kind: "orphaned", postgresPid: 1001 },
  );
});

test("classifies a dead postmaster PID as a stale lock, not a live orphan", () => {
  assert.deepEqual(
    classify({ [path.join(PGDATA, "postmaster.pid")]: "1001\n" }),
    { kind: "stale", postgresPid: 1001 },
  );
});

test("rejects malformed PID files instead of probing an arbitrary process", () => {
  assert.deepEqual(
    classify({ [path.join(PGDATA, "postmaster.pid")]: "1001 trailing\n" }),
    { kind: "invalid" },
  );
});
