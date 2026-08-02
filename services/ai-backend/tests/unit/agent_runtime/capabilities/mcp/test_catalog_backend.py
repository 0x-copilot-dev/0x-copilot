"""Unit tests for the read-only ``/mcp/`` Deep Agents backend.

Reachability is the whole point, so these drive the four primitives the model
actually has — ``ls``, ``read_file``, ``grep``, ``glob`` — including through a
real ``CompositeBackend`` mount, which is where the route-prefix translation
either works or silently returns nothing.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from agent_runtime.capabilities.mcp.cards import McpToolDescriptor
from agent_runtime.capabilities.mcp.catalog import (
    McpCatalogBuilder,
    McpCatalogPaths,
    McpCatalogStore,
    Messages,
)
from agent_runtime.capabilities.mcp.catalog_backend import McpCatalogBackend

from tests.unit.agent_runtime.capabilities.mcp.test_catalog import McpCatalogMixin


class EmptyDefaultBackend(BackendProtocol):
    """A default route that holds nothing — the composite's non-``/mcp/`` half.

    Deep Agents' own ``StateBackend`` reads through LangGraph's runtime config,
    which does not exist outside a graph, so the mount test needs a default that
    is inert rather than one that raises.
    """

    def ls(self, path: str) -> LsResult:
        del path
        return LsResult(entries=[])

    async def als(self, path: str) -> LsResult:
        return self.ls(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        del offset, limit
        return ReadResult(error=f"File '{file_path}' not found")

    async def aread(
        self, file_path: str, offset: int = 0, limit: int = 2000
    ) -> ReadResult:
        return self.read(file_path, offset, limit)

    def grep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        del pattern, path, glob
        return GrepResult(matches=[])

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        return self.grep(pattern, path, glob)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        del pattern, path
        return GlobResult(matches=[])

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self.glob(pattern, path)

    def write(self, file_path: str, content: str) -> WriteResult:
        del content
        return WriteResult(path=file_path)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return self.write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        del old_string, new_string, replace_all
        return EditResult(path=file_path)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        return self.edit(file_path, old_string, new_string, replace_all)


class CatalogBackendMixin(McpCatalogMixin):
    """A published catalog plus the backend and mount that serve it."""

    def published_store(
        self,
        *,
        tools: Sequence[McpToolDescriptor] | None = None,
        server_name: str | None = None,
    ) -> McpCatalogStore:
        store = McpCatalogStore()
        card = self.make_catalog_card(name=server_name or self.CatalogValues.SERVER)
        store.publish(McpCatalogBuilder.build(self.make_loaded(card=card, tools=tools)))
        return store

    def backend(self, store: McpCatalogStore) -> McpCatalogBackend:
        return McpCatalogBackend(store)

    def mounted(self, store: McpCatalogStore) -> CompositeBackend:
        return CompositeBackend(
            default=EmptyDefaultBackend(),
            routes={McpCatalogBackend.PATH_PREFIX: McpCatalogBackend(store)},
        )

    def entry_paths(self, result: LsResult) -> list[str]:
        return [entry["path"] for entry in (result.entries or [])]


class TestCatalogListing(CatalogBackendMixin):
    def test_root_lists_every_published_server(self) -> None:
        store = self.published_store()
        store.publish(
            McpCatalogBuilder.build(
                self.make_loaded(
                    card=self.make_catalog_card(name="slack_mcp"),
                    tools=(self.make_catalog_tool("post_message"),),
                )
            )
        )

        result = self.backend(store).ls("/")

        assert self.entry_paths(result) == ["/linear/", "/slack_mcp/"]

    def test_server_directory_lists_the_always_loaded_tier_and_subdirs(self) -> None:
        store = self.published_store()

        result = self.backend(store).ls("/linear")

        assert self.entry_paths(result) == [
            "/linear/SERVER.md",
            "/linear/resources/",
            "/linear/tools/",
        ]

    def test_tools_directory_lists_one_entry_per_tool(self) -> None:
        store = self.published_store()

        result = self.backend(store).ls("/linear/tools")

        assert len(self.entry_paths(result)) == self.CatalogValues.LARGE_TOOL_COUNT
        assert all(path.endswith(".json") for path in self.entry_paths(result))

    def test_public_path_spelling_is_also_accepted(self) -> None:
        store = self.published_store()

        result = self.backend(store).ls("/mcp/linear/tools")

        assert len(self.entry_paths(result)) == self.CatalogValues.LARGE_TOOL_COUNT

    def test_a_server_named_mcp_stays_addressable(self) -> None:
        store = self.published_store(
            server_name="mcp", tools=(self.make_catalog_tool("odd_tool"),)
        )

        # The composite strips its own ``/mcp`` prefix, so this is what the
        # backend receives for ``/mcp/mcp/tools``.
        result = self.backend(store).ls("/mcp/tools")

        assert self.entry_paths(result) == ["/mcp/tools/odd_tool.json"]

    def test_unknown_directory_lists_empty_rather_than_erroring(self) -> None:
        store = self.published_store()

        result = self.backend(store).ls("/nope")

        assert result.error is None
        assert result.entries == []


class TestCatalogReads(CatalogBackendMixin):
    def test_reading_one_tool_file_returns_its_full_contract(self) -> None:
        store = self.published_store(
            tools=(self.make_catalog_tool(self.CatalogValues.READ_TOOL),)
        )

        result = self.backend(store).read(
            f"/linear/tools/{self.CatalogValues.READ_TOOL}.json"
        )

        assert result.error is None
        payload = json.loads((result.file_data or {})["content"])
        assert payload["name"] == self.CatalogValues.READ_TOOL
        assert payload["input_schema"]["type"] == "object"

    def test_read_slices_by_source_line(self) -> None:
        store = self.published_store()

        head = self.backend(store).read("/linear/SERVER.md", 0, 3)
        tail = self.backend(store).read("/linear/SERVER.md", 3, 3)

        # The offloaded-blob backend accepted offset/limit and discarded both,
        # so every read returned the same bytes. This one must not.
        assert (head.file_data or {})["content"] != (tail.file_data or {})["content"]
        assert (head.file_data or {})["content"].count("\n") <= 3

    def test_unknown_file_returns_a_safe_message(self) -> None:
        store = self.published_store()

        result = self.backend(store).read("/linear/tools/not_a_tool.json")

        assert result.file_data is None
        assert result.error == Messages.NOT_FOUND

    def test_async_read_matches_sync_read(self) -> None:
        store = self.published_store()
        backend = self.backend(store)

        sync_result = backend.read("/linear/SERVER.md")
        async_result = asyncio.run(backend.aread("/linear/SERVER.md"))

        assert sync_result.file_data == async_result.file_data


class TestCatalogSearch(CatalogBackendMixin):
    def test_grep_finds_the_tool_by_its_description(self) -> None:
        store = self.published_store(
            tools=(
                self.make_catalog_tool(
                    self.CatalogValues.READ_TOOL,
                    description="List issues in a team or project.",
                ),
                self.make_catalog_tool(
                    "list_teams", description="List teams in the workspace."
                ),
            )
        )

        result = self.backend(store).grep("List issues", "/linear")

        assert result.error is None
        assert {match["path"] for match in (result.matches or [])} == {
            "/linear/SERVER.md",
            f"/linear/tools/{self.CatalogValues.READ_TOOL}.json",
        }

    def test_grep_returns_line_numbers_and_text(self) -> None:
        store = self.published_store(
            tools=(self.make_catalog_tool(self.CatalogValues.READ_TOOL),)
        )

        result = self.backend(store).grep("invoke_with", "/linear/tools")

        matches = result.matches or []
        assert matches
        assert all(match["line"] >= 1 for match in matches)
        assert all("invoke_with" in match["text"] for match in matches)

    def test_glob_matches_every_tool_document(self) -> None:
        store = self.published_store()

        result = self.backend(store).glob("**/*.json", "/")

        assert len(result.matches or []) == self.CatalogValues.LARGE_TOOL_COUNT

    def test_glob_tolerates_a_route_prefixed_pattern(self) -> None:
        store = self.published_store()

        result = self.backend(store).glob("/mcp/linear/tools/*.json", "/")

        assert len(result.matches or []) == self.CatalogValues.LARGE_TOOL_COUNT


class TestCatalogIsReadOnly(CatalogBackendMixin):
    def test_write_is_refused_with_a_safe_message(self) -> None:
        store = self.published_store()

        result = self.backend(store).write("/linear/tools/new.json", "{}")

        assert result.error == Messages.READ_ONLY
        assert result.path is None

    def test_edit_is_refused_with_a_safe_message(self) -> None:
        store = self.published_store()

        result = self.backend(store).edit("/linear/SERVER.md", "a", "b")

        assert result.error == Messages.READ_ONLY

    def test_delete_move_and_mkdir_are_refused(self) -> None:
        backend = self.backend(self.published_store())

        deleted = asyncio.run(backend.adelete("/linear/SERVER.md"))
        moved = asyncio.run(backend.amove("/linear/SERVER.md", "/linear/OTHER.md"))
        made = asyncio.run(backend.amkdir("/linear/extra"))

        assert deleted.error == Messages.READ_ONLY
        assert moved.error == Messages.READ_ONLY
        assert made.error == Messages.READ_ONLY

    def test_a_refused_write_leaves_the_catalog_intact(self) -> None:
        store = self.published_store()
        backend = self.backend(store)

        backend.write("/linear/tools/new.json", "{}")

        assert len(store.snapshot()) == self.CatalogValues.LARGE_TOOL_COUNT + 1


class TestCatalogMount(CatalogBackendMixin):
    def test_composite_root_advertises_the_catalog_directory(self) -> None:
        composite = self.mounted(self.published_store())

        result = composite.ls("/")

        assert McpCatalogBackend.PATH_PREFIX in self.entry_paths(result)

    def test_composite_returns_public_paths_the_model_can_read_back(self) -> None:
        store = self.published_store(
            tools=(self.make_catalog_tool(self.CatalogValues.READ_TOOL),)
        )
        composite = self.mounted(store)

        listed = composite.ls(McpCatalogPaths.tools_dir(self.CatalogValues.SERVER))
        paths = self.entry_paths(listed)

        assert paths == [
            McpCatalogPaths.tool_file(
                self.CatalogValues.SERVER, self.CatalogValues.READ_TOOL
            )
        ]
        # The path ``ls`` handed back must be the one ``read_file`` accepts —
        # that round trip is the entire discovery flow.
        read_back = composite.read(paths[0])
        assert read_back.error is None
        assert json.loads((read_back.file_data or {})["content"])["name"] == (
            self.CatalogValues.READ_TOOL
        )

    def test_composite_grep_reaches_the_catalog_from_the_root(self) -> None:
        store = self.published_store(
            tools=(
                self.make_catalog_tool(
                    self.CatalogValues.READ_TOOL,
                    description="List issues in a team or project.",
                ),
            )
        )
        composite = self.mounted(store)

        result = composite.grep("List issues", "/")

        assert {match["path"] for match in (result.matches or [])} == {
            McpCatalogPaths.server_markdown(self.CatalogValues.SERVER),
            McpCatalogPaths.tool_file(
                self.CatalogValues.SERVER, self.CatalogValues.READ_TOOL
            ),
        }

    def test_composite_glob_reaches_the_catalog(self) -> None:
        composite = self.mounted(self.published_store())

        result = composite.glob("/mcp/**/*.json")

        assert len(result.matches or []) == self.CatalogValues.LARGE_TOOL_COUNT
        assert all(
            match["path"].startswith(McpCatalogBackend.PATH_PREFIX)
            for match in (result.matches or [])
        )

    def test_composite_write_into_the_catalog_is_refused(self) -> None:
        composite = self.mounted(self.published_store())

        result = composite.write(
            McpCatalogPaths.tool_file(self.CatalogValues.SERVER, "new"), "{}"
        )

        assert result.error == Messages.READ_ONLY

    def test_paths_outside_the_route_still_reach_the_default(self) -> None:
        composite = self.mounted(self.published_store())

        result = composite.read("/drafts/note.md")

        assert result.error == "File '/drafts/note.md' not found"
