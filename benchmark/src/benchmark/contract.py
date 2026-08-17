"""Shared benchmark conventions: tool roster, verifier exits, and hashing.

The byte-framed TAB listing contract lives in :mod:`benchmark.runtime.contract`
and the capsule normalizers. This module holds the smaller set of values shared
by benchmark controller, worker, verifier, and report code.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# verify.py's refusal ladder: a distinct code per reason a comparison did not
# reach a verdict, never folded into FAIL. Codes 0-8 are per comparison; 9 is the
# group rung, for a ledger group that never reached one.
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_DRIFT = 2
EXIT_AMBIGUOUS_LEAVES = 3
EXIT_MISSING_MARKER = 4
EXIT_BINDING_MISMATCH = 5
EXIT_NORMALIZE_FAILED = 6
EXIT_FAILED_SUBJECT = 7
EXIT_MALFORMED_INPUT = 8
EXIT_INCOMPLETE_GROUP = 9

VERDICT_EXIT_CODES = {"PASS": EXIT_PASS, "DRIFT": EXIT_DRIFT, "FAIL": EXIT_FAIL}

TOOLBOX_TOOLS = frozenset(
    {
        "aws-cli",
        "minio-mc",
        "ps3",
        "rclone",
        "s3-fast-list",
        "s3kor",
        "s3p",
        "s4cmd",
        "s5cmd",
        "s7cmd",
        "swath",
    }
)


# The authenticated stratum's credential travels as ONE Batch secretVariable
# holding the KEY=VALUE payload documented in
# infra/terraform/modules/gcp/s3-listing-study/aws-credentials.tf: one secret,
# one grant, one rotation, and an optional session token that a per-variable
# mapping would have to model as a separate secret. The controller names the
# secret version; the worker parses the payload and never records its values.
CREDENTIAL_ENV_VAR = "S3_STUDY_AWS_CREDENTIAL"
AWS_CREDENTIAL_ENV_KEYS = frozenset(
    {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}
)
AWS_CREDENTIAL_REQUIRED_ENV_KEYS = frozenset({"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"})


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
