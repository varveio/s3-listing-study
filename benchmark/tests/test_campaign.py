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
from google.api_core.exceptions import AlreadyExists, BadRequest
from google.cloud import batch_v1

from benchmark import adapters, campaign, gcs, identity, measure
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
) -> campaign.Attempt:
    return campaign.submit_case(
        con, case, context(plan, case, images, group_id=group_id), repeat=repeat
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


def rendered_request(tmp_path: Path, **overrides: Any) -> tuple[campaign.Attempt, dict[str, Any]]:
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
    # The hinted s3-fast-list row expands to a chain: its bootstrap `list`
    # preparation submits immediately (one attempt, replacing the hinted
    # measurement in the count), while `ks-split` and `list-hinted` wait on
    # their inputs and are booked as the two slots.
    assert (
        rendered[-1] == f"campaign: {cases} plan row(s) expand to {cases} attempt(s) and 2 slot(s)"
    )
    # One rendered line per expanded step: the chain's two waiting links print
    # alongside the immediate attempts.
    assert len(rendered[:-1]) == cases + 2
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
    with pytest.raises(campaign.CampaignError):
        rendered_request(tmp_path, signed=True, authenticated_worker_sa=None)


def test_a_job_name_that_batch_cannot_take_is_refused() -> None:
    name = campaign.job_name_for(SUITE, "aws-cli.9f300cc4d2b1", 2)
    assert name == f"{SUITE}-aws-cli-9f300cc4d2b1-s2"
    with pytest.raises(campaign.CampaignError):
        campaign.job_name_for("s" * 40, "aws-cli.9f300cc4d2b1", 1)
    with pytest.raises(campaign.CampaignError):
        campaign.job_name_for(SUITE, "aws_cli.9f300cc4d2b1", 1)


def test_a_ledger_whose_schema_version_is_unknown_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "campaign.db"
    campaign.open_ledger(str(path), suite=SUITE).close()
    con = sqlite3.connect(path)
    con.execute("UPDATE meta SET schema_version = ?", (campaign.SCHEMA_VERSION + 1,))
    con.commit()
    con.close()
    with pytest.raises(campaign.CampaignError):
        campaign.open_ledger(str(path))


def test_a_group_id_is_unique_within_an_accumulating_file(
    submitted: tuple[sqlite3.Connection, Plan, Case, campaign.ImageSet],
) -> None:
    con, plan, case, images = submitted
    minted = campaign.mint_group_id(con)
    submit(con, plan, case, images, group_id=minted)
    assert campaign.mint_group_id(con, "second-launch") == "second-launch"
    with pytest.raises(campaign.CampaignError):
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
    with pytest.raises(campaign.CampaignError):
        image_set(tmp_path, document)

    disagreeing = image_set_document()
    tools = cast(dict[str, dict[str, str]], disagreeing["tools"])
    tools["aws-cli"]["platform_sha256"] = "1" * 64
    with pytest.raises(campaign.CampaignError):
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

    The row states `segments` and chain expansion hands it to the `ks-split`
    link — the axis the capsule declares `Stated`. The chain, the modes and the
    artifact validator all stay the capsule's, so these tests still fail when
    its `REQUIRES` moves.
    """
    return bench.load_capsule("s3-fast-list")


def hinted_plan(tmp_path: Path, body: str = HINTED) -> Plan:
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
        adapters={"s3-fast-list": hinted_capsule()},
    )


class Evidence:
    """The results bucket, as much of it as a settling preparation is read from."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def publish(self, result_prefix: str, name: str, content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        self.objects[f"{result_prefix}result.json"] = json.dumps(
            {"native_manifest": {name: digest}}
        ).encode()
        self.objects[f"{result_prefix}native/{name}"] = content
        return digest

    def download(self, uri: str) -> bytes:
        return self.objects[uri]


def hinted_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setattr(campaign, "ensure_job", lambda *a, **k: ("SUBMITTED", None))
    plan = hinted_plan(tmp_path)
    con = campaign.open_ledger(str(tmp_path / "campaign.db"), suite=SUITE)
    launch = campaign.Launch(
        con, SUITE, "g20260817-000000", plan, image_set(tmp_path), "results", options()
    )
    launch.run(campaign.expand_launch(plan.cases, plan.adapters))
    return con


def test_a_declared_chain_expands_into_one_preparation_and_two_slots(tmp_path: Path) -> None:
    """`list-hinted` requires `list` then `ks-split`: only the first is identifiable."""
    plan = hinted_plan(tmp_path)
    steps = campaign.expand_launch(plan.cases, plan.adapters)

    assert [(step.case.mode, step.case.purpose, step.waits_for) for step in steps] == [
        ("list", "preparation", None),
        ("ks-split", "preparation", 0),
        ("list-hinted", "measurement", 1),
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
    assert len(rendered) == 3
    assert rendered[1].startswith("slot dry-run/1 s3-fast-list ks-split preparation awaiting ")
    assert rendered[2].startswith("slot dry-run/2 s3-fast-list list-hinted measurement awaiting ")


def test_a_preparation_is_identified_by_content_and_not_by_the_consumer(tmp_path: Path) -> None:
    """It answers "do we already have this artifact?", so the box and the role stay out."""
    plan = hinted_plan(tmp_path)
    (preparation, _, _) = campaign.expand_launch(plan.cases, plan.adapters)
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


def test_a_settled_preparation_resolves_its_chain_link_by_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Digest the artifact, mint the identity that was waiting on it, submit, repeat."""
    con = hinted_launch(tmp_path, monkeypatch)
    evidence = Evidence()
    monkeypatch.setattr(gcs, "download_bytes", evidence.download)
    listing = campaign.Attempt.from_row(attempt_row(con, "list"))
    assert [row["state"] for row in campaign.pending_rows(con)] == ["BLOCKED", "BLOCKED"]

    keyspace = evidence.publish(listing.result_prefix, "keyspace.ks", b"a/\nb/\n")
    campaign.set_state(con, listing.attempt_id, "SUCCEEDED")
    campaign.settle_dependents(con, listing, "SUCCEEDED", suite=SUITE)

    split = campaign.Attempt.from_row(attempt_row(con, "ks-split"))
    slots = campaign.pending_rows(con)
    assert (slots[0]["state"], slots[0]["became"]) == ("RESOLVED", split.attempt_id)
    # The second link could not be identified until the first settled, and now
    # waits on the attempt the slot became rather than on the slot.
    assert (slots[1]["state"], slots[1]["awaiting"]) == ("BLOCKED", split.attempt_id)
    assert (split.input_artifact_sha256, split.produced_by) == (keyspace, listing.attempt_id)

    hints = evidence.publish(split.result_prefix, "hints.input", b"m/\nz/\n")
    campaign.set_state(con, split.attempt_id, "SUCCEEDED")
    campaign.settle_dependents(con, split, "SUCCEEDED", suite=SUITE)

    hinted = campaign.Attempt.from_row(attempt_row(con, "list-hinted"))
    assert campaign.pending_rows(con)[1]["became"] == hinted.attempt_id
    assert (hinted.input_artifact_sha256, hinted.produced_by) == (hints, split.attempt_id)
    # What the worker is told to stage, and what it must digest to before use.
    request = json.loads(
        con.execute(
            "SELECT request_json FROM attempts WHERE attempt_id=?", (hinted.attempt_id,)
        ).fetchone()["request_json"]
    )
    assert campaign.request_argument(request, "--input-artifact") == (
        f"{split.result_prefix}native/hints.input"
    )
    assert campaign.request_argument(request, "--input-artifact-sha256") == hints


def test_an_unusable_artifact_fails_the_preparation_and_abandons_what_awaited_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hints file that digests cleanly and means nothing must not reach a measurement."""
    con = hinted_launch(tmp_path, monkeypatch)
    evidence = Evidence()
    monkeypatch.setattr(gcs, "download_bytes", evidence.download)
    listing = campaign.Attempt.from_row(attempt_row(con, "list"))
    evidence.publish(listing.result_prefix, "keyspace.ks", b"a/\n")
    campaign.set_state(con, listing.attempt_id, "SUCCEEDED")
    campaign.settle_dependents(con, listing, "SUCCEEDED", suite=SUITE)
    split = campaign.Attempt.from_row(attempt_row(con, "ks-split"))

    # An empty first cut point: a full-range serial scan beside every segment.
    digest = evidence.publish(split.result_prefix, "hints.input", b"\nz/\n")
    campaign.set_state(con, split.attempt_id, "SUCCEEDED")
    campaign.settle_dependents(con, split, "SUCCEEDED", suite=SUITE)

    row = con.execute(
        "SELECT state, state_detail, artifact_sha256 FROM attempts WHERE attempt_id=?",
        (split.attempt_id,),
    ).fetchone()
    assert row["state"] == "FAILED" and "first cut point is empty" in row["state_detail"]
    # The digest is a fact about what it produced; the verdict is that it is not usable.
    assert row["artifact_sha256"] == digest
    assert campaign.pending_rows(con)[1]["state"] == "BLOCKED"

    campaign.set_state(con, split.attempt_id, "ACCEPTED", "accepted FAILED")
    campaign.settle_dependents(con, split, "ACCEPTED", suite=SUITE)
    assert campaign.pending_rows(con)[1]["state"] == "ABANDONED"


def test_a_preparation_another_launch_made_is_bound_only_when_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reuse is free within a launch and a decision across them: the corpus moves."""
    con = hinted_launch(tmp_path, monkeypatch)
    evidence = Evidence()
    monkeypatch.setattr(gcs, "download_bytes", evidence.download)
    listing = campaign.Attempt.from_row(attempt_row(con, "list"))
    keyspace = evidence.publish(listing.result_prefix, "keyspace.ks", b"a/\nb/\n")
    campaign.set_state(con, listing.attempt_id, "SUCCEEDED")

    plan = hinted_plan(tmp_path)
    steps = campaign.expand_launch(plan.cases, plan.adapters)
    again = campaign.Launch(
        con, SUITE, "g20260817-000001", plan, image_set(tmp_path), "results", options()
    )
    with pytest.raises(campaign.CampaignError, match="reusing a preparation across launches"):
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
    # No second listing was submitted, and the split it unblocks is an attempt
    # rather than a slot: its identity is complete the moment the digest is known.
    assert len(campaign.attempt_rows(con, case_id=listing.case_id)) == 1
    split = campaign.Attempt.from_row(attempt_row(con, "ks-split", group_id="g20260817-000002"))
    assert (split.input_artifact_sha256, split.produced_by) == (keyspace, listing.attempt_id)


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
    listing = campaign.Attempt.from_row(attempt_row(con, "list"))
    evidence.publish(listing.result_prefix, "keyspace.ks", b"a/\nb/\n")
    stale = campaign.blocked_slots(con, listing.attempt_id)[0]

    campaign.set_state(con, listing.attempt_id, "SUCCEEDED")
    campaign.settle_dependents(con, listing, "SUCCEEDED", suite=SUITE)
    split = campaign.Attempt.from_row(attempt_row(con, "ks-split"))

    # The row a concurrent pass read before this one claimed the slot.
    inbound = campaign.Inbound(
        artifact_sha256=split.input_artifact_sha256,
        produced_by=listing.attempt_id,
        artifact_uri=f"{listing.result_prefix}native/keyspace.ks",
    )
    assert campaign.resolve_slot(con, stale, inbound, suite=SUITE) is None
    assert [row["attempt_id"] for row in campaign.attempt_rows(con, case_id=split.case_id)] == [
        split.attempt_id
    ]


def test_a_slot_whose_case_was_already_prepared_binds_it_rather_than_deadlocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ledger refuses a second attempt of a settled success, so the slot must
    bind the one that exists — a repeat of the listing produces the same hints."""
    con = hinted_launch(tmp_path, monkeypatch)
    evidence = Evidence()
    monkeypatch.setattr(gcs, "download_bytes", evidence.download)
    listing = campaign.Attempt.from_row(attempt_row(con, "list"))
    evidence.publish(listing.result_prefix, "keyspace.ks", b"a/\nb/\n")
    campaign.set_state(con, listing.attempt_id, "SUCCEEDED")
    campaign.settle_dependents(con, listing, "SUCCEEDED", suite=SUITE)
    split = campaign.Attempt.from_row(attempt_row(con, "ks-split"))
    evidence.publish(split.result_prefix, "hints.input", b"m/\nz/\n")
    campaign.set_state(con, split.attempt_id, "SUCCEEDED")
    campaign.settle_dependents(con, split, "SUCCEEDED", suite=SUITE)

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
    repeated = campaign.Attempt.from_row(attempt_row(con, "list", group_id="g20260817-000001"))
    evidence.publish(repeated.result_prefix, "keyspace.ks", b"a/\nb/\n")
    campaign.set_state(con, repeated.attempt_id, "SUCCEEDED")
    campaign.settle_dependents(con, repeated, "SUCCEEDED", suite=SUITE)

    slot = campaign.pending_rows(con, group_id="g20260817-000001")[0]
    assert (slot["state"], slot["became"]) == ("RESOLVED", split.attempt_id)
    # No second attempt of a case that has already succeeded, and the link behind
    # it did not stay blocked on an attempt that will never settle again.
    assert len(campaign.attempt_rows(con, case_id=split.case_id)) == 1
    assert campaign.pending_rows(con, group_id="g20260817-000001")[1]["state"] == "RESOLVED"


def attempt_row(
    con: sqlite3.Connection, mode: str, *, group_id: str = "g20260817-000000"
) -> sqlite3.Row:
    row = con.execute(
        "SELECT * FROM attempts WHERE mode=? AND group_id=? ORDER BY attempt DESC LIMIT 1",
        (mode, group_id),
    ).fetchone()
    assert row is not None, f"no {mode} attempt in {group_id}"
    return cast(sqlite3.Row, row)
