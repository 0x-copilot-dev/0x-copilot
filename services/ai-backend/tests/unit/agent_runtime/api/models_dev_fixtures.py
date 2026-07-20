"""Shared fixtures for models.dev catalog-source and model-catalog tests."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from agent_runtime.api.models_dev_source import ModelsDevCatalogSource
from agent_runtime.settings import RuntimeSettings


class FakeClock:
    """Deterministic, manually-advanced replacement for ``time.time``."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ModelsDevFixtureMixin:
    """Payload constants and builders for models.dev source tests."""

    OPENROUTER_SLUG = "vendor/or-test"
    EXPECTED_RECORD_COUNT = 7
    MISSING_PATH = "/nonexistent/for/tests"

    # Six supported providers (google maps to our "gemini"), one malformed
    # model row, and one unsupported provider that must be skipped entirely.
    PROVIDER_FIXTURE: dict = {
        "openai": {
            "id": "openai",
            "models": {
                "gpt-test-pro": {
                    "id": "gpt-test-pro",
                    "name": "GPT Test Pro",
                    "attachment": True,
                    "reasoning": True,
                    "tool_call": True,
                    "release_date": "2026-01-02",
                    "modalities": {"input": ["text", "image"], "output": ["text"]},
                    "limit": {"context": 400_000, "output": 128_000},
                    "cost": {"input": 1.25, "output": 10.0},
                },
                "gpt-test-mini": {
                    "id": "gpt-test-mini",
                    "name": "GPT Test Mini",
                    "release_date": "2026-03-04",
                    "limit": {"context": 128_000, "output": 16_384},
                    "cost": {"input": 0.15, "output": 0.6},
                },
                "broken-row": "not-an-object",
            },
        },
        "anthropic": {
            "id": "anthropic",
            "models": {
                "claude-test": {
                    "id": "claude-test",
                    "name": "Claude Test",
                    "reasoning": True,
                    "tool_call": True,
                    "release_date": "2026-02-01",
                    "limit": {"context": 200_000, "output": 64_000},
                    "cost": {"input": 3.0, "output": 15.0},
                },
            },
        },
        "google": {
            "id": "google",
            "models": {
                "gemini-test": {
                    "id": "gemini-test",
                    "name": "Gemini Test",
                    "attachment": True,
                    "reasoning": True,
                    "tool_call": True,
                    "release_date": "2026-01-15",
                    "limit": {"context": 1_000_000, "output": 65_536},
                    "cost": {"input": 1.25, "output": 5.0},
                },
            },
        },
        "openrouter": {
            "id": "openrouter",
            "models": {
                "vendor/or-test": {
                    "id": "vendor/or-test",
                    "name": "OR Test",
                    "reasoning": True,
                    "tool_call": True,
                    "release_date": "2026-04-01",
                    "limit": {"context": 131_072},
                    "cost": {"input": 0.3, "output": 0.9},
                },
            },
        },
        "groq": {
            "id": "groq",
            "models": {
                "groq-test": {
                    "id": "groq-test",
                    "name": "Groq Test",
                    "tool_call": True,
                    "release_date": "2025-12-01",
                    "limit": {"context": 8_192},
                },
            },
        },
        "xai": {
            "id": "xai",
            "models": {
                "grok-test": {
                    "id": "grok-test",
                    "name": "Grok Test",
                    "reasoning": True,
                    "tool_call": True,
                    "release_date": "2026-05-01",
                    "limit": {"context": 256_000},
                    "cost": {"input": 2.0, "output": 10.0},
                },
            },
        },
        "mistral": {
            "id": "mistral",
            "models": {"mistral-test": {"id": "mistral-test", "name": "Skip Me"}},
        },
    }

    @staticmethod
    def payload_with_single(model_id: str, provider: str = "openai") -> dict:
        """Minimal valid payload holding one marker model."""

        return {
            provider: {
                "id": provider,
                "models": {
                    model_id: {
                        "id": model_id,
                        "name": model_id,
                        "release_date": "2026-01-01",
                        "limit": {"context": 1_000},
                    }
                },
            }
        }

    @classmethod
    def write_snapshot(cls, tmp_path: Path, payload: dict | None = None) -> Path:
        path = tmp_path / "snapshot.json"
        path.write_text(json.dumps(payload or cls.PROVIDER_FIXTURE))
        return path

    @classmethod
    def write_cache(cls, cache_dir: Path, payload: dict) -> Path:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / ModelsDevCatalogSource.CACHE_FILENAME
        path.write_text(json.dumps(payload))
        return path

    @classmethod
    def source_with_snapshot(
        cls,
        tmp_path: Path,
        payload: dict | None = None,
        **kwargs: object,
    ) -> ModelsDevCatalogSource:
        return ModelsDevCatalogSource(
            snapshot_path=cls.write_snapshot(tmp_path, payload),
            auto_refresh=False,
            **kwargs,  # type: ignore[arg-type]
        )

    @staticmethod
    def client_returning(payload: dict) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        return httpx.Client(transport=httpx.MockTransport(handler))

    @staticmethod
    def client_http_error(status_code: int = 500) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code)

        return httpx.Client(transport=httpx.MockTransport(handler))

    @staticmethod
    def client_broken_json() -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"{not-json")

        return httpx.Client(transport=httpx.MockTransport(handler))

    @staticmethod
    def client_network_error() -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        return httpx.Client(transport=httpx.MockTransport(handler))

    @classmethod
    def settings_with(cls, environ: dict[str, str] | None = None) -> RuntimeSettings:
        """Settings isolated from the host env, env files, and template files."""

        return RuntimeSettings.load(
            env_file=cls.MISSING_PATH,
            template_file=cls.MISSING_PATH,
            environ=environ or {},
        )
