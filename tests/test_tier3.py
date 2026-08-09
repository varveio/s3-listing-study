"""Tier 3 — the verdicts no committed receipt exercises.

All 57 replayed verdicts are PASS, so FAIL, DRIFT and ERROR — the three outcomes
of the reference re-list that decides whether a discrepancy is *the tool's* —
have no coverage at all. They are also the ones that become published findings
about another project's tool, so they are the last place in this repo where a
port should be trusted on inspection.

Each case runs the shipped verifier — this checkout's `s3_listing_study.host.verify`,
asserted below — inside a staged tree. The only substitutions are the runner
security preflight and `docker`, which replays a fixture-recorded reference
listing. See `tests/tier3/stage.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from s3_listing_study.host import verify
from tests.tier3 import make_fixtures
from tests.tier3 import stage as tier3
from tests.tier3.stage import Stage


@pytest.fixture
def stage(tmp_path: Path) -> Stage:
    return Stage(tmp_path / "stage")


# ------------------------------------------------------------------ the fixtures


def test_the_committed_fixtures_are_what_the_generator_produces() -> None:
    """The fixtures are synthetic and deterministic, so this is checkable — check it.

    `make_fixtures` enforced payload/manifest agreement only while regenerating,
    which left a hand-edit of the committed files unnoticed: every case below
    would then be testing a listing that no snapshot matches, and a FAIL or a
    DRIFT it produced would prove nothing. The generator needs no data directory
    and no network, so the committed bytes are simply asserted against it.
    """
    for name, text in make_fixtures.build().items():
        assert (tier3.FIXTURES / name).read_text() == text, f"{name} is not the generated fixture"


def test_every_payload_row_canonicalises_to_its_manifest_row() -> None:
    """The invariant the whole tier rests on: the payloads verify PASS against the manifest."""
    manifest = {tier3.key_of(row): row for row in tier3.manifest_rows()}
    payload = tier3.bucket_rows()
    assert len(manifest) == len(payload), "the manifest and the listing name different key sets"
    for row in payload:
        assert make_fixtures.canonicalize(row) == manifest[tier3.key_of(row)]
        assert len(row.split("\t")) == 5


def test_the_fixture_shape_the_verdicts_need_is_present() -> None:
    """Two disjoint shard prefixes, and exactly one key under neither.

    The root-level key is what makes STRUCTURAL INCOMPLETENESS reachable at all:
    a union of prefixes never lists it. Without it that case would silently stop
    being a case.
    """
    prefixes = (tier3.ALPHA_PREFIX, tier3.BETA_PREFIX)
    assert not prefixes[1].startswith(prefixes[0]) and not prefixes[0].startswith(prefixes[1])
    keys = [tier3.key_of(row) for row in tier3.manifest_rows()]
    assert [key for key in keys if not key.startswith(prefixes)] == [
        tier3.key_of(row) for row in tier3.remainder_rows()
    ]
    for prefix in prefixes:
        assert sum(key.startswith(prefix) for key in keys) > 12


def alpha_receipt(stage: Stage, rows: list[str]) -> Path:
    return stage.receipt("alpha", tier3.ALPHA_META, rows, prefix=tier3.ALPHA_PREFIX)


def test_the_verifier_under_test_is_this_checkouts(stage: Stage) -> None:
    """A rig that quietly ran something else would prove nothing about the verifier.

    There is no staged shell file to digest any more, so the implementation under
    test is pinned the only way it can be: the package the entry point imports
    has to be this checkout's, not a stale install that happens to be on the path.
    """
    assert stage.verifier, "the rig must name the command it drives"
    assert Path(verify.__file__).resolve().is_relative_to(tier3.REPO / "src")


def test_a_clean_receipt_passes_without_re_listing(stage: Stage) -> None:
    """The control: no discrepancy, no re-list, so the stub is never consulted.

    `reference=None` leaves the fake docker with nothing to replay — it exits
    125. If the rig were manufacturing re-lists, this PASS would be an ERROR.
    """
    receipt = alpha_receipt(stage, tier3.alpha_rows())
    outcome = stage.verify(receipt, tier3.ALPHA_PREFIX, reference=None)
    assert outcome.returncode == 0
    assert outcome.verdict == "PASS"
    assert "re-listing the reference" not in outcome.log


def test_dropped_key_with_the_reference_agreeing_is_fail(stage: Stage) -> None:
    """The tool lost a key and the bucket did not move: FAIL, exit 1."""
    rows = tier3.alpha_rows()
    dropped = rows[7]
    receipt = alpha_receipt(stage, rows[:7] + rows[8:])
    outcome = stage.verify(
        receipt, tier3.ALPHA_PREFIX, reference=stage.reference(tier3.bucket_rows())
    )

    assert outcome.returncode == 1
    assert outcome.verdict == "FAIL"
    assert "| Missing | 1 |" in outcome.report
    assert tier3.key_of(dropped) in outcome.report
    # A FAIL names both candidates and does not choose between them: the adapter
    # is newer than the tool and written by us.
    assert "the tool or in this mode's `normalize.py` adapter" in outcome.report
    assert "**FAIL** — see `verify.md`" in outcome.receipt


def test_mtime_only_overwrite_is_drift_and_not_fail(stage: Stage) -> None:
    """The case the verifier's mtime comparison exists to catch.

    An object was overwritten with identical bytes: same key, same size, same
    ETag, later mtime. The tool reports what the bucket now holds and disagrees
    with the snapshot. Every key-based check says "no drift" — the key sets are
    identical, asserted here — so a drift comparison over anything less than the
    full record would issue a FAIL for a tool that is exactly right.
    """
    rows = tier3.alpha_rows()
    moved = tier3.bump_mtime(rows[3])
    bucket = [moved if row == rows[3] else row for row in tier3.bucket_rows()]
    receipt = alpha_receipt(stage, [moved, *rows[:3], *rows[4:]])

    manifest_keys = {tier3.key_of(row) for row in tier3.manifest_rows()}
    assert {tier3.key_of(row) for row in bucket} == manifest_keys

    outcome = stage.verify(receipt, tier3.ALPHA_PREFIX, reference=stage.reference(bucket))

    assert outcome.verdict != "FAIL"
    assert outcome.verdict == "DRIFT"
    assert outcome.returncode == 4
    assert "| Field mismatches | 1 |" in outcome.report
    assert "This is not a tool finding." in outcome.report
    assert "**DRIFT** — see `verify.md`" in outcome.receipt


def test_an_object_added_after_the_snapshot_is_drift(stage: Stage) -> None:
    """The tool emits a key the snapshot never had, and the bucket confirms it."""
    rows = tier3.alpha_rows()
    added = rows[11]
    manifest = [row for row in tier3.manifest_rows() if tier3.key_of(row) != tier3.key_of(added)]
    stage = Stage(stage.root.parent / "added", manifest=manifest)
    receipt = alpha_receipt(stage, rows)

    outcome = stage.verify(
        receipt, tier3.ALPHA_PREFIX, reference=stage.reference(tier3.bucket_rows())
    )

    assert outcome.returncode == 4
    assert outcome.verdict == "DRIFT"
    assert "| Extra | 1 |" in outcome.report
    assert "The bucket moved since" in outcome.report


@pytest.mark.parametrize(
    ("docker_rc", "expected"),
    [(1, "failed (Docker rc=1)"), (124, "timed out (Docker wrapper rc=124)")],
)
def test_a_re_list_that_cannot_run_is_error_not_a_verdict(
    stage: Stage, docker_rc: int, expected: str
) -> None:
    """No re-list, no attribution. ERROR is not a pass and not a finding."""
    rows = tier3.alpha_rows()
    receipt = alpha_receipt(stage, rows[1:])
    outcome = stage.verify(
        receipt,
        tier3.ALPHA_PREFIX,
        reference=stage.reference(tier3.bucket_rows()),
        docker_rc=docker_rc,
        docker_stderr="Unable to locate credentials",
    )

    assert outcome.returncode == 3
    assert outcome.verdict == "ERROR"
    assert expected in outcome.report
    assert "cannot attribute the discrepancy" in outcome.report
    assert "Unable to locate credentials" in outcome.report
    assert "**ERROR** — see `verify.md`" in outcome.receipt


# ------------------------------------------------------------------- --scope union


def union_shards(stage: Stage, alpha: list[str], beta: list[str]) -> tuple[Path, Path]:
    return (
        stage.receipt("shard-alpha", tier3.ALPHA_META, alpha, prefix=tier3.ALPHA_PREFIX),
        stage.receipt("shard-beta", tier3.BETA_META, beta, prefix=tier3.BETA_PREFIX),
    )


def union_remainder(stage: Stage) -> Path:
    return stage.receipt("shard-remainder", tier3.REMAINDER_META, tier3.remainder_rows())


def test_union_without_a_remainder_shard_is_a_structural_error(stage: Stage) -> None:
    """The root-level key lives under no shard prefix: a defect in the FAN-OUT PLAN.

    A union of prefixes never lists a root-level key. Attributing that to the tool
    would report a defect for a key nobody asked it to list, so it is an ERROR
    about the plan — reported before any re-list, which is why this case needs
    no docker at all.
    """
    shards = union_shards(stage, tier3.alpha_rows(), tier3.beta_rows())
    outcome = stage.verify_union(shards, reference=None)

    assert outcome.returncode == 3
    assert outcome.verdict == "ERROR"
    assert "**STRUCTURAL INCOMPLETENESS**" in outcome.report
    assert "| Root-level keys under no prefix | 1 |" in outcome.report
    assert "| Structural status | INCOMPLETE |" in outcome.report
    assert "distinct from the tool dropping keys" in outcome.report
    assert "re-listing the reference" not in outcome.log


def test_union_with_a_designated_remainder_is_complete_and_passes(stage: Stage) -> None:
    """The control for the structural case: designate the remainder and it covers."""
    shards = union_shards(stage, tier3.alpha_rows(), tier3.beta_rows())
    remainder = union_remainder(stage)
    outcome = stage.verify_union([*shards, remainder], remainder=remainder, reference=None)

    assert outcome.returncode == 0
    assert outcome.verdict == "PASS"
    assert "| Root-level keys under no prefix | 1 |" in outcome.report
    assert "| Structural status | complete |" in outcome.report


def test_union_refuses_shards_from_different_regions(stage: Stage) -> None:
    shards = union_shards(stage, tier3.alpha_rows(), tier3.beta_rows())
    remainder = union_remainder(stage)
    beta_meta = shards[1] / "run.meta"
    beta_meta.write_text(beta_meta.read_text().replace("region=us-east-1", "region=us-west-2"))
    outcome = stage.verify_union([*shards, remainder], remainder=remainder, reference=None)
    assert outcome.returncode == 3
    assert outcome.verdict == "ERROR"
    assert "is region 'us-west-2', not 'us-east-1'" in outcome.report


def test_union_dropped_key_with_the_reference_agreeing_is_fail(stage: Stage) -> None:
    """A shard that fails its own scope, with the bucket unmoved: FAIL, exit 1."""
    beta = tier3.beta_rows()
    shards = union_shards(stage, tier3.alpha_rows(), beta[:-1])
    remainder = union_remainder(stage)
    outcome = stage.verify_union(
        [*shards, remainder],
        remainder=remainder,
        reference=stage.reference(tier3.bucket_rows()),
    )

    assert outcome.returncode == 1
    assert outcome.verdict == "FAIL"
    assert "| Missing | 1 |" in outcome.report
    assert tier3.key_of(beta[-1]) in outcome.report
    assert "shard 1 (prefix='beta/'): missing=1 extra=0" in outcome.report
    assert "the tool or in this mode's `normalize.py` adapter" in outcome.report


def test_union_mtime_only_overwrite_is_drift_and_not_fail(stage: Stage) -> None:
    """The union re-lists before ANY FAIL, for the same reason the single path does."""
    beta = tier3.beta_rows()
    moved = tier3.bump_mtime(beta[2])
    bucket = [moved if row == beta[2] else row for row in tier3.bucket_rows()]
    shards = union_shards(stage, tier3.alpha_rows(), [moved, *beta[:2], *beta[3:]])
    remainder = union_remainder(stage)

    assert {tier3.key_of(row) for row in bucket} == {
        tier3.key_of(row) for row in tier3.manifest_rows()
    }

    outcome = stage.verify_union(
        [*shards, remainder], remainder=remainder, reference=stage.reference(bucket)
    )

    assert outcome.verdict != "FAIL"
    assert outcome.verdict == "DRIFT"
    assert outcome.returncode == 4
    assert "| Field mismatches | 1 |" in outcome.report
    assert "This is not a tool finding." in outcome.report


def test_a_union_refusal_still_leaves_the_durable_artifact(stage: Stage) -> None:
    """`errors.py` invariant: a union ERROR lands in `union-verify.md`, not only on stderr.

    An adapter that exits non-zero is the cheapest way to reach a refusal raised
    below the union's own `union_die` equivalent. Routing that through a handler
    that converted the error without writing left the union verdict nowhere
    durable — and named a staged temp path instead of the shard.
    """
    adapter = stage.root / "failing-normalize.sh"
    adapter.write_text("#!/usr/bin/env bash\nexit 7\n")
    adapter.chmod(0o755)
    shards = union_shards(stage, tier3.alpha_rows(), tier3.beta_rows())
    remainder = union_remainder(stage)

    outcome = stage.verify_union(
        [*shards, remainder], remainder=remainder, reference=None, normalize=adapter
    )

    assert outcome.returncode == 3
    assert outcome.verdict == "ERROR"
    assert "union shard 0" in outcome.report
    assert "shard.0.stdout.raw" not in outcome.report


def test_union_re_list_that_cannot_run_is_error(stage: Stage) -> None:
    beta = tier3.beta_rows()
    shards = union_shards(stage, tier3.alpha_rows(), beta[:-1])
    remainder = union_remainder(stage)
    outcome = stage.verify_union(
        [*shards, remainder],
        remainder=remainder,
        reference=stage.reference(tier3.bucket_rows()),
        docker_rc=1,
        docker_stderr="Could not connect to the endpoint URL",
    )

    assert outcome.returncode == 3
    assert outcome.verdict == "ERROR"
    assert "failed (Docker rc=1)" in outcome.report
    assert "cannot attribute the union discrepancy" in outcome.report
