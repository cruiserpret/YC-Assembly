"""Phase 9A.3 — universal persona psychology layer.

Infers OCEAN + 5 additional psychology traits for any run-scoped persona
from its existing evidence, role context, market traits, and prior
simulation responses. Universal — no LumaLoop hardcoding, no random
priors, no global personas, no new retrieval.
"""
from assembly.sources.persona_psychology_layer.inference import (
    infer_persona_psychology_profile,
)
from assembly.sources.persona_psychology_layer.schemas import (
    PsychologyProfile,
    PsychologyTrait,
)
from assembly.sources.persona_psychology_layer.scoring import (
    compute_profile_variance,
    detect_identical_profiles,
)
from assembly.sources.persona_psychology_layer.validators import (
    SENSITIVE_INFERENCE_FORBIDDEN_FIELDS,
    validate_no_sensitive_inferences,
)


__all__ = [
    "PsychologyProfile",
    "PsychologyTrait",
    "SENSITIVE_INFERENCE_FORBIDDEN_FIELDS",
    "compute_profile_variance",
    "detect_identical_profiles",
    "infer_persona_psychology_profile",
    "validate_no_sensitive_inferences",
]
