# Reader D — Output contracts, adapter design, observability, memory model

**Subject:** `swath` @ `cef8ec2` (v0.2.0), SOURCE_ROOT = `/home/vscode/.s3-listing-study/sources/swath`. Harness read from `/home/vscode/.s3-listing-study/sources/swath-work/harness/`.

Claim labels: `[SRC path:line @ cef8ec2]` = read in code; `[DOC path]` = project doc (design intent, not verified against code unless also anchored); `[HARNESS path:line]` = the study's own harness; `[INFERRED]` = my derivation, with its basis stated.

---

## 0. Headline findings (the things that change the plan)

1. **Only three of swath's four formats are capturable by this harness at all.** The harness never bind-mounts anything — `DOCKER_CMD` contains no `-v`/`--mount` `[HARNESS smoke-run.sh:326-345]` — and evidence is collected exclusively via `docker logs` into `stdout.raw`/`stderr.raw` `[HARNESS smoke-run.sh:571]`. Parquet is a **path-based sink** that refuses stdout outright (`"Parquet output requires -o <dir>"`) `[SRC swath-cli/.../OutputOptions.java:460-463 @ cef8ec2]`, and `Formatters.text()` throws for `PARQUET` `[SRC swath-core/.../output/Formatters.java:134-142 @ cef8ec2]`. **`--format parquet` is uncapturable without a harness change.** Same for any `-o <file>` text run. Verifiable modes are `--format tsv|jsonl|table` **to stdout only**.
2. **stdout is clean.** The only writer of fd 1 in the whole main source tree is the output sink `[SRC swath-cli/.../OutputOptions.java:488 @ cef8ec2]`. Logging is pinned to Logback's `System.err` console appender `[SRC swath-cli/.../CliLogging.java:17 @ cef8ec2]`, progress renders to fd 2 `[SRC swath-cli/.../ProgressDisplay.java:20-33 @ cef8ec2]`, the `--stats` block and the resolved-output echo go to `System.err` `[SRC swath-cli/.../ListCommand.java:280,387 @ cef8ec2]`, and `--report` only ever writes to a `Path` `[SRC swath-core/.../JsonRunSummaryWriter.java:972-987 @ cef8ec2]`. **No banner/summary/progress contamination of the data stream.** A normalizer needs no separation logic.
3. **TSV's `\xHH` escaping is not invertible and must not be un-escaped.** `ControlCharEscaper` escapes `<0x20` and `0x7f` as `\xHH` but **does not escape the backslash itself** `[SRC swath-core/.../output/ControlCharEscaper.java:21-49 @ cef8ec2]`. A key literally containing the four characters `\x09` is byte-identical on the wire to a key containing a real TAB. The correct adapter behaviour is to **detect and refuse**, not to decode. Escaping is also **not bypassable at this revision**: `--raw-output` has no `@Option` (the field is a resume-restore artifact only — *"new runs always escape text"*) `[SRC swath-cli/.../OutputOptions.java:89-90 @ cef8ec2]`, and the CLI hardwires `escape = !output.rawOutput` `[SRC swath-cli/.../ListCommand.java:1456 @ cef8ec2]`.
4. **JSONL is the only faithful text format, and even it is lossy for non-UTF-8 keys.** All three text formatters render the key via `KeyBytes.asString()`, which is `new String(raw, UTF_8)` — invalid bytes become **U+FFFD**, irreversibly `[SRC swath-model/.../KeyBytes.java:65-72 @ cef8ec2]`, confirmed in a comment as the reason lone surrogates can never reach the writer `[SRC swath-core/.../output/CountingWriter.java:31-34 @ cef8ec2]`. Only the Parquet `key` column is byte-exact (`rawUnsafe()`) `[SRC swath-core/.../output/parquet/ListEntryWriteSupport.java:44 @ cef8ec2]`. **Recommend `--format jsonl` as the primary verified mode.**
5. **`cost.api_calls` is trustworthy and has exactly one increment site**, `[SRC swath-s3/.../S3PageFetcher.java:213-214 @ cef8ec2]` — see §5. But `--report` writes to a container-local path, so for the harness the capturable route is `-v`'s `list_run_summary … api_calls=N` line on **stderr** `[SRC swath-core/.../runtime/ListRunner.java:1357 @ cef8ec2]`.
6. **`--sort` is irrelevant to this study's capturable modes**: it is refused for any text format (exit 2) `[SRC swath-cli/.../ListCommand.java:824,1519 @ cef8ec2]` and additionally requires a checkpoint `[SRC …:307]` and a directory dataset `[SRC …:1531]`.

---

## 1. The contract each adapter must satisfy

From the verifier itself, not from prose:

- Invocation: `normalize.sh <mode> <prefix>` with the tool's captured payload on **stdin**, 5-field TSV on **stdout** `[HARNESS verify-listing.sh:785-787]`. `$2` is the run's prefix from `run.meta`, supplied so path-relative tools can rebuild full keys — **swath emits absolute keys, so `$2` is unused**.
- Every row must have exactly 5 tab fields or the verifier dies `[HARNESS verify-listing.sh:791-798]`.
- `key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class`, `-` for anything a mode does not expose; a field is asserted **only where non-`-`** `[HARNESS verify-listing.sh:38-40, 843-847]`.
- **mtime shape is validated before any comparison** against `^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(Z|\+00:00|\+0000)$` — **no fractional seconds permitted**. A violation is an *adapter-contract violation*, killing the run — neither PASS nor FAIL `[HARNESS verify-listing.sh:51, 65-70]`.
- etag compared **case-insensitively**, unquoted `[HARNESS verify-listing.sh:86]`. swath already stores ETags with quotes stripped `[SRC swath-model/.../ObjectEntry.java:13 @ cef8ec2]`, so no stripping is needed — but strip defensively.
- size compared as a **string** `[HARNESS verify-listing.sh:85]` — do not reformat.
- `LC_ALL=C` is exported by the verifier `[HARNESS verify-listing.sh:24]`; adapters should re-export it so `cut -c` is byte-oriented and `awk` does not re-interpret bytes.
- Capture caps: 64 MiB per stream, truncation blocks a completeness verdict `[HARNESS smoke-run.sh:609-625]`. At ~148,917 keys `[HARNESS README.md:16]` a TSV/JSONL run is far under this.

---

## 2. Per-format output specification (verified against source)

### 2.1 Format selection

`--format auto|table|tsv|jsonl|parquet` `[SRC swath-cli/.../OutputOptions.java:47 @ cef8ec2]`. Default `auto` = **`TABLE` on a TTY, `TSV` otherwise** `[SRC swath-core/.../output/OutputFormat.java:159-161 @ cef8ec2]`. The harness `docker create`s without `-t`, so an unset `--format` yields **TSV**. Pin it explicitly anyway.

`-o` default is stdout; `-` is stdout; a `.tsv`/`.jsonl`/`.parquet` extension makes it FILE-kind; anything else is a directory dataset `[SRC swath-cli/.../OutputOptions.java:80-82 @ cef8ec2]`.

### 2.2 `tsv`

**Header line, always** — `OutputStage` calls `formatter.writeHeader()` unconditionally before the drain loop `[SRC swath-core/.../output/OutputStage.java:69 @ cef8ec2]`, and TSV's header is a literal constant:

```
key	size	last_modified	etag	storage_class	row_type
```
`[SRC swath-core/.../output/TsvFormatter.java:23 @ cef8ec2]` — pinned by test `[SRC swath-core/src/test/.../output/FormatterTest.java:78 @ cef8ec2]`.

**Six columns, and the order is NOT the harness order** — `last_modified` sits *before* `etag`:

| # | column | ObjectEntry | DeleteMarkerEntry | CommonPrefixEntry |
|---|---|---|---|---|
| 1 | `key` | escaped key | escaped key | escaped key |
| 2 | `size` | decimal long | *empty* | *empty* |
| 3 | `last_modified` | ISO instant | ISO instant | *empty* |
| 4 | `etag` | escaped, unquoted | *empty* | *empty* |
| 5 | `storage_class` | escaped | *empty* | *empty* |
| 6 | `row_type` | `OBJECT` | `DELETE_MARKER` | `COMMON_PREFIX` |

`[SRC swath-core/.../output/TsvFormatter.java:40-55 @ cef8ec2]`, `[SRC swath-model/.../RowType.java:108-114 @ cef8ec2]`. In v1.0 only `ObjectEntry` is ever emitted `[DOC docs/internals/contracts.md §1.2 "In practice v1.0 emits ObjectEntry rows only"]`, so every row is 6 fully-populated fields with `row_type=OBJECT`.

**Escaping** is applied to `key`, `etag`, `storage_class` via `text()` `[SRC …TsvFormatter.java:42,46,47,57-59]` and is on by default and un-disableable (§0.3). Escaped set: every char `< 0x20` (includes TAB `\x09`, LF `\x0a`, ESC `\x1b`) plus DEL `\x7f`, rendered lowercase `\xHH` `[SRC …ControlCharEscaper.java:21-23,43 @ cef8ec2]` + `[SRC swath-core/.../output/Hex.java:61 @ cef8ec2]`. Clean strings return by identity — **no other transformation whatsoever**.

**Timestamp.** `Fields.isoMicros(long epochMicros)`: `0` → **empty string** (sentinel for missing); otherwise `DateTimeFormatter.ISO_INSTANT` over an `Instant` built from `floorDiv/floorMod` micros `[SRC swath-core/.../output/Fields.java:18-26 @ cef8ec2]`.

> **Correction to the reported detail.** "ISO with microsecond precision" is not what ships. `ISO_INSTANT` emits **variable** precision — 0, 3, 6 or 9 fractional digits as needed. Because `S3PageFetcher.toEpochMicros` is `getEpochSecond()*1e6 + getNano()/1000` `[SRC swath-s3/.../S3PageFetcher.java:405-409 @ cef8ec2]` and S3's `LastModified` is second-granularity, the practical output is `YYYY-MM-DDTHH:MM:SSZ` — **already exactly contract v2**. But a sub-second-capable endpoint would produce `…SS.ssssssZ`, which the verifier rejects outright as an adapter violation. **The normalizer must strip a fractional part unconditionally.** [INFERRED from the two anchors above.]

Also note the sentinel collision: epoch `0` (1970-01-01T00:00:00Z) is indistinguishable from "missing" `[SRC …Fields.java:19-21]`.

### 2.3 `jsonl`

**No header** `[SRC swath-core/.../output/JsonlFormatter.java:31-34 @ cef8ec2]`. **The summary is deliberately kept off the data stream so `jq -s length` equals the row count** `[SRC …JsonlFormatter.java:16-21 @ cef8ec2]` — confirms the reported detail.

One JSON object per line. Field order for an `ObjectEntry`: `key`, `size`, `last_modified`, `etag`, `storage_class`, `version_id`, `is_latest` (only when `version_id != null`), `owner_id`, `owner_display_name`, `checksum_algorithm`, `checksum_type`, `row_type` `[SRC …JsonlFormatter.java:37-68 @ cef8ec2]`.

**Nullable fields are OMITTED, not emitted as `null`** — `optStr` returns early on null `[SRC …JsonlFormatter.java:91-95]`, and `time()` omits `last_modified` entirely when `isoMicros` returned empty `[SRC …:84-89]`. A positional/regex parser is therefore wrong; **key on field names.**

**Escaping is JSON, and is invertible** — `Json.quote` escapes `"`, `\`, `\n`, `\r`, `\t`, `\b`, `\f`, and `<0x20` as `\u00HH` `[SRC swath-core/.../output/Json.java:85-107 @ cef8ec2]`. Critically, `\` is itself escaped, so unlike TSV there is no ambiguity. Explicitly documented as unaffected by `--raw-output` `[SRC …JsonlFormatter.java:16-19]`, and tested: an embedded newline stays on one line and appears as `\n` `[SRC …FormatterTest.java:57-66 @ cef8ec2]`.

Timestamp: same `Fields.isoMicros` `[SRC …JsonlFormatter.java:85]` — same variable-precision caveat.

### 2.4 `table` (`AlignedFormatter`)

**No header** `[SRC swath-core/.../output/AlignedFormatter.java:135-138 @ cef8ec2]`. **Carries only size, time and key — no etag, no storage_class.** Layout is fixed-width, key last:

```
pad(size, 14, right-justified) + "  " + pad(time, 24, left-justified) + "  " + escape(key) + "\n"
```
`[SRC …AlignedFormatter.java:123-124, 141-166 @ cef8ec2]`.

⇒ byte offsets (ASCII-only prefix ⇒ chars == bytes): size `[1..14]`, spaces `[15..16]`, time `[17..40]`, spaces `[41..42]`, key `[43..]`. [INFERRED from the widths + concatenation order above.]

Sentinels: `DeleteMarkerEntry` size = `-`; `CommonPrefixEntry` size = `PRE`, time = empty `[SRC …:150-157]`.

**Alignment is not guaranteed.** `pad()` appends the value unpadded when `gap <= 0` `[SRC …:168-179]`. A size ≥ 15 digits, or a timestamp with 6 fractional digits (`2026-01-01T00:00:00.123456Z` = 27 chars > `TIME_WIDTH` 24), silently shifts every later column. The adapter must **assert the separator spaces and die otherwise** rather than mis-slice. [INFERRED from `pad()`.]

Key is escaped with the same non-invertible `\xHH` scheme `[SRC …:163]`.

### 2.5 `parquet` — see §3.

---

## 3. Parquet dataset layout, and why the harness cannot capture it

### 3.1 Capturability — say it plainly

**A `--format parquet` run produces no bytes on stdout and leaves its dataset inside the container's writable layer, which the harness never mounts, copies, or archives.** `docker create` args carry no volume `[HARNESS smoke-run.sh:326-345]`; evidence is `docker logs` only `[HARNESS smoke-run.sh:571]`; `run.meta` records only `stdout_path`/`stderr_path` `[HARNESS smoke-run.sh:718-722]`. Stdout Parquet is refused by swath with exit 2 `[SRC swath-cli/.../OutputOptions.java:460-463 @ cef8ec2]`.

**Consequence:** Parquet cannot be verified under the current harness. It would need either (a) a harness change adding a bind mount plus a post-run archive step into the receipt dir, or (b) an out-of-harness run whose dataset is normalized separately and fed in via `--input`. Recommend recording Parquet as *"not verified — mode is directory-output; harness captures stdout only"* rather than leaving it looking untested.

### 3.2 Layout (for whoever does add it)

Dataset root = the `-o` directory `[SRC swath-core/.../output/parquet/DatasetLayout.java:32-69 @ cef8ec2]`:

| Path | Role |
|---|---|
| `data/` | **pure Parquet only** — no markers, no manifest, so `data/*` is a safe glob |
| `data/part-w{writer}-{NNNNN}.parquet` | unsorted parts, `%05d` seq — `String.format("part-w%d-%05d.parquet", id, seq++)` `[SRC .../ParquetWriterPool.java:515 @ cef8ec2]` |
| `data/part-{NNNNN}.parquet` | `--sort` finals, no `w`-infix, lexical name order == key order `[DOC docs/internals/contracts.md §4.1]` |
| `data/part-00000.parquet` | the single-sink `ParquetFormatter` path `[SRC .../ParquetFormatter.java:283 @ cef8ec2]` |
| `manifest.json` | consumer manifest, S3-Inventory shape + `sorted`/`sortKey`; `files[]` = `{key:"data/<part>", size, MD5checksum, rowCount, minKey?, maxKey?}` `[SRC .../Manifest.java:30-46, 121-140 @ cef8ec2]` |
| `.swath-state.json` | **internal** resume identity (`args_hash`, `run_id`) — never consumer-facing `[SRC .../Manifest.java:39-40, 101-110]` |
| `_SUCCESS` | empty; written **LAST**; the publish commit point `[SRC .../ParquetFormatter.java:310-313 @ cef8ec2]`, `[DOC contracts.md §6]` |
| `symlink.txt` | newline-delimited `data/<part>` paths for Hive/Athena/Trino `[SRC .../Manifest.java:44]` |
| `_staging/` | `--sort` only; visible (not dot-hidden) `[SRC swath-cli/.../ListCommand.java:122 @ cef8ec2]` |
| `_swath_summary.json` | the default `--report` sidecar for any non-stdout Parquet destination `[DOC docs/metrics-and-observability.md §3]` |

**Schema** (canonical superset, one `MessageType` for every writer) `[SRC .../ParquetSchema.java:26-42 @ cef8ec2]`: `key` BINARY **required** (raw bytes), `size` INT64 opt, `last_modified` INT64 `TIMESTAMP(MICROS, UTC)` opt, `etag`/`storage_class`/`version_id` BINARY(UTF8) opt, `is_latest` BOOL opt, `is_delete_marker` BOOL **required**, `owner_id`/`owner_display_name`/`checksum_algorithm`/`checksum_type` BINARY(UTF8) opt, `row_type` BINARY(UTF8) **required**. Writer settings pinned: block 64 MB, page 1 MB, dictionary on, ZSTD-3 `[DOC contracts.md §7]`.

**Read-back sketch** (if capture is ever solved). `duckdb -csv -c "SELECT ... FROM read_parquet('<root>/data/*.parquet')"` — glob is safe because `data/` is pure. `key` is BINARY, so it must be emitted as raw bytes, not hex/base64; `last_modified` is TIMESTAMP(MICROS,UTC) so format as `strftime(..., '%Y-%m-%dT%H:%M:%SZ')` to drop micros. Guard on `_SUCCESS` existing before trusting the dataset. [INFERRED from the schema + layout anchors; not executed.]

---

## 4. Memory model

### 4.1 Output is streaming, not accumulate-then-dump

`OutputStage.consume` receives one `PageBatch` at a time and writes each entry straight through the formatter, holding no per-run collection `[SRC swath-core/.../output/OutputStage.java:97-113 @ cef8ec2]`. Each formatter reuses one `StringBuilder(256)` and emits one line per `write` `[SRC …TsvFormatter.java:27,41-54; …JsonlFormatter.java:25,38-67; …AlignedFormatter.java:128,142-165 @ cef8ec2]`. `AlignedFormatter`'s javadoc is explicit: *"Streams per row with fixed column widths — no full-result buffering, so it holds at any scale."* `[SRC …AlignedFormatter.java:116-119 @ cef8ec2]`. The only per-run state is `RowTally`'s four longs `[SRC swath-core/.../output/RowTally.java:141-144 @ cef8ec2]`.

### 4.2 `--object-listing-queue-size` is an ENTRY budget — confirmed

`Channel` is bounded by a **weight** budget, not a slot count. Each `Item`'s weight is the batch's entry count; `End`/`Failure` weigh `0` and therefore never block `[SRC swath-core/.../pipeline/Channel.java:18-27, 63-68, 85-96 @ cef8ec2]`.

What it means in practice:
- Admission test is `inFlight < capacity`, evaluated **before** adding the whole item `[SRC …Channel.java:85, 91]`. A page is admitted whole even if it overshoots, so in-flight can reach `capacity - 1 + pageSize`, i.e. **transient overshoot of up to one S3 page (≤1000 entries)** — matching `[DOC contracts.md §7]`.
- A single page larger than `capacity` is still admitted on an empty channel, so the pipeline cannot deadlock `[SRC …Channel.java:23-27]`.
- Default 50,000 entries; documented budget ≈ `cap × (max_key_len + ~200 B/entry) × #queues` `[DOC contracts.md §7]`.
- The backing `LinkedBlockingQueue` is **unbounded**; the weight gate is the only bound `[SRC …Channel.java:36, 95]`. With exactly one producer per channel `[DOC contracts.md §1.3]` this is sound, but it means a bug in the weigher would silently remove the bound. [INFERRED.]

### 4.3 What bounds memory, and where it does not

Bounded: active row/page/writer/merge buffers are functions of configured knobs, never object count — invariant I11 `[DOC contracts.md §0]`. PERF gate ceilings: **< 256 MB** for stdout/TSV/JSONL/table, **< 1 GB** for Parquet, the latter measured only at 100,000 keys `[DOC contracts.md §7.2]`. *(Existence noted; numbers not adopted as findings.)*

**Grows with N (explicitly outside I11):**
1. **Parquet finalized-part metadata is `O(parts)`, and the full `files[]` list is re-serialized on every finalize ⇒ cumulative `O(parts²)` work** `[DOC contracts.md §4.1]`.
2. **`--sort` retained staging metadata is `O(segments)`** `[DOC contracts.md §7.2]`.
3. Neither applies to the capturable text/stdout modes.

Bounded-by-design elsewhere: the trajectory rollup uses a ring-doubling bin merge for constant memory regardless of run length `[SRC swath-core/.../observability/RunSummary.java:171-178 @ cef8ec2]`; the split tree and cursors are SQLite-resident `[DOC contracts.md §7.2]`; latency series are capped at 3 call classes × 5 phases `[SRC swath-core/.../observability/RunMetrics.java:309 @ cef8ec2]`.

### 4.4 What `--sort` does — it spills, it does not buffer the run

`--sort` is an **external merge sort**, and it is **rejected for every text format** (exit 2) `[SRC swath-cli/.../ListCommand.java:824, 1519 @ cef8ec2]`; it also requires a checkpoint `[SRC …:307]` and a directory dataset `[SRC …:1531]`.

Mechanism `[SRC swath-core/.../sort/SortLane.java:23-55 @ cef8ec2]`, `[DOC contracts.md §6]`:
- Pages fill an in-memory `SortBuffer`; a gate fires on `segment-bytes` or `segment-entries` (`SealTrigger.BYTE_GATE`/`ENTRY_CAP`; `DRAIN` at end-of-listing) `[SRC swath-core/.../sort/SealTrigger.java:8-16 @ cef8ec2]`.
- The sealed buffer is handed to **one** off-thread encoder and flushed as an internally-sorted **`.pageseg`** page-run segment.
- **Spill location: `<output-dir>/_staging/`** — visible, same filesystem as the final output `[SRC swath-cli/.../ListCommand.java:122, 1332 @ cef8ec2]`, `[SRC swath-core/.../sort/CaptureSorter.java:84 @ cef8ec2]`.
- **At most `swath.sort.buffers` (default 2) live buffers**, enforced by a `Semaphore(buffers-1)` released only after encoding completes; over-run blocks `admit()` (measured backpressure) `[SRC …SortLane.java:35-52 @ cef8ec2]`.
- `segment-bytes` is heap-adaptive: ≈8 % of `Runtime.maxMemory()`, floored at 64 MB `[SRC swath-core/.../sort/SortConfig.java:79, 109, 117-131 @ cef8ec2]`, `[DOC contracts.md §7]`.
- Merge phase holds `effectiveFanIn = min(fan-in, max(2, merge-budget-bytes / merge-per-stream-bytes))` open streams at ~64 KiB each (`DEFAULT_MERGE_PER_STREAM_BYTES = 64 KiB`) `[SRC …SortConfig.java:86 @ cef8ec2]`, `[DOC contracts.md §7]` — so merge peak memory is a function of the budget knob, not the segment count.
- **Disk, not heap, is the real `--sort` risk**: peak staging disk is empirically ~2× the final compressed output; a startup pre-check plus a periodic runtime guard (which calls `Runtime.halt`) enforce it, bypassable with `--force-sort` `[SRC swath-core/.../sort/SortDiskGuard.java:22-58 @ cef8ec2]`.
  - **Correction (review):** there is no `--force-sort` flag. `SortDiskGuard` names it in javadoc (`:52,71,119`) *and* in the live runtime exhaustion message (`:186-192`, flag name on `:190`), but no such `@Option` exists; the real escape hatch is `--tune sort.ignore-disk-check=on` `[SRC swath-cli/.../TuneOptions.java:32-33 @ cef8ec2]`. The sentence above is left as written because this file is a derivation record; the corrected form is carried in the report as drift item **D15**.

---

## 5. `--report` JSON, and `cost.api_calls`

### 5.1 Where it goes

`--report PATH` `[SRC swath-cli/.../OutputOptions.java:350-352 @ cef8ec2]`. Written **atomically** (`<path>.tmp` → `ATOMIC_MOVE` rename, with a same-directory fallback) `[SRC swath-core/.../observability/JsonRunSummaryWriter.java:972-987 @ cef8ec2]`. **Never to stdout** — the doc states the rule (*"stdout stays data, the JSON never goes there"*) and the code has no stdout path `[DOC docs/metrics-and-observability.md §3]` + `[SRC …JsonRunSummaryWriter.java:107, 972 @ cef8ec2]`. Default sidecar `<output>/_swath_summary.json` exists only for non-stdout **Parquet** destinations; a stdout/FILE-kind text run writes nothing unless `--report` is passed `[SRC swath-cli/.../OutputOptions.java:549-560 @ cef8ec2]`, `[DOC metrics-and-observability.md §3]`.

**Harness consequence:** a `--report` path inside the container is as uncapturable as a Parquet dataset. Use `-v` and scrape stderr instead (§5.4).

### 5.2 Contents at this revision

Top level: `schema_version` (2), `run_id`, `args_hash`, `argv`, `strategy`, `completed`, `exit_code`, `stop_reason`, `stop_source`, `error_class`, `started_at`, `as_of`, `duration_ms`, `session_duration_ms`, `objects`. Blocks: `config`, `engine_flags`, `output`, `cost`, `efficiency`, `engine`, `seed` (+`decisions[]`), `trajectory`, `slow_ranges[]`, `probe_latency[]`, `client_cost[]`, `demand_gate`, `shape`, `meters[]`, plus a `sort` block on `--sort` runs `[DOC docs/metrics-and-observability.md §3]`. The record shape is mirrored by `RunSummary` `[SRC swath-core/.../observability/RunSummary.java:52-89 @ cef8ec2]`.

Consumer warnings worth carrying into a study record:
- Written **periodically** (`--tune summary.interval`, default `--progress-interval`) with `completed:false, exit_code:null`, plus a final write at close `[DOC §3]`. Read `completed`, not file existence.
- `meters[]` is **not guaranteed present** — a degraded terminal write drops it `[DOC §3]`.
- `schema_version` bumps only on breaking change; added fields do not bump it `[DOC §3]`.
- **The report contains real key material** — `argv`, `config.target`, `config.filters` verbatim, `seed.decisions[].prefix`, `slow_ranges[].lo/hi/cursor` `[DOC §7 "Sensitivity"]`. Treat as sensitive alongside the listing itself.

### 5.3 `cost.api_calls` — what it counts, and is it trustworthy

`costNode.put("api_calls", summary.apiCalls())` `[SRC swath-core/.../observability/JsonRunSummaryWriter.java:588 @ cef8ec2]`; the value is `Math.round(counterTotal("swath.api.calls"))` — the **sum across every `strategy` tag series** `[SRC swath-core/.../observability/RunMetrics.java:2169 @ cef8ec2]`.

There is **exactly one increment site in the entire main source tree**: `metrics.recordApiCall()` immediately *before* `s3.listObjectsV2(...)` `[SRC swath-s3/.../S3PageFetcher.java:213-214 @ cef8ec2]`; `recordApiCall` bumps the tagged counter and a plain tally `[SRC .../RunMetrics.java:771-776 @ cef8ec2]`.

**Verdict: yes, trustworthy as an API-call count for run records**, with these precise semantics:
- It counts **attempts issued, not successes** (incremented before the call, so timeouts, 503s and connection failures all count).
- It counts **every request class**: worker page fetches, seed probes, thief 1-key pivot probes, and `delimiter=/` structure probes — all route through this one fetcher. Confirmed by the doc's own wording, *"actual S3 list attempts (incl. engine throttle-retries) — the real HTTP-request count"* `[DOC metrics-and-observability.md §1]`.
- **One increment ≈ one HTTP request**, because SDK-internal retry is disabled (`S3Config.DEFAULT_MAX_ATTEMPTS = 1`) precisely so swath's own loop is the sole retrier `[DOC contracts.md §7]`. Each swath-level retry re-enters `fetchPage` and increments again. [INFERRED from that doc + the single call site.]
- **Nothing else is missed**: a repo-wide scan of the S3 module found only `s3.listObjectsV2(...)` and the local `s3.serviceClientConfiguration()` — no `HeadBucket`, no `GetBucketLocation`, no `ListBuckets`. Region redirect is detected from a *failing* `ListObjectsV2` response `[SRC swath-s3/.../S3FaultClassifier.java:219-225 @ cef8ec2]`, so it is counted, not hidden.
- **Use `cost.api_calls`, never a single `meters[]` row.** If the strategy is unknown at the first call, the counter fragments into `strategy="unknown"` and the real strategy; each series undercounts, the summed field does not `[DOC metrics-and-observability.md §1]` + `[SRC .../RunMetrics.java:773 @ cef8ec2]`.
- **On a resume it counts only this process's calls** `[DOC metrics-and-observability.md §2]`.
- `cost_usd = api_calls × 0.005 / 1000`, a hardcoded `us-east-1` reference rate, and the figure is withheld under `--endpoint-url` `[SRC .../RunMetrics.java:2654-2656 @ cef8ec2]`.

### 5.4 Getting the count without a writable path

`-v` emits `list_run_summary run_id=… api_calls=… cost_usd=…` on **stderr** from the same `RunSummary` `[SRC swath-core/.../runtime/ListRunner.java:1357 @ cef8ec2]`. The `--stats` human block also renders it, but formatted with thousands separators (`%,d`) `[SRC swath-cli/.../SummaryRenderer.java:205-206 @ cef8ec2]` + `[SRC swath-cli/.../OperatorText.java:32-34 @ cef8ec2]` — the `-v` log line is the cleaner scrape target.

---

## 6. Observability surfaces — stream by stream

| Surface | Flag | Stream | Notes |
|---|---|---|---|
| Listing data | `--format` | **stdout (fd 1)** | raw `FileOutputStream(FileDescriptor.out)` so broken pipe throws and exits 0 `[SRC OutputOptions.java:488 @ cef8ec2]`, `[SRC output/BrokenPipe.java:216-219 @ cef8ec2]` |
| End-of-run block | `--stats`/`--no-stats` | **stderr** | option description says stderr `[SRC OutputOptions.java:361-365 @ cef8ec2]`; writer resolves to `System.err` `[SRC ListCommand.java:280 @ cef8ec2]`. Auto-on for runs > 1.5 s / that produced output / that stopped short; silent on broken pipe |
| Live progress | `--progress`/`--no-progress`, `--progress-interval` | **stderr** | `[SRC OutputOptions.java:375-378 @ cef8ec2]`; redraws in place only when fd 2 is a terminal, otherwise plain newline-terminated records — *"a redirected stderr must never receive a control sequence"* `[SRC ProgressDisplay.java:26-33 @ cef8ec2]`. Carries no key text by design `[SRC ProgressDisplay.java:48-52]` |
| Structured `progress` log record | `-v` (or non-tty stderr) | **stderr** | one record per tick, phase-shaped `[SRC observability/LoggingProgressSink.java:32 @ cef8ec2]`, `[DOC metrics-and-observability.md §4]` |
| All logging | `-v`/`-q` | **stderr** | Logback console appender pinned to `System.err`; only that appender is re-pointed `[SRC CliLogging.java:16-17, 46-66 @ cef8ec2]` |
| `--report` JSON | `--report PATH` | **file only** | §5.1 |
| `--trace` | `--trace PATH` | **file only** | JSONL flight recorder, stream-append (not atomic-rename) so a crash leaves a readable prefix; **the final line may be torn** — parse line-by-line and drop a trailing fragment `[SRC observability/JsonlTraceSink.java:22-37 @ cef8ec2]`. Contains `lo`/`cursor`/`hi`/`pivot` — real key names `[DOC metrics-and-observability.md §7]` |
| `--metrics-endpoint URL` | also `SWATH_OTLP_ENDPOINT` | **network (OTLP/HTTP push)** | swaps `SimpleMeterRegistry` for `OtlpMeterRegistry`; default step 5 s; nothing on stdout/stderr `[SRC observability/MeterRegistries.java:26-60 @ cef8ec2]`. The harness runs `--network none` for probes and would need egress; **leave it off** |
| Resolved-output echo | always | **stderr** | `output.echoResolvedOutput(resolvedOutput, System.err, quiet)` — *"never silent"* `[SRC ListCommand.java:387 @ cef8ec2]` |
| Liveness watchdog / stuck markers | — | **stderr** | via Logback `[SRC runtime/LivenessWatchdog.java:268, 426 @ cef8ec2]` |

**Answer to the critical adapter question: no. Nothing but listing rows reaches stdout.** A repo-wide grep over `swath-cli/src/main`, `swath-core/src/main`, `swath-s3/src/main` for `System.out` / `FileDescriptor.out` returns exactly one production write path — the sink. The normalizer needs no filtering, and `--stream stdout` is the correct (and default-heuristic-correct) pin for `verify-listing.sh` `[HARNESS README.md:172-174]`.

One caveat to record: with `--format tsv`, stdout is not *purely* rows — it carries the **header line**, which the adapter must drop (§7.1).

---

## 7. The deliverable: `normalize.sh`

Modes: `tsv`, `jsonl`, `table`. (`parquet` is unreachable — §3.1.) `$2` (prefix) is accepted and ignored: swath emits absolute keys.

Design decisions, stated so they are auditable:
- **Never un-escape `\xHH`.** `ControlCharEscaper` does not escape backslash, so decoding is not invertible (§0.3). The adapter **detects and dies** — an honest ERROR beats a fabricated FAIL or a fabricated PASS.
- **Strip fractional seconds unconditionally** — required by the verifier's shape gate (§1).
- **Empty ⇒ `-`** for every optional field.
- **`jq` with `join("\t")`, never `@tsv`** — jq's `@tsv` escapes backslash as `\\` and would corrupt any key containing one.
- **Guard against a key containing a TAB or newline** and die: contract v2 is TAB-delimited and cannot represent such a key. That is a *contract* limit, not a swath defect — but silently emitting a 6-field row would read as an adapter bug.

```bash
#!/usr/bin/env bash
# tools/swath/adapter/normalize.sh — contract v2 adapter for swath v0.2.0.
#   normalize.sh <mode> [prefix]   stdin = captured stdout, stdout = 5-field TSV
#   key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class   ('-' where unexposed)
set -euo pipefail
export LC_ALL=C
MODE="${1:?mode}"; PREFIX="${2:-}"; : "$PREFIX"   # swath emits ABSOLUTE keys; prefix unused
die() { printf 'normalize.sh(swath/%s): %s\n' "$MODE" "$*" >&2; exit 3; }

# swath escapes C0 + DEL as \xHH in tsv/table and does NOT escape backslash,
# so the transform is NOT invertible: a key containing the literal chars \x09
# is byte-identical to one containing a real TAB. Refuse rather than guess.
guard_esc='if (k ~ /\\x[0-9a-f][0-9a-f]/) {
    printf "normalize.sh: key carries a swath \\xHH control escape, which is NOT invertible (ControlCharEscaper does not escape backslash). Re-run with --format jsonl. Offending: %s\n", k > "/dev/stderr"; exit 3 }'
guard_sep='if (k ~ /\t/ || k ~ /\n/) {
    printf "normalize.sh: key contains a TAB/newline, unrepresentable in contract v2: %s\n", k > "/dev/stderr"; exit 3 }'

case "$MODE" in

  # ---- jsonl : RECOMMENDED. JSON escaping is invertible; no header; summary is
  #      kept off the data stream by construction. Fields are OMITTED when null,
  #      so key on names, never position.
  jsonl)
    command -v jq >/dev/null || die "jq is required for mode jsonl"
    jq -r '
      def dash: if . == null then "-" else . end;
      [ (.key        // "-"),
        (if .size == null then "-" else (.size|tostring) end),
        (.etag         | dash),
        ((.last_modified | dash) | sub("\\.[0-9]+Z$"; "Z")),
        (.storage_class| dash) ]
      | join("\t")' \
    | awk -F'\t' '{ k=$1; '"$guard_sep"'; print }'
    ;;

  # ---- tsv : header line + SIX columns in a DIFFERENT order than contract v2
  #      (key size last_modified etag storage_class row_type).
  tsv)
    awk -F'\t' -v OFS='\t' '
      NR==1 && $1=="key" && $2=="size" && $3=="last_modified" { next }   # drop header
      NF==0 { next }
      {
        if (NF != 6) { printf "normalize.sh: expected 6 tsv columns, got %d on line %d\n", NF, NR > "/dev/stderr"; exit 3 }
        k=$1; sz=$2; mt=$3; et=$4; sc=$5
        '"$guard_esc"'
        '"$guard_sep"'
        sub(/\.[0-9]+Z$/, "Z", mt)                       # ISO_INSTANT emits 0/3/6/9 frac digits
        if (sz=="") sz="-"; if (mt=="") mt="-"
        if (et=="") et="-"; if (sc=="") sc="-"
        gsub(/^"|"$/, "", et)                            # defensive: swath already strips ETag quotes
        print k, sz, et, mt, sc
      }' ;;

  # ---- table : no header, FIXED-WIDTH, key LAST, no etag / no storage_class.
  #      size[1..14] "  "[15..16] time[17..40] "  "[41..42] key[43..]
  table)
    awk '
      length($0) < 43 { printf "normalize.sh: table line %d shorter than the 42-byte fixed prefix\n", NR > "/dev/stderr"; exit 3 }
      {
        if (substr($0,15,2) != "  " || substr($0,41,2) != "  ") {
          printf "normalize.sh: table column overflow on line %d (size>14ch or timestamp>24ch) — offsets unreliable\n", NR > "/dev/stderr"; exit 3 }
        sz=substr($0,1,14); mt=substr($0,17,24); k=substr($0,43)
        gsub(/^ +| +$/, "", sz); gsub(/^ +| +$/, "", mt)
        '"$guard_esc"'
        '"$guard_sep"'
        sub(/\.[0-9]+Z$/, "Z", mt)
        if (sz=="" || sz=="PRE" || sz=="-") sz="-"       # PRE=common prefix, -=delete marker
        if (mt=="") mt="-"
        printf "%s\t%s\t-\t%s\t-\n", k, sz, mt           # table exposes NO etag, NO storage_class
      }' ;;

  *) die "unknown mode (expected tsv|jsonl|table; parquet is a directory sink and is not stdout-capturable)" ;;
esac
```

### 7.1 Field coverage the verifier will report per mode

| mode | key | size | etag | mtime | storage_class |
|---|---|---|---|---|---|
| `jsonl` | ✔ | ✔ | ✔ | ✔ | ✔ |
| `tsv` | ✔ | ✔ | ✔ | ✔ | ✔ |
| `table` | ✔ | ✔ | `-` | ✔ | `-` |

### 7.2 Recommended invocations

```
swath list s3://<bucket>/ --format jsonl --no-sign-request          # primary
swath list s3://<bucket>/ --format tsv   --no-sign-request          # header-drop path
swath list s3://<bucket>/ --format table --no-sign-request          # 3-field, fixed-width
```
Add `-v` when the run record needs `api_calls` (stderr, §5.4). Do **not** add `--report` (container-local path), `--trace` (same), or `--metrics-endpoint` (needs egress). Pin `--stream stdout` on the verifier.

---

## 8. Client-side filters (Q6)

`--include`, `--exclude`, `--min-size`, `--max-size`, `--modified-since`, `--modified-until`, `--storage-class` `[SRC swath-cli/.../FilterOptions.java:29-56 @ cef8ec2]`, compiled into a `FilterChain` `[SRC …:58-96]`.

**They change only what is *emitted*, never what is *requested*.** Applied in the fetch worker **after** the page's checkpoint commit and after the in-range clamp, immediately before the batch is packed and sent downstream:

```java
awaitCommit(commit);   // durable before emit (I1)
...
List<ListEntry> kept = filters.apply(inRange);
```
`[SRC swath-core/.../engine/WorkStealingScan.java:730-741 @ cef8ec2]`, with the identical placement on the sequential/checkpointed producers `[SRC swath-core/.../runtime/ScanProducer.java:57 @ cef8ec2]`, `[SRC …/CheckpointedScanProducer.java:71 @ cef8ec2]`.

Nothing in the filter path touches the `ListObjectsV2` request: `S3PageFetcher` builds a request from prefix / start-after / delimiter / max-keys only `[SRC swath-s3/.../S3PageFetcher.java:180-186 @ cef8ec2]`. swath's own code says so directly — *"filters don't change what is listed"*, which is why the filter spec is excluded from `args_hash` and instead resume-checked separately `[SRC swath-cli/.../FilterOptions.java:100-103 @ cef8ec2]`.

Consequences worth recording:
- **Filtering does not reduce API calls.** It reduces emitted rows only, so `api_calls_per_1k_objects` and `overfetch_ratio` will both climb under heavy filtering — the doc calls this out as a legitimate cause of a high `overfetch_ratio` `[DOC metrics-and-observability.md §3]`.
- Evaluation is **cost-ordered**, cheap predicates before regex `[SRC swath-core/.../filter/FilterChain.java:25-27 @ cef8ec2]`.
- `--include`/`--exclude` use `Pattern.find()` — **substring, not anchored match** — over `key().asString()`, i.e. the U+FFFD-lossy decode `[SRC swath-core/.../filter/IncludeRegexFilter.java:114-116 @ cef8ec2]`.
- Non-object rows pass every value filter unconditionally (`return true` for non-`ObjectEntry`) `[SRC swath-core/.../filter/SizeFilter.java:149-153 @ cef8ec2]`.
- **For verification runs, use no filters at all** — a filtered run cannot be checked against the full manifest scope.

---

## 9. Open items / things I could not settle read-only

- I did **not** execute swath, so the `table` byte offsets (§2.4) and the "no fractional seconds in practice" conclusion (§2.2) are derivations from source, not observations. Both are cheap to confirm on the first smoke run — check that `cut -b 43-` lands on the key and that no `.` appears in the timestamp column.
- The `#queues` multiplier in the documented queue budget `cap × … × #queues` is doc-only `[DOC contracts.md §7]`; I confirmed the per-`Channel` weight mechanism but did not enumerate how many channels the text path instantiates (that sits in reader A's engine area).
- Whether the study wants Parquet covered at all is a scoping call for the orchestrator; if yes, it needs a harness change (bind mount + payload-dir archive), which is reader E's territory.
