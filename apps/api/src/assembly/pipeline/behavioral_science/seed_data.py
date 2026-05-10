"""Phase 8.2D — deterministic seed catalog for the behavioral mechanism library.

This module is the single source of truth for the seeded contents of the
seven Phase-8.2D tables. Loading is done by `mechanism_library.seed_all`
which:

  - inserts research_sources (8 themes)
  - inserts behavioral_mechanisms (22+) and binds them to sources via
    mechanism_evidence_links
  - inserts persuasion_strategy_taxonomy (14 strategies)
  - inserts belief_network_rules (research-anchored same-cluster /
    adjacent-cluster pairs; the strongest allowed strength is 'moderate')
  - inserts mechanism_applicability_rules (domain × mechanism hints)

Seed data is keyed by stable string identifiers (`SourceKey`,
`MechanismKey`) so cross-references stay legible. The library resolves
keys to UUIDs at insert time — no UUIDs are baked into this module.

CRITICAL invariants enforced by the validator + DB CHECK constraints:
  - every mechanism has at least one evidence link
  - no belief rule has allowed_inference_strength='strong'
  - mechanism priors NEVER outrank source-bound evidence (initializer
    enforces; no rule in this seed contradicts that)
  - the demographic-only-roleplay anti-pattern has its own mechanism +
    applicability rule that refuses it on `unsupported_demographic_only`
    domains.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Stable string identifiers
# ---------------------------------------------------------------------------


class SourceKey:
    WINNING_ARGUMENTS = "winning_arguments_changemyview"
    PERSUASION_FOR_GOOD = "persuasion_for_good"
    SILICON_SAMPLING = "silicon_sampling_algorithmic_fidelity"
    RANDOM_SILICON_SAMPLING = "random_silicon_sampling"
    GENERATIVE_AGENTS = "generative_agents_park_et_al"
    BELIEF_NETWORK = "belief_network_beyond_demographics"
    INFORMATION_VS_CONFORMITY = "information_vs_social_conformity"
    PUBLIC_OPINION_BIAS = "public_opinion_simulation_bias"


class MechanismKey:
    # Persuasion / argument-style (Winning Arguments)
    ENTRY_ORDER_ADVANTAGE = "entry_order_advantage"
    BACK_AND_FORTH_DYNAMIC = "back_and_forth_dynamic"
    DIMINISHING_RETURNS_OF_ARGUMENTS = "diminishing_returns_of_arguments"
    EVIDENCE_LINKING_DRIVES_CHANGE = "evidence_linking_drives_change"
    ARGUMENT_INTENSITY_PENALTY = "argument_intensity_penalty"
    SUBLINEAR_PERSUADER_COUNT = "sublinear_persuader_count"
    OPINION_MALLEABILITY_HETEROGENEITY = "opinion_malleability_heterogeneity"

    # Persuasion strategy use (Persuasion for Good)
    STRATEGY_PERSONALIZATION = "strategy_personalization"
    LOGICAL_VS_EMOTIONAL_APPEAL_BALANCE = "logical_vs_emotional_appeal_balance"
    INQUIRY_BEFORE_PERSUASION = "inquiry_before_persuasion"

    # Population sampling / silicon sampling
    GROUP_LEVEL_CONDITIONING_OK = "group_level_conditioning_ok"
    INDIVIDUAL_TRUTH_NOT_RECOVERABLE = "individual_truth_not_recoverable"
    REPLICABILITY_VARIES_BY_GROUP_TOPIC = "replicability_varies_by_group_topic"
    LLM_TRAINING_BIASES_LEAK_INTO_SAMPLING = (
        "llm_training_biases_leak_into_sampling"
    )

    # Generative agents (memory / planning loop)
    MEMORY_RECENCY_RELEVANCE_IMPORTANCE = (
        "memory_recency_relevance_importance"
    )
    REFLECTION_FROM_OBSERVATIONS = "reflection_from_observations"
    OBSERVATION_REFLECTION_PLANNING_LOOP = (
        "observation_reflection_planning_loop"
    )

    # Belief network
    DEMOGRAPHIC_ONLY_ROLEPLAY_UNRELIABLE = (
        "demographic_only_roleplay_unreliable"
    )
    EMPIRICAL_BELIEF_NETWORK_IMPROVES_ALIGNMENT = (
        "empirical_belief_network_improves_alignment"
    )
    BOUNDED_SAME_CLUSTER_SPILLOVER = "bounded_same_cluster_spillover"

    # Conformity
    INFORMATIONAL_VS_NORMATIVE_CONFORMITY = (
        "informational_vs_normative_conformity"
    )
    COMPLIANCE_VS_ACCEPTANCE = "compliance_vs_acceptance"

    # Simulation bias
    GEO_CULTURAL_LANGUAGE_ECONOMY_BIAS = (
        "geo_cultural_language_economy_bias"
    )
    WESTERN_ENGLISH_PERFORMANCE_TILT = "western_english_performance_tilt"


# ---------------------------------------------------------------------------
# 1) Research sources
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SourceSeed:
    key: str
    title: str
    authors: str | None
    year: int | None
    source_type: str
    citation: str | None
    notes: str | None


SEED_SOURCES: tuple[_SourceSeed, ...] = (
    _SourceSeed(
        key=SourceKey.WINNING_ARGUMENTS,
        title="Winning Arguments — analysis of opinion change on ChangeMyView",
        authors="Tan et al. (uploaded research theme; specific paper not stored in repo)",
        year=2016,
        source_type="uploaded_paper",
        citation=(
            "Phase 8.2D ingests the user-supplied research theme summarising "
            "ChangeMyView opinion-change findings. The actual paper is not "
            "stored under this repo; mechanisms cite the theme summary."
        ),
        notes=(
            "Used as the source-of-record for: entry-order advantage, "
            "back-and-forth dynamics, diminishing returns of arguments, "
            "evidence linking, argument intensity penalty, sublinear "
            "persuader count, and opinion-malleability heterogeneity."
        ),
    ),
    _SourceSeed(
        key=SourceKey.PERSUASION_FOR_GOOD,
        title="Persuasion for Good — personalized persuasion strategy taxonomy",
        authors="Wang et al. (uploaded research theme)",
        year=2019,
        source_type="uploaded_paper",
        citation=(
            "Theme summarises a personalized-persuasion taxonomy that informs "
            "the 14-strategy catalog seeded into persuasion_strategy_taxonomy."
        ),
        notes=(
            "Source-of-record for: logical_appeal, emotional_appeal, "
            "credibility_appeal, personal_story, self_modeling, "
            "foot_in_the_door, task_product_information, "
            "source_related_inquiry, task_related_inquiry, "
            "personal_related_inquiry."
        ),
    ),
    _SourceSeed(
        key=SourceKey.SILICON_SAMPLING,
        title="Silicon Sampling — algorithmic fidelity for group-level simulation",
        authors="Argyle et al. (uploaded research theme)",
        year=2023,
        source_type="uploaded_paper",
        citation=(
            "Theme covers algorithmic fidelity: group-level conditioning works "
            "in some domains, but does not recover individual truth."
        ),
        notes=(
            "Source-of-record for: group_level_conditioning_ok, "
            "individual_truth_not_recoverable."
        ),
    ),
    _SourceSeed(
        key=SourceKey.RANDOM_SILICON_SAMPLING,
        title="Random Silicon Sampling — replicability and LLM bias warnings",
        authors="(uploaded research theme; multiple authors)",
        year=2024,
        source_type="uploaded_paper",
        citation=(
            "Theme covers cross-group / cross-topic replicability variance and "
            "documented LLM training biases that leak into simulated populations."
        ),
        notes=(
            "Source-of-record for: replicability_varies_by_group_topic, "
            "llm_training_biases_leak_into_sampling."
        ),
    ),
    _SourceSeed(
        key=SourceKey.GENERATIVE_AGENTS,
        title="Generative Agents — Interactive Simulacra of Human Behavior",
        authors="Park et al. (uploaded research theme)",
        year=2023,
        source_type="uploaded_paper",
        citation=(
            "Theme covers the memory-stream architecture (recency / relevance "
            "/ importance), reflection from observations, and the "
            "observation→reflection→planning loop."
        ),
        notes=(
            "Source-of-record for: memory_recency_relevance_importance, "
            "reflection_from_observations, "
            "observation_reflection_planning_loop."
        ),
    ),
    _SourceSeed(
        key=SourceKey.BELIEF_NETWORK,
        title="Belief Networks Beyond Demographics — empirical alignment for personas",
        authors="(uploaded research theme; multiple authors)",
        year=2024,
        source_type="uploaded_paper",
        citation=(
            "Theme covers: demographic-only roleplay is unreliable; empirical "
            "belief networks improve persona alignment; spillover only within "
            "supported related-topic networks."
        ),
        notes=(
            "Source-of-record for: demographic_only_roleplay_unreliable, "
            "empirical_belief_network_improves_alignment, "
            "bounded_same_cluster_spillover."
        ),
    ),
    _SourceSeed(
        key=SourceKey.INFORMATION_VS_CONFORMITY,
        title="Information vs Social Conformity — informational and normative pressure",
        authors="(uploaded research theme; multiple authors)",
        year=2022,
        source_type="uploaded_paper",
        citation=(
            "Theme covers the dual pressures (informational vs normative) and "
            "the compliance-vs-acceptance distinction in opinion shift."
        ),
        notes=(
            "Source-of-record for: informational_vs_normative_conformity, "
            "compliance_vs_acceptance."
        ),
    ),
    _SourceSeed(
        key=SourceKey.PUBLIC_OPINION_BIAS,
        title="Public Opinion Simulation — geographic, cultural, language, and economic bias",
        authors="(uploaded research theme; multiple authors)",
        year=2024,
        source_type="uploaded_paper",
        citation=(
            "Theme catalogs LLM-driven public-opinion simulation bias: "
            "geographic, cultural, language, and economic variation; "
            "Western/English samples typically perform best."
        ),
        notes=(
            "Source-of-record for: geo_cultural_language_economy_bias, "
            "western_english_performance_tilt."
        ),
    ),
)


# ---------------------------------------------------------------------------
# 2) Behavioral mechanisms
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _MechanismSeed:
    key: str
    name: str
    category: str
    description: str
    when_to_apply: str
    when_not_to_apply: str
    default_strength: float
    status: str
    sources: tuple[tuple[str, str, str], ...]
    """Each source link: (SourceKey, support_type, excerpt_or_summary)."""


SEED_MECHANISMS: tuple[_MechanismSeed, ...] = (
    # ----- Winning Arguments -----------------------------------------------
    _MechanismSeed(
        key=MechanismKey.ENTRY_ORDER_ADVANTAGE,
        name="entry_order_advantage",
        category="argument_style",
        description=(
            "In multi-turn debates, the first counter-argument an agent "
            "engages with often carries disproportionate weight on final "
            "stance — entering early gives a structural anchor for the "
            "back-and-forth that follows."
        ),
        when_to_apply=(
            "When sequencing peer-to-peer or persuader-to-persuadee turns "
            "and the order of who-speaks-first is a free choice."
        ),
        when_not_to_apply=(
            "When the debate is a one-shot exchange (no back-and-forth) — "
            "the entry-order advantage is observed in interactive threads."
        ),
        default_strength=0.55,
        status="active",
        sources=((
            SourceKey.WINNING_ARGUMENTS,
            "empirical_result",
            "Earlier entries into a back-and-forth thread on ChangeMyView "
            "are over-represented in stance-change deltas.",
        ),),
    ),
    _MechanismSeed(
        key=MechanismKey.BACK_AND_FORTH_DYNAMIC,
        name="back_and_forth_dynamic",
        category="argument_style",
        description=(
            "Persuasion success correlates with sustained back-and-forth — "
            "agents that exchange multiple turns are more likely to shift "
            "than agents who exchange a single argument."
        ),
        when_to_apply=(
            "When designing simulation rounds that expose agents to peer "
            "arguments; favour multi-turn exchanges over one-shot drops."
        ),
        when_not_to_apply=(
            "When the round structure is non-conversational (e.g. proof "
            "exposure)."
        ),
        default_strength=0.5,
        status="active",
        sources=((
            SourceKey.WINNING_ARGUMENTS,
            "empirical_result",
            "Multi-turn exchanges on ChangeMyView produce a higher rate of "
            "delta awards than single-turn drops.",
        ),),
    ),
    _MechanismSeed(
        key=MechanismKey.DIMINISHING_RETURNS_OF_ARGUMENTS,
        name="diminishing_returns_of_arguments",
        category="argument_style",
        description=(
            "Each additional argument in a chain carries less marginal "
            "persuasive force. Agents stop engaging or harden after a "
            "saturation point."
        ),
        when_to_apply=(
            "When deciding how many peer arguments to surface in a debate "
            "round — surface the strongest few, not the most."
        ),
        when_not_to_apply=(
            "When the goal is exploring a long argumentative landscape "
            "(e.g. evidence-graph traversal), not changing stance."
        ),
        default_strength=0.5,
        status="active",
        sources=((
            SourceKey.WINNING_ARGUMENTS,
            "empirical_result",
            "Persuasion delta plateaus as argument count rises; later "
            "arguments contribute marginally less.",
        ),),
    ),
    _MechanismSeed(
        key=MechanismKey.EVIDENCE_LINKING_DRIVES_CHANGE,
        name="evidence_linking_drives_change",
        category="evidence_processing",
        description=(
            "Arguments that explicitly link to external, citable evidence "
            "are more likely to produce stance change than purely "
            "rhetorical arguments."
        ),
        when_to_apply=(
            "When constructing peer-to-peer arguments in simulation; "
            "prefer arguments that anchor to a real evidence_anchor over "
            "ones that are pure rhetoric."
        ),
        when_not_to_apply=(
            "When evidence is sparse and forced citations would be "
            "fabrications — better silence than fake citations."
        ),
        default_strength=0.6,
        status="active",
        sources=((
            SourceKey.WINNING_ARGUMENTS,
            "empirical_result",
            "Arguments that include URLs / citations have a higher rate of "
            "successful opinion change on ChangeMyView.",
        ),),
    ),
    _MechanismSeed(
        key=MechanismKey.ARGUMENT_INTENSITY_PENALTY,
        name="argument_intensity_penalty",
        category="argument_style",
        description=(
            "Highly intense / emotional / hostile argument styles correlate "
            "with LOWER stance-change rates. Calibrated, measured tone is "
            "more persuasive on average."
        ),
        when_to_apply=(
            "When parameterizing peer-argument generation tone; bias "
            "agents toward calibrated language unless the agent's profile "
            "explicitly carries a high-intensity persuasion style."
        ),
        when_not_to_apply=(
            "When simulating a population that is *known* to favour "
            "high-intensity argumentation (rare in commerce)."
        ),
        default_strength=0.45,
        status="active",
        sources=((
            SourceKey.WINNING_ARGUMENTS,
            "caution_or_limitation",
            "High-intensity / hostile language correlates with reduced "
            "delta probability on ChangeMyView.",
        ),),
    ),
    _MechanismSeed(
        key=MechanismKey.SUBLINEAR_PERSUADER_COUNT,
        name="sublinear_persuader_count",
        category="social_influence",
        description=(
            "Adding more persuaders does NOT linearly increase stance-change "
            "probability; the marginal effect of each new persuader drops. "
            "Population-Mode debate samples should not assume linear "
            "scaling."
        ),
        when_to_apply=(
            "When designing peer-sampling counts for the social-influence "
            "round; cap at a small fan-out and trust convergence."
        ),
        when_not_to_apply=(
            "When the goal is breadth coverage (e.g. measuring how many "
            "agents an argument *reaches*), not stance change."
        ),
        default_strength=0.5,
        status="active",
        sources=((
            SourceKey.WINNING_ARGUMENTS,
            "empirical_result",
            "Stance-change probability is sublinear in persuader count; "
            "adding the 5th, 10th, 20th persuader yields diminishing impact.",
        ),),
    ),
    _MechanismSeed(
        key=MechanismKey.OPINION_MALLEABILITY_HETEROGENEITY,
        name="opinion_malleability_heterogeneity",
        category="opinion_change",
        description=(
            "Different agents have very different baseline malleability — "
            "some shift easily on weak evidence, others are stable across "
            "many strong arguments. Population samples should not assume "
            "uniform susceptibility."
        ),
        when_to_apply=(
            "When initializing per-persona susceptibility; bias toward a "
            "distribution rather than a single global susceptibility value."
        ),
        when_not_to_apply=(
            "When deliberately running a uniform-cohort comparison study."
        ),
        default_strength=0.5,
        status="active",
        sources=((
            SourceKey.WINNING_ARGUMENTS,
            "empirical_result",
            "Malleability varies by user; some authors give frequent "
            "deltas, others almost never.",
        ),),
    ),

    # ----- Persuasion for Good ---------------------------------------------
    _MechanismSeed(
        key=MechanismKey.STRATEGY_PERSONALIZATION,
        name="strategy_personalization",
        category="persuasion",
        description=(
            "Persuasion strategy choice should be matched to the audience's "
            "profile (logical for analytical agents, personal_story for "
            "narrative-receptive agents, etc.) rather than applied uniformly."
        ),
        when_to_apply=(
            "When choosing a persuasion strategy in simulation; bias "
            "selection by the persona's `communication_style` and "
            "`trust_triggers`."
        ),
        when_not_to_apply=(
            "When the persona's communication_style is `unknown` — fall "
            "back to a balanced default rather than guess."
        ),
        default_strength=0.55,
        status="active",
        sources=((
            SourceKey.PERSUASION_FOR_GOOD,
            "direct_claim",
            "Personalized persuasion strategy outperforms uniform strategy "
            "selection across the Persuasion-for-Good corpus.",
        ),),
    ),
    _MechanismSeed(
        key=MechanismKey.LOGICAL_VS_EMOTIONAL_APPEAL_BALANCE,
        name="logical_vs_emotional_appeal_balance",
        category="persuasion",
        description=(
            "Mixing logical_appeal with at least one emotional_appeal or "
            "personal_story tends to outperform pure-logical-only argument "
            "stacks across most audiences."
        ),
        when_to_apply=(
            "When constructing a multi-strategy argument; do not stack "
            "logical_appeal alone."
        ),
        when_not_to_apply=(
            "When the audience's profile explicitly rejects emotional "
            "appeals (e.g. enterprise procurement)."
        ),
        default_strength=0.5,
        status="active",
        sources=((
            SourceKey.PERSUASION_FOR_GOOD,
            "empirical_result",
            "Mixed-strategy conversations outperform single-strategy ones.",
        ),),
    ),
    _MechanismSeed(
        key=MechanismKey.INQUIRY_BEFORE_PERSUASION,
        name="inquiry_before_persuasion",
        category="persuasion",
        description=(
            "Strategies that begin with inquiry "
            "(source_related_inquiry / task_related_inquiry / "
            "personal_related_inquiry) BEFORE persuasion outperform "
            "strategies that lead with the appeal."
        ),
        when_to_apply=(
            "When sequencing turns in a multi-turn persuasion round; "
            "prefer to open with inquiry, then appeal."
        ),
        when_not_to_apply=(
            "When the round is single-turn — there is no opportunity for "
            "an inquiry phase."
        ),
        default_strength=0.55,
        status="active",
        sources=((
            SourceKey.PERSUASION_FOR_GOOD,
            "empirical_result",
            "Inquiry-led conversations outperform appeal-first conversations "
            "in the Persuasion-for-Good corpus.",
        ),),
    ),

    # ----- Silicon sampling ------------------------------------------------
    _MechanismSeed(
        key=MechanismKey.GROUP_LEVEL_CONDITIONING_OK,
        name="group_level_conditioning_ok",
        category="population_sampling",
        description=(
            "LLM-conditioned group-level distributions can recover plausible "
            "aggregate stances — but only as group-level signals, never "
            "as individual truth."
        ),
        when_to_apply=(
            "When reporting Population-Mode aggregate stance distributions "
            "in well-supported domains (commerce, consumer goods)."
        ),
        when_not_to_apply=(
            "When making individual-level claims about a specific persona's "
            "real-world behavior."
        ),
        default_strength=0.5,
        status="active",
        sources=((
            SourceKey.SILICON_SAMPLING,
            "direct_claim",
            "Algorithmic fidelity holds at the group level for many domains; "
            "individual fidelity does not.",
        ),),
    ),
    _MechanismSeed(
        key=MechanismKey.INDIVIDUAL_TRUTH_NOT_RECOVERABLE,
        name="individual_truth_not_recoverable",
        category="simulation_bias",
        description=(
            "An LLM-conditioned simulated individual's stance is not a "
            "ground-truth claim about any specific real person, even when "
            "demographic conditioning is applied. The framework MUST not "
            "imply otherwise."
        ),
        when_to_apply=(
            "Always — when surfacing per-persona stances in the UI, frame "
            "them as 'one synthetic agent's reasoning', never as a "
            "prediction about a real individual."
        ),
        when_not_to_apply=(
            "Never. This is a hard constraint."
        ),
        default_strength=0.9,
        status="active",
        sources=((
            SourceKey.SILICON_SAMPLING,
            "caution_or_limitation",
            "Individual-level fidelity is not established; group-level may be.",
        ),),
    ),
    _MechanismSeed(
        key=MechanismKey.REPLICABILITY_VARIES_BY_GROUP_TOPIC,
        name="replicability_varies_by_group_topic",
        category="simulation_bias",
        description=(
            "Silicon-sampling replicability varies sharply by group and "
            "topic. Some domains replicate observed survey distributions "
            "well; others fail. The framework should NOT assume uniform "
            "replicability across topics."
        ),
        when_to_apply=(
            "When labelling society_strength in the audit panel — drop "
            "to 'thin' for low-evidence domains and topics with poor "
            "replicability priors."
        ),
        when_not_to_apply=(
            "Never silently."
        ),
        default_strength=0.5,
        status="active",
        sources=((
            SourceKey.RANDOM_SILICON_SAMPLING,
            "empirical_result",
            "Replication of human-survey statistics by silicon samples is "
            "uneven across groups and topics.",
        ),),
    ),
    _MechanismSeed(
        key=MechanismKey.LLM_TRAINING_BIASES_LEAK_INTO_SAMPLING,
        name="llm_training_biases_leak_into_sampling",
        category="simulation_bias",
        description=(
            "Documented LLM training biases (geo, cultural, language) leak "
            "into silicon-sampling outputs. Population-Mode results MUST "
            "carry caveats about these known biases when surfaced."
        ),
        when_to_apply=(
            "When generating the audit panel's `representativeness_caveats` "
            "list — always include LLM-bias caveats for non-Western / "
            "non-English populations."
        ),
        when_not_to_apply=(
            "Never silently."
        ),
        default_strength=0.5,
        status="active",
        sources=((
            SourceKey.RANDOM_SILICON_SAMPLING,
            "caution_or_limitation",
            "LLM training distributions are documented to over-represent "
            "Western / English data and under-represent others.",
        ),),
    ),

    # ----- Generative Agents -----------------------------------------------
    _MechanismSeed(
        key=MechanismKey.MEMORY_RECENCY_RELEVANCE_IMPORTANCE,
        name="memory_recency_relevance_importance",
        category="memory",
        description=(
            "Agent memory streams should weight retrieval by a combination "
            "of recency, relevance to current context, and importance "
            "(domain-specific salience)."
        ),
        when_to_apply=(
            "When designing agent state-passing across simulation rounds; "
            "weight stored observations by all three axes."
        ),
        when_not_to_apply=(
            "When simulation is single-round — memory weighting has no "
            "effect on a one-shot stance."
        ),
        default_strength=0.5,
        status="active",
        sources=((
            SourceKey.GENERATIVE_AGENTS,
            "implementation_inspiration",
            "Generative-Agents memory stream uses recency × relevance × "
            "importance as the retrieval scoring function.",
        ),),
    ),
    _MechanismSeed(
        key=MechanismKey.REFLECTION_FROM_OBSERVATIONS,
        name="reflection_from_observations",
        category="memory",
        description=(
            "Agents periodically reflect over recent observations to "
            "synthesize higher-order beliefs. Without reflection, agents "
            "drift between turns rather than building coherent stance."
        ),
        when_to_apply=(
            "Between simulation rounds in a Population-Mode debate — "
            "summarize the agent's accumulated observations into a stable "
            "stance rationale."
        ),
        when_not_to_apply=(
            "When the simulation is single-round."
        ),
        default_strength=0.45,
        status="active",
        sources=((
            SourceKey.GENERATIVE_AGENTS,
            "implementation_inspiration",
            "Park et al. introduce a reflection step that turns observations "
            "into higher-order beliefs.",
        ),),
    ),
    _MechanismSeed(
        key=MechanismKey.OBSERVATION_REFLECTION_PLANNING_LOOP,
        name="observation_reflection_planning_loop",
        category="planning",
        description=(
            "Generative agents follow an observation → reflection → planning "
            "loop. In Population Mode, the same loop maps onto round-by-"
            "round state transitions."
        ),
        when_to_apply=(
            "When designing the simulation engine's per-round state "
            "transitions for an extended horizon."
        ),
        when_not_to_apply=(
            "When the simulation is short-horizon and the planning step "
            "would add cost without behavioral signal."
        ),
        default_strength=0.45,
        status="experimental",
        sources=((
            SourceKey.GENERATIVE_AGENTS,
            "implementation_inspiration",
            "Park et al. propose the observation → reflection → planning "
            "loop as the agent's core cognitive cycle.",
        ),),
    ),

    # ----- Belief network --------------------------------------------------
    _MechanismSeed(
        key=MechanismKey.DEMOGRAPHIC_ONLY_ROLEPLAY_UNRELIABLE,
        name="demographic_only_roleplay_unreliable",
        category="belief_network",
        description=(
            "Demographic-only LLM roleplay (age + gender + location) is "
            "documented as an unreliable foundation for persona behavior. "
            "Phase 8.2D's initializer REFUSES to construct a persona from "
            "demographic-only inputs without explicit `unsupported` flag."
        ),
        when_to_apply=(
            "Always check before initializing; if the persona inputs are "
            "demographic-only with no source-bound trait, refuse."
        ),
        when_not_to_apply=(
            "Never bypass."
        ),
        default_strength=0.9,
        status="active",
        sources=((
            SourceKey.BELIEF_NETWORK,
            "caution_or_limitation",
            "Demographic-only roleplay is empirically unreliable; persona "
            "alignment improves when belief-network grounding is added.",
        ),),
    ),
    _MechanismSeed(
        key=MechanismKey.EMPIRICAL_BELIEF_NETWORK_IMPROVES_ALIGNMENT,
        name="empirical_belief_network_improves_alignment",
        category="belief_network",
        description=(
            "Grounding personas in empirical belief networks "
            "(survey-derived clusters of related opinions) improves "
            "alignment with observed populations relative to demographic-"
            "only conditioning."
        ),
        when_to_apply=(
            "When an explicit belief network is available for the topic "
            "domain — anchor persona traits to it."
        ),
        when_not_to_apply=(
            "When the belief network for a topic is itself thin (low "
            "evidence) — better to surface uncertainty than to fake "
            "alignment."
        ),
        default_strength=0.55,
        status="active",
        sources=((
            SourceKey.BELIEF_NETWORK,
            "empirical_result",
            "Empirical belief-network conditioning outperforms demographic "
            "conditioning on alignment metrics.",
        ),),
    ),
    _MechanismSeed(
        key=MechanismKey.BOUNDED_SAME_CLUSTER_SPILLOVER,
        name="bounded_same_cluster_spillover",
        category="belief_network",
        description=(
            "Spillover between related opinions is permitted ONLY within "
            "supported same-cluster pairs and bounded to at most "
            "`moderate` inference strength. Strong spillover is forbidden "
            "by the framework."
        ),
        when_to_apply=(
            "When propagating an opinion from topic_a to topic_b — first "
            "verify a `belief_network_rules` row exists with relation_type "
            "in {same_cluster, adjacent_cluster}; respect its strength."
        ),
        when_not_to_apply=(
            "When no rule exists, OR the rule's strength is `none`."
        ),
        default_strength=0.4,
        status="active",
        sources=((
            SourceKey.BELIEF_NETWORK,
            "direct_claim",
            "Belief-network spillover is bounded; cross-cluster "
            "spillover is unsupported.",
        ),),
    ),

    # ----- Conformity ------------------------------------------------------
    _MechanismSeed(
        key=MechanismKey.INFORMATIONAL_VS_NORMATIVE_CONFORMITY,
        name="informational_vs_normative_conformity",
        category="conformity",
        description=(
            "Conformity has two distinct drivers: informational (peers "
            "carry information I lack) and normative (peers exert social "
            "pressure even without new information). The two have "
            "different effect signatures."
        ),
        when_to_apply=(
            "When attributing a stance shift in the social-influence round "
            "— attempt to label the cause as informational vs normative."
        ),
        when_not_to_apply=(
            "When the round structure does not separate the two."
        ),
        default_strength=0.5,
        status="active",
        sources=((
            SourceKey.INFORMATION_VS_CONFORMITY,
            "direct_claim",
            "Conformity decomposes into informational and normative drivers "
            "with different temporal and durability signatures.",
        ),),
    ),
    _MechanismSeed(
        key=MechanismKey.COMPLIANCE_VS_ACCEPTANCE,
        name="compliance_vs_acceptance",
        category="conformity",
        description=(
            "An agent's apparent stance shift can be COMPLIANCE (surface "
            "conformity that reverts when peers leave) or ACCEPTANCE "
            "(internalized belief change). Compliance dominates in "
            "normative-pressure-only scenarios; acceptance requires "
            "informational input."
        ),
        when_to_apply=(
            "When characterizing the durability of a stance shift in "
            "round 6 — flag compliance-only shifts so they don't get "
            "treated as durable."
        ),
        when_not_to_apply=(
            "When durability is not measured."
        ),
        default_strength=0.5,
        status="active",
        sources=((
            SourceKey.INFORMATION_VS_CONFORMITY,
            "theoretical_support",
            "The compliance-vs-acceptance distinction predicts whether a "
            "shift will persist after peer pressure is removed.",
        ),),
    ),

    # ----- Public opinion bias --------------------------------------------
    _MechanismSeed(
        key=MechanismKey.GEO_CULTURAL_LANGUAGE_ECONOMY_BIAS,
        name="geo_cultural_language_economy_bias",
        category="simulation_bias",
        description=(
            "Public-opinion simulation results vary by geography, culture, "
            "language, and economy. Population-Mode's audit panel must "
            "carry coverage labels and caveats reflecting these axes."
        ),
        when_to_apply=(
            "When labelling `geography_coverage_label` and "
            "`representativeness_caveats` in the audit row."
        ),
        when_not_to_apply=(
            "Never silently."
        ),
        default_strength=0.5,
        status="active",
        sources=((
            SourceKey.PUBLIC_OPINION_BIAS,
            "empirical_result",
            "Sim outputs differ measurably across geo/cultural/language/"
            "economic strata.",
        ),),
    ),
    _MechanismSeed(
        key=MechanismKey.WESTERN_ENGLISH_PERFORMANCE_TILT,
        name="western_english_performance_tilt",
        category="simulation_bias",
        description=(
            "LLM-driven public-opinion simulation typically performs better "
            "on Western / English-language populations and worse on "
            "non-Western / non-English populations. Outputs for the latter "
            "MUST be flagged as lower-confidence."
        ),
        when_to_apply=(
            "When generating audit caveats for non-Western markets."
        ),
        when_not_to_apply=(
            "Never silently."
        ),
        default_strength=0.5,
        status="active",
        sources=((
            SourceKey.PUBLIC_OPINION_BIAS,
            "caution_or_limitation",
            "Western-English populations are over-represented in LLM "
            "training data; sim quality reflects this tilt.",
        ),),
    ),
)


# ---------------------------------------------------------------------------
# 3) Persuasion strategies (14)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _StrategySeed:
    name: str
    description: str
    source_key: str
    usage_notes: str | None


SEED_STRATEGIES: tuple[_StrategySeed, ...] = (
    _StrategySeed(
        name="logical_appeal",
        description=(
            "Appeal to evidence, reasoning, or logical structure. Effective "
            "when the audience reports analytical framing."
        ),
        source_key=SourceKey.PERSUASION_FOR_GOOD,
        usage_notes=None,
    ),
    _StrategySeed(
        name="emotional_appeal",
        description=(
            "Appeal to the audience's emotions or values. Effective when "
            "the audience reports values-driven decision making."
        ),
        source_key=SourceKey.PERSUASION_FOR_GOOD,
        usage_notes=None,
    ),
    _StrategySeed(
        name="credibility_appeal",
        description=(
            "Appeal to source credibility (institutional authority, "
            "expertise, track record)."
        ),
        source_key=SourceKey.PERSUASION_FOR_GOOD,
        usage_notes=None,
    ),
    _StrategySeed(
        name="personal_story",
        description=(
            "Tell a first-person or close-second-person story illustrating "
            "the point. Effective when the audience is narrative-receptive."
        ),
        source_key=SourceKey.PERSUASION_FOR_GOOD,
        usage_notes=None,
    ),
    _StrategySeed(
        name="self_modeling",
        description=(
            "Persuader frames their own behavior as the model. 'I do X "
            "because Y.'"
        ),
        source_key=SourceKey.PERSUASION_FOR_GOOD,
        usage_notes=None,
    ),
    _StrategySeed(
        name="foot_in_the_door",
        description=(
            "Begin with a small, easy commitment that opens the way to the "
            "larger ask."
        ),
        source_key=SourceKey.PERSUASION_FOR_GOOD,
        usage_notes=(
            "Use sparingly in commerce contexts; it can be perceived as "
            "manipulative when scaled."
        ),
    ),
    _StrategySeed(
        name="task_product_information",
        description=(
            "Provide concrete information about the product or task without "
            "an explicit ask."
        ),
        source_key=SourceKey.PERSUASION_FOR_GOOD,
        usage_notes=None,
    ),
    _StrategySeed(
        name="source_related_inquiry",
        description=(
            "Open by asking the audience about the source / background of "
            "their current opinion. Inquiry-led conversation."
        ),
        source_key=SourceKey.PERSUASION_FOR_GOOD,
        usage_notes=(
            "Pairs well with `inquiry_before_persuasion` mechanism."
        ),
    ),
    _StrategySeed(
        name="task_related_inquiry",
        description=(
            "Open by asking the audience about their current task / "
            "behavior to ground the conversation."
        ),
        source_key=SourceKey.PERSUASION_FOR_GOOD,
        usage_notes=None,
    ),
    _StrategySeed(
        name="personal_related_inquiry",
        description=(
            "Open by asking the audience about themselves to surface "
            "trust_triggers and decision criteria."
        ),
        source_key=SourceKey.PERSUASION_FOR_GOOD,
        usage_notes=None,
    ),
    _StrategySeed(
        name="evidence_linking",
        description=(
            "Tie the argument to a specific external citation or piece of "
            "evidence. Mechanism `evidence_linking_drives_change` quantifies "
            "the effect."
        ),
        source_key=SourceKey.WINNING_ARGUMENTS,
        usage_notes=(
            "Pair only with real evidence — fabricating citations is "
            "structurally forbidden by the framework."
        ),
    ),
    _StrategySeed(
        name="social_proof",
        description=(
            "Cite peer behavior or peer adoption to justify the position."
        ),
        source_key=SourceKey.INFORMATION_VS_CONFORMITY,
        usage_notes=(
            "Effect signature aligns with INFORMATIONAL conformity when "
            "peers carry information; otherwise NORMATIVE."
        ),
    ),
    _StrategySeed(
        name="authority_signal",
        description=(
            "Invoke an authoritative source (regulator, recognized "
            "institution, well-known practitioner)."
        ),
        source_key=SourceKey.PERSUASION_FOR_GOOD,
        usage_notes=None,
    ),
    _StrategySeed(
        name="peer_conformity_signal",
        description=(
            "Frame the argument as 'people like you do X'. Distinct from "
            "social_proof in that it is identity-keyed."
        ),
        source_key=SourceKey.INFORMATION_VS_CONFORMITY,
        usage_notes=None,
    ),
)


# ---------------------------------------------------------------------------
# 4) Belief network rules (research-anchored, NEVER 'strong')
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _BeliefRuleSeed:
    topic_a: str
    topic_b: str
    relation_type: str
    allowed_inference_strength: str
    notes: str | None
    source_key: str


SEED_BELIEF_RULES: tuple[_BeliefRuleSeed, ...] = (
    _BeliefRuleSeed(
        topic_a="brand_control_priorities",
        topic_b="ai_tooling_acceptance",
        relation_type="adjacent_cluster",
        allowed_inference_strength="weak",
        notes=(
            "Premium-brand operators concerned about brand control are "
            "weakly more skeptical of unmediated AI tooling."
        ),
        source_key=SourceKey.BELIEF_NETWORK,
    ),
    _BeliefRuleSeed(
        topic_a="price_sensitivity",
        topic_b="trust_in_free_alternatives",
        relation_type="same_cluster",
        allowed_inference_strength="moderate",
        notes=(
            "Price-sensitive personas show moderate same-cluster spillover "
            "into trust toward free alternatives."
        ),
        source_key=SourceKey.BELIEF_NETWORK,
    ),
    _BeliefRuleSeed(
        topic_a="environmental_values",
        topic_b="willingness_to_pay_for_eco",
        relation_type="same_cluster",
        allowed_inference_strength="moderate",
        notes=(
            "Environmental-values cluster has moderate spillover to "
            "willingness to pay for eco-positioned products."
        ),
        source_key=SourceKey.BELIEF_NETWORK,
    ),
    _BeliefRuleSeed(
        topic_a="political_opinion_left_right",
        topic_b="consumer_brand_preference",
        relation_type="unrelated",
        allowed_inference_strength="none",
        notes=(
            "No supported same-cluster spillover; political identity must "
            "NOT be used to predict consumer brand preferences in V0."
        ),
        source_key=SourceKey.BELIEF_NETWORK,
    ),
    _BeliefRuleSeed(
        topic_a="health_status",
        topic_b="commerce_purchase_intent",
        relation_type="unrelated",
        allowed_inference_strength="none",
        notes=(
            "Health information is sensitive AND unrelated to commerce "
            "purchase intent for V0; spillover is forbidden."
        ),
        source_key=SourceKey.BELIEF_NETWORK,
    ),
    _BeliefRuleSeed(
        topic_a="anti_subscription_sentiment",
        topic_b="willingness_to_pay_subscription",
        relation_type="conflict",
        allowed_inference_strength="moderate",
        notes=(
            "Conflict cluster: anti-subscription personas are moderately "
            "less likely to convert on subscription pricing."
        ),
        source_key=SourceKey.BELIEF_NETWORK,
    ),
)


# ---------------------------------------------------------------------------
# 5) Mechanism applicability rules (domain × mechanism)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ApplicabilityRuleSeed:
    mechanism_key: str
    domain_label: str
    applies_when: dict[str, Any]
    notes: str | None
    source_key: str | None


SEED_APPLICABILITY_RULES: tuple[_ApplicabilityRuleSeed, ...] = (
    _ApplicabilityRuleSeed(
        mechanism_key=MechanismKey.STRATEGY_PERSONALIZATION,
        domain_label="commerce",
        applies_when={"requires": ["communication_style", "trust_triggers"]},
        notes="Apply only when both fields are source-supported.",
        source_key=SourceKey.PERSUASION_FOR_GOOD,
    ),
    _ApplicabilityRuleSeed(
        mechanism_key=MechanismKey.STRATEGY_PERSONALIZATION,
        domain_label="saas_tooling",
        applies_when={"requires": ["communication_style"]},
        notes="SaaS tooling decisions weight communication_style heavily.",
        source_key=SourceKey.PERSUASION_FOR_GOOD,
    ),
    _ApplicabilityRuleSeed(
        mechanism_key=MechanismKey.EVIDENCE_LINKING_DRIVES_CHANGE,
        domain_label="commerce",
        applies_when={"requires_evidence_anchor": True},
        notes="Only apply when a real evidence_anchor is bound.",
        source_key=SourceKey.WINNING_ARGUMENTS,
    ),
    _ApplicabilityRuleSeed(
        mechanism_key=MechanismKey.DEMOGRAPHIC_ONLY_ROLEPLAY_UNRELIABLE,
        domain_label="unsupported_demographic_only",
        applies_when={"refuses_initialization": True},
        notes=(
            "Initializer MUST refuse persona construction in this domain "
            "unless the caller explicitly opts in to a demographic-only "
            "experimental mode."
        ),
        source_key=SourceKey.BELIEF_NETWORK,
    ),
    _ApplicabilityRuleSeed(
        mechanism_key=MechanismKey.EMPIRICAL_BELIEF_NETWORK_IMPROVES_ALIGNMENT,
        domain_label="well_supported_topic",
        applies_when={"requires_belief_rules": True},
        notes=(
            "Apply only when at least one same-cluster or adjacent-cluster "
            "belief rule is loaded for the topic."
        ),
        source_key=SourceKey.BELIEF_NETWORK,
    ),
    _ApplicabilityRuleSeed(
        mechanism_key=MechanismKey.BOUNDED_SAME_CLUSTER_SPILLOVER,
        domain_label="well_supported_topic",
        applies_when={"max_strength": "moderate"},
        notes="Spillover never exceeds 'moderate'.",
        source_key=SourceKey.BELIEF_NETWORK,
    ),
    _ApplicabilityRuleSeed(
        mechanism_key=MechanismKey.WESTERN_ENGLISH_PERFORMANCE_TILT,
        domain_label="low_evidence_domain",
        applies_when={"requires_caveat": True},
        notes=(
            "Audit panel MUST surface a Western/English tilt caveat for "
            "non-Western markets."
        ),
        source_key=SourceKey.PUBLIC_OPINION_BIAS,
    ),
    _ApplicabilityRuleSeed(
        mechanism_key=MechanismKey.GEO_CULTURAL_LANGUAGE_ECONOMY_BIAS,
        domain_label="political_opinion",
        applies_when={"requires_caveat": True},
        notes=(
            "Political-opinion sims have particularly large bias signatures; "
            "always caveat."
        ),
        source_key=SourceKey.PUBLIC_OPINION_BIAS,
    ),
    _ApplicabilityRuleSeed(
        mechanism_key=MechanismKey.LLM_TRAINING_BIASES_LEAK_INTO_SAMPLING,
        domain_label="political_opinion",
        applies_when={"requires_caveat": True},
        notes="Always caveat LLM bias for political-opinion outputs.",
        source_key=SourceKey.RANDOM_SILICON_SAMPLING,
    ),
    _ApplicabilityRuleSeed(
        mechanism_key=MechanismKey.INDIVIDUAL_TRUTH_NOT_RECOVERABLE,
        domain_label="commerce",
        applies_when={"frame_individual_responses_as_synthetic": True},
        notes=(
            "Per-persona stances are surfaced as 'one synthetic agent's "
            "reasoning', never as a prediction about a real individual."
        ),
        source_key=SourceKey.SILICON_SAMPLING,
    ),
    _ApplicabilityRuleSeed(
        mechanism_key=MechanismKey.MEMORY_RECENCY_RELEVANCE_IMPORTANCE,
        domain_label="commerce",
        applies_when={"multi_round_required": True},
        notes="Memory weighting only relevant in multi-round simulations.",
        source_key=SourceKey.GENERATIVE_AGENTS,
    ),
    _ApplicabilityRuleSeed(
        mechanism_key=MechanismKey.COMPLIANCE_VS_ACCEPTANCE,
        domain_label="commerce",
        applies_when={"durability_label_required": True},
        notes="Round-6 shift attribution must label compliance vs acceptance.",
        source_key=SourceKey.INFORMATION_VS_CONFORMITY,
    ),
)


# ---------------------------------------------------------------------------
# Convenience: counts (used by tests as drift checks)
# ---------------------------------------------------------------------------


def seed_summary() -> dict[str, int]:
    return {
        "research_sources": len(SEED_SOURCES),
        "behavioral_mechanisms": len(SEED_MECHANISMS),
        "persuasion_strategies": len(SEED_STRATEGIES),
        "belief_network_rules": len(SEED_BELIEF_RULES),
        "applicability_rules": len(SEED_APPLICABILITY_RULES),
        "evidence_links": sum(len(m.sources) for m in SEED_MECHANISMS),
    }
