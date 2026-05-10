"""Phase 9A.1 — universal atomic-evidence-signal extractor.

Takes one accepted evidence item (Brave snippet, Tavily snippet,
YouTube comment/title, Firecrawl extraction) + the founder brief +
the anchor plan, and emits a list of atomic `EvidenceSignal`s.

One evidence item can support multiple persona-candidate seeds.
A source mentioning Brand X PLUS a price concern PLUS a night-
running use case yields three distinct atomic signals — and three
distinct candidate seeds via the persona-emission widener.

Universal: signal lexicons are derived from the brief and a small
universal lexicon (`UNIVERSAL_SIGNAL_LEXICONS`) — never from per-
product templates. NO LLM, NO network. Same inputs → same output.
"""

from assembly.sources.evidence_signal_extractor.constants import (
    UNIVERSAL_SIGNAL_LEXICONS,
)
from assembly.sources.evidence_signal_extractor.extractor import (
    extract_evidence_signals,
)
from assembly.sources.evidence_signal_extractor.schemas import (
    EvidenceSignal, SignalType,
)

__all__ = [
    "EvidenceSignal",
    "SignalType",
    "UNIVERSAL_SIGNAL_LEXICONS",
    "extract_evidence_signals",
]
