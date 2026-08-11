"""Canonicalize and create one immutable Snakemake execution profile."""

from __future__ import annotations

import argparse
import json

from scripts.workflow import WorkflowInputError, freeze_execution_profile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        created, digest = freeze_execution_profile(args.source, args.output)
    except WorkflowInputError as exc:
        parser.error(str(exc))
    print(json.dumps({"created": created, "path": args.output, "sha256": digest}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
