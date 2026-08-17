"""What the ledger must refuse, and what it must never lose.

The model is `benchmark/docs/model.md`; these tests cover the invariants that
protect evidence — identity landing intact, a hash collision being loud, an
ordinal that cannot be raced, a settled success that cannot be quietly re-run —
rather than the shape of any particular row.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from google.api_core.exceptions import AlreadyExists, BadRequest
from google.cloud import batch_v1

from benchmark import campaign, identity
from benchmark.contract import CREDENTIAL_ENV_VAR, TOOLBOX_TOOLS
from benchmark.plan import Case, Plan

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


def submit(
    con: sqlite3.Connection,
    plan: Plan,
    case: Case,
    images: campaign.ImageSet,
    *,
    group_id: str = "g20260817-000000",
    repeat: bool = False,
) -> campaign.Attempt:
    return campaign.submit_case(
        con,
        case,
        suite=SUITE,
        group_id=group_id,
        plan=plan,
        image_set=images,
        results_bucket="results",
        options=options(),
        repeat=repeat,
    )


@pytest.fixture
def submitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[sqlite3.Connection, Plan, Case, campaign.ImageSet]:
    """A ledger with one attempt in it, created against a provider that says yes."""
    monkeypatch.setattr(campaign, "ensure_job", lambda *a, **k: ("SUBMITTED", None))
    plan = loaded_plan()
    con = campaign.open_ledger(str(tmp_path / "campaign.db"), suite=SUITE)
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

    def build(ordinal: int) -> tuple[campaign.Attempt, str]:
        raise AssertionError("a colliding insert must be refused before it renders")

    with pytest.raises(campaign.CampaignError, match="hash to one case_id"):
        campaign.journal_intent(
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
    other = campaign.open_ledger(str(tmp_path / "campaign.db"), suite=SUITE)
    other.execute("PRAGMA busy_timeout=50")
    raced: list[Exception] = []

    def build(ordinal: int) -> tuple[campaign.Attempt, str]:
        try:
            campaign.journal_intent(
                other,
                case_id=first.case_id,
                case_inputs=first.case_inputs,
                build=lambda n: (_replaced(first, n), "{}"),
            )
        except sqlite3.OperationalError as exc:
            raced.append(exc)
        return _replaced(first, ordinal), "{}"

    second, _ = campaign.journal_intent(
        con, case_id=first.case_id, case_inputs=first.case_inputs, build=build
    )
    other.close()
    assert second.attempt == 2
    assert raced, "a concurrent journal was allowed to read the ordinal mid-transaction"


def _replaced(attempt: campaign.Attempt, ordinal: int) -> campaign.Attempt:
    attempt_id = identity.attempt_id(attempt.case_id, ordinal)
    return campaign.Attempt(
        **{
            **{name: getattr(attempt, name) for name in campaign.Attempt.__dataclass_fields__},
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
    campaign.set_state(con, attempt.attempt_id, "SUCCEEDED")

    with pytest.raises(campaign.CampaignError, match="already has a successful attempt"):
        submit(con, plan, case, images)
    assert submit(con, plan, case, images, repeat=True).attempt == 2


def test_intent_is_durable_before_the_provider_is_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = campaign.open_ledger(str(tmp_path / "campaign.db"), suite=SUITE)
    plan = loaded_plan()

    def observe(*_args: object, **_kwargs: object) -> tuple[str, str | None]:
        row = con.execute("SELECT state, request_json, settled_at FROM attempts").fetchone()
        assert row["state"] == "SUBMITTING"
        assert json.loads(row["request_json"])["taskGroups"]
        assert row["settled_at"] is None
        return "SUBMITTED", None

    monkeypatch.setattr(campaign, "ensure_job", observe)
    attempt = submit(con, plan, any_case(plan), image_set(tmp_path))

    row = con.execute("SELECT * FROM attempts").fetchone()
    assert (row["state"], row["settled_at"]) == ("SUBMITTED", None)
    campaign.set_state(con, attempt.attempt_id, "FAILED", "the machine went away")
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


def rendered_request(
    tmp_path: Path, **overrides: object
) -> tuple[campaign.Attempt, dict[str, Any]]:
    plan = loaded_plan()
    case = any_case(plan, signed=bool(overrides.pop("signed", False)))
    images = image_set(tmp_path)
    _, _, build = campaign.planned_attempt(
        case,
        suite=SUITE,
        group_id="g20260817-000000",
        plan=plan,
        image_set=images,
        results_bucket="results",
        options=options(**overrides),
    )
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
    assert len(rendered) == len(loaded_plan().cases)
    assert not state.exists()


def test_an_existing_job_is_submitted_only_when_it_matches_recorded_intent(
    tmp_path: Path,
) -> None:
    """`SUBMITTED` covers what we created and what we found; `NOT_CREATED` covers the rest."""
    attempt, request = rendered_request(tmp_path)
    name = f"projects/p/locations/us-east1/jobs/{attempt.job_name}"
    matching = campaign._job_from_dict(request)
    matching.name = name

    found = campaign.ensure_job(
        "p", "us-east1", attempt.job_name, request, client=cast(Any, ExistingClient(matching))
    )
    assert found[0] == "SUBMITTED"

    different = campaign._job_from_dict(request)
    different.name = name
    different.task_groups[0].task_spec.runnables[0].container.commands.append("--extra")
    state, detail = campaign.ensure_job(
        "p", "us-east1", attempt.job_name, request, client=cast(Any, ExistingClient(different))
    )
    assert state == "NOT_CREATED"
    assert detail is not None


def test_a_refused_creation_is_not_created(tmp_path: Path) -> None:
    attempt, request = rendered_request(tmp_path)

    class Refusing:
        def create_job(self, **_kwargs: object) -> batch_v1.Job:
            raise BadRequest("machine type unavailable")  # type: ignore[no-untyped-call]

    state, detail = campaign.ensure_job(
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
    with pytest.raises(campaign.CampaignError, match="signing worker"):
        rendered_request(tmp_path, signed=True, authenticated_worker_sa=None)


def test_a_job_name_that_batch_cannot_take_is_refused() -> None:
    name = campaign.job_name_for(SUITE, "aws-cli.9f300cc4d2b1", 2)
    assert name == f"{SUITE}-aws-cli-9f300cc4d2b1-s2"
    with pytest.raises(campaign.CampaignError, match="usable Batch job ID"):
        campaign.job_name_for("s" * 40, "aws-cli.9f300cc4d2b1", 1)
    with pytest.raises(campaign.CampaignError, match="usable Batch job ID"):
        campaign.job_name_for(SUITE, "aws_cli.9f300cc4d2b1", 1)


def test_a_ledger_whose_schema_version_is_unknown_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "campaign.db"
    campaign.open_ledger(str(path), suite=SUITE).close()
    con = sqlite3.connect(path)
    con.execute("UPDATE meta SET schema_version = ?", (campaign.SCHEMA_VERSION + 1,))
    con.commit()
    con.close()
    with pytest.raises(campaign.CampaignError, match="does not migrate"):
        campaign.open_ledger(str(path))


def test_a_group_id_is_unique_within_an_accumulating_file(
    submitted: tuple[sqlite3.Connection, Plan, Case, campaign.ImageSet],
) -> None:
    con, plan, case, images = submitted
    minted = campaign.mint_group_id(con)
    submit(con, plan, case, images, group_id=minted)
    assert campaign.mint_group_id(con, "second-launch") == "second-launch"
    with pytest.raises(campaign.CampaignError, match="already exists"):
        campaign.mint_group_id(con, minted)
    # The minted form is a timestamp an operator can type, suffixed rather than
    # reused when two launches land in one second.
    assert campaign.mint_group_id(con).startswith(minted.rsplit("-", 1)[0][:9])


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
        campaign.set_state(con, attempt.attempt_id, "FAILED", "settled failure")
    con.execute("UPDATE attempts SET statistic='rate' WHERE attempt_id=?", (sampled.attempt_id,))
    retried: list[str] = []

    def observe(con: sqlite3.Connection, row: sqlite3.Row, **kwargs: object) -> campaign.Attempt:
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
    campaign.set_state(con, attempt.attempt_id, "FAILED", "settled failure")
    row = con.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt.attempt_id,)).fetchone()

    moved = image_set_document()
    tools = cast(dict[str, dict[str, str]], moved["tools"])
    tools[case.tool]["tool_version"] = "2.0"
    with pytest.raises(campaign.CampaignError, match="new campaign, not a retry"):
        campaign.retry_attempt(
            con,
            row,
            suite=SUITE,
            image_set=image_set(tmp_path / "moved", moved),
            results_bucket="results",
            options=options(),
        )


def test_accept_failure_records_which_failure_was_accepted(
    submitted: tuple[sqlite3.Connection, Plan, Case, campaign.ImageSet], tmp_path: Path
) -> None:
    con, plan, case, images = submitted
    attempt = submit(con, plan, case, images)
    campaign.set_state(con, attempt.attempt_id, "NOT_CREATED", "Forbidden: no quota")
    con.close()

    campaign.cmd_accept_failure(
        cast(
            argparse.Namespace,
            SimpleNamespace(state=str(tmp_path / "campaign.db"), attempt=attempt.attempt_id),
        )
    )
    reopened = campaign.open_ledger(str(tmp_path / "campaign.db"), readonly=True)
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
    with pytest.raises(campaign.CampaignError, match="tool entry must contain"):
        image_set(tmp_path, document)

    disagreeing = image_set_document()
    tools = cast(dict[str, dict[str, str]], disagreeing["tools"])
    tools["aws-cli"]["platform_sha256"] = "1" * 64
    with pytest.raises(campaign.CampaignError, match="disagree on platform_sha256"):
        image_set(tmp_path / "other", disagreeing)
