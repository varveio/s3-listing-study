"""Profile-bound, marker-last publication over a caller-supplied object store."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import BinaryIO, Generic, Protocol, TypeVar

from twinstamp.profiles import EvidenceProfile

U = TypeVar("U")
Version = str | int
PayloadOpener = Callable[[], BinaryIO]

_SHA256 = re.compile(r"[0-9a-f]{64}")


def _valid_version(version: object) -> bool:
    return isinstance(version, (str, int)) and not isinstance(version, bool) and version != ""


def _canonical_relative_name(name: str) -> bool:
    if not name or name.startswith("/") or name.endswith("/") or "\\" in name or "\x00" in name:
        return False
    return all(part not in ("", ".", "..") for part in name.split("/"))


@dataclass(frozen=True, slots=True)
class PublicationObject:
    """One immutable payload and its expected bytes, without buffering the payload."""

    name: str
    size: int
    sha256: str
    open_payload: PayloadOpener = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not _canonical_relative_name(self.name):
            raise ValueError(
                f"publication object name is not canonical and relative: {self.name!r}"
            )
        if type(self.size) is not int or self.size < 0:
            raise ValueError("publication object size must be a nonnegative integer")
        if _SHA256.fullmatch(self.sha256) is None:
            raise ValueError("publication object sha256 must be 64 lowercase hexadecimal digits")


@dataclass(frozen=True, slots=True)
class ObjectCreated:
    """The create completed and exposed an immutable store version."""

    version: Version

    def __post_init__(self) -> None:
        if not _valid_version(self.version):
            raise ValueError("created object version must be a nonempty string or integer")


@dataclass(frozen=True, slots=True)
class ObjectConflict:
    """The create-only precondition definitively found an existing object."""


@dataclass(frozen=True, slots=True)
class ObjectCreateAmbiguous:
    """The create may have taken effect and requires exact read-back."""

    detail: str | None = None


ObjectCreateResult = ObjectCreated | ObjectConflict | ObjectCreateAmbiguous


@dataclass(frozen=True, slots=True)
class ObjectReadBack:
    """One bounded read-back stream and the version observed with those bytes."""

    version: Version | None
    chunks: Iterable[bytes]


class PublicationStore(Protocol):
    """Minimal mutation/read surface required by :func:`publish`."""

    def create(self, key: str, payload: PublicationObject) -> ObjectCreateResult: ...

    def read_back(self, key: str, *, max_bytes: int) -> ObjectReadBack | None: ...


@dataclass(frozen=True, slots=True)
class EvidencePublication(Generic[U]):
    """A fixed, profile-bound artifact sequence followed by exactly one marker."""

    prefix: str
    profile: EvidenceProfile[U]
    unit: U
    artifacts: tuple[PublicationObject, ...]
    marker: PublicationObject

    def __post_init__(self) -> None:
        if not isinstance(self.artifacts, tuple) or not all(
            isinstance(artifact, PublicationObject) for artifact in self.artifacts
        ):
            raise TypeError("publication artifacts must be a fixed tuple of publication objects")
        if not isinstance(self.marker, PublicationObject):
            raise TypeError("publication marker must be one publication object")

    @property
    def unit_key(self) -> str:
        try:
            key = self.profile.render(self.unit)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("publication unit does not belong to its evidence profile") from exc
        if self.profile.parse(key) != self.unit:
            raise ValueError("publication unit does not round-trip through its evidence profile")
        return key

    @property
    def unit_prefix(self) -> str:
        return f"{self.prefix.rstrip('/')}/{self.unit_key}" if self.prefix else self.unit_key


@dataclass(frozen=True, slots=True)
class PublishedObject:
    name: str
    key: str
    version: Version
    ambiguous_create_resolved: bool = False


@dataclass(frozen=True, slots=True)
class PublicationReceipt(Generic[U]):
    publication: EvidencePublication[U]
    objects: tuple[PublishedObject, ...]


class PublicationRefused(RuntimeError):
    """Publication stopped without creating any later object or marker."""

    def __init__(self, object_key: str, reason: str) -> None:
        super().__init__(f"{object_key}: {reason}")
        self.object_key = object_key
        self.reason = reason


def _digest_payload(payload: PublicationObject) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with payload.open_payload() as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _preflight(publication: EvidencePublication[U]) -> str:
    # Resolve the typed identity and validate the complete fixed manifest before
    # opening the create surface. Payloads are streamed and discarded.
    unit_prefix = publication.unit_prefix
    ordered = (*publication.artifacts, publication.marker)
    names = [payload.name for payload in ordered]
    if len(names) != len(set(names)):
        raise ValueError("publication object names must be unique")
    for payload in ordered:
        try:
            observed = _digest_payload(payload)
        except OSError as exc:
            raise PublicationRefused(payload.name, f"payload preflight failed: {exc}") from exc
        if observed != (payload.size, payload.sha256):
            raise PublicationRefused(
                payload.name, "payload does not match its declared size/sha256"
            )
    return unit_prefix


def _resolve_ambiguous(store: PublicationStore, key: str, payload: PublicationObject) -> Version:
    try:
        observed = store.read_back(key, max_bytes=payload.size + 1)
        if observed is None:
            raise PublicationRefused(key, "ambiguous create read-back found no object")
        if not _valid_version(observed.version):
            raise PublicationRefused(key, "ambiguous create read-back exposed no object version")
        digest = hashlib.sha256()
        size = 0
        for chunk in observed.chunks:
            size += len(chunk)
            if size > payload.size:
                raise PublicationRefused(key, "ambiguous create read-back did not match")
            digest.update(chunk)
    except PublicationRefused:
        raise
    except Exception as exc:
        raise PublicationRefused(key, f"ambiguous create read-back failed: {exc}") from exc
    if (size, digest.hexdigest()) != (payload.size, payload.sha256):
        raise PublicationRefused(key, "ambiguous create read-back did not match")
    assert observed.version is not None
    return observed.version


def publish(publication: EvidencePublication[U], store: PublicationStore) -> PublicationReceipt[U]:
    """Create a fixed evidence unit in order, resolving ambiguity and sealing last."""

    unit_prefix = _preflight(publication)
    written: list[PublishedObject] = []
    for payload in (*publication.artifacts, publication.marker):
        key = f"{unit_prefix}/{payload.name}" if unit_prefix else payload.name
        outcome = store.create(key, payload)
        if isinstance(outcome, ObjectConflict):
            raise PublicationRefused(key, "create conflict; evidence unit was not sealed")
        if isinstance(outcome, ObjectCreated):
            ambiguous = False
            version = outcome.version
        else:
            ambiguous = True
            version = _resolve_ambiguous(store, key, payload)
        written.append(PublishedObject(payload.name, key, version, ambiguous))
    return PublicationReceipt(publication, tuple(written))
