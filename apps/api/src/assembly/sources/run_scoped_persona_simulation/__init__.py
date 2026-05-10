"""Phase 8.5E — run-scoped persona simulation adapter + universal
validators + deterministic quality evaluator.

Universal:
  * Loader fetches personas/traits/evidence-links/sources by
    `run_scope_id` — never by hardcoded brief / product / role.
  * Validators (`launch-state`, `stance-label`, `forecast/verdict`)
    are universal — parameterized by `product_name` / closed stance
    set, never by per-product templates.
  * Quality evaluator scores 9 dimensions deterministically.

NO LLM. NO network. NO DB writes from this package — those happen
in the Phase 8.5E execution script that *consumes* this package.
"""

from assembly.sources.run_scoped_persona_simulation.loader import (
    RunScopedAgentContext, load_run_scoped_agents,
)
from assembly.sources.run_scoped_persona_simulation.quality_evaluator import (
    QualityEvaluation, evaluate_simulation_quality,
)
from assembly.sources.run_scoped_persona_simulation.schemas import (
    AGENT_ROUND_TYPES, MARKET_ENTRY_STANCES, RoundOutputAudit,
    RoundType, SimulationStanceLabel,
)
from assembly.sources.run_scoped_persona_simulation.validators import (
    UniversalClaimValidationResult,
    scan_forecast_or_verdict_claims,
    scan_unlaunched_product_use_claims,
    validate_market_entry_stance_label,
)

__all__ = [
    "AGENT_ROUND_TYPES",
    "MARKET_ENTRY_STANCES",
    "QualityEvaluation",
    "RoundOutputAudit",
    "RoundType",
    "RunScopedAgentContext",
    "SimulationStanceLabel",
    "UniversalClaimValidationResult",
    "evaluate_simulation_quality",
    "load_run_scoped_agents",
    "scan_forecast_or_verdict_claims",
    "scan_unlaunched_product_use_claims",
    "validate_market_entry_stance_label",
]
