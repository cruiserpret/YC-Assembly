"""Phase 10B.1 / 10B.2 — Product Fact Lock (a.k.a. Product Fact Card).

Generates a per-run, author-of-record fact card from the founder
brief and renders it as a prompt block injected into every
discussion-stage LLM call. The fact card is treated as the
HIGHEST-AUTHORITY source for product facts during simulation —
agents may push back on the truth of those facts, but they may
not contradict them or pretend they were never given.

Phase 10B.2 additions (Product Fact Lock + Price Hierarchy):
  * primary_price (the main product/kit price) is distinguished
    from accessory_prices (replacement / refill / consumable)
  * kit_contents (what's inside the primary purchase)
  * power_facts / charging_facts (parsed from description +
    optional context)
  * included_features (what the product DOES)
  * excluded_features (what it explicitly does NOT — heat / steam /
    UV / etc. — these are the most-commonly-confused product
    boundaries)

Universal — no hardcoded product names. Each field is optional;
fields not derivable from the brief are simply omitted instead
of being hallucinated.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AccessoryPrice:
    """A non-primary price line item — refill / replacement /
    consumable / add-on. The `label` is short ("Replacement filter
    pack") so the prompt block can render it next to the amount."""
    label: str
    amount: str


@dataclass(frozen=True)
class ProductFactCard:
    """Author-of-record product facts. Every field is optional —
    omit anything not derivable from the brief instead of
    hallucinating it."""
    product_name: str
    product_type: str | None = None
    not_categories: list[str] = field(default_factory=list)
    # 10B.1 single-price field — kept for backward-compat. Same
    # value as `primary_price` in 10B.2+.
    price_or_price_structure: str | None = None
    # 10B.2 — split out for the Price Hierarchy validator.
    primary_price: str | None = None
    # 10B.3 — distinguish bundle / multi-pack price from primary +
    # accessory. Bundles are still primary product, just at a
    # multi-unit discount.
    bundle_price: str | None = None
    accessory_prices: list[AccessoryPrice] = field(default_factory=list)
    kit_contents: list[str] = field(default_factory=list)
    # 10B.2 — power / charging / excluded features.
    power_facts: list[str] = field(default_factory=list)
    charging_facts: list[str] = field(default_factory=list)
    included_features: list[str] = field(default_factory=list)
    excluded_features: list[str] = field(default_factory=list)
    # 10B.3 — performance / cleaning / material facts. These are
    # the most-commonly-misremembered fields in the GlowPlate run
    # and the hardest to fix without a separate lock surface.
    materials: list[str] = field(default_factory=list)
    temperature_facts: list[str] = field(default_factory=list)
    runtime_facts: list[str] = field(default_factory=list)
    cleaning_facts: list[str] = field(default_factory=list)
    # Phase 10B.4 — Negation-Scope Fact Lock. Each flag is a
    # *positive* fact about the product, separable from privacy /
    # safety / capability negations. The PantryPulse case showed
    # that "does not record video" was getting collapsed into "no
    # camera" by personas — so the lock now stores camera-existence
    # as a separate signal from camera-behavior.
    sensing_facts: dict[str, bool] = field(default_factory=dict)
    # Each sensing-fact entry has a small description list so the
    # prompt can render "Has camera (tiny wide-angle, captures still
    # shelf/label images)" without losing the qualifier.
    sensing_fact_details: dict[str, list[str]] = field(default_factory=dict)
    # Input-mechanism flags (barcode / NFC / RFID / app entry /
    # voice / etc.). Same shape as sensing_facts.
    input_mechanism_facts: dict[str, bool] = field(default_factory=dict)
    input_mechanism_details: dict[str, list[str]] = field(default_factory=dict)
    # Phase 10B.6 — generic explicit-negative-feature lock. Pulled
    # from "does not have X / does not use X / does not record X /
    # is not a X / no X" patterns in the brief. The validator uses
    # these to flag any agent text that mentions a forbidden
    # feature as if it existed. Universal — extracted dynamically
    # for every product.
    forbidden_features: list[Any] = field(default_factory=list)
    launch_geography: str | None = None
    launch_state: str | None = None
    target_customers: list[str] = field(default_factory=list)
    competitors_or_alternatives: list[str] = field(default_factory=list)
    optional_context: str | None = None
    constraints: list[str] = field(default_factory=list)


# --- "Not"-category hints --------------------------------------------------

_NOT_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "shoe-drying",
        ("a shoe", "an insole", "a sock", "footwear",
         "a generic boot warmer", "a shoe organizer"),
    ),
    (
        "shoe drying",
        ("a shoe", "an insole", "a sock", "footwear",
         "a generic boot warmer", "a shoe organizer"),
    ),
    (
        "boot dryer",
        ("a shoe", "an insole", "a sock", "footwear",
         "a shoe organizer"),
    ),
    (
        "rear light",
        ("a bike", "a helmet", "a brake light"),
    ),
    (
        "hydration reminder",
        ("a water bottle", "a hydration tracker app",
         "a smart water bottle by itself"),
    ),
    (
        "shoe sanitizer",
        ("a shoe", "footwear", "a generic UV lamp"),
    ),
    (
        "garment-refresh",
        ("a washing machine", "a dryer", "a steamer",
         "a dry-cleaning replacement"),
    ),
    (
        "garment refresh",
        ("a washing machine", "a dryer", "a steamer",
         "a dry-cleaning replacement"),
    ),
    (
        "moisture-control hanger",
        ("a washing machine", "a dryer", "a clothes steamer",
         "a dry-cleaning replacement"),
    ),
)


def _derive_not_categories(
    product_type: str | None, product_description: str
) -> list[str]:
    blob = " ".join(
        [(product_type or "").lower(), (product_description or "").lower()]
    )
    out: list[str] = []
    seen: set[str] = set()
    for anchor, hints in _NOT_HINTS:
        if anchor in blob:
            for h in hints:
                if h not in seen:
                    out.append(h)
                    seen.add(h)
    # Founder may already include explicit "not" phrasing in the
    # description — pick those up too.
    desc_low = (product_description or "").lower()
    for explicit_pattern, label in (
        (r"\bnot a (washing machine)", "a washing machine"),
        (r"\bnot a (dryer)\b", "a dryer"),
        (r"\bnot a (steamer)\b", "a steamer"),
        (r"\bnot a (dry[\- ]cleaning replacement)", "a dry-cleaning replacement"),
        (r"\bnot a (shoe)\b", "a shoe"),
        (r"\bnot an? (insole)\b", "an insole"),
    ):
        if re.search(explicit_pattern, desc_low) and label not in seen:
            out.append(label)
            seen.add(label)
    return out


# --- Price parsing ---------------------------------------------------------

_DOLLAR_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{1,2})?")
_ACCESSORY_KEYWORDS = (
    "replacement", "replace", "refill", "consumable", "consumables",
    "filter pack", "pod pair", "accessory", "accessories", "add-on",
    "addon", "spare", "extra", "subscription",
)
_PRIMARY_KEYWORDS = (
    "starter kit", "starter", "main", "primary", "kit", "system",
    "dock", "station", "for the",
)
# Phase 10B.3 — multi-unit bundle keywords. A bundle is still the
# primary product, just sold as a multi-pack at a discount.
_BUNDLE_KEYWORDS = (
    "two-plate bundle", "two plate bundle", "two-pack", "two pack",
    "twin pack", "twin-pack", "double pack", "double-pack",
    "bundle", "multi-pack", "multi pack", "family pack",
    "set of two", "set of 2", "set of three", "set of 3",
)


_BUNDLE_PRECEDED_RE = re.compile(
    r"(?:two[\- ]plate\s+bundle|two[\- ]pack|twin[\- ]pack|"
    r"double[\- ]pack|multi[\- ]pack|family\s+pack|"
    r"set\s+of\s+(?:two|2|three|3)|bundle)"
    r"[\s:,;\-—–]+\$\s?(\d[\d,]*(?:\.\d{1,2})?)",
    re.IGNORECASE,
)


def _parse_prices(
    price_str: str | None,
) -> tuple[str | None, str | None, list[AccessoryPrice]]:
    """Best-effort split of a free-form price field into a primary
    price + optional bundle price + zero-or-more accessory prices.

    Strategy:
      1. Pull out bundle prices first via a position-aware regex
         that requires the bundle keyword to *precede* the dollar
         amount (e.g. "two-plate bundle: $139"). This avoids the
         wide-window false positive where a bundle keyword sitting
         after the primary price hijacks it.
      2. For the remaining amounts, walk left-to-right and use the
         tighter ±40-char window to attribute primary / accessory.
      3. The first non-bundle, non-accessory amount wins as
         `primary_price`.
    """
    if not price_str:
        return None, None, []
    text = price_str
    norm = re.sub(r"\s*\n+\s*", ". ", text).strip()

    bundle: str | None = None
    bundle_spans: list[tuple[int, int]] = []
    for m in _BUNDLE_PRECEDED_RE.finditer(norm):
        if bundle is None:
            bundle = "$" + m.group(1)
        bundle_spans.append((m.start(1) - 1, m.end()))

    primary: str | None = None
    accessories: list[AccessoryPrice] = []

    def _in_bundle_span(pos: int) -> bool:
        return any(s <= pos < e for s, e in bundle_spans)

    for m in _DOLLAR_RE.finditer(norm):
        if _in_bundle_span(m.start()):
            continue  # already attributed as bundle
        amount = m.group(0).replace(" ", "")
        start = max(0, m.start() - 40)
        end = min(len(norm), m.end() + 40)
        ctx = norm[start:end].lower()
        is_accessory = any(k in ctx for k in _ACCESSORY_KEYWORDS)
        is_primary = any(k in ctx for k in _PRIMARY_KEYWORDS)

        if is_accessory and not is_primary:
            label = _derive_accessory_label(ctx, amount)
            accessories.append(AccessoryPrice(label=label, amount=amount))
            continue

        if primary is None:
            primary = amount
            continue

        primary_value = _amount_to_float(primary)
        new_value = _amount_to_float(amount)
        if (
            primary_value is not None
            and new_value is not None
            and new_value < primary_value
        ):
            label = _derive_accessory_label(ctx, amount)
            accessories.append(AccessoryPrice(label=label, amount=amount))
        elif bundle is None and new_value is not None:
            bundle = amount
        else:
            primary = f"{primary} / {amount}"

    return primary, bundle, accessories


def _derive_accessory_label(ctx: str, amount: str) -> str:
    """Pick a readable label for an accessory line item by looking
    at the words around the dollar amount. Falls back to
    "Replacement / refill" when no specific noun is found."""
    # Find a short noun phrase before/after the dollar amount
    # ("replacement filter pack", "refill", "pod pair").
    for nphrase in (
        "replacement filter pack", "filter pack", "replacement filters",
        "replacement pod pair", "pod pair", "replacement pack",
        "refill pack", "refill", "consumables", "consumable", "subscription",
        "add-on", "addon", "spare",
    ):
        if nphrase in ctx:
            return nphrase.title()
    return "Replacement / refill"


def _amount_to_float(amount: str) -> float | None:
    raw = amount.replace("$", "").replace(",", "").strip()
    try:
        return float(raw)
    except ValueError:
        return None


# --- Power / charging / feature parsing -----------------------------------

_POWER_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\bplugs?\s+into\s+(?:a\s+)?(?:normal\s+|standard\s+)?"
            r"wall\s+outlet\b",
            re.IGNORECASE,
        ),
        "Plugs into a normal wall outlet",
    ),
    (
        re.compile(r"\b(?:wired|hard[\- ]wired)\s+(?:to|into)\s+mains?\b", re.IGNORECASE),
        "Hard-wired into mains",
    ),
    (
        re.compile(r"\b(?:usb-?c?|usb-c)\s+powered\b", re.IGNORECASE),
        "USB-powered",
    ),
)


_CHARGING_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\bcharges?\s+(?:wirelessly|magnetically)\b",
            re.IGNORECASE,
        ),
        "Charges wirelessly / magnetically",
    ),
    (
        re.compile(
            r"\brun\s+wirelessly\s+for\s+up\s+to\s+(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)",
            re.IGNORECASE,
        ),
        "Runs wirelessly for up to {0} hours per cycle",
    ),
    (
        re.compile(
            r"\bbattery\s+life\s+(?:of|is)\s+(?:up\s+to\s+)?(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)",
            re.IGNORECASE,
        ),
        "Battery life up to {0} hours",
    ),
    (
        re.compile(
            r"\b(?:usb[\- ]?c|type[\- ]?c\s+usb)[\- ]?(?:rechargeable|charging|charged|powered)?",
            re.IGNORECASE,
        ),
        "USB-C rechargeable / USB-C powered",
    ),
    (
        re.compile(
            r"\brechargeable\b",
            re.IGNORECASE,
        ),
        "Rechargeable",
    ),
)


_EXCLUDED_FEATURE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bdoes\s+not\s+use\s+heat\b", re.IGNORECASE),
     "Does not use heat"),
    (re.compile(r"\bdoes\s+not\s+use\s+steam\b", re.IGNORECASE),
     "Does not use steam"),
    (re.compile(r"\bdoes\s+not\s+use\s+water\b", re.IGNORECASE),
     "Does not use water"),
    (re.compile(r"\bdoes\s+not\s+use\s+(?:detergent|soap)\b", re.IGNORECASE),
     "Does not use detergent"),
    (re.compile(r"\bdoes\s+not\s+use\s+(?:uv|uv[\- ]?c|ultraviolet)\b",
                re.IGNORECASE),
     "Does not use UV light"),
    (re.compile(r"\bdoes\s+not\s+use\s+ozone\b", re.IGNORECASE),
     "Does not use ozone"),
    (re.compile(r"\bno\s+heat\b", re.IGNORECASE), "No heat"),
    (re.compile(r"\bno\s+steam\b", re.IGNORECASE), "No steam"),
    (re.compile(r"\bno\s+water\b", re.IGNORECASE), "No water"),
    (re.compile(r"\bno\s+detergent\b", re.IGNORECASE), "No detergent"),
    (re.compile(r"\bno\s+(?:uv|ultraviolet)\b", re.IGNORECASE),
     "No UV light"),
    (re.compile(r"\bno\s+ozone\b", re.IGNORECASE), "No ozone"),
)


# Phase 10B.3 — temperature / runtime / cleaning / material parsers.
# These cover the GlowPlate-shaped fact gaps that 10B.2 left open.

_TEMPERATURE_RANGE_RE = re.compile(
    r"(\d{2,3})\s?°?\s?[Ff]\s?[-–—~to]+\s?(\d{2,3})\s?°?\s?[Ff]\b",
)
_TEMPERATURE_SINGLE_RE = re.compile(
    r"\b(?:warming|warm|hold(?:s|ing)?|keeps?|maintains?)\s+"
    r"(?:at|to|around|near)?\s*(\d{2,3})\s?°?\s?[Ff]\b",
    re.IGNORECASE,
)
_RUNTIME_MINUTES_RE = re.compile(
    r"\b(?:up\s+to\s+|for\s+up\s+to\s+|holds?\s+(?:food\s+)?warm\s+(?:for\s+)?(?:up\s+to\s+)?)?"
    r"(\d{1,3})\s?(?:min(?:ute)?s?|mins?)\b",
    re.IGNORECASE,
)
_RUNTIME_HOURS_RE = re.compile(
    r"\b(?:up\s+to\s+|for\s+up\s+to\s+)?(\d{1,2}(?:\.\d+)?)\s?(?:hr|hrs|hour|hours)\b",
    re.IGNORECASE,
)
_DISHWASHER_SAFE_RE = re.compile(
    r"\bdishwasher[\- ]safe\b",
    re.IGNORECASE,
)
_MICROWAVE_SAFE_RE = re.compile(
    r"\bmicrowave[\- ]safe\b",
    re.IGNORECASE,
)
_HAND_WASH_RE = re.compile(
    r"\bhand[\- ]wash(?:\s+only)?\b",
    re.IGNORECASE,
)
_TOP_RACK_RE = re.compile(
    r"\btop[\- ]rack(?:\s+only)?\s+(?:dishwasher|safe)?\b",
    re.IGNORECASE,
)
_USB_C_RE = re.compile(
    r"\b(?:usb[\- ]?c|type[\- ]?c\s+usb)\b",
    re.IGNORECASE,
)
_USB_A_RE = re.compile(
    r"\b(?:usb[\- ]?a|micro[\- ]?usb)\b",
    re.IGNORECASE,
)
_RECHARGEABLE_RE = re.compile(
    r"\brechargeable\b",
    re.IGNORECASE,
)

_MATERIAL_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bceramic\s+(?:plate|surface|disc|insert)\b", re.IGNORECASE),
     "Ceramic plate"),
    (re.compile(r"\bremovable\s+ceramic\b", re.IGNORECASE),
     "Removable ceramic plate"),
    (re.compile(r"\bstainless\s+steel\b", re.IGNORECASE),
     "Stainless steel"),
    (re.compile(r"\bbpa[\- ]free\b", re.IGNORECASE),
     "BPA-free"),
    (re.compile(r"\bfood[\- ]grade\b", re.IGNORECASE),
     "Food-grade"),
    (re.compile(r"\bsilicone\s+(?:base|seal|grip)?\b", re.IGNORECASE),
     "Silicone"),
    (re.compile(r"\bactivated[\- ]carbon\b", re.IGNORECASE),
     "Activated carbon"),
    (re.compile(r"\baluminum\b", re.IGNORECASE),
     "Aluminum"),
    (re.compile(r"\bglass\s+(?:plate|surface|cover)\b", re.IGNORECASE),
     "Glass"),
    (re.compile(r"\btempered\s+glass\b", re.IGNORECASE),
     "Tempered glass"),
)


def _parse_temperature_facts(blob: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in _TEMPERATURE_RANGE_RE.finditer(blob):
        lo, hi = m.group(1), m.group(2)
        phrase = f"Warming range {lo}°F–{hi}°F"
        if phrase not in seen:
            out.append(phrase)
            seen.add(phrase)
    if not out:
        for m in _TEMPERATURE_SINGLE_RE.finditer(blob):
            phrase = f"Holds at {m.group(1)}°F"
            if phrase not in seen:
                out.append(phrase)
                seen.add(phrase)
    return out


def _parse_runtime_facts(blob: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    # Look for common runtime phrasings — "up to 45 minutes",
    # "keeps food warm up to 45 min", "for 45 minutes". We require
    # at least one explicit time unit (minutes or hours).
    blob_low = blob.lower()
    if "minute" in blob_low or " min" in blob_low:
        for m in _RUNTIME_MINUTES_RE.finditer(blob):
            ctx_start = max(0, m.start() - 60)
            ctx = blob[ctx_start:m.end()].lower()
            if any(
                key in ctx
                for key in (
                    "warm", "warming", "hold", "keep", "stays",
                    "session", "cycle", "runtime", "battery", "charge",
                )
            ):
                phrase = f"Up to {m.group(1)} minutes"
                if phrase not in seen:
                    out.append(phrase)
                    seen.add(phrase)
                    break  # one minutes-fact is enough
    if "hour" in blob_low or "hr" in blob_low:
        for m in _RUNTIME_HOURS_RE.finditer(blob):
            ctx_start = max(0, m.start() - 60)
            ctx = blob[ctx_start:m.end()].lower()
            if any(
                key in ctx
                for key in (
                    "warm", "warming", "hold", "keep", "battery",
                    "wireless", "wirelessly", "charge", "runtime",
                )
            ):
                phrase = f"Up to {m.group(1)} hours per session"
                if phrase not in seen:
                    out.append(phrase)
                    seen.add(phrase)
                    break
    return out


def _parse_cleaning_facts(blob: str) -> list[str]:
    out: list[str] = []
    if _DISHWASHER_SAFE_RE.search(blob):
        out.append("Dishwasher-safe")
    elif _TOP_RACK_RE.search(blob):
        out.append("Top-rack dishwasher-safe")
    if _MICROWAVE_SAFE_RE.search(blob):
        # Look for the "when separated" qualifier nearby
        m = _MICROWAVE_SAFE_RE.search(blob)
        if m:
            window = blob[m.start():min(len(blob), m.end() + 120)].lower()
            if "separated" in window or "without the base" in window:
                out.append("Microwave-safe when separated from base")
            else:
                out.append("Microwave-safe")
    if _HAND_WASH_RE.search(blob):
        out.append("Hand-wash only")
    return out


def _parse_materials(blob: str) -> list[str]:
    out: list[str] = []
    for rx, label in _MATERIAL_HINTS:
        if rx.search(blob) and label not in out:
            out.append(label)
    return out


# Phase 10B.4 — Negation-scope sensing + input-mechanism parsers.
#
# Each sensing capability is detected by a POSITIVE pattern (the
# product *has* this feature) and a NEGATION pattern (the product
# *does not do* this with the feature). The two are stored as
# separate flags so personas can't collapse "does not record video"
# into "no camera".

# (positive_re, fact_key, description_template)
_SENSING_POSITIVE_PATTERNS: tuple[
    tuple[re.Pattern[str], str, str], ...
] = (
    (re.compile(
        r"\b(?:tiny|small|wide[\- ]angle|miniature|built[\- ]in)?\s*camera\b"
        r"(?!\s+(?:shutter|cover|lens\s+cap))",
        re.IGNORECASE,
    ), "has_camera", "Camera present"),
    (re.compile(
        r"\b(?:still\s+image|still\s+photo|snapshot|stills)s?\b",
        re.IGNORECASE,
    ), "captures_still_images", "Captures still images"),
    (re.compile(r"\bphysical\s+(?:camera\s+)?shutter\b", re.IGNORECASE),
     "physical_camera_shutter", "Physical camera shutter"),
    (re.compile(
        r"\bvisible\s+(?:scan\s+)?(?:led|light|indicator)\b",
        re.IGNORECASE,
    ), "visible_scan_led", "Visible scan LED / indicator"),
    (re.compile(r"\bmicrophone\b", re.IGNORECASE),
     "has_microphone", "Microphone present"),
    (re.compile(
        r"\b(?:thermometer|temperature\s+sensor|temp\s+probe)\b",
        re.IGNORECASE,
    ), "has_thermometer", "Temperature sensor present"),
    (re.compile(r"\b(?:weight|load)\s+sensor\b", re.IGNORECASE),
     "has_weight_sensor", "Weight / load sensor"),
)

# Negations that override / qualify the positive sensing facts. Each
# negation sets a *behaviour* flag (records_video, livestreams,
# identifies_people, etc.) — NOT the existence flag. So a brief
# saying "has a tiny camera but does not record video" yields
# `has_camera=true, records_video=false`.
_SENSING_NEGATION_PATTERNS: tuple[
    tuple[re.Pattern[str], str, str], ...
] = (
    (re.compile(
        r"\bdoes\s+not\s+record\s+(?:any\s+)?video\b|"
        r"\bno\s+video\s+recording\b|"
        r"\bdoes\s+not\s+capture\s+(?:any\s+)?video\b",
        re.IGNORECASE,
    ), "records_video", "Does NOT record video"),
    (re.compile(
        r"\bdoes\s+not\s+livestream\b|"
        r"\bno\s+live\s+stream(?:ing)?\b|"
        r"\bdoes\s+not\s+stream\b",
        re.IGNORECASE,
    ), "livestreams", "Does NOT livestream"),
    (re.compile(
        r"\bdoes\s+not\s+(?:identify|recognise|recognize)\s+people\b|"
        r"\bno\s+(?:facial|face)\s+(?:recognition|id)\b|"
        r"\bdoes\s+not\s+(?:perform\s+)?(?:facial|face)\s+(?:recognition|id)\b",
        re.IGNORECASE,
    ), "identifies_people", "Does NOT identify people / no face recognition"),
    (re.compile(
        r"\bdoes\s+not\s+listen\b|"
        r"\bno\s+audio\s+recording\b|"
        r"\bdoes\s+not\s+record\s+audio\b",
        re.IGNORECASE,
    ), "records_audio", "Does NOT record audio"),
    (re.compile(
        r"\bdoes\s+not\s+(?:upload|send)\s+(?:images|photos|video)\b|"
        r"\b(?:images|photos)\s+stay\s+on\s+device\b|"
        r"\blocal[\- ]only\s+processing\b",
        re.IGNORECASE,
    ), "uploads_media_to_cloud", "Does NOT upload media to the cloud"),
)

# Input-mechanism positive patterns (barcode / NFC / RFID / app /
# voice / manual). Same shape as sensing.
_INPUT_MECHANISM_PATTERNS: tuple[
    tuple[re.Pattern[str], str, str], ...
] = (
    (re.compile(
        r"\bbarcode\s*[/\-]?\s*(?:and\s+)?(?:nfc\s+)?(?:scanner|scanning|scan|reader)|"
        r"\bbarcode\s+(?:scanner|scanning|scan|reader)|"
        r"\bbarcode/nfc\s+(?:scanner|scanning|scan|reader)",
        re.IGNORECASE,
    ),
     "has_barcode_scanning", "Barcode scanning"),
    (re.compile(
        r"\bnfc\s+(?:scanner|scanning|scan|reader|tag|tags)|"
        r"\bbarcode\s*/\s*nfc\b|"
        r"\b(?:barcode|qr)\s+(?:and\s+|/\s*)nfc\b",
        re.IGNORECASE,
    ),
     "has_nfc_scanning", "NFC scanning"),
    (re.compile(r"\b(?:reusable\s+)?nfc\s+(?:food\s+)?tags?\b", re.IGNORECASE),
     "has_reusable_nfc_tags", "Reusable NFC tags"),
    (re.compile(r"\brfid\s+(?:scanner|tag|reader)\b", re.IGNORECASE),
     "has_rfid_scanning", "RFID scanning"),
    (re.compile(
        r"\bqr[\s\-]+code[\s\-]+(?:scanner|scanning|scan|reader)\b",
        re.IGNORECASE,
    ),
     "has_qr_scanning", "QR-code scanning"),
    (re.compile(r"\bvoice\s+(?:input|command|control|assistant)\b", re.IGNORECASE),
     "has_voice_input", "Voice input"),
    (re.compile(
        r"\b(?:manual\s+)?(?:in[\- ]app|app)\s+entry\b|"
        r"\btype\s+(?:items|in)\b",
        re.IGNORECASE,
    ), "has_manual_app_entry", "Manual app entry"),
    (re.compile(r"\bbluetooth\s+(?:scale|sync)\b", re.IGNORECASE),
     "has_bluetooth_input", "Bluetooth-paired input device"),
)


def _parse_sensing_facts(
    blob: str,
) -> tuple[dict[str, bool], dict[str, list[str]]]:
    """Parse sensing capabilities into (flags, details). The flag set
    distinguishes *existence* from *behavior* — `has_camera=True` is
    independent of `records_video=False`. Both can be true."""
    flags: dict[str, bool] = {}
    details: dict[str, list[str]] = {}
    for rx, key, label in _SENSING_POSITIVE_PATTERNS:
        if rx.search(blob):
            flags[key] = True
            details.setdefault(key, []).append(label)
    for rx, key, label in _SENSING_NEGATION_PATTERNS:
        if rx.search(blob):
            flags[key] = False
            details.setdefault(key, []).append(label)
    return flags, details


def _parse_input_mechanism_facts(
    blob: str,
) -> tuple[dict[str, bool], dict[str, list[str]]]:
    flags: dict[str, bool] = {}
    details: dict[str, list[str]] = {}
    for rx, key, label in _INPUT_MECHANISM_PATTERNS:
        if rx.search(blob):
            flags[key] = True
            details.setdefault(key, []).append(label)
    return flags, details


_INCLUDED_FEATURE_HINTS: tuple[str, ...] = (
    "moisture sensing", "moisture sensor", "humidity sensor",
    "uv-c sanitation", "uv-c", "uv c sanitation",
    "warm airflow", "gentle warm airflow",
    "quiet night mode", "quiet micro-fan", "micro-fan",
    "led dryness indicator", "led indicator",
    "activated-carbon filter", "carbon filter", "odor filter",
    "app status", "humidity status",
    "quick refresh", "rain-damp dry", "odor reset",
    "magnetic", "magnetic charging", "magnetic pods",
)


_EXCLUDED_LIST_RE = re.compile(
    r"\b(?:does\s+not\s+use|do\s+not\s+use|without)\s+([^.]+?)\.",
    re.IGNORECASE,
)


def _parse_excluded_features_from_list(blob: str) -> list[str]:
    """Catch comma-separated lists like 'does not use heat, steam,
    water, detergent, or UV light.' that the per-pattern regexes
    miss when items run together."""
    out: list[str] = []
    for m in _EXCLUDED_LIST_RE.finditer(blob):
        items_blob = m.group(1)
        # Split on commas + "and"/"or"
        raw = re.split(r"\s*,\s*|\s+and\s+|\s+or\s+", items_blob)
        for item in raw:
            # Items can still carry leading "or " / "and " when the
            # source had `, or X` (which the comma split absorbs
            # without the conjunction).
            it = re.sub(r"^(?:or|and)\s+", "", item.strip()).strip(" .;").lower()
            if not it or len(it) > 60:
                continue
            # Whitelist of recognized feature words to avoid noise.
            for feat, label in (
                ("heat", "No heat"),
                ("steam", "No steam"),
                ("water", "No water"),
                ("detergent", "No detergent"),
                ("soap", "No detergent"),
                ("uv", "No UV light"),
                ("uv light", "No UV light"),
                ("uv-c", "No UV light"),
                ("ultraviolet", "No UV light"),
                ("ozone", "No ozone"),
                ("chemicals", "No chemicals"),
            ):
                if it == feat or it.startswith(f"{feat} "):
                    if label not in out:
                        out.append(label)
                    break
    return out


def _parse_power_charging_features(
    description: str, optional_context: str | None,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Return (power_facts, charging_facts, included_features,
    excluded_features) extracted from the description + optional
    context. Best-effort regex; never invents facts."""
    blob = " ".join([description or "", optional_context or ""])
    power: list[str] = []
    charging: list[str] = []
    included: list[str] = []
    excluded: list[str] = []

    for rx, template in _POWER_PATTERNS:
        if rx.search(blob):
            if template not in power:
                power.append(template)

    for rx, template in _CHARGING_PATTERNS:
        m = rx.search(blob)
        if not m:
            continue
        if "{0}" in template and m.groups():
            phrase = template.format(m.group(1))
        else:
            phrase = template
        if phrase not in charging:
            charging.append(phrase)

    for rx, template in _EXCLUDED_FEATURE_PATTERNS:
        if rx.search(blob):
            if template not in excluded:
                excluded.append(template)

    # Catch comma-separated lists the per-pattern regexes miss.
    for label in _parse_excluded_features_from_list(blob):
        if label not in excluded:
            excluded.append(label)

    blob_low = blob.lower()
    for hint in _INCLUDED_FEATURE_HINTS:
        if hint in blob_low:
            phrase = hint.replace("uv-c", "UV-C").replace("led", "LED")
            phrase = phrase[0].upper() + phrase[1:]
            if phrase not in included:
                included.append(phrase)

    return power, charging, included, excluded


_KIT_CONTENT_BULLET_RE = re.compile(
    r"^\s*[-•*]\s*(.+?)$", re.MULTILINE,
)
_KIT_CONTENT_NUMBER_RE = re.compile(
    r"\b(\d+)\s+(?:smart\s+)?([a-z][a-z\- ]+?)(?=[,;.\n]|$)",
    re.IGNORECASE,
)


def _parse_kit_contents(price_str: str | None, description: str) -> list[str]:
    """Parse kit contents bullets. Looks for `- foo` / `• foo`
    bullets (typically inside the price field) and falls back to
    `N <noun>` patterns inside parenthetical sentences in the
    description (e.g. "kit includes one rail and three hangers")."""
    out: list[str] = []
    blob = " ".join([price_str or "", description or ""])
    for m in _KIT_CONTENT_BULLET_RE.finditer(blob):
        item = m.group(1).strip().rstrip(".")
        if 3 <= len(item) <= 120 and "$" not in item:
            if item not in out:
                out.append(item)
    return out


# --- Public API ------------------------------------------------------------


def generate_product_fact_card(brief: dict[str, Any]) -> ProductFactCard:
    """Build the fact card from a FounderBriefIn-shaped dict.
    Universal — works for any product brief."""
    product_name = (brief.get("product_name") or "").strip() or "the product"
    product_type = (
        brief.get("category_hint")
        or brief.get("product_type")
        or _derive_product_type_from_description(
            brief.get("product_description") or ""
        )
    )
    description = brief.get("product_description") or ""
    optional_context = (brief.get("optional_context") or "").strip() or None

    not_cats = _derive_not_categories(product_type, description)

    price_str = brief.get("price_or_price_structure") or None
    primary_price, bundle_price, accessories = _parse_prices(price_str)

    power, charging, included, excluded = _parse_power_charging_features(
        description, optional_context,
    )

    kit_contents = _parse_kit_contents(price_str, description)

    # Phase 10B.3 — temperature / runtime / cleaning / materials
    # facts are scanned across the description + optional_context +
    # price field. The price field can carry kit-content phrasing
    # like "ceramic plate + base" so it gets included.
    fact_blob = " ".join(
        [description or "", optional_context or "", price_str or ""]
    )
    temperature_facts = _parse_temperature_facts(fact_blob)
    runtime_facts = _parse_runtime_facts(fact_blob)
    cleaning_facts = _parse_cleaning_facts(fact_blob)
    materials = _parse_materials(fact_blob)
    # Phase 10B.4 — sensing + input-mechanism flags
    sensing_facts, sensing_fact_details = _parse_sensing_facts(fact_blob)
    input_mechanism_facts, input_mechanism_details = (
        _parse_input_mechanism_facts(fact_blob)
    )
    # Phase 10B.6 — generic forbidden-feature extractor.
    from assembly.sources.product_grounding.forbidden_features import (
        extract_forbidden_features,
    )
    forbidden_features = extract_forbidden_features(
        product_description=description or "",
        optional_context=optional_context,
    )

    return ProductFactCard(
        product_name=product_name,
        product_type=(product_type or None),
        not_categories=not_cats,
        price_or_price_structure=price_str,
        primary_price=primary_price,
        bundle_price=bundle_price,
        accessory_prices=accessories,
        kit_contents=kit_contents,
        power_facts=power,
        charging_facts=charging,
        included_features=included,
        excluded_features=excluded,
        materials=materials,
        temperature_facts=temperature_facts,
        runtime_facts=runtime_facts,
        cleaning_facts=cleaning_facts,
        sensing_facts=sensing_facts,
        sensing_fact_details=sensing_fact_details,
        input_mechanism_facts=input_mechanism_facts,
        input_mechanism_details=input_mechanism_details,
        forbidden_features=forbidden_features,
        launch_geography=brief.get("launch_geography"),
        launch_state=brief.get("launch_state"),
        target_customers=list(brief.get("target_customers") or []),
        competitors_or_alternatives=list(
            brief.get("competitors_or_alternatives") or []
        ),
        optional_context=optional_context,
        constraints=list(brief.get("constraints") or []),
    )


def _derive_product_type_from_description(desc: str) -> str | None:
    if not desc:
        return None
    low = desc.lower()
    for marker in (" is a ", " is an "):
        idx = low.find(marker)
        if idx >= 0:
            tail = desc[idx + len(marker):].strip()
            for stop in (".", ",", " that ", " for ", " which ", " — "):
                pos = tail.find(stop)
                if pos > 0:
                    tail = tail[:pos]
                    break
            return tail.strip().rstrip(".")
    words = desc.split()[:14]
    return " ".join(words).rstrip(".")


def fact_card_prompt_block(card: ProductFactCard) -> str:
    """Render the fact card into a deterministic plain-text block
    safe to inject into any LLM prompt. The Phase 10B.2 version
    explicitly distinguishes primary price from accessory/refill
    prices and lists locked power / excluded-feature facts so
    agents cannot accidentally re-ask them."""
    lines: list[str] = []
    lines.append("PRODUCT FACT LOCK — DO NOT CONTRADICT")
    lines.append(f"Product: {card.product_name}")
    if card.product_type:
        lines.append(f"Type: {card.product_type}")
    if card.not_categories:
        lines.append(
            "Not: " + ", ".join(card.not_categories)
        )
    # Price hierarchy — the Phase 10B.2 fix. Make it impossible for
    # an agent to confuse a refill price with the main product price.
    if card.primary_price:
        lines.append(
            f"Primary price (the product itself): {card.primary_price}"
        )
    elif card.price_or_price_structure:
        lines.append(
            f"Price: {card.price_or_price_structure}"
        )
    if card.bundle_price:
        lines.append(
            f"Bundle / multi-pack price (still the same product, "
            f"sold as a multi-unit set): {card.bundle_price}"
        )
    if card.accessory_prices:
        for ap in card.accessory_prices:
            lines.append(
                f"Accessory / refill price (NOT the main product): "
                f"{ap.amount} for {ap.label}"
            )
        lines.append(
            "  ⚠ Accessory / refill prices are NOT the main product "
            "price. Do not call the product itself by an "
            "accessory amount."
        )
    if card.kit_contents:
        lines.append("Kit contents (what the primary purchase includes):")
        for item in card.kit_contents[:10]:
            lines.append(f"  - {item}")
    if card.materials:
        lines.append(
            "Materials: " + ", ".join(card.materials)
        )
    if card.temperature_facts:
        lines.append(
            "Temperature / performance: "
            + "; ".join(card.temperature_facts)
        )
    if card.runtime_facts:
        lines.append(
            "Runtime / duration: " + "; ".join(card.runtime_facts)
        )
    if card.cleaning_facts:
        lines.append(
            "Cleaning / care: " + ", ".join(card.cleaning_facts)
        )
    # Phase 10B.4 — sensing facts. Render the *positive* facts first
    # so personas see "Has camera" before "Does NOT record video".
    # This preserves negation scope: the negation applies to the
    # behavior, not the existence of the sensor.
    if card.sensing_facts:
        positive_sensing = [
            (k, card.sensing_fact_details.get(k, [k]))
            for k, v in card.sensing_facts.items()
            if v
        ]
        negative_sensing = [
            (k, card.sensing_fact_details.get(k, [k]))
            for k, v in card.sensing_facts.items()
            if not v
        ]
        if positive_sensing:
            lines.append("Sensing capabilities (the product HAS these):")
            for _, dets in positive_sensing:
                lines.append(f"  • {' / '.join(dets)}")
        if negative_sensing:
            lines.append(
                "Sensing behaviors the product DOES NOT do (the sensor "
                "exists, but its behavior is bounded):"
            )
            for _, dets in negative_sensing:
                lines.append(f"  • {' / '.join(dets)}")
    if card.input_mechanism_facts:
        positive_inputs = [
            (k, card.input_mechanism_details.get(k, [k]))
            for k, v in card.input_mechanism_facts.items()
            if v
        ]
        if positive_inputs:
            lines.append("Input mechanisms (the product HAS these ways "
                         "to capture data):")
            for _, dets in positive_inputs:
                lines.append(f"  • {' / '.join(dets)}")
    # Phase 10B.6 — explicit forbidden features. The brief itself
    # told us the product does NOT have these. Personas must never
    # mention them as if they existed.
    if card.forbidden_features:
        lines.append(
            "Features the brief explicitly says the product does NOT "
            "have (do NOT discuss these as if they existed):"
        )
        for ff in card.forbidden_features[:12]:
            lines.append(
                f"  • {ff.canonical_name} "
                f"(source: \"{ff.source_sentence[:120]}\")"
            )
    if card.launch_geography:
        lines.append(f"Launch geography: {card.launch_geography}")
    if card.launch_state:
        lines.append(f"Launch state: {card.launch_state}")
    if card.target_customers:
        lines.append(
            "Target customers: " + ", ".join(card.target_customers)
        )
    if card.competitors_or_alternatives:
        lines.append(
            "Competitors / alternatives: "
            + ", ".join(card.competitors_or_alternatives)
        )
    if card.power_facts:
        lines.append("Power: " + "; ".join(card.power_facts))
    if card.charging_facts:
        lines.append("Charging / battery: " + "; ".join(card.charging_facts))
    if card.included_features:
        lines.append(
            "Included features: " + ", ".join(card.included_features[:10])
        )
    if card.excluded_features:
        lines.append(
            "Excluded features (the product explicitly does NOT do these): "
            + "; ".join(card.excluded_features)
        )
    if card.constraints:
        lines.append(
            "Constraints: " + "; ".join(card.constraints)
        )
    if card.optional_context:
        lines.append(f"Context: {card.optional_context}")
    lines.append("Important rules for personas:")
    lines.append(
        "  • The product is unlaunched — no persona has bought, used, "
        "owned, or reviewed it."
    )
    lines.append(
        "  • Treat ALL facts above as already provided. You may "
        "question whether a claim is credible, but DO NOT ask for "
        "facts that are listed here as if they were missing — "
        "INCLUDING price, bundle price, kit contents, materials, "
        "power, charging, runtime, temperature, cleaning / "
        "dishwasher / microwave claims, and excluded features."
    )
    lines.append(
        "  • If a fact above is something you'd want VERIFIED "
        "(e.g. dishwasher-safe across many cycles, runtime under "
        "real loads, food-contact certification), phrase your "
        "concern as 'Since the brief says X, I'd want proof Y' — "
        "do NOT phrase it as if X were unknown."
    )
    if card.sensing_facts:
        lines.append(
            "  • NEGATION SCOPE: when a sensor or capability is "
            "marked HAS above, the product HAS it. A separate fact "
            "that the product 'DOES NOT do <behavior>' applies to "
            "the BEHAVIOR, not the EXISTENCE of the sensor. For "
            "example, 'has camera' + 'does NOT record video' means "
            "the camera EXISTS and captures still images. Do NOT "
            "say 'no camera' or 'without a camera' in that case — "
            "it's a fact inversion."
        )
    if card.input_mechanism_facts:
        lines.append(
            "  • If the product has barcode / NFC / RFID / app / "
            "voice input above, do NOT say 'no scanning' or 'no "
            "input mechanism' — the input mechanism EXISTS. Your "
            "valid concern is whether the workflow is "
            "low-friction enough, not whether a mechanism exists."
        )
    if card.forbidden_features:
        examples = ", ".join(
            ff.canonical_name for ff in card.forbidden_features[:5]
        )
        lines.append(
            f"  • FORBIDDEN FEATURES: the brief explicitly says the "
            f"product does NOT have {examples}. Do NOT discuss any "
            "of those as if they existed. You MAY discuss the "
            "valid alternative mechanism the brief affirms."
        )
    if card.primary_price and card.accessory_prices:
        lines.append(
            "  • The primary price applies to the main product. "
            "Accessory / refill prices are recurring costs you can "
            "discuss separately — do not conflate them."
        )
    if card.excluded_features:
        lines.append(
            "  • The product explicitly does NOT include the "
            "excluded features. Do not ask whether it does. You "
            "MAY question whether the product still works without "
            "those features."
        )
    lines.append(
        "  • If retrieved evidence describes competitors, that "
        "describes COMPETITORS — it does not redefine the product "
        "above."
    )
    lines.append(
        "  • You are NOT an AI assistant. You are NOT a synthetic "
        "agent. You are a real person in the target market reacting "
        "naturally to this product based on your situation, "
        "preferences, constraints, and the alternatives you "
        "actually use. Speak like a person, not an evaluator."
    )
    lines.append(
        "  • Do NOT say 'as an agent', 'as a synthetic persona', "
        "'in this simulation', 'in this synthetic society', "
        "'n=24', 'directional', 'not a forecast', 'not a verdict', "
        "or any other system-level caveat. Those phrases NEVER "
        "appear in real human speech and they break the run."
    )
    lines.append(
        "  • You MAY reference real competitors or alternatives "
        "you plausibly use ('I use an Ember Mug', 'I usually "
        "reheat in the microwave'). You MAY NOT claim to have "
        "used the unlaunched product itself."
    )
    return "\n".join(lines)
