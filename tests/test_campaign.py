"""Campaign identity, job naming, artifact layout, and the submission ledger."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from s3_listing_study.manager.bench import plan as bench
from s3_listing_study.manager.campaign import (
    CAMPAIGN_MAX,
    JOB_ID_MAX,
    JOB_ID_RE,
    Attempt,
    CampaignError,
    attempt_fingerprint,
    attempt_prefix,
    attempts_for,
    job_id,
    manifest,
    validate_campaign_id,
)
from s3_listing_study.manager.campaign import ledger as ledger_module

NOW = "2026-08-10T12:00:00Z"


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


# ── job ids ──────────────────────────────────────────────────────────────────


def test_every_job_id_the_committed_plan_generates_is_legal() -> None:
    """The constraint that forced the design: a case id alone is already 46 chars.

    Batch takes `^[a-z]([a-z0-9-]*[a-z0-9])?$` and at most 63, so the id is
    budgeted rather than assembled from the parts that identify a case.
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
    """Batch refuses a leading digit, and a dated campaign has one."""
    assert job_id(
        campaign="2026-08-10-first", tool="swath", case_id="x", fingerprint="0" * 64, submission=1
    ).startswith("c-2026-08-10-first-")


def test_two_images_of_one_case_get_different_job_ids() -> None:
    plan = committed_plan()
    first = attempts_for(plan, campaign="2026-08-10-first", images=image_set(plan))
    second = attempts_for(plan, campaign="2026-08-10-first", images=image_set(plan, OTHER_IMAGE))
    assert not {a.job_id for a in first} & {a.job_id for a in second}


def test_a_resubmission_changes_the_job_id_but_not_the_path() -> None:
    """A job id names a submission; a path names the attempt it was for.

    Nothing is deleted, so a submission that never started leaves its name
    spent — and the still-empty path it was aimed at is the one to aim at again.
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


def test_a_prefix_names_the_manager_run_and_not_the_execution() -> None:
    """The worker names its own UUID execution directory beneath this.

    Batch can execute one task more than once (`BATCH_TASK_RETRY_ATTEMPT`), so a
    leaf chosen here would be written twice and refused by the create-only
    upload — after the run had already cost what it cost.
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


def test_repetitions_get_distinct_run_paths_and_batch_job_ids() -> None:
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
    assert document["attempt_fingerprint_version"] == 2
    assert {a["job_id"] for a in document["attempts"]} == {a.job_id for a in generated}
    assert document["plans"][0]["sha256"] == plan.digest
    assert document["images"]["swath"]["shared_base_digest"] == IMAGE["shared_base_digest"]
    # Neither is in any fingerprint, and a reader will ask about both.
    assert (document["provisioning"], document["zone"]) == ("SPOT", "us-east4-a")


# ── the ledger ───────────────────────────────────────────────────────────────


def one_attempt(submission: int = 1) -> Attempt:
    plan = committed_plan()
    return attempts_for(
        plan, campaign="2026-08-10-first", images=image_set(plan), submission=submission
    )[0]


def attempt_rows(connection: sqlite3.Connection, campaign: str) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT * FROM attempts WHERE campaign = ? ORDER BY tool, case_id, run_ordinal, submission",
        (campaign,),
    ).fetchall()


def test_the_ledger_refuses_the_same_attempt_twice(tmp_path: Path) -> None:
    """The guard against paying for one case twice under two names."""
    attempt = one_attempt()
    with ledger_module.open_ledger(tmp_path / "ledger.sqlite3") as connection:
        ledger_module.record_intent(
            connection, attempt=attempt.as_dict(), campaign=attempt.campaign, now=NOW
        )
        with pytest.raises(ledger_module.LedgerError, match="already in the ledger"):
            ledger_module.record_intent(
                connection, attempt=attempt.as_dict(), campaign=attempt.campaign, now=NOW
            )


def test_the_ledger_records_what_was_invoked_and_not_only_its_digest(tmp_path: Path) -> None:
    """A fingerprint says whether two attempts match, never what either one was.

    The sweep's own variable is the point: "everything at 2 GB" has to be a
    WHERE clause on the record of what ran, not a join against a manifest in a
    bucket the runner may not be able to reach.
    """
    plan = committed_plan()
    swept = next(
        a
        for a in attempts_for(plan, campaign="2026-08-10-first", images=image_set(plan))
        if a.case.resources.container_memory_gb == 2
    )
    with ledger_module.open_ledger(tmp_path / "ledger.sqlite3") as connection:
        ledger_module.record_intent(
            connection, attempt=swept.as_dict(), campaign=swept.campaign, now=NOW
        )
        row = attempt_rows(connection, swept.campaign)[0]

    assert row["mode"] == "recursive-parquet-sorted"
    assert row["machine_type"] == "n4-highcpu-2"
    assert (row["vcpus"], row["memory_gb"], row["container_memory_gb"]) == (2, 4, 2)
    assert row["timeout_s"] == 3600
    assert row["region"] == "us-east-1"
    # The heap share, as the environment the runtime was actually told through.
    assert json.loads(row["env_json"]) == [["JAVA_TOOL_OPTIONS", "-XX:MaxRAMPercentage=75"]]
    # And the whole thing verbatim, so a column nobody thought to add is still
    # recoverable from the row written at the time.
    assert json.loads(row["case_json"]) == swept.as_dict()


def test_no_ceiling_is_recorded_as_null_rather_than_a_number(tmp_path: Path) -> None:
    """Absent is a real answer — the container saw the whole box — not a zero."""
    plan = committed_plan()
    unswept = next(
        a
        for a in attempts_for(plan, campaign="2026-08-10-first", images=image_set(plan))
        if a.case.tool == "s5cmd"
    )
    with ledger_module.open_ledger(tmp_path / "ledger.sqlite3") as connection:
        ledger_module.record_intent(
            connection, attempt=unswept.as_dict(), campaign=unswept.campaign, now=NOW
        )
        row = attempt_rows(connection, unswept.campaign)[0]
    assert row["container_memory_gb"] is None


def test_an_intent_and_its_first_event_are_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt = one_attempt()
    with ledger_module.open_ledger(tmp_path / "ledger.sqlite3") as connection:
        monkeypatch.setattr(
            ledger_module,
            "_event",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("event write failed")),
        )
        with pytest.raises(RuntimeError, match="event write failed"):
            ledger_module.record_intent(
                connection, attempt=attempt.as_dict(), campaign=attempt.campaign, now=NOW
            )
        assert attempt_rows(connection, attempt.campaign) == []


def test_the_ledger_applies_its_schema_to_a_fresh_file(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "ledger.sqlite3"
    with ledger_module.open_ledger(path) as connection:
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {"attempts", "events"} <= tables
    assert path.is_file()


def test_reopening_the_ledger_keeps_what_was_written(tmp_path: Path) -> None:
    """The runner submits across more than one invocation; the record has to survive."""
    attempt = one_attempt()
    path = tmp_path / "ledger.sqlite3"
    with ledger_module.open_ledger(path) as connection:
        ledger_module.record_intent(
            connection, attempt=attempt.as_dict(), campaign=attempt.campaign, now=NOW
        )
    with ledger_module.open_ledger(path) as connection:
        assert len(attempt_rows(connection, attempt.campaign)) == 1
        assert isinstance(connection, sqlite3.Connection)
