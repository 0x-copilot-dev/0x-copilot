"""BYOK provider API keys — encrypted per-user provider credentials.

Storage adapters live in :mod:`backend_app.provider_keys.store`, the
TokenVault-composing service in :mod:`backend_app.provider_keys.service`,
and the public ``/v1/settings/provider-keys`` routes in
:mod:`backend_app.provider_keys.routes`.
"""

from backend_app.provider_keys.routes import register_provider_keys_routes
from backend_app.provider_keys.service import (
    ProviderKeyFormatError,
    ProviderKeysService,
)
from backend_app.provider_keys.store import (
    InMemoryProviderApiKeyStore,
    PostgresProviderApiKeyStore,
    ProviderApiKeyRecord,
    ProviderApiKeyStore,
    ProviderName,
)


__all__ = [
    "InMemoryProviderApiKeyStore",
    "PostgresProviderApiKeyStore",
    "ProviderApiKeyRecord",
    "ProviderApiKeyStore",
    "ProviderKeyFormatError",
    "ProviderKeysService",
    "ProviderName",
    "register_provider_keys_routes",
]
