# The differential replay oracle

This harness re-issues **every committed verdict in the repo** through the
verifier and requires the bytes back unchanged. It is the safety mechanism for
the bash → Python port: an implementation that produces a different `verify.md`,
a different receipt stamp, or a different union report for any committed run is
wrong, and this gate says so mechanically.

The corpus is **67 single-receipt verdicts + 2 unions**, pinned as
`EXPECTED_SINGLES` / `EXPECTED_UNIONS` in `__main__.py`. That is every committed
`verify.md` and every committed `union-verify.md`. An unfiltered run that
discovers any other number fails: otherwise a dropped or moved verdict artifact
shrinks the denominator along with the numerator and the gate reports a clean
sweep of whatever survived. Changing either constant asserts that the set of
committed verdicts genuinely changed, and belongs in the same commit as the
artifact it accounts for. The repo holds 85 `run.meta`
files, but the remaining 18 are capability probes and BLOCKED `s3-fast-list` runs
that never produced a verdict — there is nothing to replay them against.

## Running it

```sh
python3 -m tests.differential          # from the repo root; ~4 minutes
```

Stdlib Python 3 only. No network, no docker, no security gate: both
`security_preflight` calls in `harness/verify-listing.sh` sit inside the
discrepancy/union re-list branches, and every committed verdict is a PASS, so the
replay never reaches them.

Useful flags:

| Flag | Effect |
| --- | --- |
| `--jobs N` | replays to run concurrently (default 4) |
| `--filter SUBSTR` | only replay cases whose repo-relative path contains `SUBSTR` |
| `--singles-only` / `--unions-only` | restrict the corpus |
| `--keep DIR` | work under `DIR` and leave the staged artifacts behind |
| `--state PATH` / `--no-state` | where to record the run (default `notes/agent-state/cleanup.json`) |
| `--unit NAME` | unit name recorded in the state file (default `U0`) |

## Exit-code contract

| Code | Meaning |
| --- | --- |
| `0` | green — every committed verdict replayed byte-identical |
| `1` | mismatch — at least one did not. **The implementation under test is wrong.** |
| `42` | `ORACLE_UNAVAILABLE` — the oracle could not be read, so nothing was judged |

`1` also covers the cases where no verdict was formed at all and green would be a
lie: a corpus-size mismatch, a selection that matched nothing, a harness crash,
and a state file that could not be written. The state record distinguishes them —
`corpus-mismatch`, `no-cases`, `error` — from an honest `red`.

## The state record

The gate writes `notes/agent-state/cleanup.json` (plus an append-only
`cleanup.log.jsonl`) with the unit, status, the **actual** argv it was invoked
with, a `selection` block naming `--filter` / `--singles-only` / `--unions-only`,
the HEAD sha, and the counts. Plan §7 makes this the record an unattended run
reads to decide whether a unit passed, so:

| Status | Meaning |
| --- | --- |
| `green` | the **whole** corpus replayed byte-identical |
| `partial` | a restricted run (filtered or one-sided) replayed clean — not a pass |
| `red` | at least one verdict came back different |
| `corpus-mismatch` | discovery did not find the pinned corpus; nothing was judged |
| `no-cases` | the selection matched nothing; nothing was judged |
| `oracle-unavailable` | preflight refused; nothing was judged |
| `error` | the harness itself crashed; nothing was judged |

A restricted run can never write `green`. A corrupt existing state file is
refused rather than overwritten — silently starting over would destroy every
prior unit's record — and a state file that cannot be written turns a would-be
`0` into `1`, because a run with no record is a run with no evidence.

`42` is never a pass and never a skip. It means the gate declined to form an
opinion, and the unit that depends on it halts rather than degrading to green.
It is raised when the data directory is absent, when the reference manifest does
not hash to `c78a8273…92adb`, when the committed registry fixture does not hash to
`254c8cfe…bbced`, or when **any raw payload the corpus names** is missing or does
not hash to the digest its own `run.meta` cites. That last check is what keeps a
partial restore of the data directory from surfacing as exit 1 — "your port is
wrong" — when the truth is that the oracle is incomplete.

## Oracle inputs

| Input | Where | Bound by |
| --- | --- | --- |
| Reference manifest | `$S3_STUDY_DATA/manifests/noaa-normals-pds.2026-07-17.tsv.gz` | sha256 `c78a8273…92adb` |
| Raw payloads (57 of 67) | `$S3_STUDY_DATA/receipts/` | each run.meta's own sha256, checked by the preflight |
| Raw payloads (10 of 67) | `tools/*/receipts/` | committed |
| Registry fixture | `tests/fixtures/registry-254c8cfe.md` | sha256 `254c8cfe…bbced` |

`$S3_STUDY_DATA` defaults to `~/s3-list-study-data`. It is never hardcoded — the
gate has to run on any machine that has the data.

The manifest and the payloads are deliberately **not** committed: they are a
snapshot of a third party's bucket (NOAA), and this repo ships only its own work.
Acceptance is a sha256 match against a binding that already existed in the
committed receipts, so the bytes may come from anywhere — data dir, backup, a
rebuilt listing — without weakening the check. Preimage resistance means a broken
source cannot fake a match.

## Why a registry fixture

The 85 committed `run.meta` cite registry sha `254c8cfe…`; today's
`docs/smoke-bucket.md` hashes to something else. The verifier refuses to judge a
run against a registry the run never saw, so the replay exports
`SMOKE_REGISTRY=tests/fixtures/registry-254c8cfe.md`. Pinning it also insulates
the oracle from every later registry edit: the fixture is what the runs saw, and
that does not change. It was recovered from the pre-squash repo at
`32951ff6:docs/smoke-bucket.md` and is sha-verified on every run.

## How a replay works

For a **single receipt**:

1. Stage a copy of the receipt directory under a temp root. Nothing outside that
   root is ever written — `$S3_STUDY_DATA` and `tools/*/receipts` are read-only
   inputs, and an oracle the gate can write to is not an oracle.
2. Restore the verdict placeholder in the staged `receipt.md`. The re-verify guard
   (`harness/verify-listing.sh:671-680`) refuses an already-stamped receipt; the
   placeholder is exactly the state the receipt was in when the committed verdict
   was issued.
3. Read the scope back from the committed `verify.md` Scope row, and the verified
   stream from `run.meta` (`stdout_path`/`stderr_path`, preferring the stream whose
   recorded sha256 is not the empty digest) — never from what happens to be on disk.
4. Run `harness/verify-listing.sh` with cwd set to a link farm.
5. Require exit 0, a byte-identical `verify.md`, and a byte-identical restamped
   `receipt.md`.

For a **union**, the shard set is read from the `union-verify.md` shard table, not
inferred from directory layout — the aws-cli union pulls its fourth prefix shard
(`s3api-v2-text-hourly`) from outside the fanout directory, and a layout heuristic
silently drops it. The table also records shard order and the remainder
designation, both of which the regenerated report reproduces column for column.
The union path writes only into `--out` and never stamps its shards, so the shards
are read straight out of the repo. Comparison is over **bytes**, modulo the single
`Generated (UTC)` line, which is blanked as a bytes pattern: decoding the report
first would fold CRLF into LF and raise on any byte the locale codec cannot read.

The **link farm** is a working directory holding three symlinks — `manifests` and
`receipts` into `$S3_STUDY_DATA`, `tools` into the repo. No committed `run.meta`
declares `payload_path_base`, so the verifier resolves relative payload paths
against the caller's working directory, and the corpus mixes two conventions
(`receipts/…` for external payloads, `tools/…` for in-repo ones). The farm makes
both resolve without copying 531 MB and without the verifier seeing any path other
than the ones its own records name.

## Layout

| File | Role |
| --- | --- |
| `oracle.py` | oracle inputs, digest pins, the preflight, the exit codes |
| `corpus.py` | discovery; reads scope, stream, and union shard tables back out of committed artifacts |
| `replay.py` | link farm, staging, invocation, byte comparison |
| `__main__.py` | gate entrypoint, corpus pin, exit contract, state emission |

## Testing the oracle

The oracle is the only safety mechanism the port has, so it carries the repo's
strongest static coverage: `tests/` is under the same strict mypy, ruff lint and
ruff format gates as `src/`. Its own unit tests live in `tests/test_*.py` and run
offline under `uv run pytest` — no data directory needed. One of them replays the
corpus pin against the real repo, so CI fails if a committed verdict artifact ever
stops being discovered.
