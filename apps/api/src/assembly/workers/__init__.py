"""Phase 6.5 — arq worker package.

Public surface:

    from assembly.workers import WorkerSettings, run_pipeline, build_provider

The worker is intentionally thin: it imports the orchestrator, picks a
provider per env config, and forwards. All real logic lives in
`assembly.pipeline.orchestration.run_full_pipeline`.

The static drift tripwire scans this package for direct LLM calls.
The worker MUST go through `call_llm_for_simulation` via the orchestrator.
"""
from assembly.workers.pipeline_worker import build_provider, run_pipeline
from assembly.workers.settings import WorkerSettings

__all__ = ["WorkerSettings", "build_provider", "run_pipeline"]
