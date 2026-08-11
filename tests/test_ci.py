"""The CI decision layer: roster, tag grammar, probe classification, and the build graph.

These cover the logic that used to live as heredocs inside the image workflow,
where a mistake surfaced as a failed job minutes into a run. The registry
boundary is the only impure part and is stubbed; everything else is exercised
directly.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from s3_listing_study.ci import CIError, buildable_tools
from s3_listing_study.ci import cli as ci_cli
from s3_listing_study.ci import registry as registry_module
from s3_listing_study.ci import tags as tag_grammar
from s3_listing_study.ci.bake import assert_metadata_digests, bake_definition
from s3_listing_study.ci.plan import Plan, build_plan, project_version, render_outputs
from s3_listing_study.ci.publication import promotion_report, publication_manifest
from s3_listing_study.common.build_selection import (
    derived_image_build_command,
    load_registered_selection,
)

REPOSITORY = "ghcr.io/varveio/s3-listing-study"
SHARED = "fab65202126ea6d3cb1ea801b11742e92e7373997373583e468cd5fe78ce728c"
BUILD = "ad24983324e6c8172c12d8267488fb2ceed78b3bf3f82675cea3f0d0b0999d30"
WORKER = "0" * 60 + "beef"
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
REVISION = "b69beade955fe53756830230da9cdc45b86a0399"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- roster


def test_the_roster_is_exactly_the_capsules_that_register_an_image() -> None:
    roster = buildable_tools(repo_root())
    assert len(roster) == 11
    assert roster == tuple(sorted(roster))
    for tool in roster:
        assert (repo_root() / "tools" / tool / "build" / "image.json").is_file()


def test_a_tracked_subject_without_a_build_recipe_is_not_in_the_roster() -> None:
    # pure-storage and s3-inventory are tracked subjects with no image. They drop
    # out because they register no build/image.json, not via an exclusion list.
    roster = buildable_tools(repo_root())
    assert "pure-storage" not in roster
    assert "s3-inventory" not in roster
    assert (repo_root() / "tools" / "pure-storage").is_dir()


def test_an_empty_tree_is_refused_rather_than_planning_nothing(tmp_path: Path) -> None:
    (tmp_path / "tools").mkdir()
    with pytest.raises(CIError, match="no buildable tools"):
        buildable_tools(tmp_path)


# --------------------------------------------------------------------------- tags


def test_published_tags_are_reproduced_exactly() -> None:
    # These three strings exist in the public package. If this test fails, the
    # workflow would publish under a name nothing else can find.
    assert tag_grammar.shared_tag(REPOSITORY, SHARED) == (
        f"{REPOSITORY}:shared-python3.11-fab65202126e"
    )
    assert tag_grammar.tool_tag(REPOSITORY, "aws-cli", "2.36.1", SHARED, BUILD) == (
        f"{REPOSITORY}:tool-aws-cli-v2.36.1-base-fab65202126e-build-ad24983324e6"
    )
    assert tag_grammar.channel_suffix("docker/oci-layer-ci", is_main_publication=False) == (
        "branch-docker-oci-layer-ci-6fde05768ed0"
    )


def test_a_pull_request_from_a_branch_named_main_cannot_reach_the_main_channel() -> None:
    assert tag_grammar.channel_suffix("main", is_main_publication=True) == "main"
    suffix = tag_grammar.channel_suffix("main", is_main_publication=False)
    assert suffix.startswith("branch-main-")
    assert suffix != "main"


def test_branches_sharing_a_long_prefix_get_different_channels() -> None:
    long = "feature/" + "a" * 80
    first = tag_grammar.channel_suffix(long + "-one", is_main_publication=False)
    second = tag_grammar.channel_suffix(long + "-two", is_main_publication=False)
    assert first != second
    assert tag_grammar.CHANNEL_SUFFIX_RE.fullmatch(first)
    assert tag_grammar.CHANNEL_SUFFIX_RE.fullmatch(second)


@pytest.mark.parametrize("ref", ["", "///", "..."])
def test_a_ref_that_cannot_produce_a_tag_is_refused(ref: str) -> None:
    with pytest.raises(CIError):
        tag_grammar.channel_suffix(ref, is_main_publication=False)


def test_a_short_hash_is_refused_rather_than_padded() -> None:
    with pytest.raises(CIError, match="64 lowercase hexadecimal"):
        tag_grammar.short("abc", "test field")


def test_a_tag_longer_than_docker_allows_is_refused() -> None:
    with pytest.raises(CIError, match="invalid Docker tag"):
        tag_grammar.execution_tag(REPOSITORY, "x" * 200, "1.0", SHARED, BUILD, "0.1.0", WORKER)


def test_digest_references_require_a_real_digest() -> None:
    assert tag_grammar.digest_reference(REPOSITORY, DIGEST_A) == f"{REPOSITORY}@{DIGEST_A}"
    with pytest.raises(CIError, match="not a sha256 digest"):
        tag_grammar.digest_reference(REPOSITORY, "sha256:short")


# --------------------------------------------------------------------------- probing


def _stub_run(monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: str, stderr: str) -> None:
    def fake_run(command: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(command), returncode, stdout, stderr)

    monkeypatch.setattr(registry_module, "_run", fake_run)


def test_a_present_reference_resolves_to_its_manifest_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_run(
        monkeypatch,
        0,
        json.dumps({"digest": DIGEST_A, "mediaType": "application/vnd.oci.image.manifest.v1+json"}),
        "",
    )
    assert registry_module.probe(f"{REPOSITORY}:anything") == DIGEST_A


REF = f"{REPOSITORY}:some-tag"


@pytest.mark.parametrize(
    "stderr",
    [
        # GHCR's actual wording for a missing tag, captured from the real registry.
        f"ERROR: {REF}: not found",
        f"ERROR: {REF}: manifest unknown",
        f"{REF}: manifest_unknown: manifest unknown",
        f"ERROR: {REF}: name unknown",
        f"ERROR: {REF}: unexpected status 404 Not Found",
    ],
)
def test_an_absent_reference_is_absence_not_an_error(
    monkeypatch: pytest.MonkeyPatch, stderr: str
) -> None:
    _stub_run(monkeypatch, 1, "", stderr)
    assert registry_module.probe(REF) is None


@pytest.mark.parametrize(
    "stderr",
    [
        "denied: permission_denied",
        "unauthorized: authentication required",
        "toomanyrequests: rate limit exceeded",
        "dial tcp: i/o timeout",
        "",
        # A missing repository: GHCR answers from the token endpoint, and the
        # message says nothing about the tag being absent.
        "ERROR: failed to authorize: failed to fetch anonymous token: "
        "unexpected status from GET request to https://ghcr.io/token: 403 Forbidden",
        # A broken credential helper — the ordinary laptop misconfiguration. The
        # phrase "not found" is about an executable, not about the reference.
        'error getting credentials - err: exec: "docker-credential-desktop": '
        "executable file not found in $PATH",
        "ERROR: docker buildx imagetools: command not found",
        # An existing manifest that has no matching platform.
        f"ERROR: {REF}: no match for platform in manifest: not found",
    ],
)
def test_every_other_failure_fails_closed_rather_than_reading_as_absence(
    monkeypatch: pytest.MonkeyPatch, stderr: str
) -> None:
    # This is the single most consequential predicate in the pipeline: absence
    # means "publish it fresh". An auth failure or a broken credential helper
    # read as absence republishes over a canonical tag from an unverified build.
    _stub_run(monkeypatch, 1, "", stderr)
    with pytest.raises(CIError, match="cannot resolve"):
        registry_module.probe(REF)


def test_a_not_found_that_never_names_the_reference_is_not_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A registry that genuinely lacks a tag echoes the tag back. A message that
    # does not name what we asked about came from somewhere else.
    _stub_run(monkeypatch, 1, "", "ERROR: something else entirely: not found")
    with pytest.raises(CIError, match="cannot resolve"):
        registry_module.probe(REF)


def test_a_registry_reporting_a_malformed_digest_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_run(monkeypatch, 0, json.dumps({"digest": "sha256:nope", "mediaType": "x"}), "")
    with pytest.raises(CIError, match="invalid digest"):
        registry_module.probe(f"{REPOSITORY}:weird")


def test_an_index_is_refused_where_a_plain_image_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # BuildKit's default attestations wrap an image in an index, which would make
    # the promoted digest name a manifest list rather than the image.
    _stub_run(
        monkeypatch,
        0,
        json.dumps({"digest": DIGEST_A, "mediaType": "application/vnd.oci.image.index.v1+json"}),
        "",
    )
    with pytest.raises(CIError, match="attestations must be disabled"):
        registry_module.assert_plain_manifests([f"{REPOSITORY}:execution-x"])


# --------------------------------------------------------------------------- planning


def test_project_and_packaged_versions_must_agree() -> None:
    assert project_version(repo_root())


def test_nothing_published_means_every_tool_builds_its_whole_chain() -> None:
    plan = build_plan(
        repo_root(),
        repository=REPOSITORY,
        ref_name="topic/x",
        is_main_publication=False,
        existing={},
    )
    assert plan.shared_needed
    assert len(plan.bucket("chain")) == 11
    assert plan.bucket("bake") == ()
    assert plan.bucket("adopt") == ()
    assert render_outputs(plan)["build-needed"] == "true"


def test_published_parents_with_a_new_worker_bake_thin_layers_only() -> None:
    """The common case: Python changed, both parents already exist."""
    root = repo_root()
    empty = build_plan(
        root, repository=REPOSITORY, ref_name="topic/x", is_main_publication=False, existing={}
    )
    existing: dict[str, str | None] = {empty.shared_tag: DIGEST_A}
    existing.update({item.tool_tag: DIGEST_B for item in empty.tools})
    plan = build_plan(
        root,
        repository=REPOSITORY,
        ref_name="topic/x",
        is_main_publication=False,
        existing=existing,
    )
    assert not plan.shared_needed
    assert plan.bucket("chain") == ()
    assert len(plan.bucket("bake")) == 11
    outputs = render_outputs(plan)
    assert json.loads(outputs["chain-matrix"]) == []
    assert len(json.loads(outputs["bake-tools"])) == 11


def test_everything_published_builds_nothing_at_all() -> None:
    root = repo_root()
    empty = build_plan(
        root, repository=REPOSITORY, ref_name="topic/x", is_main_publication=False, existing={}
    )
    existing: dict[str, str | None] = {empty.shared_tag: DIGEST_A}
    for item in empty.tools:
        existing[item.tool_tag] = DIGEST_B
        existing[item.execution_tag] = DIGEST_C
    plan = build_plan(
        root,
        repository=REPOSITORY,
        ref_name="topic/x",
        is_main_publication=False,
        existing=existing,
    )
    assert len(plan.bucket("adopt")) == 11
    assert render_outputs(plan)["build-needed"] == "false"
    assert plan.missing() == ()


def test_a_child_published_without_its_parent_is_refused() -> None:
    # Only possible if the package was edited outside this pipeline. No automatic
    # action is safe, so the run stops rather than guessing.
    root = repo_root()
    empty = build_plan(
        root, repository=REPOSITORY, ref_name="topic/x", is_main_publication=False, existing={}
    )
    existing: dict[str, str | None] = {empty.shared_tag: DIGEST_A}
    existing[empty.tools[0].execution_tag] = DIGEST_C
    with pytest.raises(CIError, match="no published tool parent"):
        build_plan(
            root,
            repository=REPOSITORY,
            ref_name="topic/x",
            is_main_publication=False,
            existing=existing,
        )


def test_an_unknown_tool_filter_is_refused() -> None:
    with pytest.raises(CIError, match="unknown tool"):
        build_plan(
            repo_root(),
            repository=REPOSITORY,
            ref_name="topic/x",
            is_main_publication=False,
            existing={},
            tools=("aws-cli", "not-a-tool"),
        )


def test_a_plan_round_trips_through_json_preserving_its_buckets() -> None:
    root = repo_root()
    plan = build_plan(
        root, repository=REPOSITORY, ref_name="topic/x", is_main_publication=False, existing={}
    )
    restored = Plan.from_json(json.loads(json.dumps(plan.as_json())))
    assert [item.tool for item in restored.tools] == [item.tool for item in plan.tools]
    assert restored.channel_suffix == plan.channel_suffix
    assert len(restored.bucket("chain")) == 11


def test_resolving_a_plan_keeps_saying_which_images_this_run_built() -> None:
    root = repo_root()
    plan = build_plan(
        root, repository=REPOSITORY, ref_name="topic/x", is_main_publication=False, existing={}
    )
    resolved = plan.resolved(
        shared_digest=DIGEST_A,
        tool_digests={item.tool: DIGEST_B for item in plan.tools},
        execution_digests={item.tool: DIGEST_C for item in plan.tools},
    )
    assert resolved.missing() == ()
    # Every digest is now known, but the record must still say these were built
    # by this run rather than adopted from the registry.
    assert {item.reuse_source for item in resolved.tools} == {"built"}
    assert {item.bucket for item in resolved.tools} == {"chain"}


# --------------------------------------------------------------------------- bake


def _baked_plan() -> Plan:
    root = repo_root()
    empty = build_plan(
        root, repository=REPOSITORY, ref_name="topic/x", is_main_publication=False, existing={}
    )
    existing: dict[str, str | None] = {empty.shared_tag: DIGEST_A}
    existing.update({item.tool_tag: DIGEST_B for item in empty.tools})
    return build_plan(
        root,
        repository=REPOSITORY,
        ref_name="topic/x",
        is_main_publication=False,
        existing=existing,
    )


def test_every_bake_target_disables_attestations_through_the_field_bake_honours() -> None:
    # Regression guard. Bake silently ignores unknown keys, and the `provenance`
    # and `sbom` target fields are among the ones it ignores in a JSON definition
    # — a file carrying them still pushed an attestation index. `attest` works.
    definition = bake_definition(_baked_plan(), push=True)
    for target in definition["target"].values():
        assert target["attest"] == [
            "type=provenance,disabled=true",
            "type=sbom,disabled=true",
        ]
        assert "provenance" not in target
        assert "sbom" not in target


def test_a_bake_target_pins_its_parent_by_digest_and_carries_it_into_provenance() -> None:
    definition = bake_definition(_baked_plan(), push=True)
    target = definition["target"]["aws-cli"]
    parent = f"{REPOSITORY}@{DIGEST_B}"
    assert target["args"]["TOOL_IMAGE"] == parent
    assert target["args"]["TOOL_IMAGE_URI"] == parent
    assert target["args"]["TOOL_IMAGE_DIGEST"] == DIGEST_B
    assert target["contexts"] == {
        "adapter": "tools/aws-cli/adapter",
        "selection": "tools/aws-cli/build",
    }


def test_a_pull_request_bake_writes_nowhere() -> None:
    assert bake_definition(_baked_plan(), push=False)["target"]["aws-cli"]["output"] == [
        "type=cacheonly"
    ]
    assert bake_definition(_baked_plan(), push=True)["target"]["aws-cli"]["output"] == [
        "type=registry"
    ]


def test_baking_a_tool_whose_parent_is_unpublished_is_refused() -> None:
    root = repo_root()
    plan = build_plan(
        root, repository=REPOSITORY, ref_name="topic/x", is_main_publication=False, existing={}
    )
    with pytest.raises(CIError, match="no thin layers to bake"):
        bake_definition(plan, push=True)


def test_a_dropped_bake_target_cannot_pass_as_a_complete_set() -> None:
    plan = _baked_plan()
    metadata = {
        item.tool: {"containerimage.digest": DIGEST_C, "image.name": item.execution_tag}
        for item in plan.bucket("bake")[:-1]
    }
    with pytest.raises(CIError, match="no target for"):
        assert_metadata_digests(
            plan,
            metadata,
            expected_tools=[item.tool for item in plan.bucket("bake")],
            push=True,
        )


def test_bake_pushing_an_unplanned_tag_is_refused() -> None:
    plan = _baked_plan()
    metadata = {
        item.tool: {
            "containerimage.digest": DIGEST_C,
            "image.name": f"{REPOSITORY}:something-else",
        }
        for item in plan.bucket("bake")
    }
    with pytest.raises(CIError, match="rather than exactly the planned"):
        assert_metadata_digests(
            plan,
            metadata,
            expected_tools=[item.tool for item in plan.bucket("bake")],
            push=True,
        )


# --------------------------------------------------------------------------- publication


def _complete_plan() -> Plan:
    root = repo_root()
    plan = build_plan(
        root, repository=REPOSITORY, ref_name="topic/x", is_main_publication=False, existing={}
    )
    return plan.resolved(
        shared_digest=DIGEST_A,
        tool_digests={item.tool: DIGEST_B for item in plan.tools},
        execution_digests={item.tool: DIGEST_C for item in plan.tools},
    )


def test_a_manifest_is_only_written_for_a_complete_set() -> None:
    root = repo_root()
    incomplete = build_plan(
        root, repository=REPOSITORY, ref_name="topic/x", is_main_publication=False, existing={}
    )
    with pytest.raises(CIError, match="not a sha256 digest"):
        publication_manifest(
            incomplete, checkout_revision=REVISION, source_ref="refs/heads/x", pull_request=None
        )


def test_a_complete_manifest_records_every_tool_and_one_shared_runtime() -> None:
    document = publication_manifest(
        _complete_plan(),
        checkout_revision=REVISION,
        source_ref="refs/heads/topic/x",
        pull_request=None,
    )
    assert document["format_version"] == 2
    assert document["kind"] == "github-container-image-publication"
    assert set(document["images"]) == set(buildable_tools(repo_root()))
    assert document["shared"]["digest"] == DIGEST_A
    assert {image["tool"]["digest"] for image in document["images"].values()} == {DIGEST_B}


def test_a_manifest_is_byte_stable_for_the_same_inputs() -> None:
    # The ledger tag is the manifest hash, so an unstable serialisation would
    # mint a new ledger image for an identical publication.
    first = publication_manifest(
        _complete_plan(), checkout_revision=REVISION, source_ref="refs/heads/x", pull_request=None
    )
    second = publication_manifest(
        _complete_plan(), checkout_revision=REVISION, source_ref="refs/heads/x", pull_request=None
    )
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_a_bad_checkout_revision_is_refused() -> None:
    with pytest.raises(CIError, match="not a Git object ID"):
        publication_manifest(
            _complete_plan(),
            checkout_revision="not-a-sha",
            source_ref="refs/heads/x",
            pull_request=None,
        )


def test_the_promotion_report_records_what_every_channel_pointed_at_first() -> None:
    plan = _complete_plan()
    previous: dict[str, str | None] = {plan.tools[0].execution_channel_tag: DIGEST_A}
    report = promotion_report(
        plan,
        checkout_revision=REVISION,
        set_digest=DIGEST_A,
        set_version_tag=f"{REPOSITORY}:set-v2-abcdef123456",
        previous_channels=previous,
        previous_set=None,
    )
    assert report["publication_set"]["previous_digest"] == "absent"
    assert report["publication_set"]["status"] == "planned"
    first = report["execution_channels"][plan.tools[0].tool]
    assert first["previous_digest"] == DIGEST_A
    assert first["intended_digest"] == DIGEST_C
    others = [
        entry for tool, entry in report["execution_channels"].items() if tool != plan.tools[0].tool
    ]
    assert {entry["previous_digest"] for entry in others} == {"absent"}


# --------------------------------------------------------------- tag goldens


def test_every_published_tag_spelling_is_pinned() -> None:
    """Literal goldens for all five grammars, not just the two live ones.

    Every other test builds these through the same functions and compares
    structurally, so renaming a separator — `-src-` to `-source-` — would pass
    the whole suite while orphaning every published image and re-bucketing the
    roster into a full rebuild. These strings are the deleted shell's own output.
    """
    golden_worker = "beefcafe" + "0" * 56
    assert tag_grammar.execution_tag(
        REPOSITORY, "aws-cli", "2.36.1", SHARED, BUILD, "0.1.0", golden_worker
    ) == (
        f"{REPOSITORY}:execution-aws-cli-v2.36.1-base-fab65202126e"
        "-build-ad24983324e6-worker-v0.1.0-src-beefcafe0000"
    )
    assert tag_grammar.execution_channel_tag(REPOSITORY, "aws-cli", "main") == (
        f"{REPOSITORY}:execution-aws-cli-main"
    )
    assert tag_grammar.set_channel_tag(REPOSITORY, "branch-docker-oci-layer-ci-6fde05768ed0") == (
        f"{REPOSITORY}:set-branch-docker-oci-layer-ci-6fde05768ed0"
    )
    assert tag_grammar.set_ledger_tag(REPOSITORY, SHARED) == f"{REPOSITORY}:set-v2-fab65202126e"


def test_the_manifest_serialisation_is_stable_across_processes() -> None:
    """Byte stability under a different hash seed, not just within one process.

    The ledger tag is the hash of these bytes, so an unstable serialisation would
    mint a fresh ledger image for an identical publication. Comparing two dicts
    built in one process cannot detect a set leaking in — the iteration order
    would match either way.
    """
    script = (
        "import json,sys;sys.path.insert(0,'src');"
        "from tests.test_ci import _complete_plan, REVISION;"
        "from s3_listing_study.ci.publication import publication_manifest;"
        "print(json.dumps(publication_manifest(_complete_plan(),"
        "checkout_revision=REVISION,source_ref='refs/heads/x',pull_request=None),"
        "sort_keys=True))"
    )
    digests = set()
    for seed in ("0", "1", "12345"):
        environment = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": "."}
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            cwd=repo_root(),
            env=environment,
        )
        digests.add(hashlib.sha256(result.stdout.encode()).hexdigest())
    assert len(digests) == 1


# ------------------------------------------------------- bake / build parity


def test_a_baked_layer_is_built_exactly_like_a_chain_built_one() -> None:
    """The two paths that produce an execution image must agree.

    `chain` builds through `derived_image_build_command` and `bake` builds from a
    generated target. They mint the same `execution-…-src-<worker12>` identity, so
    the study's reuse argument depends on them being interchangeable. If the
    derived recipe gains a build argument, this fails rather than silently
    producing two different images that claim one name.
    """
    plan = _baked_plan()
    item = next(entry for entry in plan.tools if entry.tool == "aws-cli")
    target = bake_definition(plan, push=True)["target"]["aws-cli"]

    selection = load_registered_selection(repo_root(), "aws-cli")
    assert item.tool_digest is not None
    parent = f"{REPOSITORY}@{item.tool_digest}"
    command = derived_image_build_command(repo_root(), selection, item.execution_tag, parent)

    command_args = {
        value.split("=", 1)[0]: value.split("=", 1)[1]
        for flag, value in itertools.pairwise(command)
        if flag == "--build-arg"
    }
    assert target["args"] == command_args
    command_contexts = {
        value.split("=", 1)[0]: value.split("=", 1)[1]
        for flag, value in itertools.pairwise(command)
        if flag == "--build-context"
    }
    assert set(target["contexts"]) == set(command_contexts)
    for name, path in target["contexts"].items():
        assert command_contexts[name] == str(repo_root() / path)
    assert str(repo_root() / target["dockerfile"]) == command[command.index("--file") + 1]


def test_a_validating_bake_reports_no_digest_and_that_is_not_a_failure() -> None:
    """Regression guard for the pull-request path.

    `type=cacheonly` writes no `containerimage.digest` — only `buildx.build.ref`.
    Demanding a digest there failed every pull-request run of the exact change
    class this design exists to make fast.
    """
    plan = _baked_plan()
    tools = [item.tool for item in plan.bucket("bake")]
    cacheonly = {tool: {"buildx.build.ref": "default/default/abc123"} for tool in tools}
    assert assert_metadata_digests(plan, cacheonly, expected_tools=tools, push=False) == {}

    with pytest.raises(CIError, match="reported no build"):
        assert_metadata_digests(
            plan, {tool: {} for tool in tools}, expected_tools=tools, push=False
        )
    published = {tool: {"containerimage.digest": DIGEST_C, "image.name": "x"} for tool in tools}
    with pytest.raises(CIError, match="must not produce a published image digest"):
        assert_metadata_digests(plan, published, expected_tools=tools, push=False)


def test_a_pushed_target_without_an_image_name_is_refused() -> None:
    plan = _baked_plan()
    tools = [item.tool for item in plan.bucket("bake")]
    metadata = {tool: {"containerimage.digest": DIGEST_C} for tool in tools}
    with pytest.raises(CIError, match="no image name"):
        assert_metadata_digests(plan, metadata, expected_tools=tools, push=True)


def test_a_pushed_target_carrying_extra_tags_is_refused() -> None:
    plan = _baked_plan()
    tools = [item.tool for item in plan.bucket("bake")]
    metadata = {
        item.tool: {
            "containerimage.digest": DIGEST_C,
            "image.name": f"{item.execution_tag},{REPOSITORY}:sneaky-extra",
        }
        for item in plan.bucket("bake")
    }
    with pytest.raises(CIError, match="rather than exactly the planned"):
        assert_metadata_digests(plan, metadata, expected_tools=tools, push=True)


# ---------------------------------------------------- the oci-layout contract


def test_a_layout_parent_context_is_keyed_by_the_exact_from_reference() -> None:
    """The context name must equal the `FROM` string it overrides.

    buildx matches a named context against the reference verbatim. If the two ever
    diverge it silently ignores the context and resolves from the registry —
    masked on a publishing run, where the parent was just pushed, and a late
    failure on a pull request, where it was never pushed at all.
    """
    selection = load_registered_selection(repo_root(), "aws-cli")
    parent = f"{REPOSITORY}@{DIGEST_B}"
    command = derived_image_build_command(
        repo_root(),
        selection,
        "study:test",
        parent,
        extra_contexts={parent: f"oci-layout:///tmp/layout@{DIGEST_B}"},
    )
    index = command.index("--build-arg")
    assert f"TOOL_IMAGE={parent}" in command
    contexts = [value for flag, value in itertools.pairwise(command) if flag == "--build-context"]
    override = [value for value in contexts if value.startswith(f"{parent}=")]
    assert override == [f"{parent}=oci-layout:///tmp/layout@{DIGEST_B}"]
    assert index >= 0


# ------------------------------------------------------------- the CLI itself


def _write_plan(tmp_path: Path, plan: Plan) -> Path:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan.as_json(), indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_outputs_refuse_a_value_that_could_forge_another_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(destination))
    ci_cli._write_outputs({"safe": "a=b"})
    assert destination.read_text() == "safe=a=b\n"
    for hostile in ("first\nsecond=x", "first\rsecond=x"):
        with pytest.raises(CIError, match="not a single line"):
            ci_cli._write_outputs({"key": hostile})


def test_a_plan_that_is_not_a_plan_is_refused(tmp_path: Path) -> None:
    for document, message in (
        ({"kind": "something-else", "schema_version": 1}, "not an image build plan"),
        ({"kind": "image-build-plan", "schema_version": 99}, "unsupported"),
    ):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(CIError, match=message):
            Plan.from_json(json.loads(path.read_text()))


def test_a_plan_carrying_a_forged_channel_tag_is_refused(tmp_path: Path) -> None:
    # The promotion worklist is built from these values and each line becomes an
    # `imagetools create --tag`. A tab here would promote a channel nobody planned.
    document = _complete_plan().as_json()
    document["tools"][0]["execution_channel_tag"] = f"{REPOSITORY}:good\tevil"
    with pytest.raises(CIError, match="invalid Docker tag"):
        Plan.from_json(document)


def test_a_plan_carrying_an_empty_digest_is_refused_at_load() -> None:
    # An empty digest is absence wearing a value: it would otherwise mint
    # `repository@` as a promotion reference. Rejected on load, and `missing()`
    # treats a falsy digest as absent as well, so neither layer relies on the other.
    document = _complete_plan().as_json()
    document["tools"][0]["execution_digest"] = ""
    with pytest.raises(CIError, match="not a sha256 digest"):
        Plan.from_json(document)

    partial = replace(
        _complete_plan(),
        tools=tuple(
            replace(item, execution_digest=None) if index == 0 else item
            for index, item in enumerate(_complete_plan().tools)
        ),
    )
    assert partial.missing() == (partial.tools[0].execution_tag,)


def test_channels_refuses_an_incomplete_set(tmp_path: Path) -> None:
    root = repo_root()
    incomplete = build_plan(
        root, repository=REPOSITORY, ref_name="topic/x", is_main_publication=False, existing={}
    )
    plan_path = _write_plan(tmp_path, incomplete)
    assert (
        ci_cli.main(["channels", "--plan", str(plan_path), "--output", str(tmp_path / "c.tsv")])
        == 2
    )
    assert not (tmp_path / "c.tsv").exists()


def test_channels_emits_one_immutable_reference_per_tool(tmp_path: Path) -> None:
    plan_path = _write_plan(tmp_path, _complete_plan())
    output = tmp_path / "channels.tsv"
    assert ci_cli.main(["channels", "--plan", str(plan_path), "--output", str(output)]) == 0
    lines = output.read_text().strip().split("\n")
    assert len(lines) == 11
    for line in lines:
        tool, reference, channel = line.split("\t")
        assert reference == f"{REPOSITORY}@{DIGEST_C}"
        assert channel.endswith(f"execution-{tool}-branch-topic-x-{channel.rsplit('-', 1)[1]}")


def test_record_promotion_refuses_a_digest_the_plan_did_not_intend(tmp_path: Path) -> None:
    plan = _complete_plan()
    report = promotion_report(
        plan,
        checkout_revision=REVISION,
        set_digest=DIGEST_A,
        set_version_tag=f"{REPOSITORY}:set-v2-abcdef123456",
        previous_channels={},
        previous_set=None,
    )
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    tool = plan.tools[0].tool
    # A channel that moved somewhere other than the intended digest must fail,
    # not be recorded as a success.
    assert (
        ci_cli.main(
            [
                "record-promotion",
                "--report",
                str(path),
                "--tool",
                tool,
                "--promoted-digest",
                DIGEST_A,
            ]
        )
        == 2
    )
    assert json.loads(path.read_text())["execution_channels"][tool]["status"] == "planned"
    assert (
        ci_cli.main(
            [
                "record-promotion",
                "--report",
                str(path),
                "--tool",
                tool,
                "--promoted-digest",
                DIGEST_C,
            ]
        )
        == 0
    )
    assert json.loads(path.read_text())["execution_channels"][tool]["status"] == "promoted"


def test_the_set_channel_will_not_advance_over_an_unpromoted_channel(tmp_path: Path) -> None:
    plan = _complete_plan()
    report = promotion_report(
        plan,
        checkout_revision=REVISION,
        set_digest=DIGEST_A,
        set_version_tag=f"{REPOSITORY}:set-v2-abcdef123456",
        previous_channels={},
        previous_set=None,
    )
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    assert ci_cli.main(["assert-promoted", "--report", str(path)]) == 2
    for entry in report["execution_channels"].values():
        entry["status"] = "promoted"
    path.write_text(json.dumps(report), encoding="utf-8")
    assert ci_cli.main(["assert-promoted", "--report", str(path)]) == 0


def test_reconcile_refuses_two_plans_that_describe_different_sets(tmp_path: Path) -> None:
    complete = _complete_plan()
    planned = _write_plan(tmp_path, complete)
    subset = replace(complete, tools=complete.tools[:3])
    published = tmp_path / "published.json"
    published.write_text(json.dumps(subset.as_json()), encoding="utf-8")
    assert (
        ci_cli.main(
            [
                "reconcile",
                "--planned",
                str(planned),
                "--published",
                str(published),
                "--output",
                str(tmp_path / "merged.json"),
            ]
        )
        == 2
    )
    assert not (tmp_path / "merged.json").exists()


def test_the_ledger_tag_comes_from_the_manifest_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(b'{"kind":"test"}\n')
    destination = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(destination))
    assert ci_cli.main(["ledger-tag", "--manifest", str(manifest), "--repository", REPOSITORY]) == 0
    expected = hashlib.sha256(manifest.read_bytes()).hexdigest()
    written = dict(line.split("=", 1) for line in destination.read_text().strip().split("\n"))
    assert written["manifest-sha256"] == expected
    assert written["version-tag"] == f"{REPOSITORY}:set-v2-{expected[:12]}"


def test_an_unknown_subcommand_is_refused() -> None:
    assert ci_cli.main([]) == 2
    assert ci_cli.main(["not-a-subcommand"]) == 2


def test_the_roster_subcommand_agrees_with_the_package(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo_root())
    assert ci_cli.main(["roster", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == list(buildable_tools(repo_root()))


def test_a_layout_parent_must_still_be_named_by_digest() -> None:
    """The layout shortcut cannot smuggle in a mutable parent.

    Serving the parent from disk changes where the bytes come from, never how the
    image is identified — so the same digest rule applies, and it is enforced
    where the context is built rather than left to the callee.
    """
    from s3_listing_study.common.build_selection import BuildSelectionError, _layout_context

    parent = f"{REPOSITORY}@{DIGEST_B}"
    assert _layout_context(parent, "/tmp/layout") == {
        parent: f"oci-layout:///tmp/layout@{DIGEST_B}"
    }
    for mutable in (f"{REPOSITORY}:some-tag", "not-a-reference", f"{REPOSITORY}@sha256:short"):
        with pytest.raises(BuildSelectionError, match="immutable digest reference"):
            _layout_context(mutable, "/tmp/layout")
