"""Phase 8.5E — run-scoped simulation execution for the StrideShield
mini-society.

Loads the 7 personas persisted by Phase 8.5D.2E (by `run_scope_id`),
constructs simulation agents, runs 7 rounds via `cost_guarded_chat`,
applies universal launch-state + forecast/verdict validators every
round, and writes the bounded simulation rows.

Modes:
  --dry-run (default): no DB writes, no LLM calls. Loads personas,
    validates count, emits expected deltas + planned prompts.
  --commit: writes Simulation/Agent/SimulationRound/AgentResponse
    rows; LLM calls go through `cost_guarded_chat`. Hard cap $2.00.

NEVER writes to source_records / persona_records / persona_traits /
persona_evidence_links. Tables are read-only for those.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from dotenv import load_dotenv
from sqlalchemy import func, select

from assembly.db import get_sessionmaker
from assembly.llm.guarded_chat import cost_guarded_chat
from assembly.llm.mock import MockProvider
from assembly.llm.provider import LLMMessage, LLMProvider
from assembly.models.agent import Agent
from assembly.models.persona import (
    PersonaEvidenceLink, PersonaRecord, PersonaTrait, SourceRecord,
)
from assembly.models.round import (
    AgentResponse, DebateTurn, SimulationRound,
)
from assembly.models.simulation import Simulation, SimulationInput
from assembly.sources.run_scoped_persona_simulation import (
    AGENT_ROUND_TYPES, MARKET_ENTRY_STANCES, RoundOutputAudit,
    RunScopedAgentContext, evaluate_simulation_quality,
    load_run_scoped_agents, scan_forecast_or_verdict_claims,
    scan_unlaunched_product_use_claims,
)


PHASE_LABEL = "8.5E"
RUN_SCOPE_ID = "run_8_5d_2e_bf580d12bf5c"
PRODUCT_NAME = "StrideShield"
LAUNCH_STATE = "unlaunched"
EXPECTED_AGENT_COUNT = 7

DEFAULT_HARD_CAP_USD = Decimal("2.00")
DEFAULT_SOFT_CAP_USD = Decimal("0.75")

FOUNDER_BRIEF_DICT: dict[str, Any] = {
    "product_name": PRODUCT_NAME,
    "product_description": (
        "A pocket-sized anti-blister and anti-chafe balm for college "
        "students, runners, hikers, gym-goers, theme-park walkers, "
        "and people whose shoes or sandals rub during long days. It "
        "is sweat-resistant, fragrance-free, non-greasy, and designed "
        "to be applied to heels, toes, thighs, and other friction "
        "spots before walking, running, workouts, or outdoor activity."
    ),
    "price_or_price_structure": "$12.99",
    "launch_geography": "California, United States",
    "target_customers": [
        "college students who walk a lot on campus", "runners",
        "hikers", "gym-goers", "theme-park visitors",
        "people who get shoe rub, sandal cuts, blisters, or thigh chafing",
        "people who dislike greasy lotions or messy powders",
    ],
    "competitors": [
        "Body Glide", "Gold Bond Friction Defense",
        "Megababe Thigh Rescue", "Squirrel's Nut Butter",
        "Trail Toes",
    ],
    "launch_state": LAUNCH_STATE,
}


REQUIRED_CAVEATS: list[str] = [
    "micro-simulation: n=7 (run-scoped, not representative of the "
    "full California market).",
    "This is not a forecast.",
    "This is not a market verdict.",
    "Output is not representative of every California buyer.",
    "Personas are run-scoped generated personas — created from the "
    "founder brief + retrieved evidence for this run only.",
    "Personas are evidence-backed but still synthetic.",
    "Source evidence includes historical Amazon Reviews 2023 data "
    "and live Brave Search web snippets.",
    "There is no direct StrideShield customer evidence because "
    "StrideShield is unlaunched.",
]


def _load_env() -> None:
    here = Path(__file__).resolve()
    for c in (
        here.parent.parent / ".env",
        here.parent.parent.parent.parent / ".env",
    ):
        if c.is_file():
            load_dotenv(c, override=False)


async def _read_table_counts(sessionmaker) -> dict[str, int]:
    async with sessionmaker() as session:
        sr = (await session.execute(
            select(func.count()).select_from(SourceRecord)
        )).scalar_one()
        pr = (await session.execute(
            select(func.count()).select_from(PersonaRecord)
        )).scalar_one()
        pt = (await session.execute(
            select(func.count()).select_from(PersonaTrait)
        )).scalar_one()
        pel = (await session.execute(
            select(func.count()).select_from(PersonaEvidenceLink)
        )).scalar_one()
        sim = (await session.execute(
            select(func.count()).select_from(Simulation)
        )).scalar_one()
        ag = (await session.execute(
            select(func.count()).select_from(Agent)
        )).scalar_one()
        rd = (await session.execute(
            select(func.count()).select_from(SimulationRound)
        )).scalar_one()
        ar = (await session.execute(
            select(func.count()).select_from(AgentResponse)
        )).scalar_one()
        dt = (await session.execute(
            select(func.count()).select_from(DebateTurn)
        )).scalar_one()
    return {
        "source_records": int(sr), "persona_records": int(pr),
        "persona_traits": int(pt), "persona_evidence_links": int(pel),
        "simulations": int(sim), "agents": int(ag),
        "simulation_rounds": int(rd),
        "agent_responses": int(ar), "debate_turns": int(dt),
    }


def _build_persona_block(agent: RunScopedAgentContext) -> str:
    """Compact persona profile for the prompt. Keeps token usage tight
    while preserving full evidence anchoring."""
    traits_blob = "\n".join(
        f"  - {t['field_name']}: {t['value'][:200]}"
        + (f" (rationale: {t['rationale'][:140]})" if t.get("rationale") else "")
        for t in agent.traits[:6]
    )
    excerpts = agent.evidence_excerpts(max_excerpts=3)
    excerpts_blob = "\n".join(
        f"  - {ex[:280]}" for ex in excerpts
    ) or "  (no excerpts loaded)"
    return (
        f"Persona profile (run-scoped, brief-scoped, generated for "
        f"this {PRODUCT_NAME} run only — not a real person):\n"
        f"  display_name: {agent.display_name}\n"
        f"  normalized_primary_role: {agent.normalized_primary_role}\n"
        f"  segment_label: {agent.segment_label}\n"
        f"  evidence_theme: {agent.evidence_theme}\n"
        f"  source_provider_family: {agent.source_provider_family}\n"
        f"  compressed_candidate_id: {agent.compressed_candidate_id}\n"
        f"Persisted traits:\n{traits_blob}\n"
        f"Source evidence excerpts:\n{excerpts_blob}\n"
    )


_UNIVERSAL_FORBIDDEN_RULES = (
    f"- DO NOT claim direct {PRODUCT_NAME} use, purchase, ownership, "
    f"or review. {PRODUCT_NAME} is unlaunched — saying you bought, "
    f"tried, used, or own it is a fabrication.\n"
    "- DO NOT produce buy-percentages, market-share forecasts, "
    "adoption-rate predictions, or 'the market will / won't ...' "
    "claims.\n"
    "- DO NOT issue launch / kill / ship verdicts.\n"
    "- DO NOT speak for 'the market'; speak for THIS persona only.\n"
    "- You MAY reference your evidence-backed competitor / substitute "
    "history (e.g., what you've used before, what you liked or "
    "disliked).\n"
    "- You MAY say you would compare, would consider, would be "
    "skeptical, or would need more proof.\n"
)


_ALLOWED_STANCE_BLOCK = (
    "Allowed final-stance labels (closed set — pick exactly one):\n"
    + "\n".join(f"  - {s}" for s in MARKET_ENTRY_STANCES)
)


_ROUND_QUESTIONS: dict[str, str] = {
    "baseline_context": (
        "BASELINE — describe your CURRENT competitor/substitute "
        f"behavior in this category, BEFORE seeing {PRODUCT_NAME}. "
        "What do you currently use, why, and what frustrates you?"
    ),
    "first_exposure": (
        f"FIRST EXPOSURE — read this {PRODUCT_NAME} brief. Give your "
        "FIRST honest reaction. Don't over-commit; this is your "
        "initial impression. Pick a stance from the allowed set."
    ),
    "objection_formation": (
        "OBJECTIONS — what concrete blockers / concerns / risks would "
        f"keep you from trying {PRODUCT_NAME}? Be specific (no vague "
        "generic complaints)."
    ),
    "competitor_comparison": (
        f"COMPARISON — compare {PRODUCT_NAME} explicitly to your "
        "evidence-backed competitor or substitute. What is better, "
        "what is worse, what is the same? Reference your real-world "
        "experience with the alternative."
    ),
    "proof_exposure": (
        f"PROOF — what specific PROOF, MESSAGING, or PRODUCT DETAIL "
        f"would make you more open to {PRODUCT_NAME}? Be concrete "
        "(test results, ingredient claims, pricing tiers, peer "
        "reviews, brand signal). Do NOT invent proof points; describe "
        "what you would need."
    ),
    "social_influence": (
        "PEER VOICES — here is a summary of objections and reactions "
        "from other personas in this same simulation. Do those "
        "voices change anything for you? Do you update your stance, "
        "or hold? Explain briefly."
    ),
    "final_stance": (
        f"FINAL — given everything (your evidence-backed history, "
        f"the {PRODUCT_NAME} brief, the proof you'd need, the peer "
        "voices), commit to a final stance from the allowed set + "
        "your one-paragraph reasoning + the SINGLE strongest "
        "objection that's still unresolved + the SINGLE strongest "
        "persuasion lever that could shift you."
    ),
}


def _build_round_user_message(
    *,
    round_type: str,
    agent: RunScopedAgentContext,
    peer_summary: str | None = None,
) -> str:
    parts: list[str] = []
    parts.append("=" * 60)
    parts.append(_build_persona_block(agent))
    parts.append("=" * 60)
    parts.append(
        f"Founder brief ({PRODUCT_NAME}, {LAUNCH_STATE}):\n"
        f"  description: {FOUNDER_BRIEF_DICT['product_description']}\n"
        f"  price: {FOUNDER_BRIEF_DICT['price_or_price_structure']}\n"
        f"  launch_geography: "
        f"{FOUNDER_BRIEF_DICT['launch_geography']}\n"
        f"  competitors: "
        f"{', '.join(FOUNDER_BRIEF_DICT['competitors'])}\n"
    )
    parts.append("=" * 60)
    parts.append("Round task: " + _ROUND_QUESTIONS[round_type])
    if peer_summary:
        parts.append("=" * 60)
        parts.append("Peer summary:\n" + peer_summary)
    parts.append("=" * 60)
    parts.append(_ALLOWED_STANCE_BLOCK)
    parts.append("Universal rules (NEVER violate):")
    parts.append(_UNIVERSAL_FORBIDDEN_RULES)
    parts.append(
        "Respond ONLY in valid JSON with this exact shape:\n"
        "{\n"
        '  "stance": "<one of allowed labels OR null for non-stance rounds>",\n'
        '  "reasoning": "<one short paragraph>",\n'
        '  "objections": [{"text": "<concrete>", "category": "<short>"}],\n'
        '  "persuasion_levers": [{"text": "<concrete>", "category": "<short>"}],\n'
        '  "competitor_mentions": ["<brand or substitute>", ...],\n'
        '  "shift_from_previous": null OR {"from": "<prior_stance>", "to": "<new_stance>", "reason": "<short>"}\n'
        "}"
    )
    return "\n".join(parts)


_SYSTEM_PROMPT = (
    "You are an evidence-backed run-scoped persona in a market-entry "
    "simulation for an unlaunched product. Stay in character. Speak "
    "ONLY for this single persona — not for any aggregate or 'the "
    "market'. Output ONLY the requested JSON; no preamble, no markdown, "
    "no extra prose. Avoid forecasts, percentages, or launch verdicts."
)


def _parse_round_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from a model response. Tolerant: strips
    markdown code-fences if the model adds them."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)
        s = s[1] if len(s) > 1 else "{}"
        if s.startswith("json"):
            s = s[4:]
    s = s.strip()
    # Find first { and last }
    open_idx = s.find("{")
    close_idx = s.rfind("}")
    if open_idx < 0 or close_idx <= open_idx:
        return {
            "stance": None, "reasoning": text[:400] or "",
            "objections": [], "persuasion_levers": [],
            "competitor_mentions": [], "shift_from_previous": None,
        }
    try:
        return json.loads(s[open_idx:close_idx + 1])
    except Exception:
        return {
            "stance": None, "reasoning": text[:400] or "",
            "objections": [], "persuasion_levers": [],
            "competitor_mentions": [], "shift_from_previous": None,
        }


def _normalize_response(parsed: dict[str, Any]) -> dict[str, Any]:
    """Coerce the parsed JSON into a strict shape — fills in missing
    keys with safe defaults so the audit never crashes."""
    return {
        "stance": parsed.get("stance"),
        "reasoning": (parsed.get("reasoning") or "")[:1500],
        "objections": [
            {"text": (o.get("text") or "")[:280],
             "category": (o.get("category") or "")[:64]}
            for o in (parsed.get("objections") or [])[:6]
            if isinstance(o, dict)
        ],
        "persuasion_levers": [
            {"text": (l.get("text") or "")[:280],
             "category": (l.get("category") or "")[:64]}
            for l in (parsed.get("persuasion_levers") or [])[:6]
            if isinstance(l, dict)
        ],
        "competitor_mentions": [
            (c or "")[:64]
            for c in (parsed.get("competitor_mentions") or [])[:8]
            if isinstance(c, str) and (c or "").strip()
        ],
        "shift_from_previous": parsed.get("shift_from_previous"),
    }


def _scan_response_for_forbidden(
    *, response_text: str, parsed: dict[str, Any], product_name: str,
) -> list[str]:
    """Return a list of forbidden-claim findings against the
    response text + structured parsed reasoning."""
    findings: list[str] = []
    blob = (response_text or "") + " | " + (parsed.get("reasoning") or "")
    v1 = scan_unlaunched_product_use_claims(
        text=blob, product_name=product_name,
    )
    if not v1.is_valid:
        findings.append(f"launch_state:{v1.rejection_reason}")
    v2 = scan_forecast_or_verdict_claims(text=blob)
    if not v2.is_valid:
        findings.append(f"forecast_or_verdict:{v2.rejection_reason}")
    return findings


def _peer_summary_from_round_outputs(
    rounds: list[RoundOutputAudit],
) -> str:
    """Compact summary of objections + stance distribution from
    the rounds-so-far — fed into round 6 (social_influence)."""
    if not rounds:
        return "(no peer data yet)"
    obj_counter: Counter = Counter()
    stance_counter: Counter = Counter()
    for r in rounds:
        for o in r.objections or []:
            t = ((o.get("text") or "")[:60].strip().lower())
            if t:
                obj_counter[t] += 1
        if r.stance:
            stance_counter[r.stance] += 1
    top_obj = "; ".join(
        f"{txt} (×{cnt})"
        for txt, cnt in obj_counter.most_common(5)
    ) or "(none)"
    stance_dist = ", ".join(
        f"{s}={c}" for s, c in stance_counter.most_common()
    ) or "(no final stances yet)"
    return f"Top objections: {top_obj}\nStance distribution: {stance_dist}"


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=f"Phase {PHASE_LABEL} — run-scoped simulation.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", default=True,
        help="Default. No DB writes, no LLM calls.",
    )
    mode.add_argument(
        "--commit", action="store_true",
        help="Commit simulation rows + run real LLM calls.",
    )
    parser.add_argument(
        "--provider", default="anthropic",
        choices=("anthropic", "mock"),
        help="LLM provider for --commit. Default: anthropic.",
    )
    parser.add_argument(
        "--hard-cap-usd", type=str,
        default=str(DEFAULT_HARD_CAP_USD),
    )
    parser.add_argument(
        "--run-scope-id", default=RUN_SCOPE_ID,
    )
    args = parser.parse_args()
    do_commit = bool(args.commit)
    hard_cap_usd = Decimal(args.hard_cap_usd)
    _load_env()

    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    audit_root.mkdir(exist_ok=True)
    out_path = audit_root / "strideshield_simulation_8_5e.json"
    qual_path = audit_root / "strideshield_simulation_quality_8_5e.json"

    sm = get_sessionmaker()
    pre = await _read_table_counts(sm)
    print(f"DB pre-counts: {pre}")

    # 1. Load run-scoped agents
    async with sm() as session:
        agents = await load_run_scoped_agents(
            session=session, run_scope_id=args.run_scope_id,
        )

    if len(agents) != EXPECTED_AGENT_COUNT:
        msg = (
            f"REFUSED: loaded {len(agents)} personas for "
            f"run_scope_id={args.run_scope_id!r}; "
            f"expected {EXPECTED_AGENT_COUNT}."
        )
        print(msg)
        out_path.write_text(json.dumps({
            "phase": "8_5e_strideshield_run_scoped_simulation",
            "completed_at": datetime.now(UTC).isoformat(),
            "run_scope_id": args.run_scope_id,
            "input_persona_count": len(agents),
            "rollback_reason": msg,
            "ready_for_founder_report_phase": False,
        }, indent=2), encoding="utf-8")
        return 2

    print(
        f"Loaded {len(agents)} personas | total traits: "
        f"{sum(len(a.traits) for a in agents)} | total links: "
        f"{sum(len(a.evidence_links) for a in agents)} | "
        f"total source records: "
        f"{sum(len(a.source_records) for a in agents)}"
    )

    if not do_commit:
        # Dry-run preflight: emit expected delta + planned prompts
        # snapshot, no LLM, no DB writes.
        out_path.write_text(json.dumps({
            "phase": "8_5e_strideshield_run_scoped_simulation",
            "mode": "dry_run",
            "completed_at": datetime.now(UTC).isoformat(),
            "run_scope_id": args.run_scope_id,
            "founder_brief": FOUNDER_BRIEF_DICT,
            "input_persona_count": len(agents),
            "input_persona_ids": [str(a.persona_id) for a in agents],
            "input_persona_summary": [
                {
                    "persona_id": str(a.persona_id),
                    "display_name": a.display_name,
                    "normalized_primary_role": a.normalized_primary_role,
                    "compressed_candidate_id": a.compressed_candidate_id,
                    "trait_count": len(a.traits),
                    "evidence_link_count": len(a.evidence_links),
                    "source_record_count": len(a.source_records),
                }
                for a in agents
            ],
            "rounds_planned": list(AGENT_ROUND_TYPES),
            "expected_db_deltas_on_commit": {
                "simulations": 1,
                "simulation_inputs": 1,
                "agents": EXPECTED_AGENT_COUNT,
                "simulation_rounds": len(AGENT_ROUND_TYPES),
                "agent_responses": (
                    EXPECTED_AGENT_COUNT * len(AGENT_ROUND_TYPES)
                ),
                "source_records": 0,
                "persona_records": 0,
                "persona_traits": 0,
                "persona_evidence_links": 0,
            },
            "ready_for_founder_report_phase": False,
            "recommendation": (
                "Dry-run preflight passed. Run --commit to execute "
                "the bounded simulation against the loaded personas."
            ),
        }, indent=2, default=str), encoding="utf-8")
        post = await _read_table_counts(sm)
        print(
            f"\nDry-run complete. Pre/post unchanged: "
            f"{pre == post}"
        )
        print(f"→ audit: {out_path}")
        return 0

    # 2. Commit path: create Simulation + Agent rows in one transaction,
    # then run rounds with LLM calls (each cost-guarded).
    if args.provider == "mock":
        mock_provider = MockProvider()
        mock_provider.add_default(_canned_mock_json())
        provider: LLMProvider = mock_provider
    else:
        from assembly.llm.anthropic import AnthropicProvider
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ERROR: ANTHROPIC_API_KEY missing.")
            return 2
        provider = AnthropicProvider()

    sim_id: UUID | None = None
    persona_to_agent_id: dict[UUID, UUID] = {}
    rollback_reason: str | None = None
    rounds_audit: list[RoundOutputAudit] = []
    round_id_by_type: dict[str, UUID] = {}
    cost_summary = {"calls": 0, "input_tokens": 0, "output_tokens": 0}

    try:
        async with sm() as session:
            async with session.begin():
                sim_id = uuid.uuid4()
                sim = Simulation(
                    id=sim_id,
                    user_id=f"phase_{PHASE_LABEL}_strideshield",
                    status="simulating",
                    started_at=datetime.now(UTC),
                    progress={
                        "phase": PHASE_LABEL,
                        "run_scope_id": args.run_scope_id,
                        "expected_rounds": len(AGENT_ROUND_TYPES),
                        "expected_agents": EXPECTED_AGENT_COUNT,
                    },
                )
                session.add(sim)
                # SimulationInput row
                price_value = float(
                    str(FOUNDER_BRIEF_DICT["price_or_price_structure"])
                    .replace("$", "").strip() or 0
                )
                sim_input = SimulationInput(
                    id=uuid.uuid4(),
                    simulation_id=sim_id,
                    product_type="anti-blister anti-chafe balm",
                    product_name=PRODUCT_NAME,
                    description=FOUNDER_BRIEF_DICT["product_description"],
                    price_structure={
                        "amount_usd": price_value,
                        "structure": "one_time",
                    },
                    target_society={
                        "geography_broad": (
                            FOUNDER_BRIEF_DICT["launch_geography"]
                        ),
                        "target_customers": list(
                            FOUNDER_BRIEF_DICT["target_customers"],
                        ),
                    },
                    competitors=[
                        {"name": c} for c in FOUNDER_BRIEF_DICT["competitors"]
                    ],
                    raw_brief=dict(FOUNDER_BRIEF_DICT),
                )
                session.add(sim_input)

                # Agent rows — one per persona
                for a in agents:
                    agent_id = uuid.uuid4()
                    persona_to_agent_id[a.persona_id] = agent_id
                    agent = Agent(
                        id=agent_id,
                        simulation_id=sim_id,
                        segment_label=(
                            a.segment_label
                            or a.normalized_primary_role
                        )[:128],
                        weight=1.0,
                        buyer_state={
                            "current_alternatives": [
                                t["value"] for t in a.traits
                                if t["field_name"] == "current_alternatives"
                            ],
                            "current_behavior": "",
                            "objection_pattern": ", ".join(
                                t["value"][:80] for t in a.traits
                                if t["field_name"] == "objection_patterns"
                            ),
                            "price_sensitivity": ", ".join(
                                t["value"][:80] for t in a.traits
                                if t["field_name"] == "price_sensitivity"
                            ) or "moderate",
                        },
                        traits={
                            "persisted_persona_id": str(a.persona_id),
                            "compressed_candidate_id": (
                                a.compressed_candidate_id
                            ),
                            "normalized_primary_role": (
                                a.normalized_primary_role
                            ),
                            "evidence_theme": a.evidence_theme,
                            "source_provider_family": (
                                a.source_provider_family
                            ),
                            "run_scope_id": a.run_scope_id,
                            "display_name": a.display_name,
                            "trait_field_names": [
                                t["field_name"] for t in a.traits
                            ],
                        },
                        evidence_anchors=[],
                    )
                    session.add(agent)
                await session.flush()
                print(f"Inserted simulation={sim_id} + 7 agents.")

        # 3. Run rounds OUTSIDE the persona-creation transaction.
        # Each LLM call goes through cost_guarded_chat (which uses
        # its own row-locked transaction per call).
        peer_summary: str = ""
        for round_idx, round_type in enumerate(AGENT_ROUND_TYPES, start=1):
            round_id = uuid.uuid4()
            round_id_by_type[round_type] = round_id
            async with sm() as session:
                async with session.begin():
                    rd = SimulationRound(
                        id=round_id,
                        simulation_id=sim_id,
                        round_number=round_idx,
                        round_type=round_type,
                        started_at=datetime.now(UTC),
                        summary={
                            "phase": PHASE_LABEL,
                            "round_type": round_type,
                            "round_number": round_idx,
                        },
                    )
                    session.add(rd)
            print(
                f"\nRound {round_idx} ({round_type}) — "
                f"running {len(agents)} agents..."
            )
            for a in agents:
                user_msg = _build_round_user_message(
                    round_type=round_type, agent=a,
                    peer_summary=(
                        peer_summary if round_type == "social_influence"
                        else None
                    ),
                )
                messages = [
                    LLMMessage(role="system", content=_SYSTEM_PROMPT),
                    LLMMessage(role="user", content=user_msg),
                ]
                try:
                    response = await cost_guarded_chat(
                        sessionmaker=sm,
                        simulation_id=sim_id,
                        stage=f"round_{round_type}",
                        messages=messages,
                        provider=provider,
                        hard_cap_usd=hard_cap_usd,
                        max_tokens=900,
                        temperature=0.4,
                        estimated_prompt_tokens=2500,
                        estimated_completion_tokens=600,
                    )
                except Exception as e:
                    rollback_reason = (
                        f"LLM call failed: {type(e).__name__}: {e}"
                    )
                    print(f"ERROR — {rollback_reason}")
                    raise
                cost_summary["calls"] += 1
                cost_summary["input_tokens"] += int(
                    response.prompt_tokens or 0,
                )
                cost_summary["output_tokens"] += int(
                    response.completion_tokens or 0,
                )

                parsed = _normalize_response(_parse_round_json(response.text))
                forbidden = _scan_response_for_forbidden(
                    response_text=response.text,
                    parsed=parsed, product_name=PRODUCT_NAME,
                )
                # Persist the AgentResponse row
                stance_for_db = parsed.get("stance") or "needs_more_information"
                if stance_for_db not in MARKET_ENTRY_STANCES:
                    stance_for_db = "needs_more_information"
                async with sm() as session:
                    async with session.begin():
                        ar_row = AgentResponse(
                            id=uuid.uuid4(),
                            round_id=round_id,
                            agent_id=persona_to_agent_id[a.persona_id],
                            stance=stance_for_db,
                            reasoning=(parsed.get("reasoning") or "")[:4000],
                            objections=parsed.get("objections") or [],
                            persuasion_drivers=(
                                parsed.get("persuasion_levers") or []
                            ),
                            shift_from_previous=parsed.get(
                                "shift_from_previous",
                            ),
                            state_after={
                                "stance": stance_for_db,
                                "round_type": round_type,
                                "competitor_mentions": (
                                    parsed.get("competitor_mentions") or []
                                ),
                                "forbidden_claim_audit": forbidden,
                            },
                            raw_output={
                                "raw_text": (response.text or "")[:6000],
                                "model": response.model,
                                "provider": response.provider,
                                "parsed": parsed,
                                "forbidden_claim_audit": forbidden,
                            },
                        )
                        session.add(ar_row)
                rounds_audit.append(RoundOutputAudit(
                    agent_persona_id=str(a.persona_id),
                    display_name=a.display_name,
                    compressed_candidate_id=a.compressed_candidate_id,
                    normalized_primary_role=a.normalized_primary_role,
                    round_type=round_type,  # type: ignore[arg-type]
                    round_number=round_idx,
                    stance=(
                        stance_for_db  # type: ignore[arg-type]
                        if stance_for_db in MARKET_ENTRY_STANCES
                        else None
                    ),
                    reasoning=(parsed.get("reasoning") or "")[:1500],
                    objections=parsed.get("objections") or [],
                    persuasion_levers=(
                        parsed.get("persuasion_levers") or []
                    ),
                    competitor_mentions=(
                        parsed.get("competitor_mentions") or []
                    ),
                    shift_from_previous=parsed.get("shift_from_previous"),
                    forbidden_claim_audit=forbidden,
                    raw_text=(response.text or "")[:6000],
                ))
                print(
                    f"  {a.display_name:14s} stance="
                    f"{stance_for_db:24s} obj={len(parsed.get('objections') or [])}"
                    f" forbid={len(forbidden)}"
                )
            # Update the round summary
            async with sm() as session:
                async with session.begin():
                    rd_row = (await session.execute(
                        select(SimulationRound).where(
                            SimulationRound.id == round_id,
                        )
                    )).scalar_one()
                    these_rounds = [
                        r for r in rounds_audit
                        if r.round_type == round_type
                    ]
                    rd_row.completed_at = datetime.now(UTC)
                    rd_row.summary = {
                        "phase": PHASE_LABEL,
                        "round_type": round_type,
                        "round_number": round_idx,
                        "stance_distribution": dict(Counter(
                            r.stance for r in these_rounds if r.stance
                        )),
                        "agent_count": len(these_rounds),
                        "any_forbidden_claims": any(
                            r.forbidden_claim_audit for r in these_rounds
                        ),
                    }
            # Refresh peer summary for next round
            peer_summary = _peer_summary_from_round_outputs(rounds_audit)

        # 4. Mark simulation complete
        async with sm() as session:
            async with session.begin():
                sim_row = (await session.execute(
                    select(Simulation).where(Simulation.id == sim_id)
                )).scalar_one()
                sim_row.status = "simulation_completed"
                sim_row.completed_at = datetime.now(UTC)
                sim_row.progress = {
                    **sim_row.progress,
                    "rounds_completed": len(AGENT_ROUND_TYPES),
                }
    except Exception as e:
        if rollback_reason is None:
            rollback_reason = (
                f"unexpected exception: {type(e).__name__}: {e}"
            )
        print(f"ROLLBACK: {rollback_reason}")
        # Mark simulation row as failed if it exists
        try:
            async with sm() as session:
                async with session.begin():
                    sim_row = (await session.execute(
                        select(Simulation).where(Simulation.id == sim_id)
                    )).scalar_one_or_none()
                    if sim_row is not None:
                        sim_row.status = "failed"
                        sim_row.completed_at = datetime.now(UTC)
                        sim_row.error = {
                            "reason": rollback_reason[:1000],
                        }
        except Exception:
            pass

    post = await _read_table_counts(sm)

    # 5. Forbidden-claim audit summary
    any_fake_use = any(
        any(f.startswith("launch_state:") for f in r.forbidden_claim_audit)
        for r in rounds_audit
    )
    any_forecast = any(
        any(f.startswith("forecast_or_verdict:") for f in r.forbidden_claim_audit)
        for r in rounds_audit
    )
    forbidden_audit_summary = {
        "fake_target_product_use_count": sum(
            1 for r in rounds_audit
            if any(f.startswith("launch_state:") for f in r.forbidden_claim_audit)
        ),
        "forecast_or_verdict_count": sum(
            1 for r in rounds_audit
            if any(f.startswith("forecast_or_verdict:") for f in r.forbidden_claim_audit)
        ),
        "any_fake_target_product_use": any_fake_use,
        "any_forecast_or_verdict": any_forecast,
    }

    # 6. Stance distribution + objections + levers (final round + global)
    final_rounds = [r for r in rounds_audit if r.round_type == "final_stance"]
    final_stance_dist = dict(Counter(
        r.stance for r in final_rounds if r.stance
    ))
    obj_global = Counter()
    lever_global = Counter()
    competitor_mentions = Counter()
    for r in rounds_audit:
        for o in r.objections or []:
            t = (o.get("text") or "")[:80].strip().lower()
            if t:
                obj_global[t] += 1
        for l in r.persuasion_levers or []:
            t = (l.get("text") or "")[:80].strip().lower()
            if t:
                lever_global[t] += 1
        for c in r.competitor_mentions or []:
            competitor_mentions[(c or "")[:60].strip().lower()] += 1

    # 7. Source/persona table integrity check
    integrity_keys = (
        "source_records", "persona_records",
        "persona_traits", "persona_evidence_links",
    )
    persona_tables_unchanged = all(
        post[k] == pre[k] for k in integrity_keys
    )
    sim_deltas = {
        k: post[k] - pre[k] for k in (
            "simulations", "agents", "simulation_rounds",
            "agent_responses", "debate_turns",
        )
    }

    # 8. Quality eval
    qual = evaluate_simulation_quality(
        rounds=rounds_audit, caveats=REQUIRED_CAVEATS,
        product_name=PRODUCT_NAME,
        agents_with_traits_count=sum(1 for a in agents if a.traits),
        total_agents=len(agents),
    )

    summary = {
        "phase": "8_5e_strideshield_run_scoped_simulation",
        "completed_at": datetime.now(UTC).isoformat(),
        "run_scope_id": args.run_scope_id,
        "founder_brief": FOUNDER_BRIEF_DICT,
        "launch_state": LAUNCH_STATE,
        "input_persona_count": len(agents),
        "input_persona_ids": [str(a.persona_id) for a in agents],
        "input_persona_summary": [
            {
                "persona_id": str(a.persona_id),
                "display_name": a.display_name,
                "normalized_primary_role": a.normalized_primary_role,
                "compressed_candidate_id": a.compressed_candidate_id,
                "source_provider_family": a.source_provider_family,
                "evidence_theme": a.evidence_theme,
                "trait_count": len(a.traits),
                "evidence_link_count": len(a.evidence_links),
                "source_record_count": len(a.source_records),
            }
            for a in agents
        ],
        "traits_loaded_count": sum(len(a.traits) for a in agents),
        "evidence_links_loaded_count": sum(
            len(a.evidence_links) for a in agents
        ),
        "source_records_loaded_count": len({
            sr["source_record_id"]
            for a in agents for sr in a.source_records
        }),
        "simulation_id": str(sim_id) if sim_id else None,
        "agents_created_count": len(persona_to_agent_id),
        "rounds_run": [r.round_type for r in rounds_audit if r.round_type] if rounds_audit else [],
        "rounds_completed": (
            len(AGENT_ROUND_TYPES)
            if rollback_reason is None else 0
        ),
        "per_round_outputs": [
            json.loads(r.model_dump_json()) for r in rounds_audit
        ],
        "final_stance_distribution": final_stance_dist,
        "top_objections": [
            {"text": t, "count": c}
            for t, c in obj_global.most_common(10)
        ],
        "top_persuasion_levers": [
            {"text": t, "count": c}
            for t, c in lever_global.most_common(10)
        ],
        "competitor_comparison_summary": [
            {"competitor": k, "mentions": v}
            for k, v in competitor_mentions.most_common(10)
        ],
        "proof_needed_summary": [
            {"text": t, "count": c}
            for t, c in lever_global.most_common(8)
        ],
        "social_influence_summary": _peer_summary_from_round_outputs(
            rounds_audit,
        ),
        "forbidden_claim_audit": forbidden_audit_summary,
        "caveats": REQUIRED_CAVEATS,
        "cost_summary": {
            **cost_summary,
            "hard_cap_usd": str(hard_cap_usd),
            "soft_cap_usd": str(DEFAULT_SOFT_CAP_USD),
            "cost_guard_active": True,
            "model_used": (
                "claude-sonnet-4-6" if args.provider == "anthropic"
                else "mock_provider"
            ),
        },
        "db_pre_counts": pre,
        "db_post_counts": post,
        "db_delta_summary": {
            **sim_deltas,
            "source_records": post["source_records"] - pre["source_records"],
            "persona_records": post["persona_records"] - pre["persona_records"],
            "persona_traits": post["persona_traits"] - pre["persona_traits"],
            "persona_evidence_links": (
                post["persona_evidence_links"] - pre["persona_evidence_links"]
            ),
        },
        "source_persona_tables_unchanged": persona_tables_unchanged,
        "rollback_reason": rollback_reason,
        "ready_for_founder_report_phase": (
            rollback_reason is None
            and persona_tables_unchanged
            and not any_fake_use
            and not any_forecast
            and qual.ready_state in (
                "READY_FOR_FOUNDER_REPORT", "READY_FOR_PROMPT_FIX",
            )
        ),
        "quality_evaluator_result": json.loads(qual.model_dump_json()),
        "recommendation": (
            "PASS — bounded simulation completed; quality "
            f"ready_state={qual.ready_state}."
            if rollback_reason is None
            else f"FAIL — {rollback_reason}"
        ),
    }
    out_path.write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8",
    )
    qual_path.write_text(
        json.dumps({
            "phase": "8_5e_strideshield_run_scoped_simulation_quality",
            "completed_at": datetime.now(UTC).isoformat(),
            "run_scope_id": args.run_scope_id,
            "simulation_id": str(sim_id) if sim_id else None,
            "scores": json.loads(qual.model_dump_json()),
            "input_round_count": len(rounds_audit),
            "input_caveat_count": len(REQUIRED_CAVEATS),
        }, indent=2, default=str),
        encoding="utf-8",
    )

    print("\n" + "=" * 72)
    print(f"Phase {PHASE_LABEL} — StrideShield run-scoped simulation")
    print("=" * 72)
    print(f"mode: {'COMMIT' if do_commit else 'DRY-RUN'}")
    print(f"provider: {args.provider}")
    print(f"simulation_id: {sim_id}")
    print(f"agents created: {len(persona_to_agent_id)}")
    print(f"rounds completed: {summary['rounds_completed']}")
    print(f"LLM calls: {cost_summary['calls']}")
    print(
        f"input_tokens={cost_summary['input_tokens']}, "
        f"output_tokens={cost_summary['output_tokens']}"
    )
    print(f"final stance dist: {final_stance_dist}")
    print(
        f"forbidden claims: fake_use={forbidden_audit_summary['any_fake_target_product_use']}, "
        f"forecast={forbidden_audit_summary['any_forecast_or_verdict']}"
    )
    print(f"persona_tables_unchanged: {persona_tables_unchanged}")
    print(f"db_delta: {summary['db_delta_summary']}")
    print(f"quality.aggregate_score: {qual.aggregate_score}")
    print(f"quality.ready_state: {qual.ready_state}")
    print(f"ready_for_founder_report_phase: {summary['ready_for_founder_report_phase']}")
    print(f"\n→ audit JSON: {out_path}")
    print(f"→ quality JSON: {qual_path}")
    return 0 if rollback_reason is None else 1


def _canned_mock_json() -> str:
    """Deterministic canned JSON for --provider mock. Universal
    shape — works for every round, every persona. Sufficient for
    smoke-testing the persistence + parsing flow without API spend."""
    return json.dumps({
        "stance": "interested_if_proven",
        "reasoning": (
            "Speaking only for this single persona: I currently use "
            "Body Glide for friction. The brief sounds plausible but "
            "I would compare on grease, sweat resistance, and price "
            "before changing my routine. I have not used StrideShield "
            "and cannot judge it directly."
        ),
        "objections": [
            {
                "text": (
                    "Greasiness on heels — Body Glide already wins "
                    "this; needs proof."
                ),
                "category": "texture",
            },
        ],
        "persuasion_levers": [
            {
                "text": "Independent runner reviews + a price below $13.",
                "category": "social_proof",
            },
        ],
        "competitor_mentions": ["Body Glide", "Megababe Thigh Rescue"],
        "shift_from_previous": None,
    })


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
