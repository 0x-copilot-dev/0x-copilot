"""Refresh the vendored LiteLLM pricing catalog (dev/build-time only).

The runtime reads from
``src/agent_runtime/pricing/litellm_data/model_prices.json`` so we
don't import ``litellm`` at runtime (the package pins ``openai`` and
``pydantic`` at strictly older versions than the rest of the service).
This script downloads the upstream JSON directly from the LiteLLM
GitHub repo and writes it into the vendor directory.

Usage::

    python services/ai-backend/scripts/pricing/refresh_litellm_data.py
    python services/ai-backend/scripts/pricing/refresh_litellm_data.py --ref v1.83.14
    python services/ai-backend/scripts/pricing/refresh_litellm_data.py --check-only

``--check-only`` exits 0 when the vendored file is byte-identical to
upstream, 1 otherwise. CI uses it to flag stale vendor data.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path
from typing import Final


_REPO_RAW = "https://raw.githubusercontent.com/BerriAI/litellm"
_FILE_PATH = "model_prices_and_context_window.json"
_VENDOR_PATH: Final[Path] = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "agent_runtime"
    / "pricing"
    / "litellm_data"
    / "model_prices.json"
)


def _fetch(ref: str) -> bytes:
    url = f"{_REPO_RAW}/{ref}/{_FILE_PATH}"
    print(f"Fetching {url}", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 — trusted github URL
        return response.read()


def _sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh the vendored LiteLLM pricing catalog from upstream GitHub.",
    )
    parser.add_argument(
        "--ref",
        default="main",
        help="Git ref (branch, tag, or commit SHA) to pull from (default: main).",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Exit 0 if vendored file matches upstream, 1 otherwise. Does not write.",
    )
    args = parser.parse_args(argv)

    upstream = _fetch(args.ref)
    upstream_sha = _sha256(upstream)

    if _VENDOR_PATH.exists():
        vendored = _VENDOR_PATH.read_bytes()
        vendored_sha = _sha256(vendored)
    else:
        vendored = b""
        vendored_sha = ""

    print(f"  upstream sha256: {upstream_sha}", file=sys.stderr)
    print(f"  vendored sha256: {vendored_sha or '(no vendored file)'}", file=sys.stderr)

    if vendored_sha == upstream_sha:
        print("Vendored file is up to date.", file=sys.stderr)
        return 0

    if args.check_only:
        print(
            "Vendored file differs from upstream — run without --check-only to refresh.",
            file=sys.stderr,
        )
        return 1

    _VENDOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    _VENDOR_PATH.write_bytes(upstream)
    print(
        f"Refreshed {_VENDOR_PATH.relative_to(Path.cwd()) if _VENDOR_PATH.is_absolute() else _VENDOR_PATH} ({len(upstream)} bytes).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
