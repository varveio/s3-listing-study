# Repository validation scripts

`validate-tool-capsule.py` checks one function-grouped runnable-tool capsule.
Its current-contract mode validates `tool.json` and `claims.json` with Draft
2020-12 schemas, checks evidence references, verifies the root layout and README
contract, and resolves local Markdown links and fragments:

```sh
python3 scripts/validate-tool-capsule.py --tool s3-fast-list
```

The validator requires Python 3 and the `jsonschema` package.

The completed capsule migration also has a separate, frozen regression. It
checks legacy-claim conservation, preserved research, receipt immutability, and
the two synthetic-fixture reclassifications against a commit where
`tools/<tool>/README.md` is still the historical pre-capsule page. Since PR #22
merged, that means the last pre-migration commit on `main`, not `main` itself:

```sh
python3 scripts/validate-tool-capsule.py --tool s3-fast-list \
  --migration-base f5beafd4d8e83a605af38aa7e22a75d94cbaa50b
```

`--base` remains a compatibility alias for the sealed migration playbook. CI
runs current-contract validation and the frozen migration regression as
separately named checks for every runnable tool.

`check-links.py` checks relative Markdown links and heading fragments on the
repo's current-state surfaces (root pages, `docs/`, the harness, scripts, and
tools overviews, plus the README-only contextual tool directories that carry
no capsule). Capsule-internal pages are covered by the validator, and internal
working notes (not published) are dated history, so neither is in its scope.
No arguments:

```sh
python3 scripts/check-links.py
```

`check-source-anchors.py` checks that every cited source anchor names lines
that exist. A `source` evidence entry in `data/claims.json` pins a repository,
a commit, a path, and often a line range; the proposition can be right while the
line numbers are wrong, and nothing else catches that. The checkouts are not in
this repo, so one is supplied per repository with `--source-root` (repeatable,
`PATH` or `REPOSITORY=PATH`), and a root is used only when its HEAD is the
commit the evidence cites — checking against the wrong revision is worse than
not checking. Files are read with `git show <commit>:<path>`, never from the
working tree. Anchors with no root for their repository are reported as skipped
with a count, never as passing:

```sh
python3 scripts/check-source-anchors.py --tool swath \
  --source-root /path/to/swath-at-cef8ec2
```

`--tool` scopes to one capsule; with no `--tool`, every capsule under `tools/`.
`--markdown [PATH ...]` additionally reads the inline `[SRC path:lines @ sha]`
labels in reports and reader notes. That mode is opt-in and best-effort: prose
anchors abbreviate paths (`.../Foo.java`), so it resolves each against the file
list at the cited commit and reports the ones it cannot resolve unambiguously
as skipped rather than failing them. `--self-test` builds a throwaway git
repository and checks that the gate still catches an out-of-range range, a path
missing at the cited commit, and a source root on the wrong revision; CI runs
that, plus a checkout-free sweep that reports its skip count, since a hosted
runner has no subject checkouts to resolve anchors against.

The parameterized migration procedure, evidence fences, fixture exceptions,
and review gates are in the tool capsule migration playbook.
