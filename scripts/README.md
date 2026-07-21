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

The parameterized migration procedure, evidence fences, fixture exceptions,
and review gates are in the tool capsule migration playbook.
