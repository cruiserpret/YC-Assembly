"""Round 5 — Proof / clarification exposure."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.llm.provider import LLMProvider
from assembly.pipeline.simulation.rounds._base import run_per_agent_round
from assembly.pipeline.simulation.state import RoundContext, RoundResult


def _proof_extras_for_agent(agent, snapshot, ctx):
    """Build a 'proof_artifacts' block highlighting the evidence excerpts
    most relevant to this agent's accumulated objections. Pure: no LLM call.

    For V0 we just surface up to 5 direct + analogical evidence excerpts so
    the prompt's evidence_ledger block is reinforced with explicit proof
    framing. The agent's `proof_requirement` trait determines how the LLM
    weighs them.
    """
    direct_or_analogical = [
        e for e in ctx.evidence if e.kind in ("direct", "analogical")
    ]
    selected = direct_or_analogical[:5]
    lines = []
    for e in selected:
        excerpt = (e.content or "")[:300].replace("\n", " ")
        if len(e.content or "") > 300:
            excerpt += "…"
        lines.append(f"- [{e.kind}/{e.source_type} id={e.id}]: {excerpt}")
    payload = "\n".join(lines) or "(no proof artifacts available)"
    return [("proof_artifacts_for_agent", payload)]


async def run_round(
    ctx: RoundContext,
    *,
    provider: LLMProvider,
    sessionmaker: async_sessionmaker,
) -> RoundResult:
    return await run_per_agent_round(
        ctx,
        provider=provider,
        sessionmaker=sessionmaker,
        extra_blocks_for=_proof_extras_for_agent,
    )
