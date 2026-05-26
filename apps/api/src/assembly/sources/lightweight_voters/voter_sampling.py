"""Phase 12C — generate 100 lightweight voters from SocietyCohort centroids.

Deterministic given (cohorts, simulation_seed). Zero LLM calls.
Voters are sampled around each cohort's centroid (role / stance /
psy / objection / proof_need) with bounded ±0.15 jitter on psy
values. Quota gates enforce min-per-cohort, role-concentration cap,
and competitor-user cap before sampling.

Round 0 (initial intent) is computed at the end via the existing
`infer_simulated_intent(...)` cascade — same rule cascade the rich
personas use.
"""
from __future__ import annotations

import hashlib
import random
import uuid as _uuid_mod
from typing import Any
from uuid import UUID

from assembly.calibration.market_buckets import (
    map_assembly_intent_to_market_bucket,
)
from assembly.sources.intent_layer.inference import infer_simulated_intent
from assembly.sources.lightweight_voters.voter_schema import (
    HARD_RESISTANT_INTENTS,
    HARD_RESISTANT_ROLE_PATTERNS,
    LightweightVoter,
)


# Psychology fields the cascade reads. Voters MUST populate all of
# these so infer_simulated_intent() doesn't fall back to default 0.5.
_PSY_FIELDS: tuple[str, ...] = (
    "openness",
    "novelty_seeking",
    "trust_proof_threshold",
    "risk_tolerance",
    "price_sensitivity",
    "social_influence_susceptibility",
    "category_involvement_or_expertise",
)


def _stable_seed(*parts: str) -> int:
    """Derive a 64-bit integer from sha256(parts...) for use as a
    random.Random seed. Deterministic across processes."""
    h = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def _weighted_choice(
    rng: random.Random, items: dict[str, int] | dict[str, float],
) -> str | None:
    """Pick one key from a {key: weight} dict, proportional to
    weight. Returns None if items is empty or all weights are zero."""
    if not items:
        return None
    keys = list(items.keys())
    weights = [float(items[k]) for k in keys]
    if sum(weights) <= 0:
        return None
    return rng.choices(keys, weights=weights, k=1)[0]


def _clip(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _jitter(
    rng: random.Random, centroid: float, span: float = 0.15,
) -> float:
    """Bounded uniform jitter, clipped to [0, 1]."""
    return _clip(centroid + rng.uniform(-span, span))


def _coerce_centroid_value(v: Any, default: float = 0.5) -> float:
    """Robust to the two psychology_summary shapes seen in practice:
      (a) flat:     {trait: 0.5}                  → use directly
      (b) wrapped:  {trait: {"mean": 0.5, ...}}   → use .mean
    Anything else falls back to `default`.
    """
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        m = v.get("mean")
        if isinstance(m, (int, float)):
            return float(m)
    return default


def _coerce_dist_dict(
    raw: Any,
) -> dict[str, int]:
    """Robust to the two summary shapes seen in practice:
      (a) flat:    {"price": 3, "tone": 2}
      (b) wrapped: {"by_bucket": {"price": 3, ...}, "top_buckets": [...]}
    Returns a flat {key: int} for weighted sampling.
    """
    if not raw:
        return {}
    if isinstance(raw, dict):
        # Wrapped shape
        if "by_bucket" in raw and isinstance(raw["by_bucket"], dict):
            return {
                str(k): int(v) for k, v in raw["by_bucket"].items()
                if isinstance(v, (int, float)) and v > 0
            }
        # Flat shape — filter to int values
        out: dict[str, int] = {}
        for k, v in raw.items():
            if isinstance(v, (int, float)):
                if v > 0:
                    out[str(k)] = int(v)
        return out
    return {}


def _classify_hard_resistant(
    *,
    initial_intent: str,
    role: str,
    switching_resistance: float,
    trust_threshold: float,
    has_current_alternative: bool,
) -> tuple[bool, str | None]:
    """Decide whether a voter is hard_resistant + the reason label.

    Hard-resistant means: needs explicit proof satisfaction to move
    from the skeptical bucket to anything else in one influence round.
    Multiple signals can fire; the first match wins for the reason
    label (intent-based reasons take precedence).
    """
    if initial_intent in HARD_RESISTANT_INTENTS:
        if initial_intent == "would_reject":
            return True, "would_reject_intent"
        if initial_intent == "would_block":
            return True, "would_block_intent"
        if initial_intent == "loyal_to_current_alternative":
            return True, "loyal_to_current_alternative_intent"
    # Role-based / psy-based hard resistance only fires for voters
    # whose initial_intent already signals doubt. Without this guard,
    # a voter with `would_buy_now` intent could be marked hard_resistant
    # purely because of high trust threshold + a competitor role — which
    # contradicts the semantic ("would push back on adoption").
    bucket = _initial_bucket_from_intent(initial_intent)
    if bucket in ("receptive", "buyer"):
        return False, None
    for pattern in HARD_RESISTANT_ROLE_PATTERNS:
        if pattern in role:
            return True, f"role:{pattern}"
    # Strong combined signal: very high switching resistance AND
    # locked in on a current alternative AND high trust threshold.
    if (
        has_current_alternative
        and switching_resistance >= 0.75
        and trust_threshold >= 0.70
    ):
        return (
            True,
            "high_switching_resistance_locked_to_alternative",
        )
    return False, None


def _initial_bucket_from_intent(intent: str) -> str:
    """Wrap map_assembly_intent_to_market_bucket so callers don't have
    to handle the (bucket, warning) tuple."""
    try:
        bucket, _ = map_assembly_intent_to_market_bucket(intent)
    except Exception:
        bucket = "uncertain"
    return bucket


def allocate_voters_per_cohort(
    cohorts: list[dict[str, Any]],
    n: int = 100,
    min_per_cohort: int = 3,
    max_per_cohort: int = 30,
) -> dict[str, int]:
    """Distribute N voters across cohorts, respecting min/max quota
    gates and proportional to cohort_weight.

    Returns: dict[cohort_id (str) -> voter count].

    Quota gates:
      - every cohort gets at least min_per_cohort if N allows
      - max_per_cohort caps concentration, but is RELAXED when there
        are too few cohorts to fit N otherwise. The effective cap is
        max(max_per_cohort, ceil(n / n_cohorts * 1.5)).
      - residual from rounding is distributed proportionally
    """
    if not cohorts:
        return {}
    n_cohorts = len(cohorts)
    # Dynamic max_per_cohort: relax when few cohorts. With 9 cohorts
    # and N=100, ceil(100/9 * 1.5) = 17 — tighter than 30 (good).
    # With 2 cohorts and N=100, ceil(100/2 * 1.5) = 75 — relaxed
    # (necessary). With 1 cohort, ~150 → effectively no cap.
    import math
    effective_max = max(
        max_per_cohort, math.ceil(n / max(1, n_cohorts) * 1.5),
    )

    total_w = sum(float(c["cohort_weight"]) for c in cohorts) or 1.0
    raw: dict[str, float] = {}
    for c in cohorts:
        cid = str(c["cohort_id"])
        raw[cid] = n * float(c["cohort_weight"]) / total_w

    # Initial rounding
    alloc = {
        cid: max(min_per_cohort, round(v)) for cid, v in raw.items()
    }
    # Cap at effective_max
    alloc = {cid: min(effective_max, v) for cid, v in alloc.items()}

    # Adjust to hit exactly N total
    diff = n - sum(alloc.values())
    if diff != 0:
        # Sort by raw allocation (largest first if diff>0; smallest if diff<0)
        sorted_cids = sorted(
            raw.keys(),
            key=lambda c: raw[c],
            reverse=(diff > 0),
        )
        step = 1 if diff > 0 else -1
        i = 0
        while diff != 0 and i < len(sorted_cids) * 50:
            cid = sorted_cids[i % len(sorted_cids)]
            new_v = alloc[cid] + step
            if min_per_cohort <= new_v <= effective_max:
                alloc[cid] = new_v
                diff -= step
            i += 1
    return alloc


def generate_voters_from_cohorts(
    cohorts: list[dict[str, Any]],
    *,
    run_scope_id: str,
    simulation_seed: int,
    n: int = 100,
    role_concentration_cap: float = 0.30,
    competitor_user_cap: float = 0.50,
) -> tuple[list[LightweightVoter], list[str]]:
    """Generate `n` lightweight voters from cohort centroids.

    Each cohort dict must carry:
      - cohort_id (str or UUID)
      - cohort_label (str)
      - cohort_weight (float, 0-1)
      - role_distribution (dict[str, int])
      - stance_distribution (dict[str, int]) — drives initial intent
      - psychology_summary (dict[str, float]) — centroid for jitter
      - objection_summary (dict[str, int])
      - proof_need_summary (dict[str, int])
      - top_alternatives (dict[str, int])   — optional

    Returns (voters, warnings). Warnings is non-empty when a quota
    gate fired or coverage was incomplete.

    Determinism: same (cohorts, simulation_seed) → same voters.
    """
    warnings: list[str] = []
    if not cohorts:
        return [], ["no_cohorts_supplied"]

    # Step 1 — allocation
    alloc = allocate_voters_per_cohort(cohorts, n=n)
    if sum(alloc.values()) != n:
        warnings.append(
            f"voter_allocation_off_by={n - sum(alloc.values())}"
        )

    # Step 2 — sample voters
    voters: list[LightweightVoter] = []
    role_counts: dict[str, int] = {}
    competitor_user_count = 0

    by_id = {str(c["cohort_id"]): c for c in cohorts}
    for cohort_id_str, count in alloc.items():
        cohort = by_id[cohort_id_str]
        # Cohort id may be a real UUID (from DB) OR a string label
        # (from live_founder_brief ctx like "live_cohort_0"). Both
        # need to be representable as UUID for the schema; we derive
        # a deterministic UUID5 from the original id string.
        if isinstance(cohort["cohort_id"], UUID):
            cohort_id_uuid: UUID = cohort["cohort_id"]
        else:
            try:
                cohort_id_uuid = UUID(str(cohort["cohort_id"]))
            except (ValueError, AttributeError):
                cohort_id_uuid = _uuid_mod.uuid5(
                    _uuid_mod.NAMESPACE_OID,
                    str(cohort["cohort_id"]),
                )
        cohort_label = cohort["cohort_label"]
        cohort_weight = float(cohort["cohort_weight"])
        role_dist = _coerce_dist_dict(
            cohort.get("role_distribution"),
        )
        stance_dist = _coerce_dist_dict(
            cohort.get("stance_distribution"),
        )
        psy_centroid = cohort.get("psychology_summary") or {}
        objection_dist = _coerce_dist_dict(
            cohort.get("objection_summary"),
        )
        proof_dist = _coerce_dist_dict(
            cohort.get("proof_need_summary"),
        )
        alts = _coerce_dist_dict(cohort.get("top_alternatives"))
        # Phase 12C.1 — read the cohort's intent_distribution if the
        # orchestrator attached one. This carries the 24-rich intent
        # signal (including skeptical/loyal mass) directly into voter
        # sampling, bypassing the synthesized-stance-via-cascade path
        # that was erasing resistance.
        intent_dist = _coerce_dist_dict(
            cohort.get("intent_distribution"),
        )

        for i in range(count):
            seed_str = (
                f"{simulation_seed}|cohort:{cohort_id_str}|i:{i}"
            )
            seed_int = _stable_seed(run_scope_id, seed_str)
            rng = random.Random(seed_int)

            role = _weighted_choice(rng, role_dist) or "generic_buyer"
            # Stance: read directly so we can pass it to the cascade
            # via the synthesized final_ballot.
            stance = (
                _weighted_choice(rng, stance_dist)
                or "curious_but_unconvinced"
            )

            # Psychology — bounded jitter around centroid, clipped to
            # [0, 1]. Robust to both flat {trait: 0.5} and wrapped
            # {trait: {"mean": 0.5, ...}} shapes — see
            # _coerce_centroid_value. Defaults to 0.5 if missing.
            psy: dict[str, float] = {}
            for fld in _PSY_FIELDS:
                centroid_val = _coerce_centroid_value(
                    psy_centroid.get(fld) if psy_centroid else None,
                    default=0.5,
                )
                psy[fld] = _jitter(rng, centroid_val, 0.15)

            current_alt = _weighted_choice(rng, alts) if alts else None
            # Phase 12C.1 — derive current_alternative from role suffix
            # when role indicates a competitor user but the cohort
            # `top_alternatives` map is empty (common in live mode).
            if current_alt is None and role.startswith("competitor_user_"):
                current_alt = (
                    role.replace("competitor_user_", "")
                    .replace("_", " ")
                    .strip()
                    .title()
                ) or None
            primary_obj = (
                _weighted_choice(rng, objection_dist)
                if objection_dist else None
            )
            proof_need = (
                _weighted_choice(rng, proof_dist)
                if proof_dist else None
            )

            # Derived psy aggregates
            # NOTE: "social_influence_weight" expresses how much this
            # voter influences OTHERS. The susceptibility-to-others
            # is `social_influence_susceptibility` (psy field).
            extraversion_proxy = psy.get("openness", 0.5)
            social_influence_weight = _clip(
                (extraversion_proxy + psy["novelty_seeking"]) / 2,
            )
            switching_resistance = _clip(
                (psy["price_sensitivity"]
                 + psy["trust_proof_threshold"]) / 2,
            )

            # Track quota gates BEFORE creating voter
            is_competitor_user = role.startswith("competitor_user_")
            if (
                is_competitor_user
                and (competitor_user_count + 1) / n > competitor_user_cap
            ):
                # Replace role with a non-competitor-user from cohort
                non_cu = {
                    k: v for k, v in role_dist.items()
                    if not k.startswith("competitor_user_")
                }
                if non_cu:
                    role = _weighted_choice(rng, non_cu) or role
                    is_competitor_user = role.startswith("competitor_user_")

            cur_role_count = role_counts.get(role, 0)
            if (cur_role_count + 1) / n > role_concentration_cap:
                # Try a different role from cohort.
                alts_role = {
                    k: v for k, v in role_dist.items()
                    if k != role and (role_counts.get(k, 0) + 1) / n <= role_concentration_cap
                }
                if alts_role:
                    role = _weighted_choice(rng, alts_role) or role
                    is_competitor_user = role.startswith("competitor_user_")

            role_counts[role] = role_counts.get(role, 0) + 1
            if is_competitor_user:
                competitor_user_count += 1

            # Deterministic voter_id from (run_scope_id, cohort_id, i).
            # uuid4 would re-randomize on every run and break Round
            # 2's seeded RNG (which keys off str(voter_id)).
            deterministic_voter_id = _uuid_mod.uuid5(
                _uuid_mod.NAMESPACE_OID,
                f"voter|{run_scope_id}|{cohort_id_str}|{i}",
            )
            voter = LightweightVoter(
                voter_id=deterministic_voter_id,
                run_scope_id=run_scope_id,
                cohort_id=cohort_id_uuid,
                sampling_seed=seed_str,
                segment=cohort_label,
                role=role,
                current_alternative=current_alt,
                population_weight=cohort_weight / max(1, count),
                trust_threshold=psy["trust_proof_threshold"],
                novelty_seeking=psy["novelty_seeking"],
                price_sensitivity=psy["price_sensitivity"],
                category_expertise=psy[
                    "category_involvement_or_expertise"
                ],
                social_influence_weight=social_influence_weight,
                switching_resistance=switching_resistance,
                primary_objection=primary_obj,
                proof_need=proof_need,
            )

            # Step 3 — Round 0: initial intent.
            #
            # Phase 12C.1 path (preferred): the cohort dict carries
            # `intent_distribution` (a histogram of the 24-rich pipeline's
            # inferred intent labels for this cohort's members). We
            # sample voter.initial_intent directly from that histogram,
            # which preserves the cohort's skeptical/loyal mass.
            #
            # Legacy path (no intent_distribution attached, e.g.
            # synthetic test fixtures): fall back to the cascade with
            # the synthesized stance — historical behavior.
            if intent_dist:
                sampled_intent = _weighted_choice(rng, intent_dist)
                voter.initial_intent = (
                    sampled_intent or "would_consider_if_proven"
                )
                voter.evidence_basis = (
                    f"sampled_from_cohort={cohort_label}|"
                    f"role={role}|stance={stance}|"
                    f"source=cohort_intent_distribution|"
                    f"intent={voter.initial_intent}"
                )
            else:
                final_ballot = {
                    "private_stance": stance,
                    "top_proof_need": proof_need,
                    "public_private_delta": None,
                }
                draft = infer_simulated_intent(
                    persona_id=str(voter.voter_id),
                    cohort_id=cohort_id_str,
                    normalized_role=role,
                    psychology_value_map=psy,
                    pre_ballot=None,
                    final_ballot=final_ballot,
                    reflection_ballot=None,
                    persona_text_corpus="",
                    ballot_ids=[],
                    discussion_turn_ids=[],
                    memory_atom_ids=[],
                    cohort_objection_summary={
                        k: int(v) for k, v in objection_dist.items()
                    },
                )
                voter.initial_intent = draft.simulated_intent
                voter.evidence_basis = (
                    f"sampled_from_cohort={cohort_label}|"
                    f"role={role}|stance={stance}|"
                    f"cascade_rule={draft.evidence_basis[:100]}"
                )

            # Phase 12C.1 — set initial_bucket at sampling time. The
            # transition audit needs this BEFORE the influence loop
            # runs; without it we cannot distinguish "voter was always
            # receptive" from "voter started skeptical and converted".
            voter.initial_bucket = _initial_bucket_from_intent(
                voter.initial_intent,
            )

            # Phase 12C.1 — classify hard_resistant. Hard-resistant
            # voters need explicit proof satisfaction to cross from
            # `skeptical` to a non-skeptical bucket. The flag is
            # consumed by influence_loop.run_influence_rounds().
            hr, hr_reason = _classify_hard_resistant(
                initial_intent=voter.initial_intent,
                role=role,
                switching_resistance=voter.switching_resistance,
                trust_threshold=voter.trust_threshold,
                has_current_alternative=current_alt is not None,
            )
            voter.hard_resistant = hr
            voter.hard_resistant_reason = hr_reason

            voters.append(voter)

    # Step 4 — sanity warnings
    if role_concentration_cap and any(
        c / n > role_concentration_cap for c in role_counts.values()
    ):
        worst_role = max(role_counts, key=role_counts.get)
        warnings.append(
            f"role_concentration_violation:{worst_role}="
            f"{role_counts[worst_role] / n:.2f}"
        )
    if competitor_user_count / n > competitor_user_cap:
        warnings.append(
            f"competitor_user_violation:{competitor_user_count / n:.2f}"
        )
    # Stance coverage: warn if any non-zero stance in any cohort got
    # 0 voters (only checked at population level, not per cohort).
    seen_intents = {v.initial_intent for v in voters}
    if len(seen_intents) < 2:
        warnings.append(
            f"intent_collapse:only_{len(seen_intents)}_distinct_intents"
        )

    return voters, warnings
