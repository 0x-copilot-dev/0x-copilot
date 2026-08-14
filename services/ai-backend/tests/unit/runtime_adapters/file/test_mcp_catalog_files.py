"""The MCP catalog as REAL FILES — asserted against the filesystem, not a fake.

Every assertion here reads the disk directly, because the live failure these
tests exist for was invisible to a mock: the catalog lived in the harness, the
harness is rebuilt per run and again on approval resume, so ``/mcp/`` was empty
on turn 2 and empty after every write approval. A test that published into a
store and read back from the same object proves the object works; it cannot
prove the bytes outlive the object that wrote them.

So these drive the properties only real files can have:

* the tree exists on disk where a human is told to look, with the modes the
  scratch uses;
* a SECOND store instance over the same directory — a new turn, or the second
  harness an approval resume builds — sees what the first one wrote;
* two SEPARATELY COMPOSED backends (the supervisor's and a subagent's) read the
  same bytes, which two in-process stores could not;
* the model still cannot write, edit or delete any of it.
"""

from __future__ import annotations

import json
import stat
from collections.abc import Sequence
from pathlib import Path

from agent_runtime.capabilities.desktop.agent_scratch import (
    COPILOT_HOME_ENV,
    MCP_DIR_NAME,
    agent_scratch_root,
)
from agent_runtime.capabilities.mcp.cards import McpToolDescriptor
from agent_runtime.capabilities.mcp.catalog import (
    Keys,
    McpCatalogBuilder,
    McpCatalogPaths,
    McpCatalogStore,
    Messages,
)
from agent_runtime.capabilities.mcp.catalog_backend import McpCatalogBackend
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.execution.factory import acreate_agent_runtime
from runtime_adapters.file.mcp_catalog_store import FileMcpCatalogStore
from runtime_worker.agent_scratch_wiring import AgentScratchWorkerWiring

from tests.unit.agent_runtime.capabilities.mcp.test_catalog import McpCatalogMixin
from tests.unit.agent_runtime.capabilities.mcp.test_catalog_wiring import (
    CapturingBuilder,
    CatalogWiringMixin,
)


class FileCatalogMixin(McpCatalogMixin):
    """A file-backed catalog rooted in a real temporary directory."""

    class Values:
        CONVERSATION = "conv_filecatalog"
        OTHER_SERVER = "slack_mcp"
        SEARCH_TOOL = "search_issues"

    def store(self, root: Path) -> FileMcpCatalogStore:
        return FileMcpCatalogStore(root)

    def seeded(self, root: Path, names: Sequence[str] = ()) -> FileMcpCatalogStore:
        """Return a store seeded from cards for ``names`` (default: one server)."""

        store = self.store(root)
        store.seed(
            McpCatalogBuilder.seed_all(
                tuple(
                    self.make_catalog_card(name=name)
                    for name in (names or (self.CatalogValues.SERVER,))
                )
            )
        )
        return store

    def load_into(
        self,
        store: FileMcpCatalogStore,
        *,
        server_name: str | None = None,
        tools: Sequence[McpToolDescriptor] | None = None,
    ) -> None:
        """Publish a LOADED catalog, exactly as ``load_mcp_server`` does."""

        card = self.make_catalog_card(name=server_name or self.CatalogValues.SERVER)
        store.publish(McpCatalogBuilder.build(self.make_loaded(card=card, tools=tools)))

    def server_dir(self, root: Path, server_name: str | None = None) -> Path:
        return root / (server_name or self.CatalogValues.SERVER)


class TestFilesAreRealOnDisk(FileCatalogMixin):
    def test_seeding_writes_a_server_markdown_a_human_can_open(
        self, tmp_path: Path
    ) -> None:
        self.seeded(tmp_path)

        markdown = self.server_dir(tmp_path) / Keys.File.SERVER_MARKDOWN
        assert markdown.is_file()
        body = markdown.read_text(encoding="utf-8")
        assert self.CatalogValues.SERVER in body
        # Honest about what a seed does NOT know: claiming "0 tools" for a
        # server nobody has opened is a lie the model would act on.
        assert Messages.Seed.TOOLS_UNKNOWN in body
        assert "load_mcp_server" in body

    def test_a_seeded_server_has_no_tools_directory(self, tmp_path: Path) -> None:
        # The absence of ``tools/`` IS the tier marker — that is how a store in
        # a NEW process knows this server has not been loaded, without a
        # sidecar state file for the two to disagree about.
        self.seeded(tmp_path)

        assert not (self.server_dir(tmp_path) / Keys.Dir.TOOLS).exists()

    def test_loading_writes_one_real_file_per_tool(self, tmp_path: Path) -> None:
        store = self.seeded(tmp_path)

        self.load_into(store)

        tools_dir = self.server_dir(tmp_path) / Keys.Dir.TOOLS
        files = sorted(path.name for path in tools_dir.iterdir())
        assert len(files) == self.CatalogValues.LARGE_TOOL_COUNT
        payload = json.loads((tools_dir / files[0]).read_text(encoding="utf-8"))
        assert payload[Keys.Field.SERVER_NAME] == self.CatalogValues.SERVER
        assert payload[Keys.Field.INPUT_SCHEMA]

    def test_artifacts_carry_the_scratch_file_mode(self, tmp_path: Path) -> None:
        # Same posture as the rest of the scratch: these are connector
        # descriptions written into the user's home directory.
        self.seeded(tmp_path)

        markdown = self.server_dir(tmp_path) / Keys.File.SERVER_MARKDOWN
        assert stat.S_IMODE(markdown.stat().st_mode) == 0o600

    def test_the_backend_serves_the_bytes_that_are_on_disk(
        self, tmp_path: Path
    ) -> None:
        store = self.seeded(tmp_path)
        self.load_into(store, tools=(self.make_catalog_tool(self.Values.SEARCH_TOOL),))

        backend = McpCatalogBackend(store)
        result = backend.read(self.tool_file_path(self.Values.SEARCH_TOOL))

        on_disk = (
            self.server_dir(tmp_path)
            / Keys.Dir.TOOLS
            / f"{self.invoke_name(self.Values.SEARCH_TOOL)}{Keys.Ext.JSON}"
        ).read_text(encoding="utf-8")
        assert result.error is None
        assert result.file_data is not None
        assert result.file_data["content"] == on_disk


class TestCatalogOutlivesTheHarness(FileCatalogMixin):
    def test_a_second_store_over_the_same_directory_sees_the_loaded_tree(
        self, tmp_path: Path
    ) -> None:
        # Turn 2, and the second harness an approval resume builds. Two
        # in-process stores would both be empty here; that is the whole bug.
        first = self.seeded(tmp_path)
        self.load_into(first, tools=(self.make_catalog_tool(self.Values.SEARCH_TOOL),))

        second = self.store(tmp_path)

        assert second.loaded_server_names() == (self.CatalogValues.SERVER,)
        assert self.tool_file_path(self.Values.SEARCH_TOOL) in second.snapshot()

    def test_a_later_seed_pass_never_un_loads_a_server(self, tmp_path: Path) -> None:
        first = self.seeded(tmp_path)
        self.load_into(first)

        outcome = self.seeded(tmp_path).seed(
            McpCatalogBuilder.seed_all(
                (self.make_catalog_card(name=self.CatalogValues.SERVER),)
            )
        )

        assert outcome.retained == (self.CatalogValues.SERVER,)
        assert outcome.seeded == ()
        tools_dir = self.server_dir(tmp_path) / Keys.Dir.TOOLS
        assert len(list(tools_dir.iterdir())) == self.CatalogValues.LARGE_TOOL_COUNT

    def test_a_stub_is_refreshed_rather_than_retained(self, tmp_path: Path) -> None:
        self.seeded(tmp_path)

        outcome = self.store(tmp_path).seed(
            McpCatalogBuilder.seed_all(
                (self.make_catalog_card(name=self.CatalogValues.SERVER),)
            )
        )

        # A stub carries no descriptor, so rewriting it is free and keeps the
        # auth/health status line current between runs.
        assert outcome.seeded == (self.CatalogValues.SERVER,)
        assert outcome.retained == ()

    def test_an_unauthorized_server_directory_is_pruned(self, tmp_path: Path) -> None:
        store = self.seeded(
            tmp_path, names=(self.CatalogValues.SERVER, self.Values.OTHER_SERVER)
        )
        self.load_into(store, server_name=self.Values.OTHER_SERVER)

        outcome = self.store(tmp_path).seed(
            McpCatalogBuilder.seed_all(
                (self.make_catalog_card(name=self.CatalogValues.SERVER),)
            )
        )

        # In-memory this never came up — the store died with the process. On
        # disk a removed connector's schemas would sit there being read.
        assert outcome.pruned == (self.Values.OTHER_SERVER,)
        assert not self.server_dir(tmp_path, self.Values.OTHER_SERVER).exists()


class TestEveryCompositionReadsTheSameBytes(FileCatalogMixin):
    def test_two_separately_composed_backends_share_one_catalog(
        self, tmp_path: Path
    ) -> None:
        # Deep Agents hands ONE backend object to the supervisor and every
        # subagent, so this is stricter than production needs — two stores,
        # two backends, composed independently. Two in-memory stores would
        # disagree; two views of one directory cannot.
        supervisor_store = self.seeded(tmp_path)
        subagent_store = self.store(tmp_path)
        supervisor = McpCatalogBackend(supervisor_store)
        subagent = McpCatalogBackend(subagent_store)

        self.load_into(
            supervisor_store, tools=(self.make_catalog_tool(self.Values.SEARCH_TOOL),)
        )

        path = self.tool_file_path(self.Values.SEARCH_TOOL)
        supervisor_read = supervisor.read(path)
        subagent_read = subagent.read(path)
        assert subagent_read.error is None
        assert supervisor_read.file_data == subagent_read.file_data

    def test_grep_reaches_a_tool_written_by_the_other_composition(
        self, tmp_path: Path
    ) -> None:
        writer = self.seeded(tmp_path)
        reader = McpCatalogBackend(self.store(tmp_path))

        self.load_into(writer, tools=(self.make_catalog_tool(self.Values.SEARCH_TOOL),))

        matches = reader.grep(
            self.Values.SEARCH_TOOL,
            path=McpCatalogPaths.server_dir(self.CatalogValues.SERVER),
        )
        assert matches.matches


class TestStillReadOnlyToTheModel(FileCatalogMixin):
    async def test_writes_edits_and_deletes_are_refused(self, tmp_path: Path) -> None:
        store = self.seeded(tmp_path)
        backend = McpCatalogBackend(store)
        path = McpCatalogPaths.server_markdown(self.CatalogValues.SERVER)
        before = (self.server_dir(tmp_path) / Keys.File.SERVER_MARKDOWN).read_text(
            encoding="utf-8"
        )

        assert backend.write(path, "x").error == Messages.READ_ONLY
        assert backend.edit(path, "a", "b").error == Messages.READ_ONLY
        assert (await backend.adelete(path)).error == Messages.READ_ONLY
        assert (await backend.amkdir("/linear/extra")).error == Messages.READ_ONLY

        # Real files make this worth asserting rather than assuming: a refusal
        # that still touched the disk would be a worse bug than the blob.
        assert (self.server_dir(tmp_path) / Keys.File.SERVER_MARKDOWN).read_text(
            encoding="utf-8"
        ) == before


class TestPathsCannotEscapeTheServerDirectory(FileCatalogMixin):
    def test_a_traversing_artifact_path_is_dropped_not_written(
        self, tmp_path: Path
    ) -> None:
        store = self.store(tmp_path)
        catalog = McpCatalogBuilder.build(self.make_loaded())
        escaping = catalog.model_copy(
            update={
                "files": (
                    catalog.server_markdown,
                    catalog.server_markdown.model_copy(
                        update={"path": "/mcp/linear/../../escaped.md"}
                    ),
                )
            }
        )

        store.publish(escaping)

        assert not (tmp_path.parent / "escaped.md").exists()
        assert not (tmp_path / "escaped.md").exists()


class TestWorkerWiringGate(FileCatalogMixin):
    def test_a_desktop_run_gets_a_file_store_under_the_conversation_scratch(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv(COPILOT_HOME_ENV, str(tmp_path / "home"))

        store = AgentScratchWorkerWiring(workspace_backend=object()).mcp_catalog_store(
            conversation_id=self.Values.CONVERSATION
        )

        assert isinstance(store, FileMcpCatalogStore)
        expected = (
            agent_scratch_root().conversation(self.Values.CONVERSATION).path
            / MCP_DIR_NAME
        )
        assert store.root == expected
        assert expected.is_dir()

    def test_a_hosted_run_declines_and_says_why(self, caplog) -> None:
        with caplog.at_level("INFO"):
            store = AgentScratchWorkerWiring(workspace_backend=None).mcp_catalog_store(
                conversation_id=self.Values.CONVERSATION
            )

        assert store is None
        # A future live "ls /mcp was empty" report has to be answerable from
        # the log alone; a silent decline is what makes that impossible.
        assert "mcp_catalog.store_declined reason=not_desktop" in caplog.text

    def test_an_unusable_conversation_id_declines_and_says_why(
        self, tmp_path: Path, monkeypatch, caplog
    ) -> None:
        monkeypatch.setenv(COPILOT_HOME_ENV, str(tmp_path / "home"))

        with caplog.at_level("WARNING"):
            store = AgentScratchWorkerWiring(
                workspace_backend=object()
            ).mcp_catalog_store(conversation_id="../not an id")

        assert store is None
        assert (
            "mcp_catalog.store_declined reason=unusable_conversation_id" in caplog.text
        )
        # The id may be user content, so it must never reach the log line.
        assert "not an id" not in caplog.text


class TestComposedRuntimeWritesRealFiles(CatalogWiringMixin, FileCatalogMixin):
    """``acreate_agent_runtime`` end to end, with the desktop store injected."""

    async def test_run_start_seeds_the_conversation_directory_on_disk(
        self, tmp_path: Path, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        dependencies = self.live_dependencies().model_copy(
            update={"mcp_catalog_store": FileMcpCatalogStore(tmp_path)}
        )
        builder = CapturingBuilder()

        await acreate_agent_runtime(
            context=runtime_context_admin,
            dependencies=dependencies,
            agent_builder=builder,
        )

        # No load call: composing the runtime is what put files here.
        markdown = (
            tmp_path / self.TestValues.Names.DRIVE_MCP / Keys.File.SERVER_MARKDOWN
        )
        assert markdown.is_file()
        backend = getattr(builder.calls[0], "memory_backend")
        assert self.entry_paths(backend.ls("/mcp")) == [
            f"/mcp/{self.TestValues.Names.DRIVE_MCP}/"
        ]

    async def test_loading_a_server_enriches_the_directory_on_disk(
        self, tmp_path: Path, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        dependencies = self.live_dependencies().model_copy(
            update={"mcp_catalog_store": FileMcpCatalogStore(tmp_path)}
        )
        builder = CapturingBuilder()
        await acreate_agent_runtime(
            context=runtime_context_admin,
            dependencies=dependencies,
            agent_builder=builder,
        )
        load_tool = self.named_tool(
            getattr(builder.calls[0], "tools"), "load_mcp_server"
        )

        await load_tool.ainvoke(
            {
                "args": {"server_name": self.TestValues.Names.DRIVE_MCP},
                "name": "load_mcp_server",
                "type": "tool_call",
                "id": "call_file_catalog_1",
            }
        )

        tool_file = (
            tmp_path
            / self.TestValues.Names.DRIVE_MCP
            / Keys.Dir.TOOLS
            / f"{self.invoke_name(self.TestValues.Names.DRIVE_SEARCH, server=self.TestValues.Names.DRIVE_MCP)}{Keys.Ext.JSON}"
        )
        assert tool_file.is_file()
        # And the NEXT harness — turn 2, or the approval-resume harness — reads
        # it back without a second load.
        assert self.store(tmp_path).loaded_server_names() == (
            self.TestValues.Names.DRIVE_MCP,
        )


class TestFallbackStoreIsUnchanged(FileCatalogMixin):
    def test_the_in_memory_store_still_satisfies_the_same_seam(self) -> None:
        # The non-desktop path composes this one, so the two must stay
        # behaviourally interchangeable — one catalog per run either way.
        store = McpCatalogStore()

        outcome = store.seed(
            McpCatalogBuilder.seed_all(
                (self.make_catalog_card(name=self.CatalogValues.SERVER),)
            )
        )

        assert outcome.seeded == (self.CatalogValues.SERVER,)
        assert McpCatalogBackend(store).ls("/").entries
