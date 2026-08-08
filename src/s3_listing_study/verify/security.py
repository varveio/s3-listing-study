"""The runner-security boundary, as an injectable seam.

The verifier reaches its FAIL / DRIFT / ERROR branches only through ``docker``,
behind the preflight, so without an injectable seam those verdicts are
unreachable offline and cannot be tested at all. The seam is therefore part of
the contract rather than a testing convenience (``tests/tier3/README.md``
§ "The fake runtime contract is implementation-agnostic").

What is injectable is the **preflight** — runner readiness is a property of the
box, not of the implementation under test, and it has its own suite in
``harness/tests/``. Everything else is production verbatim: the same
``timeout -k 2s 30s`` wrapper, the same ``--pull=never`` network profile, the
same evidence-log driver, the same status strings — because those land in the
report an ERROR verdict is read from.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .errors import VerifierError

NETWORK = "s3-listing-study-subjects"
DOCKER_CONTROL_TIMEOUT_S = 30
DOCKER_CLEANUP_TIMEOUT_S = 10

SECURITY_CHECK = Path("harness") / "runner-security-check.sh"
"""The preflight script, relative to whichever ancestor of this module holds it."""


def security_check() -> Path:
    """Locate the preflight script by walking up from this module.

    A fixed ``parents[3]`` is correct only in a source tree: under the shipped
    console script it resolves inside the install prefix, the missing file
    raises ``OSError``, and the process exits 1 — the FAIL code — about a tool
    that may be perfectly correct. Not finding the script is a
    :class:`VerifierError`, because a verifier that cannot run its own readiness
    gate has not checked anyone's software.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / SECURITY_CHECK
        if candidate.is_file():
            return candidate
    raise VerifierError(
        f"{SECURITY_CHECK} not found in any parent of {here} — the runner readiness gate "
        "cannot be run, so no reference re-list is started"
    )


# Test-only path overrides are stripped so a verifier caller cannot weaken the
# production readiness check through inherited environment.
_PREFLIGHT_UNSET = (
    "S3_STUDY_SECURITY_STATE_FILE",
    "S3_STUDY_SECURITY_INSTALLED_HELPER",
    "S3_STUDY_SECURITY_ALLOW_UNPRIVILEGED_STATE",
)


def docker_status(returncode: int) -> str:
    """The production status string. GNU ``timeout`` reports 124/137 on its deadline."""
    if returncode in (124, 137):
        return f"timed out (Docker wrapper rc={returncode})"
    return f"failed (Docker rc={returncode})"


class SecurityBoundary:
    """Docker argv construction plus the runner preflight, as production runs them."""

    def control(self, args: list[str]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["timeout", "-k", "2s", f"{DOCKER_CONTROL_TIMEOUT_S}s", "docker", *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    def cleanup(self, args: list[str]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["timeout", "-k", "2s", f"{DOCKER_CLEANUP_TIMEOUT_S}s", "docker", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    def image_present(self, image: str) -> bool:
        return self.control(["image", "inspect", image]).returncode == 0

    def preflight(self, bucket: str, region: str) -> bool:
        env = {k: v for k, v in os.environ.items() if k not in _PREFLIGHT_UNSET}
        return (
            subprocess.run(
                [str(security_check()), "--quiet", "--bucket", bucket, "--region", region],
                env=env,
                check=False,
            ).returncode
            == 0
        )

    def run_prefix(self, name: str, image: str) -> list[str]:
        """The complete ``docker run`` argv up to (not including) the client's own words."""
        return [
            "timeout",
            "-k",
            "2s",
            f"{DOCKER_CONTROL_TIMEOUT_S}s",
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--log-driver=json-file",
            "--log-opt",
            "max-size=-1",
            "--pull=never",
            "--network",
            NETWORK,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "-e",
            "AWS_EC2_METADATA_DISABLED=true",
            "-e",
            "TZ=UTC",
            image,
        ]

    def reconcile_container_absent(self, name: str) -> bool:
        """``rm`` may report "not found" after ``--rm`` already won; confirm absence."""
        if self.cleanup(["rm", "-f", name]).returncode == 0:
            return True
        probe = self.cleanup(
            ["container", "ls", "-a", "--filter", f"name=^/{name}$", "--format", "{{.Names}}"]
        )
        return probe.returncode == 0 and not probe.stdout.strip()


class PreflightSkipped(SecurityBoundary):
    """The seam ``tests/tier3`` needs: runner readiness is asserted elsewhere.

    Only :meth:`preflight` changes — asserted by
    ``test_the_preflight_seam_cannot_widen``. Every docker path still resolves
    ``docker`` on ``PATH`` and builds the production argv, so a fixture
    ``docker`` sees exactly what the real one would.

    No argv selects this class: a ``--skip-preflight`` flag would make a
    mandatory gate optional for anyone running the shipped entry point against a
    real bucket. Tier 3 constructs this boundary in process —
    ``tests/tier3/verifier_entry.py``, which is not installed.
    """

    def preflight(self, bucket: str, region: str) -> bool:
        return bool(bucket) and bool(region)
