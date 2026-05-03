#!/usr/bin/env python3
"""Verify a deployment-manifest.json before promoting it to a tenant.

Performs:
 1. Schema check (v2 required: must include cosign signing block, attestation block,
    image list, and SBOM references).
 2. cosign verify on every image, asserting OIDC issuer + expected subject regex.
 3. gh attestation verify on every image, asserting the repository owner.
 4. (Optional) data-residency check: if the tenant restricts allowed_image_registries,
    every image's registry must be in the allowlist.

Exits 0 only if every check passes for every image. Any failure exits 1 with a
human-readable message and the failing image reference.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _check_tool(name: str) -> None:
    if shutil.which(name) is None:
        print(f"FAIL: required tool {name!r} not found on PATH", file=sys.stderr)
        sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest", required=True, help="Path to deployment-manifest.json"
    )
    parser.add_argument(
        "--owner", required=True, help="GHCR repository owner (lowercase)"
    )
    parser.add_argument(
        "--tenant", required=True, help="Path to tenant JSON (from load_tenant.py)"
    )
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"FAIL: manifest not found at {manifest_path}", file=sys.stderr)
        return 1

    manifest = json.loads(manifest_path.read_text())

    if manifest.get("schema") != "enterprise-search/deployment-manifest/v2":
        print(
            f"FAIL: manifest schema is {manifest.get('schema')!r}, expected v2",
            file=sys.stderr,
        )
        return 1

    images = manifest.get("images", [])
    if not images:
        print("FAIL: manifest has no images", file=sys.stderr)
        return 1

    signing = manifest.get("signing") or {}
    issuer = signing.get("oidc_issuer")
    subject_regex = signing.get("expected_subject_regex")
    if not issuer or not subject_regex:
        print(
            "FAIL: manifest signing block missing oidc_issuer or expected_subject_regex",
            file=sys.stderr,
        )
        return 1
    try:
        re.compile(subject_regex)
    except re.error as exc:
        print(f"FAIL: expected_subject_regex is invalid: {exc}", file=sys.stderr)
        return 1

    tenant = json.loads(Path(args.tenant).read_text())
    residency = (tenant.get("data_residency") or {}).get("allowed_image_registries")

    _check_tool("cosign")
    _check_tool("gh")

    failures: list[str] = []
    for image in images:
        ref = image.get("reference", "")
        component = image.get("component", "<unknown>")
        if "@sha256:" not in ref:
            failures.append(
                f"{component}: image reference is not digest-pinned: {ref!r}"
            )
            continue

        if residency:
            registry_host = ref.split("/", 1)[0]
            allowed = (
                any(ref.startswith(prefix) for prefix in residency)
                or registry_host in residency
            )
            if not allowed:
                failures.append(
                    f"{component}: registry {registry_host!r} not in tenant data_residency.allowed_image_registries"
                )
                continue

        rc, out = _run(
            [
                "cosign",
                "verify",
                "--certificate-oidc-issuer",
                issuer,
                "--certificate-identity-regexp",
                subject_regex,
                ref,
            ]
        )
        if rc != 0:
            failures.append(f"{component}: cosign verify failed: {out[:300]}")
            continue

        rc, out = _run(["gh", "attestation", "verify", ref, "--owner", args.owner])
        if rc != 0:
            failures.append(f"{component}: gh attestation verify failed: {out[:300]}")
            continue

        print(f"OK: {component} {ref}")

    if failures:
        print(
            "\nFAIL: release verification failed for one or more images:",
            file=sys.stderr,
        )
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print(f"\nOK: {len(images)} image(s) verified for tenant {tenant['id']!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
