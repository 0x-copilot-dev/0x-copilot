#!/usr/bin/env python3
"""Invoke the tenant-configured orchestrator (argo|helm|flux) with digest-pinned images.

Reads the tenant entry (JSON, from load_tenant.py) and a verified deployment-manifest.json,
then dispatches to the appropriate orchestrator. Endpoint URLs come from environment
variables resolved at workflow time from per-environment GitHub Actions secrets — they
are never read directly from the registry.

Required env:
  ORCHESTRATOR_ENDPOINT   the URL/host the orchestrator is reachable at (per-tenant secret)
  ORCHESTRATOR_TOKEN      auth token for the orchestrator (per-tenant secret, if needed)

Args:
  --tenant <path>     tenant JSON
  --manifest <path>   deployment-manifest.json
  --environment <env>  staging|production
  --timeout <secs>    polling timeout for rollout (default 600)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _run(
    cmd: list[str], *, check: bool = True, env: dict | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, check=check, env=env or os.environ.copy()
    )


def _check_tool(name: str) -> None:
    if shutil.which(name) is None:
        print(f"FAIL: required tool {name!r} not found on PATH", file=sys.stderr)
        sys.exit(1)


def _digests_by_component(manifest: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for image in manifest.get("images", []):
        ref = image.get("reference", "")
        if "@sha256:" not in ref:
            continue
        out[image["component"]] = ref
    return out


def deploy_argo(
    tenant: dict, environment: str, digests: dict[str, str], timeout: int
) -> int:
    endpoint = os.environ["ORCHESTRATOR_ENDPOINT"].rstrip("/")
    token = os.environ.get("ORCHESTRATOR_TOKEN", "")
    app = tenant["orchestrator"].get("release_name") or f"{tenant['id']}-{environment}"

    payload = {
        "spec": {
            "source": {
                "helm": {
                    "parameters": [
                        {
                            "name": f"images.{component}.digest",
                            "value": ref.split("@", 1)[1],
                        }
                        for component, ref in digests.items()
                    ]
                }
            }
        }
    }

    _check_tool("curl")
    _run(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "-X",
            "PATCH",
            "-H",
            f"Authorization: Bearer {token}",
            "-H",
            "Content-Type: application/json",
            "--data-binary",
            json.dumps(payload),
            f"{endpoint}/api/v1/applications/{app}",
        ]
    )
    _run(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "-X",
            "POST",
            "-H",
            f"Authorization: Bearer {token}",
            f"{endpoint}/api/v1/applications/{app}/sync",
        ]
    )

    deadline = time.time() + timeout
    while time.time() < deadline:
        proc = _run(
            [
                "curl",
                "--fail",
                "--silent",
                "--show-error",
                "-H",
                f"Authorization: Bearer {token}",
                f"{endpoint}/api/v1/applications/{app}",
            ],
            check=False,
        )
        if proc.returncode == 0:
            try:
                state = json.loads(proc.stdout)
                health = state.get("status", {}).get("health", {}).get("status", "")
                sync = state.get("status", {}).get("sync", {}).get("status", "")
                if health == "Healthy" and sync == "Synced":
                    print(f"OK: argo app {app!r} Healthy/Synced")
                    return 0
            except json.JSONDecodeError:
                pass
        time.sleep(10)
    print(
        f"FAIL: argo app {app!r} did not reach Healthy/Synced within {timeout}s",
        file=sys.stderr,
    )
    return 1


def deploy_helm(
    tenant: dict, environment: str, digests: dict[str, str], timeout: int
) -> int:
    _check_tool("helm")
    _check_tool("kubectl")
    namespace = (
        tenant["orchestrator"].get("namespace") or f"{tenant['id']}-{environment}"
    )
    release = tenant["orchestrator"].get("release_name") or tenant["id"]
    chart_path = os.environ.get("HELM_CHART_PATH", "deploy/charts/0x-copilot")
    set_flags: list[str] = []
    for component, ref in digests.items():
        digest = ref.split("@", 1)[1]
        repo = ref.split("@", 1)[0]
        set_flags += ["--set-string", f"images.{component}.repository={repo}"]
        set_flags += ["--set-string", f"images.{component}.digest={digest}"]
    cmd = [
        "helm",
        "upgrade",
        "--install",
        release,
        chart_path,
        "--namespace",
        namespace,
        "--create-namespace",
        "--atomic",
        "--wait",
        "--timeout",
        f"{timeout}s",
        *set_flags,
    ]
    proc = _run(cmd, check=False)
    print(proc.stdout)
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        return 1
    print(f"OK: helm release {release!r} in namespace {namespace!r} upgraded")
    return 0


def deploy_flux(
    tenant: dict, environment: str, digests: dict[str, str], timeout: int
) -> int:
    _check_tool("flux")
    namespace = (
        tenant["orchestrator"].get("namespace") or f"{tenant['id']}-{environment}"
    )
    release = tenant["orchestrator"].get("release_name") or tenant["id"]
    proc = _run(
        [
            "flux",
            "reconcile",
            "helmrelease",
            release,
            "--namespace",
            namespace,
            "--with-source",
        ],
        check=False,
    )
    print(proc.stdout)
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        return 1
    print(f"OK: flux helmrelease {release!r} reconciled in namespace {namespace!r}")
    return 0


DISPATCH = {"argo": deploy_argo, "helm": deploy_helm, "flux": deploy_flux}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument(
        "--environment", required=True, choices=["staging", "production"]
    )
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    tenant = json.loads(Path(args.tenant).read_text())
    manifest = json.loads(Path(args.manifest).read_text())
    digests = _digests_by_component(manifest)
    if not digests:
        print("FAIL: manifest contains no digest-pinned images", file=sys.stderr)
        return 1

    orchestrator_type = tenant["orchestrator"]["type"]
    handler = DISPATCH.get(orchestrator_type)
    if handler is None:
        print(
            f"FAIL: unsupported orchestrator type: {orchestrator_type!r}",
            file=sys.stderr,
        )
        return 1

    if "ORCHESTRATOR_ENDPOINT" not in os.environ:
        print(
            "FAIL: ORCHESTRATOR_ENDPOINT env not set; expected from per-tenant GitHub secret",
            file=sys.stderr,
        )
        return 1

    return handler(tenant, args.environment, digests, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
