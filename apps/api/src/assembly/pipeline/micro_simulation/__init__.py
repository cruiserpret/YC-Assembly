"""Phase 8.2K — micro-simulation harness.

NOT a market simulation. NOT a population study. NOT user-facing.
A mechanical proof that the persona-node + reaction-round + debate
machinery is correct, run on the smallest possible audience pool.

Public surface:
  - run_micro_simulation()              top-level orchestrator
  - load_micro_persona_state()          DB-aware persona-state loader
  - run_baseline_round()                deterministic baseline
  - run_llm_round()                     one LLM round per persona
  - run_debate_turn()                   one pairwise turn
  - audit_full_trace_and_summary()      forbidden-language scanner
  - MicroSimulationResult / -Trace / -PersonaState / -*Stance closed enums

Drift test in `tests/test_no_drift_micro_simulation.py` enforces:
  - no provider.chat / structured_output / embed direct calls
  - no population-graph or Phase 7 ORM-row construction
  - no frontend references
"""
from assembly.pipeline.micro_simulation.debate import run_debate_turn
from assembly.pipeline.micro_simulation.llm_call import (
    STAGE_BASELINE,
    STAGE_DEBATE,
    STAGE_FINAL_STANCE,
    STAGE_FIRST_EXPOSURE,
    STAGE_OBJECTION,
    micro_llm_call,
)
from assembly.pipeline.micro_simulation.output_audit import (
    COVERAGE_THINNESS_MARKERS,
    MICRO_TEST_MARKERS,
    SAMPLE_SIZE_CAVEAT_MARKERS,
    audit_debate_turn,
    audit_full_trace_and_summary,
    audit_round_result,
    has_marker,
    scan_text_for_forbidden_claims,
)
from assembly.pipeline.micro_simulation.persona_state import (
    MicroPersonaStateLoadError,
    load_micro_persona_state,
)
from assembly.pipeline.micro_simulation.rounds import (
    run_baseline_round,
    run_llm_round,
)
from assembly.pipeline.micro_simulation.runner import (
    DEFAULT_COST_CAP_USD,
    MicroSimulationRefused,
    run_micro_simulation,
)
from assembly.pipeline.micro_simulation.schemas import (
    MarketEntryFinalStance,
    MicroDebateTurn,
    MicroPersonaState,
    MicroRelevanceLabel,
    MicroRoundKind,
    MicroRoundResult,
    MicroSimulationOutputAudit,
    MicroSimulationResult,
    MicroStance,
    MicroTrace,
    map_micro_stance_to_market_entry,
)


__all__ = [
    "COVERAGE_THINNESS_MARKERS",
    "DEFAULT_COST_CAP_USD",
    "MICRO_TEST_MARKERS",
    "MarketEntryFinalStance",
    "MicroDebateTurn",
    "MicroPersonaState",
    "MicroPersonaStateLoadError",
    "MicroRelevanceLabel",
    "MicroRoundKind",
    "MicroRoundResult",
    "MicroSimulationOutputAudit",
    "MicroSimulationRefused",
    "MicroSimulationResult",
    "MicroStance",
    "MicroTrace",
    "map_micro_stance_to_market_entry",
    "SAMPLE_SIZE_CAVEAT_MARKERS",
    "STAGE_BASELINE",
    "STAGE_DEBATE",
    "STAGE_FINAL_STANCE",
    "STAGE_FIRST_EXPOSURE",
    "STAGE_OBJECTION",
    "audit_debate_turn",
    "audit_full_trace_and_summary",
    "audit_round_result",
    "has_marker",
    "load_micro_persona_state",
    "micro_llm_call",
    "run_baseline_round",
    "run_debate_turn",
    "run_llm_round",
    "run_micro_simulation",
    "scan_text_for_forbidden_claims",
]
