"""Vulture whitelist for services/ai-backend static dead-code passes.

Pass this module as a **second PATH** argument so Vulture treats names
assigned here as used::

    cd services/ai-backend
    .venv/bin/vulture src ../../docs/unused-code/ai-backend/vulture_whitelist.py --min-confidence 80

Extend assignments when repeated false positives appear across runs.
"""

from __future__ import annotations

# Protocol / typing placeholders — values are irrelevant; names suppress reports.
files = None  # DeepAgentsBackend Protocol upload/aupload signatures
RuntimeEventProducer = object
CitationStorePort = object
McpAuthSessionCreator = object
migrate = True  # Historical kw on RuntimeAdapterFactory.from_settings
