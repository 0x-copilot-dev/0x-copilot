"""Compatibility imports for runtime API app composition."""

from runtime_api.app import RuntimeApiAppFactory, app
from runtime_api.http.routes import RuntimeApiRoutes, RuntimeApiRouter

__all__ = ["RuntimeApiAppFactory", "RuntimeApiRoutes", "RuntimeApiRouter", "app"]
