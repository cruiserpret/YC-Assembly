"""Phase 6 — Per-round modules.

Each module exposes `async def run_round(ctx, *, provider, sessionmaker)`.
The engine orchestrator threads the same `RoundContext` through every
round in sequence."""
