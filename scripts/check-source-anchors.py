#!/usr/bin/env python3
"""Check that every cited source anchor names lines that exist.

A `source` evidence entry in `data/claims.json` pins a repository, a commit, a
path, and optionally a line range. The proposition can be right while the line
numbers are wrong — mis-transcribed, or drifted from an earlier revision — and
nothing catches that by reading the ledger. Checking a cited range against the
file at the cited commit is mechanical, so this gate does it.

Scope: `data/claims.json` for every capsule under `tools/` (the authoritative,
structured target), and optionally the inline `[SRC path:lines @ shortsha]`
labels in Markdown reports (`--markdown`, best-effort — prose anchors are
free-form).

Resolution: the subject checkouts do not live in this repo, so a checkout is
supplied with `--source-root` (repeatable, `PATH` or `REPOSITORY=PATH`). A root
is used for a repository only when its HEAD is the commit the evidence cites: a
check against the wrong revision is worse than no check. Anchors with no root
for their repository are reported as skipped with a count, never as passing.

Three rules keep a skip from reading as a pass. A supplied root that no anchor
ever resolved to is an error, not a silent no-op — the bare-`PATH` form is
associated only by sitting on the cited commit, so one revision out of date it
matches nothing and would otherwise be ignored behind a zero exit. A Markdown
label that spells a complete path naming nothing at the commit fails, since only
an elided or ambiguous label is prose being imprecise. And `--require-checked`
fails a run that verified no anchor at all, so an invocation whose green tick is
meant to mean "anchors were checked" cannot mean anything less.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parent.parent

# [SRC path/to/file.java:12-18,30 @ abc1234]. One label may carry several
# semicolon-separated paths under a single trailing commit, and lines are
# optional, so the label body is split rather than matched in one pattern.
SRC_LABEL = re.compile(r"\[SRC\s+(?P<body>[^\]]*)\]")
LABEL_COMMIT = re.compile(r"@\s*(?P<commit>[0-9a-f]{7,40})\s*$")
LABEL_PART = re.compile(r"^(?P<path>[^\s:]+)(?::(?P<lines>[\d\s,-]+))?$")
LINES_SPEC = re.compile(r"^\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$")


def git(root: Path, *args: str, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], text=text, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )


def normalize_repository(url: str) -> str:
    """Reduce a remote URL to host/path so equivalent spellings compare equal."""
    value = url.strip().lower()
    value = re.sub(r"^(?:git\+)?(?:https?|ssh|git)://", "", value)
    value = re.sub(r"^[^/@]+@([^:/]+):", r"\1/", value)  # scp-style git@host:path
    value = value.removesuffix(".git").rstrip("/")
    return value


class SourceRoot:
    """One checkout offered on the command line."""

    def __init__(self, path: Path, label: str | None) -> None:
        self.path = path
        self.head = ""
        self.identities: set[str] = set()
        self.used = False  # set by run(): did any anchor resolve to this root?
        if label:
            self.identities.add(normalize_repository(label))

    def probe(self) -> str | None:
        """Read HEAD and remotes. Returns an error string when unusable."""
        if not self.path.is_dir():
            return f"source root is not a directory: {self.path}"
        head = git(self.path, "rev-parse", "HEAD")
        if head.returncode != 0:
            return f"source root is not a git checkout: {self.path}"
        self.head = head.stdout.strip()
        remotes = git(self.path, "remote", "-v")
        for line in remotes.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                self.identities.add(normalize_repository(parts[1]))
        return None

    def is_at(self, commit: str) -> bool:
        """True when this checkout's HEAD is the cited commit (short or full)."""
        return bool(self.head) and (
            self.head.startswith(commit) or commit.startswith(self.head)
        )


class Anchor:
    """One cited path[:lines] at one commit, with where it was cited from."""

    def __init__(
        self, tool: str, origin: str, repository: str | None,
        commit: str, path: str, lines: str | None, best_effort: bool = False,
    ) -> None:
        self.tool = tool
        self.origin = origin  # claim id, or "file:line" for a Markdown label
        self.repository = repository
        self.commit = commit
        # removeprefix, not lstrip: lstrip("./") would eat the leading dot of
        # a real path like .github/workflows/ci.yml.
        self.path = path.removeprefix("./")
        self.lines = lines
        self.best_effort = best_effort  # prose label: path may be abbreviated

    @property
    def cited(self) -> str:
        return f"{self.path}:{self.lines}" if self.lines else self.path

    def failure(self, reason: str) -> str:
        return f"{self.tool} · {self.origin} · {self.cited} · {reason}"


def parse_lines(spec: str) -> list[tuple[int, int]] | None:
    if not LINES_SPEC.match(spec):
        return None
    ranges = []
    for part in spec.split(","):
        start, _, end = part.partition("-")
        ranges.append((int(start), int(end) if end else int(start)))
    return ranges


def collect_json_anchors(capsule: Path, errors: list[str]) -> list[Anchor]:
    claims_path = capsule / "data" / "claims.json"
    try:
        data = json.loads(claims_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{claims_path.relative_to(REPO)}: {exc}")
        return []
    tool = data.get("tool", capsule.name) if isinstance(data, dict) else capsule.name
    anchors = []
    for claim in data.get("claims", []) if isinstance(data, dict) else []:
        if not isinstance(claim, dict):
            continue
        for evidence in claim.get("evidence", []):
            if not isinstance(evidence, dict) or evidence.get("kind") != "source":
                continue
            path = evidence.get("path")
            commit = evidence.get("commit")
            if not path or not commit:
                continue  # schema-level defect; the capsule validator owns it
            anchors.append(Anchor(
                tool, str(claim.get("id")), evidence.get("repository"),
                str(commit), str(path), evidence.get("lines"),
            ))
    return anchors


def markdown_pages(targets: list[Path], capsules: list[Path]) -> list[Path]:
    pages: set[Path] = set()
    roots = targets or capsules
    for target in roots:
        if target.is_file() and target.suffix.lower() == ".md":
            pages.add(target)
        elif target.is_dir():
            pages.update(
                page for page in target.rglob("*.md")
                if "receipts" not in page.parts
            )
    return sorted(pages)


def collect_markdown_anchors(pages: list[Path]) -> tuple[list[Anchor], int, int]:
    """Returns (anchors, labels seen, labels that did not parse).

    Prose labels are free-form, so a label this cannot read is counted and
    reported rather than dropped: a label nobody checked must not look checked.
    """
    anchors: list[Anchor] = []
    labels = 0
    unparsed = 0
    for page in pages:
        try:
            text = page.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            relative = page.relative_to(REPO)
        except ValueError:
            relative = page
        parts = relative.parts
        tool = parts[1] if len(parts) > 2 and parts[0] == "tools" else "-"
        # A page that pins exactly one commit lets its abbreviated continuation
        # labels (which carry no `@ sha`) be read against that commit.
        page_commits = {
            found.group("commit") for label in SRC_LABEL.finditer(text)
            for found in [LABEL_COMMIT.search(label.group("body"))] if found
        }
        page_commit = page_commits.pop() if len(page_commits) == 1 else None
        for match in SRC_LABEL.finditer(text):
            labels += 1
            line_number = text.count("\n", 0, match.start()) + 1
            commit_match = LABEL_COMMIT.search(match.group("body"))
            if commit_match:
                commit = commit_match.group("commit")
                body = match.group("body")[:commit_match.start()]
            elif page_commit:
                commit = page_commit
                body = match.group("body")
            else:
                unparsed += 1  # no pinned commit anywhere to check this against
                continue
            for segment in body.split(";"):
                segment = segment.strip().rstrip(",").strip()
                if not segment:
                    continue
                part = LABEL_PART.match(segment)
                lines = re.sub(r"\s+", "", part.group("lines")) if part and part.group("lines") else None
                if not part or (lines and not LINES_SPEC.match(lines)):
                    unparsed += 1
                    continue
                anchors.append(Anchor(
                    tool, f"{relative}:{line_number}", None,
                    commit, part.group("path"), lines, best_effort=True,
                ))
    return anchors, labels, unparsed


def tree_files(root: SourceRoot, commit: str, cache: dict) -> set[str]:
    key = (str(root.path), commit)
    if key not in cache:
        listing = git(root.path, "ls-tree", "-r", "--name-only", commit)
        cache[key] = set(listing.stdout.splitlines()) if listing.returncode == 0 else set()
    return cache[key]


def resolve_best_effort_path(
    anchor: Anchor, root: SourceRoot, cache: dict,
) -> tuple[str | None, str | None]:
    """Resolve an abbreviated prose path (.../Foo.java, …Foo.java, Foo.java).

    Returns (path, None) for the one file at the commit the label can only
    mean; (None, None) when the label is genuinely abbreviated or ambiguous,
    which prose is allowed to be, so it is reported as skipped; and
    (None, reason) when the label spelled a complete path — no elision marker,
    more than one segment — that names nothing at the commit and is a suffix of
    nothing there either. That is a typo (`src/Missing.java`), not an
    approximation, and counting it as skipped let a demonstrably bad anchor
    read as one nobody could check.
    """
    files = tree_files(root, anchor.commit, cache)
    # Strip only an explicit elision marker: a bare leading "." is part of a
    # real path (.github/workflows/ci.yml).
    elided = re.match(r"^(?:\.\.\.|…)+/?", anchor.path)
    suffix = anchor.path[elided.end():] if elided else anchor.path
    if suffix in files:
        return suffix, None
    candidates = [path for path in files if path.endswith("/" + suffix)]
    if not candidates and "/" not in suffix:
        candidates = [path for path in files if path.rpartition("/")[2].endswith(suffix)]
    if len(candidates) == 1:
        return candidates[0], None
    if not candidates and not elided and "/" in suffix:
        return None, f"path does not exist at {anchor.commit[:7]}"
    # No candidates for a bare filename stays skippable: prose names classes and
    # generated files that legitimately are not paths in the tree. Several
    # candidates stays skippable in every form: the label really is ambiguous.
    return None, None


def file_lines(root: SourceRoot, commit: str, path: str, cache: dict) -> int | str:
    """Line count of path at commit, or a reason string when it is not a file."""
    key = (str(root.path), commit, path)
    if key not in cache:
        kind = git(root.path, "cat-file", "-t", f"{commit}:{path}")
        if kind.returncode != 0:
            cache[key] = f"path does not exist at {commit[:7]}"
        elif kind.stdout.strip() != "blob":
            cache[key] = f"path is a {kind.stdout.strip()}, not a file, at {commit[:7]}"
        else:
            blob = git(root.path, "show", f"{commit}:{path}", text=False)
            content = blob.stdout
            count = content.count(b"\n")
            if content and not content.endswith(b"\n"):
                count += 1
            cache[key] = count
    return cache[key]


def check_anchor(anchor: Anchor, root: SourceRoot, path: str, cache: dict) -> str | None:
    length = file_lines(root, anchor.commit, path, cache)
    if isinstance(length, str):
        return length
    if not anchor.lines:
        return None
    ranges = parse_lines(anchor.lines)
    if ranges is None:
        return f"unparseable line specification: {anchor.lines}"
    for start, end in ranges:
        if start < 1:
            return "line 0 is not a line number"
        if end < start:
            return f"inverted line range {start}-{end}"
        if end > length:
            return f"cited line {end} is past end of file ({length} lines at {anchor.commit[:7]})"
    return None


def resolve_root(anchor: Anchor, roots: list[SourceRoot]) -> SourceRoot | None:
    """Pick the checkout that speaks for this anchor's repository, if any."""
    if anchor.repository:
        identity = normalize_repository(anchor.repository)
        matched = [root for root in roots if identity in root.identities]
        if matched:
            for root in matched:
                if root.is_at(anchor.commit):
                    return root
            return matched[0]
    # No repository (Markdown labels carry none) or no root claims it: a
    # checkout sitting exactly on the cited commit is unambiguously the right
    # bytes, since a commit id names one tree.
    for root in roots:
        if root.is_at(anchor.commit):
            return root
    return None


class Outcome(NamedTuple):
    checked: int
    failures: list[str]
    refusals: list[str]  # one line per (root, repository) on the wrong revision
    refused: int  # anchors those refusals cover
    skipped: dict[tuple[str, str], int]  # (repository, commit) -> anchor count
    unresolved: dict[str, int]  # commit -> Markdown labels whose path did not resolve
    unused: list[str]  # one line per supplied root no anchor ever resolved to


def run(anchors: list[Anchor], roots: list[SourceRoot]) -> Outcome:
    blobs: dict = {}
    trees: dict = {}
    checked = 0
    failures: list[str] = []
    refused: dict[tuple[str, str, str, str], int] = {}
    skipped: dict[tuple[str, str], int] = {}
    unresolved: dict[str, int] = {}
    for root in roots:
        root.used = False
    for anchor in anchors:
        root = resolve_root(anchor, roots)
        if root is None:
            key = (anchor.repository or "(no repository cited)", anchor.commit)
            skipped[key] = skipped.get(key, 0) + 1
            continue
        root.used = True
        if not root.is_at(anchor.commit):
            key = (str(root.path), root.head, anchor.repository or "cited", anchor.commit)
            refused[key] = refused.get(key, 0) + 1
            continue
        path = anchor.path
        if anchor.best_effort:
            path, unresolvable = resolve_best_effort_path(anchor, root, trees)
            if path is None and not unresolvable:
                unresolved[anchor.commit] = unresolved.get(anchor.commit, 0) + 1
                continue
            if path is None:
                checked += 1  # the label was resolved far enough to be wrong
                failures.append(anchor.failure(unresolvable))
                continue
        checked += 1
        reason = check_anchor(anchor, root, path, blobs)
        if reason:
            failures.append(anchor.failure(reason))
    refusals = [
        f"source root {path} is at HEAD {head[:7]} but {repository} evidence cites "
        f"{commit[:7]} — refusing to check {count} anchor(s) against the wrong revision"
        for (path, head, repository, commit), count in sorted(refused.items())
    ]
    # A root the caller supplied and this never used is not a neutral fact: the
    # caller believes they handed over a checkout and got a verdict about it. The
    # bare-PATH form makes that easy to hit -- a checkout with no remote naming
    # the cited repository is associated only by sitting on the cited commit, so
    # one revision out of date it matches nothing at all and every anchor it was
    # meant to answer for is merely skipped. Say so, and fail.
    unused = [
        f"source root {root.path} was never used: "
        + (
            f"it names no repository (no remote, and no REPOSITORY= label)"
            if not root.identities
            else f"it names {', '.join(sorted(root.identities))}"
        )
        + f" and its HEAD {root.head[:7]} is not a commit any anchor in scope "
        "cites — a supplied checkout must be checked against or refused, never "
        "silently ignored"
        for root in roots if not root.used
    ]
    return Outcome(
        checked, failures, refusals, sum(refused.values()), skipped, unresolved, unused,
    )


def self_test() -> int:
    """Prove the gate still catches the defect class, without a subject checkout.

    Builds a throwaway repository whose first commit holds a 38-line file and
    whose second commit does not, then exercises the behaviours the gate exists
    for. Offline, git-only, no fixture files in the tree.
    """
    identity = "https://example.invalid/subject"
    author = ("-c", "user.email=gate@example.invalid", "-c", "user.name=gate")
    failed: list[str] = []
    cases = 0

    def case(held: bool, complaint: str) -> None:
        """Record one behaviour, so the reported case count cannot drift."""
        nonlocal cases
        cases += 1
        if not held:
            failed.append(complaint)

    with tempfile.TemporaryDirectory() as workspace:
        subject = Path(workspace) / "subject"
        (subject / "src").mkdir(parents=True)
        source = subject / "src" / "Small.java"
        source.write_text("\n".join(f"line {n}" for n in range(1, 39)) + "\n", encoding="utf-8")
        # Two files sharing a basename, so an abbreviated prose label can be
        # genuinely ambiguous rather than merely absent.
        for folder in ("a", "b"):
            (subject / folder).mkdir()
            (subject / folder / "Dup.java").write_text("only line\n", encoding="utf-8")
        git(subject, "init", "-q", "-b", "main")
        git(subject, "add", "-A")
        git(subject, *author, "commit", "-q", "-m", "seed")
        pinned = git(subject, "rev-parse", "HEAD").stdout.strip()
        source.write_text("one line now\n", encoding="utf-8")
        git(subject, "add", "-A")
        git(subject, *author, "commit", "-q", "-m", "move on")
        pinned_checkout = Path(workspace) / "pinned"
        git(subject, "worktree", "add", "-q", "--detach", str(pinned_checkout), pinned)

        def anchor(path: str, lines: str | None) -> list[Anchor]:
            return [Anchor("fixture", "fixture-claim", identity, pinned, path, lines)]

        def prose(path: str, lines: str | None) -> list[Anchor]:
            # A Markdown [SRC] label: best-effort, and carrying no repository.
            return [Anchor("fixture", "notes.md:1", None, pinned, path, lines, True)]

        def source_root(path: Path, label: str | None = identity) -> SourceRoot:
            root = SourceRoot(path, label)
            problem = root.probe()
            if problem:
                failed.append(problem)
            return root

        pinned_root = source_root(pinned_checkout)
        moved_root = source_root(subject)  # HEAD is the later commit

        outcome = run(anchor("src/Small.java", "10-38"), [pinned_root])
        case(
            outcome.checked == 1 and not outcome.failures and not outcome.refusals,
            f"in-range anchor did not pass cleanly: {outcome}",
        )
        outcome = run(anchor("src/Small.java", "85-107"), [pinned_root])
        case(
            any("past end of file" in failure for failure in outcome.failures),
            f"out-of-range anchor not caught: {outcome.failures}",
        )
        outcome = run(anchor("src/Gone.java", "1"), [pinned_root])
        case(
            any("does not exist" in failure for failure in outcome.failures),
            f"missing path not caught: {outcome.failures}",
        )
        outcome = run(anchor("src/Small.java", "85-107"), [moved_root])
        case(
            not outcome.checked and not outcome.failures and outcome.refused == 1,
            f"wrong-revision root was not refused: {outcome}",
        )
        outcome = run(anchor("src/Small.java", "1"), [])
        case(
            not outcome.checked and sum(outcome.skipped.values()) == 1,
            f"anchor with no source root was not skipped: {outcome}",
        )

        # The documented bare-PATH form: a checkout with no remote naming the
        # cited repository is associated only by sitting on the cited commit.
        # One revision out of date it matches nothing, and used to be ignored in
        # silence behind a zero exit.
        outcome = run(anchor("src/Small.java", "1"), [source_root(subject, None)])
        case(
            not outcome.checked and len(outcome.unused) == 1
            and sum(outcome.skipped.values()) == 1,
            f"unusable bare-PATH source root was not refused: {outcome}",
        )
        outcome = run(anchor("src/Small.java", "10-38"), [source_root(pinned_checkout, None)])
        case(
            outcome.checked == 1 and not outcome.failures and not outcome.unused,
            f"bare-PATH root at the cited commit did not check: {outcome}",
        )

        # Prose labels are best-effort, but only where prose is actually
        # imprecise: an elided or ambiguous path is skippable, a complete one
        # that names nothing at the commit is a defect like any other.
        outcome = run(prose("src/Missing.java", "1"), [pinned_root])
        case(
            any("does not exist" in failure for failure in outcome.failures),
            f"complete-but-absent Markdown path was not failed: {outcome}",
        )
        outcome = run(prose(".../Missing.java", "1"), [pinned_root])
        case(
            not outcome.failures and sum(outcome.unresolved.values()) == 1,
            f"elided Markdown path was not skipped: {outcome}",
        )
        outcome = run(prose("Dup.java", "1"), [pinned_root])
        case(
            not outcome.failures and sum(outcome.unresolved.values()) == 1,
            f"ambiguous Markdown path was not skipped: {outcome}",
        )
        outcome = run(prose(".../Small.java", "10-38"), [pinned_root])
        case(
            outcome.checked == 1 and not outcome.failures,
            f"resolvable elided Markdown path was not checked: {outcome}",
        )
        outcome = run(prose(".../Small.java", "85-107"), [pinned_root])
        case(
            any("past end of file" in failure for failure in outcome.failures),
            f"out-of-range Markdown anchor not caught: {outcome.failures}",
        )
        git(subject, "worktree", "remove", "--force", str(pinned_checkout))

    for case in failed:
        print(f"ERROR: self-test: {case}", file=sys.stderr)
    if failed:
        print(f"check-source-anchors: self-test failed ({len(failed)} case(s))", file=sys.stderr)
        return 1
    print(f"check-source-anchors: self-test passed ({cases} case(s))")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", help="tool directory slug; default every capsule under tools/")
    parser.add_argument(
        "--source-root", dest="source_roots", action="append", default=[], metavar="[REPOSITORY=]PATH",
        help=(
            "checkout to resolve cited paths against, repeatable; its HEAD must be "
            "the cited commit. REPOSITORY= pins it to one evidence repository when "
            "the checkout's remotes do not name it"
        ),
    )
    parser.add_argument(
        "--markdown", nargs="*", default=None, metavar="PATH",
        help=(
            "also check inline [SRC path:lines @ shortsha] labels (best-effort); "
            "with no PATH, every Markdown page in the selected capsules"
        ),
    )
    parser.add_argument(
        "--require-checked", action="store_true",
        help=(
            "fail when the run verified no anchor at all; pass it wherever a "
            "green result is meant to mean anchors were checked, so a sweep "
            "with no usable checkout cannot read as a clean one"
        ),
    )
    parser.add_argument(
        "--self-test", action="store_true",
        help="check the gate itself against a synthetic repository and exit",
    )
    args = parser.parse_args()
    if args.self_test:
        return self_test()

    if args.tool:
        capsules = [REPO / "tools" / args.tool]
        if not (capsules[0] / "data" / "claims.json").is_file():
            print(f"check-source-anchors: no capsule ledger at tools/{args.tool}/data/claims.json", file=sys.stderr)
            return 1
    else:
        capsules = sorted(
            entry for entry in (REPO / "tools").iterdir()
            if (entry / "data" / "claims.json").is_file()
        )

    roots: list[SourceRoot] = []
    for spec in args.source_roots:
        label, separator, path_text = spec.partition("=")
        root = SourceRoot(Path(path_text if separator else label).expanduser().resolve(),
                          label if separator else None)
        problem = root.probe()
        if problem:
            print(f"check-source-anchors: {problem}", file=sys.stderr)
            return 2
        roots.append(root)

    errors: list[str] = []
    anchors: list[Anchor] = []
    for capsule in capsules:
        anchors.extend(collect_json_anchors(capsule, errors))
    labels = unparsed = 0
    if args.markdown is not None:
        targets = [Path(value).expanduser().resolve() for value in args.markdown]
        pages = markdown_pages(targets, capsules)
        markdown, labels, unparsed = collect_markdown_anchors(pages)
        anchors.extend(markdown)

    outcome = run(anchors, roots)
    errors.extend(outcome.refusals)
    errors.extend(outcome.unused)
    errors.extend(outcome.failures)
    if args.require_checked and not outcome.checked:
        errors.append(
            f"--require-checked: this run verified 0 of {len(anchors)} anchor(s) in "
            "scope — a sweep that checked nothing must not report success"
        )
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    for (repository, commit), count in sorted(outcome.skipped.items()):
        print(
            f"check-source-anchors: skipped {count} anchor(s) — no --source-root "
            f"at {commit[:7]} for {repository}"
        )
    for commit, count in sorted(outcome.unresolved.items()):
        print(
            f"check-source-anchors: skipped {count} Markdown anchor(s) at {commit[:7]} "
            "— abbreviated or ambiguous path, unresolvable without the prose context"
        )
    if unparsed:
        print(
            f"check-source-anchors: skipped {unparsed} Markdown anchor(s) — "
            "not readable as [SRC path:lines @ commit]"
        )
    scope = f"{len(capsules)} capsule(s)"
    if args.markdown is not None:
        scope += f", {len(pages)} page(s), {labels} [SRC] label(s)"
    skipped = sum(outcome.skipped.values()) + sum(outcome.unresolved.values()) + unparsed
    summary = (
        f"check-source-anchors: {scope}: {len(anchors) + unparsed} anchor(s), "
        f"{outcome.checked} checked, {skipped} skipped, {len(outcome.failures)} failed"
    )
    if outcome.refused:
        summary += f", {outcome.refused} refused (source root on the wrong revision)"
    print(summary)
    if anchors and not outcome.checked:
        print(
            "check-source-anchors: nothing was verified — no --source-root matched "
            "a cited repository at its cited commit"
        )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
