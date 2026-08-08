#!/usr/bin/env python3
"""Live check for the Virtuals provider integration. Needs a real key.

Everything else about this integration is covered by unit tests against a mock
transport. The one thing a mock cannot prove is the arm that needs a credential:
that a GOOD key returns 200 from the probe. The rejection arm (403 without a
key) is verified in CI; this script closes the other half.

Usage:

    VIRTUALS_ACP_KEY=... python tools/check_virtuals_key.py

It performs three checks, in the order the product performs them:

  1. the public catalog fetch the runtime's VirtualsModelSource caches;
  2. the live probe the backend runs when a key is saved
     (POST /chat/completions), asserting the SAME verdict mapping
     live_validator.py applies;
  3. a real one-token completion on the model the composer would default to.

It NEVER prints the key, and it writes nothing to disk. Exit code 0 = the
integration is live; 1 = a check failed, with the reason.
"""

from __future__ import annotations

import os
import sys
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover - operator-facing
    sys.exit("This check needs httpx. Run it with a service venv's python.")

BASE_URL = "https://compute.virtuals.io/v1"
# Mirrors ProviderKeyLiveValidator._VIRTUALS_PROBE_MODEL. If you change one,
# change the other — this script exists to prove that probe works.
PROBE_MODEL = "z-ai-glm-4-7-flash"
TIMEOUT = 30.0


class Check:
    """One named check with a pass/fail verdict and a one-line reason."""

    def __init__(self, key: str) -> None:
        self._headers = {"Authorization": f"Bearer {key}"}
        self.failures: list[str] = []

    def report(self, name: str, ok: bool, detail: str) -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}")
        if not ok:
            self.failures.append(name)

    def catalog(self) -> list[dict[str, Any]]:
        """1. The inventory VirtualsModelSource caches. Public — no key needed."""

        try:
            response = httpx.get(f"{BASE_URL}/models", timeout=TIMEOUT)
            models = response.json().get("data", [])
        except Exception as exc:  # noqa: BLE001 - operator-facing summary
            self.report("catalog fetch", False, f"{type(exc).__name__}: {exc}")
            return []
        ok = response.status_code == 200 and bool(models)
        self.report(
            "catalog fetch",
            ok,
            f"{len(models)} models from {BASE_URL}/models (HTTP {response.status_code})",
        )
        return models if ok else []

    def probe(self) -> None:
        """2. The exact call the backend makes when a key is saved."""

        body = {
            "model": PROBE_MODEL,
            "messages": [{"role": "user", "content": " "}],
            "max_tokens": 1,
        }
        try:
            response = httpx.post(
                f"{BASE_URL}/chat/completions",
                headers=self._headers,
                json=body,
                timeout=TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001 - operator-facing summary
            self.report("live key probe", False, f"unreachable: {type(exc).__name__}")
            return

        code = response.status_code
        if code == 200:
            self.report("live key probe", True, "200 — the key is accepted (VALID)")
        elif code in (401, 403):
            self.report(
                "live key probe",
                False,
                f"{code} — the gateway REJECTED this key (INVALID_KEY). "
                "Check the value, not the code.",
            )
        elif code in (400, 404):
            self.report(
                "live key probe",
                False,
                f"{code} — probe model '{PROBE_MODEL}' looks retired. The key may "
                "be fine; update _VIRTUALS_PROBE_MODEL in live_validator.py.",
            )
        else:
            self.report(
                "live key probe",
                False,
                f"{code} — non-verdictive (PROVIDER_UNREACHABLE)",
            )

    def completion(self, model: str) -> None:
        """3. A real run through the model the composer would default to."""

        try:
            response = httpx.post(
                f"{BASE_URL}/chat/completions",
                headers=self._headers,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Reply with just: ok"}],
                    "max_tokens": 8,
                },
                timeout=TIMEOUT,
            )
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - operator-facing summary
            self.report("real completion", False, f"{type(exc).__name__}: {exc}")
            return
        if response.status_code != 200:
            self.report(
                "real completion", False, f"HTTP {response.status_code} on {model}"
            )
            return
        choices = payload.get("choices") or []
        text = ""
        if choices and isinstance(choices[0], dict):
            text = (choices[0].get("message") or {}).get("content") or ""
        self.report(
            "real completion",
            bool(text.strip()),
            f"{model} replied {text.strip()[:40]!r}"
            if text.strip()
            else f"{model} returned an empty choice",
        )


def main() -> int:
    key = (os.environ.get("VIRTUALS_ACP_KEY") or "").strip()
    if not key:
        print("VIRTUALS_ACP_KEY is not set.\n")
        print("  VIRTUALS_ACP_KEY=... python tools/check_virtuals_key.py")
        return 1

    print(f"Virtuals live check — key ending …{key[-4:]}\n")
    check = Check(key)
    models = check.catalog()
    check.probe()
    if models:
        # Prefer the model the composer defaults to; fall back to the probe one.
        ids = {m.get("id") for m in models if isinstance(m, dict)}
        preferred = "anthropic-claude-sonnet-5"
        check.completion(preferred if preferred in ids else PROBE_MODEL)

    print()
    if check.failures:
        print(f"FAILED: {', '.join(check.failures)}")
        return 1
    print("All checks passed — Virtuals is live for this key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
