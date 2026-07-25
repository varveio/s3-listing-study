"""Registry access: read ``data/registry.toml``.

Two properties decide whether a receipt can be trusted, and both are enforced
here.

**All ambiguity is fatal.** A second ``Manifest sha256`` entry silently
resolving to whichever came first would put a plausible wrong digest in a
receipt — worse than no receipt at all. TOML gets that for free: redefining a
table or a key is a parse error, so duplication cannot reach the lookup.
Everything the format does not enforce is validated on load — required fields,
digest shape, date shape, key count, and an outright rejection of unknown
fields, since an unknown field is a misspelling of a real one and a misspelling
that parses is the same silent-wrong-value bug.

**A receipt can prove which registry produced it.** :attr:`Registry.path` and
:attr:`Registry.digest` are the ``--path`` / ``--digest`` equivalents; the digest
is the sha256 of the data file itself, so a receipt citing it binds to exact
bytes. There is deliberately no environment override of the registry location:
an environment variable is how official-looking evidence gets produced from a
registry nobody reviewed, so redirection is an explicit argument at the call
site or nothing.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The fields a caller may resolve for a bucket, in registry order.
FIELDS = ("region", "manifest", "manifest_sha256", "snapshot_date", "keys", "shape")

# Anchored with ``\Z``, never ``$``, and applied with ``fullmatch``: Python's
# ``$`` also matches before a trailing newline, so ``^…$`` would accept a digest
# or a date carrying one — and TOML strings can carry one.
SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
DATE_RE = re.compile(r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


class RegistryError(Exception):
    """The registry is unreadable, malformed, or does not carry what was asked."""


@dataclass(frozen=True)
class Bucket:
    """One registered bucket's facts."""

    name: str
    region: str
    manifest: str
    manifest_sha256: str
    snapshot_date: str
    keys: int
    shape: str

    def field(self, field: str) -> str:
        """The value of ``field`` rendered as the registry states it."""
        if field not in FIELDS:
            raise RegistryError(f"unknown field {field!r} ({'|'.join(FIELDS)})")
        value = getattr(self, field)
        return str(value)


@dataclass(frozen=True)
class Registry:
    """Every registered bucket, plus the bytes it was read from."""

    path: Path
    digest: str
    harness_image: str
    buckets: dict[str, Bucket]

    @classmethod
    def load(cls, path: Path | None = None) -> Registry:
        return _load(default_path() if path is None else path)

    def bucket(self, name: str) -> Bucket:
        try:
            return self.buckets[name]
        except KeyError:
            raise RegistryError(
                f"bucket {name!r} is not registered in {self.path} "
                "(register it before running anything against it)"
            ) from None

    def bucket_names(self) -> list[str]:
        return sorted(self.buckets)

    def field(self, bucket: str, field: str) -> str:
        return self.bucket(bucket).field(field)


def default_path() -> Path:
    """``data/registry.toml`` at the repo root."""
    return Path(__file__).resolve().parents[2] / "data" / "registry.toml"


def _load(path: Path) -> Registry:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RegistryError(f"registry not readable: {path} ({exc.strerror})") from None
    try:
        doc = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise RegistryError(f"registry is not valid TOML: {path}: {exc}") from None

    _reject_unknown(doc, ("harness_client", "buckets"), "registry", path)
    return Registry(
        path=path,
        digest=hashlib.sha256(raw).hexdigest(),
        harness_image=_harness_image(doc, path),
        buckets=_buckets(doc, path),
    )


def _reject_unknown(
    table: dict[str, Any], allowed: tuple[str, ...], where: str, path: Path
) -> None:
    unknown = sorted(set(table) - set(allowed))
    if unknown:
        raise RegistryError(
            f"{where} in {path} carries unknown key(s) {', '.join(repr(k) for k in unknown)} "
            f"({'|'.join(allowed)})"
        )


def _table(doc: dict[str, Any], key: str, where: str, path: Path) -> dict[str, Any]:
    value = doc.get(key)
    if value is None:
        raise RegistryError(f"registry {path} has no [{where}] table")
    if not isinstance(value, dict):
        raise RegistryError(f"[{where}] in {path} is not a table")
    return value


def _string(table: dict[str, Any], key: str, where: str, path: Path) -> str:
    value = table.get(key)
    if value is None:
        raise RegistryError(f"[{where}] in {path} has no '{key}'")
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"[{where}] '{key}' in {path} is not a non-empty string")
    return value


def _harness_image(doc: dict[str, Any], path: Path) -> str:
    table = _table(doc, "harness_client", "harness_client", path)
    _reject_unknown(table, ("image",), "[harness_client]", path)
    image = _string(table, "image", "harness_client", path)
    if "@sha256:" not in image:
        raise RegistryError(f"harness image is not digest-pinned: {image}")
    return image


def _buckets(doc: dict[str, Any], path: Path) -> dict[str, Bucket]:
    table = _table(doc, "buckets", "buckets", path)
    if not table:
        raise RegistryError(f"registry {path} registers no buckets")
    return {name: _bucket(name, table, path) for name in table}


def _bucket(name: str, buckets: dict[str, Any], path: Path) -> Bucket:
    where = f'buckets."{name}"'
    table = _table(buckets, name, where, path)
    _reject_unknown(table, FIELDS, f"[{where}]", path)

    manifest = _string(table, "manifest", where, path)
    # Expand a leading ~ ourselves: no shell ever sees this string, and a receipt
    # that cited a literal "~" would not name a path anyone could check.
    if manifest.startswith("~/"):
        manifest = f"{Path.home()}/{manifest[2:]}"

    manifest_sha256 = _string(table, "manifest_sha256", where, path)
    if not SHA256_RE.fullmatch(manifest_sha256):
        raise RegistryError(
            f"manifest_sha256 for {name!r} is not a 64-hex digest: {manifest_sha256!r}"
        )

    snapshot_date = _string(table, "snapshot_date", where, path)
    if not DATE_RE.fullmatch(snapshot_date):
        raise RegistryError(f"snapshot_date for {name!r} is not YYYY-MM-DD: {snapshot_date!r}")

    # 0 is legal: a registered bucket that is legitimately empty must stay
    # resolvable rather than be refused as malformed.
    keys = table.get("keys")
    if not isinstance(keys, int) or isinstance(keys, bool) or keys < 0:
        raise RegistryError(f"keys for {name!r} is not a non-negative integer: {keys!r}")

    # Absent or empty is fatal: the receipt is required to carry the shape, and a
    # receipt silently missing it looks complete while omitting the context that
    # makes its numbers mean anything.
    shape = _string(table, "shape", where, path).strip("\n")

    return Bucket(
        name=name,
        region=_string(table, "region", where, path),
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        snapshot_date=snapshot_date,
        keys=keys,
        shape=shape,
    )
