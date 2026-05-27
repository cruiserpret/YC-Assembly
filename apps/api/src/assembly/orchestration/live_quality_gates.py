"""Phase 10A.3 — quality gates + audit wording scanner for live runs.

Two responsibilities:

1. Persona quality gates (Part D of 10A.3 spec):
   Inspect the live society right after compression, and return a
   pass/fail audit. Failure aborts persistence so the orchestrator
   never persists a weak/clone society.

2. Stale-wording scanner (Part B of 10A.3 spec):
   Walk the audit JSON files in `_audit/live_runs/{run_id}/` and
   raise if any fresh-mode artifact contains wording that belongs
   only to fixture / dev-reuse mode (e.g. ``reuse_existing_society
   mode``, ``inherited from 9B``, ``existing 9B society``,
   ``LumaLoop artifact``, ``fixture``, ``10a_1_``).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


# -----------------------------------------------------------------------
# Persona quality gates
# -----------------------------------------------------------------------


_ALLOWED_TRAIT_FIELDS = (
    "interests", "role_or_context", "buying_constraints",
    "trust_triggers", "current_alternatives",
    "communication_style", "influence_signals",
    "price_sensitivity", "objection_patterns",
    "geography_broad",
)


def evaluate_persona_quality_gates(
    *,
    compressed_candidates: list[Any],
    accepted_evidence: list[dict[str, Any]],
    target_brief_id: str,
    run_scope_id: str,
    min_count: int = 21,
    max_count: int = 30,
    max_role_concentration: float = 0.35,
    min_distinct_roles: int = 5,
    min_provider_diversity: int = 1,
    # Objection diversity is informational only by default. The
    # widener emits `inferred_objections` only when the upstream
    # signal extractor surfaced an `objection_pattern`; for some
    # product categories the retrieval pool simply doesn't carry
    # those, and that's an upstream coverage issue, not a
    # society-quality one. `trait_dimension_diversity` carries the
    # real "is this society diverse?" check.
    min_objection_diversity: int = 0,
    min_trait_dimension_diversity: int = 2,
    # Phase 10B.3+ hotfix: bumped from 0.50 → 0.60. The previous cap
    # was tight enough that briefs whose competitors have rich review
    # corpora (well-known appliances, popular SaaS, etc.) routinely
    # tipped just over the line — even after the canonical-slug
    # dedup. Competitor-derived personas are still capped well below
    # dominance, but the gate no longer blocks legitimate runs.
    max_competitor_user_share: float = 0.6,
    target_product_name: str | None = None,
) -> dict[str, Any]:
    """Run all persona quality gates against the compressed candidate
    set. Returns an audit dict with one boolean per gate plus a top-
    level ``all_gates_passed``.

    The orchestrator inspects ``all_gates_passed``: if False, the
    stage raises StageError before persisting weak personas.
    """
    n = len(compressed_candidates)
    role_counter: Counter = Counter()
    provider_counter: Counter = Counter()
    objection_counter: Counter = Counter()
    trait_dimension_counter: Counter = Counter()
    role_signature_seen: set[str] = set()
    duplicate_signatures: list[str] = []
    candidates_without_evidence: list[str] = []
    candidates_with_too_few_traits: list[str] = []
    candidates_with_invalid_role: list[str] = []
    fake_use_phrases: list[str] = []

    # Fake-use detector: catches the persona (or LLM-generated reaction)
    # CLAIMING to have bought/used/owned the target product. Scopes to
    # the product name when available so generic "I used X" in real
    # competitor reviews doesn't false-positive.
    if target_product_name:
        fake_use_re = re.compile(
            r"\b(i|we|as\s+(?:a|an))\s+(?:bought|own|use|used|tried|"
            r"tested|reviewed|purchased)\s+(?:the\s+|a\s+|an\s+|my\s+)?"
            + re.escape(target_product_name.lower()),
            re.IGNORECASE,
        )
    else:
        fake_use_re = re.compile(
            r"\b(i|we)\s+(?:bought|own|use|used|tried|tested|reviewed|"
            r"purchased)\s+(?:the\s+|a\s+|an\s+|my\s+)?(?:target\s+|"
            r"this\s+)?(?:product|item|brand)\b",
            re.IGNORECASE,
        )

    for c in compressed_candidates:
        cid = getattr(c, "candidate_id", None) or "unknown"
        role = (
            getattr(c, "normalized_primary_role", None)
            or getattr(c, "pre_normalization_role", None)
            or "unknown"
        )
        role_counter[role] += 1
        provider = getattr(c, "source_provider_family", "unknown") or "unknown"
        provider_counter[provider] += 1
        # Evidence-link coverage: every persona must reference at least
        # one source_record_id from the accepted-evidence pool.
        src_ids = list(getattr(c, "source_record_ids", []) or [])
        if not src_ids or src_ids == ["unknown"]:
            candidates_without_evidence.append(cid)
        # Trait coverage
        traits = list(getattr(c, "inferred_traits", []) or [])
        valid_traits = [
            t for t in traits
            if (t.get("trait_value") if isinstance(t, dict)
                else getattr(t, "trait_value", None))
        ]
        if len(valid_traits) < 2:
            candidates_with_too_few_traits.append(cid)
        # Trait dimension diversity — count distinct trait_name values
        # across all candidates (excluding the catch-all role_or_context
        # fallback). High distinct count = personas anchored to varied
        # signal types; low count = a clone society.
        for t in valid_traits:
            tname = (
                t.get("trait_name") if isinstance(t, dict)
                else getattr(t, "trait_name", None)
            ) or ""
            if tname and tname != "role_or_context":
                trait_dimension_counter[tname] += 1
        if role not in (
            "unknown",
        ):
            # role must be a non-empty, brief-scoped slug
            if not isinstance(role, str) or len(role) > 80:
                candidates_with_invalid_role.append(cid)
        # Duplicate detection: signature = role + first 80 chars of
        # the first evidence snippet (lowercase)
        snippets = list(getattr(c, "evidence_snippets", []) or [])
        snip0 = (snippets[0] if snippets else "")[:80].lower()
        sig = f"{role}::{snip0}"
        if sig in role_signature_seen and snip0:
            duplicate_signatures.append(sig)
        else:
            role_signature_seen.add(sig)
        # Objection diversity — count distinct objection buckets across
        # candidates. The widener may emit zero or one objection per
        # candidate; what we care about is whether the *population*
        # surfaces multiple distinct buckets.
        for o in (getattr(c, "inferred_objections", []) or []):
            text = o if isinstance(o, str) else getattr(o, "text", "") or ""
            bucket = text.strip().lower()[:40] or "unknown"
            objection_counter[bucket] += 1
        # Fake-use detection: scan ONLY LLM-generated text fields
        # (reaction + summary). Do NOT scan evidence_snippets — those
        # are real reviewer text where "I bought X" is legitimate
        # evidence, not a claim by the synthetic persona.
        scan_blob = " ".join([
            getattr(c, "hypothetical_target_product_reaction", "") or "",
            getattr(c, "evidence_summary", "") or "",
        ])
        if fake_use_re.search(scan_blob):
            fake_use_phrases.append(cid)

    # Gate: count
    count_in_range = (n >= min_count) and (n <= max_count)
    # Gate: role concentration
    if n > 0:
        max_role_count = max(role_counter.values())
        role_concentration = max_role_count / n
    else:
        role_concentration = 1.0
    role_concentration_ok = role_concentration <= max_role_concentration
    # Gate: distinct role count
    distinct_roles = len([r for r, _ in role_counter.most_common()])
    distinct_roles_ok = distinct_roles >= min(
        min_distinct_roles, max(1, n // 4),
    )
    # Gate: provider diversity (at least 1 in single-provider runs;
    # encourage 2+ when feasible)
    provider_diversity = len(provider_counter)
    provider_diversity_ok = provider_diversity >= min_provider_diversity
    # Gate: objection diversity (≥ N distinct objection buckets across
    # the population)
    objection_diversity_ok = (
        len(objection_counter) >= min_objection_diversity
    )
    # Gate: trait-dimension diversity. Replaces the old proof_diversity
    # gate, which structurally couldn't pass because the widener never
    # populates `inferred_preferences`. This new gate measures whether
    # the population surfaces multiple distinct trait dimensions
    # (signal-type derived).
    trait_dimension_diversity_ok = (
        len(trait_dimension_counter)
        >= min_trait_dimension_diversity
    )
    # Gate: competitor-user dominance
    comp_count = sum(
        v for k, v in role_counter.items()
        if k.startswith("competitor_user")
    )
    comp_share = comp_count / max(n, 1)
    competitor_user_ok = comp_share <= max_competitor_user_share
    # Gate: no global personas — every persona must be brief-scoped
    # (run_scope_id present in the audit dict; we don't verify per
    # persona here because the persistence step adds the tag).
    not_global_ok = bool(run_scope_id) and bool(target_brief_id)
    # Gate: no exact duplicates
    no_duplicates_ok = len(duplicate_signatures) == 0
    # Gate: every persona has evidence link
    evidence_link_ok = len(candidates_without_evidence) == 0
    # Gate: every persona has ≥2 traits (or justified fallback)
    traits_ok = len(candidates_with_too_few_traits) == 0
    # Gate: no invalid roles
    role_format_ok = len(candidates_with_invalid_role) == 0
    # Gate: no fake product users
    no_fake_use_ok = len(fake_use_phrases) == 0

    gates = {
        "count_in_range": count_in_range,
        "role_concentration_ok": role_concentration_ok,
        "distinct_roles_ok": distinct_roles_ok,
        "provider_diversity_ok": provider_diversity_ok,
        "objection_diversity_ok": objection_diversity_ok,
        "trait_dimension_diversity_ok": trait_dimension_diversity_ok,
        "competitor_user_share_ok": competitor_user_ok,
        "not_global_personas_ok": not_global_ok,
        "no_duplicates_ok": no_duplicates_ok,
        "evidence_link_coverage_ok": evidence_link_ok,
        "min_traits_per_persona_ok": traits_ok,
        "role_format_ok": role_format_ok,
        "no_fake_product_users_ok": no_fake_use_ok,
    }
    all_passed = all(gates.values())
    blocker_messages: list[str] = []
    if not count_in_range:
        blocker_messages.append(
            f"compressed_count={n} not in [{min_count}, {max_count}]"
        )
    if not role_concentration_ok:
        blocker_messages.append(
            f"role_concentration={role_concentration:.2f} > "
            f"{max_role_concentration:.2f}"
        )
    if not distinct_roles_ok:
        blocker_messages.append(
            f"distinct_roles={distinct_roles} below required minimum"
        )
    if not provider_diversity_ok:
        blocker_messages.append(
            f"provider_diversity={provider_diversity} below "
            f"{min_provider_diversity}"
        )
    if not objection_diversity_ok:
        blocker_messages.append(
            f"objection_diversity={len(objection_counter)} below "
            f"{min_objection_diversity}"
        )
    if not trait_dimension_diversity_ok:
        blocker_messages.append(
            f"trait_dimension_diversity={len(trait_dimension_counter)} "
            f"below {min_trait_dimension_diversity}"
        )
    if not competitor_user_ok:
        blocker_messages.append(
            f"competitor_user_share={comp_share:.2f} > "
            f"{max_competitor_user_share:.2f}"
        )
    if not no_duplicates_ok:
        blocker_messages.append(
            f"{len(duplicate_signatures)} duplicate persona "
            "signatures detected"
        )
    if not evidence_link_ok:
        blocker_messages.append(
            f"{len(candidates_without_evidence)} personas have no "
            "source evidence link"
        )
    if not traits_ok:
        blocker_messages.append(
            f"{len(candidates_with_too_few_traits)} personas have "
            "fewer than 2 traits"
        )
    if not role_format_ok:
        blocker_messages.append(
            f"{len(candidates_with_invalid_role)} personas have "
            "invalid role labels"
        )
    if not no_fake_use_ok:
        blocker_messages.append(
            f"{len(fake_use_phrases)} personas contain fake-use "
            "language (claim of having bought / used the product)"
        )

    return {
        "phase": "10a_3_persona_quality_gates",
        "compressed_count": n,
        "min_count": min_count,
        "max_count": max_count,
        "gate_results": gates,
        "all_gates_passed": all_passed,
        "blocker_messages": blocker_messages,
        "role_distribution": dict(role_counter),
        "max_role_concentration_observed": round(role_concentration, 3),
        "provider_distribution": dict(provider_counter),
        "objection_bucket_count": len(objection_counter),
        "trait_dimension_bucket_count": len(trait_dimension_counter),
        "trait_dimension_distribution": dict(trait_dimension_counter),
        "competitor_user_share": round(comp_share, 3),
        "duplicate_signatures": duplicate_signatures[:10],
        "candidates_without_evidence": candidates_without_evidence[:10],
        "candidates_with_too_few_traits": candidates_with_too_few_traits[:10],
        "fake_use_candidate_ids": fake_use_phrases[:10],
        "run_scope_id": run_scope_id,
        "target_brief_id": target_brief_id,
    }


# -----------------------------------------------------------------------
# Stale wording scanner
# -----------------------------------------------------------------------


# Patterns that must NOT appear in fresh-live-mode artifacts. These are
# either old phase tags or wording that only applies to dev-reuse /
# fixture mode.
_FRESH_LIVE_FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"reuse_existing_society\s*mode", "stale dev-reuse wording"),
    (r"inherited\s+from\s+9B", "stale 9B inheritance wording"),
    (r"existing\s+9B\s+society", "stale 9B society reference"),
    (r"\bfixture\b", "fixture-mode reference in fresh artifact"),
    (r"LumaLoop\s+artifact", "stale LumaLoop reference"),
    (r"phase[\"':\s]+10a[_\.]1[_a-z]*", "stale 10A.1 phase tag"),
    (
        r"\"phase\"\s*:\s*\"10a_1_",
        "stale 10A.1 JSON phase value",
    ),
    (r"\(dev_reuse\)", "stale dev_reuse marker"),
    (
        r"existing\s+9B\s+pre-ballots",
        "stale 9B pre-ballot reference",
    ),
    (r"9B\.1-repaired\s+society", "stale 9B.1 repair reference"),
)


# Files that are *expected* to contain the dev-reuse wording — they
# only run in dev-reuse mode and the wording is correct there.
_DEV_REUSE_ONLY_ARTIFACTS = (
    # (none — fresh mode rewrites all artifacts under 10A.3)
)


# Files we never scan because they aren't generated by the
# orchestrator (user input, cost pre-estimate, the audit's own
# output, etc.).
_SCANNER_EXCLUDED_FILES = frozenset((
    "live_founder_brief_input.json",  # user's brief — out of scope
    "cost_estimate.json",  # pre-computed before the brief is read
    "fresh_live_artifact_wording_audit.json",  # the audit's own file
    "user_facing_language_audit.json",  # may contain pattern names
))


def scan_fresh_live_artifacts_for_stale_wording(
    *,
    run_dir: Path,
    is_dev_reuse: bool = False,
) -> dict[str, Any]:
    """Scan every JSON + markdown file in ``run_dir`` for forbidden
    fresh-mode wording. Returns an audit dict with per-file findings.

    If ``is_dev_reuse=True``, skips the scan entirely (dev-reuse mode
    is allowed to use the legacy wording)."""
    if is_dev_reuse:
        return {
            "phase": "10a_3_fresh_live_artifact_wording_audit",
            "skipped": True,
            "skip_reason": "is_dev_reuse=True; legacy wording is allowed",
            "violation_count": 0,
            "any_violations": False,
            "violations_by_file": {},
        }
    findings: dict[str, list[dict[str, Any]]] = {}
    if not run_dir.exists():
        return {
            "phase": "10a_3_fresh_live_artifact_wording_audit",
            "skipped": True,
            "skip_reason": f"run_dir does not exist: {run_dir}",
            "violation_count": 0,
            "any_violations": False,
            "violations_by_file": {},
        }
    for path in sorted(run_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name in _DEV_REUSE_ONLY_ARTIFACTS:
            continue
        if path.name in _SCANNER_EXCLUDED_FILES:
            continue
        if path.suffix.lower() not in (".json", ".md", ".txt"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        per_file: list[dict[str, Any]] = []
        for pattern, label in _FRESH_LIVE_FORBIDDEN_PATTERNS:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                per_file.append({
                    "pattern": pattern,
                    "label": label,
                    "match": m.group(0)[:120],
                    "offset": m.start(),
                })
        if per_file:
            findings[path.name] = per_file
    violation_count = sum(len(v) for v in findings.values())
    return {
        "phase": "10a_3_fresh_live_artifact_wording_audit",
        "skipped": False,
        "violation_count": violation_count,
        "any_violations": violation_count > 0,
        "violations_by_file": findings,
        "files_scanned": [
            p.name for p in sorted(run_dir.iterdir())
            if p.is_file()
            and p.suffix.lower() in (".json", ".md", ".txt")
            and p.name not in _SCANNER_EXCLUDED_FILES
        ],
        "files_excluded": list(_SCANNER_EXCLUDED_FILES),
    }


def write_persona_quality_gates_artifact(
    *, run_dir: Path, audit: dict[str, Any],
) -> Path:
    out = run_dir / "persona_quality_gates.json"
    out.write_text(
        json.dumps(audit, indent=2, default=str), encoding="utf-8",
    )
    return out


def write_wording_audit_artifact(
    *, run_dir: Path, audit: dict[str, Any],
) -> Path:
    out = run_dir / "fresh_live_artifact_wording_audit.json"
    out.write_text(
        json.dumps(audit, indent=2, default=str), encoding="utf-8",
    )
    return out


# -----------------------------------------------------------------------
# User-facing language scanner (Part G)
# -----------------------------------------------------------------------


_USER_FACING_FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        r"\b\d{1,3}\s*%\s+of\s+(?:the\s+)?(?:market|customers|users)\s+will\b",
        "market-percentage forecast",
    ),
    (r"\bthe\s+market\s+will\s+adopt\b", "market adoption verdict"),
    (r"\blaunch\s+this\b", "launch verdict"),
    (r"\bkill\s+this\b", "kill verdict"),
    (r"\bguaranteed\s+demand\b", "guaranteed demand"),
    (
        r"\b(?:customers|buyers|users)\s+(?:used|tried|bought|reviewed)\s+this\s+product\b",
        "fake product usage claim",
    ),
    (r"\breal\s+buyers\s+said\b", "fake real-buyer attribution"),
    (
        r"\b(?:guaranteed|certain)\s+(?:to\s+)?(?:succeed|win|fail)\b",
        "outcome guarantee",
    ),
)


def scan_user_facing_language(text: str) -> dict[str, Any]:
    """Scan user-facing report text for forbidden language. Returns
    an audit dict with findings + a boolean ``any_violations``."""
    findings: list[dict[str, Any]] = []
    for pattern, label in _USER_FACING_FORBIDDEN_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            findings.append({
                "pattern": pattern,
                "label": label,
                "match": m.group(0)[:120],
                "offset": m.start(),
            })
    return {
        "phase": "10a_3_user_facing_language_audit",
        "violation_count": len(findings),
        "any_violations": bool(findings),
        "findings": findings,
    }


# -----------------------------------------------------------------------
# Scoped user-facing language scan (Part G — refinement)
#
# The plain `scan_user_facing_language(text)` above is correct for the
# *LLM-generated summary* parts of the founder report (executive
# summary, recommendations, takeaways, caveats — the prose the
# report-writer LLM produced). It is OVER-AGGRESSIVE when run against
# the whole report payload, because the payload also embeds verbatim
# persona-voice text: full debate transcript turns,
# persona_reasoning_cards.top_objection.text, representative_debates,
# etc. A synthetic persona saying "I'd kill this if my team built it"
# is *evidence* of skepticism, not the report writer issuing a kill
# verdict, but the regex matches both.
#
# `scan_main_report_summary_language(report)` walks the report dict
# and scans ONLY non-persona-voice subtrees. Persona-voice subtrees
# are quotes that founders should see verbatim (they're the
# transcript), and they're already independently sanitized for
# fake-product-use claims by forbidden_claim_audit in
# discussion_layer/validators.py.
# -----------------------------------------------------------------------


# Top-level keys in main_report whose subtree contains verbatim
# persona-voice text. Excluded from the user-facing language scan.
_PERSONA_VOICE_REPORT_KEYS: frozenset[str] = frozenset({
    # Phase 14A — full debate transcript embedded directly in
    # main_report. Every turn is a verbatim persona utterance and
    # can legitimately contain "kill this" / "burn this" / etc. as
    # blunt feedback. Without this exclusion the scan blocks any
    # competitor-heavy brief where personas naturally use that
    # language.
    "full_debate",
    # Phase 12F.1 — per-persona reasoning cards. The `.text`
    # subfields under top_objection / top_proof_need /
    # adoption_trigger come straight from final-ballot persona
    # output. Same persona-voice argument as full_debate.
    "persona_reasoning_cards",
    # Representative paraphrased debate samples (Phase 12C). Same
    # argument.
    "representative_debates",
    # Spread / resisted argument lists capture persona-voice claims
    # made during debate.
    "arguments_that_spread",
    "arguments_that_were_resisted",
})


def _collect_summary_text(node: Any, path: str = "") -> list[str]:
    """Walk a report node, returning every string leaf whose path
    does NOT cross a persona-voice subtree key. The result is the
    LLM-summary text the user-facing language scan should be
    enforced against."""
    out: list[str] = []
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, list):
        for item in node:
            out.extend(_collect_summary_text(item, path))
    elif isinstance(node, dict):
        for k, v in node.items():
            if k in _PERSONA_VOICE_REPORT_KEYS:
                # Skip the entire subtree — persona-voice content.
                continue
            out.extend(_collect_summary_text(v, f"{path}.{k}" if path else k))
    return out


def scan_main_report_summary_language(
    report: dict[str, Any],
) -> dict[str, Any]:
    """User-facing language scan scoped to LLM-summary subtrees of
    the founder report. Skips persona-voice subtrees (full_debate,
    persona_reasoning_cards, representative_debates, etc.) — those
    are verbatim quotes, not the report writer's verdicts.

    Returns the same audit shape as `scan_user_facing_language(text)`,
    plus `scope_excluded_keys` so the audit trail makes the scoping
    explicit.
    """
    summary_blob = "\n".join(_collect_summary_text(report))
    base = scan_user_facing_language(summary_blob)
    base["scope_excluded_keys"] = sorted(_PERSONA_VOICE_REPORT_KEYS)
    base["scope"] = (
        "llm_summary_only — persona-voice subtrees (debate turns, "
        "persona reasoning cards, representative debates) excluded "
        "because verbatim persona quotes can legitimately contain "
        "blunt rejection language and are evidence, not verdicts"
    )
    return base
