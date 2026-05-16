"""Phase 11B.6 — schema regression test for `review_timestamp BIGINT`.

The McAuley Lab Amazon Reviews 2023 dataset stores review timestamps
as milliseconds-since-epoch (13-digit numbers ~1.6 trillion). A
32-bit INTEGER column overflows on insert. Phase 11A's column
declaration was Integer; Phase 11B.6 widened it to BigInteger.

This test pins the model column type so a future "let's shrink that
back to int" refactor doesn't silently re-break commit ingestion.
"""
from __future__ import annotations

from sqlalchemy import BigInteger
from sqlalchemy.types import BIGINT


def test_review_timestamp_column_is_bigint_in_orm_model() -> None:
    """ORM-level assertion: the dataclass column type is BigInteger."""
    from assembly.models.amazon_review_signal import AmazonReviewSignal
    col = AmazonReviewSignal.__table__.c.review_timestamp
    assert isinstance(col.type, (BigInteger, BIGINT)) or \
        col.type.python_type is int, (
        f"review_timestamp must be BigInteger (got {col.type!r})"
    )
    # Sanity: the column compiles to BIGINT in PostgreSQL.
    from sqlalchemy.dialects import postgresql
    compiled = col.type.compile(dialect=postgresql.dialect())
    assert compiled.upper() == "BIGINT", (
        f"review_timestamp compiles to {compiled!r}, expected BIGINT"
    )


def test_real_mcauley_timestamp_value_fits_python_int() -> None:
    """Document the actual range: McAuley 2023 ms timestamps land
    around 1.6 trillion. INTEGER (max ~2.15B) cannot hold this;
    BIGINT (max ~9.2 quintillion) easily can."""
    real_mcauley_ms = 1_602_133_857_705  # observed in real data
    INT32_MAX = 2_147_483_647
    INT64_MAX = 9_223_372_036_854_775_807
    assert real_mcauley_ms > INT32_MAX, (
        "test sample no longer represents the overflow case"
    )
    assert real_mcauley_ms < INT64_MAX, (
        "BIGINT not sufficient — would need numeric"
    )


def test_product_title_column_is_unbounded_text() -> None:
    """Phase 11B.6 widened product_title from VARCHAR(512) to TEXT
    because real Amazon All_Beauty + Health_and_Personal_Care
    titles can exceed 1,900 characters."""
    from sqlalchemy import Text
    from sqlalchemy.types import TEXT
    from assembly.models.amazon_review_signal import AmazonReviewSignal
    col = AmazonReviewSignal.__table__.c.product_title
    assert isinstance(col.type, (Text, TEXT)), (
        f"product_title must be Text/TEXT (got {col.type!r})"
    )
    # PostgreSQL TEXT has no length cap.
    length = getattr(col.type, "length", None)
    assert length is None, (
        f"product_title must be unbounded TEXT, got length={length}"
    )
