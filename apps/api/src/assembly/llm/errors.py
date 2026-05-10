"""LLM-layer exceptions."""
from __future__ import annotations


class LLMProviderError(Exception):
    """Base for any error raised by an LLMProvider implementation."""


class LLMSchemaValidationError(LLMProviderError):
    """Raised when an LLM response cannot be parsed into the requested Pydantic
    schema even after repair attempts are exhausted."""


class LLMRepairExhausted(LLMSchemaValidationError):
    """Raised by `LLMProvider.structured_output()` when `max_repair_attempts`
    is reached without a successful parse."""


class CostCapExceeded(LLMProviderError):
    """Raised by `with_cost_guard()` when the next call would push a
    simulation's total LLM cost over its hard cap."""

    def __init__(self, *, simulation_id: str, total_so_far: float, estimated_next: float, hard_cap: float):
        self.simulation_id = simulation_id
        self.total_so_far = total_so_far
        self.estimated_next = estimated_next
        self.hard_cap = hard_cap
        super().__init__(
            f"Cost cap exceeded for simulation {simulation_id}: "
            f"spent {total_so_far:.4f}, next call estimated {estimated_next:.4f}, "
            f"hard cap {hard_cap:.4f}"
        )


class CutoffViolationError(LLMProviderError):
    """Raised when a fetch or prompt would include post-cutoff content that
    violates a simulation's `evidence_cutoff_date`."""
