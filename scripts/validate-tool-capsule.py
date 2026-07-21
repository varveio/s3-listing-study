#!/usr/bin/env python3
"""Validate one function-grouped runnable-tool capsule."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:
    print("validate-tool-capsule: Python package 'jsonschema' is required", file=sys.stderr)
    raise SystemExit(2)


REQUIRED_DIRS = {"data", "docs", "adapter", "research", "receipts"}
ALLOWED_ROOT = REQUIRED_DIRS | {"README.md", "build"}
REQUIRED_FILES = {
    "data/tool.json", "data/claims.json", "docs/mechanism.md",
    "docs/running.md", "research/claims-migration.md", "adapter/run.sh",
    "adapter/normalize.sh", "research/tool-page.md",
}
REQUIRED_H2 = {
    "At a glance", "How it works", "Modes and study coverage", "What we learned",
    "Limitations and open questions", "Navigate this directory",
    "Provenance", "Evidence boundary",
}
FIXTURE_TOOLS = {"s3p", "s4cmd"}

HISTORICAL_BANNER = re.compile(
    r"^> \*\*Historical landing page \(\d{4}-\d{2}-\d{2}, capsule migration\)\.\*\* This is the full\n"
    r"^> pre-restructure landing page\. Any `current-state` wording below is historical\n"
    r"^> as of the date it records and is superseded by the root README and `data/`\.\n"
    r"^> Only this banner and link targets changed; body prose and evidence\n"
    r"^> qualifications are preserved\.\n\n",
    re.M,
)


def load_json(path: Path, errors: list[str]) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path}: {exc}")
        return None


def validate_schema(instance: object, schema_path: Path, label: str, errors: list[str]) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.path)):
        location = ".".join(str(part) for part in error.path) or "<root>"
        errors.append(f"{label}:{location}: {error.message}")


def check_claim_schema_contract(schema_path: Path, errors: list[str]) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())

    def document(status: str, evidence: list[dict[str, str]]) -> dict[str, object]:
        return {
            "$schema": "../../../schemas/claims.schema.json",
            "schema_version": 1,
            "tool": "schema-fixture",
            "legacy_ledger": {
                "source": "../research/tool-page.md",
                "migration_map": "../research/claims-migration.md",
                "expected_origins": ["M1"],
            },
            "claims": [{
                "id": "schema-fixture",
                "statement": "A schema contract fixture.",
                "scope": "runtime",
                "status": status,
                "disposition": "retained",
                "qualification": "Used only to test schema semantics.",
                "legacy_origins": ["M1"],
                "evidence": evidence,
            }],
        }

    valid = {
        "confirmed-run": document("confirmed", [{"kind": "run", "receipt": "../receipts/fixture"}]),
        "supported-source": document("supported", [{
            "kind": "source", "subject": "upstream", "repository": "https://example.com/repo",
            "commit": "abcdef0", "path": "src/main.rs",
        }]),
        "unverified-none": document("unverified", [{"kind": "none", "reason": "Not run."}]),
        "unverifiable-none": document("unverifiable", [{"kind": "none", "reason": "No surviving evidence."}]),
    }
    invalid = {
        "confirmed-with-none": document("confirmed", [
            {"kind": "run", "receipt": "../receipts/fixture"},
            {"kind": "none", "reason": "Contradictory evidence state."},
        ]),
        "supported-with-none": document("supported", [{"kind": "none", "reason": "Not evidence."}]),
        "unverified-with-source": document("unverified", [{
            "kind": "source", "subject": "upstream", "repository": "https://example.com/repo",
            "commit": "abcdef0", "path": "src/main.rs",
        }]),
        "source-with-receipt": document("supported", [{
            "kind": "source", "subject": "upstream", "repository": "https://example.com/repo",
            "commit": "abcdef0", "path": "src/main.rs", "receipt": "../receipts/fixture",
        }]),
        "run-with-source-fields": document("confirmed", [{
            "kind": "run", "receipt": "../receipts/fixture", "repository": "https://example.com/repo",
        }]),
    }
    for name, fixture in valid.items():
        if list(validator.iter_errors(fixture)):
            errors.append(f"claims schema rejects valid contract fixture: {name}")
    for name, fixture in invalid.items():
        if not list(validator.iter_errors(fixture)):
            errors.append(f"claims schema admits invalid contract fixture: {name}")


def git(repo: Path, *args: str, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, text=text, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )


def heading_ids(text: str) -> set[str]:
    ids: set[str] = set()
    counts: dict[str, int] = {}
    for match in re.finditer(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.M):
        label = re.sub(r"[`*~]", "", match.group(1)).strip().lower()
        slug = re.sub(r"[^\w\- ]", "", label, flags=re.UNICODE)
        slug = re.sub(r"\s", "-", slug).strip("-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        ids.add(slug if count == 0 else f"{slug}-{count}")
    ids.update(re.findall(r"<a\s+(?:name|id)=[\"']([^\"']+)", text, re.I))
    return ids


def check_markdown_links(capsule: Path, errors: list[str]) -> None:
    pattern = re.compile(r"\[[^]]*\]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)")
    for page in capsule.rglob("*.md"):
        if "receipts" in page.relative_to(capsule).parts:
            continue
        for raw_target in pattern.findall(page.read_text(encoding="utf-8")):
            if raw_target.startswith(("http://", "https://", "mailto:")):
                continue
            path_text, separator, fragment = raw_target.partition("#")
            resolved = page if not path_text else (page.parent / path_text).resolve()
            if not resolved.exists():
                errors.append(f"broken link in {page.relative_to(capsule)}: {raw_target}")
                continue
            if separator and fragment:
                fragment_file = resolved / "README.md" if resolved.is_dir() else resolved
                if fragment_file.suffix.lower() != ".md" or not fragment_file.is_file():
                    errors.append(f"fragment target is not Markdown in {page.relative_to(capsule)}: {raw_target}")
                elif fragment not in heading_ids(fragment_file.read_text(encoding="utf-8")):
                    errors.append(f"missing fragment in {page.relative_to(capsule)}: {raw_target}")


def mask_markdown_link_targets(text: str) -> str:
    pattern = re.compile(r"(!?\[[^]\n]*\]\()([^)\s]+)(\s+[\"'][^\"'\n]*[\"'])?(\))")
    return pattern.sub(
        lambda match: match.group(1) + "<TARGET>" + (match.group(3) or "") + match.group(4),
        text,
    )


def check_research_preservation(repo: Path, capsule: Path, tool: str, base: str, errors: list[str]) -> None:
    old_page = git(repo, "show", f"{base}:tools/{tool}/README.md")
    if old_page.returncode != 0:
        errors.append(f"cannot read historical tool page from {base}: {old_page.stderr.strip()}")
    else:
        page = capsule / "research" / "tool-page.md"
        if page.is_file():
            current = page.read_text(encoding="utf-8")
            banners = list(HISTORICAL_BANNER.finditer(current))
            if len(banners) != 1 or banners[0].start() > 200:
                errors.append("research/tool-page.md must contain one exact historical banner immediately after its title")
            else:
                banner = banners[0]
                without_banner = current[:banner.start()] + current[banner.end():]
                if mask_markdown_link_targets(without_banner) != mask_markdown_link_targets(old_page.stdout):
                    errors.append("research/tool-page.md changes body content beyond the banner and Markdown link targets")

    old_root = f"tools/{tool}/research"
    listed = git(repo, "ls-tree", "-r", "--name-only", base, "--", old_root)
    if listed.returncode != 0:
        errors.append(f"cannot inspect historical research from {base}: {listed.stderr.strip()}")
        return
    old_paths = {Path(line).relative_to(old_root) for line in listed.stdout.splitlines() if line}
    research = capsule / "research"
    current_paths = {path.relative_to(research) for path in research.rglob("*") if path.is_file()}
    expected_paths = old_paths | {Path("tool-page.md"), Path("claims-migration.md")}
    if current_paths != expected_paths:
        errors.append(
            "research file-set changed beyond adding tool-page.md and claims-migration.md: "
            f"missing={sorted(map(str, expected_paths-current_paths))} "
            f"unexpected={sorted(map(str, current_paths-expected_paths))}"
        )
    for relative in sorted(old_paths):
        current_path = research / relative
        if not current_path.is_file():
            continue
        old = git(repo, "show", f"{base}:{old_root}/{relative}", text=False)
        if old.returncode != 0:
            errors.append(f"cannot read historical research file from {base}: {relative}")
            continue
        current_bytes = current_path.read_bytes()
        if relative.suffix.lower() == ".md":
            try:
                old_text = old.stdout.decode("utf-8")
                current_text = current_bytes.decode("utf-8")
            except UnicodeDecodeError:
                errors.append(f"historical Markdown is not UTF-8: {relative}")
                continue
            if mask_markdown_link_targets(current_text) != mask_markdown_link_targets(old_text):
                errors.append(f"research/{relative} changes content beyond Markdown link targets")
        elif current_bytes != old.stdout:
            errors.append(f"research/{relative} differs from {base}")


def check_fixture_reclassification(repo: Path, capsule: Path, tool: str, base: str, errors: list[str]) -> None:
    old_root = f"tools/{tool}/receipts/smoke/_adapter"
    listed = git(repo, "ls-tree", "-r", "--name-only", base, "--", old_root)
    if listed.returncode != 0:
        errors.append(f"cannot inspect base fixtures: {listed.stderr.strip()}")
        return
    old_paths = [line for line in listed.stdout.splitlines() if line]
    if not old_paths:
        errors.append(f"{tool} is a fixture exception but {base} has no {old_root} files")
        return
    destination = capsule / "adapter" / "fixtures"
    expected_rel = {Path(path).relative_to(old_root) for path in old_paths}
    actual_rel = {path.relative_to(destination) for path in destination.rglob("*") if path.is_file()} if destination.exists() else set()
    if expected_rel != actual_rel:
        errors.append(
            f"fixture file-set mismatch: missing={sorted(map(str, expected_rel-actual_rel))} "
            f"unexpected={sorted(map(str, actual_rel-expected_rel))}"
        )
    for old_path in old_paths:
        relative = Path(old_path).relative_to(old_root)
        new_path = destination / relative
        if not new_path.is_file():
            continue
        old = git(repo, "show", f"{base}:{old_path}", text=False)
        if old.returncode != 0:
            errors.append(f"cannot read base fixture: {old_path}")
            continue
        allowed = old.stdout
        if tool == "s3p" and relative.as_posix() == "check.sh":
            allowed = allowed.replace(b"../../../normalize.sh", b"../normalize.sh")
        if tool == "s3p" and relative.name == "README.md":
            allowed = allowed.replace(
                b"tools/s3p/normalize.sh", b"tools/s3p/adapter/normalize.sh"
            )
        if new_path.read_bytes() != allowed:
            errors.append(f"fixture differs beyond named helper edit: {relative}")

    diff = git(repo, "diff", "--name-status", base, "--", f"tools/{tool}/receipts")
    if diff.returncode != 0:
        errors.append(f"cannot compare fixture receipts with {base}: {diff.stderr.strip()}")
    else:
        for line in diff.stdout.splitlines():
            status, _, path = line.partition("\t")
            if status != "D" or not path.startswith(old_root + "/"):
                errors.append(f"receipt change outside synthetic fixture removal: {line}")


def check_receipts(repo: Path, capsule: Path, tool: str, base: str, errors: list[str]) -> None:
    if tool in FIXTURE_TOOLS:
        check_fixture_reclassification(repo, capsule, tool, base, errors)
        return
    receipts = f"tools/{tool}/receipts"
    diff = git(repo, "diff", "--name-only", base, "--", receipts)
    if diff.returncode != 0:
        errors.append(f"could not compare receipts with {base}: {diff.stderr.strip()}")
    elif diff.stdout.strip():
        errors.append(f"receipts differ from {base}: {diff.stdout.strip()}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", required=True, help="tool directory slug")
    parser.add_argument(
        "--migration-base", "--base", dest="migration_base",
        help=(
            "pre-capsule git ref for the frozen migration regression; "
            "--base remains as a compatibility alias for the sealed migration playbook"
        ),
    )
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    capsule = repo / "tools" / args.tool
    errors: list[str] = []
    if not capsule.is_dir():
        print(f"validate-tool-capsule: missing {capsule}", file=sys.stderr)
        return 1

    actual_root = {entry.name for entry in capsule.iterdir()}
    for name in sorted(REQUIRED_DIRS - actual_root):
        errors.append(f"missing required root directory: {name}/")
    for name in sorted(actual_root - ALLOWED_ROOT):
        errors.append(f"unexpected item at capsule root: {name}")
    for relative in sorted(REQUIRED_FILES):
        if not (capsule / relative).is_file():
            errors.append(f"missing required file: {relative}")
    for name in sorted({"mechanism.md", "running.md", "run.sh", "normalize.sh", "Dockerfile"} & actual_root):
        errors.append(f"legacy mixed-responsibility root file remains: {name}")
    if (capsule / "docs" / "claims-migration.md").is_file():
        errors.append("legacy docs/claims-migration.md remains; the conservation audit lives in research/")

    build_exists = (capsule / "build").exists()
    if build_exists and not (capsule / "build" / "Dockerfile").is_file():
        errors.append("build/ exists without Dockerfile")
    if args.migration_base:
        base_has_dockerfile = git(
            repo, "cat-file", "-e",
            f"{args.migration_base}:tools/{args.tool}/Dockerfile",
        ).returncode == 0
        if base_has_dockerfile and not (capsule / "build" / "Dockerfile").is_file():
            errors.append("migration base contains Dockerfile but build/Dockerfile is missing")
        if not base_has_dockerfile and build_exists:
            errors.append("build/ exists even though the migration-base tool has no Dockerfile")

    tool_data = load_json(capsule / "data" / "tool.json", errors)
    claims_data = load_json(capsule / "data" / "claims.json", errors)
    check_claim_schema_contract(repo / "schemas" / "claims.schema.json", errors)
    if tool_data is not None:
        validate_schema(tool_data, repo / "schemas" / "tool.schema.json", "tool.json", errors)
    if claims_data is not None:
        validate_schema(claims_data, repo / "schemas" / "claims.schema.json", "claims.json", errors)

    if isinstance(tool_data, dict):
        if tool_data.get("slug") != args.tool:
            errors.append("tool.json slug does not match --tool")
        for value in tool_data.get("evidence_roots", []):
            if not (capsule / "data" / value).resolve().exists():
                errors.append(f"tool.json evidence root does not exist: {value}")
        tested = tool_data.get("tested", {})
        for field in ("version", "revision", "upstream_base"):
            item = tested.get(field) if isinstance(tested, dict) else None
            reference = item.get("provenance", {}).get("reference") if isinstance(item, dict) else None
            if reference and not reference.startswith("https://") and not (capsule / "data" / reference).resolve().exists():
                errors.append(f"tool.json {field} provenance reference does not exist: {reference}")

    if isinstance(claims_data, dict):
        if claims_data.get("tool") != args.tool:
            errors.append("claims.json tool does not match --tool")
        claims = claims_data.get("claims", [])
        ids = [claim.get("id") for claim in claims if isinstance(claim, dict)]
        if len(ids) != len(set(ids)):
            errors.append("claim IDs are not unique")
        if args.migration_base:
            ledger = claims_data.get("legacy_ledger", {})
            expected = ledger.get("expected_origins", []) if isinstance(ledger, dict) else []
            seen = {
                origin for claim in claims if isinstance(claim, dict)
                for origin in claim.get("legacy_origins", [])
            }
            if set(expected) != seen:
                errors.append(
                    "legacy origin conservation mismatch: "
                    f"missing={sorted(set(expected)-seen)} "
                    f"unexpected={sorted(seen-set(expected))}"
                )
            migration = capsule / "research" / "claims-migration.md"
            if migration.exists():
                parsed: dict[str, set[str]] = {}
                for match in re.finditer(
                    r"^\| ([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*) \| [^|]* \| ([^|]*) \|$",
                    migration.read_text(encoding="utf-8"),
                    re.M,
                ):
                    origin, claim_cell = match.groups()
                    if origin in parsed:
                        errors.append(f"claims-migration.md repeats origin: {origin}")
                    parsed[origin] = set(
                        re.findall(r"`([a-z0-9]+(?:-[a-z0-9]+)*)`", claim_cell)
                    )
                if set(parsed) != set(expected):
                    errors.append("claims-migration.md must contain each declared legacy origin exactly once")
                known_ids = {claim.get("id") for claim in claims if isinstance(claim, dict)}
                for origin, mapped_ids in parsed.items():
                    unknown = mapped_ids - known_ids
                    if unknown:
                        errors.append(
                            f"claims-migration.md {origin} names unknown claims: {sorted(unknown)}"
                        )
                    expected_ids = {
                        claim.get("id") for claim in claims
                        if isinstance(claim, dict) and origin in claim.get("legacy_origins", [])
                    }
                    if mapped_ids != expected_ids:
                        errors.append(
                            f"claims-migration.md {origin} disagrees with legacy_origins: "
                            f"map={sorted(mapped_ids)} claims={sorted(expected_ids)}"
                        )
        tested = tool_data.get("tested", {}) if isinstance(tool_data, dict) else {}
        upstream = tool_data.get("upstream", {}) if isinstance(tool_data, dict) else {}
        revision = tested.get("revision", {}).get("value", "") if isinstance(tested, dict) else ""
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            for evidence in claim.get("evidence", []):
                if not isinstance(evidence, dict):
                    continue
                for key in ("receipt", "artifact"):
                    value = evidence.get(key)
                    if value and not (capsule / "data" / value).resolve().exists():
                        errors.append(f"claim {claim.get('id')} has missing {key}: {value}")
                if evidence.get("kind") == "documentation" and evidence.get("path"):
                    value = evidence["path"]
                    if not (capsule / "data" / value).resolve().exists():
                        errors.append(f"claim {claim.get('id')} has missing documentation path: {value}")
                if evidence.get("kind") == "source" and evidence.get("subject") == "tested-variant":
                    if evidence.get("repository") != tested.get("repository"):
                        errors.append(f"claim {claim.get('id')} tested-variant source repository disagrees with tool.json")
                    if revision and not revision.startswith(evidence.get("commit", "")):
                        errors.append(f"claim {claim.get('id')} source commit disagrees with tested revision")
                if evidence.get("kind") == "source" and evidence.get("subject") == "upstream":
                    if evidence.get("repository") != upstream.get("repository"):
                        errors.append(f"claim {claim.get('id')} upstream source repository disagrees with tool.json")

    readme_path = capsule / "README.md"
    if readme_path.exists() and isinstance(tool_data, dict):
        readme = readme_path.read_text(encoding="utf-8")
        intro = re.split(r"^##\s+", readme, maxsplit=1, flags=re.M)[0]
        upstream_url = tool_data.get("upstream", {}).get("repository", "")
        if not upstream_url or upstream_url not in intro:
            errors.append("README introduction must link the tool.json upstream repository before the first H2")
        stable = "This study's groundwork is complete; no benchmark comparison has been run."
        if stable not in intro:
            errors.append("README introduction must contain the stable study-status sentence")
        h2 = set(re.findall(r"^## ([^#\n]+?)\s*$", readme, re.M))
        missing_h2 = REQUIRED_H2 - h2
        if missing_h2:
            errors.append(f"README contract missing H2 sections: {sorted(missing_h2)}")
        provenance_match = re.search(
            r"^## Provenance\s*$\n(?P<body>.*?)(?=^## |\Z)", readme, re.M | re.S,
        )
        if provenance_match:
            provenance = provenance_match.group("body")
            for required in ("Mixed provenance", "not a run record", "research/tool-page.md", "research/reconciliation.md"):
                if required not in provenance:
                    errors.append(f"README Provenance section does not name {required}")
        for required in ("data/claims.json", "research/claims-migration.md", "research/tool-page.md", "receipts/"):
            if required not in readme:
                errors.append(f"README navigation does not name {required}")
        for directory in sorted(REQUIRED_DIRS | ({"build"} if build_exists else set())):
            if f"{directory}/" not in readme:
                errors.append(f"README navigation does not name {directory}/")

    forbidden_names = {"claims.md", "tool.md", "catalog.json", "index.html"}
    for path in capsule.rglob("*"):
        if "receipts" in path.relative_to(capsule).parts:
            continue
        if path.is_file() and (path.name in forbidden_names or path.suffix == ".html"):
            errors.append(f"forbidden committed generated view: {path.relative_to(capsule)}")
    for path in (capsule / "data").glob("*"):
        if path.is_file() and path.suffix != ".json":
            errors.append(f"data/ contains non-JSON source: {path.name}")

    if args.migration_base:
        check_receipts(repo, capsule, args.tool, args.migration_base, errors)
        check_research_preservation(
            repo, capsule, args.tool, args.migration_base, errors,
        )
    check_markdown_links(capsule, errors)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"validate-tool-capsule: {len(errors)} error(s)", file=sys.stderr)
        return 1
    if args.migration_base:
        print(
            f"validate-tool-capsule: {args.tool} current contract and frozen "
            f"migration regression passed against {args.migration_base}"
        )
    else:
        print(f"validate-tool-capsule: {args.tool} current contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
