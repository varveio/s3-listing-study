# Reader C — CLI surface, modes & tunables, auth, resume

**Subject:** `swath` @ `cef8ec2` (tag v0.2.0), `SOURCE_ROOT=/home/vscode/.s3-listing-study/sources/swath`, worktree verified clean at the pinned SHA.

**Authority note:** the three files under `swath-cli/src/test/resources/help/` are exact `CommandLine.getUsageMessage(Ansi.OFF)` captures, asserted byte-equal by `HelpUsageGoldenTest` and regenerable only under `-Dswath.goldens.update=true` [SRC swath-cli/src/test/java/io/varve/swath/cli/HelpUsageGoldenTest.java:33-46 @ cef8ec2]. They are therefore an authoritative statement of the CLI surface at this SHA, not documentation prose.

---

## 1. Command surface

| Command | Visible | Notes |
|---|---|---|
| `swath list <s3-uri>` | yes | The only listing verb [SRC swath-cli/src/test/resources/help/swath.txt:11] |
| `swath resume <dir>` | yes | Resume by output-directory run handle [SRC .../swath.txt:12] |
| `swath help [cmd]` | yes | picocli `HelpCommand` [SRC swath-cli/src/main/java/io/varve/swath/cli/App.java:38] |
| `swath dump-run <file.pageseg>` | **hidden** | Read-only page-run staging-segment inspector; `hidden = true`, absent from root help [SRC swath-cli/src/main/java/io/varve/swath/cli/DumpRunCommand.java:31-32] |
| `swath completion` | **hidden** | picocli `GenerateCompletion`, explicitly hidden [SRC App.java:141-143] |

No default command: a bare `swath s3://bucket` is exit 2 with a `did you mean: swath list …` hint [SRC App.java:156-171].

**No config-file surface exists.** Exhaustive grep for `.swathrc` / `swath.toml` / `swath.yaml` / "config file" across `docs/` and `swath-cli/src/main` returns nothing. Configuration is flags + AWS-SDK env + `SWATH_OTLP_*` + `-D` JVM properties only [SRC swath-cli/src/main/java/io/varve/swath/cli/ListOptionGroups.java:115; DOC docs/configuration.md:22-31,168-196].

---

## 2. THE MODE / TUNABLE INVENTORY

Applying the BRIEF definition verbatim: a **mode** changes the *request pattern or the output contract*; a **tunable** changes only *magnitude*.

### 2a. Modes (smoke every one)

| # | Mode | How selected | Why it is a mode | Evidence |
|---|---|---|---|---|
| M1 | `table` | `--format table`, or `auto` on a TTY | Distinct output contract: aligned human view, no file extension, TTY-only | [SRC OutputOptions.java:47-48,115-118]; [SRC swath-core/.../output/OutputFormat.java:9-18] |
| M2 | `tsv` | `--format tsv` / `-o x.tsv` / `auto` off-TTY | Distinct output encoding | same |
| M3 | `jsonl` | `--format jsonl` / `-o x.jsonl` | Distinct output encoding | same |
| M4 | `parquet` **FILE-kind** | `-o out.parquet`, or `--output-type file` | Collapses to **one** writer; requires `--checkpoint none`; **non-resumable**; still physically writes a one-writer dataset dir | [SRC OutputOptions.java:417-427]; [SRC ListCommand.java:1554-1580]; [DOC docs/usage.md:128-140] |
| M5 | `parquet` **DIRECTORY-dataset** | `-o out/` (no recognised extension), or `--output-type dir` | Multi-writer pool (2–4), `data/*.parquet` + `manifest.json` + `_SUCCESS` + `symlink.txt` + `.swath-state.json`; the **only resumable** regime | [SRC OutputOptions.java:283-296,461-468]; [DOC docs/usage.md:170-190] |
| M6 | **`--sort`** | `--sort` (parquet directory only) | Different output contract: globally key-sorted, **range-disjoint** parts, `manifest.json` gains `sorted`/`sortKey`/`minKey`/`maxKey`. Also a different *run shape*: a LISTING→MERGING→PUBLISHED state machine whose merge phase issues **zero** LIST calls | [SRC ListOptionGroups.java:32-36]; [SRC ListCommand.java:1516-1534, 1326-1370]; [SRC swath-core/.../checkpoint/SortPhase.java:247-251] |
| M7 | **`swath resume <dir>`** | subcommand | Different entry point and request pattern: reloads existing nodes and continues each from `cursor`/`durable_cursor`; **never** runs the seed step | [SRC ResumeCommand.java:119-194]; [SRC swath-core/.../engine/SeedStep.java:149-151]; [SRC swath-core/.../checkpoint/Node.java:170-176] |
| M8 | **`--tune seed.mode=shallow` (default)** | `--tune seed.mode=shallow` | Issues a bounded up-front **`delimiter=/`** structure-probe pass to tile the root range | [SRC TuneOptions.java:25-26,161-168]; [SRC EngineOptions.java:49-58]; [SRC SeedStep.java:159] |
| M9 | **`--tune seed.mode=none`** | `--tune seed.mode=none` | **Genuinely different request pattern**: one root range `(⊥, null]`, zero delimiter probes, stealing only. Registry class `IDENTITY` — refused on resume | [SRC TuneOptions.java:25-26,78-81]; [SRC SeedStep.java:155-158]; [SRC swath-core/.../engine/SeedMode.java:12-18] |
| M10 | `--tune seed.mode=hints` | — | **Declared but unreachable in practice.** CLI validation accepts it; it throws `InvalidConfigException` (exit 2) at seed time — *after* the checkpoint DB is opened and the S3 client built | [SRC EngineOptions.java:54]; [SRC SeedStep.java:160-161] |

> **M8/M9 is a finding the prior pathfinder appears to have missed.** It is the only user-reachable *supported* control over whether swath issues `delimiter=/` requests. It is not a delimiter/shallow *output* mode — output is a full recursive listing either way — but it changes the request pattern, which is exactly what the BRIEF's definition catches.

**Diagnostic mode-shaped surface (`--engine-toggle`) — explicitly unsupported.** Fourteen toggles reach the engine. Several change the request pattern outright (`structure_probes=off` removes `delimiter=/` probing entirely; `readahead=on` adds speculative fetches). By the letter of the definition these are modes; by the project's own words they are not configurations. The namespace's javadoc states it plainly:

> "**EXPERIMENTAL / DIAGNOSTIC — not a supported configuration.** `DEFAULT` is the only supported configuration, with one documented exception: `rate_anchored_sensing=off` together with `tail_floor=current` is the supported rollback to pre-0.2.0 engine behaviour"
> [SRC swath-core/src/main/java/io/varve/swath/engine/EngineToggles.java:22-27]

Full toggle set: ten ablations default-`on` (`owner_split`, `density_ewma`, `radix_bands`, `structure_probes`, `far_ahead`, `alphabet_pivots`, `reflect`, `confetti_feedback`, `reflect_lift`, `fanout_tiling`) [SRC EngineToggles.java:197-199]; `readahead` opt-in default-**off** [SRC :201-202]; `mass_aware_seed` opt-out default-**on** [SRC :204-209]; `rate_anchored_sensing` opt-out default-**on** since 0.2.0 [SRC :211-220]; `tail_floor` — the only **value-taking** toggle, `current|est_direct|reach_floored`, default `reach_floored` [SRC :222-231]. Unknown name / bad value / contradictory repeat = exit 2, validated before any I/O [SRC EngineToggles.java:257-296; ListCommand.java:312].

### 2b. Tunables (magnitude only — sweep candidates)

| Flag | Default | Declared in | Effect | Resume class |
|---|---|---|---|---|
| `--concurrency N` | `64` (`S3Config.DEFAULT_MAX_PARALLEL`) | `ConnectionOptions:78-80` | AIMD ceiling `Tmax`; must be `[1, 100000]` | FREE |
| `--object-listing-queue-size N` | `50000` | `ConnectionOptions:82-84` | In-flight **entry** budget (not batch slots); `>= 1` | FREE |
| `--request-rate N` | unset / `0` = uncapped | `ConnectionOptions:86-89` | Aggregate client-side req/s cap; `NaN`/`Inf`/negative → exit 2 | FREE |
| `--parquet-part-size SIZE` | `256mb` (`ROW_GROUP_BYTES*4`) | `OutputOptions:336-337` | Part rotation target | FREE |
| `--part-rotation-interval DUR` | `30s`; floor `100ms`; `0/none/off` disables | `OutputOptions:339-342,407` | Part rotation by age | FREE |
| `--part-rotation-max-rows N` | `2000000`; `0` disables | `OutputOptions:344-347` | Part rotation by rows | FREE |
| `--progress-interval DUR` | `1s` redraw / `30s` appended; floor `1s`, **rejected not clamped** | `ListOptionGroups:39-46`, `LivenessOptions:36,51-58` | Progress cadence; implies `--progress` | FREE |
| `--idle-timeout DUR` | `120s`; `0/none/off` disables | `ListOptionGroups:55-60` | Watchdog stall window | FREE |
| `--no-progress-timeout DUR` | `10m`; `0/none/off` disables | `ListOptionGroups:62-67` | Zero-real-progress backstop | FREE |
| `--max-duration DUR` | unset | `ListOptionGroups:48-53` | Timebox → exit 124, resumable | FREE |
| `--bearer-token-refresh-interval` | `45m` | `BearerTokenOptions:54-58` | Token re-mint cadence | FREE |
| `--tune parquet.writers=N` | `3`, range `2..4` | `TuneOptions:27-28` | Writer-pool size; FILE-kind forces 1 | FREE |
| `--tune summary.interval=DUR` | `--progress-interval`, else `30s` | `TuneOptions:29-31` | Sidecar flush cadence | FREE |
| `--tune sort.ignore-disk-check=on\|off` | `off` | `TuneOptions:32-33` | Skips `--sort` disk guard | FREE, **the only resume-applicable tune key** |
| `--tune engine.readahead=on\|off` | `off` | `TuneOptions:23-24` | Alias that appends `readahead=<v>` to `--engine-toggle` | FREE |

Note the aliasing: `--tune engine.readahead` writes straight into `engine.engineToggle` [SRC TuneOptions.java:151-160] — the "supported" tune registry has one member that is really a diagnostic toggle in disguise.

### 2c. Neither — connection / filters / observability

`--region`, `--profile`, `--no-sign-request`, `--endpoint-url`, `--force-path-style`, `--bearer-token-command`, `--requester-pays`, `--fetch-owner`; filters `--include/--exclude/--min-size/--max-size/--modified-since/--modified-until/--storage-class`; observability `--report`, `--trace`, `--metrics-endpoint`, `--no-metrics`, `--color`, `-v`, `-q`, `--stats`, `--progress`; lifecycle `--checkpoint`, `--restart`, `--force`/`--overwrite`.

Two borderline calls I want on the record:

- **`--fetch-owner` is request-shape, not a mode.** It sets `FetchOwner=true` on every `ListObjectsV2` and populates `owner_id`/`owner_display_name` [SRC ConnectionOptions.java:61-63; DOC docs/usage.md:450]. Neither field is in the study's `normalize.sh` contract (`key/size/etag/mtime/storage_class`), so the *normalized* output contract is unchanged. Recommend: one representative run, not a full mode.
- **Filters are post-listing.** "Filters apply after listing; they do not reduce API calls" [DOC docs/usage.md:620]. They change the row set, not the request pattern or encoding. Registry class `IDENTITY` — changing one refuses a resume [SRC FilterOptions.java:28-55; ResumeRegistry.java:57-63].

---

## 3. The three absence questions — settled with positive evidence

**Q: Is there a `--max-keys` / page-size flag?** **No — and it is a hard-coded constant, not merely an absent flag.**

```java
int pageMax = 1000;   // S3 page cap
```
[SRC swath-cli/src/main/java/io/varve/swath/cli/ListCommand.java:395]

`pageMax` is a local, threaded into `runWithCheckpoint` → `listSpec`/`parquetSpec` → `RangeScanner`. Nothing on any `@Option`-annotated field feeds it. The only `--max-keys` `@Option` in the whole repo belongs to a different binary: `swath-replay-server`'s `BenchCommand` [SRC swath-replay-server/src/main/java/io/varve/swath/replay/server/BenchCommand.java:47]. Page size is therefore **not sweepable for the benchmark phase** without patching source.

**Q: Is `--no-owner-split` reachable from the command line?** **No — the field exists, carries no `@Option`, and the CLI actively rejects the spelling.**

```java
boolean noOwnerSplit;   // line 27 — no @Option, no @Resume
```
[SRC swath-cli/src/main/java/io/varve/swath/cli/EngineOptions.java:27]

It is only ever read as the second argument of `EngineToggles.parse(engineToggle, noOwnerSplit)` [SRC EngineOptions.java:46], i.e. a permanently-`false` programmatic seam. The removal is asserted by a dedicated test:

```java
void removedOwnerSplitAliasIsRejected() {
    ListCommand cmd = new ListCommand();
    assertThatThrownBy(() -> new CommandLine(cmd).parseArgs(
            "s3://bucket/prefix", "--no-owner-split"))
            .isInstanceOf(CommandLine.UnmatchedArgumentException.class);
}
```
[SRC swath-cli/src/test/java/io/varve/swath/cli/EngineToggleCliValidationTest.java:67-72]

The supported spelling is `--engine-toggle owner_split=off`. Note that `EngineToggles.parse` still emits `"--no-owner-split conflicts with --engine-toggle owner_split=on"` [SRC EngineToggles.java:294-295] — an **unreachable error message**, and its javadoc still calls `--no-owner-split` "the pre-existing kill-switch" [SRC EngineToggles.java:254-255]. Stale javadoc, live code correct.

**Q: Is there a `--delimiter` or `--recursive` flag?** **No.** Exhaustive `@Option`-string grep across all Java in the repo finds `"--delimiter"` only in `swath-replay-server`'s `BenchCommand` [SRC swath-replay-server/.../BenchCommand.java:53]; `"--recursive"` appears nowhere at all. `swath list` is unconditionally recursive: `ListCommand` hard-wires `ListingMode.OBJECTS` into the `RunKey` [SRC ListCommand.java:640], and `ListRunner` hard-wires it into every `EngineContext` [SRC swath-core/.../runtime/ListRunner.java:391,500,622]. Delimiter requests exist only *internally* — the shallow seed pass and the thief's structure probes [SRC ListCommand.java:1149; SRC EngineToggles.java:37-38] — and are reachable only through `--tune seed.mode` (M8/M9) or the diagnostic `structure_probes` toggle.

**Bonus absence — versioned listing.** `ListingMode.VERSIONS` exists in the model [SRC swath-model/src/main/java/io/varve/swath/model/ListingMode.java:9-12], the SQLite `run_meta.mode` CHECK constraint admits `'VERSIONS'` [SRC swath-core/.../checkpoint/CheckpointSchema.java:195], `S3PageFetcher` branches on it [SRC swath-s3/.../S3PageFetcher.java:161], and `ListCommand` even reads `run.mode() == ListingMode.VERSIONS` to pick a `SortMode` [SRC ListCommand.java:1336]. But **nothing can ever set it**: the sole `RunKey` construction passes the literal `ListingMode.OBJECTS` [SRC ListCommand.java:640] and no flag exists. `--all-versions` appears nowhere in any `@Option`. The whole VERSIONS path is dead code reachable only from a hand-crafted checkpoint DB. Docs concur: "Versioned listing (`ListObjectVersions`) is not implemented" [DOC docs/operating.md:30-31].

**Bonus absence — removed metrics flags,** same shape, same positive evidence:
```java
for (String removed : new String[]{"--metrics-export", "--metrics-interval"}) { … UnmatchedArgumentException … }
```
[SRC swath-cli/src/test/java/io/varve/swath/cli/MetricsExportCliValidationTest.java:62-69]

**Other no-`@Option` fields** (same "exists in code, unreachable from CLI" shape, for anyone auditing the surface): `EngineOptions.seed` (set only via `--tune seed.mode`) [SRC EngineOptions.java:25]; `MetricsOptions.metricsInterval` [SRC MetricsOptions.java:18]; `OutputOptions.rawOutput` ("stored run-context field retained for resume reconstruction; new runs always escape text") [SRC OutputOptions.java:89-90]; `OutputOptions.noSummaryJson` ("internal suppression seam") [SRC OutputOptions.java:380]; `OutputOptions.parquetWriters` (only via `--tune parquet.writers`) [SRC OutputOptions.java:92]; `CheckpointOptions.resume` (set only by `ResumeCommand`) [SRC CheckpointOptions.java:21].

---

## 4. Anonymous / unsigned access

**Mechanism.** `--no-sign-request` → `AnonymousCredentialsProvider`, resolved at the top of a three-branch precedence chain:

```java
private AwsCredentialsProvider resolveCredentials() {
    if (noSignRequest)   return AnonymousCredentialsProvider.create();
    if (profile != null) return ProfileCredentialsProvider.create(profile);
    return DefaultCredentialsProvider.builder().build();
}
```
[SRC swath-cli/src/main/java/io/varve/swath/cli/ConnectionOptions.java:248-256]

So precedence is **`--no-sign-request` > `--profile` > SDK default chain**. Resume class `STICKY` — soft-restored from the checkpoint unless re-passed [SRC ConnectionOptions.java:45-46; ResumeRegistry.java:100-102].

A fourth, orthogonal path sits *above* all three: `--bearer-token-command` replaces SigV4 signing entirely via `ProcessBearerTokenSupplier` [SRC ConnectionOptions.java:211-223]. Docs: "`--profile`/`--no-sign-request`/`AWS_ACCESS_KEY_ID` are ignored for signing when it's set" [DOC docs/usage.md:~490].

**The region trap is real — verified, and it has two distinct halves.**

*Half 1 — no region resolves ⇒ exit 2, even anonymously.* `--no-sign-request` has **no effect on region resolution**; the two are independent code paths:

```java
private Region resolveRegion(URI endpoint) throws InvalidConfigException {
    if (region != null)  return Region.of(region);
    if (endpoint != null) return Region.US_EAST_1;
    try { return new DefaultAwsRegionProviderChain().getRegion(); }
    catch (RuntimeException e) {
        throw new InvalidConfigException(
            "no AWS region: pass --region or set AWS_REGION / AWS_DEFAULT_REGION", e);
    }
}
```
[SRC ConnectionOptions.java:233-246]

**Prerequisite for a credential-starved container run:** the invocation must supply `--region REGION` **or** `AWS_REGION`/`AWS_DEFAULT_REGION` **or** `--endpoint-url` (which short-circuits to `us-east-1`). A container with no AWS config, no env, and only `--no-sign-request` fails at exit 2 before the first request. Note the quickstart in `install.md` shows exactly that invocation with no region [DOC docs/install.md:15-18, 70] — it works only because the reader's shell happens to carry `AWS_REGION`. **This is the single most likely reason a first smoke run fails.**

*Half 2 — wrong region ⇒ fatal exit 1, no auto-retry.* A `301 PermanentRedirect` becomes a typed `RegionRedirectException extends ListingException` (exit 1). It is deliberately **never retried and never self-corrected**: "Rebuilding the `S3Client` and retrying automatically is deliberately out of scope … the fetcher's client is long-lived and shared across every worker and thief thread" [SRC swath-core/src/main/java/io/varve/swath/error/RegionRedirectException.java:8-27]. The message carries `x-amz-bucket-region` so the operator knows what to re-pass [SRC S3FaultClassifier.java:219-225].

---

## 5. `resume` semantics

**Handle.** The output *directory* is the run handle; `swath resume <dir>` opens `<dir>/.swath/checkpoint.sqlite` directly and refuses an arbitrary SQLite path [SRC ResumeCommand.java:36-45,131-144,265-278; CheckpointOptions.java:37-40].

**`--checkpoint auto|none|PATH` resolves to:**

| Value | stdout | FILE-kind | DIRECTORY-dataset |
|---|---|---|---|
| `auto` (default) | `null` → ephemeral in-memory store | **refused, exit 2** | `<dir>/.swath/checkpoint.sqlite` (durable, resumable) |
| `none` | ephemeral | ephemeral (the *only* accepted mode) | ephemeral |
| explicit `PATH` | **refused, exit 2** (orphan checkpoint) | **refused, exit 2** | that SQLite file verbatim |

[SRC CheckpointOptions.java:67-77 (`CheckpointMode.resolve`); ListCommand.java:1554-1580 (`checkpointRefusalForEphemeralSink`)]. `--checkpoint none` runs the *identical* work-stealing engine — only durability differs [SRC EngineOptions.java:60-65; DOC docs/usage.md:142-146]. The checkpoint is **deleted on clean completion**, so `swath resume <completed-dir>` prints "already complete" and exits 0 [SRC ResumeCommand.java:136-140].

**What the checkpoint stores** — SQLite, `PRAGMA user_version = 1`, exact-match or refuse (no migration path) [SRC swath-core/src/main/java/io/varve/swath/checkpoint/CheckpointSchema.java:35,55-62,105-121]. Three tables [SRC :190-214]:

- `run_meta` — `store_scheme, endpoint, bucket, prefix(BLOB), args_hash, strategy, filter_spec, output_format, mode CHECK IN ('OBJECTS','VERSIONS'), started_at, finished_at, status CHECK IN ('RUNNING','COMPLETED','FAILED')`, plus additively-migrated context columns `no_sign_request, profile, region, fetch_owner, raw_output, output_path, sort_enabled, sort_phase, fatal_error, request_payer, destination_kind, output_type, identity_spec` [SRC :133-168].
- `listing_node` — per-range `range_start, range_end, cursor, durable_cursor, opaque_token, key_marker, version_id_marker, inventory_uri, status(PENDING/IN_PROGRESS/COMPLETED), generation, owner_lease, pages_emitted, api_calls, unsplittable`. `cursor` = last **emitted** key (text sinks, at-most-once); `durable_cursor` = highest key inside a **finalized** part (file sinks, exactly-once) [SRC :198-207; Node.java:170-176].
- `part_file` — `writer_id, path, format, finalized, rows, bytes` [SRC :209-213].

**The STICKY / IDENTITY / FREE classification** is a first-class, drift-tested construct: `@Resume(ResumeClass)` sits on the *same* program element as `@Option` "so the annotation can never name a wrong option", and `ResumeRegistryDriftTest` asserts every visible option carries one [SRC swath-cli/src/main/java/io/varve/swath/cli/Resume.java:13-31; ResumeClass.java:8-20].

| Class | Meaning | Members |
|---|---|---|
| **IDENTITY** | Changes *what is listed* or *where it is written* → **refused** when it differs from the checkpoint | `--endpoint-url`, `--format`†, `-o/--output`†, `--output-type`†, `--fetch-owner`†, `--sort`†, all seven filters (collapsed to one `filter_spec` column), `--tune seed.mode`, plus the four `args_hash` fields (scheme/endpoint/bucket/prefix) and the derived `raw_output`/`destination_kind` [SRC ResumeRegistry.java:65-109; TuneOptions.java:26] |
| **STICKY** | Auth/region/billing — **soft-restored** from the checkpoint unless explicitly re-passed (CLI wins) | `--no-sign-request`, `--profile`, `--region`, `--requester-pays` [SRC ResumeRegistry.java:99-109; ListCommand.java:1868-1882] |
| **FREE** | May change freely on a resume | everything else: `--concurrency`, queue size, rate, all Parquet knobs, all liveness knobs, `--checkpoint`, `--restart`, `--force`, `--trace`, `--engine-toggle`, `--tune`, `--color`, `-v`, `-q`, `--stats`, `--progress`, **and both bearer-token flags** |

† `@Resume(value = IDENTITY, restored = true)` — a fourth-class distinction: restored on a bare resume (that is how a bare resume learns *where* and *how* to write) **and** refused when re-passed differently [SRC Resume.java:18-23].

Enforcement is a `LengthPrefixedFields`-encoded `identity_spec` string persisted at creation and recomputed post-restore; the refusal **names the changed column** rather than guessing [SRC ResumeRegistry.java:130-182; ListCommand.java:724-729,780-787].

**What a resume refuses to do** (each writes an early-exit summary first, so a consumer always finds one) — in the code's own settled order [SRC ListCommand.java:649-815]:

1. **Recorded FILE-kind text destination** (`.tsv`/`.jsonl`) — non-resumable; "changing the resume invocation's `-o` cannot recover the already-committed prefix" [SRC :655-658, 1916-1930].
2. **Malformed / absent `output_format`** in the checkpoint [SRC :1884-1908].
3. **Resolved FILE-kind output** — defensive backstop for hand-made/foreign checkpoints; COMPLETED can precede a failed publication, so it is refused regardless of status [SRC :748-755].
4. **`status == FAILED && fatal_error`** — a deterministic in-process failure would just re-fail; deliberately **narrower** than `status == FAILED`, because the broken-pipe path marks FAILED *without* the flag so a truncated stdout run stays resumable (INT-12) [SRC :768-776; RunMeta.java:88-99].
5. **Any changed IDENTITY column** — names the column(s), steers to `--restart` [SRC :780-787].
6. **Stale `--sort` staging format** — a checkpoint whose finalized segments are not `page-run` [SRC :801-815].
7. `swath resume` + `--restart` and `swath resume` + `--overwrite` are mutually exclusive; `swath resume` + `--checkpoint none` is an error [SRC :288-305].
8. `--sort` + non-parquet effective format, re-checked *after* the format restore [SRC :823-826].

**What a resume accepts.** Its whole flag surface is `--tune` (resume-safe keys only — a run-shape `IDENTITY` key gets "is a run-shape setting and cannot be changed by swath resume" [SRC TuneOptions.java:76-84]), `--stats`, `--progress`, `--bearer-token-command`, `--bearer-token-refresh-interval`, `--color`, `-q`, `-v`, `-h`, `-V` [SRC swath-cli/src/test/resources/help/swath-resume.txt:1-33].

**The bearer-token exception is deliberate and well-argued.** Both bearer flags are `FREE` and never persisted, for two reasons: "*A stored command is a stored secret*" (`--bearer-token-command 'echo eyJhbGci…'` would put a literal token at rest in `run_meta`) and "*A stored command is executed later*" (a checkpoint would decide what a subsequent `swath resume` runs; "a checkpoint is data, not a trusted script") [SRC swath-cli/src/main/java/io/varve/swath/cli/BearerTokenOptions.java:15-33]. Consequence: a resumed run against a bearer-auth endpoint **must re-pass** the command, and passing only the refresh interval is rejected at exit 2 rather than silently falling back to SigV4 [SRC ConnectionOptions.java:211-218].

---

## 6. Exit codes

| Code | Constant | Meaning | Source |
|---|---|---|---|
| **0** | `SUCCESS` | Success, empty result, already-complete resume, **or a broken pipe** (stdout closed by e.g. `head`) — a *clean* exit, not an error | [SRC ExitCodes.java:24,95-97,135-137] |
| **1** | `UNEXPECTED` | Unrecoverable/unclassified: `ListingException` (incl. `AccessDeniedException`, `RegionRedirectException`, `ProtocolViolationException`), `OutputException`, `CheckpointException`, or any unmapped throwable | [SRC ExitCodes.java:25,100; error/{Listing,Output,Checkpoint}Exception.java] |
| **2** | `USAGE` | Bad args, invalid URI, invalid config, **or a deliberate guarded refusal** (unfinished/foreign output dir, extension/format mismatch, a resume whose identity changed, a directory bucket) | [SRC ExitCodes.java:27-34; error/{InvalidArgs,InvalidUri,InvalidConfig,UnsupportedBucket}Exception.java] |
| **75** | `STUCK` | `EX_TEMPFAIL` — a cooperative `stop_reason=stuck` unwound to a **resumable partial**. Three sources: liveness-watchdog escalation, transient-retry cap exhaustion, bare seed interrupt; `error_class=` is one of `stuck_api_timeouts`/`stuck_throttle`/`stuck_unknown`. Deliberately not 130 and not 1. The watchdog's `Runtime.halt` backstop uses it too | [SRC ExitCodes.java:36-49; ListCommand.java:541-556] |
| **124** | `TIMEBOX` | `--max-duration` elapsed, graceful stop, resumable partial. GNU `timeout(1)` convention; `stop_reason=max_duration` | [SRC ExitCodes.java:51-58; ListCommand.java:536-540] |
| **130** | `SIGINT` | SIGINT / Ctrl-C (128+2). Also the fallback for a source-less signal cancel. Resumable partial | [SRC ExitCodes.java:60-65; CancelledException.java:19-21] |
| **143** | `SIGTERM` | SIGTERM (128+15) — kept distinct from 130 so a supervisor stop is distinguishable from an interactive Ctrl-C. Resumable partial | [SRC ExitCodes.java:67-73; ListCommand.java:557-568] |

**Two subtleties the published tables do not surface.**

1. **`ProtocolViolationException` outranks every cancellation code.** `forThrowable` searches the throwable, its cause chain, *and* suppressed exceptions before anything else, because a violation discovered during unwind is attached as *suppressed* while the cancel stays primary — and every cancellation code claims "resumable partial, come back to it", which a protocol-violated run is not [SRC ExitCodes.java:82-89,103-128; ListCommand.java:529-535]. It resolves to exit 1 (`ProtocolViolationException extends ListingException`) [SRC error/ProtocolViolationException.java:24].
2. **The hierarchy is `sealed`**, making the error→exit mapping compiler-checked for exhaustiveness [SRC error/SwathException.java:13-16].

The `faq.md` and `usage.md` tables agree with the source on all seven codes [DOC docs/faq.md:32-42; DOC docs/usage.md:783-795].

---

## 7. Docs-vs-source drift (real findings)

Ordered by how likely each is to mislead someone running the tool.

**D1 — `--all-versions` is documented as a named flag; it does not exist.**
> *Docs:* "Versioned listing (`--all-versions`) is **planned but not built in v1.0**." [DOC docs/usage.md:751]
> *Source:* no `@Option` named `--all-versions` exists anywhere. Repeated in `docs/internals/contracts.md:89,95,834,981`, `docs/internals/architecture.md:335`, `ROADMAP.md:11`, and in live javadoc at `swath-core/.../sort/SortMode.java:11` and `.../sort/PageBlock.java:50`, and `.../runtime/ArgsHashFields.java:11` (the last three describe `args_hash` as covering "recursive flag, `--all-versions`" — **two** flags that do not exist).

**D2 — `--max-keys` described as a run-configurable page size.**
> *Docs:* "a (never-recommended) `--max-keys=1` run misclassifies every ordinary worker page fetch as `pivot_probe`" [DOC docs/metrics-and-observability.md:34]
> *Source:* `int pageMax = 1000;` is a hard-coded local [SRC ListCommand.java:395]. `--max-keys` exists only on the separate `swath-replay-server` `BenchCommand` [SRC swath-replay-server/.../BenchCommand.java:47]. The caveat describes a run that cannot be invoked.

**D3 — `--prefix` described as a user-passed flag.**
> *Docs:* "a bound it copies verbatim still can — a `--prefix` **you** pass that ends in a lone `%`" [DOC docs/operating.md:96]
> *Source:* there is no `--prefix` flag; the prefix is the path component of the positional `<s3-uri>` [SRC S3Uri.java:23-46; ListCommand.java:130-132]. The *behaviour* described is correct (a user-supplied prefix ending in `%` can trip a nonconformant endpoint); only the spelling is wrong.

**D4 — `--seed` and `--hints` named as flags in live javadoc and in an error message users can actually see.**
> *Javadoc:* "`HINTS` — cut-points from a `--hints` file (not yet wired)" and "algorithms.md §8, `--seed`" [SRC swath-core/.../engine/SeedMode.java:10,17]
> *Runtime error string:* `"--seed hints requires a --hints cut-points file, which is not yet implemented"` [SRC SeedStep.java:160-161]
> *Source:* the surface is `--tune seed.mode=…` [SRC TuneOptions.java:25-26]; neither `--seed` nor `--hints` is a flag. A user who hits this error is told to pass two nonexistent flags.

**D5 — `--no-owner-split` still described as a live kill-switch in javadoc.**
> *Javadoc:* "the `--no-owner-split` alias"; "`--no-owner-split` (the pre-existing kill-switch), folded in as `owner_split=off`" [SRC EngineToggles.java:246,254-255], plus the unreachable error string at `:294-295`.
> *Source:* rejected with `UnmatchedArgumentException` [SRC EngineToggleCliValidationTest.java:67-72].

**D6 — `--metrics-interval` referenced in live javadoc.**
> *Javadoc:* "`--metrics-endpoint`/`--metrics-interval` inputs" [SRC swath-core/.../runtime/RunContext.java:63]; "@param metricsInterval the `--metrics-interval` value" [SRC .../observability/MeterRegistries.java:83]
> *Source:* removed, asserted rejected [SRC MetricsExportCliValidationTest.java:62-69]. Cadence is env-only (`SWATH_OTLP_INTERVAL`).

**D7 — `--single-file` referenced as a thing that was replaced.** [SRC OutputOptions.java:412-413] "A `-o path.parquet` single-file destination -- replacing the old `--single-file` flag". Historical, harmless, but confirms the flag surface has churned.

**D8 — `install.md` shows credential-free quickstarts with no region.** [DOC docs/install.md:15-18,70,82,139] Every anonymous example omits `--region` and any `AWS_REGION`. Against the region resolution at [SRC ConnectionOptions.java:240-245] these fail at exit 2 in a clean container. The `faq.md` entry documents the failure correctly [DOC docs/faq.md:3-14] — but the quickstart a new user actually copies does not mention it. **This is the highest-value drift for the smoke phase.**

**D9 — `install.md` says "No release has been cut yet"** [DOC docs/install.md:8] while the pinned rev *is* tag `v0.2.0`. Stale relative to its own tag; matters for whether an upstream image is expected to exist.

**Non-drift worth noting as accurate:** `docs/configuration.md` and `docs/usage.md`'s flag/default tables match the golden help and the source field defaults on every entry I checked — the reference tables are in good shape; the drift is concentrated in *prose* and *javadoc*.

---

## 8. Notes for the benchmark phase

- **Nothing sweeps page size.** Sweepable magnitude knobs are `--concurrency` (default 64, ceiling 100000), `--object-listing-queue-size` (50000), `--request-rate`, `--parquet-part-size`, `--part-rotation-*`, `--tune parquet.writers` (2..4 only).
- **Modes to smoke:** M1–M9 (nine), i.e. five output/destination modes, `--sort`, `resume`, and both supported `seed.mode` values. M10 (`hints`) is a *documented capability gap* worth one probe receipt showing the exit-2 failure — note it fails *after* opening the checkpoint DB and building the S3 client, so it is not a pure-validation refusal.
- **Concurrency cap compliance:** `--concurrency 1` is accepted (`>= 1`) [SRC ConnectionOptions.java:119-125], so bringing a run inside a `CONCURRENCY_CAP` is trivially possible — no blocked-and-recorded mode on that axis.
- **API-call count is exposed:** `cost.api_calls` in the JSON sidecar and the `list_run_summary` log line [DOC docs/operating.md:154-168]. `--report PATH` forces the sidecar for non-Parquet runs [SRC OutputOptions.java:548-567]. Existence noted only; no numbers transcribed.
- **`--trace PATH` gives per-event request shape** (seeds, claims, page commits, steals, splits) and works under `--checkpoint none` [SRC EngineOptions.java:60-71]. Caution: it "carries real key names on nearly every event" [DOC docs/usage.md:614-618] — redaction-relevant.
- **Container footgun:** the image runs as UID 10001, so a host output dir needs `--user "$(id -u):$(id -g)"`; and off a TTY progress is *appended* every 30 s, so a short run looks silent [DOC docs/install.md:128-149].
