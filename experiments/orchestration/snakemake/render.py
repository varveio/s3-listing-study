"""Render every frozen campaign attempt into the parity projection as JSON."""

from __future__ import annotations

import argparse
import json

from scripts.workflow import load_campaign, load_execution_profile, project_attempt, sha256_file


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--execution-profile", required=True)
    args = parser.parse_args()
    campaign = load_campaign(args.campaign)
    execution = load_execution_profile(args.execution_profile)
    rendered = {
        "campaign_sha256": sha256_file(args.campaign),
        "execution_sha256": sha256_file(args.execution_profile),
        "attempts": [project_attempt(campaign, row, execution) for row in campaign["attempts"]],
    }
    print(json.dumps(rendered, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
