"""Campaign identity, job naming, artifact layout, and canonical manifests."""

from __future__ import annotations

from dataclasses import replace

import pytest

from s3_listing_study.manager.bench import plan as bench
from s3_listing_study.manager.campaign import (
    CAMPAIGN_MAX,
    JOB_ID_MAX,
    JOB_ID_RE,
    CampaignError,
    attempt_fingerprint,
    attempt_prefix,
    attempts_for,
    job_id,
    manifest,
    validate_campaign_id,
)


def registration(*, subject: str = "a", derived: str = "d") -> dict[str, object]:
    """One tool's image: the digest that ran, plus what it was built from."""
    return {
        "derived_image": "sha256:" + derived * 64,
        "shared_base_digest": "sha256:" + subject * 64,
        "shared_base_uri": "registry.example/base@sha256:" + subject * 64,
        "shared_base_source_sha256": subject * 64,
        "tool_build_sha256": subject * 64,
        "tool_artifact": {"kind": "release-binary", "locator": "example", "sha256": subject * 64},
        "adapter_bundle_sha256": subject * 64,
        "harness_revision": "0.1.0",
        "tool_image_digest": "sha256:" + subject * 64,
        "selection_sha256": subject * 64,
    }


IMAGE = registration()
OTHER_IMAGE = registration(subject="b", derived="e")


def committed_plan() -> bench.Plan:
    return bench.Plan.load(bench.default_path("noaa-ghcn-pds"))


def image_set(
    plan: bench.Plan, image: dict[str, object] | None = None
) -> dict[str, dict[str, object]]:
    return dict.fromkeys(plan.tools(), image or IMAGE)


# ── identity ─────────────────────────────────────────────────────────────────


def test_the_image_is_part_of_an_attempts_identity() -> None:
    """Why the fingerprint is two-layer: a rebuilt tool is a different attempt.

    A fixed image re-run inside a campaign the other tools already completed is
    the case this exists for — it must not resolve to the attempt that failed.
    """
    case = committed_plan().cases[0]
    first = attempt_fingerprint(case_fingerprint=case.fingerprint, components=IMAGE)
    second = attempt_fingerprint(case_fingerprint=case.fingerprint, components=OTHER_IMAGE)
    assert first != second


def test_rebuilding_only_the_orchestrator_side_leaves_identity_alone() -> None:
    """The derived image also carries the collector and uploader, which run after
    the timer closes; an edit to those must not invalidate every case."""
    case = committed_plan().cases[0]
    before = attempt_fingerprint(case_fingerprint=case.fingerprint, components=IMAGE)
    rebuilt = attempt_fingerprint(
        case_fingerprint=case.fingerprint,
        components={**IMAGE, "derived_image": "sha256:" + "9" * 64},
    )
    assert before == rebuilt


def test_a_case_fingerprint_stays_free_of_the_image() -> None:
    """`resolve-plan` contacts nothing, so it cannot know which image will run.

    Folding the image into the case fingerprint would make a plan unreadable
    without a registry.
    """
    plan = committed_plan()
    again = bench.Plan.load(bench.default_path("noaa-ghcn-pds"))
    assert [c.fingerprint for c in plan.cases] == [c.fingerprint for c in again.cases]


def test_an_image_missing_a_component_is_refused() -> None:
    with pytest.raises(CampaignError, match="missing adapter_bundle_sha256"):
        attempt_fingerprint(case_fingerprint="abc", components={"harness_revision": "0.1.0"})


def test_an_image_that_is_not_a_digest_is_refused() -> None:
    plan = committed_plan()
    images = image_set(plan, {**IMAGE, "derived_image": "latest"})
    with pytest.raises(CampaignError, match="not a sha256 digest"):
        attempts_for(plan, campaign="2026-08-10-first", images=images)


# ── planned job identities ───────────────────────────────────────────────────


def test_every_job_id_the_committed_plan_generates_is_legal() -> None:
    """The constraint that forced the design: a case id alone is already 46 chars.

    Provider job resources take `^[a-z]([a-z0-9-]*[a-z0-9])?$` and at most 63,
    so the id is budgeted rather than assembled from the case identity parts.
    """
    plan = committed_plan()
    for attempt in attempts_for(plan, campaign="2026-08-10-first", images=image_set(plan)):
        assert JOB_ID_RE.fullmatch(attempt.job_id), attempt.job_id
        assert len(attempt.job_id) <= JOB_ID_MAX, (attempt.job_id, len(attempt.job_id))


def test_the_longest_possible_job_id_still_fits() -> None:
    """Every component is capped, so this is the worst case by construction."""
    longest = job_id(
        campaign="2026-08-10-longest",
        tool="s3-fast-list",
        case_id="recursive-parquet-sorted.container_memory_gb-2",
        fingerprint="f" * 64,
        submission=99,
    )
    assert len("2026-08-10-longest") == CAMPAIGN_MAX
    assert len(longest) <= JOB_ID_MAX
    assert JOB_ID_RE.fullmatch(longest)


def test_a_job_id_starts_with_a_letter_though_a_campaign_starts_with_a_date() -> None:
    """Provider job resources refuse a leading digit, and a dated campaign has one."""
    assert job_id(
        campaign="2026-08-10-first", tool="swath", case_id="x", fingerprint="0" * 64, submission=1
    ).startswith("c-2026-08-10-first-")


def test_two_images_of_one_case_get_different_job_ids() -> None:
    plan = committed_plan()
    first = attempts_for(plan, campaign="2026-08-10-first", images=image_set(plan))
    second = attempts_for(plan, campaign="2026-08-10-first", images=image_set(plan, OTHER_IMAGE))
    assert not {a.job_id for a in first} & {a.job_id for a in second}


def test_a_new_planned_submission_changes_provenance_id_but_not_the_path() -> None:
    """A job ID records submission generation; the path names the planned run.

    Workflow restarts reuse one planned ID. Only an explicit new submission
    generation changes it, while targeting the same planned evidence path.
    """
    plan = committed_plan()
    first = attempts_for(plan, campaign="2026-08-10-first", images=image_set(plan), submission=1)[0]
    again = attempts_for(plan, campaign="2026-08-10-first", images=image_set(plan), submission=2)[0]
    assert first.job_id != again.job_id
    assert first.prefix == again.prefix
    assert first.fingerprint == again.fingerprint


@pytest.mark.parametrize(
    "campaign", ["first", "2026-8-10-first", "2026-08-10", "2026-08-10-First", "20260810-first"]
)
def test_a_campaign_id_that_is_not_dated_is_refused(campaign: str) -> None:
    with pytest.raises(CampaignError, match="yyyy-mm-dd"):
        validate_campaign_id(campaign)


def test_a_campaign_id_too_long_for_the_budget_is_refused() -> None:
    with pytest.raises(CampaignError, match="can spare"):
        validate_campaign_id("2026-08-10-calibration")


# ── layout ───────────────────────────────────────────────────────────────────


def test_the_bucket_is_in_the_path_so_two_plans_cannot_collide() -> None:
    """`s5cmd/recursive` against two buckets is one case id and two cases."""
    assert attempt_prefix(
        campaign="2026-08-10-first",
        bucket="noaa-ghcn-pds",
        tool="s5cmd",
        case_id="recursive",
    ) != attempt_prefix(
        campaign="2026-08-10-first",
        bucket="commoncrawl",
        tool="s5cmd",
        case_id="recursive",
    )


def test_a_prefix_names_the_planned_run_and_not_the_worker_execution() -> None:
    """The worker names its own UUID execution directory beneath this.

    A scheduler can execute one task more than once, so a leaf chosen here would
    be written twice and refused by the create-only upload only after the repeated
    run had already cost what it cost.
    """
    prefix = attempt_prefix(
        campaign="2026-08-10-first",
        bucket="noaa-ghcn-pds",
        tool="swath",
        case_id="recursive-parquet-sorted.container_memory_gb-2",
    )
    assert prefix == (
        "campaigns/2026-08-10-first/results/noaa-ghcn-pds/swath/"
        "recursive-parquet-sorted.container_memory_gb-2/run-1"
    )


def test_repetitions_get_distinct_run_paths_and_planned_job_ids() -> None:
    plan = committed_plan()
    repeated_case = replace(plan.cases[0], reps=2)
    repeated = replace(plan, cases=(repeated_case,))
    generated = attempts_for(
        repeated,
        campaign="2026-08-10-first",
        images={repeated_case.tool: IMAGE},
    )
    assert [attempt.run_ordinal for attempt in generated] == [1, 2]
    assert len({attempt.job_id for attempt in generated}) == 2
    assert generated[0].fingerprint == generated[1].fingerprint
    assert generated[0].prefix.endswith("/run-1")
    assert generated[1].prefix.endswith("/run-2")


def test_the_path_keeps_the_plans_vocabulary_and_not_the_image() -> None:
    """An address is readable; identity is the fingerprint, which has the image."""
    plan = committed_plan()
    first = attempts_for(plan, campaign="2026-08-10-first", images=image_set(plan))
    second = attempts_for(plan, campaign="2026-08-10-first", images=image_set(plan, OTHER_IMAGE))
    assert [a.prefix for a in first] == [a.prefix for a in second]
    assert [a.fingerprint for a in first] != [a.fingerprint for a in second]


# ── the campaign manifest ────────────────────────────────────────────────────


def test_a_tool_with_no_image_is_refused() -> None:
    plan = committed_plan()
    images = image_set(plan)
    del images["swath"]
    with pytest.raises(CampaignError, match="no final per-tool image digest for swath"):
        attempts_for(plan, campaign="2026-08-10-first", images=images)


def test_the_manifest_indexes_every_job_and_names_the_image_components() -> None:
    """A job id is deliberately not self-describing, so this is what maps it.

    The components sit beside each digest because a harness rebuild moves every
    derived digest without a tool changing, and only they say which happened.
    """
    plan = committed_plan()
    images = image_set(plan)
    generated = attempts_for(plan, campaign="2026-08-10-first", images=images)
    document = manifest(
        campaign="2026-08-10-first",
        plans=[plan],
        images=images,
        attempts=generated,
        results_bucket="study-results",
        provisioning="SPOT",
        zone="us-east4-a",
    )
    assert len(document["attempts"]) == 14
    assert document["schema_version"] == 3
    assert document["attempt_fingerprint_version"] == 3
    assert {a["job_id"] for a in document["attempts"]} == {a.job_id for a in generated}
    assert document["plans"][0]["sha256"] == plan.digest
    assert document["images"]["swath"]["shared_base_digest"] == IMAGE["shared_base_digest"]
    # Neither is in any fingerprint, and a reader will ask about both.
    assert (document["provisioning"], document["zone"]) == ("SPOT", "us-east4-a")
