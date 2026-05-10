"""Phase 9A.4 — human-like discussion layer package.

Universal building blocks for Assembly's first artificial society
discussion layer. Reads 9A.2 personas + 9A.3 psychology + prior
simulation responses; emits structured discussion turns + private
ballots + grounded memory atoms.

NO new retrieval. NO LLM calls inside the package — those go through
`scripts/run_discussion_layer_9a_4.py` via `cost_guarded_chat`. NO
LumaLoop hardcoding.
"""
from assembly.sources.discussion_layer.evaluator import (
    DiscussionQualityScores,
    evaluate_discussion_quality,
    evaluate_scaled_discussion_quality,
)
from assembly.sources.discussion_layer.retry import call_with_retry
from assembly.sources.discussion_layer.group_assignment import (
    assign_groups_stratified,
)
from assembly.sources.discussion_layer.memory import (
    MemoryAtomDraft,
    build_seed_memory_atoms,
    rank_memory_atoms,
)
from assembly.sources.discussion_layer.report import (
    render_discussion_report_json,
    render_discussion_report_markdown,
)
from assembly.sources.discussion_layer.schemas import (
    DiscussionStance,
    PrivateBallotDraft,
    PsychologyControlSnapshot,
    TurnDraft,
)
from assembly.sources.discussion_layer.validators import (
    classify_public_private_delta,
    detect_overcooperation,
    forbidden_claim_audit,
    sensitive_inference_audit,
)


__all__ = [
    "DiscussionQualityScores",
    "DiscussionStance",
    "MemoryAtomDraft",
    "PrivateBallotDraft",
    "PsychologyControlSnapshot",
    "TurnDraft",
    "assign_groups_stratified",
    "build_seed_memory_atoms",
    "call_with_retry",
    "classify_public_private_delta",
    "detect_overcooperation",
    "evaluate_discussion_quality",
    "evaluate_scaled_discussion_quality",
    "forbidden_claim_audit",
    "rank_memory_atoms",
    "render_discussion_report_json",
    "render_discussion_report_markdown",
    "sensitive_inference_audit",
]
