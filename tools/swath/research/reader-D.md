# Reader D — output area, v0.2.0 (cef8ec2) -> v0.3.1 (7b9a5e2)

Read-only re-verification of the twelve output/normalizer claims. Every line number below was checked with `sed -n` on `/home/sagi_varve_io/workspaces/swath-v0.3.1`. Companion JSON: `reader-D.json`.

## Verdicts at a glance

| Claim | Verdict |
| --- | --- |
| text-sink-key-encoding-is-lossy | changed |
| jsonl-escaping-is-invertible | holds-reanchored |
| tsv-header-and-field-order | changed |
| parquet-key-column-is-byte-exact | changed |
| aligned-fixed-column-timestamp-assumption | changed |
| timestamp-precision-is-variable | changed |
| stdout-is-clean | changed |
| output-is-streaming | holds |
| bounded-memory-at-scale (unverified) | reason still applies; targets moved |
| control-char-key-fidelity-untested (unverified) | reason still applies |
| parquet-modes-execute (unverified) | reason still applies; study has a 0.3.1 receipt |
| parquet-output-byte-exact (unverified) | reason still applies; read-back contract changed |

## What changed

### 1. Timestamps are now the endpoint's text, not swath's ISO_INSTANT rendering

`Fields.isoMicros` no longer exists [SRC swath-core/src/main/java/io/varve/swath/output/Fields.java:9-16 @ 7b9a5e2]. `ObjectEntry` carries a `lastModifiedText` string [SRC swath-model/src/main/java/io/varve/swath/model/ObjectEntry.java:103-105 @ 7b9a5e2], and every text formatter writes that: TSV [SRC swath-core/src/main/java/io/varve/swath/output/TsvFormatter.java:45 @ 7b9a5e2], JSONL [SRC .../output/JsonlFormatter.java:84-88 @ 7b9a5e2], table [SRC .../output/AlignedFormatter.java:47 @ 7b9a5e2].

On the production S3 path the streaming interceptor captures the `LastModified` element text verbatim [SRC swath-s3/src/main/java/io/varve/swath/store/s3/StreamingListObjectsV2Interceptor.java:208-213 @ 7b9a5e2] and builds entries with it [SRC ...:541-542 @ 7b9a5e2]; the fetcher's javadoc says this path "preserves last-modified text without parsing it" [SRC swath-s3/.../S3PageFetcher.java:47-50 @ 7b9a5e2]. Only the compatibility SDK-model path [SRC .../S3PageFetcher.java:388-391 @ 7b9a5e2], the sort spill cursor and fixtures rebuild from epoch micros, rendered by `LastModified.textFromEpochMicros` with 0, 3 or 6 fraction digits and empty for zero [SRC swath-model/.../LastModified.java:37-71 @ 7b9a5e2].

Consequence for the normalizer: S3 spells LastModified with milliseconds, so every live 0.3.1 row reads `YYYY-MM-DDTHH:MM:SS.000Z` (the study's own `receipts/observations-v0.3.1/adapter-modes/jsonl.sample.jsonl` and `table.sample.txt` show this). The `\.[0-9]+Z` strip now fires on every row. The aligned table's `TIME_WIDTH = 24` [SRC .../output/AlignedFormatter.java:22-23 @ 7b9a5e2] is exactly filled by that 24-character spelling, so offsets 1-14 / 17-40 / 43-end still hold; anything longer overflows because `pad()` appends unpadded when the gap is not positive [SRC .../output/AlignedFormatter.java:71-82 @ 7b9a5e2]. At the time of this read the normalize.py docstring still said "ISO_INSTANT (Fields.isoMicros)" and "passed through unchanged"; that was pre-fix context, and the committed adapter now documents the endpoint LastModified text and the unconditional fraction stripping.

### 2. Parquet `key` is STRING-annotated and non-UTF-8 keys are refused

Schema: `required BINARY .as(stringType()) named key` [SRC swath-core/.../output/parquet/ParquetSchema.java:27 @ 7b9a5e2]. The write support validates with `KeyBytes.isValidUtf8` and throws `InvalidKeyEncodingException` before `binary("key", 0, key)` [SRC .../output/parquet/ListEntryWriteSupport.java:46-51 @ 7b9a5e2]; the exception is a typed `OutputException` naming a 16-byte hex prefix [SRC swath-core/src/main/java/io/varve/swath/error/InvalidKeyEncodingException.java:20-26 @ 7b9a5e2]. The validator itself [SRC swath-model/.../KeyBytes.java:50-95 @ 7b9a5e2].

Byte-exactness for accepted keys is unchanged (same raw bytes written), but DuckDB now returns `VARCHAR` not `BLOB` [SRC docs/usage.md:258,275-284 @ 7b9a5e2]; normalize.py's `PARQUET_QUERY` comment ("The key column is Parquet BLOB") is stale, and a byte-exactness check must compare the UTF-8 bytes of the returned string. The "only byte-exact key path" framing no longer distinguishes Parquet: every key a live listing can produce is well-formed UTF-8 (both live paths do `key.getBytes(UTF_8)` on the SDK's decoded string [SRC .../S3PageFetcher.java:388-391; .../StreamingListObjectsV2Interceptor.java:541-542 @ 7b9a5e2]), for which the text sinks' decode is lossless too; the residual text-sink defect is the ambiguous `\xHH` escape, which JSONL avoids.

Answer to the parent's question: a live listing cannot emit a non-UTF-8 key at 0.3.1. If one arrives from a fixture/capture, the text sinks replace it with U+FFFD [SRC .../KeyBytes.java:113-120 @ 7b9a5e2; Utf8TsvFormatter.java:99-106 @ 7b9a5e2] and the Parquet sink fails the run.

### 3. Text sinks: escaper unchanged, a new byte-oriented TSV writer for dataset parts

`ControlCharEscaper` and `Json` are byte-identical between tags [SRC .../output/ControlCharEscaper.java:21-49; .../output/Json.java:15-37 @ 7b9a5e2]. Partitioned TSV parts are written by `Utf8TsvFormatter`, which copies valid UTF-8 key bytes directly and applies the same backslash-x-HH escape without escaping the backslash [SRC .../output/Utf8TsvFormatter.java:99-106,160-170 @ 7b9a5e2]. Header and six-column order are unchanged on both writers [SRC .../output/TsvFormatter.java:23,40-55; .../output/Utf8TsvFormatter.java:26-28,50-85 @ 7b9a5e2] and each dataset part opens with its own header [SRC swath-core/.../output/text/TextDatasetFormat.java:188 @ 7b9a5e2]. JSONL dataset parts reuse `JsonlFormatter` [SRC .../output/text/TextDatasetFormat.java:213-218,280 @ 7b9a5e2]. normalize.py's column declarations, per-part header skip and `\xHH` refusal still match.

### 4. stdout

The sink still opens `FileDescriptor.out` raw, now via `encode()`, which can wrap it in gzip/zstd per `--compression` [SRC swath-cli/.../OutputOptions.java:670,700-711 @ 7b9a5e2]. One new stdout writer exists: `ListCommand.call` hands a `PrintWriter(System.out)` to `tune.apply`, and `--tune help` / `--tune KEY=?` print there and exit 0 before listing [SRC swath-cli/.../ListCommand.java:303-306; swath-cli/.../TuneOptions.java:134-141,155-156 @ 7b9a5e2]. Verbose "tune effective" and the resolved-output echo remain on stderr [SRC .../ListCommand.java:316,429 @ 7b9a5e2]. An exhaustive grep of main sources finds no other fd-1 writer. For a listing run the claim holds; the statement needs scoping.

### 5. Streaming invariant

`OutputStage.writeBatch` and `RowTally` are unchanged [SRC .../output/OutputStage.java:97-113; .../output/RowTally.java:26-29 @ 7b9a5e2]. New `DiscardOutputStage` keeps the same per-page shape [SRC .../output/DiscardOutputStage.java:56-68 @ 7b9a5e2]. Dataset writers hold bounded batch queues (64 writers max, 256 batches pool-wide) [SRC .../output/dataset/SharedDatasetWriterPool.java:74-83 @ 7b9a5e2]. `Channel.java` is identical.

### 6. Datasets, manifests, part names

- Manifest written once at `publishSuccess`, then state, symlink, `_SUCCESS` last [SRC .../output/dataset/DatasetPublication.java:28-31; .../output/dataset/SharedDatasetWriterPool.java:47-51,584-592; .../output/parquet/Manifest.java:214-216 @ 7b9a5e2]. A killed run has neither.
- Direct parts: `part-w<lane>-<seq><suffix>` [SRC .../output/dataset/SharedDatasetWriterPool.java:885 @ 7b9a5e2]; text suffixes `.tsv/.jsonl` plus `.gz/.zst` [SRC .../output/text/TextDatasetFormat.java:50-56 @ 7b9a5e2].
- Sorted parts: `part-%05d.parquet` for i from 0 [SRC .../output/sorted/StagingNames.java:55-57; .../output/sorted/SortedDatasetPublisher.java:99-101 @ 7b9a5e2] while `swath.sort.file_index` is `ordinal + 1` [SRC swath-core/.../sort/finalize/PartEncoders.java:359-360; .../output/parquet/sorted/SortedParquetStamp.java:28 @ 7b9a5e2].
- `--format discard` [SRC .../output/DiscardOutputStage.java:21-29; swath-cli/.../OutputOptions.java:337-339 @ 7b9a5e2]; `--compression` [SRC swath-cli/.../OutputOptions.java:50-52 @ 7b9a5e2]; `--writeback-size` [SRC swath-cli/.../OutputOptions.java:433-436 @ 7b9a5e2]; `parquet.writers` 2..64 [SRC swath-cli/.../TuneOptions.java:29 @ 7b9a5e2].

### 7. Report schema

`schema_version` stays 2 [SRC swath-core/.../observability/JsonRunSummaryWriter.java:408 @ 7b9a5e2]; new `listing_duration_ms`, `recovered_objects` [SRC ...:462-468 @ 7b9a5e2], `dataset_writer` [SRC ...:519 @ 7b9a5e2], `sort.arm` [SRC ...:771 @ 7b9a5e2]; `buffer_sort_fallbacks` is a constant 0 [SRC ...:831 @ 7b9a5e2] and `merge_boundaries_ms` is gone.

## Unverified claims

All four stay unverified for their original reasons. What a future run must check differently: the Parquet read-back yields VARCHAR keys (compare as UTF-8 bytes); every 0.3.1 dataset key is valid UTF-8 by construction; sorted parts start at `part-00000.parquet`; no manifest exists without `_SUCCESS`; the memory qualification's "Parquet part-metadata re-serialization" growth path no longer exists (manifest once at completion) and the things to vary are the writer-pool queue budget and the sort pipeline's heap/FD admission. The study capsule already holds `receipts/observations-v0.3.1/adapter-modes/` with a sorted-Parquet stderr and an observation noting `data/part-00000.parquet` — an execution observation the parent may promote.

## Housekeeping

`adapter/normalize.py` in the capsule changed on disk during this task (the table query gained fraction stripping); it was assessed as-is and not edited. No repository file was modified.
