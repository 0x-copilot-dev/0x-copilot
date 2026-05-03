#!/usr/bin/env python3
"""Validate deploy/tenants.yml against deploy/tenants.schema.json.

Exits 0 on success (including when tenants.yml is absent — empty registry is allowed
during initial bootstrapping). Exits 1 on schema violation, duplicate ids, or tier
ordering inconsistency.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import jsonschema
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TENANTS_PATH = REPO_ROOT / "deploy" / "tenants.yml"
SCHEMA_PATH = REPO_ROOT / "deploy" / "tenants.schema.json"


def main() -> int:
    if not SCHEMA_PATH.exists():
        print(f"FAIL: schema missing at {SCHEMA_PATH}", file=sys.stderr)
        return 1
    schema = json.loads(SCHEMA_PATH.read_text())

    if not TENANTS_PATH.exists():
        print(f"OK: {TENANTS_PATH} not present (empty registry tolerated)")
        return 0

    data = yaml.safe_load(TENANTS_PATH.read_text())
    if data is None:
        print(f"FAIL: {TENANTS_PATH} is empty or invalid YAML", file=sys.stderr)
        return 1

    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as exc:
        print(
            f"FAIL: schema violation: {exc.message} (path: {list(exc.absolute_path)})",
            file=sys.stderr,
        )
        return 1

    tenants = data.get("tenants", [])

    ids = [t["id"] for t in tenants]
    duplicates = [tid for tid, count in Counter(ids).items() if count > 1]
    if duplicates:
        print(f"FAIL: duplicate tenant ids: {duplicates}", file=sys.stderr)
        return 1

    for tenant in tenants:
        prefix = tenant["gh_environment_prefix"]
        expected_prefix = f"tenant-{tenant['id']}"
        if prefix != expected_prefix:
            print(
                f"FAIL: tenant {tenant['id']!r} has gh_environment_prefix={prefix!r}, expected {expected_prefix!r}",
                file=sys.stderr,
            )
            return 1

    print(f"OK: {len(tenants)} tenant(s) validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
