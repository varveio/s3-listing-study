# S3 Listing Study

We build [Swath](https://github.com/varveio/swath), a tool for listing large
S3 buckets. Before building it, we studied how existing listing tools approached
the problem. That work helped shape Swath, and it left us with a pile of notes:
one tool is much faster than the CLI, another struggles at very large scale,
another only parallelizes transfers. When we went looking for the runs behind
those statements, a lot of them were hard to find. Some of the notes were ours,
and we had not properly checked them either.

So we started testing. This repo is where we install each tool, read how it
works, run it against real buckets, and write down what we see.

This is not a leaderboard. These tools were built for different jobs, and we
are not crowning an overall winner. We are part of the story: Varve builds
Swath, maintains this repo, and decides what gets merged, and our earlier tool
research informed Swath's design. When we report an observation from a run, we
tie it to a specific version, setup, bucket, and machine, with a link to that
run.

## Why this exists

Our early research mixed notes from tool documentation, source code, issue
trackers, and published benchmarks. It was useful when deciding how Swath should
work, but it was not a reproducible comparison. When we went back to check the
notes, we found that many observations — including some we had treated as
settled — did not have a reproducible run behind them.

This project is us doing that homework properly: install each tool, read its
documentation, inspect source where the documentation does not settle a
question, run it against real buckets in the modes it actually offers, and
save enough detail for someone else to check our work. The original starting
notes are preserved in each tool's `research/` directory and reconciliation;
they are questions to check, not results to repeat.

Benchmark numbers age quickly. Mechanism reports — how a tested version divides
the keyspace, paginates, uses memory, and handles failures — often remain useful
longer.

## Status

**Groundwork is complete; comparative measurement has not started. No benchmark
result exists in this repository yet.**

Every subject has completed the groundwork pipeline: a pinned build or source
checkout, a source-anchored mechanism report, claim-by-claim reconciliation,
and smoke receipts where the tool could list the registered public bucket.
Smoke timings are harness diagnostics and facts about individual runs, not
comparative performance results.

Groundwork split the roster into two cohorts:

- **Ran anonymously at smoke:** `aws-cli`, `s5cmd`, `s7cmd`, `rclone`,
  `minio-mc`, `s3-fast-list` (a disclosed fork build pending an upstream
  `--no-sign-request` contribution), and Swath. A smoke run does not by itself
  establish correctness or performance beyond that exact run.
- **Blocked without credentials:** `s3p`, `s3kor`, `s4cmd`, and `ps3` expose no
  unsigned request path. Their committed capability receipts document that
  limitation; they have not successfully listed the smoke bucket.

See [`tools/README.md`](tools/README.md) for the per-tool status,
[`docs/methodology.md`](docs/methodology.md) for the measurement plan we wrote
down before running the comparisons, and
[`docs/operating/runner-security.md`](docs/operating/runner-security.md) for the mandatory boundary
that must be activated before any further networked container execution.

## What we do

For each tool in scope:

1. **Learn how listing works.** Does it parallelize listing, or only transfers?
   How does it choose cut-points in the keyspace? Documentation first; source
   when the documentation is silent or ambiguous.
2. **Run it for real** against sample buckets with known, differing keyspace
   shapes — flat, deep, dense-tailed, and sparse.
3. **Try its real modes.** If it has a concurrency knob, sweep it. If it
   accepts hints from a prior listing, run both cold and hinted paths. If it has
   a fast-path flag, use it. We put comparable effort into finding a supported
   setup for each tool rather than testing only defaults.
4. **Try it under limits and interruption.** Constrain memory, increase scale,
   and interrupt it mid-run. Record what happens, including whether output is
   complete and whether the exit status describes the result accurately.
5. **Publish the run record.** Record the exact invocation, tool version, box spec,
   bucket identity, exit code, captured output or content-addressed artifact,
   and verifier result.

## How we work

This project is maintained by [Varve](https://varve.io/), which builds
[Swath](https://github.com/varveio/swath), one of the tools evaluated here.
Varve's object-storage work depends on understanding large bucket listings;
[Outcrop](https://outcrop.varve.io/) is a public example of that work over
open-data buckets.

We naturally know Swath better than we know the other tools. We publish our
setup and run records so people who know those tools can help us improve the
work. Varve decides what gets merged. This is a project we run because we are
interested in the space, not a sales comparison.

- **We wrote the plan down first.** What gets measured was committed before
  comparative runs — see [`docs/methodology.md`](docs/methodology.md). Material
  later changes are dated and explained, so the history stays easy to follow.
- **The motivating workload is declared.** Swath was designed for first-contact
  listing: an unfamiliar bucket with no hints or prior inventory. That creates
  a framing bias, so first-contact, hinted, and warm-path results are treated as
  distinct workloads and reported separately.
- **Swath is recorded on the same terms.** It uses the same correctness
  verifier, benchmark harness, resource limits, limits-and-interruption checks,
  run-record requirements, and review. Our familiarity with it does not turn an
  unpublished result into a result for this project.
- **We look for a useful supported setup for every tool.** If one tool is tuned, comparable knobs are
  explored for the others and the tuning is disclosed. Where a tool's best
  supported configuration is unclear, its maintainers are invited to correct
  the setup.
- **Swath results are published on the same terms whether or not they favor it.**
  Readers should be able to see where another approach works better or Swath
  does not suit the workload.
- **Surprising or consequential observations include a reproducer.** The run
  record carries the invocation, version, environment, bucket identity, exit
  code, and captured output needed to check it.
- **Maintainer context is welcome.** If we run into something surprising, we'll
  usually check with the tool's maintainers before we write it up — the way we'd
  want someone to do for us. Reproducible problems are filed upstream where
  appropriate.
- **Patches and forks are disclosed.** Study-modified builds are identified
  explicitly and are never presented as upstream behavior. Comparative
  benchmarks use upstream releases unless a documented exception is part of
  the result.

We expect to miss better modes and misunderstand details. Corrections from the
people who know these tools are especially welcome.

## How the tool pages evolve

Each tool began with inherited starting notes compiled from public
documentation, source, issue trackers, and published observations. Groundwork
then used a separate source-first pass: the researcher first worked from
upstream docs, pinned source, and its own smoke runs without seeing that sheet.
Only after that report existed were the two compared item by item.

Every runnable subject uses the same capsule layout: a concise `README.md`
that routes to machine-readable identity and claims (`data/`), the current
explanation (`docs/`), harness integration (`adapter/`), the frozen research
trail (`research/`), and committed run evidence (`receipts/`). The layout and
the responsibilities of every layer are described in
[`tools/README.md`](tools/README.md) and defined in
[`docs/operating/tool-structure.md`](docs/operating/tool-structure.md).

Updates are observation-specific, not page-wide. Documentation and source can
support a description or explain a question, but executable behavior is not a
project result until a committed run checks it. We expect some of the starting
notes to be wrong; finding and fixing those is part of the work.

## Two phases

**Phase 1 is real S3:** real buckets, real networks, and real failures.
Credibility is established there first, because a real-S3 result needs no
argument about whether the environment was faithful.

**Phase 2 adds Swath's replay endpoint:** a captured listing served as an S3
`ListObjectsV2` endpoint for deterministic request counting, request-shape
capture, and fault injection. Because the endpoint is part of Swath, it must be
checked against real-S3 captures before it is used to describe other tools. It
reports observations; it does not decide correctness by itself.

## Results

Comparative results have not been published yet. When available, versioned
campaigns and machine-readable datasets will live under `results/`. Smoke
receipts remain run-specific evidence about correctness and testability, not
benchmark results.

## Contributing

**If we got your tool wrong, please tell us** — see
[`CONTRIBUTING.md`](CONTRIBUTING.md). Corrections from the people who wrote
these tools are the most valuable contribution this repository can receive.

## License

[Apache-2.0](LICENSE). Copyright 2026 Varve Systems Ltd.

The tools evaluated here are the property of their respective authors and are
used under their own licenses; no third-party source is vendored into this
repository, save one upstream build recipe carried verbatim
(`tools/s3-fast-list/build/Dockerfile`, MIT-0). See [`NOTICE`](NOTICE) for attribution
and [`THIRD-PARTY.md`](THIRD-PARTY.md) for the per-tool license inventory, that
exception, and the copyleft posture.
