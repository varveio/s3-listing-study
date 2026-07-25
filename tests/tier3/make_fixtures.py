"""Generate the tier-3 fixtures: a synthetic listing and the manifest that matches it.

Tier 3 tests the verifier's VERDICT LOGIC — FAIL, DRIFT, ERROR, structural
incompleteness — not the contents of anyone's bucket. Any internally consistent
manifest/payload pair exercises those branches identically, so the fixtures are
invented here rather than sliced out of a third party's listing: this repo
commits no real bucket data, and regeneration works on any checkout with no
`$S3_STUDY_DATA` and no network.

What IS load-bearing is the shape, and every part of it is deliberate:

* contract-v2's five fields, in `s3api ... --output text` spelling (quoted ETag,
  `+00:00` offset), each payload row canonicalising to exactly its manifest row —
  the payloads stand in for a run that verified PASS;
* two distinct, non-overlapping shard prefixes, so `--scope union` has something
  to union and the overlap pre-check has something to accept;
* one key at the ROOT, under neither prefix — the only thing that makes
  STRUCTURAL INCOMPLETENESS reachable, since a union of prefixes never lists it;
* enough rows per shard that a test can move one and leave the rest, and that
  the mtime-only-overwrite case (`bump_mtime`) leaves the key sets identical.

Values are derived from the key with blake2b, so the generator is deterministic:
`tests/test_tier3.py` asserts the committed bytes are exactly what it produces,
which is what stops a hand-edit from drifting the fixtures out of agreement.

Run it only to regenerate:

    python3 -m tests.tier3.make_fixtures
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "tests" / "fixtures" / "tier3"

ALPHA_PREFIX = "alpha/"
BETA_PREFIX = "beta/"
ROOT_KEY = "catalog.json"

# Rows per shard. Small enough to commit and read, large enough that a test can
# drop or move a single row well inside the shard and still have neighbours.
ALPHA_ROWS = 120
BETA_ROWS = 80

# Sizes and mtimes a listing of a real bucket could plausibly carry: a few KB to
# a few MB, and instants a few seconds apart in a single crawl window.
MIN_SIZE = 1_024
SIZE_SPREAD = 8_000_000
EPOCH = datetime(2026, 3, 16, 14, 0, 0, tzinfo=UTC)
STORAGE_CLASS = "STANDARD"


def digest(key: str) -> bytes:
    return hashlib.blake2b(key.encode("utf-8"), digest_size=32).digest()


def etag(key: str) -> str:
    """A synthetic ETag: 32 hex characters, the shape S3 gives a single-part upload."""
    return digest(key)[:16].hex()


def size(key: str) -> int:
    return MIN_SIZE + int.from_bytes(digest(key)[16:24], "big") % SIZE_SPREAD


def mtime(index: int) -> str:
    return (EPOCH + timedelta(seconds=37 * index)).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def payload_row(key: str, index: int) -> str:
    """One `aws s3api ... --output text` row: the ETag quoted, the offset spelled out."""
    return "\t".join((key, str(size(key)), f'"{etag(key)}"', mtime(index), STORAGE_CLASS))


def canonicalize(row: str) -> str:
    """`s3api ... --output text` row -> the manifest's canonical 5-field form.

    The same two rewrites the verifier applies to a reference re-list: unquote
    the ETag, `+00:00` -> `Z`.
    """
    key, row_size, row_etag, row_mtime, storage_class = row.split("\t")
    row_etag = row_etag.replace('"', "")
    if row_mtime.endswith("+00:00"):
        row_mtime = row_mtime[: -len("+00:00")] + "Z"
    return "\t".join((key, row_size, row_etag, row_mtime, storage_class))


def shard(prefix: str, rows: int, start: int) -> list[str]:
    """One prefix's listing, in the lexicographic order S3 returns keys in."""
    keys = [f"{prefix}2020-2024/access/station-{i:04d}.csv" for i in range(rows)]
    return [payload_row(key, start + i) for i, key in enumerate(sorted(keys))]


def build() -> dict[str, str]:
    """The four fixture files, as `{name: text}`. Pure: no data directory, no network."""
    alpha = shard(ALPHA_PREFIX, ALPHA_ROWS, 0)
    beta = shard(BETA_PREFIX, BETA_ROWS, ALPHA_ROWS)
    root = [payload_row(ROOT_KEY, ALPHA_ROWS + BETA_ROWS)]

    payload_rows = alpha + beta + root
    keys = {row.split("\t", 1)[0] for row in payload_rows}
    if len(keys) != len(payload_rows):
        raise RuntimeError("shards share a key — the union would double-count it")

    # The manifest is the snapshot the payloads verify PASS against, so it is
    # exactly their canonical form, in the key order a manifest is written in.
    manifest = sorted(canonicalize(row) for row in payload_rows)

    files = {
        "payload-alpha.txt": alpha,
        "payload-beta.txt": beta,
        "payload-root.txt": root,
        "manifest.tsv": manifest,
    }
    return {name: "".join(f"{row}\n" for row in rows) for name, rows in files.items()}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    files = build()
    for name, text in files.items():
        (OUT / name).write_text(text)
    print(f"wrote {len(files)} fixture files to {OUT}")


if __name__ == "__main__":
    main()
