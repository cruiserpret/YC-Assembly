"""Phase 14C — durable run-artifact path resolution.

All per-run artifacts (live founder report, discussion transcript,
lightweight voters, intent, etc.) live under a single artifact root:

    <artifact_root>/live_runs/<run_id>/<filename>

By default the artifact root is the in-repo ``apps/api/_audit`` directory,
which preserves the historical local/dev behaviour exactly. In production
(Railway) the container filesystem is *ephemeral*: ``_audit/`` is wiped on
every redeploy/restart, so completed runs lose their report / transcript /
voter artifacts. Set::

    ASSEMBLY_ARTIFACT_ROOT=/data/assembly_artifacts

to a durable, mounted path (a Railway Volume) so new runs survive
redeploys. The run record's ``artifact_manifest`` stores absolute paths
under this root, so as long as the mount path is stable the manifest stays
valid across restarts.

This module ONLY resolves *where* artifacts live. It never changes their
content, never regenerates missing artifacts, and contains no simulation,
calibration, persona, or LLM logic. Missing artifacts remain the caller's
concern (they return the existing graceful "unavailable"/503 contract).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# Environment variable that, when set, overrides the artifact root.
ARTIFACT_ROOT_ENV = "ASSEMBLY_ARTIFACT_ROOT"

# Historical default: ``apps/api/_audit``. This file lives at
# ``apps/api/src/assembly/artifact_paths.py`` so ``parents[2]`` is
# ``apps/api``. Keeping this anchored to ``__file__`` matches the legacy
# ``_AUDIT_ROOT`` in live_founder_brief.py so default behaviour is
# byte-identical to before Phase 14C.
_DEFAULT_AUDIT_ROOT = Path(__file__).resolve().parents[2] / "_audit"

# run_id is a UUID in practice. Allow only safe single-segment characters
# so a run_id can never traverse outside the run scope.
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


def artifact_root() -> Path:
    """Root under which all artifact subtrees live (``live_runs/`` etc.).

    Returns ``ASSEMBLY_ARTIFACT_ROOT`` (resolved) when it is set and
    non-empty — the durable production path — otherwise the in-repo
    ``apps/api/_audit`` default (unchanged local/dev behaviour).
    """
    env = os.environ.get(ARTIFACT_ROOT_ENV)
    if env and env.strip():
        return Path(env).expanduser().resolve()
    return _DEFAULT_AUDIT_ROOT


def live_runs_root() -> Path:
    """Directory holding one subdirectory per live run."""
    return artifact_root() / "live_runs"


def _safe_run_id(run_id: str) -> str:
    """Validate a run_id is a single safe path segment (anti-traversal)."""
    rid = str(run_id)
    if (
        not rid
        or rid in (".", "..")
        or ".." in rid
        or "/" in rid
        or "\\" in rid
        or not _SAFE_RUN_ID.match(rid)
    ):
        raise ValueError(f"unsafe run_id for artifact path: {run_id!r}")
    return rid


def run_artifact_dir(run_id: str) -> Path:
    """Resolve the run-scoped artifact directory for ``run_id``.

    Does NOT create the directory and does NOT require it to exist — a
    missing directory is the caller's graceful-unavailable concern.
    Rejects any run_id that would escape ``live_runs_root()``.
    """
    root = live_runs_root().resolve()
    rid = _safe_run_id(run_id)
    candidate = (root / rid).resolve()
    # Defense in depth: the resolved child must stay within the root.
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"run artifact path escapes root: {run_id!r}")
    return candidate


def run_artifact_path(run_id: str, filename: str) -> Path:
    """Resolve a single artifact file path inside a run's directory.

    ``filename`` must be a plain filename (no path separators).
    """
    name = str(filename)
    if not name or "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"unsafe artifact filename: {filename!r}")
    return run_artifact_dir(run_id) / name


def atomic_write_text(path: Path, data: str, *, encoding: str = "utf-8") -> None:
    """Atomically write text to ``path`` (write temp + os.replace).

    Creates parent directories as needed. Available for callers that want
    crash-consistent artifact writes; existing writers may adopt it
    incrementally. Does not alter artifact content.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(data, encoding=encoding)
    os.replace(tmp, path)
