"""Phase 8.2F — grouping tests (pure, no DB)."""
from __future__ import annotations

from uuid import UUID, uuid4

from assembly.pipeline.persona_construction.grouping import (
    CandidateRecord,
    group_records_into_shells,
)
from assembly.pipeline.persona_construction.source_classifier import (
    SourceClassification,
    SourceClassificationReport,
)


def _strong_record(
    *,
    record_id: UUID | None = None,
    source_url: str | None = None,
    user_handle_hash: str | None = None,
    content: str = "stub strong-signal content for grouping test",
    metadata: dict | None = None,
) -> CandidateRecord:
    return CandidateRecord(
        record_id=record_id or uuid4(),
        source_kind="tavily_search_extract",
        source_url=source_url,
        user_handle_hash=user_handle_hash,
        content=content,
        metadata=metadata or {},
        classification=SourceClassificationReport(
            classification=SourceClassification.STRONG_PERSONA_SIGNAL,
            persona_voice_score=10,
            article_marketing_score=0,
            url_shape_score=4,
            rationale=("test fixture",),
        ),
    )


def _context_record(**overrides) -> CandidateRecord:
    base = _strong_record(**overrides)
    return CandidateRecord(
        **{
            **base.__dict__,
            "classification": SourceClassificationReport(
                classification=SourceClassification.CONTEXT_ONLY,
                persona_voice_score=0,
                article_marketing_score=10,
                url_shape_score=-4,
                rationale=("test fixture context-only",),
            ),
        }
    )


# ---------------------------------------------------------------------------
# Same user_handle_hash → one shell
# ---------------------------------------------------------------------------


def test_same_handle_hash_groups_into_one_shell() -> None:
    h = "deadbeef" * 8
    r1 = _strong_record(user_handle_hash=h, source_url="https://a.test/1")
    r2 = _strong_record(user_handle_hash=h, source_url="https://b.test/2")
    shells = group_records_into_shells([r1, r2])
    assert len(shells) == 1
    assert set(shells[0].record_ids) == {r1.record_id, r2.record_id}


# ---------------------------------------------------------------------------
# No handle, different domains → separate shells
# ---------------------------------------------------------------------------


def test_no_handle_different_domains_do_not_overgroup() -> None:
    r1 = _strong_record(source_url="https://reddit.test/r/shopify/aaa")
    r2 = _strong_record(source_url="https://forum.test/threads/bbb")
    shells = group_records_into_shells([r1, r2])
    assert len(shells) == 2


# ---------------------------------------------------------------------------
# No handle, same source_url → one shell
# ---------------------------------------------------------------------------


def test_same_source_url_groups_into_one_shell() -> None:
    url = "https://forum.test/threads/12345"
    r1 = _strong_record(source_url=url)
    r2 = _strong_record(source_url=url)
    shells = group_records_into_shells([r1, r2])
    assert len(shells) == 1


# ---------------------------------------------------------------------------
# Same query / source_kind alone is not enough to group
# ---------------------------------------------------------------------------


def test_same_query_alone_does_not_group() -> None:
    metadata = {"query": "shopify plugin bloat"}
    r1 = _strong_record(
        source_url="https://reddit.test/x", metadata=metadata,
    )
    r2 = _strong_record(
        source_url="https://forum.test/y", metadata=metadata,
    )
    shells = group_records_into_shells([r1, r2])
    assert len(shells) == 2  # different URLs → separate shells


# ---------------------------------------------------------------------------
# context_only / rejected records are excluded
# ---------------------------------------------------------------------------


def test_context_only_records_excluded() -> None:
    r_strong = _strong_record(source_url="https://forum.test/strong")
    r_ctx = _context_record(source_url="https://blog.test/article")
    shells = group_records_into_shells([r_strong, r_ctx])
    assert len(shells) == 1
    assert r_strong.record_id in shells[0].record_ids
    assert r_ctx.record_id not in shells[0].record_ids


def test_no_eligible_records_yields_no_shells() -> None:
    r1 = _context_record(source_url="https://blog.test/a")
    r2 = _context_record(source_url="https://blog.test/b")
    shells = group_records_into_shells([r1, r2])
    assert shells == []


# ---------------------------------------------------------------------------
# Shell content is aggregated with record-boundary separators
# ---------------------------------------------------------------------------


def test_shell_aggregates_content_with_separators() -> None:
    h = "abc" * 16
    r1 = _strong_record(
        user_handle_hash=h,
        source_url="https://forum.test/a",
        content="first record content here for grouping",
    )
    r2 = _strong_record(
        user_handle_hash=h,
        source_url="https://forum.test/b",
        content="second record content here for grouping",
    )
    shells = group_records_into_shells([r1, r2])
    assert len(shells) == 1
    s = shells[0]
    assert "### record 1" in s.aggregated_content
    assert "### record 2" in s.aggregated_content
    assert s.aggregated_metadata["record_count"] == 2
