from __future__ import annotations

import json
from pathlib import Path

import pytest

from s3_listing_study.worker.image_provenance import (
    ImageProvenanceError,
    load_image_provenance,
)


def _write(path: Path, *, schema: int, worker: str | None = None) -> None:
    value: dict[str, object] = {
        "schema_version": schema,
        "selection_sha256": "a" * 64,
        "tool_image": {
            "digest": "sha256:" + "b" * 64,
            "uri": "ghcr.io/example/image@sha256:" + "b" * 64,
        },
    }
    if worker is not None:
        value["worker_source_sha256"] = worker
    path.write_text(json.dumps(value), encoding="utf-8")


def test_schema_two_exposes_worker_source_identity(tmp_path: Path) -> None:
    path = tmp_path / "provenance.json"
    _write(path, schema=2, worker="c" * 64)

    assert load_image_provenance(path)["worker_source_sha256"] == "c" * 64


def test_schema_one_remains_readable(tmp_path: Path) -> None:
    path = tmp_path / "provenance.json"
    _write(path, schema=1)

    assert "worker_source_sha256" not in load_image_provenance(path)


@pytest.mark.parametrize("worker", [None, "c" * 63, "C" * 64])
def test_schema_two_rejects_missing_or_invalid_worker_identity(
    tmp_path: Path, worker: str | None
) -> None:
    path = tmp_path / "provenance.json"
    _write(path, schema=2, worker=worker)

    with pytest.raises(ImageProvenanceError):
        load_image_provenance(path)
