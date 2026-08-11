"""Decide what a run actually has to build, by asking the registry what exists.

The pipeline this replaces ran identical machinery whether a parent was already
published or had to be compiled from source: on a worker-only change it still
staged a shared runtime, restored a tool tarball, started a registry container to
mint digests that already existed, and rebuilt the execution image twice.

The fix is not a fast path. A fast path is a branch someone has to remember to
keep correct. Instead the plan is *sized by what is missing*: every reference is
probed once, and each tool lands in exactly one bucket.

``chain``
    The tool parent is absent, so it must be built — and because its bytes never
    leave the job that built them, that job also builds its execution child.
    Expensive and rare: a tool recipe change, or a first build on a branch.

``bake``
    The tool parent is published but this execution identity is not. Only the
    thin worker layer is new, so every such tool is one target in a single
    ``buildx bake``. This is the common case — any change to ``worker/``,
    ``common/``, or an adapter.

``adopt``
    Both are published. Nothing is built; the existing digest is promoted.

A bucket that ends up empty produces *no job at all*, because an empty
``strategy.matrix`` creates zero jobs rather than one that skips. The shape of
the run on the Actions page is therefore the answer to "what changed".
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from s3_listing_study import __version__
from s3_listing_study.ci import CIError, buildable_tools
from s3_listing_study.ci import tags as tag_grammar
from s3_listing_study.common.build_selection import (
    BuildSelection,
    BuildSelectionError,
    derived_image_source_sha256,
    docker_tag_version,
    load_registered_selection,
    validate_tool_slug,
)


@dataclass(frozen=True, slots=True)
class ToolPlan:
    """One tool's identities, and what remains to be built for it."""

    tool: str
    tool_version: str
    tool_tag_version: str
    worker_version: str
    worker_tag_version: str
    shared_source_sha256: str
    tool_build_sha256: str
    worker_source_sha256: str
    selection_sha256: str
    tool_tag: str
    tool_digest: str | None
    execution_tag: str
    execution_digest: str | None
    execution_channel_tag: str
    planned_bucket: str | None = None
    """The bucket this tool was in when the run was planned.

    Resolving a plan fills every digest in, which would make every tool look
    adopted. The original decision is carried forward so the ledger can state
    truthfully whether an image was built by this run or already existed.
    """

    @property
    def bucket(self) -> str:
        if self.planned_bucket is not None:
            return self.planned_bucket
        if self.tool_digest is None:
            return "chain"
        if self.execution_digest is None:
            return "bake"
        return "adopt"

    @property
    def reuse_source(self) -> str:
        return "adopted" if self.bucket == "adopt" else "built"

    def as_json(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "tool_version": self.tool_version,
            "tool_tag_version": self.tool_tag_version,
            "worker_version": self.worker_version,
            "worker_tag_version": self.worker_tag_version,
            "shared_source_sha256": self.shared_source_sha256,
            "tool_build_sha256": self.tool_build_sha256,
            "worker_source_sha256": self.worker_source_sha256,
            "selection_sha256": self.selection_sha256,
            "tool_tag": self.tool_tag,
            "tool_digest": self.tool_digest,
            "execution_tag": self.execution_tag,
            "execution_digest": self.execution_digest,
            "execution_channel_tag": self.execution_channel_tag,
            "bucket": self.bucket,
            "reuse_source": self.reuse_source,
        }

    @classmethod
    def from_json(cls, document: dict[str, Any]) -> ToolPlan:
        """Re-validate a plan read back from disk.

        A plan crosses a job boundary as a file, and everything downstream — the
        promotion worklist above all — mints registry references out of these
        values. Trusting them because we wrote them earlier is how the one output
        that moves published identity ends up being the one place the package's
        own validators are skipped.
        """
        try:
            for key in ("tool_tag", "execution_tag", "execution_channel_tag"):
                tag_grammar.validate_tag(document[key])
            for key in ("tool_digest", "execution_digest"):
                value = document[key]
                if value is not None and tag_grammar.DIGEST_RE.fullmatch(value) is None:
                    raise CIError(f"{document['tool']}: {key} is not a sha256 digest")
            for key in (
                "shared_source_sha256",
                "tool_build_sha256",
                "worker_source_sha256",
                "selection_sha256",
            ):
                tag_grammar.short(document[key], key)
            if document["bucket"] not in {"chain", "bake", "adopt"}:
                raise CIError(f"{document['tool']}: unknown bucket {document['bucket']!r}")
            validate_tool_slug(document["tool"])
            return cls(
                tool=document["tool"],
                tool_version=document["tool_version"],
                tool_tag_version=document["tool_tag_version"],
                worker_version=document["worker_version"],
                worker_tag_version=document["worker_tag_version"],
                shared_source_sha256=document["shared_source_sha256"],
                tool_build_sha256=document["tool_build_sha256"],
                worker_source_sha256=document["worker_source_sha256"],
                selection_sha256=document["selection_sha256"],
                tool_tag=document["tool_tag"],
                tool_digest=document["tool_digest"],
                execution_tag=document["execution_tag"],
                execution_digest=document["execution_digest"],
                execution_channel_tag=document["execution_channel_tag"],
                planned_bucket=document["bucket"],
            )
        except (KeyError, TypeError) as exc:
            raise CIError(f"malformed tool plan: {exc}") from exc
        except BuildSelectionError as exc:
            raise CIError(f"malformed tool plan: {exc}") from exc

    def resolved(self, *, tool_digest: str | None, execution_digest: str | None) -> ToolPlan:
        """The same tool with registry-reported digests filled in, bucket preserved."""
        return replace(
            self,
            planned_bucket=self.bucket,
            tool_digest=tool_digest or self.tool_digest,
            execution_digest=execution_digest or self.execution_digest,
        )


@dataclass(frozen=True, slots=True)
class Plan:
    """The whole build graph for one run."""

    repository: str
    channel_suffix: str
    shared_source_sha256: str
    shared_tag: str
    shared_digest: str | None
    set_channel_tag: str
    tools: tuple[ToolPlan, ...] = field(default=())

    @property
    def shared_needed(self) -> bool:
        return self.shared_digest is None

    def bucket(self, name: str) -> tuple[ToolPlan, ...]:
        return tuple(item for item in self.tools if item.bucket == name)

    def as_json(self) -> dict[str, Any]:
        return {
            "channel_suffix": self.channel_suffix,
            "kind": "image-build-plan",
            "repository": self.repository,
            "schema_version": 1,
            "shared": {
                "digest": self.shared_digest,
                "needed": self.shared_needed,
                "source_sha256": self.shared_source_sha256,
                "tag": self.shared_tag,
            },
            "set_channel_tag": self.set_channel_tag,
            "tools": [item.as_json() for item in self.tools],
        }

    @classmethod
    def from_json(cls, document: dict[str, Any]) -> Plan:
        """Read back a plan written by an earlier job in the same run."""
        if not isinstance(document, dict) or document.get("kind") != "image-build-plan":
            raise CIError("not an image build plan")
        if document.get("schema_version") != 1:
            raise CIError("unsupported image build plan schema")
        try:
            shared = document["shared"]
            tag_grammar.validate_repository(document["repository"])
            tag_grammar.validate_tag(shared["tag"])
            tag_grammar.validate_tag(document["set_channel_tag"])
            if shared["digest"] is not None and (
                tag_grammar.DIGEST_RE.fullmatch(shared["digest"]) is None
            ):
                raise CIError("shared runtime digest is not a sha256 digest")
            tag_grammar.short(shared["source_sha256"], "shared source_sha256")
            return cls(
                repository=document["repository"],
                channel_suffix=document["channel_suffix"],
                shared_source_sha256=shared["source_sha256"],
                shared_tag=shared["tag"],
                shared_digest=shared["digest"],
                set_channel_tag=document["set_channel_tag"],
                tools=tuple(ToolPlan.from_json(item) for item in document["tools"]),
            )
        except (KeyError, TypeError) as exc:
            raise CIError(f"malformed image build plan: {exc}") from exc

    def resolved(
        self,
        *,
        shared_digest: str | None = None,
        tool_digests: Mapping[str, str] | None = None,
        execution_digests: Mapping[str, str] | None = None,
    ) -> Plan:
        """Fill in the digests a build and its pushes produced, keeping planned buckets."""
        tools = tool_digests or {}
        executions = execution_digests or {}
        return replace(
            self,
            shared_digest=shared_digest or self.shared_digest,
            tools=tuple(
                item.resolved(
                    tool_digest=tools.get(item.tool),
                    execution_digest=executions.get(item.tool),
                )
                for item in self.tools
            ),
        )

    def missing(self) -> tuple[str, ...]:
        """Every identity that still has no digest — the completeness gate before publishing."""
        gaps: list[str] = []
        if not self.tools:
            raise CIError("a publication plan with no tools cannot be complete")
        if not self.shared_digest:
            gaps.append(self.shared_tag)
        for item in self.tools:
            # Falsy rather than None: an empty digest string is absence wearing a
            # value, and it would otherwise mint `repository@` as a reference.
            if not item.tool_digest:
                gaps.append(item.tool_tag)
            if not item.execution_digest:
                gaps.append(item.execution_tag)
        return tuple(gaps)


def project_version(root: Path) -> str:
    """The one harness version, checked against the packaged ``__version__``.

    Two records of the same fact drifting apart would tag an execution image with
    a worker version the image does not contain, so the disagreement fails here
    rather than being published.
    """
    try:
        document = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise CIError(f"cannot read pyproject.toml: {exc}") from exc
    declared = document.get("project", {}).get("version")
    if not isinstance(declared, str) or not declared:
        raise CIError("pyproject.toml declares no project.version")
    if declared != __version__:
        raise CIError(
            f"pyproject project.version ({declared}) differs from package "
            f"__version__ ({__version__})"
        )
    return declared


def tool_plan(
    root: Path,
    selection: BuildSelection,
    *,
    repository: str,
    channel_suffix: str,
    worker_version: str,
    existing: Mapping[str, str | None],
) -> ToolPlan:
    """Derive one tool's identities and look up what the registry already has."""
    worker_source = derived_image_source_sha256(root, selection)
    tool_tag_version = docker_tag_version(selection.tool_version)
    worker_tag_version = docker_tag_version(worker_version)
    tool_reference = tag_grammar.tool_tag(
        repository,
        selection.tool,
        tool_tag_version,
        selection.shared_base_source_sha256,
        selection.tool_build_sha256,
    )
    execution_reference = tag_grammar.execution_tag(
        repository,
        selection.tool,
        tool_tag_version,
        selection.shared_base_source_sha256,
        selection.tool_build_sha256,
        worker_tag_version,
        worker_source,
    )
    return ToolPlan(
        tool=selection.tool,
        tool_version=selection.tool_version,
        tool_tag_version=tool_tag_version,
        worker_version=worker_version,
        worker_tag_version=worker_tag_version,
        shared_source_sha256=selection.shared_base_source_sha256,
        tool_build_sha256=selection.tool_build_sha256,
        worker_source_sha256=worker_source,
        selection_sha256=selection.selection_sha256,
        tool_tag=tool_reference,
        tool_digest=existing.get(tool_reference),
        execution_tag=execution_reference,
        execution_digest=existing.get(execution_reference),
        execution_channel_tag=tag_grammar.execution_channel_tag(
            repository, selection.tool, channel_suffix
        ),
    )


def candidate_references(plan: Plan) -> tuple[str, ...]:
    """Every reference whose existence changes the plan, for one batched probe.

    Taken from a plan built with nothing known to exist, rather than derived a
    second time. Two independent derivations of the tag grammar inside the one
    package that exists to unify it would drift silently: the probe would look up
    references the plan never uses, every lookup would miss, and the run would
    quietly re-bucket the whole roster and republish.
    """
    references = [plan.shared_tag]
    for item in plan.tools:
        references.extend((item.tool_tag, item.execution_tag))
    return tuple(sorted(set(references)))


def _selection(root: Path, tool: str) -> BuildSelection:
    try:
        return load_registered_selection(root, tool)
    except BuildSelectionError as exc:
        raise CIError(f"{tool}: {exc}") from exc


def build_plan(
    root: Path,
    *,
    repository: str,
    ref_name: str,
    is_main_publication: bool,
    existing: Mapping[str, str | None],
    tools: tuple[str, ...] | None = None,
) -> Plan:
    """Assemble the graph. Pure over ``existing`` so the decisions are testable."""
    tag_grammar.validate_repository(repository)
    roster = buildable_tools(root) if tools is None else tools
    if not roster:
        raise CIError("no tools selected")
    unknown = sorted(set(roster) - set(buildable_tools(root)))
    if unknown:
        raise CIError(f"unknown tool(s) requested: {', '.join(unknown)}")
    suffix = tag_grammar.channel_suffix(ref_name, is_main_publication=is_main_publication)
    worker_version = project_version(root)

    selections = [_selection(root, tool) for tool in roster]
    shared_sources = {selection.shared_base_source_sha256 for selection in selections}
    if len(shared_sources) != 1:
        raise CIError("registered tools do not agree on one shared-base source identity")
    shared_source = shared_sources.pop()
    shared_reference = tag_grammar.shared_tag(repository, shared_source)

    plans = tuple(
        tool_plan(
            root,
            selection,
            repository=repository,
            channel_suffix=suffix,
            worker_version=worker_version,
            existing=existing,
        )
        for selection in selections
    )
    # A published execution image was built on a published parent. If the parent
    # is missing while its child is present, the registry has been edited outside
    # this pipeline and no automatic action is safe.
    orphaned = [item.tool for item in plans if item.tool_digest is None and item.execution_digest]
    if orphaned:
        raise CIError(
            "published execution image has no published tool parent: " + ", ".join(orphaned)
        )
    return Plan(
        repository=repository,
        channel_suffix=suffix,
        shared_source_sha256=shared_source,
        shared_tag=shared_reference,
        shared_digest=existing.get(shared_reference),
        set_channel_tag=tag_grammar.set_channel_tag(repository, suffix),
        tools=plans,
    )


def render_outputs(plan: Plan) -> dict[str, str]:
    """The job outputs the workflow consumes, matrices already JSON-encoded."""
    return {
        "channel-suffix": plan.channel_suffix,
        "shared-tag": plan.shared_tag,
        "shared-needed": "true" if plan.shared_needed else "false",
        "shared-digest": plan.shared_digest or "",
        "set-channel-tag": plan.set_channel_tag,
        "chain-matrix": json.dumps([item.tool for item in plan.bucket("chain")]),
        "bake-tools": json.dumps([item.tool for item in plan.bucket("bake")]),
        "adopt-tools": json.dumps([item.tool for item in plan.bucket("adopt")]),
        "build-needed": (
            "true" if plan.bucket("chain") or plan.bucket("bake") or plan.shared_needed else "false"
        ),
    }
