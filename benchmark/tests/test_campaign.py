"""What the ledger must refuse, and what it must never lose.

The model is `benchmark/docs/model.md`; these tests cover the invariants that
protect evidence — identity landing intact, a hash collision being loud, an
ordinal that cannot be raced, a settled success that cannot be quietly re-run —
rather than the shape of any particular row.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from google.api_core.exceptions import AlreadyExists, BadRequest, GoogleAPIError
from google.cloud import batch_v1

from benchmark import adapters, batch_client, campaign, gcs, identity, ledger, measure, report
from benchmark import plan as bench
from benchmark.contract import CREDENTIAL_ENV_VAR, TOOLBOX_TOOLS
from benchmark.plan import Case, Plan
from benchmark.runtime.command_adapter import HEAP_PERCENT

ROOT = Path(__file__).parents[2]
PLAN_PATH = ROOT / "benchmark/plans/buckets/noaa-ghcn-pds.yaml"
DIGEST = "a" * 64
PLATFORM = "9" * 64
AUTH_SECRET = "projects/p/secrets/aws-credentials/versions/1"
SUITE = "s3-listing-study"


def tool_image(tool: str) -> dict[str, str]:
    return {
        "tool_version": "1.0",
        "tool_build_sha256": "b" * 64,
        "tool_artifact_kind": "release-binary",
        "tool_artifact_locator": f"https://example.test/{tool}",
        "tool_artifact_sha256": "f" * 64,
        "recipe_sha256": "7" * 64,
        "build_inputs_sha256": "8" * 64,
        "adapter_bundle_sha256": "c" * 64,
        "subject_workdir": "/",
        # Distinct per tool, as the real manifest's slices are: bumping one tool
        # must not move another tool's identity.
        "tool_slice_sha256": f"{abs(hash(tool)):064x}"[:64],
        "platform_sha256": PLATFORM,
    }


def image_set_document() -> dict[str, object]:
    return {
        "schema_version": 5,
        "image_uri": f"registry/toolbox@sha256:{DIGEST}",
        "toolbox_manifest_sha256": "9" * 64,
        "toolbox_recipe_sha256": "8" * 64,
        "harness_revision": "d" * 40,
        "tools": {tool: tool_image(tool) for tool in TOOLBOX_TOOLS},
    }


def image_set(tmp_path: Path, document: dict[str, object] | None = None) -> campaign.ImageSet:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "images.json"
    path.write_text(json.dumps(document if document is not None else image_set_document()))
    return campaign.load_image_set(path, set())


def options(**overrides: object) -> campaign.BatchOptions:
    values: dict[str, Any] = {
        "anonymous_worker_sa": "anon@example.test",
        "authenticated_worker_sa": "auth@example.test",
        "network": None,
        "subnetwork": None,
        "zone": None,
        "provisioning": "SPOT",
        "project": "p",
        "location": "us-east1",
        "aws_credential_secret": AUTH_SECRET,
    }
    values.update(overrides)
    return campaign.BatchOptions(**values)


def loaded_plan() -> Plan:
    return Plan.load(PLAN_PATH)


def any_case(plan: Plan, *, signed: bool = False) -> Case:
    return next(case for case in plan.cases if (case.auth_role is not None) is signed)


def context(
    plan: Plan,
    case: Case,
    images: campaign.ImageSet,
    *,
    group_id: str = "g20260817-000000",
    **overrides: Any,
) -> campaign.LaunchContext:
    return campaign.LaunchContext.for_tool(
        case.tool,
        suite=SUITE,
        group_id=group_id,
        plan=plan,
        image_set=images,
        results_bucket="results",
        options=options(**overrides),
    )


def submit(
    con: sqlite3.Connection,
    plan: Plan,
    case: Case,
    images: campaign.ImageSet,
    *,
    group_id: str = "g20260817-000000",
    repeat: bool = False,
) -> ledger.Attempt:
    return campaign.submit_case(
        con, case, context(plan, case, images, group_id=group_id), repeat=repeat
    )


@pytest.fixture
def submitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[sqlite3.Connection, Plan, Case, campaign.ImageSet]:
    """A ledger with one attempt in it, created against a provider that says yes."""
    monkeypatch.setattr(batch_client, "ensure_job", lambda *a, **k: ("SUBMITTED", None))
    plan = loaded_plan()
    con = ledger.open_ledger(str(tmp_path / "campaign.db"), suite=SUITE)
    return con, plan, any_case(plan), image_set(tmp_path)


def test_a_row_carries_the_document_its_case_id_was_hashed_from(
    submitted: tuple[sqlite3.Connection, Plan, Case, campaign.ImageSet],
) -> None:
    """The stored inputs must re-derive the stored identity, or nothing binds."""
    con, plan, case, images = submitted
    attempt = submit(con, plan, case, images)

    row = con.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt.attempt_id,)).fetchone()
    document = json.loads(row["case_inputs"])
    assert row["case_id"] == identity.case_id(
        case.tool,
        document["environment"],
        document["config"],
        document["tool_slice_sha256"],
        document["platform_sha256"],
    )
    assert row["attempt_id"] == f"{row['case_id']}.s1"
    # The two axes a comparison is read along are projected out of the blob
    # rather than stored beside it.
    assert row["mode"] == case.mode
    assert row["config"] == json.dumps(dict(case.config), sort_keys=True, separators=(",", ":"))
    assert row["result_prefix"] == (f"gs://results/{SUITE}/{plan.bucket}/{row['attempt_id']}/")


def test_two_cases_hashing_to_one_case_id_are_refused(
    submitted: tuple[sqlite3.Connection, Plan, Case, campaign.ImageSet],
) -> None:
    """The refusal that makes a 48-bit identifier a question of taste, not correctness."""
    con, plan, case, images = submitted
    attempt = submit(con, plan, case, images)

    def build(ordinal: int) -> tuple[ledger.Attempt, str]:
        raise AssertionError("a colliding insert must be refused before it renders")

    with pytest.raises(ledger.CampaignError, match="hash to one case_id"):
        ledger.journal_intent(
            con,
            case_id=attempt.case_id,
            case_inputs=json.dumps({"environment": "something else"}),
            build=build,
        )


def test_an_ordinal_cannot_be_read_past_an_open_transaction(
    submitted: tuple[sqlite3.Connection, Plan, Case, campaign.ImageSet], tmp_path: Path
) -> None:
    """`max(attempt) + 1` is allocated under the write lock, so a race cannot share one."""
    con, plan, case, images = submitted
    first = submit(con, plan, case, images)
    other = ledger.open_ledger(str(tmp_path / "campaign.db"), suite=SUITE)
    other.execute("PRAGMA busy_timeout=50")
    raced: list[Exception] = []

    def build(ordinal: int) -> tuple[ledger.Attempt, str]:
        try:
            ledger.journal_intent(
                other,
                case_id=first.case_id,
                case_inputs=first.case_inputs,
                build=lambda n: (_replaced(first, n), "{}"),
            )
        except sqlite3.OperationalError as exc:
            raced.append(exc)
        return _replaced(first, ordinal), "{}"

    second, _ = ledger.journal_intent(
        con, case_id=first.case_id, case_inputs=first.case_inputs, build=build
    )
    other.close()
    assert second.attempt == 2
    assert raced, "a concurrent journal was allowed to read the ordinal mid-transaction"


def _replaced(attempt: ledger.Attempt, ordinal: int) -> ledger.Attempt:
    attempt_id = identity.attempt_id(attempt.case_id, ordinal)
    return ledger.Attempt(
        **{
            **{name: getattr(attempt, name) for name in ledger.Attempt.__dataclass_fields__},
            "attempt": ordinal,
            "job_name": campaign.job_name_for(SUITE, attempt.case_id, ordinal),
            "result_prefix": f"gs://results/{SUITE}/b/{attempt_id}/",
        }
    )


def test_a_case_with_a_successful_attempt_is_not_resubmitted(
    submitted: tuple[sqlite3.Connection, Plan, Case, campaign.ImageSet],
) -> None:
    """Re-measuring is `reps` or an explicit flag, never an implicit repeat."""
    con, plan, case, images = submitted
    attempt = submit(con, plan, case, images)
    ledger.set_state(con, attempt.attempt_id, "SUCCEEDED")

    with pytest.raises(ledger.CampaignError, match="already has a successful attempt"):
        submit(con, plan, case, images)
    assert submit(con, plan, case, images, repeat=True).attempt == 2


def test_intent_is_durable_before_the_provider_is_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = ledger.open_ledger(str(tmp_path / "campaign.db"), suite=SUITE)
    plan = loaded_plan()

    def observe(*_args: object, **_kwargs: object) -> tuple[str, str | None]:
        row = con.execute("SELECT state, request_json, settled_at FROM attempts").fetchone()
        assert row["state"] == "SUBMITTING"
        assert json.loads(row["request_json"])["taskGroups"]
        assert row["settled_at"] is None
        return "SUBMITTED", None

    monkeypatch.setattr(batch_client, "ensure_job", observe)
    attempt = submit(con, plan, any_case(plan), image_set(tmp_path))

    row = con.execute("SELECT * FROM attempts").fetchone()
    assert (row["state"], row["settled_at"]) == ("SUBMITTED", None)
    ledger.set_state(con, attempt.attempt_id, "FAILED", "the machine went away")
    settled = con.execute("SELECT state, state_detail, settled_at FROM attempts").fetchone()
    assert settled["state_detail"] == "the machine went away"
    assert settled["settled_at"] is not None


class ExistingClient:
    def __init__(self, job: batch_v1.Job) -> None:
        self.job = job

    def create_job(self, **_kwargs: object) -> batch_v1.Job:
        raise AlreadyExists("exists")  # type: ignore[no-untyped-call]

    def get_job(self, **_kwargs: object) -> batch_v1.Job:
        return self.job


def rendered_request(tmp_path: Path, **overrides: Any) -> tuple[ledger.Attempt, dict[str, Any]]:
    plan = loaded_plan()
    case = any_case(plan, signed=bool(overrides.pop("signed", False)))
    images = image_set(tmp_path)
    _, _, build = campaign.planned_attempt(case, context(plan, case, images, **overrides))
    attempt, request = build(1)
    return attempt, cast(dict[str, Any], json.loads(request))


def test_a_dry_run_renders_every_planned_attempt_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The rendered request is what a unit test can check of a provider contract."""
    image_set(tmp_path)
    state = tmp_path / "campaign.db"
    assert (
        campaign.main(
            [
                "--state",
                str(state),
                "submit",
                "--suite",
                SUITE,
                "--plan",
                str(PLAN_PATH),
                "--project",
                "p",
                "--location",
                "us-east1",
                "--results-bucket",
                "results",
                "--image-set",
                str(tmp_path / "images.json"),
                "--secret-resource",
                AUTH_SECRET,
                "--anonymous-worker-sa",
                "anon@example.test",
                "--authenticated-worker-sa",
                "auth@example.test",
                "--dry-run",
            ]
        )
        == 0
    )
    rendered = capsys.readouterr().out.splitlines()
    cases = len(loaded_plan().cases)
    # The hinted s3-fast-list row waits on a bootstrap listing, and this plan's
    # own unhinted `list` row is one: the slot is paid by that measurement's
    # artifact, so no standalone preparation is minted and the bucket is listed
    # once rather than twice with byte-identical argv. Its `ks-split` runs inside
    # the hinted attempt and books nothing.
    assert (
        rendered[-1]
        == f"campaign: {cases} plan row(s) expand to {cases - 1} attempt(s) and 1 slot(s)"
    )
    # One rendered line per expanded step, and the expansion is the plan's own
    # rows: nothing was added behind them.
    assert len(rendered[:-1]) == cases
    slots = [line for line in rendered if line.startswith("slot ")]
    assert slots == [
        "slot dry-run/1 s3-fast-list list-hinted measurement awaiting step 1 (s3-fast-list list)"
    ]
    assert not state.exists()


def test_an_existing_job_is_submitted_only_when_it_matches_recorded_intent(
    tmp_path: Path,
) -> None:
    """`SUBMITTED` covers what we created and what we found; `NOT_CREATED` covers the rest."""
    attempt, request = rendered_request(tmp_path)
    name = f"projects/p/locations/us-east1/jobs/{attempt.job_name}"
    matching = batch_client._job_from_dict(request)
    matching.name = name

    found = batch_client.ensure_job(
        "p", "us-east1", attempt.job_name, request, client=cast(Any, ExistingClient(matching))
    )
    assert found[0] == "SUBMITTED"

    different = batch_client._job_from_dict(request)
    different.name = name
    different.task_groups[0].task_spec.runnables[0].container.commands.append("--extra")
    state, detail = batch_client.ensure_job(
        "p", "us-east1", attempt.job_name, request, client=cast(Any, ExistingClient(different))
    )
    assert state == "NOT_CREATED"
    assert detail is not None


def test_a_refused_creation_is_not_created(tmp_path: Path) -> None:
    attempt, request = rendered_request(tmp_path)

    class Refusing:
        def create_job(self, **_kwargs: object) -> batch_v1.Job:
            raise BadRequest("machine type unavailable")  # type: ignore[no-untyped-call]

    state, detail = batch_client.ensure_job(
        "p", "us-east1", attempt.job_name, request, client=cast(Any, Refusing())
    )
    assert state == "NOT_CREATED"
    assert detail is not None and "machine type unavailable" in detail


def test_the_rendered_job_carries_the_attempt_identity_and_its_prefix(tmp_path: Path) -> None:
    """The worker is told which row it is, so `result.json` can name it back."""
    attempt, request = rendered_request(tmp_path)
    commands = request["taskGroups"][0]["taskSpec"]["runnables"][0]["container"]["commands"]
    pairs = dict(zip(commands[::2], commands[1::2], strict=True))
    assert pairs["--attempt-id"] == attempt.attempt_id
    assert pairs["--case-id"] == attempt.case_id
    assert pairs["--destination"] == attempt.result_prefix
    assert pairs["--config"] == attempt.config
    assert request["labels"] == {"suite": SUITE}
    # An unsigned case has no credential anywhere in its request.
    assert AUTH_SECRET not in json.dumps(request)


def test_a_signing_case_carries_one_secret_and_the_signing_identity(tmp_path: Path) -> None:
    attempt, request = rendered_request(tmp_path, signed=True)
    task_spec = request["taskGroups"][0]["taskSpec"]
    assert task_spec["environment"]["secretVariables"] == {CREDENTIAL_ENV_VAR: AUTH_SECRET}
    assert request["allocationPolicy"]["serviceAccount"]["email"] == "auth@example.test"
    assert attempt.auth_role is not None


def test_a_signing_case_without_a_signing_account_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ledger.CampaignError):
        rendered_request(tmp_path, signed=True, authenticated_worker_sa=None)


def test_a_job_name_that_batch_cannot_take_is_refused() -> None:
    name = campaign.job_name_for(SUITE, "aws-cli.9f300cc4d2b1", 2)
    assert name == f"{SUITE}-aws-cli-9f300cc4d2b1-s2"
    with pytest.raises(ledger.CampaignError):
        campaign.job_name_for("s" * 40, "aws-cli.9f300cc4d2b1", 1)
    with pytest.raises(ledger.CampaignError):
        campaign.job_name_for(SUITE, "aws_cli.9f300cc4d2b1", 1)


def test_a_ledger_whose_schema_version_is_unknown_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "campaign.db"
    ledger.open_ledger(str(path), suite=SUITE).close()
    con = sqlite3.connect(path)
    con.execute("UPDATE meta SET schema_version = ?", (ledger.SCHEMA_VERSION + 1,))
    con.commit()
    con.close()
    with pytest.raises(ledger.CampaignError):
        ledger.open_ledger(str(path))
    # Unknown in both directions: a version this code cannot read is not read.
    with pytest.raises(ledger.CampaignError):
        ledger.open_ledger(str(path), readonly=True)


def test_a_superseded_ledger_still_opens_for_reading_and_never_for_writing(
    tmp_path: Path,
) -> None:
    """Bumping the schema must not lock away the campaigns that came before it.

    Every settled ledger in the state directory is schema 1, and `status`,
    `report`, `verify` and `prune` only ask them what happened. The columns
    schema 2 added are projected in as NULL, which is what they mean: a slot
    booked before producer specs named one attempt id and disqualified nothing.
    """
    path = tmp_path / "campaign.db"
    ledger.open_ledger(str(path), suite=SUITE).close()
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE old AS SELECT group_id, slot, tool, purpose, known_inputs, "
        "awaiting, state, became, recorded_at, settled_at FROM pending;"
        "DROP TABLE pending;"
        "ALTER TABLE old RENAME TO pending;"
        "INSERT INTO pending VALUES ('g1', 1, 's3-fast-list', 'measurement', '{}', "
        "'s3-fast-list.abcdef012345.s1', 'BLOCKED', NULL, '2026-08-17T21:38:32+00:00', NULL);"
        "UPDATE meta SET schema_version = 1;"
    )
    con.commit()
    con.close()

    with pytest.raises(ledger.CampaignError, match="does not migrate"):
        ledger.open_ledger(str(path))
    reading = ledger.open_ledger(str(path), readonly=True)
    slot = ledger.pending_rows(reading)[0]
    assert (slot["producer"], slot["disqualified"]) == (None, None)
    assert slot["awaiting"] == "s3-fast-list.abcdef012345.s1"
    # And the readers that matter can still ask a slot their question.
    assert ledger.slot_owed_reason(reading, slot) == (
        "no attempt in this group produces what it consumes"
    )
    reading.close()


def test_a_group_id_is_unique_within_an_accumulating_file(
    submitted: tuple[sqlite3.Connection, Plan, Case, campaign.ImageSet],
) -> None:
    con, plan, case, images = submitted
    minted = ledger.mint_group_id(con)
    submit(con, plan, case, images, group_id=minted)
    assert ledger.mint_group_id(con, "second-launch") == "second-launch"
    with pytest.raises(ledger.CampaignError):
        ledger.mint_group_id(con, minted)
    # The minted form is a timestamp an operator can type, suffixed rather than
    # reused when two launches land in one second.
    assert ledger.mint_group_id(con).startswith(minted.rsplit("-", 1)[0][:9])


def test_retry_leaves_other_groups_and_rate_cases_alone(
    submitted: tuple[sqlite3.Connection, Plan, Case, campaign.ImageSet],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    con, plan, case, images = submitted
    mine = submit(con, plan, case, images, group_id="mine")
    sampled = submit(con, plan, plan.cases[1], images, group_id="mine")
    theirs = submit(con, plan, plan.cases[2], images, group_id="theirs")
    for attempt in (mine, sampled, theirs):
        ledger.set_state(con, attempt.attempt_id, "FAILED", "settled failure")
    con.execute("UPDATE attempts SET statistic='rate' WHERE attempt_id=?", (sampled.attempt_id,))
    retried: list[str] = []

    def observe(con: sqlite3.Connection, row: sqlite3.Row, **kwargs: object) -> ledger.Attempt:
        retried.append(row["attempt_id"])
        return mine

    monkeypatch.setattr(campaign, "retry_attempt", observe)
    monkeypatch.setattr(campaign, "load_image_set", lambda *a, **k: images)
    campaign.cmd_retry(
        cast(
            argparse.Namespace,
            SimpleNamespace(
                state=str(tmp_path / "campaign.db"),
                group="mine",
                results_bucket="results",
                image_set="unused",
                **vars(_provider_namespace()),
            ),
        )
    )
    assert retried == [mine.attempt_id]


def test_one_rows_retry_refusal_does_not_abort_the_sweep(
    submitted: tuple[sqlite3.Connection, Plan, Case, campaign.ImageSet],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first live sweep died on a case whose later ordinal had succeeded,
    leaving a preempted sibling behind it unretried — a refusal answers one
    row, never the rows after it."""
    con, plan, case, images = submitted
    answered = submit(con, plan, case, images, group_id="mine")
    preempted = submit(con, plan, plan.cases[1], images, group_id="mine")
    for attempt in (answered, preempted):
        ledger.set_state(con, attempt.attempt_id, "FAILED", "spot reclaimed the machine")
    retried: list[str] = []

    def observe(con: sqlite3.Connection, row: sqlite3.Row, **kwargs: object) -> ledger.Attempt:
        if row["attempt_id"] == answered.attempt_id:
            raise ledger.CampaignError("already has a successful attempt")
        retried.append(row["attempt_id"])
        return preempted

    monkeypatch.setattr(campaign, "retry_attempt", observe)
    monkeypatch.setattr(campaign, "load_image_set", lambda *a, **k: images)
    campaign.cmd_retry(
        cast(
            argparse.Namespace,
            SimpleNamespace(
                state=str(tmp_path / "campaign.db"),
                group="mine",
                results_bucket="results",
                image_set="unused",
                **vars(_provider_namespace()),
            ),
        )
    )
    assert retried == [preempted.attempt_id]


def _provider_namespace() -> SimpleNamespace:
    return SimpleNamespace(
        project="p",
        location="us-east1",
        anonymous_worker_sa="anon@example.test",
        authenticated_worker_sa="auth@example.test",
        network=None,
        subnetwork=None,
        zone=None,
        provisioning="SPOT",
        secret_resource=AUTH_SECRET,
    )


def test_a_retry_that_would_change_the_frozen_request_is_refused(
    submitted: tuple[sqlite3.Connection, Plan, Case, campaign.ImageSet], tmp_path: Path
) -> None:
    """A retry re-runs an attempt; changing the image set is a new campaign."""
    con, plan, case, images = submitted
    attempt = submit(con, plan, case, images)
    ledger.set_state(con, attempt.attempt_id, "FAILED", "settled failure")
    row = con.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt.attempt_id,)).fetchone()

    moved = image_set_document()
    tools = cast(dict[str, dict[str, str]], moved["tools"])
    tools[case.tool]["tool_version"] = "2.0"
    with pytest.raises(ledger.CampaignError, match="new campaign, not a retry"):
        campaign.retry_attempt(
            con,
            row,
            suite=SUITE,
            image_set=image_set(tmp_path / "moved", moved),
            results_bucket="results",
            options=options(),
        )


def test_a_retry_replays_the_deadline_its_ledger_was_frozen_under(
    submitted: tuple[sqlite3.Connection, Plan, Case, campaign.ImageSet],
) -> None:
    """The provider deadline is a safety net, and widening it is not a new campaign.

    A fresh launch gets the current flat slack; an in-flight ledger's frozen
    request keeps the figure it was launched with, or every attempt frozen
    before the change would be refused as "a new campaign".
    """
    con, plan, case, images = submitted
    attempt = submit(con, plan, case, images)
    ledger.set_state(con, attempt.attempt_id, "FAILED", "settled failure")
    stored = "SELECT request_json FROM attempts WHERE attempt_id=?"
    frozen = json.loads(con.execute(stored, (attempt.attempt_id,)).fetchone()[0])
    assert campaign.request_max_run_duration(frozen) == (
        f"{attempt.timeout_s + int(options().term_grace) + campaign.DEADLINE_SLACK_S}s"
    )

    # The same row as an older, narrower slack would have frozen it.
    frozen["taskGroups"][0]["taskSpec"]["maxRunDuration"] = "999s"
    con.execute(
        "UPDATE attempts SET request_json=? WHERE attempt_id=?",
        (json.dumps(frozen), attempt.attempt_id),
    )
    row = con.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt.attempt_id,)).fetchone()
    retried = campaign.retry_attempt(
        con, row, suite=SUITE, image_set=images, results_bucket="results", options=options()
    )
    replayed = json.loads(con.execute(stored, (retried.attempt_id,)).fetchone()[0])
    assert campaign.request_max_run_duration(replayed) == "999s"


def test_accept_failure_records_which_failure_was_accepted(
    submitted: tuple[sqlite3.Connection, Plan, Case, campaign.ImageSet], tmp_path: Path
) -> None:
    con, plan, case, images = submitted
    attempt = submit(con, plan, case, images)
    ledger.set_state(con, attempt.attempt_id, "NOT_CREATED", "Forbidden: no quota")
    con.close()

    campaign.cmd_accept_failure(
        cast(
            argparse.Namespace,
            SimpleNamespace(
                state=str(tmp_path / "campaign.db"), attempt=attempt.attempt_id, slot=None
            ),
        )
    )
    reopened = ledger.open_ledger(str(tmp_path / "campaign.db"), readonly=True)
    row = reopened.execute("SELECT state, state_detail FROM attempts").fetchone()
    assert row["state"] == "ACCEPTED"
    assert "NOT_CREATED" in row["state_detail"] and "no quota" in row["state_detail"]


def test_cancel_and_prune_refuse_to_run_over_a_whole_file() -> None:
    """An unscoped delete over an accumulating file is the one mistake with no undo."""
    for verb in ("cancel", "prune", "retry"):
        with pytest.raises(SystemExit):
            campaign.parse_args([verb])


def test_an_image_set_without_slices_cannot_identify_a_case(tmp_path: Path) -> None:
    document = image_set_document()
    tools = cast(dict[str, dict[str, str]], document["tools"])
    del tools["aws-cli"]["tool_slice_sha256"]
    with pytest.raises(ledger.CampaignError):
        image_set(tmp_path, document)

    disagreeing = image_set_document()
    tools = cast(dict[str, dict[str, str]], disagreeing["tools"])
    tools["aws-cli"]["platform_sha256"] = "1" * 64
    with pytest.raises(ledger.CampaignError):
        image_set(tmp_path / "other", disagreeing)


HINTED = """
spec_version: 2
bucket: b
region: us-east-1
defaults:
  reps: 1
  timeout_s: 3600
  vcpus: 2
  memory_gb: 8
tools:
  s3-fast-list:
    cases:
      - {mode: list-hinted, concurrency: 8, segments: 16}
"""


def hinted_capsule() -> Any:
    """The real s3-fast-list capsule, untouched.

    The row states `segments`, the axis the capsule declares `Stated`, and the
    hinted mode carries it into the `ks-split` its own attempt runs inline. The
    chain, the modes and the artifact validators all stay the capsule's, so these
    tests still fail when its `REQUIRES` moves.
    """
    return bench.load_capsule("s3-fast-list")


def hinted_plan(tmp_path: Path, body: str = HINTED, *, tools: tuple[str, ...] = ()) -> Plan:
    """A plan asking for the one shipped mode with a declared prerequisite chain.

    Loaded against the real capsule, so the arithmetic below is a drift guard on
    `s3-fast-list`'s own `REQUIRES` rather than on a fixture repeating it.
    """
    path = tmp_path / "b.yaml"
    path.write_text(body, encoding="utf-8")
    return Plan.load(
        path,
        default_modes={},
        instances={(2, 8): "n4-standard-2"},
        heap=bench.HeapConfig(percent=75),
        adapters={
            "s3-fast-list": hinted_capsule(),
            **{tool: bench.load_capsule(tool) for tool in tools},
        },
    )


def test_the_suite_filter_quotes_its_value() -> None:
    """The real API 400s an unquoted hyphenated label value — the first live
    polling pass proved it — so the filter must always quote the suite."""

    class Client:
        def list_jobs(self, *, request: dict[str, str], **_kwargs: object) -> list[batch_v1.Job]:
            assert request["filter"] == 'labels.suite="c-2026-08-17-x"'
            return []

    assert (
        batch_client.list_job_states(
            "p",
            "us-east1",
            "c-2026-08-17-x",
            client=Client(),  # type: ignore[arg-type]
        )
        == {}
    )


class Evidence:
    """The results bucket, as much of it as a settling preparation is read from."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def publish(self, result_prefix: str, name: str, content: bytes) -> str:
        """Add one file to an attempt's sink, keeping whatever it published before."""
        marker = f"{result_prefix}result.json"
        digest = hashlib.sha256(content).hexdigest()
        published = (
            json.loads(self.objects[marker])["native_manifest"] if marker in self.objects else {}
        )
        self.objects[marker] = json.dumps({"native_manifest": {**published, name: digest}}).encode()
        self.objects[f"{result_prefix}native/{name}"] = content
        return digest

    def download(self, uri: str) -> bytes:
        return self.objects[uri]


SWEEP = """
spec_version: 2
bucket: b
region: us-east-1
defaults:
  reps: 1
  timeout_s: 3600
  vcpus: 2
  memory_gb: 8
tools:
  s3-fast-list:
    cases:
      - {mode: list-hinted, concurrency: 8, segments: 8}
      - {mode: list-hinted, concurrency: 8, segments: 16}
      - {mode: list}
"""
"""Two hinted cells and the unhinted arm they can be paid by — with the consumers
written first, because plan row order is exactly what must not decide this."""


def hinted_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    body: str = HINTED,
    con: sqlite3.Connection | None = None,
    group_id: str = "g20260817-000000",
    tools: tuple[str, ...] = (),
) -> sqlite3.Connection:
    monkeypatch.setattr(batch_client, "ensure_job", lambda *a, **k: ("SUBMITTED", None))
    plan = hinted_plan(tmp_path, body, tools=tools)
    con = con or ledger.open_ledger(str(tmp_path / "campaign.db"), suite=SUITE)
    launch = campaign.Launch(con, SUITE, group_id, plan, image_set(tmp_path), "results", options())
    launch.run(campaign.expand_launch(plan.cases, plan.adapters))
    return con


def test_a_declared_chain_expands_into_one_preparation_and_one_slot(tmp_path: Path) -> None:
    """`list-hinted` requires `list`, and the split it also needs books nothing:
    an inline setup exec runs inside the measurement's own attempt."""
    plan = hinted_plan(tmp_path)
    steps = campaign.expand_launch(plan.cases, plan.adapters)

    assert [(step.case.mode, step.case.purpose, step.waits_for) for step in steps] == [
        ("list", "preparation", None),
        ("list-hinted", "measurement", 0),
    ]
    rendered = campaign.render_launch(
        steps,
        suite=SUITE,
        group_id="dry-run",
        plan=plan,
        image_set=image_set(tmp_path),
        results_bucket="results",
        options=options(),
    )
    assert len(rendered) == 2
    assert rendered[1].startswith("slot dry-run/1 s3-fast-list list-hinted measurement awaiting ")


def test_a_preparation_is_identified_by_content_and_not_by_the_consumer(tmp_path: Path) -> None:
    """It answers "do we already have this artifact?", so the box and the role stay out."""
    plan = hinted_plan(tmp_path)
    (preparation, _) = campaign.expand_launch(plan.cases, plan.adapters)
    images = image_set(tmp_path)
    minted, inputs = campaign.case_identity(
        preparation.case,
        auth_role=None,
        target_bucket=plan.bucket,
        target_region=plan.region,
        location="us-east1",
        tool_slice=images.image_for("s3-fast-list")["tool_slice_sha256"],
        platform=PLATFORM,
    )

    environment = json.loads(inputs)["environment"]
    assert set(environment) == {"target_bucket", "target_region", "target_prefix"}
    # The prerequisite runs the capsule's own mode at the capsule's own config:
    # the consumer's concurrency 8 is an axis of the hinted listing, not of this.
    assert json.loads(inputs)["config"] == {"mode": "list"}
    # A bigger box produces the same hints, so it is the same preparation.
    on_a_bigger_box = replace(
        preparation.case, resources=replace(preparation.case.resources, vcpus=4, memory_gb=16)
    )
    assert (
        campaign.case_identity(
            on_a_bigger_box,
            auth_role="fixture-role",
            target_bucket=plan.bucket,
            target_region=plan.region,
            location="europe-west1",
            tool_slice=images.image_for("s3-fast-list")["tool_slice_sha256"],
            platform=PLATFORM,
        )[0]
        == minted
    )


def test_a_settled_preparation_resolves_the_measurement_that_waited_on_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Digest the artifact, mint the identity that was waiting on it, submit."""
    con = hinted_launch(tmp_path, monkeypatch)
    evidence = Evidence()
    monkeypatch.setattr(gcs, "download_bytes", evidence.download)
    listing = ledger.Attempt.from_row(attempt_row(con, "list"))
    assert [row["state"] for row in ledger.pending_rows(con)] == ["BLOCKED"]

    keyspace = evidence.publish(listing.result_prefix, "keyspace.ks", b"a/\nb/\n")
    # A second file in the same sink, which is what a listing publishing its
    # product lands beside its key distribution: the chain binds the artifact
    # `REQUIRES` named, not the one the sink happens to hold.
    evidence.publish(listing.result_prefix, "listing.parquet", b"PAR1" * 64)
    ledger.set_state(con, listing.attempt_id, "SUCCEEDED")
    campaign.settle_dependents(con, listing, "SUCCEEDED", suite=SUITE)

    hinted = ledger.Attempt.from_row(attempt_row(con, "list-hinted"))
    slot = ledger.pending_rows(con)[0]
    assert (slot["state"], slot["became"]) == ("RESOLVED", hinted.attempt_id)
    # The measurement consumes the key distribution directly: the cut points it
    # lists under are made by its own inline setup exec, so no second attempt
    # stands between the listing and the hinted run.
    assert (hinted.input_artifact_sha256, hinted.produced_by) == (keyspace, listing.attempt_id)
    # What the worker is told to stage, and what it must digest to before use.
    request = json.loads(
        con.execute(
            "SELECT request_json FROM attempts WHERE attempt_id=?", (hinted.attempt_id,)
        ).fetchone()["request_json"]
    )
    assert campaign.request_argument(request, "--input-artifact") == (
        f"{listing.result_prefix}native/keyspace.ks"
    )
    assert campaign.request_argument(request, "--input-artifact-sha256") == keyspace


def test_an_artifact_is_validated_against_the_mode_that_produced_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A capsule with two producers has two structures, and neither check reads
    the other's file: the validator is looked up by producing mode, and a mode
    with no entry has nothing structural to say."""
    evidence = Evidence()
    # An empty first cut point: a full-range serial scan beside every segment.
    evidence.publish("gs://results/prep/", "hints.input", b"\nz/\n")
    monkeypatch.setattr(gcs, "download_bytes", evidence.download)
    uri = "gs://results/prep/native/hints.input"
    with pytest.raises(ledger.CampaignError, match="first cut point is empty"):
        campaign.validate_artifact("s3-fast-list", "ks-split", uri)
    # The same bytes under the mode that publishes a key distribution: the capsule
    # declares no check for it, so there is nothing to refuse.
    campaign.validate_artifact("s3-fast-list", "list", uri)


def test_a_preparation_with_no_consumable_artifact_fails_and_abandons_what_awaited_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A preparation whose sink does not hold the named artifact must not resolve
    the measurement waiting on it — and the slot survives the failure, because a
    retry may still pay it.

    One file under a plausible name is the case a count could never catch: the
    listing published something, and it is not the key distribution the hinted
    run is compiled against.
    """
    con = hinted_launch(tmp_path, monkeypatch)
    evidence = Evidence()
    monkeypatch.setattr(gcs, "download_bytes", evidence.download)
    listing = ledger.Attempt.from_row(attempt_row(con, "list"))
    evidence.publish(listing.result_prefix, "listing.parquet", b"PAR1")
    ledger.set_state(con, listing.attempt_id, "SUCCEEDED")
    campaign.settle_dependents(con, listing, "SUCCEEDED", suite=SUITE)

    row = con.execute(
        "SELECT state, state_detail FROM attempts WHERE attempt_id=?", (listing.attempt_id,)
    ).fetchone()
    assert row["state"] == "FAILED" and "keyspace.ks" in row["state_detail"]
    assert ledger.pending_rows(con)[0]["state"] == "BLOCKED"

    ledger.set_state(con, listing.attempt_id, "ACCEPTED", "accepted FAILED")
    campaign.settle_dependents(con, listing, "ACCEPTED", suite=SUITE)
    assert ledger.pending_rows(con)[0]["state"] == "ABANDONED"


def test_a_preparation_another_launch_made_is_bound_only_when_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reuse is free within a launch and a decision across them: the corpus moves."""
    con = hinted_launch(tmp_path, monkeypatch)
    evidence = Evidence()
    monkeypatch.setattr(gcs, "download_bytes", evidence.download)
    listing = ledger.Attempt.from_row(attempt_row(con, "list"))
    keyspace = evidence.publish(listing.result_prefix, "keyspace.ks", b"a/\nb/\n")
    ledger.set_state(con, listing.attempt_id, "SUCCEEDED")

    plan = hinted_plan(tmp_path)
    steps = campaign.expand_launch(plan.cases, plan.adapters)
    again = campaign.Launch(
        con, SUITE, "g20260817-000001", plan, image_set(tmp_path), "results", options()
    )
    with pytest.raises(ledger.CampaignError, match="reusing a preparation across launches"):
        again.run(steps)

    reusing = campaign.Launch(
        con,
        SUITE,
        "g20260817-000002",
        plan,
        image_set(tmp_path),
        "results",
        options(),
        reuse_preparations=True,
    )
    reusing.run(steps)
    # No second listing was submitted, and the measurement it unblocks is an
    # attempt rather than a slot: its identity is complete the moment the digest
    # is known.
    assert len(ledger.attempt_rows(con, case_id=listing.case_id)) == 1
    hinted = ledger.Attempt.from_row(attempt_row(con, "list-hinted", group_id="g20260817-000002"))
    assert (hinted.input_artifact_sha256, hinted.produced_by) == (keyspace, listing.attempt_id)


def test_a_swath_job_names_its_heap_variable_once_and_the_worker_takes_it(
    tmp_path: Path,
) -> None:
    """The seam this closed: the plan rendered JAVA_TOOL_OPTIONS into `--case-env`
    while swath's capsule rendered it too, and the worker refuses two sources for
    one key — so every swath attempt died before its subject started.
    """
    plan = loaded_plan()
    case = next(c for c in plan.cases if c.tool == "swath")
    images = image_set(tmp_path)
    _, _, build = campaign.planned_attempt(case, context(plan, case, images))
    _, request = build(1)
    argv = json.loads(request)["taskGroups"][0]["taskSpec"]["runnables"][0]["container"]["commands"]

    args = measure.parse_args(argv)
    # The capsule the image would carry, read from the tree it is built from.
    command, functional_env = adapters.compile_command(
        adapters.adapter_dir_for(args.tool, str(ROOT / "tools")),
        args.tool,
        mode=args.mode,
        bucket=args.bucket,
        region=args.region,
        prefix=args.prefix,
        signed=args.auth_role is not None,
        config=json.loads(args.config),
        sink_dir=str(tmp_path / "native"),
        artifact_path="",
        visible_memory_gb=float(args.container_memory_gb or args.memory_gb),
        heap_percent=HEAP_PERCENT,
    )
    assert measure.validate_environment_inputs(functional_env) is None
    assert functional_env["JAVA_TOOL_OPTIONS"] == f"-XX:MaxRAMPercentage={HEAP_PERCENT}"
    # The subject's whole environment, assembled as the worker assembles it: the
    # share reaches the JVM once, from the capsule that knows the flag.
    env = {
        **measure.SUBJECT_ENV,
        "AWS_REGION": args.region,
        "AWS_DEFAULT_REGION": args.region,
        **functional_env,
    }
    assert [name for name in env if "JAVA" in name] == ["JAVA_TOOL_OPTIONS"]
    assert not [token for token in (*argv, *command) if "MaxRAMPercentage" in token]


def test_a_slot_resolves_once_however_many_passes_see_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two passes over one settled preparation must not submit one measurement twice."""
    con = hinted_launch(tmp_path, monkeypatch)
    evidence = Evidence()
    monkeypatch.setattr(gcs, "download_bytes", evidence.download)
    listing = ledger.Attempt.from_row(attempt_row(con, "list"))
    evidence.publish(listing.result_prefix, "keyspace.ks", b"a/\nb/\n")
    stale = ledger.blocked_slots(con, listing.group_id)[0]

    ledger.set_state(con, listing.attempt_id, "SUCCEEDED")
    campaign.settle_dependents(con, listing, "SUCCEEDED", suite=SUITE)
    hinted = ledger.Attempt.from_row(attempt_row(con, "list-hinted"))

    # The row a concurrent pass read before this one claimed the slot.
    inbound = campaign.Inbound(
        artifact_sha256=hinted.input_artifact_sha256,
        produced_by=listing.attempt_id,
        artifact_uri=f"{listing.result_prefix}native/keyspace.ks",
    )
    assert campaign.resolve_slot(con, stale, inbound, suite=SUITE) is None
    assert [row["attempt_id"] for row in ledger.attempt_rows(con, case_id=hinted.case_id)] == [
        hinted.attempt_id
    ]


def test_a_slot_whose_case_was_already_prepared_binds_it_rather_than_deadlocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ledger refuses a second attempt of a settled success, so the slot must
    bind the one that exists — a repeat of the listing produces the same hints."""
    con = hinted_launch(tmp_path, monkeypatch)
    evidence = Evidence()
    monkeypatch.setattr(gcs, "download_bytes", evidence.download)
    listing = ledger.Attempt.from_row(attempt_row(con, "list"))
    evidence.publish(listing.result_prefix, "keyspace.ks", b"a/\nb/\n")
    ledger.set_state(con, listing.attempt_id, "SUCCEEDED")
    campaign.settle_dependents(con, listing, "SUCCEEDED", suite=SUITE)
    hinted = ledger.Attempt.from_row(attempt_row(con, "list-hinted"))
    ledger.set_state(con, hinted.attempt_id, "SUCCEEDED")

    plan = hinted_plan(tmp_path)
    again = campaign.Launch(
        con,
        SUITE,
        "g20260817-000001",
        plan,
        image_set(tmp_path),
        "results",
        options(),
        repeat=True,
    )
    again.run(campaign.expand_launch(plan.cases, plan.adapters))
    repeated = ledger.Attempt.from_row(attempt_row(con, "list", group_id="g20260817-000001"))
    evidence.publish(repeated.result_prefix, "keyspace.ks", b"a/\nb/\n")
    ledger.set_state(con, repeated.attempt_id, "SUCCEEDED")
    campaign.settle_dependents(con, repeated, "SUCCEEDED", suite=SUITE)

    slot = ledger.pending_rows(con, group_id="g20260817-000001")[0]
    assert (slot["state"], slot["became"]) == ("RESOLVED", hinted.attempt_id)
    # No second attempt of a case that has already succeeded: the slot bound the
    # one that exists rather than asking the ledger for a run it refuses.
    assert len(ledger.attempt_rows(con, case_id=hinted.case_id)) == 1


def attempt_row(
    con: sqlite3.Connection,
    mode: str,
    *,
    group_id: str = "g20260817-000000",
    tool: str = "s3-fast-list",
) -> sqlite3.Row:
    row = con.execute(
        "SELECT * FROM attempts WHERE mode=? AND group_id=? AND tool=? "
        "ORDER BY attempt DESC LIMIT 1",
        (mode, group_id, tool),
    ).fetchone()
    assert row is not None, f"no {tool} {mode} attempt in {group_id}"
    return cast(sqlite3.Row, row)


def test_a_retry_pays_the_slot_its_failed_producer_left_owed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slot survives its producer's failure so that a retry can still pay it —
    which is what `settle_dependents` promises and what nominating one attempt id
    cannot deliver: the retry settles under a new ordinal the slot never named.
    """
    con = hinted_launch(tmp_path, monkeypatch)
    evidence = Evidence()
    monkeypatch.setattr(gcs, "download_bytes", evidence.download)
    failed = attempt_row(con, "list")
    ledger.set_state(con, failed["attempt_id"], "FAILED", "preempted")
    campaign.settle_dependents(con, ledger.Attempt.from_row(failed), "FAILED", suite=SUITE)
    assert ledger.pending_rows(con)[0]["state"] == "BLOCKED"

    replacement = campaign.retry_attempt(
        con,
        failed,
        suite=SUITE,
        image_set=image_set(tmp_path),
        results_bucket="results",
        options=options(),
    )
    keyspace = evidence.publish(replacement.result_prefix, "keyspace.ks", b"a/\nb/\n")
    ledger.set_state(con, replacement.attempt_id, "SUCCEEDED")
    campaign.settle_dependents(con, replacement, "SUCCEEDED", suite=SUITE)

    hinted = ledger.Attempt.from_row(attempt_row(con, "list-hinted"))
    slot = ledger.pending_rows(con)[0]
    assert (slot["state"], slot["became"]) == ("RESOLVED", hinted.attempt_id)
    assert (hinted.input_artifact_sha256, hinted.produced_by) == (
        keyspace,
        replacement.attempt_id,
    )


def test_a_sweep_binds_the_measurement_arm_and_lists_the_bucket_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unhinted arm publishes exactly the artifact the hinted chain needs, so
    no standalone preparation is minted and both cells of the sweep bind that one
    producer — one corpus snapshot, one listing, and a measurement is still a
    measurement.
    """
    plan = hinted_plan(tmp_path, SWEEP)
    steps = campaign.expand_launch(plan.cases, plan.adapters)
    # The producer runs first though the plan writes it last: a launch dying
    # between booking a slot and journaling its candidate must not leave a slot
    # nothing in its group can pay.
    assert [(step.case.mode, step.case.purpose, step.waits_for) for step in steps] == [
        ("list", "measurement", None),
        ("list-hinted", "measurement", 0),
        ("list-hinted", "measurement", 0),
    ]

    con = hinted_launch(tmp_path, monkeypatch, body=SWEEP)
    evidence = Evidence()
    monkeypatch.setattr(gcs, "download_bytes", evidence.download)
    listing = ledger.Attempt.from_row(attempt_row(con, "list"))
    keyspace = evidence.publish(listing.result_prefix, "keyspace.ks", b"a/\nb/\n")
    ledger.set_state(con, listing.attempt_id, "SUCCEEDED")
    campaign.settle_dependents(con, listing, "SUCCEEDED", suite=SUITE)

    slots = ledger.pending_rows(con)
    assert [slot["state"] for slot in slots] == ["RESOLVED", "RESOLVED"]
    hinted = [ledger.Attempt.from_row(row) for row in ledger.attempt_rows(con)][1:]
    assert {attempt.produced_by for attempt in hinted} == {listing.attempt_id}
    assert {attempt.input_artifact_sha256 for attempt in hinted} == {keyspace}
    # The producer was measured, not prepared: its purpose and its state are
    # untouched by having also paid two slots.
    assert (listing.purpose, attempt_row(con, "list")["state"]) == ("measurement", "SUCCEEDED")


def test_a_slot_is_not_paid_by_a_producer_from_another_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unscoped spec would bind an earlier launch's hours-old bytes silently,
    which is the decision `--reuse-preparations` exists to force an operator to
    make. So the match carries the slot's own group, and the later launch waits
    for its own preparation.
    """
    con = hinted_launch(tmp_path, monkeypatch, body=SWEEP, group_id="g20260817-000000")
    evidence = Evidence()
    monkeypatch.setattr(gcs, "download_bytes", evidence.download)
    listing = ledger.Attempt.from_row(attempt_row(con, "list"))
    evidence.publish(listing.result_prefix, "keyspace.ks", b"a/\nb/\n")
    ledger.set_state(con, listing.attempt_id, "SUCCEEDED")
    campaign.settle_dependents(con, listing, "SUCCEEDED", suite=SUITE)

    hinted_launch(tmp_path, monkeypatch, con=con, group_id="g20260817-000001")
    campaign.resolve_blocked_slots(con, "g20260817-000001", suite=SUITE)
    later = ledger.pending_rows(con, group_id="g20260817-000001")[0]
    assert later["state"] == "BLOCKED"
    # Nothing is owed: its own preparation is live, so it is waiting rather than
    # unsatisfiable.
    assert ledger.slot_owed_reason(con, later) is None

    own = ledger.Attempt.from_row(attempt_row(con, "list", group_id="g20260817-000001"))
    assert own.purpose == "preparation"
    evidence.publish(own.result_prefix, "keyspace.ks", b"c/\nd/\n")
    ledger.set_state(con, own.attempt_id, "SUCCEEDED")
    campaign.settle_dependents(con, own, "SUCCEEDED", suite=SUITE)
    paid = ledger.pending_rows(con, group_id="g20260817-000001")[0]
    assert paid["state"] == "RESOLVED"
    assert (
        ledger.Attempt.from_row(
            attempt_row(con, "list-hinted", group_id="g20260817-000001")
        ).produced_by
        == own.attempt_id
    )


def test_a_succeeded_producer_that_published_nothing_usable_leaves_the_slot_loudly_owed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A measurement's timing number is honest whatever its sink holds, so an
    unusable artifact disqualifies the candidate against the slot and never flips
    the producer to FAILED. What must not happen is the slot blocking quietly
    forever: with every candidate settled and none usable, it says so.
    """
    con = hinted_launch(tmp_path, monkeypatch, body=SWEEP)
    evidence = Evidence()
    monkeypatch.setattr(gcs, "download_bytes", evidence.download)
    listing = ledger.Attempt.from_row(attempt_row(con, "list"))
    evidence.publish(listing.result_prefix, "listing.parquet", b"PAR1")
    ledger.set_state(con, listing.attempt_id, "SUCCEEDED")
    campaign.settle_dependents(con, listing, "SUCCEEDED", suite=SUITE)

    settled = attempt_row(con, "list")
    assert (settled["state"], settled["state_detail"]) == ("SUCCEEDED", None)
    slots = ledger.pending_rows(con)
    assert [slot["state"] for slot in slots] == ["BLOCKED", "BLOCKED"]
    for slot in slots:
        assert listing.attempt_id in json.loads(slot["disqualified"])
        reason = ledger.slot_owed_reason(con, slot)
        assert reason is not None and "keyspace.ks" in reason
    assert report.slot_note(con, slots[0]).count("UNSATISFIABLE") == 1

    campaign.cmd_status(
        argparse.Namespace(state=str(tmp_path / "campaign.db"), group=None, case=None)
    )
    assert capsys.readouterr().out.count("OWED, nothing can pay it") == 2


REPEATED = """
spec_version: 2
bucket: b
region: us-east-1
defaults:
  reps: 2
  timeout_s: 3600
  vcpus: 2
  memory_gb: 8
tools:
  s3-fast-list:
    cases:
      - {mode: list-hinted, concurrency: 8, segments: 16}
      - {mode: list}
"""
"""Two attempts of one producer shape, so exhaustion is a question with an
answer other than "the one attempt someone named"."""


UNRELATED = """
spec_version: 2
bucket: b
region: us-east-1
defaults:
  reps: 1
  timeout_s: 3600
  vcpus: 2
  memory_gb: 8
tools:
  s3-fast-list:
    cases:
      - {mode: list-hinted, concurrency: 8, segments: 8}
      - {mode: list-hinted, concurrency: 8, segments: 16}
      - {mode: list}
  aws-cli:
    cases:
      - {mode: s3api-v2-text}
"""
"""One launch holding a hinted chain and a subject with nothing to do with it —
a group is what went out together, not what depends on what."""


def test_accepting_an_unrelated_failure_leaves_an_owed_slot_owed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Abandoning is the deliberate step an owed slot exists to force.

    A group holds slots owed by shapes the accepted attempt is not one of. If
    `accept-failure` swept those too, an operator declaring `aws-cli`'s failure
    final would silently declare s3-fast-list's hinted arm absent in the same
    call — taking the decision the slot went loud in order to ask for.
    """
    con = hinted_launch(tmp_path, monkeypatch, body=UNRELATED, tools=("aws-cli",))
    state = str(tmp_path / "campaign.db")
    evidence = Evidence()
    monkeypatch.setattr(gcs, "download_bytes", evidence.download)

    # The hinted slots' only candidate succeeds and publishes no `.ks`, so they
    # are owed with nothing left that could pay them.
    listing = ledger.Attempt.from_row(attempt_row(con, "list"))
    evidence.publish(listing.result_prefix, "listing.parquet", b"PAR1")
    ledger.set_state(con, listing.attempt_id, "SUCCEEDED")
    campaign.settle_dependents(con, listing, "SUCCEEDED", suite=SUITE)
    owed = ledger.pending_rows(con)
    assert [slot["state"] for slot in owed] == ["BLOCKED", "BLOCKED"]
    assert all(ledger.slot_owed_reason(con, slot) is not None for slot in owed)

    unrelated = attempt_row(con, "s3api-v2-text", tool="aws-cli")
    ledger.set_state(con, unrelated["attempt_id"], "FAILED", "preempted")
    campaign.cmd_accept_failure(
        argparse.Namespace(state=state, attempt=unrelated["attempt_id"], slot=None)
    )
    assert [slot["state"] for slot in ledger.pending_rows(con)] == ["BLOCKED", "BLOCKED"]

    # And the deliberate step is available: the producer SUCCEEDED, so there is
    # no failure to accept and the slot is named directly.
    campaign.cmd_accept_failure(
        argparse.Namespace(state=state, attempt=None, slot="g20260817-000000/1")
    )
    assert [slot["state"] for slot in ledger.pending_rows(con)] == ["ABANDONED", "BLOCKED"]


def test_abandoning_a_slot_something_could_still_pay_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--slot` declares a measurement impossible, not merely slow. While its
    producer is still live the slot is waiting, and waiting is not absence."""
    con = hinted_launch(tmp_path, monkeypatch, body=SWEEP)
    state = str(tmp_path / "campaign.db")
    with pytest.raises(ledger.CampaignError, match="can still be paid"):
        campaign.cmd_accept_failure(
            argparse.Namespace(state=state, attempt=None, slot="g20260817-000000/1")
        )
    with pytest.raises(ledger.CampaignError, match="no slot"):
        campaign.cmd_accept_failure(
            argparse.Namespace(state=state, attempt=None, slot="g20260817-000000/9")
        )
    assert [slot["state"] for slot in ledger.pending_rows(con)] == ["BLOCKED", "BLOCKED"]


def test_sibling_slots_bind_one_producer_even_when_they_resolve_on_different_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sweep is an A/B over one corpus snapshot, so its cells must share a producer.

    Two candidates of the producing shape settle, and the two slots resolve on
    two separate passes because the provider refuses the second submission once.
    Ordering candidates by `settled_at` is what makes the late slot bind the
    same listing the early one did — otherwise the later pass is free to pick
    the other attempt, and the sweep compares two listings of a bucket that grew
    in between.

    What this does *not* prove is that the winner cannot move: it is the
    earliest candidate the pass can read, and an unreadable marker disqualifies
    one for that pass only (`model.md` § *What a slot waits for is a shape*).
    """
    con = hinted_launch(tmp_path, monkeypatch, body=REPEATED)
    evidence = Evidence()
    monkeypatch.setattr(gcs, "download_bytes", evidence.download)
    first, second = (
        ledger.Attempt.from_row(row)
        for row in ledger.attempt_rows(con, case_id=attempt_row(con, "list")["case_id"])
    )
    evidence.publish(first.result_prefix, "keyspace.ks", b"a/\nb/\n")
    evidence.publish(second.result_prefix, "keyspace.ks", b"c/\nd/\n")
    ledger.set_state(con, first.attempt_id, "SUCCEEDED")
    ledger.set_state(con, second.attempt_id, "SUCCEEDED")

    # The second slot is held back for one pass, at the seam `resolve_blocked_slots`
    # already treats as "still owed", so the two slots resolve against two
    # separate reads of the candidate set rather than one.
    resolve = campaign.resolve_slot

    def held_back(
        con: sqlite3.Connection, slot: sqlite3.Row, inbound: campaign.Inbound, *, suite: str
    ) -> ledger.Attempt | None:
        if int(slot["slot"]) == 2:
            raise GoogleAPIError("the provider is busy")
        return resolve(con, slot, inbound, suite=suite)

    monkeypatch.setattr(campaign, "resolve_slot", held_back)
    campaign.settle_dependents(con, first, "SUCCEEDED", suite=SUITE)
    assert [slot["state"] for slot in ledger.pending_rows(con)] == ["RESOLVED", "BLOCKED"]

    monkeypatch.setattr(campaign, "resolve_slot", resolve)
    campaign.resolve_blocked_slots(con, "g20260817-000000", suite=SUITE)
    assert [slot["state"] for slot in ledger.pending_rows(con)] == ["RESOLVED", "RESOLVED"]
    hinted = [
        ledger.Attempt.from_row(row)
        for row in ledger.attempt_rows(con)
        if json.loads(row["config"])["mode"] == "list-hinted"
    ]
    assert len(hinted) == 2
    assert {attempt.produced_by for attempt in hinted} == {first.attempt_id}


def test_a_slot_abandons_only_once_every_candidate_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accepting one failure does not owe the measurement while another attempt of
    the same shape is still live — a slot is owed by a shape, so exhaustion is
    per slot rather than off the attempt id someone accepted."""
    con = hinted_launch(tmp_path, monkeypatch, body=REPEATED)
    state = str(tmp_path / "campaign.db")
    first, second = ledger.attempt_rows(con, case_id=attempt_row(con, "list")["case_id"])
    ledger.set_state(con, first["attempt_id"], "FAILED", "preempted")
    campaign.cmd_accept_failure(
        argparse.Namespace(state=state, attempt=first["attempt_id"], slot=None)
    )
    assert [slot["state"] for slot in ledger.pending_rows(con)] == ["BLOCKED", "BLOCKED"]

    ledger.set_state(con, second["attempt_id"], "FAILED", "preempted")
    campaign.cmd_accept_failure(
        argparse.Namespace(state=state, attempt=second["attempt_id"], slot=None)
    )
    assert [slot["state"] for slot in ledger.pending_rows(con)] == ["ABANDONED", "ABANDONED"]
