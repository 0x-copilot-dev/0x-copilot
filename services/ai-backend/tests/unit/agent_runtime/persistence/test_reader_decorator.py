"""C10 reader-decorator tests."""

from __future__ import annotations

from agent_runtime.persistence._reader import READER_ATTR, reader


class TestReaderDecorator:
    def test_decorator_marks_method(self) -> None:
        @reader
        async def query_thing() -> int:
            return 1

        assert getattr(query_thing, READER_ATTR) is True

    def test_decorator_preserves_callable(self) -> None:
        @reader
        async def query_thing() -> int:
            return 42

        # Body still runs as expected.
        import asyncio

        assert asyncio.run(query_thing()) == 42

    def test_unmarked_method_has_no_attribute(self) -> None:
        async def write_thing() -> None:
            return None

        assert not getattr(write_thing, READER_ATTR, False)
