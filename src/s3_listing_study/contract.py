"""The one listing contract: emit, validate, and ``canon_mtime``.

Will hold the shared 5-field contract-v2 line format (key, size, mtime,
etag, storage-class) that every adapter's ``normalize.sh`` currently
re-implements independently (see e.g.
``tools/aws-cli/adapter/normalize.sh``). Centralizing emission and
validation here is what fixes the per-adapter key-fidelity bugs described in
``notes/2026-07-25-cleanup-plan.md`` §3 (rclone's ``jq @tsv`` escaping,
s3-fast-list's unquoted DuckDB output, s4cmd's missing ``LC_ALL=C``).

Lands in U2 (adapters to Python).
"""
