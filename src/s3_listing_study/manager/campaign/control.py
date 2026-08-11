"""Targeted retry and explicit accepted-failure finalization commands."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from google.api_core.exceptions import GoogleAPIError
from google.auth.exceptions import DefaultCredentialsError

from s3_listing_study.common.argparse_utils import UniqueStoreAction
from s3_listing_study.manager.campaign import controller, ledger, provider
from s3_listing_study.manager.campaign.models import CaseControllerProgress


def _public_progress(item: CaseControllerProgress) -> dict[str, Any]:
    """Serialize only the engine-neutral controller progress contract."""
    return {
        "job_id": item.job_id,
        "child_run_id": None,
        "phase": item.phase,
        "provider_state": item.provider_state,
        "failure_type": item.failure_type,
        "provider_resource_name": item.provider_resource_name,
        "provider_settled": item.provider_settled,
        "current_submission": item.current_submission,
        "current_job_id": item.current_job_id,
    }


def retry_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="s3-listing-study retry-case", allow_abbrev=False)
    parser.add_argument("--campaign", action=UniqueStoreAction, required=True)
    parser.add_argument("--ledger", "--ledger-path", action=UniqueStoreAction, required=True)
    parser.add_argument("--job-id", action=UniqueStoreAction, required=True)
    parser.add_argument("--submission", action=UniqueStoreAction, required=True)
    return parser


def finalize_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="s3-listing-study finalize-campaign", allow_abbrev=False)
    parser.add_argument("--campaign", action=UniqueStoreAction, required=True)
    parser.add_argument("--ledger", "--ledger-path", action=UniqueStoreAction, required=True)
    return parser


def _retry(args: argparse.Namespace) -> Any:
    try:
        submission = int(args.submission)
    except (TypeError, ValueError):
        raise controller.ControllerError("--submission must be an integer") from None
    if submission < 2:
        raise controller.ControllerError("--submission must be at least 2")
    return _public_progress(
        controller.retry_case(
            ledger_path=Path(args.ledger),
            campaign=args.campaign,
            base_job_id=args.job_id,
            submission=submission,
        )
    )


def _finalize(args: argparse.Namespace) -> Any:
    return [
        _public_progress(item)
        for item in controller.finalize(ledger_path=Path(args.ledger), campaign=args.campaign)
    ]


def _main(
    parser: argparse.ArgumentParser,
    runner: Callable[[argparse.Namespace], Any],
    argv: Sequence[str] | None,
) -> int:
    args = parser.parse_args(argv)
    try:
        print(json.dumps(runner(args), sort_keys=True, indent=2))
        return 0
    except (
        controller.ControllerError,
        DefaultCredentialsError,
        GoogleAPIError,
        ledger.LedgerError,
        OSError,
        provider.ProviderError,
    ) as exc:
        print(f"{parser.prog}: {exc}", file=sys.stderr)
        return 1


def retry_case_main(argv: Sequence[str] | None = None) -> int:
    return _main(retry_parser(), _retry, argv)


def finalize_campaign_main(argv: Sequence[str] | None = None) -> int:
    return _main(finalize_parser(), _finalize, argv)
