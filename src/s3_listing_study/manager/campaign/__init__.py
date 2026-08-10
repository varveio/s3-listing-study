"""A campaign: resolved plans, frozen images, and the jobs they become.

A plan is intent and a campaign is an execution, so everything an execution
knows and a plan does not lives here — which image each tool ran, what the jobs
were called, and where their artifacts land.

**Identity is two-layer, because the two are known at different times.** A case
fingerprint covers the resolved case and nothing else, so ``resolve-plan`` can
compute it while contacting nothing. An *attempt* fingerprint folds in the image
that ran it, which exists only once images are built. That is what lets a fixed
tool be re-run inside a campaign the other ten already completed: the same case,
a different attempt.

It folds in the image's **components** — subject digest, adapter bundle,
interpreter, harness — rather than the derived digest, because the derived image
also carries post-timing summarization and upload code, which cannot reach the
measurement. Fingerprinting the whole digest would let an
edit to orchestrator code invalidate every case's identity. The derived digest
is recorded in ``result.json`` and the manifest regardless.

**An address is not an identity.** A path names a case in the plan's own
vocabulary, readable by someone who has the plan open; the image is deliberately
absent from it and recorded in ``result.json`` instead. The manager names an
intentional ``run-N`` directory; the UUID leaf below it is named by the worker
at execution. Batch may re-execute a task (``BATCH_TASK_RETRY_ATTEMPT``), so a
submitter-chosen execution leaf would be written twice — refused by the
create-only upload only after the duplicate run had already cost what it cost.

**Nothing is deleted and nothing is overwritten.** A submission that produced
no artifacts re-submits under a new job ID and targets the same, still-empty
path; a submission that did produce them needs no retry, since a tool exiting
nonzero is a recorded outcome rather than a failure to run.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from s3_listing_study.manager.bench.plan import Case, Plan

# Cloud Batch accepts `^[a-z]([a-z0-9-]*[a-z0-9])?$` and at most 63 characters.
# A case id carries `.` and `_` and reaches 46 characters on its own, so
# `bucket-tool-case_id` is already 66 before a campaign name exists: a job id
# cannot be the case's identity, and is built to a budget instead.
JOB_ID_MAX = 63
JOB_ID_RE = re.compile(r"\A[a-z]([a-z0-9-]*[a-z0-9])?\Z")

# `c-` because a dated campaign starts with a digit and a job id may not.
JOB_ID_PREFIX = "c-"
CAMPAIGN_MAX = 18
TOOL_MAX = 12
SLUG_MAX = 12
HASH_CHARS = 8

# `<date>-<word>`: dated because a campaign is an event, and the date is the
# first thing anyone reading a result wants to know about it.
CAMPAIGN_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}-[a-z][a-z0-9]*\Z")

DIGEST_RE = re.compile(r"\Asha256:[0-9a-f]{64}\Z")

ATTEMPT_FINGERPRINT_VERSION = 1

# Everything about the image that a measurement can depend on. `provisioning`
# is deliberately absent: a spot VM is the same machine type at a different
# price, so it changes the odds of being preempted and nothing a completed run
# measured.
IMAGE_COMPONENTS = ("subject_image", "adapter_bundle_sha256", "python_libc", "harness_revision")


class CampaignError(Exception):
    """A campaign cannot be assembled from what it was given."""


def validate_campaign_id(campaign: str) -> str:
    if not CAMPAIGN_RE.fullmatch(campaign):
        raise CampaignError(
            f"campaign id {campaign!r} is not <yyyy-mm-dd>-<word> "
            "(lowercase, e.g. 2026-08-10-first)"
        )
    if len(campaign) > CAMPAIGN_MAX:
        raise CampaignError(
            f"campaign id {campaign!r} is {len(campaign)} characters, over the {CAMPAIGN_MAX} "
            "a Batch job id can spare"
        )
    return campaign


def attempt_fingerprint(*, case_fingerprint: str, components: Mapping[str, Any]) -> str:
    """What makes two runs the same attempt: the case, and what ran it.

    Separate from the case fingerprint rather than replacing it, so a plan stays
    readable without a registry: `resolve-plan` still contacts nothing.
    """
    missing = sorted(set(IMAGE_COMPONENTS) - set(components))
    if missing:
        raise CampaignError(f"image components are missing {', '.join(missing)}")
    payload = {
        "attempt_fingerprint_version": ATTEMPT_FINGERPRINT_VERSION,
        "case_fingerprint": case_fingerprint,
        "image": {name: components[name] for name in IMAGE_COMPONENTS},
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _slug(case_id: str) -> str:
    """A case id reduced to what a Batch job id accepts, for reading in a console.

    Lossy on purpose: the hash beside it carries uniqueness, and the campaign
    manifest carries the truth. This only has to get a reader to the right row.
    """
    reduced = re.sub(r"[^a-z0-9]+", "-", case_id.lower()).strip("-")
    return reduced[:SLUG_MAX].rstrip("-")


def job_id(
    *,
    campaign: str,
    tool: str,
    case_id: str,
    fingerprint: str,
    run_ordinal: int = 1,
    submission: int,
) -> str:
    """``c-2026-08-10-first-swath-recursive-pa-1f4a9c02-r1-s1``.

    ``submission`` counts re-submissions of one attempt, never runs of it: a job
    id is not deleted and not reused, so a job that failed to start leaves its
    name spent. It appears nowhere in the artifact path, which names the attempt
    rather than the try that produced it.
    """
    validate_campaign_id(campaign)
    if submission < 1 or submission > 99:
        raise CampaignError(f"submission must be between 1 and 99: {submission}")
    if run_ordinal < 1 or run_ordinal > 99:
        raise CampaignError(f"run ordinal must be between 1 and 99: {run_ordinal}")
    if len(tool) > TOOL_MAX:
        raise CampaignError(f"tool name {tool!r} is over {TOOL_MAX} characters")
    rendered = (
        f"{JOB_ID_PREFIX}{campaign}-{tool}-{_slug(case_id)}"
        f"-{fingerprint[:HASH_CHARS]}-r{run_ordinal}-s{submission}"
    )
    # Belt and braces: every component is bounded above, so this cannot fire
    # without one of those bounds being wrong.
    if len(rendered) > JOB_ID_MAX or not JOB_ID_RE.fullmatch(rendered):
        raise CampaignError(f"generated an unusable Batch job id: {rendered!r}")
    return rendered


def campaign_prefix(campaign: str) -> str:
    return f"campaigns/{validate_campaign_id(campaign)}"


def attempt_prefix(
    *, campaign: str, bucket: str, tool: str, case_id: str, run_ordinal: int = 1
) -> str:
    """Where one case's runs land, without naming any one of them.

    The bucket is in the path because a campaign covering two plans would
    otherwise collide on ``s5cmd/recursive`` — same tool, same case, different
    target. The worker appends its own execution UUID under this manager-owned
    run prefix.
    """
    return (
        f"{campaign_prefix(campaign)}/results/"
        f"{bucket}/{tool}/{case_id}/run-{run_ordinal}"
    )


@dataclass(frozen=True)
class Attempt:
    """One manager-assigned run of one case/image: one Batch job."""

    campaign: str
    bucket: str
    region: str
    case: Case
    # The whole registration: the derived digest that ran, and the components
    # the fingerprint is taken over.
    image: Mapping[str, Any]
    fingerprint: str
    job_id: str
    submission: int
    run_ordinal: int
    prefix: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "submission": self.submission,
            "run_ordinal": self.run_ordinal,
            "bucket": self.bucket,
            "region": self.region,
            "tool": self.case.tool,
            "case_id": self.case.case_id,
            "mode": self.case.mode,
            "auth": self.case.auth,
            "case_fingerprint": self.case.fingerprint,
            "derived_image": self.image["derived_image"],
            "fingerprint": self.fingerprint,
            "attempt_fingerprint": self.fingerprint,
            "resources": self.case.resources.as_dict(),
            # What the runtime was told about its own memory, if it is the kind
            # that needs telling. Part of the invocation, so it is recorded as
            # the environment it was passed as rather than as a percentage.
            "env": [list(pair) for pair in self.case.env],
            "reps": self.case.reps,
            "timeout_s": self.case.timeout_s,
            "prefix": self.prefix,
        }


def attempts_for(
    plan: Plan,
    *,
    campaign: str,
    images: Mapping[str, Mapping[str, Any]],
    submission: int = 1,
) -> tuple[Attempt, ...]:
    """Every manager-assigned run, one per case for each requested ``rep``.

    The plan retains ``reps`` as the count, while each concrete job/result uses
    an explicit 1-based ``run_ordinal``. That ordinal distinguishes intentional
    reruns; UUID children under one ordinal instead expose duplicate executions.
    """
    validate_campaign_id(campaign)
    missing = sorted({case.tool for case in plan.cases} - set(images))
    if missing:
        raise CampaignError(
            f"no derived image digest for {', '.join(missing)} — a campaign runs the images "
            "it froze, so every tool it submits must be in the image set"
        )
    attempts: list[Attempt] = []
    for case in plan.cases:
        image = images[case.tool]
        if not DIGEST_RE.fullmatch(str(image.get("derived_image", ""))):
            raise CampaignError(f"{case.tool}: derived_image is not a sha256 digest")
        digest = attempt_fingerprint(case_fingerprint=case.fingerprint, components=image)
        for run_ordinal in range(1, case.reps + 1):
            attempts.append(
                Attempt(
                    campaign=campaign,
                    bucket=plan.bucket,
                    region=plan.region,
                    case=case,
                    image=image,
                    fingerprint=digest,
                    job_id=job_id(
                        campaign=campaign,
                        tool=case.tool,
                        case_id=case.case_id,
                        fingerprint=digest,
                        run_ordinal=run_ordinal,
                        submission=submission,
                    ),
                    submission=submission,
                    run_ordinal=run_ordinal,
                    prefix=attempt_prefix(
                        campaign=campaign,
                        bucket=plan.bucket,
                        tool=case.tool,
                        case_id=case.case_id,
                        run_ordinal=run_ordinal,
                    ),
                )
            )
    return tuple(attempts)


def manifest(
    *,
    campaign: str,
    plans: Sequence[Plan],
    images: Mapping[str, Mapping[str, Any]],
    attempts: Sequence[Attempt],
    results_bucket: str,
    provisioning: str = "SPOT",
    zone: str | None = None,
) -> dict[str, Any]:
    """``campaign.json`` — the index a job id is meaningless without.

    ``zone`` and ``provisioning`` are recorded because they are not in any
    fingerprint and a reader will ask: every number includes the network path
    from the VM to the store, and spot changes the odds of being preempted.
    """
    return {
        "schema_version": 2,
        "campaign": validate_campaign_id(campaign),
        "results_bucket": results_bucket,
        "attempt_fingerprint_version": ATTEMPT_FINGERPRINT_VERSION,
        "provisioning": provisioning,
        "zone": zone,
        "plans": [
            {
                "bucket": plan.bucket,
                "region": plan.region,
                "path": f"{campaign_prefix(campaign)}/inputs/plans/{plan.bucket}.yaml",
                "sha256": plan.digest,
            }
            for plan in plans
        ],
        "images": {tool: dict(image) for tool, image in sorted(images.items())},
        "attempts": [attempt.as_dict() for attempt in attempts],
    }
