"""Upload a finalized attempt directory to a caller-supplied GCS prefix.

Ships in the derived image, and runs only after the engine has finalized
``result.json`` — after the monotonic clock has stopped, after ``getrusage``
has been read, after the disk sampler has been joined. Nothing here can reach a
figure the attempt reports, which is what makes uploading from the same
invocation safe. It is still never *part of* the engine: an upload failure must
stay legible as an upload failure and not contaminate a listing outcome.

**Standard library only, deliberately.** The obvious implementation is
``google-cloud-storage``, but the uploader only needs one POST and one
precondition header. All eleven current subjects use glibc; avoiding the SDK is
still useful because it keeps a large dependency and its compiled CRC extension
out of every pinned image.

**Do not mount the bucket instead.** The engine's atomic ``os.replace()`` of
``result.json`` is what makes a finalized attempt directory trustworthy;
gcsfuse would turn that into a non-atomic copy-and-delete, letting a partial
``result.json`` become visible at the destination and destroying the one signal
that says "this attempt finished".

The destination is a complete ``gs://bucket/prefix/`` the caller supplies
outright: this module has no opinion on layout. In a campaign, the manager
supplies the deterministic ``run-<n>`` prefix and the worker caller appends the
attempt UUID leaf, because only that execution knows the ID it minted.

``result.json`` uploads last: its presence at the destination is what lets an
external reader treat "this attempt's upload is complete" as legible — the same
ordering discipline the engine uses locally, for the same reason. Every object
uploads with ``ifGenerationMatch=0`` — create-only — so a retry can never
silently overwrite a previous upload. "An attempt is never overwritten" is
enforced here, not merely documented.

Credentials come from one of two places. ``S3_STUDY_GCS_TOKEN``, when set,
carries an OAuth2 access token the caller injected by name and supports the
strict local Docker profile, where metadata is deliberately unreachable. In
GCP Batch it is normally unset: metadata access is intentional, and the worker
reads the bounded task service-account token from the instance metadata server.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath

BULK_FILES = ("stdout.raw.gz", "stderr.raw.gz")
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


def _file_digest(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _declared_path(root: Path, value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise UploadError(f"result.json {label} path must be a nonempty string")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or any(part in ("", ".", "..") for part in relative.parts)
    ):
        raise UploadError(f"result.json {label} path is not canonical and relative: {value!r}")
    path = root.joinpath(*relative.parts)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise UploadError(f"result.json declares missing {label} artifact: {value}") from exc
    if resolved != path or not path.is_file():
        raise UploadError(f"result.json {label} artifact is not a contained regular file: {value}")
    return path


def _validate_digest(
    path: Path,
    record: Mapping[str, object],
    *,
    label: str,
    size_field: str,
    digest_field: str,
) -> None:
    size = record.get(size_field)
    digest = record.get(digest_field)
    if type(size) is not int or size < 0:
        raise UploadError(f"result.json {label} {size_field} must be a nonnegative integer")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise UploadError(f"result.json {label} {digest_field} must be a SHA-256 digest")
    actual_size, actual_digest = _file_digest(path)
    if (actual_size, actual_digest) != (size, digest):
        raise UploadError(f"result.json {label} artifact does not match its recorded evidence")


def _native_files(root: Path) -> set[str]:
    native = root / "native"
    if not os.path.lexists(native):
        return set()
    if native.is_symlink() or not native.is_dir():
        raise UploadError("attempt native artifact root is not a contained directory")
    files: set[str] = set()
    pending = [native]
    while pending:
        directory = pending.pop()
        for child in directory.iterdir():
            if child.is_symlink():
                raise UploadError(
                    f"attempt native artifact is a symlink: {child.relative_to(root)}"
                )
            if child.is_dir():
                pending.append(child)
            elif child.is_file():
                files.add(child.relative_to(root).as_posix())
            else:
                raise UploadError(
                    f"attempt native artifact is not a regular file: {child.relative_to(root)}"
                )
    return files


def _validated_upload_files(attempt_dir: Path) -> tuple[Path, list[Path]]:
    """Authenticate the finalized schema-2 evidence set before uploading any byte."""
    try:
        root = attempt_dir.resolve(strict=True)
    except OSError as exc:
        raise UploadError(f"not a finalized attempt directory: {attempt_dir}") from exc
    if not root.is_dir():
        raise UploadError(f"not a finalized attempt directory: {attempt_dir}")
    result_path = root / RESULT_FILE
    if not result_path.is_file() or result_path.is_symlink():
        raise UploadError(f"not a finalized attempt directory: {attempt_dir}")
    try:
        result = json.loads(result_path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UploadError("result.json is not valid UTF-8 JSON") from exc
    if not isinstance(result, dict) or result.get("schema_version") != 2:
        raise UploadError("result.json must be a schema-version-2 object")

    streams = result.get("streams")
    if not isinstance(streams, dict) or set(streams) != {"stdout", "stderr"}:
        raise UploadError("result.json streams must declare exactly stdout and stderr")
    files: list[Path] = []
    for stream, expected_path in zip(("stdout", "stderr"), BULK_FILES, strict=True):
        record = streams[stream]
        if not isinstance(record, dict) or record.get("path") != expected_path:
            raise UploadError(
                f"result.json {stream} path must be the worker-owned {expected_path!r}"
            )
        path = _declared_path(root, record["path"], label=stream)
        _validate_digest(
            path,
            record,
            label=stream,
            size_field="stored_bytes",
            digest_field="stored_sha256",
        )
        files.append(path)

    native_output = result.get("native_output")
    if not isinstance(native_output, list):
        raise UploadError("result.json native_output must be a list")
    declared_native: dict[str, Path] = {}
    for index, record in enumerate(native_output):
        label = f"native_output[{index}]"
        if not isinstance(record, dict):
            raise UploadError(f"result.json {label} must be an object")
        value = record.get("path")
        if not isinstance(value, str) or not value.startswith("native/"):
            raise UploadError(f"result.json {label} path must be under native/")
        if value in declared_native:
            raise UploadError(f"result.json declares native artifact twice: {value}")
        path = _declared_path(root, value, label=label)
        _validate_digest(
            path,
            record,
            label=label,
            size_field="bytes",
            digest_field="sha256",
        )
        declared_native[value] = path
    actual_native = _native_files(root)
    if actual_native != set(declared_native):
        missing = sorted(set(declared_native) - actual_native)
        undeclared = sorted(actual_native - set(declared_native))
        raise UploadError(
            f"native artifact set does not match result.json "
            f"(missing={missing}, undeclared={undeclared})"
        )
    files.extend(declared_native[name] for name in sorted(declared_native))
    return result_path, files


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
    uploader: Uploader | None = None,
) -> list[str]:
    """Preflight all worker-owned evidence, then upload ``result.json`` last."""
    result_path, files = _validated_upload_files(attempt_dir)
    bucket, prefix = parse_destination(destination)
    send = uploader if uploader is not None else _authenticated_uploader()

    uploaded: list[str] = []
    root = result_path.parent
    for local_path in files:
        relative = local_path.relative_to(root).as_posix()
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
