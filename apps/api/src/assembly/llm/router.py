"""Per-stage model routing.

Stages are coarse — synthesis-heavy (aggregation, parser) gets the premium
model; agent role-play (per-round per-agent) gets the cheaper model.

Stage-to-model mapping is configurable via settings so deployments can pin
specific model ids without code changes."""
from __future__ import annotations

from typing import Literal

from assembly.config import get_settings

StageType = Literal["synthesis", "roleplay", "extraction"]


# Stage labels used throughout the pipeline. Keep in sync with `llm_call_log.stage`.
STAGE_KIND: dict[str, StageType] = {
    # Phase 4 — high-stakes synthesis
    "intake_parser": "synthesis",
    "evidence_extractor": "extraction",
    # Phase 5
    "society_builder": "synthesis",
    # Phase 6 — per-agent per-round role-play; many calls, prefer cheap
    "round_baseline": "roleplay",
    "round_first_exposure": "roleplay",
    "round_objection_formation": "roleplay",
    "round_competitor_comparison": "roleplay",
    "round_proof_exposure": "roleplay",
    "round_social_influence": "roleplay",
    "round_final_stance": "roleplay",
    # Phase 8.2F — persona trait extraction. Short structured-output task
    # over redacted snippets; Sonnet is sufficient and ~7× cheaper than
    # Opus, which keeps bounded write-mode pilots inside the $2 cap.
    "persona_trait_extraction": "roleplay",
    # Phase 7 — synthesis
    "aggregation_sentiment": "synthesis",
    "aggregation_persuasion": "synthesis",
    "aggregation_acceptance": "synthesis",
    "aggregation_trajectory": "synthesis",
    "aggregation_competitor": "synthesis",
    "aggregation_recommendations": "synthesis",
    "aggregation_debate_shifts": "synthesis",
    "aggregation_confidence": "synthesis",
    "aggregation_evidence_ledger": "synthesis",
}


def stage_kind(stage: str) -> StageType:
    """Return the kind of LLM work `stage` performs. Defaults to 'synthesis'
    (the more expensive option) for unknown stages, so unknown work doesn't
    accidentally route to a cheaper / less capable model."""
    return STAGE_KIND.get(stage, "synthesis")


def pick_model_for_stage(stage: str) -> str:
    """Return a model id for the given stage. Reads from settings:
        ASSEMBLY_LLM_SYNTHESIS_MODEL  → for stage_kind == 'synthesis' or 'extraction'
        ASSEMBLY_LLM_ROLEPLAY_MODEL   → for stage_kind == 'roleplay'
    """
    s = get_settings()
    kind = stage_kind(stage)
    if kind == "roleplay":
        return s.llm_roleplay_model
    return s.llm_synthesis_model
