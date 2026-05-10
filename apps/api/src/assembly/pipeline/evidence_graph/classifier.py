"""Phase 6.75 — evidence node classifier.

Two passes:
  1. Deterministic — based on existing `source_type`, `kind`, and metadata.
     Most rows resolve here without an LLM call.
  2. LLM fallback (optional) — only for rows that stay `unknown` after pass 1.
     Goes through `cost_guarded_chat` so every call is logged + cap-checked.

The closed `NODE_CLASSES` enum lives in `assembly.models.evidence`. The
classifier never invents a class; out-of-set values are rejected.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.llm.guarded_chat import cost_guarded_chat
from assembly.llm.provider import LLMMessage, LLMProvider
from assembly.models.evidence import NODE_CLASSES, EvidenceItem

logger = logging.getLogger(__name__)


# Threshold below which the LLM classifier's confidence is treated as
# unknown (per Section 2 of the plan).
LLM_CONFIDENCE_THRESHOLD: Decimal = Decimal("0.5")


def deterministic_class(item: EvidenceItem) -> tuple[str, Decimal]:
    """Return (node_class, confidence) for cases the deterministic rules
    cover. Falls back to ('unknown', 0) when no rule matches."""
    if item.kind == "missing":
        # Missing-evidence rows already carry the "what was expected" hint
        # in metadata; map common product-type-keyed missing buckets to
        # node classes deterministically.
        meta = item.metadata_ or {}
        expected = (meta.get("expected_kind") or "").lower()
        if "pricing" in expected:
            return "pricing", Decimal("1.0")
        if "review" in expected or "case_study" in expected:
            return "review", Decimal("1.0")
        if "competitor" in expected:
            return "competitor", Decimal("1.0")
        if "category_language" in expected:
            return "category_language", Decimal("1.0")
        # Missing without a hint stays 'unknown' — the LLM doesn't get to
        # guess what was missing; that information is structural.
        return "unknown", Decimal("0")

    st = item.source_type
    if st == "competitor_page":
        return "competitor", Decimal("1.0")
    if st == "pricing_page":
        return "pricing", Decimal("1.0")
    if st == "public_review":
        return "review", Decimal("1.0")
    if st == "category_language":
        return "category_language", Decimal("1.0")
    if st == "analogical_market":
        return "analogical_market", Decimal("1.0")
    if st == "user_input":
        # The user's competitors block has input_field='competitors' in metadata.
        meta = item.metadata_ or {}
        field = (meta.get("input_field") or "").lower()
        if "competitor" in field:
            return "competitor", Decimal("1.0")
        if "price" in field:
            return "pricing", Decimal("1.0")
        if "target_society" in field:
            return "segment_behavior", Decimal("0.8")
        if "user_description" in field:
            return "buyer_pain", Decimal("0.6")
        # User input without a more specific hint: leave unknown so the LLM
        # can refine if configured, otherwise it stays as a low-confidence
        # buyer-context anchor.
        return "unknown", Decimal("0")
    return "unknown", Decimal("0")


# ---------------------------------------------------------------------------
# LLM fallback
# ---------------------------------------------------------------------------


class _ClassifyOne(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str
    node_class: str
    confidence: float = Field(ge=0.0, le=1.0)


class _ClassifyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    classifications: list[_ClassifyOne]


_PROMPT = """You are classifying short evidence excerpts into ONE class from this CLOSED set:

{node_classes}

Rules:
- Use ONLY the listed classes. If unsure, return "unknown" with low confidence.
- Do NOT invent classes.
- Do NOT change the kind (direct/analogical/missing). Only label.
- Confidence in [0, 1]; values below 0.5 will be treated as 'unknown'.

Return ONLY a JSON object: {{"classifications": [{{"evidence_id": "...", "node_class": "...", "confidence": 0..1}}]}}"""


async def llm_classify(
    *,
    items: Sequence[EvidenceItem],
    sessionmaker: async_sessionmaker,
    simulation_id: UUID,
    provider: LLMProvider,
    model: str,
    stage: str = "node_classifier",
) -> dict[UUID, tuple[str, Decimal]]:
    """LLM fallback: classify the supplied items. Returns a dict
    `{evidence_id: (node_class, confidence)}` for items the LLM was willing
    to label; items it refused stay un-classified (caller leaves them at
    'unknown')."""
    if not items:
        return {}

    payload = [
        {
            "evidence_id": str(it.id),
            "kind": it.kind,
            "source_type": it.source_type,
            "content": (it.content or "")[:400],
            "source_url": it.source_url,
        }
        for it in items
    ]
    system = _PROMPT.format(node_classes=", ".join(NODE_CLASSES))
    user = (
        "Classify each item in the array. Return one entry per evidence_id.\n\n"
        f"```json\n{json.dumps(payload, indent=2)}\n```\n\nReturn ONLY the JSON."
    )
    messages = [
        LLMMessage(role="system", content=system),
        LLMMessage(role="user", content=user),
    ]

    response = await cost_guarded_chat(
        sessionmaker=sessionmaker,
        simulation_id=simulation_id,
        stage=stage,
        messages=messages,
        provider=provider,
        model=model,
        max_tokens=4096,
        temperature=0.0,
    )

    try:
        cleaned = response.text.strip()
        if cleaned.startswith("```"):
            nl = cleaned.find("\n")
            if nl != -1:
                cleaned = cleaned[nl + 1 :]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
        parsed = _ClassifyResponse.model_validate(json.loads(cleaned))
    except (json.JSONDecodeError, ValidationError) as e:
        logger.warning("classifier.parse_failed err=%s", e)
        return {}

    out: dict[UUID, tuple[str, Decimal]] = {}
    item_ids = {str(it.id) for it in items}
    for c in parsed.classifications:
        if c.evidence_id not in item_ids:
            # Anti-hallucination: classifier returned an id not in our batch.
            logger.warning(
                "classifier.invented_id id=%s simulation=%s",
                c.evidence_id, simulation_id,
            )
            continue
        if c.node_class not in NODE_CLASSES:
            logger.warning(
                "classifier.invalid_class class=%s id=%s simulation=%s",
                c.node_class, c.evidence_id, simulation_id,
            )
            continue
        conf = Decimal(str(c.confidence))
        if conf < LLM_CONFIDENCE_THRESHOLD:
            out[UUID(c.evidence_id)] = ("unknown", conf)
        else:
            out[UUID(c.evidence_id)] = (c.node_class, conf)
    return out


async def classify_all(
    *,
    sessionmaker: async_sessionmaker,
    simulation_id: UUID,
    provider: LLMProvider | None = None,
    model: str | None = None,
    use_llm_fallback: bool = True,
) -> int:
    """Run both passes against every evidence_items row for this simulation.
    Persists `node_class` + `node_class_confidence` updates. Returns the
    count of rows whose class was changed by this run."""
    changed = 0
    async with sessionmaker() as session:
        items = (
            await session.execute(
                select(EvidenceItem).where(
                    EvidenceItem.simulation_id == simulation_id
                )
            )
        ).scalars().all()

        # Pass 1 — deterministic.
        residual: list[EvidenceItem] = []
        for item in items:
            klass, conf = deterministic_class(item)
            if klass != "unknown":
                if item.node_class != klass or item.node_class_confidence != conf:
                    item.node_class = klass
                    item.node_class_confidence = conf
                    changed += 1
            elif item.node_class == "unknown":
                residual.append(item)

        await session.commit()

    # Pass 2 — LLM fallback for residual items.
    if use_llm_fallback and residual and provider is not None and model is not None:
        labels = await llm_classify(
            items=residual,
            sessionmaker=sessionmaker,
            simulation_id=simulation_id,
            provider=provider,
            model=model,
        )
        if labels:
            async with sessionmaker() as session:
                async with session.begin():
                    for evidence_id, (klass, conf) in labels.items():
                        await session.execute(
                            EvidenceItem.__table__.update()
                            .where(EvidenceItem.id == evidence_id)
                            .values(
                                node_class=klass,
                                node_class_confidence=conf,
                            )
                        )
                        changed += 1

    return changed
