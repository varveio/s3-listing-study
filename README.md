# S3 Listing Study

A community notebook on one narrow question: **how do the various tools that
list an S3 bucket actually behave when the bucket is big?**

Listing sounds trivial until a bucket has a hundred million objects. Then it
matters whether a tool pages through the keys one request at a time, splits
the work across prefixes, or splits it across the keyspace, and whether it
keeps every key in memory while it does so. There are a lot of opinions
about this in blog posts and issue trackers, and not many runs anyone can
check. This repo is an attempt to fix that: install each tool, read how it
works, run it, and write down what happened, with enough detail that you can
check us.

> **Current release: `2026-09-scale-diagnostics`.** Ten tools, replay
> fixtures from 4 million to 143 million objects, plus single runs of one
> tool on live S3. The biggest of those: Swath 0.3.2 listed a billion objects
> in under four minutes and ten billion in under twenty, on one VM, each once. It is a set of
> findings about what each tool does at scale, not a benchmark and not a
> ranking. **[Read what we found →](RESULTS.md)**

## Who is behind this

We are the people who build [Swath](https://github.com/varveio/swath), one of
the tools in the study. We started this because we kept repeating claims
about other tools that we could not back up with a run, and that bothered us.
So, yes, we have a horse in this race, and every page says so. What we can
offer instead of neutrality is transparency: the same harness and the same
rules for every tool, every run published including the ones where Swath
looks bad or the instrument broke, and an open invitation to anyone who knows
a tool better than we do to fix our setup.

This is a side project run out of curiosity, not a sales comparison. Nobody
is being crowned. If you maintain one of these tools and we got it wrong,
[please tell us](CONTRIBUTING.md); that is the most useful contribution this
repo can get.

## What we found, in one paragraph

The interesting result is not who is fastest. It is that the tools use one
of four strategies, and each strategy runs into a different wall for a
different reason. **Serial** tools (aws-cli, minio-mc, s3kor, s5cmd on its
own) are correct and tiny in memory but page one request at a time, so 143
million objects would be about 143,000 sequential requests. **Prefix
discovery** (rclone's walk, s7cmd) fans out across directories, which is
fast on a bushy namespace and collapses on a flat one: rclone's walk filled
8 GiB and was killed, s7cmd drained the whole bucket on one thread.
**Speculative range splitting** (s3p, ps3) invents keyspace boundaries and
lists the ranges in parallel, without needing directories, at the cost of
probe requests and guesses that can land badly. **Supplied partitions**
(s3-fast-list with hints, s5cmd with a shard list) parallelise well once
someone tells them where to cut, and cannot help on an unfamiliar bucket.
Swath is range splitting that keeps re-splitting while it runs. All of that,
with the runs behind each sentence, is on the [findings page](RESULTS.md).

## What you can and cannot take from it

- **Take:** which tool completed which size, what it returned, how much
  memory it used, what happened at the largest size we tried, and where the
  study chose not to go further.
- **Do not take:** a speed ranking. Most runs went through a replay server
  that imitates S3 with a fixed latency, that server had a defect that
  happens to slow our own tool, the setups were not equal everywhere, and
  each cell is a single run. The release's `manifest.json` says all of this
  in machine-readable form.
- **Also do not take:** a promise of reproducibility. You can audit every
  run (its command, versions, image digests, outcome), but the fixture bytes
  and the raw logs are not published, so you cannot rerun the identical
  experiment from this repo alone.

## How to read this repo

| You want | Go to |
| --- | --- |
| The short version | [`RESULTS.md`](RESULTS.md) |
| Every number and attempt id | [the release report](results/2026-09-scale-diagnostics/REPORT.md) |
| The raw rows, one JSON object per run | [`results/2026-09-scale-diagnostics/`](results/2026-09-scale-diagnostics/) and the [release contract](results/README.md) |
| How one tool lists, and how it did here | [`tools/`](tools/README.md), one page per tool |
| How the replay instrument works and where it is wrong | [`docs/instrument.md`](docs/instrument.md) |
| The plan we wrote before running anything | [`docs/methodology.md`](docs/methodology.md) |
| The harness itself | [`benchmark/`](benchmark/README.md) |

Every tool page has the same shape: what the tool is, how its listing works
under the hood (with links into its source), what we saw in the current
release, what we saw in the earlier groundwork runs, and what is still open.

## How we work

- **Read first, run second.** Documentation, then source when the docs are
  silent, then a real run. A claim from source stays labelled as a claim
  from source until a run confirms it.
- **The plan came before the runs.** [`docs/methodology.md`](docs/methodology.md)
  was written before the comparative runs, and later changes are dated and
  explained.
- **Every run is a record.** Version, image digest, exact command, machine,
  fixture, exit code, row count, wall clock, peak memory. Failed and
  cancelled runs stay in the data.
- **Swath gets no special treatment.** Same harness, same limits, same
  publishing rules. When the instrument broke, it broke against Swath, and
  the numbers are published as they are.
- **We try to find a good setup for every tool**, not just defaults. Where a
  tool needs help the harness gives it (shards for s5cmd, cut-points for
  s3-fast-list), and that help is disclosed wherever the result appears.
- **Maintainers get the benefit of the doubt.** If something surprises us we
  ask before we write it up, and reproducible problems get filed upstream.

## Running the checks

Everything here is Python. The tests use committed synthetic fixtures and
need no bucket, no credentials and no network.

```sh
uv sync            # or: python3 -m venv .venv && .venv/bin/pip install -e .
uv run pytest      # the whole suite
uv run python -m benchmark.public_validate --release-dir results/2026-09-scale-diagnostics
```

The last line checks a published release from the committed files alone:
checksums, structure, and that no row is labelled a measurement without
earning it.

## Contributing

Corrections beat additions. If a tool page describes your tool wrongly, if
we ran it in a silly configuration, or if a claim on a page does not match
the row it cites, open an issue or a pull request.
[`CONTRIBUTING.md`](CONTRIBUTING.md) says how to tell us we got your tool
wrong, what helps us reproduce a result, and the few rules we keep.

## License

[Apache-2.0](LICENSE). Copyright 2026 Varve Systems Ltd.

The tools evaluated here belong to their respective authors and are used
under their own licenses; no third-party source is vendored, save one
upstream build recipe carried verbatim
(`tools/s3-fast-list/build/Dockerfile`, MIT-0). See [`NOTICE`](NOTICE) and
[`THIRD-PARTY.md`](THIRD-PARTY.md).
