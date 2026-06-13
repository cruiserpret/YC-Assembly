"""Phase 17B-L — strict baseline prompt builder for the AMFB-v1 benchmark.

Renders the SINGLE prompt every plain-LLM baseline (GPT / Claude / Gemini) receives
for a frozen input bundle. It gives the model ONLY the shared frozen bundle, asks for
the canonical four-bucket AMFB-v1 prediction, forbids outside knowledge / web / current
campaign status, and requires an explicit ``schema_failure`` if the model cannot comply.

Fairness invariants (enforced by construction + tested):
- the prompt is rendered ONLY from the model-facing bundle fields — it can NOT contain
  Assembly's prediction, the realized outcome, the outcome date's result, the Hollowed
  Oath case, or any benchmark commentary that would bias the model.
- the same prompt text is used for every provider, so the comparison is apples-to-apples.

Pure string building: no LLM, no network, no SDK, no provider call. Importing this
module loads nothing heavy.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

# The exact JSON object every baseline must emit (the AMFB-v1 prediction schema).
AMFB_OUTPUT_CONTRACT = """\
Return ONE JSON object and nothing else, with EXACTLY these keys:
{
  "buyer_action_positive": <number 0-100>,   // % who would take the buying/backing action now
  "receptive": <number 0-100>,               // % open/interested but not yet acting
  "uncertain_proof_needed": <number 0-100>,  // % who need more proof before deciding
  "skeptical_resistant": <number 0-100>,     // % skeptical or resistant
  "confidence": <number 0-1>,                // your calibrated self-confidence
  "top_adoption_reasons": [<string>, ...],
  "top_rejection_reasons": [<string>, ...],
  "one_thing_needed": <string>,              // the single thing that would most move the market
  "recommended_segment": <string>,           // the segment most likely to act
  "expected_action_signal": <string>,        // the concrete action you'd expect (e.g. kickstarter_pledge)
  "forecast_notes": <string>
}
The four bucket percentages MUST sum to ~100 (±1.5). If — and only if — you genuinely
cannot produce calibrated four-bucket proportions from the bundle alone, return instead:
{ "schema_failure": true, "schema_failure_reason": <string>, "confidence": <number 0-1> }
(do NOT include the four buckets in a schema_failure response)."""

_RULES = """\
RULES (read carefully):
- Use ONLY the information in the INPUT BUNDLE below. Do NOT use outside knowledge,
  memory of this specific campaign, the web, or any current/in-progress campaign status.
- Do NOT estimate or infer the current funding amount, backer count, or final outcome.
  Those are deliberately excluded and must not influence your answer.
- Predict the market's reaction to the product ON ITS MERITS, as of the bundle's frozen
  date. Do not assume the campaign succeeds or fails.
- Output ONLY the JSON object specified below — no prose before or after."""

# Model-facing bundle fields, in render order. Anything NOT in this list is never shown
# to the model (so audit/provenance fields can never leak into the prompt).
_RENDER_FIELDS: tuple[str, ...] = (
    "product_name",
    "product_description",
    "target_customers",
    "price_or_price_structure",
    "competitors_or_alternatives",
    "launch_geography",
    "launch_state",
    "campaign_context",
    "campaign_close_date",
    "constraints",
)

# Tokens that would NEVER legitimately appear in a clean market-forecast prompt and so
# are reliable structural leak anchors. (Bare English words like "outcome"/"observed"
# are deliberately NOT here — they appear in the prompt's own anti-leakage instructions;
# the value-leak path below catches Assembly's actual numbers/hash.)
FORBIDDEN_PROMPT_KEYS: tuple[str, ...] = (
    "predicted_proportions",
    "assembly_prediction",
    "assembly",        # no method should be told it is being benchmarked by Assembly
    "phase 16a",       # internal orchestration framing must never reach a model
    "hollowed_oath",
    "hollowed oath",
)


def _render_value(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return "\n".join(f"  - {v}" for v in value)
    return str(value)


def render_bundle_for_prompt(input_bundle: dict) -> str:
    """Render ONLY the whitelisted model-facing fields of the bundle as plain text."""
    lines: list[str] = []
    for key in _RENDER_FIELDS:
        if key not in input_bundle or input_bundle[key] in (None, "", [], {}):
            continue
        label = key.replace("_", " ").upper()
        lines.append(f"{label}:\n{_render_value(input_bundle[key])}")
    return "\n\n".join(lines)


def build_baseline_prompt(input_bundle: dict) -> str:
    """The full, single baseline prompt for one frozen input bundle.

    Rendered ONLY from the whitelisted model-facing fields — it cannot contain Assembly's
    prediction, the outcome, or any other audit field even if such a field were present in
    the bundle dict.
    """
    case_id = input_bundle.get("benchmark_case_id", "<unknown_case>")
    body = render_bundle_for_prompt(input_bundle)
    return (
        "You are forecasting how a market will react to a product. You will be given a "
        "frozen product brief and must output a calibrated four-bucket market-reaction "
        "prediction.\n\n"
        f"{_RULES}\n\n"
        f"INPUT BUNDLE (case_id={case_id}; frozen, pre-outcome):\n"
        "----------------------------------------\n"
        f"{body}\n"
        "----------------------------------------\n\n"
        f"{AMFB_OUTPUT_CONTRACT}\n"
    )


def assert_prompt_is_clean(prompt: str, *, forbidden_values: Sequence[str] = ()) -> list[str]:
    """Return a list of leakage reasons (empty == clean). Catches the structural
    forbidden keys plus any explicitly-supplied forbidden values (e.g. the digits of
    Assembly's predicted proportions). Used by the preflight + tests."""
    issues: list[str] = []
    low = prompt.lower()
    for k in FORBIDDEN_PROMPT_KEYS:
        if k in low:
            issues.append(f"prompt contains forbidden key/token {k!r}")
    for v in forbidden_values:
        if v and str(v).lower() in low:
            issues.append("prompt contains a forbidden value (possible Assembly-prediction/outcome leak)")
    return issues


def prompt_hash(prompt: str) -> str:
    """Stable digest of the exact prompt text (so the preflight can display/commit it)."""
    return "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def prompt_artifact(input_bundle: dict) -> str:
    """A human-readable Markdown artifact: the prompt + its hash (for inspection/commit)."""
    p = build_baseline_prompt(input_bundle)
    return (
        f"# AMFB-v1 baseline prompt — {input_bundle.get('benchmark_case_id', '')}\n\n"
        f"`prompt_hash = {prompt_hash(p)}`\n\n"
        "The identical prompt below is sent to every plain-LLM baseline (GPT / Claude / "
        "Gemini). It reveals no Assembly prediction and no outcome.\n\n"
        "```\n"
        f"{p}"
        "```\n"
    )


# Re-exported so the preflight can serialize the contract verbatim if needed.
def output_contract_json_example() -> str:
    return json.dumps(
        {
            "buyer_action_positive": 0,
            "receptive": 0,
            "uncertain_proof_needed": 0,
            "skeptical_resistant": 0,
            "confidence": 0.0,
            "top_adoption_reasons": [],
            "top_rejection_reasons": [],
            "one_thing_needed": "",
            "recommended_segment": "",
            "expected_action_signal": "",
            "forecast_notes": "",
        },
        indent=2,
    )
