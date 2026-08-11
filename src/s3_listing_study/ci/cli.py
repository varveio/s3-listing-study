"""``s3-listing-study ci …`` — everything the image workflow needs to decide.

Each subcommand is one step the workflow used to perform as an embedded
heredoc. They are ordinary commands: runnable on a laptop against the real
registry, so the owner can see the whole build graph in a second instead of
spending a seven-minute run to find out.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from s3_listing_study.ci import CIError, buildable_tools
from s3_listing_study.ci import registry as registry_module
from s3_listing_study.ci.bake import assert_metadata_digests, write_bake_definition
from s3_listing_study.ci.plan import (
    Plan,
    build_plan,
    candidate_references,
    project_version,
    render_outputs,
)
from s3_listing_study.ci.publication import (
    promotion_report,
    promotion_summary_rows,
    publication_manifest,
)
from s3_listing_study.ci.tags import (
    DIGEST_RE,
    digest_reference,
    set_ledger_tag,
    validate_tag,
)
from s3_listing_study.common.build_selection import BuildSelectionError

EXPECTED_ENTRYPOINT = (
    "/usr/bin/python3",
    "-I",
    "/opt/s3-listing-study/attempt.pyz",
)
EXPECTED_USER = "10001:10001"
SHARED_SOURCE_LABEL = "io.varve.s3-listing-study.shared-base-source-sha256"
TOOL_BUILD_LABEL = "io.varve.s3-listing-study.tool-build-sha256"
WORKER_SOURCE_LABEL = "io.varve.s3-listing-study.worker-source-sha256"


def _root() -> Path:
    try:
        return Path.cwd().resolve(strict=True)
    except OSError as exc:  # pragma: no cover - a deleted cwd
        raise CIError(f"cannot resolve the repository root: {exc}") from exc


def _write_outputs(values: dict[str, str]) -> None:
    """Emit job outputs, refusing values that could forge extra output lines."""
    destination = os.environ.get("GITHUB_OUTPUT")
    lines = []
    for key, value in values.items():
        if "\n" in value or "\r" in value:
            raise CIError(f"output {key} is not a single line")
        lines.append(f"{key}={value}")
    payload = "\n".join(lines) + "\n"
    if destination:
        with Path(destination).open("a", encoding="utf-8") as stream:
            stream.write(payload)
    else:
        sys.stdout.write(payload)


def _append_summary(text: str) -> None:
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if not destination:
        sys.stdout.write(text)
        return
    with Path(destination).open("a", encoding="utf-8") as stream:
        stream.write(text)


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CIError(f"cannot read {label}: {exc}") from exc


def _load_plan(path: Path) -> Plan:
    document = _read_json(path, "the build plan")
    if not isinstance(document, dict):
        raise CIError("the build plan is not an object")
    return Plan.from_json(document)


def _selected_tools(value: str | None, root: Path) -> tuple[str, ...] | None:
    """Parse a comma-separated tool filter, or ``None`` for the whole roster."""
    if value is None or not value.strip() or value.strip() == "all":
        return None
    wanted = tuple(item.strip() for item in value.split(",") if item.strip())
    if not wanted:
        raise CIError("--tools was given but selected nothing")
    roster = set(buildable_tools(root))
    unknown = sorted(set(wanted) - roster)
    if unknown:
        raise CIError(f"unknown tool(s): {', '.join(unknown)}")
    return tuple(sorted(set(wanted)))


def _plan_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="s3-listing-study ci plan", allow_abbrev=False)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--ref-name", required=True)
    parser.add_argument("--main-publication", action="store_true")
    parser.add_argument("--tools")
    parser.add_argument("--output", type=Path, default=Path("image-build-plan.json"))
    args = parser.parse_args(argv)

    root = _root()
    tools = _selected_tools(args.tools, root)
    project_version(root)
    # One derivation of the grammar: build the plan as if nothing were published,
    # probe exactly the references it names, then build it again for real.
    provisional = build_plan(
        root,
        repository=args.repository,
        ref_name=args.ref_name,
        is_main_publication=args.main_publication,
        existing={},
        tools=tools,
    )
    existing = registry_module.probe_many(candidate_references(provisional))
    plan = build_plan(
        root,
        repository=args.repository,
        ref_name=args.ref_name,
        is_main_publication=args.main_publication,
        existing=existing,
        tools=tools,
    )
    args.output.write_text(
        json.dumps(plan.as_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _write_outputs(render_outputs(plan))
    rows = "\n".join(
        f"| `{item.tool}` | {item.bucket} | `{item.execution_tag.rsplit(':', 1)[1]}` |"
        for item in plan.tools
    )
    _append_summary(
        "## Image build plan\n\n"
        f"Shared runtime: **{'build' if plan.shared_needed else 'published'}** "
        f"(`{plan.shared_tag.rsplit(':', 1)[1]}`)\n\n"
        "| Tool | Action | Execution tag |\n| --- | --- | --- |\n" + rows + "\n"
    )
    return 0


def _bake_file_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="s3-listing-study ci bake-file", allow_abbrev=False)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("bake.json"))
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--tools")
    args = parser.parse_args(argv)
    plan = _load_plan(args.plan)
    tools = _selected_tools(args.tools, _root())
    write_bake_definition(plan, args.output, push=args.push, tools=tools)
    return 0


def _assert_digests_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="s3-listing-study ci assert-digests", allow_abbrev=False)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args(argv)
    plan = _load_plan(args.plan)
    metadata = _read_json(args.metadata, "bake metadata")
    if not isinstance(metadata, dict):
        raise CIError("bake metadata is not an object")
    expected = [item.tool for item in plan.bucket("bake")]
    resolved = assert_metadata_digests(plan, metadata, expected_tools=expected, push=args.push)
    args.output.write_text(json.dumps(resolved, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def _verify_published_main(argv: Sequence[str]) -> int:
    """Confirm published images are what the plan says, reading manifests only.

    No layer is pulled and no container is started, so this can run in a job that
    has no business materialising a subject tool's filesystem.
    """
    parser = argparse.ArgumentParser(
        prog="s3-listing-study ci verify-published", allow_abbrev=False
    )
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args(argv)
    plan = _load_plan(args.plan)
    missing = plan.missing()
    if missing:
        raise CIError("cannot verify an incomplete publication: " + ", ".join(missing))

    references = [plan.shared_tag]
    for item in plan.tools:
        references.extend((item.tool_tag, item.execution_tag))
    registry_module.assert_plain_manifests(references)

    shared_labels = registry_module.image_labels(plan.shared_tag)
    if shared_labels.get(SHARED_SOURCE_LABEL) != plan.shared_source_sha256:
        raise CIError("published shared runtime does not carry its registered source identity")
    for item in plan.tools:
        tool_labels = registry_module.image_labels(item.tool_tag)
        if tool_labels.get(TOOL_BUILD_LABEL) != item.tool_build_sha256:
            raise CIError(f"{item.tool}: published tool parent has the wrong build identity")
        if tool_labels.get(SHARED_SOURCE_LABEL) != item.shared_source_sha256:
            raise CIError(f"{item.tool}: published tool parent has the wrong shared base")
        execution_labels = registry_module.image_labels(item.execution_tag)
        if execution_labels.get(WORKER_SOURCE_LABEL) != item.worker_source_sha256:
            raise CIError(f"{item.tool}: published execution image has the wrong worker source")
        if registry_module.image_user(item.execution_tag) != EXPECTED_USER:
            raise CIError(f"{item.tool}: published execution image does not run as {EXPECTED_USER}")
        if registry_module.image_entrypoint(item.execution_tag) != EXPECTED_ENTRYPOINT:
            raise CIError(f"{item.tool}: published execution image has an unexpected entrypoint")
    print(f"verified {len(plan.tools)} execution images and their parents", file=sys.stderr)
    return 0


def _publication_manifest_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="s3-listing-study ci publication-manifest", allow_abbrev=False
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--checkout-revision", required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--pull-request-number")
    parser.add_argument("--pull-request-base-sha")
    parser.add_argument("--pull-request-head-sha")
    parser.add_argument("--output", type=Path, default=Path("ghcr-publication-manifest.json"))
    args = parser.parse_args(argv)
    plan = _load_plan(args.plan)
    missing = plan.missing()
    if missing:
        raise CIError("refusing to write a manifest for an incomplete set: " + ", ".join(missing))
    pull_request = None
    if args.pull_request_number:
        try:
            number = int(args.pull_request_number)
        except ValueError as exc:
            raise CIError("pull-request number is not an integer") from exc
        pull_request = {
            "base_sha": args.pull_request_base_sha or "",
            "head_sha": args.pull_request_head_sha or "",
            "number": number,
        }
    document = publication_manifest(
        plan,
        checkout_revision=args.checkout_revision,
        source_ref=args.source_ref,
        pull_request=pull_request,
    )
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def _promotion_report_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="s3-listing-study ci promotion-report", allow_abbrev=False
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--checkout-revision", required=True)
    parser.add_argument("--set-digest", required=True)
    parser.add_argument("--set-version-tag", required=True)
    parser.add_argument("--output", type=Path, default=Path("promotion-report.json"))
    args = parser.parse_args(argv)
    plan = _load_plan(args.plan)
    channels = [item.execution_channel_tag for item in plan.tools]
    previous = registry_module.probe_many([*channels, plan.set_channel_tag])
    report = promotion_report(
        plan,
        checkout_revision=args.checkout_revision,
        set_digest=args.set_digest,
        set_version_tag=args.set_version_tag,
        previous_channels={tag: previous.get(tag) for tag in channels},
        previous_set=previous.get(plan.set_channel_tag),
    )
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rows = "\n".join(
        f"| `{channel}` | `{before}` | `{after}` |"
        for channel, before, after in promotion_summary_rows(report)
    )
    _append_summary(
        "## GHCR channel promotion plan\n\n"
        "| Channel | Previous | Intended |\n| --- | --- | --- |\n" + rows + "\n"
    )
    return 0


def _record_promotion_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="s3-listing-study ci record-promotion", allow_abbrev=False
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--tool")
    parser.add_argument("--publication-set", action="store_true")
    parser.add_argument("--promoted-digest", required=True)
    args = parser.parse_args(argv)
    if bool(args.tool) == args.publication_set:
        raise CIError("record exactly one of --tool or --publication-set")
    if DIGEST_RE.fullmatch(args.promoted_digest) is None:
        raise CIError(f"not a sha256 digest: {args.promoted_digest}")
    report = _read_json(args.report, "the promotion report")
    if not isinstance(report, dict):
        raise CIError("the promotion report is not an object")
    entry = (
        report["publication_set"]
        if args.publication_set
        else report["execution_channels"].get(args.tool)
    )
    if entry is None:
        raise CIError(f"promotion report has no entry for {args.tool}")
    if entry["intended_digest"] != args.promoted_digest:
        raise CIError(
            f"promotion moved {entry['channel_tag']} to {args.promoted_digest}, "
            f"but the plan intended {entry['intended_digest']}"
        )
    entry["status"] = "promoted"
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def _tool_tags_main(argv: Sequence[str]) -> int:
    """Emit one tool's planned identities as shell-safe KEY=value lines."""
    parser = argparse.ArgumentParser(prog="s3-listing-study ci tool-tags", allow_abbrev=False)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--tool", required=True)
    args = parser.parse_args(argv)
    plan = _load_plan(args.plan)
    item = next((entry for entry in plan.tools if entry.tool == args.tool), None)
    if item is None:
        raise CIError(f"the plan has no entry for {args.tool}")
    _write_outputs(
        {
            "tool-tag": item.tool_tag,
            "execution-tag": item.execution_tag,
            "execution-channel-tag": item.execution_channel_tag,
        }
    )
    return 0


def _channels_main(argv: Sequence[str]) -> int:
    """Emit the promotion worklist: tool, immutable reference, channel tag.

    Written as a file rather than resolved inline so the promotion loop reads a
    fixed plan it cannot accidentally recompute part-way through.
    """
    parser = argparse.ArgumentParser(prog="s3-listing-study ci channels", allow_abbrev=False)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    plan = _load_plan(args.plan)
    missing = plan.missing()
    if missing:
        raise CIError("refusing to promote an incomplete set: " + ", ".join(missing))
    lines = []
    for item in plan.tools:
        # Every field is re-validated here rather than trusted from the plan
        # file. This is the one output that moves published identity: each line
        # becomes an `imagetools create --tag`, so a stray tab or newline in any
        # field would promote a channel nobody planned.
        assert item.execution_digest is not None  # guaranteed by missing() above
        reference = digest_reference(plan.repository, item.execution_digest)
        channel = validate_tag(item.execution_channel_tag)
        for field, value in (("tool", item.tool), ("reference", reference), ("channel", channel)):
            if any(character in value for character in "\t\n\r"):
                raise CIError(f"{field} is not a single field: {value!r}")
        lines.append(f"{item.tool}\t{reference}\t{channel}")
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


def _reconcile_main(argv: Sequence[str]) -> int:
    """Re-probe the registry and carry the planned buckets onto the published set."""
    parser = argparse.ArgumentParser(prog="s3-listing-study ci reconcile", allow_abbrev=False)
    parser.add_argument("--planned", type=Path, required=True)
    parser.add_argument("--published", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    planned = _load_plan(args.planned)
    published = _load_plan(args.published)
    # The two plans must describe the same publication. Falling back to the
    # published bucket on a mismatch would record a freshly built image as
    # "adopted" — a ledger claiming an image predates the run that produced it.
    if planned.repository != published.repository:
        raise CIError("planned and published sets name different repositories")
    if planned.channel_suffix != published.channel_suffix:
        raise CIError("planned and published sets name different channels")
    planned_tools = {item.tool for item in planned.tools}
    published_tools = {item.tool for item in published.tools}
    # A run may build a subset, so the planned set is allowed to be smaller — the
    # published set is always the whole roster, because the set channel asserts
    # that every execution channel forms one ready publication and the ledger it
    # points at must therefore describe all of them. Tools this run did not plan
    # keep the bucket the registry reports, which is `adopt`; completeness is
    # enforced by `missing()` below, not by counting names.
    unknown = planned_tools - published_tools
    if unknown:
        raise CIError(
            "planned tools are absent from the published set: " + ", ".join(sorted(unknown))
        )
    buckets = {item.tool: item.bucket for item in planned.tools}
    merged = Plan(
        repository=published.repository,
        channel_suffix=published.channel_suffix,
        shared_source_sha256=published.shared_source_sha256,
        shared_tag=published.shared_tag,
        shared_digest=published.shared_digest,
        set_channel_tag=published.set_channel_tag,
        tools=tuple(
            replace(item, planned_bucket=buckets.get(item.tool, item.bucket))
            for item in published.tools
        ),
    )
    missing = merged.missing()
    if missing:
        raise CIError("publication is incomplete: " + ", ".join(missing))
    args.output.write_text(
        json.dumps(merged.as_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


def _ledger_tag_main(argv: Sequence[str]) -> int:
    """Derive the immutable ledger tag from the manifest bytes it will contain.

    The last tag whose grammar lived in shell (`sha256sum | cut` plus a bash
    substring). Every published tag now has exactly one implementation.
    """
    parser = argparse.ArgumentParser(prog="s3-listing-study ci ledger-tag", allow_abbrev=False)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    args = parser.parse_args(argv)
    try:
        payload = args.manifest.read_bytes()
    except OSError as exc:
        raise CIError(f"cannot read the publication manifest: {exc}") from exc
    manifest_sha256 = hashlib.sha256(payload).hexdigest()
    version_tag = set_ledger_tag(args.repository, manifest_sha256)
    # Adopt-if-present, like every other version tag in the system. Republishing
    # would move an immutable tag: the manifest bytes are identical on a re-run,
    # so the tag is the same, but a fresh build stamps a new image config and
    # therefore a new digest — leaving the previous ledger untagged.
    existing = registry_module.probe(version_tag)
    _write_outputs(
        {
            "manifest-sha256": manifest_sha256,
            "version-tag": version_tag,
            "exists": "true" if existing else "false",
            "existing-digest": existing or "",
        }
    )
    return 0


def _assert_promoted_main(argv: Sequence[str]) -> int:
    """Refuse to advance the set channel unless every execution channel moved.

    The set channel is the assertion that all eleven execution channels form one
    ready publication. Without this check that ordering rests on a shell loop not
    silently under-iterating.
    """
    parser = argparse.ArgumentParser(prog="s3-listing-study ci assert-promoted", allow_abbrev=False)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    report = _read_json(args.report, "the promotion report")
    if not isinstance(report, dict) or "execution_channels" not in report:
        raise CIError("the promotion report is not a promotion report")
    pending = sorted(
        tool
        for tool, entry in report["execution_channels"].items()
        if entry.get("status") != "promoted"
    )
    if pending:
        raise CIError(
            "refusing to advertise an incomplete set; channels not promoted: " + ", ".join(pending)
        )
    print(f"all {len(report['execution_channels'])} execution channels promoted", file=sys.stderr)
    return 0


def _published_table(plan: Plan) -> str:
    """The table a reader wants after a build: what exists, and its exact digest.

    Tags say what an image is made of; a digest is what you pin in a campaign or
    paste into `docker run`. Both belong in the same place.
    """
    shared_tag = plan.shared_tag.rsplit(":", 1)[1]
    lines = [
        f"Shared runtime `{shared_tag}`",
        f"`{plan.shared_digest or 'not published'}`",
        "",
        "| Tool | Execution tag | Digest |",
        "| --- | --- | --- |",
    ]
    for item in plan.tools:
        lines.append(
            f"| `{item.tool}` | `{item.execution_tag.rsplit(':', 1)[1]}` | "
            f"`{item.execution_digest or 'not published'}` |"
        )
    return "\n".join(lines) + "\n"


def _published_main(argv: Sequence[str]) -> int:
    """Show the published image set for a ref — tags, digests, and what is missing.

    The answer to "I pushed, something built, what are the IDs". Runs anywhere
    with registry read access and needs no CI run.
    """
    parser = argparse.ArgumentParser(prog="s3-listing-study ci published", allow_abbrev=False)
    parser.add_argument("--repository")
    parser.add_argument("--ref-name")
    parser.add_argument("--main-publication", action="store_true")
    parser.add_argument("--plan", type=Path, help="read a resolved plan instead of probing")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.plan and not (args.repository and args.ref_name):
        raise CIError("give --plan, or --repository and --ref-name to probe the registry")
    if args.plan:
        plan = _load_plan(args.plan)
    else:
        root = _root()
        provisional = build_plan(
            root,
            repository=args.repository,
            ref_name=args.ref_name,
            is_main_publication=args.main_publication,
            existing={},
        )
        existing = registry_module.probe_many(candidate_references(provisional))
        plan = build_plan(
            root,
            repository=args.repository,
            ref_name=args.ref_name,
            is_main_publication=args.main_publication,
            existing=existing,
        )
    if args.json:
        print(json.dumps(plan.as_json(), indent=2, sort_keys=True))
        return 0
    print(f"channel: {plan.channel_suffix}")
    print(f"shared : {plan.shared_tag.rsplit(':', 1)[1]}  {plan.shared_digest or 'not published'}")
    for item in plan.tools:
        print(f"  {item.tool:14} {item.execution_digest or 'not published':71}")
        print(f"  {'':14} {item.execution_tag.rsplit(':', 1)[1]}")
        print(f"  {'':14} channel {item.execution_channel_tag.rsplit(':', 1)[1]}")
    _append_summary("## Published image set\n\n" + _published_table(plan))
    return 0


def _roster_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="s3-listing-study ci roster", allow_abbrev=False)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    roster = buildable_tools(_root())
    print(json.dumps(list(roster)) if args.json else "\n".join(roster))
    return 0


SUBCOMMANDS = {
    "plan": _plan_main,
    "bake-file": _bake_file_main,
    "assert-digests": _assert_digests_main,
    "verify-published": _verify_published_main,
    "publication-manifest": _publication_manifest_main,
    "promotion-report": _promotion_report_main,
    "record-promotion": _record_promotion_main,
    "tool-tags": _tool_tags_main,
    "channels": _channels_main,
    "reconcile": _reconcile_main,
    "ledger-tag": _ledger_tag_main,
    "assert-promoted": _assert_promoted_main,
    "published": _published_main,
    "roster": _roster_main,
}


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in SUBCOMMANDS:
        print(
            "usage: s3-listing-study ci {" + ",".join(sorted(SUBCOMMANDS)) + "} ...",
            file=sys.stderr,
        )
        return 2
    try:
        return SUBCOMMANDS[arguments[0]](arguments[1:])
    except (CIError, BuildSelectionError) as exc:
        print(f"ci {arguments[0]}: {exc}", file=sys.stderr)
        return 2
    except (KeyError, OSError, TypeError, ValueError) as exc:
        # A malformed hand-edited report or an unwritable path should read as a
        # refusal, not a traceback.
        print(f"ci {arguments[0]}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
