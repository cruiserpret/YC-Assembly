"""Phase 6 — Simulation engine package.

Public surface:

    from assembly.pipeline.simulation import (
        run_simulation,            # top-level orchestrator
        BuyerStateSnapshot,        # inter-round state carrier
        RoundResult, RoundContext, # round-level structures
    )

The mandatory `call_llm_for_simulation` helper lives in
`assembly.pipeline.simulation.call_llm`. Direct
`provider.chat` / `provider.structured_output` calls inside this
package are blocked by the static drift tripwire test.
"""
from assembly.pipeline.simulation.call_llm import call_llm_for_simulation
from assembly.pipeline.simulation.engine import run_simulation
from assembly.pipeline.simulation.peer_sampling import PeerPair, sample_peer_pairs
from assembly.pipeline.simulation.persistence import write_round_results
from assembly.pipeline.simulation.state import (
    ROUND_NUMBERS_TO_TYPE,
    BuyerStateSnapshot,
    RoundContext,
    RoundResult,
)

__all__ = [
    "BuyerStateSnapshot",
    "PeerPair",
    "ROUND_NUMBERS_TO_TYPE",
    "RoundContext",
    "RoundResult",
    "call_llm_for_simulation",
    "run_simulation",
    "sample_peer_pairs",
    "write_round_results",
]
