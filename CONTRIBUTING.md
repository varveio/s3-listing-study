# Contributing

Contributions are welcome, especially from the people who build and use the
tools included here.

## Read this first

Varve maintains this repo, decides what gets merged, and builds
[Swath](https://github.com/varveio/swath), one of the tools included here. We
know Swath better than we know the others, so we publish the setup and run
records and welcome help from the people who know those tools best. See the
[README](README.md) and [`docs/methodology.md`](docs/methodology.md).

The short version: we wrote the measurement plan down before running the
comparisons, put comparable effort into finding a supported setup for each
tool, and publish Swath results on the same terms whether or not they favor it.

## If we got your tool wrong

**Please tell us — this is the most valuable contribution possible.**

We may run a tool in a suboptimal mode, miss a flag that changes everything,
misread source, or carry forward an observation that no longer holds.

Open an issue and tell us what we missed. A link or reproducible run is useful,
but it is not required to start the conversation. We are happy to do the
digging. Helpful context can include:

- a better configuration or supported mode;
- a version where the behavior changed;
- a source link that improves our explanation;
- a run that behaves differently; or
- context about the workload the tool was designed for.

Use the **Help us get your tool right** issue form, or open a blank issue if that
fits better.

## What helps us reproduce a result

You can start a correction with partial context. Before we change a recorded
result, we will fill in the complete run record so the update remains checkable:

- **`VERIFIED: no` means nobody ran it.** Not "we're fairly confident." Promotion
  to `CONFIRMED` / `CORRECTED` / `UNVERIFIABLE` requires a committed receipt:
  exact invocation, tool version, declared machine type/resources, bucket identity and shape, exit code,
  wall-clock, peak RSS, raw output. **A reputable source is not a receipt. AWS's
  own docs are not a receipt. Source reading is not a receipt.**
- **Surprising or consequential observations need a reproducer.** We include the
  full run record before publishing the observation as a project result.
- **Label mixed provenance.** If a fact came from somewhere other than a run in
  this repo, say so on that page.
- **Third-party published numbers are context, not part of our comparison.**
  Every comparison we publish uses fresh VMs with the same declared machine
  type/resources and the same workload window.

## Repo layout

| Path | What |
| --- | --- |
| `docs/methodology.md` | How runs are conducted; written down before the comparisons. |
| `docs/operating/runner-security.md` | Cooperative Batch and stricter local-Docker execution profiles. |
| `docs/open-questions.md` | Questions and observations spanning several tools. |
| `docs/operating/tool-structure.md` | Authoritative directory and document-role contract for runnable tools. |
| `docs/operating/tool-onboarding.md` | The sequence for adding a new subject; pointer-based, owns the seams only. |
| `tools/README.md` | Every tool in scope and its current status. |
| `tools/<tool>/README.md` | That tool's current entry point; it routes to explanation, canonical claims, research, and evidence. |
| `harness/` | The shared run harness: how a run is staged, executed, scanned for secrets, and checked. Read this if you're checking our setup. |
| `harness/README.md` | What each harness script does and the run contract. |

Anything about one tool goes in that tool's directory. Read
[`docs/operating/tool-structure.md`](docs/operating/tool-structure.md) before changing that
directory's layout or deciding which document owns content.

## Running the checks

The repo's own regression and secret-scan suites need no Docker, bucket, or
network:

```sh
harness/tests/run-regressions.sh     # adapter + verifier regressions, plus the shellcheck lint gate
uv run pytest -q tests/test_receipt_scan.py tests/test_receipt_pipeline.py
                                      # scanner fixtures and full receipt scan/publish behavior
```

The two repository-structure gates are subcommands of the packaged CLI, so they
need the project environment (`uv sync`):

```sh
uv run s3-listing-study validate-capsule --tool <tool>   # one capsule against the structure contract
uv run s3-listing-study check-links                      # relative Markdown links on current-state pages
```

Note `harness/tests/run.sh` is **not** the test runner — it's a fixture
stand-in tool. The commands above are the relevant entry points. The lint gate skips
(loudly) if `shellcheck` isn't installed, so install it to get real coverage.

## Commits

**No AI attribution.** No `Co-Authored-By: Claude`, no `Generated with ...`, no
tool footers. Same for PR bodies. Imperative mood; explain *why*.

## Scope of these rules

The two rules that bind every contributor are the no-AI-attribution rule above
and the run-record requirements earlier on this page. [`AGENTS.md`](AGENTS.md)
carries the rest of the working conventions, but it's written for automated
agents working in the repo (tiering, routing, provenance discipline) — read it
if that's you; otherwise this page is all you need.

## Code of conduct

Corrections and disagreement come with this work and are always welcome. We
want to work through them together, with care for both the technical details
and the people who built the tools. See
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

## License

By contributing you agree your contributions are licensed under
[Apache-2.0](LICENSE).
