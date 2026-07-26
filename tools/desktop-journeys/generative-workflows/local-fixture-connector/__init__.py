"""Hermetic MCP fixture for the Generative Workflows Desktop journeys.

This package is deliberately test-only.  It keeps all scenario state in memory
and speaks MCP over stdio; it has no HTTP client, credentials, or external
effect path.
"""

from .fixture_connector import FixtureError, FixtureStore

__all__ = ["FixtureError", "FixtureStore"]
