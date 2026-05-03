#!/usr/bin/env python3
"""POST a deploy audit event to backend's /internal/v1/audit/deploy endpoint.

Required env:
  BACKEND_URL                 e.g. https://backend.internal/
  ENTERPRISE_SERVICE_TOKEN    service-to-service token

Args:
  --tenant <path>            tenant JSON
  --manifest <path>          deployment-manifest.json
  --environment <env>
  --release-sha <sha>
  --approver <handle>
  --workflow-run-url <url>
  --started-at <iso>
  --completed-at <iso>
  --outcome <success|failed|rolled_back>
  --force-deploy             if set, records force_deploy=true
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


SERVICE_TOKEN_HEADER = "x-enterprise-service-token"
ORG_HEADER = "x-enterprise-org-id"
USER_HEADER = "x-enterprise-user-id"


def _digests(manifest: dict) -> list[dict]:
    out: list[dict] = []
    for image in manifest.get("images", []):
        ref = image.get("reference", "")
        if "@sha256:" not in ref:
            continue
        out.append({"component": image["component"], "digest": ref.split("@", 1)[1]})
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--approver", required=True)
    parser.add_argument("--workflow-run-url", required=True)
    parser.add_argument("--started-at", required=True)
    parser.add_argument("--completed-at", required=True)
    parser.add_argument("--outcome", required=True)
    parser.add_argument("--force-deploy", action="store_true")
    args = parser.parse_args()

    tenant = json.loads(Path(args.tenant).read_text())
    manifest = json.loads(Path(args.manifest).read_text())

    backend_url = os.environ.get("BACKEND_URL", "").rstrip("/")
    token = os.environ.get("ENTERPRISE_SERVICE_TOKEN", "")
    if not backend_url or not token:
        print(
            "FAIL: BACKEND_URL and ENTERPRISE_SERVICE_TOKEN env vars are required",
            file=sys.stderr,
        )
        return 1

    body = {
        "tenant_id": tenant["id"],
        "environment": args.environment,
        "release_sha": args.release_sha,
        "image_digests": _digests(manifest),
        "approver": args.approver,
        "workflow_run_url": args.workflow_run_url,
        "started_at": args.started_at,
        "completed_at": args.completed_at,
        "outcome": args.outcome,
        "force_deploy": bool(args.force_deploy),
    }

    req = urllib.request.Request(
        url=f"{backend_url}/internal/v1/audit/deploy",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "content-type": "application/json",
            SERVICE_TOKEN_HEADER: token,
            ORG_HEADER: tenant["id"],
            USER_HEADER: f"ci:{args.approver}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = resp.read().decode("utf-8")
            print(f"OK: audit recorded: {payload}")
            return 0
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")[:1000]
        print(f"FAIL: backend returned {exc.code}: {body_text}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"FAIL: backend audit POST failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
