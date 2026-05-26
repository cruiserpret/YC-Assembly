"""Phase 10B.3 — report-layer polish helpers.

Centralizes the four cosmetic-but-load-bearing transformations the
GlowPlate review demanded:

  1. Headline caveat relocation — the executive_summary should
     state the simulation result confidently; caveats live in the
     `caveats` list, not the lead sentence.
  2. Hardest-to-convince audience copy — populate even when the
     final resistant count is zero, by mining the strongest
     unresolved objections / proof needs / cohort signals.
  3. Best-fit audience copy — translate simulation-role labels
     into target-customer language ("remote workers and slow
     eaters who already understand the pain of food going cold").
  4. Evidence flavor — a one-line summary of which retrieval
     surfaces fed the run, including YouTube buyer-language when
     present.

Every helper returns a structured dict suitable to drop into the
founder report + a corresponding `*_quality.json` audit artifact.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any


# ---------------------------------------------------------------------------
# 1. Headline caveat relocation
# ---------------------------------------------------------------------------


_HEADLINE_BANNED_PHRASES: tuple[str, ...] = (
    "not a real-world purchase forecast",
    "not a real-world forecast",
    "should be validated with real prospects",
    "synthetic signal",
    "directional signal",
    "not a market verdict",
)


def _scrub_headline_caveat(line: str) -> str:
    """Strip apologetic clauses from a headline sentence. Leaves
    the confident result-statement intact."""
    if not line:
        return line
    # Drop everything after a "—" or "." that introduces the caveat
    # clause. We do a soft sweep: split on em-dash / parenthesis /
    # ", but" and re-test each fragment.
    parts = re.split(r"\s+[—–]\s+|\s+\((?=[A-Za-z])|\s+,\s+but\s+", line)
    surviving: list[str] = []
    for p in parts:
        low = p.lower()
        if any(b in low for b in _HEADLINE_BANNED_PHRASES):
            continue
        surviving.append(p.rstrip(")."))
    return " ".join(surviving).strip().rstrip(".") + (
        "." if surviving else ""
    )


def build_confident_headline(
    *,
    product_name: str,
    persona_count: int,
    receptive_final_count: int,
    shifted_toward_receptive: int,
    pre_distribution: dict[str, int],
    final_distribution: dict[str, int],
) -> str:
    """Confident result-statement headline. No caveats."""
    rec_phrase = (
        f"{receptive_final_count} of {persona_count} personas "
        "ended receptive"
    )
    shift_phrase = ""
    if shifted_toward_receptive > 0:
        shift_phrase = (
            f", with {shifted_toward_receptive} shifting toward "
            "stronger interest during discussion"
        )
    return (
        f"The synthetic society finished {_descriptor(receptive_final_count, persona_count)} "
        f"receptive: {rec_phrase}{shift_phrase}."
    )


def _descriptor(receptive: int, total: int) -> str:
    if total == 0:
        return "evenly"
    pct = receptive / total
    if pct >= 0.75:
        return "strongly"
    if pct >= 0.5:
        return "leaning"
    if pct >= 0.25:
        return "split but partly"
    return "with limited"


# ---------------------------------------------------------------------------
# 2. Hardest-to-convince audience
# ---------------------------------------------------------------------------


# Map objection/proof keywords → canonical concern descriptors. We
# use these to label why a cohort is hardest to convince when there
# are no fully-resistant ballots to cite.
_HARDEST_CONCERN_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsafe(?:ty|tier)?\b|\bburn|\bfire", re.IGNORECASE),
     "safety"),
    (re.compile(r"\bcertification|\bcertified|\bul[\- ]?listed|\bfda|\blfgb",
                re.IGNORECASE),
     "certification"),
    (re.compile(r"\b(?:food[\- ]contact|food[\- ]safe|food[\- ]grade)\b",
                re.IGNORECASE),
     "food-contact safety"),
    (re.compile(r"\bcoating|\bdurability|\bdurable\b|\bbreak|\bcrack",
                re.IGNORECASE),
     "coating / durability"),
    (re.compile(r"\bdishwasher|\bcleaning\b|\bwash", re.IGNORECASE),
     "dishwasher / cleaning durability"),
    (re.compile(r"\bauto[\- ]shutoff|\bshutoff", re.IGNORECASE),
     "auto-shutoff"),
    (re.compile(r"\bbattery|\bcharge|\bruntime", re.IGNORECASE),
     "battery / runtime under load"),
    (re.compile(r"\bprice|\bcost|\bvalue|\bprice-to-value", re.IGNORECASE),
     "price-to-value"),
    (re.compile(r"\bswitch(?:ing)?|\balternative|\balready\s+(?:use|own)",
                re.IGNORECASE),
     "switching from current alternative"),
    (re.compile(r"\btrust|\bbelieve\s+the\s+claim|\bskepti", re.IGNORECASE),
     "trust in claims"),
    (re.compile(r"\breview|\bproof|\bdata|\bevidence|\btest\s+result",
                re.IGNORECASE),
     "third-party proof"),
    (re.compile(r"\btemperature|\bhot\s+enough|\bhold\s+heat", re.IGNORECASE),
     "thermal performance"),
)


def _classify_concern(text: str) -> str | None:
    if not text:
        return None
    for rx, label in _HARDEST_CONCERN_HINTS:
        if rx.search(text):
            return label
    return None


def build_hardest_to_convince(
    *,
    role_distribution: dict[str, dict[str, int]],
    top_objections: list[dict[str, Any]],
    top_proof_needs: list[dict[str, Any]],
    target_customers: list[str],
) -> dict[str, Any]:
    """Compose the hardest-to-convince audience block.

    Order of preference for picking the audience:
      1. Roles with the highest `resistant` count.
      2. If no roles have resistant > 0, roles with the highest
         `uncertain` count.
      3. If both are tied, roles named "trust_seeker", "price_*",
         "competitor_user_*" (the canonical hardest archetypes).

    The descriptor is built from the top concerns surfaced in
    objections + proof needs.
    """
    rows: list[dict[str, Any]] = []
    for role, dist in role_distribution.items():
        rec = dist.get("receptive", 0)
        unc = dist.get("uncertain", 0)
        res = dist.get("resistant", 0)
        total = rec + unc + res
        rows.append({
            "role": role,
            "receptive": rec,
            "uncertain": unc,
            "resistant": res,
            "total": total,
        })

    primary_kind: str
    if any(r["resistant"] > 0 for r in rows):
        rows.sort(key=lambda r: (-r["resistant"], -r["uncertain"]))
        primary_kind = "resistant"
    elif any(r["uncertain"] > 0 for r in rows):
        rows.sort(key=lambda r: (-r["uncertain"], -r["resistant"]))
        primary_kind = "uncertain"
    else:
        rows.sort(key=lambda r: -r["total"])
        primary_kind = "all_receptive"

    top = [r for r in rows if r[primary_kind if primary_kind != "all_receptive" else "total"] > 0][:4]
    if not top:
        top = rows[:2]

    # Derive concern labels from top objections + proof needs.
    concerns: list[str] = []
    seen_concerns: set[str] = set()
    for entry in (top_objections or []) + (top_proof_needs or []):
        text = entry.get("bucket") or entry.get("text") or entry.get("title") or ""
        label = _classify_concern(str(text))
        if label and label not in seen_concerns:
            concerns.append(label)
            seen_concerns.add(label)
        if len(concerns) >= 4:
            break

    audience_phrase = _humanize_role_list(
        [r["role"] for r in top], target_customers,
    )

    if primary_kind == "resistant":
        copy = (
            f"{audience_phrase} were the hardest to move on this "
            f"run. They centered on "
            f"{_join_with_and(concerns) or 'unresolved proof needs'} "
            "before they could be convinced."
        )
    elif primary_kind == "uncertain":
        copy = (
            f"No cohort fully rejected the concept, but "
            f"{audience_phrase} still required stronger proof: "
            f"{_join_with_and(concerns) or 'specific verification of the headline claims'}."
        )
    else:
        copy = (
            f"{audience_phrase} were the hardest to fully convince "
            "even though all cohorts finished receptive — they "
            f"flagged {_join_with_and(concerns) or 'verification gaps'} "
            "as friction to clear before purchase."
        )

    return {
        "summary_copy": copy,
        "primary_kind": primary_kind,
        "concerns": concerns,
        "rows": top,
    }


# ---------------------------------------------------------------------------
# 3. Best-fit audience humanization
# ---------------------------------------------------------------------------


# Map normalized cohort/persona role labels → real customer
# language. Universal — never product-specific. Anything not
# matched falls through to a humanized version of the role string
# itself ("trust_seeker" → "trust-seekers").
_ROLE_HUMAN_MAP: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^trust[_\- ]seeker$", re.IGNORECASE),
     "trust-seekers (buyers who need certification + reviews before they buy)"),
    (re.compile(r"^price[_\- ](?:skeptic|sensitive|conscious)$",
                re.IGNORECASE),
     "price-sensitive buyers"),
    (re.compile(r"^competitor[_\- ]user[_\- ](.+)$", re.IGNORECASE),
     "{0} users"),
    (re.compile(r"^use[_\- ]case[_\- ]focused[_\- ]buyer$", re.IGNORECASE),
     "people with a clear use-case match"),
    (re.compile(r"^performance[_\- ]focused[_\- ]buyer$", re.IGNORECASE),
     "performance-focused buyers"),
    (re.compile(r"^convenience[_\- ]focused[_\- ]buyer$", re.IGNORECASE),
     "convenience-focused buyers"),
    (re.compile(r"^objection[_\- ]focused[_\- ]buyer$", re.IGNORECASE),
     "buyers with strong unresolved objections"),
    (re.compile(r"^format[_\- ]focused[_\- ]buyer$", re.IGNORECASE),
     "buyers focused on form factor"),
)


def humanize_role(role: str) -> str:
    """Translate a cohort/persona role label into a founder-readable
    descriptor."""
    if not role:
        return "people"
    for rx, template in _ROLE_HUMAN_MAP:
        m = rx.match(role)
        if m:
            if "{0}" in template:
                return template.format(
                    " ".join(
                        w.title() if w.lower() not in {
                            "lg", "hp", "lg styler",
                        } else w.upper()
                        for w in m.group(1).replace("_", " ").split()
                    ).strip()
                )
            return template
    return role.replace("_", " ").lower()


def _humanize_role_list(
    roles: list[str], target_customers: list[str],
) -> str:
    if not roles:
        return ", ".join(target_customers[:2]) or "the target audience"
    humanized = [humanize_role(r) for r in roles[:3]]
    return _join_with_and(humanized)


def _join_with_and(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + ", and " + items[-1]


def build_best_fit_audience(
    *,
    role_distribution: dict[str, dict[str, int]],
    target_customers: list[str],
    competitor_alternatives: list[str],
) -> dict[str, Any]:
    """Compose a founder-readable best-fit audience block."""
    rows: list[dict[str, Any]] = []
    for role, dist in role_distribution.items():
        rec = dist.get("receptive", 0)
        if rec <= 0:
            continue
        rows.append({"role": role, "receptive": rec})
    rows.sort(key=lambda r: -r["receptive"])
    top = rows[:4]

    descriptor: str
    if not top:
        descriptor = "No personas finished receptive on this run."
    else:
        primary_audience = (
            ", ".join(target_customers[:3]) or "the target audience"
        )
        secondary: list[str] = []
        # Mention competitor users / archetypes if present
        for r in top:
            label = r["role"].lower()
            if "competitor_user" in label:
                comp_name = re.sub(r"^competitor_user_", "", label)
                pretty_name = (
                    " ".join(w.title() for w in comp_name.split("_"))
                ) or "competitor"
                secondary.append(
                    f"people familiar with {pretty_name}-style "
                    "alternatives but frustrated by their format or "
                    "durability"
                )
        if not secondary and competitor_alternatives:
            comp_pretty = competitor_alternatives[0]
            secondary.append(
                f"people familiar with {comp_pretty}-style alternatives"
            )

        descriptor = (
            f"Best-fit audience: {primary_audience} who already "
            "understand the pain this product solves"
        )
        if secondary:
            descriptor += f", especially {secondary[0]}"
        descriptor += "."

    return {
        "summary_copy": descriptor,
        "rows": top,
    }


# ---------------------------------------------------------------------------
# 4. Evidence flavor
# ---------------------------------------------------------------------------


def build_evidence_flavor(
    *, retrieval_audit: dict[str, Any],
) -> dict[str, Any]:
    """Build a one-sentence evidence-flavor summary from the
    retrieval audit. No raw audit internals leak into the report."""
    providers = retrieval_audit.get("providers_attempted") or []
    yt_audit = retrieval_audit.get("youtube_audit") or {}
    accepted = int(yt_audit.get("comments_accepted") or 0)
    pulled = int(yt_audit.get("comments_pulled") or 0)
    has_yt = "youtube_data_api" in providers
    parts: list[str] = []
    if "brave_search" in providers or "tavily_search" in providers:
        parts.append("search results")
    parts.append("competitor / product pages")
    if has_yt:
        if accepted > 0:
            parts.append(
                f"buyer-language from YouTube comments "
                f"({accepted} of {pulled} passed quality filtering)"
            )
        else:
            parts.append(
                "YouTube searches (no comments passed the quality "
                "filter for this run)"
            )
    summary = "Evidence base: " + ", ".join(parts) + "."
    return {
        "summary_copy": summary,
        "providers_attempted": providers,
        "youtube_accepted_count": accepted,
        "youtube_pulled_count": pulled,
        "has_youtube": has_yt,
    }


# ---------------------------------------------------------------------------
# Helpers used by the orchestrator
# ---------------------------------------------------------------------------


def role_distribution_from_ballots(
    *,
    ballots: list[dict[str, Any]],
    role_by_pid: dict[str, str],
    intent_by_pid: dict[str, str] | None = None,
    intent_signal_by_pid: dict[str, str] | None = None,
) -> dict[str, dict[str, int]]:
    """Build a {role: {receptive, uncertain, resistant}} table from
    the calibrated ballots. Uses only `final` stage ballots so the
    report reflects the post-discussion state.

    Phase 12C.1 fix — when `intent_by_pid` is supplied, the persona's
    *inferred intent* (the load-bearing artifact at this stage of the
    pipeline) drives the bucket. Before this, the helper used only
    `private_stance`, which **does not carry the loyalty signal**:
    a persona with `loyal_to_current_alternative` intent often has
    `private_stance="curious_but_unconvinced"`, so all 5 loyal Tessera
    voters were silently rebucketed as `uncertain` and the report
    showed `resistant: 0` for every role.
    """
    from assembly.calibration.market_buckets import (
        map_assembly_intent_to_market_bucket,
        pick_market_bucket,
    )
    from assembly.sources.intent_layer.inference import (
        is_intent_signal_routing_enabled,
    )

    # Legacy stance-based map (kept as fallback when intent is missing).
    stance_bucket_map = {
        "interested_if_proven": "receptive",
        "would_buy_now": "receptive",
        "would_join_waitlist": "receptive",
        "would_consider_if_proven": "receptive",
        "curious_but_unconvinced": "uncertain",
        "needs_more_information": "uncertain",
        "skeptical": "resistant",
        "likely_reject": "resistant",
        "loyal_to_current_alternative": "resistant",
    }

    # 4-bucket calibration → 3-bucket report mapping.
    def _calibration_to_report_bucket(calibration_bucket: str) -> str:
        if calibration_bucket in ("buyer", "receptive"):
            return "receptive"
        if calibration_bucket == "skeptical":
            return "resistant"
        return "uncertain"

    intent_by_pid = intent_by_pid or {}
    intent_signal_by_pid = intent_signal_by_pid or {}
    routing_on = is_intent_signal_routing_enabled()
    role_dist: dict[str, dict[str, int]] = {}
    seen_pids: set[str] = set()
    for b in ballots:
        if (b.get("ballot_stage") or "") != "final":
            continue
        pid = str(b.get("persona_id") or "")
        if not pid or pid in seen_pids:
            continue
        seen_pids.add(pid)
        role = role_by_pid.get(pid) or "unknown"

        intent_label = intent_by_pid.get(pid)
        intent_signal = intent_signal_by_pid.get(pid)

        # Phase 12A.10D — when routing is on AND we have an
        # intent_signal, prefer it; otherwise fall back to the legacy
        # intent_label, and lastly the private_stance map.
        if intent_signal or intent_label:
            calibration_bucket, _ = pick_market_bucket(
                intent_signal=intent_signal,
                intent_label=intent_label,
                intent_signal_routing_enabled=routing_on,
            )
            bucket = _calibration_to_report_bucket(calibration_bucket)
        else:
            bucket = stance_bucket_map.get(
                b.get("private_stance") or "", "uncertain",
            )

        slot = role_dist.setdefault(
            role, {"receptive": 0, "uncertain": 0, "resistant": 0},
        )
        slot[bucket] += 1
    return role_dist
