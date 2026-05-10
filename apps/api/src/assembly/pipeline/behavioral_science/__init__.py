"""Phase 8.2D — behavioral science mechanism library.

This package is the typed Python surface for the research-backed
behavioral mechanism catalog. NO external calls, NO LLM calls, NO
network code, NO persona/trait writes happen here.

Public surface:
  - constants            closed enums shared with the migration / ORM
  - seed_data            deterministic seed payload (sources, mechanisms,
                         strategies, belief rules, applicability rules)
  - validator            structured validators with `ValidationViolation`
  - mechanism_library    DB-backed read service (`get_mechanisms_by_*`)
  - initializer          `build_persona_mechanism_profile` — pure function;
                         returns a typed profile, NEVER writes persona rows
  - audit                `write_mechanism_initialization_audit` — the single
                         blessed write surface for the audit table

The drift test in `tests/test_no_drift_behavioral_science.py` enforces
that this package contains no provider calls, no network imports, and
no persona-write surfaces.
"""
