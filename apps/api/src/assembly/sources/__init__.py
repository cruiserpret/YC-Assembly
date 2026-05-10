"""Phase 8.5A — source-adapter package root.

Each subpackage scaffolds ONE external evidence source. Adapters in
this package never auto-run live calls; the operator must explicitly
invoke a preflight script under `scripts/` with the `--live` flag (or
in the case of Amazon Reviews 2023, the local dataset must be present
on disk).

Source compliance memos for every adapter live under
`docs/source_compliance/`. NEVER add a new source adapter without
also writing the compliance memo.
"""
