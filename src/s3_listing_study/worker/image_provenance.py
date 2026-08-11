"""Read immutable execution-image provenance baked beside the worker."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Final

PROVENANCE_PATH: Final = Path("/opt/s3-listing-study/image-provenance.json")
DIGEST_RE: Final = re.compile(r"sha256:[0-9a-f]{64}")


class ImageProvenanceError(ValueError):
    """The execution image's baked provenance is absent or malformed."""


def load_image_provenance(path: Path = PROVENANCE_PATH) -> dict[str, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ImageProvenanceError(f"cannot read image provenance: {exc}") from exc
    if not isinstance(value, dict):
        raise ImageProvenanceError("image provenance has an unexpected field set")
    schema_version = value.get("schema_version")
    expected_fields = {"schema_version", "selection_sha256", "tool_image"}
    if schema_version == 2:
        expected_fields.add("worker_source_sha256")
    elif schema_version != 1:
        raise ImageProvenanceError("unsupported image provenance schema")
    if set(value) != expected_fields:
        raise ImageProvenanceError("image provenance has an unexpected field set")
    selection = value["selection_sha256"]
    tool_image = value["tool_image"]
    if not isinstance(selection, str) or re.fullmatch(r"[0-9a-f]{64}", selection) is None:
        raise ImageProvenanceError("image provenance selection digest is invalid")
    if not isinstance(tool_image, dict) or set(tool_image) != {"digest", "uri"}:
        raise ImageProvenanceError("image provenance tool image is invalid")
    digest = tool_image["digest"]
    uri = tool_image["uri"]
    if (
        not isinstance(digest, str)
        or DIGEST_RE.fullmatch(digest) is None
        or not isinstance(uri, str)
        or not uri.endswith("@" + digest)
    ):
        raise ImageProvenanceError("image provenance tool image identity is invalid")
    result = {
        "selection_sha256": selection,
        "tool_image_digest": digest,
        "tool_image_uri": uri,
    }
    if schema_version == 2:
        worker_source_sha256 = value["worker_source_sha256"]
        if (
            not isinstance(worker_source_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", worker_source_sha256) is None
        ):
            raise ImageProvenanceError("image provenance worker source digest is invalid")
        result["worker_source_sha256"] = worker_source_sha256
    return result
