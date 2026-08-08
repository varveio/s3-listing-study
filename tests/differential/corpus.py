"""Discover the replayable corpus and recover each case's original invocation.

The corpus is 57 single-receipt verdicts (every committed `verify.md`) plus 2
unions (every committed `union-verify.md`). The 73 committed `run.meta` also
cover capability probes and BLOCKED s3-fast-list runs; those carry no verdict, so
there is nothing to replay against and they are not part of the corpus.

Every fact needed to re-issue a verdict is read back from committed artifacts —
scope from the `verify.md` Scope row, stream from `run.meta`, union shard set from
the `union-verify.md` shard table — never inferred from directory layout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# sha256 of zero bytes: how run.meta records a stream that carried nothing.
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

_SCOPE_ROW = re.compile(r"^\| Scope \| `(?P<scope>.*)` \|$", re.M)
_SHARD_ROW = re.compile(r"^\| (?P<idx>\d+) \| (?P<prefix>.*?) \| `(?P<receipt>[^`]+)` \|$", re.M)
_REMAINDER = "<remainder>"


class CorpusError(Exception):
    """A committed artifact could not be read back into an invocation."""


def read_meta(path: Path) -> dict[str, str]:
    """Parse a `run.meta`. First occurrence of a key wins, as awk -F= does."""
    meta: dict[str, str] = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key not in meta:
            meta[key] = value
    return meta


@dataclass(frozen=True)
class Payload:
    """A raw listing the replay will feed back through the verifier.

    `path` is spelled exactly as `run.meta` records it — relative to the link
    farm, so `receipts/...` lands in the external data dir and `tools/...` in the
    repo. `sha256` is the binding the committed receipt already carried, which is
    the only thing the preflight accepts the bytes on.
    """

    path: str
    sha256: str


@dataclass(frozen=True)
class SingleCase:
    """One committed `verify.md` and the invocation that produced it."""

    receipt: Path  # absolute, inside the repo
    rel: str  # repo-relative, for reporting
    tool: str
    scope_args: list[str]
    stream: str  # stdout | stderr
    input_path: str  # exactly as run.meta records it
    payloads: list[Payload]

    @property
    def name(self) -> str:
        return self.rel


@dataclass(frozen=True)
class UnionCase:
    """One committed `union-verify.md` and the shard set it names."""

    union_md: Path
    rel: str
    tool: str
    shards: list[str]  # receipt paths exactly as the shard table spells them
    remainder: str | None
    payloads: list[Payload]

    @property
    def name(self) -> str:
        return self.rel


def parse_scope(verify_md: Path) -> list[str]:
    """Rebuild the --scope arguments from the committed Scope row."""
    text = verify_md.read_text()
    match = _SCOPE_ROW.search(text)
    if not match:
        raise CorpusError(f"no Scope row in {verify_md}")
    scope = match.group("scope")

    if scope == "full":
        return ["--scope", "full"]
    if scope.startswith("delimiter="):
        body = scope[len("delimiter=") :]
        delim, _, prefix = body.partition(" prefix=")
        args = ["--scope", "delimiter", "--scope-delimiter", delim]
        if prefix and prefix != "<none>":
            args += ["--scope-prefix", prefix]
        return args
    if scope.startswith("prefix="):
        return ["--scope", "prefix", "--scope-prefix", scope[len("prefix=") :]]
    raise CorpusError(f"unrecognized Scope '{scope}' in {verify_md}")


def select_stream(meta: dict[str, str], receipt: Path) -> tuple[str, str]:
    """Pick the verified stream from run.meta, not from what happens to be on disk.

    Mirrors the verifier's own union heuristic: prefer the stream that actually
    carries content, stdout winning ties. "Carries content" is decided by the
    recorded sha256, so the choice is a property of the record rather than of the
    current filesystem.
    """
    candidates = []
    for stream in ("stdout", "stderr"):
        path = meta.get(f"{stream}_path", "")
        sha = meta.get(f"{stream}_sha256", "")
        if path and sha:
            candidates.append((stream, path, sha))
    for stream, path, sha in candidates:
        if sha != EMPTY_SHA256:
            return stream, path
    if candidates:
        stream, path, _ = candidates[0]
        return stream, path
    raise CorpusError(f"run.meta in {receipt} records no stdout/stderr payload")


def discover_singles(repo: Path) -> list[SingleCase]:
    cases = []
    for verify_md in sorted(repo.glob("tools/**/verify.md")):
        receipt = verify_md.parent
        meta_path = receipt / "run.meta"
        if not meta_path.is_file():
            raise CorpusError(f"committed verdict without a run.meta: {verify_md}")
        meta = read_meta(meta_path)
        tool = meta.get("tool", "")
        if not tool:
            raise CorpusError(f"run.meta in {receipt} names no tool")
        stream, input_path = select_stream(meta, receipt)
        cases.append(
            SingleCase(
                receipt=receipt,
                rel=str(receipt.relative_to(repo)),
                tool=tool,
                scope_args=parse_scope(verify_md),
                stream=stream,
                input_path=input_path,
                payloads=[Payload(input_path, meta.get(f"{stream}_sha256", ""))],
            )
        )
    return cases


def parse_union_shards(union_md: Path) -> tuple[list[str], str | None]:
    """Read the shard set out of the committed shard table.

    Deliberately NOT inferred from directory layout: the aws-cli union pulls its
    fourth prefix shard (`s3api-v2-text-hourly`) from outside the fanout directory,
    and a layout heuristic silently drops it. The table is also the only record of
    shard *order*, which the regenerated report reproduces column for column.
    """
    text = union_md.read_text()
    section = text.split("## Shards", 1)
    if len(section) != 2:
        raise CorpusError(f"no shard table in {union_md}")
    body = section[1].split("## Counts", 1)[0]

    shards: list[str] = []
    remainder: str | None = None
    for idx, match in enumerate(_SHARD_ROW.finditer(body)):
        if int(match.group("idx")) != idx:
            raise CorpusError(f"shard table in {union_md} is not index-ordered")
        receipt = match.group("receipt")
        shards.append(receipt)
        if match.group("prefix") == _REMAINDER:
            if remainder is not None:
                raise CorpusError(f"shard table in {union_md} names two remainders")
            remainder = receipt
    if not shards:
        raise CorpusError(f"shard table in {union_md} lists no shards")
    return shards, remainder


def discover_unions(repo: Path) -> list[UnionCase]:
    cases = []
    for union_md in sorted(repo.glob("tools/**/union-verify.md")):
        shards, remainder = parse_union_shards(union_md)
        payloads = []
        for shard in shards:
            meta_path = repo / shard / "run.meta"
            if not meta_path.is_file():
                raise CorpusError(f"union shard has no run.meta: {shard}")
            shard_meta = read_meta(meta_path)
            stream, path = select_stream(shard_meta, repo / shard)
            payloads.append(Payload(path, shard_meta.get(f"{stream}_sha256", "")))
        tool = read_meta(repo / shards[0] / "run.meta").get("tool", "")
        if not tool:
            raise CorpusError(f"run.meta in {shards[0]} names no tool")
        cases.append(
            UnionCase(
                union_md=union_md,
                rel=str(union_md.parent.relative_to(repo)),
                tool=tool,
                shards=shards,
                remainder=remainder,
                payloads=payloads,
            )
        )
    return cases
