# aws-cli — independent listing report

> Groundwork report for the s3-listing-study. Derived independently from primary
> sources (official docs, the pinned source checkout, reputable third-party
> accounts, and my own smoke runs). Every behavioural claim carries an evidence
> label; an unlabelled behavioural claim is a defect.
>
> Labels: `[DOC url]` official docs · `[SRC file:line @ sha]` pinned checkout ·
> `[RUN receipt]` my smoke run · `[3P url]` third-party · `[INFERRED]` reasoning ·
> `[OBS how]` observed in a run the wrapper could not record.
>
> Pinned SHA for all `[SRC]` anchors: **`12d962d239b9fd0669951c4d27dc366388abba2d`** (tag `2.36.1`).

## 1. Metadata

| | |
| --- | --- |
| Tool | aws-cli (the AWS Command Line Interface, v2) |
| Upstream | https://github.com/aws/aws-cli — canonical AWS-owned repo `[3P github]` |
| Pinned release | tag **`2.36.1`** (latest stable at research time) |
| Commit SHA | `12d962d239b9fd0669951c4d27dc366388abba2d` |
| Language | Python (`requires-python >=3.9`) `[SRC pyproject.toml @sha]` |
| License | Apache-2.0 `[SRC LICENSE.txt @sha]` |
| Upstream health | Very active. Default branch `develop` HEAD `699c16c7` dated 2026-07-16 (day before research); ~weekly releases (2.35.19 → 2.36.1). 481 open issues, 186 open PRs, 17.1k stars `[3P github, accessed 2026-07-17]`. **Two major lines maintained in parallel**: v1 (`1.45.50`) and v2 (`2.36.1`) `[SRC git tags @sha]`. |
| Smoke image | `amazon/aws-cli:2.36.1` @ `sha256:406ca32d31e640a56e8d52921b40528cc64bfa59ec9cb4ee1456db6746cb7292` (official upstream image) |
| Reported version (in-container) | `aws-cli/2.36.1 Python/3.14.6 Linux/6.17.0-1020-gcp docker/aarch64.amzn.2023` `[RUN _build/version-help.md]` |
| Report date | 2026-07-17 |

**Note for the study.** aws-cli is not merely a subject here — the study's
**pinned harness client is also aws-cli** (`amazon/aws-cli@sha256:eb85b2c7…`,
version 2.36.0), i.e. the manifest, pre-flight, and every mismatch re-list are
produced by this same tool. My smoke image (2.36.1) is one patch newer than the
harness client (2.36.0); the two differ only in patch level and both run native
arm64 here. This co-identity is called out in Notable findings.

## 2. How it works

aws-cli has **two distinct S3 surfaces**, and they list differently:

- **`s3api`** — a thin, model-generated wrapper over the S3 API. Its
  `list-objects-v2`, `list-objects`, `list-object-versions` commands are
  generated from the botocore service model, not hand-written: `_create_command_table`
  iterates `service_model.operation_names` and builds one command each
  `[SRC awscli/clidriver.py:744-748 @sha]`, so there is no per-operation Python file.
- **`s3 ls`** — a hand-written high-level command, `class ListCommand`
  `[SRC awscli/customizations/s3/subcommands.py:782 @sha]`.

**Listing is serial pagination, in both surfaces. There is no listing
parallelism anywhere in aws-cli.**

- `s3 ls` obtains a botocore paginator and consumes it in a plain `for` loop —
  `paginator = self.client.get_paginator('list_objects_v2')` then
  `for response_data in iterator: self._display_page(...)`
  `[SRC subcommands.py:852,865 @sha]`. Recursive listing uses a second paginator
  the same way `[SRC subcommands.py:917 @sha]`. No threads are spawned around it.
- `s3api` issues one paginated call: `if client.can_paginate(...) ... paginator =
  client.get_paginator(...); response = paginator.paginate(**parameters)`
  `[SRC awscli/clidriver.py:1105-1107 @sha]` — a lazy iterator consumed serially
  by the formatter.
- **Confirmed live:** a `--debug` probe of a 3-page prefix shows 3 `ListObjectsV2`
  requests, all from a single `MainThread`, each request carrying the previous
  response's `NextContinuationToken`, timestamps strictly increasing
  `[OBS --debug, receipts/smoke/_capability/README.md]`.

The only parallelism aws-cli has is in **transfers** (`s3 cp`/`sync`, via
s3transfer / the optional CRT client `[SRC awscli/customizations/s3/factory.py]`) —
never in listing. "Parallel S3 listing" with aws-cli means **manually fanning out
N separate CLI invocations over disjoint prefixes** (smoked as the `fanout` mode
below); the caller owns the prefix segmentation and concurrency `[3P https://blog.rasc.ch/2025/07/s3-fast-list.html][INFERRED]`.

**Request-level behaviour**

- **API / request shape.** `s3 ls` always calls `ListObjectsV2`
  `[SRC subcommands.py:852,917 @sha]`. `s3api` calls whichever operation you
  named (`list-objects-v2` = ListObjectsV2, `list-objects` = the legacy V1
  ListObjects, `list-object-versions` = ListObjectVersions). Observed URL:
  `GET …/?list-type=2&prefix=…&encoding-type=url`, virtual-hosted style
  `[OBS --debug]`.
- **Keyspace division.** None built-in. Non-recursive `s3 ls` sets
  `Delimiter='/'` so S3 rolls sub-trees into `CommonPrefixes`
  `[SRC subcommands.py:853-857 @sha]`; recursive **omits** the delimiter and
  returns every key flat `[SRC subcommands.py:917-921 @sha]`. There is no
  bisection, cut-point, or automatic prefix segmentation.
- **Page size.** `--page-size` maps to `PaginationConfig.PageSize`
  `[SRC subcommands.py:857 @sha; paginate.py:182-193 @sha]`, which botocore sends
  as `MaxKeys`. The S3 server cap is 1,000 keys/page; `--page-size` is an upper
  bound, not a guarantee `[DOC https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html]`.
- **Pagination controls (s3api).** `--starting-token`
  (`PaginationConfig.StartingToken`, the resume token)
  `[SRC paginate.py:155-165 @sha]`, `--max-items` (`MaxItems`, total items then
  emit a `NextToken`) `[SRC paginate.py:195-205 @sha]`, `--page-size` as above.
  `s3 ls` exposes **only** `--page-size` — no `--starting-token`/`--max-items`
  in its `ARG_TABLE` `[SRC subcommands.py:790-805 @sha][RUN _build/help-s3_ls.txt]`.
- **Retries / backoff.** Delegated to vendored botocore's standard retry mode;
  default `max_attempts=3`, overridable via `AWS_MAX_ATTEMPTS` / config
  `max_attempts` `[SRC awscli/botocore/configprovider.py:153 @sha]`. aws-cli
  itself adds no `--max-attempts` flag.
- **Timeouts.** aws-cli owns two flags: `--cli-read-timeout` and
  `--cli-connect-timeout`, default 60s, `0` = no timeout
  `[SRC awscli/data/cli.json:61 @sha; awscli/customizations/globalargs.py:117 @sha]`.
- **Ordering.** aws-cli applies **no client-side sort** — keys are printed in
  exactly the order S3 returns them `[SRC subcommands.py:862-889 @sha]`.
  S3 general-purpose buckets return keys in UTF-8 binary (lexicographic) order
  `[DOC ListObjectsV2]` (directory/S3-Express buckets do **not** guarantee this).
  My `s3api-v2-text` output matched the byte-ordered manifest exactly, which
  confirms completeness and fields — not, by itself, ordering
  `[RUN receipts/smoke/s3api-v2-text]`.

**Memory model — this is the interesting part, and it splits by surface/format:**

- `s3 ls` **streams**: `_display_page` prints each page as it arrives and keeps
  only running counters (`_size_accumulator`, `_total_objects`), never the key
  set `[SRC subcommands.py:865-889 @sha]`. Memory is bounded regardless of bucket
  size `[INFERRED from source]`.
- `s3api` **streams** under `--output text` and `--output yaml-stream`:
  `TextFormatter` iterates `for i, page in enumerate(response)`, formatting and
  flushing each page as it arrives `[SRC awscli/formatter.py:330-355 @sha]`, and
  `StreamedYAMLFormatter` does the same `[SRC awscli/formatter.py:154 @sha]`.
  Under these, a `--query` is applied **per page** (via the paginator's
  `result_keys` `[SRC formatter.py:337-346 @sha]`), not against a full buffer.
- `s3api` **buffers** under `--output json` (the default), `--output yaml`, and
  `--output table`: all three inherit `FullyBufferedFormatter`, which calls
  `response.build_full_result()` and accumulates the entire result set in memory
  before emitting `[SRC awscli/formatter.py:65,76,94,141,197 @sha]`. (This
  corrects an earlier draft that put `--output text` in the buffered set — text
  streams.)

**Resume story.** `s3api` can resume via `--starting-token` (feed a prior
`NextToken`) `[SRC paginate.py:155-165 @sha]`. `s3 ls` has **no** resume/checkpoint —
its paginator runs to exhaustion with no exposed token `[SRC subcommands.py:790-805 @sha]`.

## 3. Modes and tunables

**Modes** (change request pattern or output contract — each smoked below):

| Mode | Command | Request pattern | Output contract | Evidence |
| --- | --- | --- | --- | --- |
| `s3api-v2-text` | `s3api list-objects-v2 … --output text` | ListObjectsV2, serial pages | text (query-projected TSV), **streamed** | `[RUN][SRC formatter.py:330]` |
| `s3api-v2-json` | `… --output json` | ListObjectsV2 | JSON, **buffered (build_full_result)** | `[RUN][SRC formatter.py:76]` |
| `s3api-v2-yamlstream` | `… --output yaml-stream` | ListObjectsV2 | YAML docs, **streamed** | `[RUN][SRC formatter.py:154]` |
| `s3api-v1-text` | `s3api list-objects …` | **ListObjects V1** (Marker pagination) | text | `[RUN][DOC list-objects]` |
| `s3api-versions-text` | `s3api list-object-versions …` | **ListObjectVersions** (KeyMarker/VersionIdMarker) | text, `Versions[]` not `Contents[]` | `[RUN][DOC]` |
| `s3-ls-recursive` | `s3 ls s3://b/ --recursive` | ListObjectsV2, no delimiter | fixed text `date time size key`, **streamed** | `[RUN][SRC subcommands.py:917]` |
| `s3-ls-delimiter` | `s3 ls s3://b/` | ListObjectsV2 + `Delimiter=/` | `PRE prefix/` rollups + keys, streamed | `[RUN][SRC subcommands.py:853]` |
| `s3api-v2-delimiter` | `s3api list-objects-v2 --delimiter /` | ListObjectsV2 + delimiter | CommonPrefixes + Contents (JSON) | `[RUN]` |
| `fanout` | N × `s3api list-objects-v2 --prefix Pᵢ` + remainder | N serial paginators, caller-parallel | union of shards | `[RUN][3P]` |

**Tunables** (change magnitude only — flag for the benchmark sweep):

| Flag | Default | Effect | Sweep? | Evidence |
| --- | --- | --- | --- | --- |
| `--page-size` | 1000 (server cap) | keys per LIST request (`MaxKeys`) | **Yes** — but capped at 1000; test whether <1000 hurts | `[SRC paginate.py:182][DOC]` |
| `--max-items` | unset (all) | total items then stop + emit NextToken | For partial-listing scenarios | `[SRC paginate.py:195]` |
| `--starting-token` | unset | resume from a prior NextToken (s3api only) | n/a (resume) | `[SRC paginate.py:155]` |
| `AWS_MAX_ATTEMPTS` | 3 | botocore retry attempts | Under fault injection | `[SRC configprovider.py:153]` |
| `--cli-read-timeout` / `--cli-connect-timeout` | 60s / 60s | HTTP timeouts (`0`=off) | Under latency injection | `[SRC globalargs.py:117]` |
| `--request-payer requester` | unset | requester-pays header | n/a here | `[SRC subcommands.py:605][RUN help]` |
| `--fetch-owner` (V2 only) | off | include Owner in results | Marginal payload size | `[RUN help-s3api_list-objects-v2.txt]` |
| `--output` | json | json/text/table/yaml/yaml-stream/off | **Yes** — memory model differs: `text`/`yaml-stream` stream, `json`/`yaml`/`table` buffer | `[SRC cli.json:23; formatter.py:76,154,330]` |

## 4. How to run it properly

**Anonymous quickstart (what smoke ran):**

```sh
# recursive full-bucket listing, machine-readable TSV
aws s3api list-objects-v2 --bucket noaa-normals-pds --region us-east-1 \
  --no-sign-request \
  --query 'Contents[].[Key,Size,ETag,LastModified,StorageClass]' --output text

# high-level, streaming, human-oriented
aws s3 ls s3://noaa-normals-pds/ --recursive --region us-east-1 --no-sign-request
```

**Anonymous / unsigned access.** `--no-sign-request` is a **global** flag that
sets botocore's `signature_version=UNSIGNED` on the session default config, so it
applies to *every* client created afterwards — both `s3` and `s3api`
`[SRC awscli/customizations/globalargs.py:99-104 @sha; awscli/data/cli.json:52 @sha]`.
Confirmed at the request level: `'auth_type': 'none'`, zero `Authorization`
headers `[OBS --debug]`. `--region` should be supplied (the bucket is
`us-east-1`); region resolution otherwise falls back to config/env like any
request `[SRC clidriver.py:1091 @sha]`.

**Best-practice configuration for large listings — per AWS's own guidance:**

- The listing itself is inherently serial at ~1,000 keys/request; AWS documents
  the pagination knobs (`--page-size`, `--max-items`) as the mechanism for large
  lists `[DOC https://docs.aws.amazon.com/cli/v1/userguide/cli-services-s3-commands.html]`.
- **For very large buckets AWS explicitly steers you off `ls` entirely**: use an
  **S3 Inventory** report (daily/weekly manifest) or S3 Batch/Athena instead of a
  live LIST, and remove expired delete markers on versioned buckets (thousands of
  delete markers can make a LIST time out) `[3P https://repost.aws/knowledge-center/s3-troubleshoot-unresponsive-list][DOC https://docs.aws.amazon.com/cli/v1/userguide/cli-services-s3-commands.html]`.
- There is **no hinted / two-pass workflow inside aws-cli**. A "faster" listing
  is achieved externally, by fanning out disjoint-prefix invocations
  (the `fanout` mode) — only useful when the keyspace splits into known prefixes
  `[3P https://blog.rasc.ch/2025/07/s3-fast-list.html]`.

**Footguns**

- `aws s3 ls s3://bucket/` (no `--recursive`) lists only the **first delimiter
  level** — folders as `PRE`, not the whole bucket `[SRC subcommands.py:853 @sha][RUN s3-ls-delimiter]`.
  New users mistake it for a full listing.
- `s3api … --output json` (the default), `--output yaml`, and `--output table`
  **buffer the entire result set in memory** via `build_full_result()`
  `[SRC formatter.py:65,76 @sha]`; on a multi-million-key bucket that is a real
  memory cost. Prefer `s3 ls`, `--output text`, or `--output yaml-stream` (all
  streaming) when memory matters.
- `s3 ls` **ignores `--output`** (its help says the global output arguments are
  ignored for this command) `[SRC subcommands.py:786-788 @sha][RUN _build/help-s3_ls.txt]`;
  it also does not honour `--query` (the high-level formatter is fixed) —
  `[INFERRED]` from the implementation, not stated in that help text. Use `s3api`
  for machine parsing.
- `--page-size` above 1000 has no effect (server caps `MaxKeys` at 1000) `[DOC]`.

## 5. Output and observability

**Formats.** s3api supports `json` (default), `text`, `table`, `yaml`,
`yaml-stream`, `off` `[SRC awscli/data/cli.json:23-33 @sha]`. `s3 ls` has a single
fixed text layout: `YYYY-MM-DD HH:MM:SS  <size>  <key>` (folders as
`PRE <prefix>/`) `[SRC subcommands.py:865-889 @sha]`.

**`normalize.sh` contract (per mode)** — emits `key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class`, `-` for unexposed fields, mtime canonicalised to `…Z`:

| Mode | key | size | etag | mtime | storage_class | Note |
| --- | --- | --- | --- | --- | --- | --- |
| `s3api-v2-text` / `-v1-text` / `-versions-text` / `-remainder` | yes | yes | yes | yes | yes | ETag un-quoted; text query TSV |
| `s3api-v2-json` | yes | yes | yes | yes | yes | jq over merged `Contents[]` |
| `s3api-v2-yamlstream` | yes | yes | yes | yes | yes | pyyaml over per-page docs |
| `s3api-v2-delimiter` | yes | yes | yes | yes | yes | CommonPrefixes → `-` rows |
| `s3-ls-recursive` | yes | yes | `-` | yes | `-` | high-level exposes only size+mtime |
| `s3-ls-delimiter` | yes | keys only | `-` | keys only | `-` | PRE rollups → `-` rows |

`s3 ls` prints time with **no offset marker**, and `_make_last_mod_str` converts
each timestamp to the process's **local** zone via `astimezone(tzlocal())`
`[SRC subcommands.py:936-952 @sha]`. Containers run `TZ=UTC`, so `tzlocal()` *is*
UTC and the printed time is UTC by construction; `normalize.sh` stamps the
explicit `Z`. (This is a footgun outside the pinned-`TZ` harness: the same
command on a non-UTC host would print local wall-clock with no marker.) All modes
verified field-clean against the manifest (§8).

**Metrics / counters / logs.** aws-cli exposes **no built-in API-call counter**
and prints no request tally in normal operation. Request shape and count are
observable only via `--debug` (full wire log to stderr) `[OBS --debug]`. `s3 ls
--summarize` adds an object-count/total-size footer `[SRC subcommands.py:964 @sha]`.
Request-shape capture at scale defers to the study's replay-server phase.

## 6. Failure surface

- **Memory growth (s3api buffered output).** `--output json`/`yaml`/`table`
  accumulate the whole result via `build_full_result()`
  `[SRC formatter.py:65,76,141,197 @sha]`; memory scales with key count. Whether
  this OOMs on a many-million-key bucket is **scale-dependent and not settled by
  smoke** — open question for the benchmark. `s3 ls`, `--output text`, and
  `--output yaml-stream` avoid it by streaming
  `[SRC formatter.py:154,330; subcommands.py:865 @sha][INFERRED]`.
- **Interruption / resume.** No checkpoint for `s3 ls`; an interrupted full
  listing restarts from scratch. `s3api` can resume with `--starting-token` if
  the caller captured the last `NextToken` `[SRC paginate.py:155 @sha]`.
- **Timeouts on pathological buckets.** Thousands of delete markers on a
  versioned bucket can make a LIST time out `[3P https://repost.aws/knowledge-center/s3-troubleshoot-unresponsive-list]`;
  `--cli-read-timeout` governs the per-request HTTP read window
  `[SRC globalargs.py:117 @sha]`.
- **Retries.** botocore standard mode, 3 attempts default, exponential backoff on
  throttling/5xx `[SRC configprovider.py:153; awscli/botocore/retries/standard.py @sha]`.
- **Truncated-response handling.** The paginator loops on `IsTruncated` /
  `NextContinuationToken`; a mid-list error surfaces as a non-zero exit after
  botocore's retries are exhausted `[INFERRED from paginator model]` — not
  exercised at smoke (all runs exit 0).

## 7. Container

**Image chosen:** the **official upstream image** `amazon/aws-cli:2.36.1`, pinned
by digest `sha256:406ca32d31e640a56e8d52921b40528cc64bfa59ec9cb4ee1456db6746cb7292`.
This is what AWS publishes and what users run, so it is the most defensible
"you didn't misconfigure it" answer. No self-authored Dockerfile is needed
(the brief's Stage B ordering: prefer the upstream image). The image entrypoint
is `/usr/local/bin/aws` `[RUN docker inspect]`, so `run.sh` argv begins at the
subcommand.

**Architecture matrix** (native support per distribution channel):

| Channel | amd64 | arm64 | Evidence |
| --- | --- | --- | --- |
| Official image `amazon/aws-cli` (Docker Hub / `public.ecr.aws/aws-cli/aws-cli`) | yes | yes | multi-arch manifest; this image runs native arm64 here `[RUN]` |
| Prebuilt installer zip (`awscli-exe-linux-{x86_64,aarch64}.zip`) | yes | yes | upstream `docker/Dockerfile` uses `awscli-exe-linux-x86_64.zip` for amd64; aarch64 zip exists `[SRC docker/Dockerfile:2 @sha]` |
| Source build (`pip`/bundled) | yes | yes | pure Python `[SRC pyproject.toml @sha]` |

Both arches are natively supported on every channel, so the benchmark's
common-architecture choice is unconstrained by aws-cli (amd64 is the expected
campaign denominator; flagged in Open questions).

**What smoke ran on:** native **arm64** (`image_arch=arm64`, `host_arch=arm64`,
`emulated=no`) on the GCP `us-east1-b` runner `[RUN run.meta]`.

## 8. Smoke results

All runs **anonymous** (`--no-sign-request`, wrapper credential-starved),
against `noaa-normals-pds` (us-east-1), manifest sha256
`c78a82737dd1982a999912afa89f870c013cb22e01e50b8c4835ddb725992adb`
(snapshot 2026-07-17). **Pre-flight**: my canonicalised full re-list is
byte-identical to the manifest (both sha256 `8b5b584e…`), so the bucket has not
drifted `[RUN _build/preflight.md]`. Every mode **exit 0**, verifier **PASS**. Durations are facts about the
run, **not** comparative numbers.

| Mode | Scope | Keys | Wall | Verdict | Receipt |
| --- | --- | --- | --- | --- | --- |
| `s3api-v2-text` | full | 148917 | 26.06s | PASS | `receipts/smoke/s3api-v2-text` |
| `s3api-v2-text` | prefix `normals-hourly/` | 2549 | 1.14s | PASS | `…/s3api-v2-text-hourly` |
| `s3api-v2-text` | prefix `normals-monthly/1991-2020/` | 15625 | 2.99s | PASS | `…/s3api-v2-text-monthly1991` |
| `s3api-v2-text` | prefix `normals-annualseasonal/1981-2010/access/` | 9839 | 2.12s | PASS | `…/s3api-v2-text-annualaccess` |
| `s3api-v2-json` | prefix `normals-hourly/` | 2549 | 1.16s | PASS | `…/s3api-v2-json-hourly` |
| `s3api-v2-yamlstream` | prefix `normals-hourly/` | 2549 | 1.32s | PASS | `…/s3api-v2-yamlstream-hourly` |
| `s3api-v1-text` | prefix `normals-hourly/` | 2549 | 2.33s | PASS | `…/s3api-v1-text-hourly` |
| `s3api-versions-text` | prefix `normals-hourly/` | 2549 | 2.20s | PASS | `…/s3api-versions-text-hourly` |
| `s3-ls-recursive` | full | 148917 | 27.04s | PASS | `…/s3-ls-recursive` |
| `s3-ls-delimiter` | delimiter `/` (root) | 5 | 0.65s | PASS | `…/s3-ls-delimiter` |
| `s3api-v2-delimiter` | delimiter `/` (root) | 5 | 0.63s | PASS | `…/s3api-v2-delimiter` |
| `fanout` (union: 4 prefix shards + remainder) | union | 148917 | ~26s agg | PASS | `…/fanout/union/union-verify.md` |

**Delimiter-root detail:** both delimiter modes returned exactly the 4 top-level
`CommonPrefixes` (`normals-annualseasonal/`, `normals-daily/`, `normals-hourly/`,
`normals-monthly/`) plus the root key `index.html` `[RUN s3-ls-delimiter]`.

**Fan-out detail:** shards `normals-monthly/` (48796), `normals-daily/` (48787),
`normals-annualseasonal/` (48784), `normals-hourly/` (2549), plus an explicit
`--remainder` (delimiter-`/` Contents-only run returning the single unprefixed
key `index.html`). Union PASS: 148917 distinct, 0 cross-shard duplicates, 0
missing/extra, root-uncovered=1 attributed to the remainder, structurally
complete `[RUN fanout/union/union-verify.md]`. Aggregate shard container time
well under the 300s/mode guardrail. Concurrency: shards were run **serially**
(each invocation's internal listing concurrency is 1); aws-cli has no internal
listing concurrency to bound, so `CONCURRENCY_CAP=4` was never approached.

**Request behaviour observed** (`[OBS --debug]`, `receipts/smoke/_capability/`):
serial `ListObjectsV2` pagination, single `MainThread`, continuation-token
chaining, `auth_type: 'none'`. Applies by construction to every s3api/s3 ls mode
(same paginator).

**Resume round-trip** (`[RUN capability]`, `receipts/smoke/_capability/resume-README.md`):
`--max-items 1000` on `normals-hourly/` returned 1000 keys + an opaque
`NextToken`; feeding it to `--starting-token` resumed and returned the remaining
1549 — union 2549 distinct, **0 cross-leg duplicates, no gap** (exactly the
manifest count). The resume *primitive* works; the token is emitted only to
stdout and nothing persists it `[SRC paginate.py:155-165 @sha]`. Capability
probe (dynamic token), not a wrapper-measured mode. Prompted by the dossier's
explicit round-trip demand — honest mixed provenance.

**Edge-case fidelity checks (unicode / weird keys / multipart ETag):
DEFERRED** — `EDGE_BUCKET=none`. `noaa-normals-pds` keys are all plain ASCII
paths, so URL-special / non-ASCII / directory-marker / multipart-ETag fidelity
was not exercised. Recorded deferred, not dropped.

## 9. Notable findings

- **The subject is also the ruler.** aws-cli is the study's pinned harness client,
  so aws-cli measures aws-cli (and every other tool). My smoke output matched the
  aws-cli-produced manifest byte-for-byte — reassuring for self-consistency, but a
  bias worth stating explicitly for the benchmark: correctness "agreement" with
  the reference is partly tautological for this one tool.
- **Memory behaviour is a *format* decision, not a *tool* decision.** The same
  `s3api list-objects-v2` is streaming under `--output text`/`yaml-stream` and
  buffered-O(N) under `--output json`/`yaml`/`table` — because only `TextFormatter`
  and `StreamedYAMLFormatter` avoid `build_full_result()`
  `[SRC formatter.py:76,154,330 @sha]`. Any memory claim about "aws-cli listing"
  is meaningless without naming the surface+format.
- **Two ways to spell the same request.** `s3 ls --recursive` and
  `s3api list-objects-v2` both issue ListObjectsV2, but differ in output contract
  (fixed text vs projectable), but **both stream page-by-page** (s3 ls and
  s3api `--output text`). Their smoke
  durations here were within a second of each other on the full bucket, as
  expected for the same underlying request stream (not reported as a comparison).
- **Legacy V1 is still a first-class subcommand.** `s3api list-objects` (V1,
  Marker pagination) exists alongside V2 and lists correctly `[RUN s3api-v1-text-hourly]`
  — useful for the benchmark if any peer tool defaults to V1.
- **`--fetch-owner` is V2-only** and off by default `[RUN help diff]`; the extra
  Owner block is a payload-size knob, not a listing-shape change.
- **`encoding-type=url` is set automatically** on the request `[OBS --debug]`, so
  botocore URL-decodes keys back before printing — relevant to the deferred
  weird-key fidelity checks.

## 10. Open questions for the benchmark phase

1. **Does `s3api --output json` OOM at scale?** The buffered `build_full_result()`
   is the headline memory risk. Sweep bucket size; compare peak RSS of
   buffered `--output json`/`yaml`/`table` vs streaming `--output text`/`yaml-stream`
   and `s3 ls`. Scale-only — not settleable at smoke. `[SRC formatter.py:76,154,330]`
2. **Serial throughput vs manual fan-out.** How does single-invocation serial
   pagination compare to a k-way prefix fan-out, and what fan-out width is
   optimal before diminishing returns / throttling? Propose k ∈ {1,2,4,8,16}
   within the campaign concurrency cap.
3. **`--page-size` sweep.** Confirm 1000 is optimal and sub-1000 only adds
   requests; test {100, 500, 1000}. (Above 1000 is a no-op — server cap.)
4. **CPU cost of buffered/JSON + downstream parsing** cross-internet vs
   in-region — the study's network-to-CPU-ratio caveat: JSON buffering + jq CPU
   may be invisible at ~10-15ms RTT and visible in-region.
5. **Architecture denominator.** aws-cli is native on both amd64 and arm64;
   confirm the campaign picks amd64 as the common denominator (no aws-cli
   constraint).
6. **Retry/timeout behaviour under fault injection** (replay-server phase):
   `AWS_MAX_ATTEMPTS`, `--cli-read-timeout` effects on a flaky endpoint.

## 11. Sources

**Official docs** (accessed 2026-07-17):
- ListObjectsV2 API — https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html
- `s3 ls` reference — https://docs.aws.amazon.com/cli/latest/reference/s3/ls.html
- `s3api list-objects-v2` reference — https://docs.aws.amazon.com/cli/latest/reference/s3api/list-objects-v2.html
- `s3api list-objects` reference — https://docs.aws.amazon.com/cli/latest/reference/s3api/list-objects.html
- High-level (s3) commands / pagination — https://docs.aws.amazon.com/cli/v1/userguide/cli-services-s3-commands.html

**Third-party** (context only, `[3P]`):
- Troubleshoot an unresponsive List command — https://repost.aws/knowledge-center/s3-troubleshoot-unresponsive-list
- Faster S3 object listing (prefix fan-out) — https://blog.rasc.ch/2025/07/s3-fast-list.html
- aws-cli repo stats — https://github.com/aws/aws-cli (481 open issues, 186 PRs, 17.1k stars)

**Pinned source.** github.com/aws/aws-cli @ `12d962d239b9fd0669951c4d27dc366388abba2d`
(tag `2.36.1`). Key anchors: `awscli/customizations/s3/subcommands.py`
(ListCommand :782, get_paginator :852, _display_page :865, recursive :917);
`awscli/clidriver.py` (:744, :1105-1107); `awscli/customizations/paginate.py`
(:155/:182/:195); `awscli/formatter.py` (:65/:76/:154);
`awscli/customizations/globalargs.py` (:99/:117); `awscli/data/cli.json`
(:23/:52/:61); `awscli/botocore/configprovider.py` (:153).

**Receipt index** (`tools/aws-cli/receipts/smoke/`): `s3api-v2-text`,
`s3api-v2-text-hourly`, `s3api-v2-text-monthly1991`, `s3api-v2-text-annualaccess`,
`s3api-v2-json-hourly`, `s3api-v2-yamlstream-hourly`, `s3api-v1-text-hourly`,
`s3api-versions-text-hourly`, `s3-ls-recursive`, `s3-ls-delimiter`,
`s3api-v2-delimiter`, `fanout/{shard-monthly,shard-daily,shard-annualseasonal,remainder,union}`;
`_build/` (version + help capture), `_capability/` (`--debug` request-behaviour probe).
