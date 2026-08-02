# Repository validation scripts

`validate-tool-capsule.py` checks one function-grouped runnable-tool capsule.
Its current-contract mode validates `tool.json` and `claims.json` with Draft
2020-12 schemas, checks evidence references, verifies the root layout and README
contract, and resolves local Markdown links and fragments:

```sh
python3 scripts/validate-tool-capsule.py --tool s3-fast-list
```

The validator requires Python 3 and the `jsonschema` package.

Whether a capsule carries a migration stratum is read from the `MIGRATED_TOOLS`
roster in the script, not inferred from whether `data/claims.json` happens to
declare a `legacy_ledger` today. The roster and the ledger are two records of one
historical fact and must agree in both directions, so a migrated capsule cannot
shed its stratum — and skip every preservation check with it — by deleting the
ledger. Retiring a stratum is legitimate under the subject-retirement rule in
[`../docs/operating/tool-structure.md`](../docs/operating/tool-structure.md), and
removing the slug from the roster is how that decision becomes visible in the
diff an owner reviews. The schema enforces the same rule inside one document:
with a ledger, every claim carries `legacy_origins`; without one, no claim may.

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
python3 scripts/check-source-anchors.py --tool swath --require-checked \
  --source-root /path/to/swath-at-cef8ec2
```

`--tool` scopes to one capsule; with no `--tool`, every capsule under `tools/`.

A skipped anchor is an honest outcome, so three rules stop a skip reading as a
pass. `--require-checked` fails a run that verified no anchor at all — pass it
whenever the run's green result is meant to mean anchors were checked. A
`--source-root` no anchor ever resolved to is an error rather than a silent
no-op: the bare `PATH` form is associated only by sitting on the cited commit,
so one revision out of date it matches nothing, and a caller who supplied a
checkout and got a success must not have had it quietly ignored. And a Markdown
label spelling a complete path that names nothing at the commit fails, because
only an elided or ambiguous label (`.../Foo.java`, a bare `Foo.java`) is prose
being imprecise; `src/Missing.java` is a typo.

`--markdown [PATH ...]` additionally reads the inline `[SRC path:lines @ sha]`
labels in reports and reader notes. That mode is opt-in and best-effort in the
sense above: it resolves each abbreviated path against the file list at the
cited commit and reports the genuinely unresolvable ones as skipped.
`--self-test` builds a throwaway git repository and checks that the gate still
catches an out-of-range range, a path missing at the cited commit, a source root
on the wrong revision, a supplied root that would otherwise go unused, and a
complete prose path absent at the commit. CI runs **only** the self-test: a
hosted runner has no subject checkouts, so it cannot verify a project anchor,
and a checkout-free sweep there would report every anchor skipped and pass —
the green tick for work not done that this gate exists to prevent. The real
sweep is run by whoever holds the pinned checkouts, with `--require-checked`.

The parameterized migration procedure, evidence fences, fixture exceptions,
and review gates are in the tool capsule migration playbook.
