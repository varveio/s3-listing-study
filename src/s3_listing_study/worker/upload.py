"""Upload a finalized attempt directory to a caller-supplied GCS prefix.

Ships in the derived image, and runs only after the engine has finalized
``result.json`` — after the monotonic clock has stopped, after ``getrusage``
has been read, after the disk sampler has been joined. Nothing here can reach a
figure the attempt reports, which is what makes uploading from the same
invocation safe. It is still never *part of* the engine: an upload failure must
stay legible as an upload failure and not contaminate a listing outcome.

**Standard library only, deliberately.** The obvious implementation is
``google-cloud-storage``, and it was the recorded choice until it met the
roster: ``google-crc32c`` — a hard dependency — publishes no musllinux wheel at
any version, and three subjects (rclone, s3kor, s5cmd) run on musl. Vendoring
the SDK would have meant compiling a C extension inside Alpine subject images
that carry no toolchain. What the uploader actually needs from GCS is one POST
and one precondition header, so it asks for exactly that: no wheels to vendor,
no libc matrix, nothing added to eleven images that a campaign pins.

**Do not mount the bucket instead.** The engine's atomic ``os.replace()`` of
``result.json`` is what makes a finalized attempt directory trustworthy;
gcsfuse would turn that into a non-atomic copy-and-delete, letting a partial
``result.json`` become visible at the destination and destroying the one signal
that says "this attempt finished".

The destination is a complete ``gs://bucket/prefix/`` the caller supplies
outright: this module has no opinion on layout. The caller appends the run
leaf, because only the worker knows the attempt id it minted.

``result.json`` uploads last: its presence at the destination is what lets an
external reader treat "this attempt's upload is complete" as legible — the same
ordering discipline the engine uses locally, for the same reason. Every object
uploads with ``ifGenerationMatch=0`` — create-only — so a retry can never
silently overwrite a previous upload. "An attempt is never overwritten" is
enforced here, not merely documented.

Credentials come from one of two places. ``S3_STUDY_GCS_TOKEN``, when set,
carries an OAuth2 access token the caller injected by name — the same shape the
runner already uses for the AWS credential, and the only option that works on
the subject bridge, where the metadata endpoint is deliberately unreachable.
When it is unset, the token is read from the instance metadata server, which is
what a Batch task's worker service account provides.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path

BULK_FILES = ("stdout.raw.gz", "stderr.raw.gz", "normalized.parquet")
METADATA_FILES = ("collected.json",)
RESULT_FILE = "result.json"
TOKEN_ENV_VAR = "S3_STUDY_GCS_TOKEN"

UPLOAD_ENDPOINT = "https://storage.googleapis.com/upload/storage/v1/b"
METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)
TIMEOUT_S = 120.0
"""Per-socket-operation deadline, so a stalled upload cannot hang an attempt."""

PRECONDITION_FAILED = 412

Uploader = Callable[[str, str, Path], None]


class UploadError(RuntimeError):
    """A finalized attempt directory could not be uploaded."""


def _iter_upload_files(attempt_dir: Path, *, metadata_only: bool) -> list[Path]:
    files = [attempt_dir / name for name in METADATA_FILES if (attempt_dir / name).is_file()]
    if not metadata_only:
        files += [attempt_dir / name for name in BULK_FILES if (attempt_dir / name).is_file()]
        native_dir = attempt_dir / "native"
        if native_dir.is_dir():
            files += sorted(p for p in native_dir.rglob("*") if p.is_file())
    return files


def parse_destination(destination: str) -> tuple[str, str]:
    if not destination.startswith("gs://"):
        raise UploadError(f"destination must be a gs:// URL: {destination!r}")
    without_scheme = destination[len("gs://") :]
    bucket_name, _, prefix = without_scheme.partition("/")
    if not bucket_name:
        raise UploadError("destination must name a bucket: gs://bucket/prefix/")
    return bucket_name, prefix.rstrip("/")


def access_token(source_env: Mapping[str, str] | None = None) -> str:
    """Return the injected OAuth2 token, or one read from the metadata server."""
    env = os.environ if source_env is None else source_env
    injected = (env.get(TOKEN_ENV_VAR) or "").strip()
    if injected:
        return injected
    request = urllib.request.Request(METADATA_TOKEN_URL, headers={"Metadata-Flavor": "Google"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise UploadError(
            f"no {TOKEN_ENV_VAR} was injected and the metadata server did not supply a token: {exc}"
        ) from exc
    token = payload.get("access_token")
    if not token:
        raise UploadError("the metadata server returned no access_token")
    return str(token)


def _upload_one(bucket: str, object_name: str, local_path: Path, token: str) -> None:
    """Create one object, refusing to replace an existing one."""
    query = urllib.parse.urlencode(
        {"uploadType": "media", "name": object_name, "ifGenerationMatch": "0"}
    )
    size = local_path.stat().st_size
    with local_path.open("rb") as body:
        request = urllib.request.Request(
            f"{UPLOAD_ENDPOINT}/{urllib.parse.quote(bucket, safe='')}/o?{query}",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/octet-stream",
                # Set explicitly: urllib would otherwise pick chunked transfer
                # for a file object, which this endpoint does not accept.
                "Content-Length": str(size),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == PRECONDITION_FAILED:
                raise UploadError(
                    f"{object_name} already exists at the destination — "
                    "an attempt is never overwritten"
                ) from exc
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise UploadError(f"uploading {object_name} failed: HTTP {exc.code} {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise UploadError(f"uploading {object_name} failed: {exc}") from exc


def upload_attempt(
    attempt_dir: Path,
    destination: str,
    *,
    metadata_only: bool = False,
    uploader: Uploader | None = None,
) -> list[str]:
    """Upload one finalized attempt directory; return the object names uploaded, in order."""
    result_path = attempt_dir / RESULT_FILE
    if not result_path.is_file():
        raise UploadError(f"not a finalized attempt directory: {attempt_dir}")

    bucket, prefix = parse_destination(destination)
    send = uploader if uploader is not None else _authenticated_uploader()

    uploaded: list[str] = []
    for local_path in _iter_upload_files(attempt_dir, metadata_only=metadata_only):
        relative = local_path.relative_to(attempt_dir).as_posix()
        object_name = f"{prefix}/{relative}" if prefix else relative
        send(bucket, object_name, local_path)
        uploaded.append(object_name)

    result_object = f"{prefix}/{RESULT_FILE}" if prefix else RESULT_FILE
    send(bucket, result_object, result_path)
    uploaded.append(result_object)
    return uploaded


def _authenticated_uploader() -> Uploader:
    """Resolve the token once, then upload every object with it."""
    token = access_token()

    def send(bucket: str, object_name: str, path: Path) -> None:
        _upload_one(bucket, object_name, path, token)

    return send
