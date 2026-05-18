"""Unit tests for the pure RRF fusion + excerpt helpers — P7.5-A4.

The hybrid search engine has many moving parts but RRF + excerpt are
the two pure functions the SOT depends on. Test them in isolation so a
regression in the fusion math is caught here, not at the HTTP layer.
"""

from __future__ import annotations

from backend_app.library.search import (
    RRF_K_DEFAULT,
    FusedHit,
    SearchHit,
    excerpt,
    rrf_fuse,
)


def _hit(record_id: str, kind: str = "page", score: float = 1.0) -> SearchHit:
    return SearchHit(record_id=record_id, kind=kind, score=score)


class TestRrfFuse:
    def test_identical_ranks_produce_identical_scores(self) -> None:
        """Two docs at the same effective rank pair fuse to the same
        score — RRF is rank-only, the per-leg raw score is intentionally
        ignored. 'a' at rank 1 in BM25 + rank 2 in vector matches 'b'
        at rank 2 in BM25 + rank 1 in vector."""

        bm25 = [_hit("a", score=10.0), _hit("b", score=1.0)]
        # 'b' first in vector, 'a' second — flip the ordering.
        vector = [_hit("b", score=0.9), _hit("a", score=0.1)]
        fused = rrf_fuse(bm25, vector)

        scores = {h.record_id: h.score for h in fused}
        # Both 'a' and 'b' appear at rank 1 in one leg + rank 2 in the
        # other → identical RRF score.
        assert scores["a"] == scores["b"]
        # The formula: 1/(60+1) + 1/(60+2).
        expected = 1.0 / (RRF_K_DEFAULT + 1) + 1.0 / (RRF_K_DEFAULT + 2)
        assert abs(scores["a"] - expected) < 1e-9

    def test_one_leg_only_falls_back_to_single_leg_order(self) -> None:
        """When the vector leg is empty (no embeddings configured), the
        fused ranking matches the BM25 leg ordering exactly. This is the
        ``bm25_only`` strategy at the route layer."""

        bm25 = [_hit("a"), _hit("b"), _hit("c")]
        fused = rrf_fuse(bm25, [])

        assert [h.record_id for h in fused] == ["a", "b", "c"]
        # Scores monotonically decreasing per RRF.
        scores = [h.score for h in fused]
        assert scores == sorted(scores, reverse=True)

    def test_empty_inputs_produce_empty_output(self) -> None:
        assert rrf_fuse([], []) == ()

    def test_union_of_legs_no_duplicates(self) -> None:
        """A doc appearing in both legs is present exactly once in the
        fused output, scored from BOTH legs (sum)."""

        bm25 = [_hit("a"), _hit("b")]
        vector = [_hit("b"), _hit("c")]
        fused = rrf_fuse(bm25, vector)

        ids = [h.record_id for h in fused]
        assert ids.count("b") == 1
        assert set(ids) == {"a", "b", "c"}
        # 'b' is at rank 2 in BM25 + rank 1 in vector.
        b_score = next(h.score for h in fused if h.record_id == "b")
        expected_b = 1.0 / (RRF_K_DEFAULT + 2) + 1.0 / (RRF_K_DEFAULT + 1)
        assert abs(b_score - expected_b) < 1e-9

    def test_bm25_rank_and_vector_rank_recorded(self) -> None:
        bm25 = [_hit("a"), _hit("b")]
        vector = [_hit("b"), _hit("c")]
        fused = rrf_fuse(bm25, vector)

        by_id = {h.record_id: h for h in fused}
        # 'a' bm25-only → vector_rank None.
        assert by_id["a"].bm25_rank == 1
        assert by_id["a"].vector_rank is None
        # 'b' in both legs.
        assert by_id["b"].bm25_rank == 2
        assert by_id["b"].vector_rank == 1
        # 'c' vector-only at rank 2 (vector list is [b, c]).
        assert by_id["c"].bm25_rank is None
        assert by_id["c"].vector_rank == 2

    def test_returns_tuple(self) -> None:
        # Pure function contract — immutable result so callers cannot
        # mutate the fused list in place and corrupt later consumers.
        result = rrf_fuse([_hit("a")], [])
        assert isinstance(result, tuple)
        assert isinstance(result[0], FusedHit)

    def test_custom_k_changes_smoothing(self) -> None:
        """Higher k flattens the rank gaps. Spot-check the math."""

        bm25 = [_hit("a"), _hit("b")]
        fused_60 = rrf_fuse(bm25, [], k=60)
        fused_10 = rrf_fuse(bm25, [], k=10)
        # At k=10 the gap between ranks is more pronounced.
        gap_60 = fused_60[0].score - fused_60[1].score
        gap_10 = fused_10[0].score - fused_10[1].score
        assert gap_10 > gap_60


class TestExcerpt:
    def test_returns_marked_snippet_around_match(self) -> None:
        text = (
            "The launch checklist covers approvals, demo prep, and "
            "comms. Make sure the launch is approved by Friday."
        )
        result = excerpt(text, query="launch")
        assert "<mark>launch</mark>" in result
        assert len(result) <= len(text) + 32  # marks add bytes

    def test_empty_text_returns_empty(self) -> None:
        assert excerpt("", query="anything") == ""

    def test_empty_query_returns_empty(self) -> None:
        assert excerpt("hello world", query="") == ""

    def test_query_with_no_match_truncates_text(self) -> None:
        text = "alpha beta gamma delta epsilon"
        result = excerpt(text, query="zeta", target_chars=10)
        # No match → first ``target_chars`` of text (no marks).
        assert "<mark>" not in result
        assert result.startswith("alpha")

    def test_html_is_escaped_before_marks(self) -> None:
        text = "<script>alert(1)</script> launch the missile"
        result = excerpt(text, query="launch")
        # Pre-existing ``<`` is escaped before our marks land, so the
        # only ``<mark>`` in the result is the one we added.
        assert "<script>" not in result
        assert "&lt;script&gt;" in result
        assert "<mark>launch</mark>" in result

    def test_case_insensitive_match(self) -> None:
        result = excerpt("LAUNCH the rocket", query="launch")
        assert "<mark>LAUNCH</mark>" in result
