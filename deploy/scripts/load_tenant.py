#!/usr/bin/env python3
"""Load a tenant entry from deploy/tenants.yml and emit it as JSON.

Usage:
    python load_tenant.py <tenant_id> <environment>

Exits 1 if the tenant is unknown, the environment is not in the tenant's allowed
environments, or the registry is malformed. Prints a single JSON object to stdout
on success.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TENANTS_PATH = REPO_ROOT / "deploy" / "tenants.yml"


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: load_tenant.py <tenant_id> <environment>", file=sys.stderr)
        return 2
    tenant_id, environment = sys.argv[1], sys.argv[2]

    if not TENANTS_PATH.exists():
        print(f"FAIL: {TENANTS_PATH} not present", file=sys.stderr)
        return 1

    data = yaml.safe_load(TENANTS_PATH.read_text()) or {}
    tenants = data.get("tenants", [])
    match = next((t for t in tenants if t.get("id") == tenant_id), None)
    if match is None:
        print(f"FAIL: unknown tenant_id={tenant_id!r}", file=sys.stderr)
        return 1

    if environment not in match.get("environments", []):
        print(
            f"FAIL: tenant {tenant_id!r} is not configured for environment {environment!r}",
            file=sys.stderr,
        )
        return 1

    json.dump(match, sys.stdout)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
