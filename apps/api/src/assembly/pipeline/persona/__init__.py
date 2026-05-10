"""Phase 8.2A — Population Mode foundation.

Schema-only foundation for the future synthetic-society engine. This
package exposes:

  - constants.py        — closed enums shared by validator/migration/UI
  - validator.py        — persona-trait + safe-for-user validators
  - sensitive_filter.py — first-pass sensitive-attribute detector
  - anonymization.py    — random display names + salted handle hashes
  - audit.py            — PopulationConstructionAudit Pydantic builder

NO ingestion. NO simulation. NO LLM calls. The package's drift test
(tests/test_no_drift_population_foundation.py) asserts that no
network/scraping/browser-automation imports appear here.
"""
from __future__ import annotations

from assembly.pipeline.persona import (
    anonymization,
    audit,
    constants,
    sensitive_filter,
    validator,
)

__all__ = [
    "anonymization",
    "audit",
    "constants",
    "sensitive_filter",
    "validator",
]
