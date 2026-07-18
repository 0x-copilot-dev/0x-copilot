"""File-native runtime adapters for the single-user desktop profile.

Plaintext JSONL folders (Claude-Code-session style) are canonical; a
content-addressed object store holds large payloads; a disposable SQLite
catalog index answers listing/lookup. See ``runtime_api_store.py`` for the
locked design decisions.
"""

from runtime_adapters.file.agent_state_store import (
    FileAgentStateWiring,
    FileAgentStoreGate,
    FileMemoryBackend,
    FileMemoryBackendFactory,
    FileMemoryStore,
    FileSkillsStore,
    FileSubagentDefinitionProvider,
    FileSubagentDefinitionStore,
    MemoryDocument,
)
from runtime_adapters.file.citation_store import FileCitationStore
from runtime_adapters.file.conversation_tool_ordinal_store import (
    FileConversationToolOrdinalStore,
)
from runtime_adapters.file.draft_store import FileDraftStore
from runtime_adapters.file.large_tool_result_backend import FileLargeToolResultBackend
from runtime_adapters.file.object_store import FileObjectStore, ObjectRef
from runtime_adapters.file.offload import FileOffloadWriter
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_adapters.file.search import ConversationSearchHit
from runtime_adapters.file.share_store import FileShareStore
from runtime_adapters.file.subagent_trace_backend import FileSubagentTraceBackend

__all__ = [
    "FileRuntimeApiStore",
    "FileObjectStore",
    "ObjectRef",
    "FileOffloadWriter",
    "FileLargeToolResultBackend",
    "FileSubagentTraceBackend",
    "FileCitationStore",
    "FileDraftStore",
    "FileShareStore",
    "FileConversationToolOrdinalStore",
    "FileAgentStateWiring",
    "FileAgentStoreGate",
    "FileMemoryBackend",
    "FileMemoryBackendFactory",
    "FileMemoryStore",
    "FileSkillsStore",
    "FileSubagentDefinitionProvider",
    "FileSubagentDefinitionStore",
    "MemoryDocument",
    "ConversationSearchHit",
]
