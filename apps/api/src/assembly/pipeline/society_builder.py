"""Phase 5 — Society Builder.

Inputs:
  - simulation_id
  - SimulationBriefIn
  - ProductIntelligenceObject (Phase 4 output)
  - EvidenceBuildResult (Phase 4 output) — provides the canonical evidence
    ledger of UUIDs the LLM may anchor to
  - desired_size (society size, default 16)

Output:
  - SocietyBuildResult — agents (with assigned UUIDs) + influence edges +
    canonical segments + repair-attempt count + warnings.

Constraints enforced:
  - C1-style source-bound: every non-assumption AgentField MUST have at least
    one evidence_anchor that exists in the simulation's evidence ledger.
  - C2-style provenance: BasisKind tag on every field. assumption requires
    rationale. (Both already enforced by Pydantic.)
  - Drift 1/3/5/6 (verdicts/precision/personas/objective sentiment): the
    language validator runs on every AgentField.value AND on the agent
    summary, blocking forced verdicts, fake metrics, absolute claims, and
    objective sentiment.
  - Drift 5 (no generic personas): a regex check on `summary` rejects shapes
    like `"Sarah, 24, likes skincare."`.

PHASE-6-GATE (O1): the LLM call below runs `provider.chat(...)` directly.
The Phase 6 worker MUST wrap this in `with_cost_guard`. See
docs/PHASE_GATES.md.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assembly.config import get_settings
from assembly.llm.errors import LLMRepairExhausted
from assembly.llm.guarded_chat import cost_guarded_chat
from assembly.llm.provider import (
    LLMMessage,
    LLMProvider,
    wrap_user_content_as_data,
)
from assembly.models.agent import Agent as AgentORM
from assembly.models.agent import AgentEdge as AgentEdgeORM
from assembly.pipeline.aggregation.validator import validate_text
from assembly.pipeline.evidence_builder import (
    EvidenceBuildResult,
    PendingEvidenceItem,
)
from assembly.schemas.brief import SimulationBriefIn
from assembly.schemas.product_intelligence import ProductIntelligenceObject
from assembly.schemas.society import (
    AgentTraits,
    BasisKind,
    CategoricalTrait,
    GeneratedAgent,
    InfluenceEdge,
    LLMAgentDraft,
    LLMSocietyDraft,
    SocietyBuildResult,
    SocietyValidationError,
)

logger = logging.getLogger(__name__)


_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "society_builder.md"
)


def load_society_builder_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


# Generic persona shape: "Sarah, 24, likes skincare." — capitalized first
# name, comma, age, comma. Blocks demographic-only personas.
_GENERIC_PERSONA = re.compile(
    r"^[A-Z][a-z]+,\s*\d+(?:\s*(?:years?\s+old|y/?o))?\s*,\s+(?:likes?|enjoys?)\b",
    re.IGNORECASE,
)

_MIN_SUMMARY_WORDS = 12
_MIN_SOCIETY_SIZE = 6
_MAX_SOCIETY_SIZE = 30

# Rule IDs the language linter SKIPS when validating evidence-anchored agent
# fields and the agent summary. Rationale:
#   - `num.dollar_forecast` and `num.metric_acronym` fire on legitimate
#     descriptive content like "$40k MRR" or "the buyer demands ROI proof"
#     — those are buyer-state descriptions of real conditions, not Assembly
#     forecasts. Skip on evidence-anchored content.
#   - Forecast SHAPES like `num.percent_forecast` ("3.2%") and
#     `num.convert_at` ("convert at X") still fire — those are Assembly-style
#     forecasts that have no place in buyer state.
# Verdict rules and objective-sentiment rules are NEVER skipped: those are
# language-style issues independent of numbers.
_BUYER_STATE_SKIP_RULES: frozenset[str] = frozenset(
    {"num.dollar_forecast", "num.metric_acronym"}
)


def _language_violations_for_buyer_state(
    text: str, *, field_path: str
) -> list:
    """Buyer-state-friendly language linting. Used for both the agent
    summary and evidence-anchored AgentField values."""
    return validate_text(
        text, field_path=field_path, skip_rules=_BUYER_STATE_SKIP_RULES
    )


@dataclass(frozen=True)
class _ValidationContext:
    """Provides the set of valid evidence UUIDs the LLM may anchor to."""

    evidence_ids: frozenset[UUID]


def _collect_evidence_ids(evidence: EvidenceBuildResult) -> frozenset[UUID]:
    return frozenset(item.id for item in evidence.items)


def _validate_agent(
    agent: LLMAgentDraft,
    *,
    index: int,
    ctx: _ValidationContext,
) -> list[SocietyValidationError]:
    """Run all post-Pydantic checks on a single agent draft."""
    errors: list[SocietyValidationError] = []

    # 1. Generic-persona check on summary.
    if _GENERIC_PERSONA.match(agent.summary.strip()):
        errors.append(
            SocietyValidationError(
                agent_index=index,
                field_path="summary",
                rule="society.generic_persona",
                message=(
                    f"summary {agent.summary!r} looks like a generic demographic "
                    "persona (Name, Age, likes X). Replace with a buyer-state "
                    "sentence covering role, current alternatives, fear/desire, "
                    "and switching trigger."
                ),
            )
        )

    # 2. Substance check on summary (word count).
    if len(agent.summary.split()) < _MIN_SUMMARY_WORDS:
        errors.append(
            SocietyValidationError(
                agent_index=index,
                field_path="summary",
                rule="society.summary_too_short",
                message=(
                    f"summary has {len(agent.summary.split())} words; "
                    f"need at least {_MIN_SUMMARY_WORDS}."
                ),
            )
        )

    # 3. Language linter on summary (buyer-state-friendly: descriptive
    #    dollar amounts and metric acronyms allowed; forecast shapes and
    #    verdicts still blocked).
    summary_violations = _language_violations_for_buyer_state(
        agent.summary, field_path="summary"
    )
    for v in summary_violations:
        errors.append(
            SocietyValidationError(
                agent_index=index,
                field_path="summary",
                rule=f"language.{v.rule_id}",
                message=(
                    f"summary contains forbidden language ({v.rule_id}): "
                    f"{v.matched_phrase!r}. {v.suggestion}"
                ),
            )
        )

    # 4. Per-field checks: language linter + evidence anchor existence.
    field_pairs: list[tuple[str, "_AnyField"]] = [
        ("current_alternatives", agent.current_alternatives),
        ("budget_level", agent.budget_level),
        ("trust_threshold", agent.trust_threshold),
        ("switching_trigger", agent.switching_trigger),
        ("fear", agent.fear),
        ("desire", agent.desire),
        ("price_sensitivity", agent.price_sensitivity),
        ("objection_pattern", agent.objection_pattern),
        ("emotional_state", agent.emotional_state),
    ]

    for field_name, field in field_pairs:
        # Language linter on value. Evidence-anchored fields get the
        # buyer-state-friendly profile (descriptive numbers allowed);
        # assumption fields get the strict profile.
        if field.basis == BasisKind.ASSUMPTION:
            language_violations = validate_text(field.value, field_path=field_name)
        else:
            language_violations = _language_violations_for_buyer_state(
                field.value, field_path=field_name
            )
        for v in language_violations:
            errors.append(
                SocietyValidationError(
                    agent_index=index,
                    field_path=field_name,
                    rule=f"language.{v.rule_id}",
                    message=(
                        f"{field_name}.value contains forbidden language "
                        f"({v.rule_id}): {v.matched_phrase!r}. {v.suggestion}"
                    ),
                )
            )

        # Evidence anchor existence (non-assumption only).
        if field.basis != BasisKind.ASSUMPTION:
            for anchor in field.evidence_anchors:
                if anchor not in ctx.evidence_ids:
                    errors.append(
                        SocietyValidationError(
                            agent_index=index,
                            field_path=f"{field_name}.evidence_anchors",
                            rule="society.unknown_evidence_anchor",
                            message=(
                                f"evidence_anchor {anchor} referenced by "
                                f"{field_name} does not exist in the "
                                "simulation's evidence ledger. Use only IDs "
                                "from the supplied ledger."
                            ),
                        )
                    )

        # Missing-evidence link existence (assumption only).
        if (
            field.basis == BasisKind.ASSUMPTION
            and field.missing_evidence_link is not None
            and field.missing_evidence_link not in ctx.evidence_ids
        ):
            errors.append(
                SocietyValidationError(
                    agent_index=index,
                    field_path=f"{field_name}.missing_evidence_link",
                    rule="society.unknown_missing_evidence_link",
                    message=(
                        f"missing_evidence_link {field.missing_evidence_link} "
                        "does not exist in the evidence ledger."
                    ),
                )
            )

    # 5. Phase 5.5 — six-layer trait validation.
    errors.extend(_validate_traits(agent.traits, agent_index=index, ctx=ctx))

    return errors


def _validate_traits(
    traits: AgentTraits,
    *,
    agent_index: int,
    ctx: _ValidationContext,
) -> list[SocietyValidationError]:
    """Validate the AgentTraits six-layer object: language linter on every
    free-text value, evidence-anchor existence check on every basis other
    than assumption, and basic sanity on missing_evidence_link.

    Categorical traits are not language-linted on their `level` (it's
    `low|moderate|high` — a finite alphabet) but the `rationale` IS linted.
    """
    errors: list[SocietyValidationError] = []

    # Free-text AgentField walks
    for field_path, field in traits.all_agent_field_paths():
        # Language linter — buyer-state-friendly for evidence-anchored,
        # strict for assumption.
        if field.basis == BasisKind.ASSUMPTION:
            language_violations = validate_text(field.value, field_path=field_path)
        else:
            language_violations = _language_violations_for_buyer_state(
                field.value, field_path=field_path
            )
        for v in language_violations:
            errors.append(
                SocietyValidationError(
                    agent_index=agent_index,
                    field_path=f"traits.{field_path}",
                    rule=f"language.{v.rule_id}",
                    message=(
                        f"traits.{field_path}.value contains forbidden language "
                        f"({v.rule_id}): {v.matched_phrase!r}. {v.suggestion}"
                    ),
                )
            )

        # Evidence anchor existence (non-assumption only).
        if field.basis != BasisKind.ASSUMPTION:
            for anchor in field.evidence_anchors:
                if anchor not in ctx.evidence_ids:
                    errors.append(
                        SocietyValidationError(
                            agent_index=agent_index,
                            field_path=f"traits.{field_path}.evidence_anchors",
                            rule="society.unknown_evidence_anchor",
                            message=(
                                f"evidence_anchor {anchor} on traits.{field_path} "
                                "does not exist in the simulation's evidence ledger."
                            ),
                        )
                    )

        # missing_evidence_link existence (assumption only).
        if (
            field.basis == BasisKind.ASSUMPTION
            and field.missing_evidence_link is not None
            and field.missing_evidence_link not in ctx.evidence_ids
        ):
            errors.append(
                SocietyValidationError(
                    agent_index=agent_index,
                    field_path=f"traits.{field_path}.missing_evidence_link",
                    rule="society.unknown_missing_evidence_link",
                    message=(
                        f"missing_evidence_link {field.missing_evidence_link} on "
                        f"traits.{field_path} does not exist in the evidence ledger."
                    ),
                )
            )

    # CategoricalTrait walks — lint the rationale, check anchors.
    for field_path, ct in traits.all_categorical_fields():
        # The categorical `level` is bounded (low/moderate/high) — no
        # linting needed. The `rationale` is descriptive commentary about the
        # buyer (e.g., "open to switching only with clear ROI proof"). Use
        # the buyer-state-friendly profile uniformly regardless of basis:
        # buyer vocabulary (MRR/ROI/$X) is allowed, but forced verdicts,
        # objective sentiment, and forecast shapes (3.2%, "convert at X")
        # still fire.
        language_violations = _language_violations_for_buyer_state(
            ct.rationale, field_path=field_path
        )
        for v in language_violations:
            errors.append(
                SocietyValidationError(
                    agent_index=agent_index,
                    field_path=f"traits.{field_path}.rationale",
                    rule=f"language.{v.rule_id}",
                    message=(
                        f"traits.{field_path}.rationale contains forbidden language "
                        f"({v.rule_id}): {v.matched_phrase!r}. {v.suggestion}"
                    ),
                )
            )

        if ct.basis != BasisKind.ASSUMPTION:
            for anchor in ct.evidence_anchors:
                if anchor not in ctx.evidence_ids:
                    errors.append(
                        SocietyValidationError(
                            agent_index=agent_index,
                            field_path=f"traits.{field_path}.evidence_anchors",
                            rule="society.unknown_evidence_anchor",
                            message=(
                                f"evidence_anchor {anchor} on traits.{field_path} "
                                "does not exist in the evidence ledger."
                            ),
                        )
                    )

        if (
            ct.basis == BasisKind.ASSUMPTION
            and ct.missing_evidence_link is not None
            and ct.missing_evidence_link not in ctx.evidence_ids
        ):
            errors.append(
                SocietyValidationError(
                    agent_index=agent_index,
                    field_path=f"traits.{field_path}.missing_evidence_link",
                    rule="society.unknown_missing_evidence_link",
                    message=(
                        f"missing_evidence_link {ct.missing_evidence_link} on "
                        f"traits.{field_path} does not exist in the evidence ledger."
                    ),
                )
            )

    return errors


# small structural type so validators don't need to import AgentField directly
class _AnyField:  # pragma: no cover  (just for typing)
    value: str
    basis: BasisKind
    evidence_anchors: list[UUID]
    missing_evidence_link: UUID | None


def validate_society(
    draft: LLMSocietyDraft,
    *,
    evidence: EvidenceBuildResult,
    desired_size: int,
) -> list[SocietyValidationError]:
    """Post-Pydantic substantive validation. Returns 0+ errors that the
    repair loop feeds back to the LLM verbatim."""
    errors: list[SocietyValidationError] = []
    ctx = _ValidationContext(evidence_ids=_collect_evidence_ids(evidence))

    # Society-level checks
    n = len(draft.agents)
    if n < _MIN_SOCIETY_SIZE:
        errors.append(
            SocietyValidationError(
                field_path="agents",
                rule="society.too_small",
                message=(
                    f"society has {n} agents; minimum is {_MIN_SOCIETY_SIZE}. "
                    "Add more diverse buyer-state agents."
                ),
            )
        )
    if n > _MAX_SOCIETY_SIZE:
        errors.append(
            SocietyValidationError(
                field_path="agents",
                rule="society.too_large",
                message=(
                    f"society has {n} agents; maximum is {_MAX_SOCIETY_SIZE}. "
                    "Reduce to the most distinct buyer-state perspectives."
                ),
            )
        )

    # Per-agent checks
    for i, agent in enumerate(draft.agents):
        errors.extend(_validate_agent(agent, index=i, ctx=ctx))

    # Edge sanity
    for j, edge in enumerate(draft.edges):
        if edge.source_index >= n or edge.target_index >= n:
            errors.append(
                SocietyValidationError(
                    field_path=f"edges[{j}]",
                    rule="society.edge_index_out_of_range",
                    message=(
                        f"edge ({edge.source_index} -> {edge.target_index}) "
                        f"references an index >= number of agents ({n})."
                    ),
                )
            )

    return errors


# ---------------------------------------------------------------------------
# Default fallback influence graph
# ---------------------------------------------------------------------------


def _default_influence_edges(
    agents: list[GeneratedAgent],
    *,
    same_cluster_strength: float = 0.6,
    cross_cluster_strength: float = 0.2,
    cross_cluster_density: float = 0.25,
) -> list[InfluenceEdge]:
    """Generate a sparse default influence graph when the LLM doesn't.

    Within a cluster: all pairs get edges at `same_cluster_strength`.
    Across clusters: ~`cross_cluster_density` of pairs at `cross_cluster_strength`.
    """
    edges: list[InfluenceEdge] = []
    by_cluster: dict[str, list[GeneratedAgent]] = {}
    for a in agents:
        key = a.cluster or a.segment
        by_cluster.setdefault(key, []).append(a)

    # Within-cluster
    for cluster, members in by_cluster.items():
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                edges.append(
                    InfluenceEdge(
                        source_agent_id=a.agent_id,
                        target_agent_id=b.agent_id,
                        influence_strength=same_cluster_strength,
                        cluster_label=cluster,
                    )
                )
                edges.append(
                    InfluenceEdge(
                        source_agent_id=b.agent_id,
                        target_agent_id=a.agent_id,
                        influence_strength=same_cluster_strength,
                        cluster_label=cluster,
                    )
                )

    # Cross-cluster (deterministic stride for reproducibility)
    cluster_keys = list(by_cluster.keys())
    if len(cluster_keys) > 1:
        all_pairs: list[tuple[GeneratedAgent, GeneratedAgent]] = []
        for i, ck in enumerate(cluster_keys):
            for cl in cluster_keys[i + 1 :]:
                for a in by_cluster[ck]:
                    for b in by_cluster[cl]:
                        all_pairs.append((a, b))
        keep_n = max(1, int(len(all_pairs) * cross_cluster_density))
        stride = max(1, len(all_pairs) // keep_n) if all_pairs else 1
        for a, b in all_pairs[::stride]:
            edges.append(
                InfluenceEdge(
                    source_agent_id=a.agent_id,
                    target_agent_id=b.agent_id,
                    influence_strength=cross_cluster_strength,
                    cluster_label=None,
                )
            )

    return edges


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def _format_evidence_ledger_block(evidence: EvidenceBuildResult) -> str:
    """Render evidence_items grouped by kind so the LLM can pick anchor IDs."""
    by_kind: dict[str, list[PendingEvidenceItem]] = {
        "direct": [],
        "analogical": [],
        "missing": [],
    }
    for item in evidence.items:
        by_kind.setdefault(item.kind, []).append(item)

    sections = []
    for kind in ("direct", "analogical", "missing"):
        items = by_kind.get(kind, [])
        if not items:
            sections.append(f"### {kind} evidence\n(none)")
            continue
        lines = [f"### {kind} evidence"]
        for item in items:
            input_field = item.metadata.get("input_field")
            node_class = item.metadata.get("node_class")  # Phase 6.75
            tag = f"source_type={item.source_type}"
            if node_class:
                tag += f" node_class={node_class}"
            if input_field:
                tag += f" input_field={input_field}"
            if item.source_url:
                tag += f" url={item.source_url}"
            preview = item.content[:160].replace("\n", " ")
            if len(item.content) > 160:
                preview += "…"
            lines.append(f"- id: {item.id}  ({tag})\n    {preview}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _format_pio_block(pio: ProductIntelligenceObject) -> str:
    """Compact PIO summary the LLM can reference. Strips provenance metadata
    for brevity (LLM only needs the *values* here)."""
    blob = {
        "product_type": pio.product_type.value,
        "product_name": pio.product_name.value,
        "description": pio.description_normalized.value,
        "price_summary": pio.price_summary.value,
        "target_society_summary": pio.target_society_summary.value,
        "buyer_roles": [r.value for r in pio.buyer_roles],
        "current_alternatives": [r.value for r in pio.current_alternatives],
        "claims": [c.text.value for c in pio.claims],
        "trust_risks": [r.value for r in pio.trust_risks],
        "objections": [o.value for o in pio.objections],
        "switching_triggers": [t.value for t in pio.switching_triggers],
        "novelty_type": pio.novelty_type.value if pio.novelty_type else None,
    }
    return json.dumps(blob, indent=2)


def _build_user_message(
    *,
    brief: SimulationBriefIn,
    pio: ProductIntelligenceObject,
    evidence: EvidenceBuildResult,
    desired_size: int,
) -> str:
    return (
        f"Generate a society of **{desired_size}** buyer-state agents for the "
        "brief below. Reference UUIDs from the Evidence Ledger; do not invent "
        "IDs. Return one JSON object that validates against `LLMSocietyDraft`.\n\n"
        + wrap_user_content_as_data(
            "brief.product_type", brief.product_type
        )
        + "\n\n"
        + wrap_user_content_as_data(
            "brief.product_name", brief.product_name
        )
        + "\n\n"
        + wrap_user_content_as_data(
            "brief.description", brief.description
        )
        + "\n\n"
        + wrap_user_content_as_data(
            "brief.target_society",
            brief.target_society.description
            + ("\n" + (brief.target_society.geography or ""))
            + ("\n" + ", ".join(brief.target_society.known_segments)),
        )
        + "\n\n"
        + wrap_user_content_as_data(
            "product_intelligence_object",
            _format_pio_block(pio),
        )
        + "\n\n"
        + "## Evidence Ledger (anchor evidence_anchors to these UUIDs only)\n\n"
        + _format_evidence_ledger_block(evidence)
        + "\n\n"
        + f"desired_size: {desired_size}\n"
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _strip_code_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1 :]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _tolerant_json_loads(text: str):
    """Parse JSON with tolerance for trailing commas before } / ].
    Long LLM JSON outputs (~20KB society dumps) sometimes emit them.
    If strict parsing fails on the first pass, retry after stripping."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        cleaned = _TRAILING_COMMA_RE.sub(r"\1", text)
        return json.loads(cleaned)


def _parse_society_json(text: str) -> LLMSocietyDraft:
    cleaned = _strip_code_fences(text)
    if not cleaned:
        raise ValueError("empty response from society builder")
    data = _tolerant_json_loads(cleaned)
    return LLMSocietyDraft.model_validate(data)


def _format_validation_errors(errors: list[SocietyValidationError]) -> str:
    return json.dumps(
        [
            {
                "agent_index": e.agent_index,
                "field_path": e.field_path,
                "rule": e.rule,
                "message": e.message,
            }
            for e in errors
        ],
        indent=2,
    )


def _format_schema_error(err: Exception) -> str:
    if isinstance(err, ValidationError):
        return err.json(indent=2, include_url=False)
    return str(err)


def _build_repair_message(
    bad_response: str,
    *,
    kind: str,
    detail: str,
) -> str:
    if kind == "schema":
        return (
            "Your previous response did not validate against the "
            "`LLMSocietyDraft` schema. Pydantic errors:\n\n"
            f"```json\n{detail}\n```\n\n"
            "Return ONLY a corrected JSON object that validates exactly. No "
            "commentary, no markdown, no code fences."
        )
    return (
        "Your previous response had society validation errors. Fix every "
        "issue listed below. Either correct the field, change the basis to "
        "`assumption` with a rationale, or replace it with a different "
        "buyer-state perspective. Use only `evidence_anchors` UUIDs that "
        "appear in the Evidence Ledger.\n\n"
        f"```json\n{detail}\n```\n\n"
        "Return ONLY the corrected JSON object."
    )


@dataclass
class _Attempt:
    kind: str
    detail: str


async def build_society(
    *,
    simulation_id: UUID,
    brief: SimulationBriefIn,
    pio: ProductIntelligenceObject,
    evidence: EvidenceBuildResult,
    provider: LLMProvider,
    sessionmaker: async_sessionmaker,
    model: str,
    desired_size: int = 16,
    max_repair_attempts: int = 2,
    stage: str = "society_builder",
) -> SocietyBuildResult:
    """Generate a synthetic society of buyer-state agents.

    Phase 6.6: every attempt — including repairs — flows through
    `cost_guarded_chat`, which acquires the per-simulation row lock,
    enforces the cost cap, and writes a row to `llm_call_log`. This closes
    the PHASE-6-GATE gap where society_builder bypassed the cost guard.
    """
    if desired_size < _MIN_SOCIETY_SIZE or desired_size > _MAX_SOCIETY_SIZE:
        raise ValueError(
            f"desired_size must be in [{_MIN_SOCIETY_SIZE}, {_MAX_SOCIETY_SIZE}], "
            f"got {desired_size}"
        )

    system_prompt = load_society_builder_prompt()
    user_message = _build_user_message(
        brief=brief, pio=pio, evidence=evidence, desired_size=desired_size
    )

    # Phase 12A.10G: society_builder's system prompt is ~2500 tokens
    # of static schema + drift rules + agent-field grammar. The user
    # message contains the per-run brief + PIO + evidence ledger
    # (dynamic). Mark the system message as the cache breakpoint so
    # repair-loop attempts reuse the cached prefix. When the
    # ASSEMBLY_ANTHROPIC_PROMPT_CACHE flag is off, this is a no-op.
    messages: list[LLMMessage] = [
        LLMMessage(
            role="system", content=system_prompt, cache_breakpoint=True,
        ),
        LLMMessage(role="user", content=user_message),
    ]

    last_attempt: _Attempt | None = None
    last_response_text = ""

    for attempt in range(max_repair_attempts + 1):
        # Six-layer trait society for ≥6 agents lands at ~45KB output. The
        # Anthropic provider auto-streams when max_tokens > 8192 (32K is
        # well within Claude opus-4-7's 64K output ceiling).
        # Phase 12A.10F: temperature is settings-driven (default 0.4
        # preserves pre-12A.10F behavior; lower values reduce
        # persona-compression variance for repeatability tests).
        response = await cost_guarded_chat(
            sessionmaker=sessionmaker,
            simulation_id=simulation_id,
            stage=stage,
            messages=messages,
            provider=provider,
            model=model,
            max_tokens=32768,
            temperature=get_settings().society_builder_temperature,
        )
        last_response_text = response.text

        # Stage 1 — schema validation.
        try:
            draft = _parse_society_json(response.text)
        except (ValueError, ValidationError) as e:
            last_attempt = _Attempt(kind="schema", detail=_format_schema_error(e))
            # Save bad response for post-mortem inspection.
            try:
                from pathlib import Path
                Path("/tmp/assembly_debug").mkdir(exist_ok=True)
                debug_path = Path(f"/tmp/assembly_debug/society_attempt_{simulation_id}_{attempt}.txt")
                debug_path.write_text(
                    f"=== error ===\n{e}\n\n=== response ===\n{response.text}",
                    encoding="utf-8",
                )
            except Exception:
                pass
            messages = list(messages) + [
                LLMMessage(role="assistant", content=response.text),
                LLMMessage(
                    role="user",
                    content=_build_repair_message(
                        response.text,
                        kind="schema",
                        detail=last_attempt.detail,
                    ),
                ),
            ]
            logger.info(
                "society_builder.repair attempt=%d kind=schema simulation=%s",
                attempt, simulation_id,
            )
            continue

        # Stage 2 — substantive validation.
        errors = validate_society(
            draft, evidence=evidence, desired_size=desired_size
        )
        if errors:
            last_attempt = _Attempt(
                kind="validation",
                detail=_format_validation_errors(errors),
            )
            messages = list(messages) + [
                LLMMessage(role="assistant", content=response.text),
                LLMMessage(
                    role="user",
                    content=_build_repair_message(
                        response.text,
                        kind="validation",
                        detail=last_attempt.detail,
                    ),
                ),
            ]
            logger.info(
                "society_builder.repair attempt=%d kind=validation "
                "errors=%d simulation=%s",
                attempt, len(errors), simulation_id,
            )
            continue

        # Both stages clean — assign UUIDs and assemble result.
        agents = [
            _draft_to_generated_agent(draft_agent)
            for draft_agent in draft.agents
        ]
        edges: list[InfluenceEdge] = []
        for ed in draft.edges:
            edges.append(
                InfluenceEdge(
                    source_agent_id=agents[ed.source_index].agent_id,
                    target_agent_id=agents[ed.target_index].agent_id,
                    influence_strength=ed.influence_strength,
                    cluster_label=ed.cluster_label,
                )
            )

        warnings: list[str] = []
        if not edges:
            edges = _default_influence_edges(agents)
            warnings.append(
                "LLM produced no edges; default within-cluster + sparse "
                "cross-cluster graph generated."
            )

        segments = sorted({a.segment for a in agents})

        return SocietyBuildResult(
            agents=agents,
            edges=edges,
            segments=segments,
            repair_attempts_used=attempt,
            raw_response_text=last_response_text,
            warnings=warnings,
        )

    raise LLMRepairExhausted(
        f"society_builder failed to produce a valid society after "
        f"{max_repair_attempts} repair attempts. Last error kind: "
        f"{last_attempt.kind if last_attempt else 'unknown'} — "
        f"{(last_attempt.detail if last_attempt else '')[:600]}"
    )


def _draft_to_generated_agent(draft: LLMAgentDraft) -> GeneratedAgent:
    """Assign a fresh UUID and copy fields. Pydantic re-validates on construct."""
    return GeneratedAgent(
        segment=draft.segment,
        role=draft.role,
        cluster=draft.cluster,
        weight=draft.weight,
        summary=draft.summary,
        current_alternatives=draft.current_alternatives,
        budget_level=draft.budget_level,
        trust_threshold=draft.trust_threshold,
        switching_trigger=draft.switching_trigger,
        fear=draft.fear,
        desire=draft.desire,
        price_sensitivity=draft.price_sensitivity,
        objection_pattern=draft.objection_pattern,
        emotional_state=draft.emotional_state,
        influence_score=draft.influence_score,
        susceptibility_to_peer_shift=draft.susceptibility_to_peer_shift,
        assumptions=list(draft.assumptions),
        missing_evidence_awareness=list(draft.missing_evidence_awareness),
        traits=draft.traits,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def persist_society(
    session: AsyncSession,
    *,
    simulation_id: UUID,
    society: SocietyBuildResult,
) -> tuple[list[AgentORM], list[AgentEdgeORM]]:
    """Insert agents and edges into the database. Caller owns the
    transaction; this function only does add() + flush().

    The full GeneratedAgent JSON lands in `agents.buyer_state` (JSONB). The
    rolled-up evidence_anchors set lands in `agents.evidence_anchors`."""
    agent_rows: list[AgentORM] = []
    for ga in society.agents:
        anchors = ga.all_evidence_anchors()
        # buyer_state JSONB ← full GeneratedAgent dump (existing fields + traits-by-ref;
        # Phase 5.5 rule: the canonical agent record is GeneratedAgent.model_dump()).
        # traits JSONB ← the explicit six-layer AgentTraits dump (Phase 5.5).
        row = AgentORM(
            id=ga.agent_id,
            simulation_id=simulation_id,
            segment_label=ga.segment,
            weight=ga.weight,
            buyer_state=json.loads(ga.model_dump_json()),
            traits=json.loads(ga.traits.model_dump_json()),
            evidence_anchors=anchors,
        )
        session.add(row)
        agent_rows.append(row)

    edge_rows: list[AgentEdgeORM] = []
    for edge in society.edges:
        row = AgentEdgeORM(
            simulation_id=simulation_id,
            source_agent_id=edge.source_agent_id,
            target_agent_id=edge.target_agent_id,
            influence_strength=edge.influence_strength,
            cluster_label=edge.cluster_label,
        )
        session.add(row)
        edge_rows.append(row)

    await session.flush()
    return agent_rows, edge_rows


__all__ = [
    "build_society",
    "load_society_builder_prompt",
    "persist_society",
    "validate_society",
]
