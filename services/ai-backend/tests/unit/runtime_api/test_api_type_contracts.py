from __future__ import annotations

from pathlib import Path
import re

from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.schemas import AgentRunStatus, RuntimeActivityKind, RuntimeApiEventType


class TestApiTypeContracts:
    def test_typescript_runtime_event_constants_match_backend_enums(self) -> None:
        repo_root = Path(__file__).resolve().parents[5]
        api_types = (repo_root / "packages/api-types/src/index.ts").read_text()

        assert self._string_array(api_types, "RUNTIME_API_EVENT_TYPES") == {
            event_type.value for event_type in RuntimeApiEventType
        }
        assert self._string_array(api_types, "RUNTIME_EVENT_SOURCES") == {
            source.value for source in StreamEventSource
        }
        assert self._string_array(api_types, "RUNTIME_ACTIVITY_KINDS") == {
            kind.value for kind in RuntimeActivityKind
        }

    def test_typescript_runtime_status_constants_match_backend_enums(self) -> None:
        repo_root = Path(__file__).resolve().parents[5]
        api_types = (repo_root / "packages/api-types/src/index.ts").read_text()

        assert self._string_array(api_types, "AGENT_RUN_STATUSES") == {
            status.value for status in AgentRunStatus
        }

    @classmethod
    def _string_array(cls, source: str, name: str) -> set[str]:
        match = re.search(rf"export const {name} = \[(.*?)\] as const", source, re.S)
        assert match is not None
        return set(re.findall(r'"([^"]+)"', match.group(1)))
