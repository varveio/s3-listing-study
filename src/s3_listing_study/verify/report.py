"""Verdict report rendering: ``verify.md`` and ``union-verify.md``.

Byte-identical output is the acceptance bar for the port
(``tests/differential/``), so this module reproduces the shell's ``printf``
sequence exactly — including the trailing prose about counting duplicates
before dedup, and the first-20 excerpt blocks. Any report-format improvement is
a separate change made only after equivalence is proven.

Reports are assembled as **bytes**: a key is raw listing bytes and an excerpt
block prints it, so encoding the report through a text layer would be the same
key-mangling the contract exists to prevent.
"""

from __future__ import annotations

from collections.abc import Sequence

EXCERPT = 20
"""Rows of each key list the report carries. ``head -20`` in the shell."""

PLACEHOLDER = "_(filled in by `harness/verify-listing.sh`)_"
"""The verdict slot a fresh receipt carries, and the re-verify guard's anchor."""

_TRAILER = (
    b"\nDuplicates are counted **before** dedup: the completeness diff needs a\n"
    b"deduplicated set, but a set union would destroy the duplicate evidence,\n"
    b"so the multiset is counted first.\n"
)


def _excerpt(title: str, lines: Sequence[bytes]) -> bytes:
    if not lines:
        return b""
    body = b"".join(line + b"\n" for line in lines[:EXCERPT])
    return f"\n### {title} (first 20)\n\n```\n".encode() + body + b"```\n"


def single(
    *,
    tool: str,
    mode: str,
    verdict: str,
    drift_note: str,
    scope: str,
    fields_checked: str,
    registry_path: str,
    registry_sha256: str,
    manifest_sha256: str,
    snapshot_date: str,
    expected_keys: int,
    total_records: int,
    distinct_keys: int,
    duplicates: int,
    missing: Sequence[bytes],
    extra: Sequence[bytes],
    dups: Sequence[bytes],
    field_mismatches: Sequence[bytes],
    inputs: int,
) -> bytes:
    """The single-receipt ``verify.md``."""
    out = f"# Verifier — `{tool}` / mode `{mode}`\n\n".encode()
    out += f"**Verdict: {verdict}**\n\n".encode()
    if drift_note:
        out += f"{drift_note}\n\n".encode()
    out += b"| | |\n| --- | --- |\n"
    rows = [
        f"| Scope | `{scope}` |",
        f"| Fields checked | {fields_checked} |",
        f"| Registry | `{registry_path}` (sha256 `{registry_sha256}`, as used by the run) |",
        f"| Manifest | `{manifest_sha256}` |",
        f"| Snapshot date | {snapshot_date} |",
        f"| Expected keys | {expected_keys} |",
        f"| Emitted records | {total_records} (multiset, pre-dedup) |",
        f"| Distinct keys | {distinct_keys} |",
        f"| Duplicates | {duplicates} |",
        f"| Missing | {len(missing)} |",
        f"| Extra | {len(extra)} |",
        f"| Field mismatches | {len(field_mismatches)} |",
        f"| Inputs | {inputs} |",
    ]
    out += "".join(f"{row}\n" for row in rows).encode()
    out += _TRAILER
    out += _excerpt("missing", missing)
    out += _excerpt("extra", extra)
    out += _excerpt("dup", dups)
    if field_mismatches:
        out += b"\n### field mismatches (first 20)\n\n```\n"
        out += b"".join(line + b"\n" for line in field_mismatches[:EXCERPT])
        out += b"```\n"
    return out


def union_error(generated: str, message: str) -> bytes:
    """The durable ERROR artifact a plan-defect death owes ``--out``.

    Every plan-defect verdict — duplicate receipts, mixed modes, overlapping
    prefixes, an undesignated empty prefix, a redaction or truncation refusal —
    is an ERROR exactly as structural incompleteness is, and the union verdict
    is promised to land in ``union-verify.md``. Writing it here keeps that
    promise for the early deaths too, not only for the ones that reach the main
    writer.
    """
    return (
        b"# Verifier (union)\n\n"
        b"**Verdict: ERROR**\n\n"
        + f"- Generated (UTC): {generated}\n\n".encode()
        + f"{message}\n".encode()
    )


def union(
    *,
    tool: str,
    mode: str,
    verdict: str,
    generated: str,
    registry_digest: str,
    bucket: str,
    region: str,
    auth: str,
    manifest_sha256: str,
    snapshot_date: str,
    relist_note: str,
    note: str,
    shards: Sequence[tuple[int, str, str]],
    prefix_shards: int,
    has_remainder: bool,
    orphan: int,
    structural: bool,
    expected_keys: int,
    total_records: int,
    distinct_keys: int,
    duplicates: int,
    missing: Sequence[bytes],
    extra: Sequence[bytes],
    dups: Sequence[bytes],
    field_mismatches: Sequence[bytes],
) -> bytes:
    """The ``--scope union`` report."""
    out = f"# Verifier (union) — `{tool}` / mode `{mode}`\n\n".encode()
    out += f"**Verdict: {verdict}**\n\n".encode()
    out += f"- Generated (UTC): {generated}\n".encode()
    out += f"- Registry digest: `{registry_digest}`\n".encode()
    out += f"- Bucket: `{bucket}`  region `{region}`  auth `{auth}`\n".encode()
    out += f"- Manifest sha256: `{manifest_sha256}`  snapshot {snapshot_date}\n".encode()
    if relist_note:
        out += f"- Reference re-list: {relist_note}\n".encode()
    out += b"\n"
    if note:
        out += f"{note}\n\n".encode()
    out += b"## Shards\n\n| # | prefix | receipt |\n| --- | --- | --- |\n"
    for index, prefix, receipt in shards:
        out += f"| {index} | {prefix} | `{receipt}` |\n".encode()
    out += b"\n## Counts\n\n| | |\n| --- | --- |\n"
    rows = [
        f"| Scope | union of {len(shards)} shard(s) |",
        f"| Prefix shards | {prefix_shards} |",
        f"| Remainder shard | {'present' if has_remainder else 'absent'} |",
        f"| Root-level keys under no prefix | {orphan} |",
        f"| Structural status | {'INCOMPLETE' if structural else 'complete'} |",
        f"| Manifest keys | {expected_keys} |",
        f"| Emitted records | {total_records} (multiset, pre-dedup) |",
        f"| Distinct keys | {distinct_keys} |",
        f"| Cross-shard duplicates (before dedup) | {duplicates} |",
        f"| Missing | {len(missing)} |",
        f"| Extra | {len(extra)} |",
        f"| Field mismatches | {len(field_mismatches)} |",
    ]
    out += "".join(f"{row}\n" for row in rows).encode()
    out += _excerpt("union.missing", missing)
    out += _excerpt("union.extra", extra)
    out += _excerpt("union.dup", dups)
    if field_mismatches:
        out += b"\n### field mismatches (first 20)\n\n```\n"
        out += b"".join(line + b"\n" for line in field_mismatches[:EXCERPT])
        out += b"```\n"
    return out


def stamp(receipt_md: str, verdict: str) -> tuple[str, int]:
    """Splice the verdict into the receipt, literally. Returns the text and the hit count.

    The count is of *lines* carrying the placeholder, which is what ``grep -cF``
    reports and what the re-verify guard is written against. The splice is
    ``index``/slice rather than a regex substitution: the placeholder contains
    ``(``, ``)`` and ``.``, so a regex spelling matches nothing while the guard
    matches fine — the file is rewritten unchanged, the verdict is silently
    dropped, and ``receipt.md`` keeps its placeholder forever.
    """
    lines = receipt_md.split("\n")
    hits = sum(1 for line in lines if PLACEHOLDER in line)
    replacement = f"**{verdict}** — see `verify.md`"
    done = False
    for index, line in enumerate(lines):
        position = line.find(PLACEHOLDER)
        if position >= 0 and not done:
            lines[index] = line[:position] + replacement + line[position + len(PLACEHOLDER) :]
            done = True
    return "\n".join(lines), hits
