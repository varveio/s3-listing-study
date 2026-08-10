"""A campaign: resolved plans, frozen images, and the jobs they become.

A plan is intent and a campaign is an execution, so everything an execution
knows and a plan does not lives here — which image each tool ran, what the jobs
were called, and where their artifacts land.

**Identity is two-layer, because the two are known at different times.**
:attr:`~s3_listing_study.manager.bench.plan.Case.fingerprint` is a digest over
the resolved case and nothing else, so ``resolve-plan`` can compute it while
contacting nothing. An *attempt* fingerprint folds in the derived image digest
that actually ran, which only exists once images are built and pushed. Two
functions rather than one because a case does not stop being the same case when
its image is rebuilt — it stops being the same *attempt*, which is exactly the
distinction that lets a fixed tool be re-run inside a campaign the other ten
tools already completed.

**An address is not an identity.** A path names a case in the plan's own
vocabulary, readable by someone who has the plan open; the image is deliberately
absent from it and recorded in ``result.json`` instead. The run directory is
named by the worker at execution, not by the submitter, because Batch may
re-execute a task (``BATCH_TASK_RETRY_ATTEMPT``) and a path the submitter chose
would then be written twice — refused by the create-only upload, after the run
had already cost what it cost.

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
SLUG_MAX = 14
HASH_CHARS = 8

# `<date>-<word>`: dated because a campaign is an event, and the date is the
# first thing anyone reading a result wants to know about it.
CAMPAIGN_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}-[a-z][a-z0-9]*\Z")

DIGEST_RE = re.compile(r"\Asha256:[0-9a-f]{64}\Z")

ATTEMPT_FINGERPRINT_VERSION = 1


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


def attempt_fingerprint(*, case_fingerprint: str, derived_image: str) -> str:
    """What makes two runs the same attempt: the case, and the image that ran it.

    Separate from the case fingerprint rather than replacing it, so a plan stays
    readable without a registry: `resolve-plan` still contacts nothing.
    """
    if not DIGEST_RE.fullmatch(derived_image):
        raise CampaignError(f"derived image is not a sha256 digest: {derived_image!r}")
    payload = {
        "attempt_fingerprint_version": ATTEMPT_FINGERPRINT_VERSION,
        "case_fingerprint": case_fingerprint,
        "derived_image": derived_image,
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


def job_id(*, campaign: str, tool: str, case_id: str, fingerprint: str, submission: int) -> str:
    """``c-2026-08-10-first-swath-recursive-parq-1f4a9c02-s1``.

    ``submission`` counts re-submissions of one attempt, never runs of it: a job
    id is not deleted and not reused, so a job that failed to start leaves its
    name spent. It appears nowhere in the artifact path, which names the attempt
    rather than the try that produced it.
    """
    validate_campaign_id(campaign)
    if submission < 1 or submission > 99:
        raise CampaignError(f"submission must be between 1 and 99: {submission}")
    if len(tool) > TOOL_MAX:
        raise CampaignError(f"tool name {tool!r} is over {TOOL_MAX} characters")
    rendered = (
        f"{JOB_ID_PREFIX}{campaign}-{tool}-{_slug(case_id)}"
        f"-{fingerprint[:HASH_CHARS]}-s{submission}"
    )
    # Belt and braces: every component is bounded above, so this cannot fire
    # without one of those bounds being wrong.
    if len(rendered) > JOB_ID_MAX or not JOB_ID_RE.fullmatch(rendered):
        raise CampaignError(f"generated an unusable Batch job id: {rendered!r}")
    return rendered


def campaign_prefix(campaign: str) -> str:
    return f"campaigns/{validate_campaign_id(campaign)}"


def attempt_prefix(*, campaign: str, bucket: str, tool: str, case_id: str) -> str:
    """Where one case's runs land, without naming any one of them.

    The bucket is in the path because a campaign covering two plans would
    otherwise collide on ``s5cmd/recursive`` — same tool, same case, different
    target. The worker appends its own run directory under this.
    """
    return f"{campaign_prefix(campaign)}/attempts/{bucket}/{tool}/{case_id}"


@dataclass(frozen=True)
class Attempt:
    """One case, one image: the unit a job is submitted for."""

    campaign: str
    bucket: str
    region: str
    case: Case
    derived_image: str
    fingerprint: str
    job_id: str
    submission: int
    prefix: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "submission": self.submission,
            "bucket": self.bucket,
            "region": self.region,
            "tool": self.case.tool,
            "case_id": self.case.case_id,
            "mode": self.case.mode,
            "case_fingerprint": self.case.fingerprint,
            "derived_image": self.derived_image,
            "fingerprint": self.fingerprint,
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
    images: Mapping[str, str],
    submission: int = 1,
) -> tuple[Attempt, ...]:
    """Every attempt one plan contributes, one per case per rep.

    A rep is a separate job on its own instance rather than a repeated run in
    one, so nothing about a first rep can carry into a second — a warm page
    cache, a filled disk, a JIT that has already compiled the hot path.
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
        digest = attempt_fingerprint(case_fingerprint=case.fingerprint, derived_image=image)
        for _ in range(case.reps):
            attempts.append(
                Attempt(
                    campaign=campaign,
                    bucket=plan.bucket,
                    region=plan.region,
                    case=case,
                    derived_image=image,
                    fingerprint=digest,
                    job_id=job_id(
                        campaign=campaign,
                        tool=case.tool,
                        case_id=case.case_id,
                        fingerprint=digest,
                        submission=submission,
                    ),
                    submission=submission,
                    prefix=attempt_prefix(
                        campaign=campaign,
                        bucket=plan.bucket,
                        tool=case.tool,
                        case_id=case.case_id,
                    ),
                )
            )
    return tuple(attempts)


def manifest(
    *,
    campaign: str,
    plans: Sequence[Plan],
    images: Mapping[str, str],
    selections: Mapping[str, Mapping[str, Any]],
    attempts: Sequence[Attempt],
    results_bucket: str,
) -> dict[str, Any]:
    """``campaign.json`` — the index a job id is meaningless without.

    Carries the image *components* beside each digest, not just the digest: a
    harness rebuild moves every derived digest without any tool changing, and
    only the components say which of the two happened.
    """
    return {
        "schema_version": 1,
        "campaign": validate_campaign_id(campaign),
        "results_bucket": results_bucket,
        "attempt_fingerprint_version": ATTEMPT_FINGERPRINT_VERSION,
        "plans": [
            {
                "bucket": plan.bucket,
                "region": plan.region,
                "path": f"{campaign_prefix(campaign)}/plans/{plan.bucket}.yaml",
                "sha256": plan.digest,
            }
            for plan in plans
        ],
        "images": {
            tool: {
                "derived_image": digest,
                "subject_image": selections.get(tool, {}).get("subject_image"),
                "subject_version": selections.get(tool, {}).get("subject_version"),
                "adapter_bundle_sha256": selections.get(tool, {}).get("adapter_bundle_sha256"),
            }
            for tool, digest in sorted(images.items())
        },
        "attempts": [attempt.as_dict() for attempt in attempts],
    }
