"""Execute the language-neutral publication vectors through writer and reader."""

from __future__ import annotations

import base64
import hashlib
import io
import json
from importlib.resources import files
from typing import Any, BinaryIO, cast

import pytest

from twinstamp.identity import Submission
from twinstamp.profiles import PHYSICAL_EXECUTION, PhysicalExecutionUnit
from twinstamp.publication import (
    EvidencePublication,
    ObjectConflict,
    ObjectCreateAmbiguous,
    ObjectCreated,
    ObjectCreateResult,
    ObjectReadBack,
    PublicationObject,
    PublicationRefused,
    publish,
)
from twinstamp.reconcile import reconcile
from twinstamp.resolution import (
    CanonicalEvidenceUnit,
    LeafAssessment,
    SealState,
    SelectionState,
    SlotResolution,
)
from twinstamp.sealcheck import CanonicalJsonMarker, MarkerIssue
from twinstamp.stores import StoredObject
from twinstamp.testing import MemoryObjectStore


def _vectors() -> dict[str, Any]:
    resource = files("twinstamp").joinpath("golden/publication-v1.json")
    return cast(dict[str, Any], json.loads(resource.read_text(encoding="utf-8")))


VECTORS = _vectors()
CASES = cast(list[dict[str, Any]], VECTORS["cases"])


def _payload(record: dict[str, Any]) -> tuple[PublicationObject, bytes]:
    content = base64.b64decode(record["content_base64"])
    assert len(content) == record["size"]
    assert hashlib.sha256(content).hexdigest() == record["sha256"]

    def open_payload() -> BinaryIO:
        return io.BytesIO(content)

    return (
        PublicationObject(record["name"], record["size"], record["sha256"], open_payload),
        content,
    )


def _publication(
    marker_selector: str,
) -> tuple[EvidencePublication, dict[str, bytes]]:
    artifacts_and_bytes = [_payload(record) for record in VECTORS["artifacts"]]
    marker, marker_bytes = _payload(cast(dict[str, Any], VECTORS[marker_selector]))
    unit = PHYSICAL_EXECUTION.parse(VECTORS["unit"])
    assert unit is not None
    publication = EvidencePublication.for_unit(
        VECTORS["prefix"],
        PHYSICAL_EXECUTION,
        unit,
        tuple(item for item, _content in artifacts_and_bytes),
        marker,
    )
    contents = {item.name: content for item, content in artifacts_and_bytes}
    contents[marker.name] = marker_bytes
    return publication, contents


class _VectorStore:
    def __init__(self, *, action: str, at: str | None = None) -> None:
        self.action = action
        self.at = at
        self.objects: dict[str, bytes] = {}
        self.create_calls: list[str] = []
        self.read_calls: list[str] = []

    def create(self, key: str, payload: PublicationObject) -> ObjectCreateResult:
        self.create_calls.append(key)
        content = payload.open_payload().read()
        if payload.name == self.at and self.action == "conflict":
            return ObjectConflict()
        self.objects[key] = (
            b"mismatch"
            if payload.name == self.at and self.action == "ambiguous-mismatch"
            else content
        )
        if payload.name == self.at and self.action.startswith("ambiguous-"):
            return ObjectCreateAmbiguous("golden injected ambiguity")
        return ObjectCreated(str(len(self.create_calls)))

    def read_back(self, key: str, *, max_bytes: int) -> ObjectReadBack | None:
        self.read_calls.append(key.rsplit("/", 1)[-1])
        content = self.objects.get(key)
        return None if content is None else ObjectReadBack("read-version", (content,))


def _resolution(
    objects: dict[str, bytes],
) -> SlotResolution[PhysicalExecutionUnit, object]:
    store = MemoryObjectStore(objects)

    def validate(
        candidate: CanonicalEvidenceUnit[PhysicalExecutionUnit], _submission: Submission
    ) -> LeafAssessment[PhysicalExecutionUnit, object]:
        document = candidate.marker.document
        assert document is not None
        if document != VECTORS["marker"]["canonical_tree"]:
            return LeafAssessment.invalid(document)
        manifest = document.get("artifact_manifest")
        if not isinstance(manifest, list):
            return LeafAssessment.invalid(document)
        for record in manifest:
            if not isinstance(record, dict):
                return LeafAssessment.invalid(document)
            name, size, digest = record.get("name"), record.get("size"), record.get("sha256")
            if not isinstance(name, str) or type(size) is not int or not isinstance(digest, str):
                return LeafAssessment.invalid(document)
            stored: StoredObject | None = store.read_object(
                f"{VECTORS['prefix']}/{candidate.key}/{name}", max_bytes=size + 1
            )
            if (
                stored is None
                or len(stored.content) != size
                or hashlib.sha256(stored.content).hexdigest() != digest
            ):
                return LeafAssessment.invalid(document)
        return LeafAssessment.valid(document, marker_key=candidate.marker.key)

    return reconcile(
        store,
        VECTORS["prefix"],
        PHYSICAL_EXECUTION,
        Submission("golden-submission"),
        CanonicalJsonMarker("result.json", max_bytes=4096),
        validate,
    )


_CASE_KEYS = {
    "publish": {"name", "action", "marker", "objects", "outcome"},
    "torn": {"name", "action", "marker", "stop_after", "objects", "outcome"},
    "conflict": {"name", "action", "marker", "at", "objects", "outcome"},
    "ambiguous-exact": {
        "name",
        "action",
        "marker",
        "at",
        "objects",
        "read_back",
        "outcome",
    },
    "ambiguous-mismatch": {
        "name",
        "action",
        "marker",
        "at",
        "objects",
        "read_back",
        "outcome",
    },
    "reader": {"name", "action", "marker", "outcome"},
}


def _assert_outcome(
    case: dict[str, Any], resolution: SlotResolution[PhysicalExecutionUnit, object]
) -> None:
    outcome = case["outcome"]
    if outcome == "sealed":
        assert resolution.selection.state is SelectionState.SELECTED
        assert resolution.leaves[0].assessment.seal_state is SealState.VALID
    elif outcome in ("unsealed", "refused-unsealed"):
        assert resolution.selection.state is SelectionState.UNSEALED
        assert resolution.leaves[0].assessment.seal_state is SealState.UNSEALED
    elif outcome == "invalid":
        assert resolution.selection.state is SelectionState.INVALID
        assert resolution.leaves[0].assessment.seal_state is SealState.INVALID
    else:
        pytest.fail(f"unknown golden outcome: {outcome!r}")


@pytest.mark.parametrize("case", CASES, ids=lambda case: cast(str, case["name"]))
def test_publication_golden_case(case: dict[str, Any]) -> None:
    action = cast(str, case["action"])
    expected_keys = set(_CASE_KEYS[action])
    if "corrupt_artifact" in case:
        expected_keys.add("corrupt_artifact")
    if "marker_issue" in case:
        expected_keys.add("marker_issue")
    assert set(case) == expected_keys

    marker_selector = cast(str, case["marker"])
    publication, contents = _publication(marker_selector)
    if "marker_issue" not in case:
        assert VECTORS[marker_selector]["sha256"] == VECTORS["marker"]["sha256"]
    objects: dict[str, bytes]
    if action == "torn":
        objects = {}
        stopped = False
        for name, content in contents.items():
            if name == publication.marker.name:
                break
            objects[f"{publication.prefix}/{name}"] = content
            if name == case["stop_after"]:
                stopped = True
                break
        assert stopped
    elif action == "reader":
        objects = {f"{publication.prefix}/{name}": content for name, content in contents.items()}
        corrupt = case.get("corrupt_artifact")
        if corrupt is not None:
            key = f"{publication.prefix}/{corrupt}"
            assert key in objects
            objects[key] = b"X" + objects[key][1:]
    else:
        store = _VectorStore(action=action, at=case.get("at"))
        if case["outcome"] == "refused-unsealed":
            with pytest.raises(PublicationRefused):
                publish(publication, store)
        else:
            receipt = publish(publication, store)
            assert [item.name for item in receipt.objects] == case["objects"]
        objects = store.objects
        assert [key.rsplit("/", 1)[-1] for key in objects] == case["objects"]
        if "read_back" in case:
            assert store.read_calls == case["read_back"]

    if "objects" in case and action == "torn":
        assert [key.rsplit("/", 1)[-1] for key in objects] == case["objects"]
    resolution = _resolution(objects)
    _assert_outcome(case, resolution)
    if "marker_issue" in case:
        assert resolution.leaves[0].marker is not None
        assert resolution.leaves[0].marker.issue == MarkerIssue(case["marker_issue"])


def test_all_payloads_preflight_before_first_create() -> None:
    publication, _contents = _publication("marker")
    bad_marker = PublicationObject(
        publication.marker.name,
        publication.marker.size,
        "0" * 64,
        publication.marker.open_payload,
    )
    store = _VectorStore(action="publish")
    with pytest.raises(PublicationRefused, match="declared size/sha256"):
        publish(
            EvidencePublication(
                publication.prefix,
                publication.artifacts,
                bad_marker,
            ),
            store,
        )
    assert store.create_calls == []


def test_noncanonical_contained_name_refuses_before_create() -> None:
    with pytest.raises(ValueError, match="not canonical"):
        PublicationObject("native/../escape", 0, hashlib.sha256(b"").hexdigest(), io.BytesIO)
