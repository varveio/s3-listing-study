# Reader C — CLI, modes, resume, exit codes: v0.2.0 (cef8ec2) → v0.3.1 (7b9a5e2)

Read-only re-verification. Every line number below was confirmed on the frozen
v0.3.1 tree with sed/awk. Tally: 2 `holds`, 6 `holds-reanchored`, 7 `changed`,
0 `contradicted`, 0 `gone`. The unverified `crash-resume-works` stays unverified
for the same reason as before.

## What changed in this area

**The mode inventory grew.** `OutputFormat` gained `DISCARD`
[SRC swath-core/.../output/OutputFormat.java:9-15 @ 7b9a5e2], a diagnostic sink
that runs the full work-stealing scan and checkpoint commit-before-emit but
formats and writes nothing [SRC swath-core/.../runtime/ListRunner.java:499-529 @ 7b9a5e2],
[SRC swath-core/.../output/DiscardOutputStage.java:21-29 @ 7b9a5e2]. It refuses `-o`
[SRC swath-cli/.../OutputOptions.java:337-340 @ 7b9a5e2] and `--compression`
[SRC OutputOptions.java:255-259]. A TSV or JSONL directory destination is now a
partitioned text dataset (`--text-writers` 2-64 default 3, `--text-part-size`,
`--writeback-size`) [SRC OutputOptions.java:126-130,427-436 @ 7b9a5e2],
[SRC swath-core/.../output/text/TextDatasetFormat.java:36-56 @ 7b9a5e2]; the old
"directory dataset is Parquet-only" guard now refuses only `--format table`
[SRC OutputOptions.java:372-381 @ 7b9a5e2]. `--compression none|gzip|zstd` applies to
text streams, files and parts, is inferred from `.gz`/`.zst`, and is refused for
Parquet [SRC OutputOptions.java:49-56,240-260 @ 7b9a5e2]. Ten modes at v0.2.0 are at
least thirteen now, plus a compression axis. Seed modes are unchanged
[SRC swath-core/.../engine/SeedMode.java:12-18 @ 7b9a5e2].

**The command surface did not change, but its visibility did.** Subcommands are
still list, resume, help plus hidden dump-run and completion
[SRC swath-cli/.../App.java:38-43,168-170 @ 7b9a5e2], and the bare-URI did-you-mean
hint is intact [SRC App.java:183-198 @ 7b9a5e2]. `--engine-toggle` is now
`hidden = true` but still parses [SRC swath-cli/.../ListOptionGroups.java:99-104 @ 7b9a5e2];
`dump-run` is hidden [SRC swath-cli/.../DumpRunCommand.java:33-34 @ 7b9a5e2]. A new
page, `docs/cli.md`, is the explicit supported surface and `HelpUsageGoldenTest`
requires all 49 visible options to be named there and pins the hidden set
[SRC swath-cli/src/test/.../HelpUsageGoldenTest.java:53-64,66-94 @ 7b9a5e2]. So the
owner-split kill switch is still `--engine-toggle owner_split=off`, but through a
hidden option; `--no-owner-split` remains absent and test-rejected
[SRC swath-cli/.../EngineOptions.java:27 @ 7b9a5e2],
[SRC swath-cli/src/test/.../EngineToggleCliValidationTest.java:70 @ 7b9a5e2].

**Exit codes: seven became eight.** `DISK_FULL = 74` (EX_IOERR, added 0.2.2)
[SRC swath-cli/.../ExitCodes.java:36-40 @ 7b9a5e2]. It is not a new exception type:
`OutputException.exitCode()` returns 74 when `DiskFull.isIn` finds an out-of-space
IOException anywhere in the cause chain [SRC swath-core/.../error/OutputException.java:18-23,38-41 @ 7b9a5e2],
[SRC swath-core/.../output/DiskFull.java:13-24 @ 7b9a5e2]; the sealed permits list is
unchanged [SRC swath-core/.../error/SwathException.java:13-16 @ 7b9a5e2]. The protocol
violation-first resolution moved to [SRC ExitCodes.java:88-107,116-143 @ 7b9a5e2].
Upstream tables agree on all eight and say 74/75/124/130/143 imply resumable work
only for a managed Parquet directory [SRC docs/usage.md:299-312 @ 7b9a5e2],
[SRC docs/faq.md:123-125 @ 7b9a5e2].

**Resume scope is narrower in words and enforced in three places.** Only a
managed Parquet directory is resumable. Text datasets are refused at fresh
creation unless `--checkpoint none` is passed
[SRC swath-cli/.../ListCommand.java:1950-1979, esp. 1955-1957 @ 7b9a5e2], at resume after
destination restore [SRC ListCommand.java:773-781 @ 7b9a5e2], and in the text runner
[SRC ListCommand.java:1422-1425 @ 7b9a5e2]. The `--checkpoint` help and the resume
javadoc say so [SRC swath-cli/.../CheckpointOptions.java:15-19 @ 7b9a5e2],
[SRC swath-cli/.../ResumeCommand.java:45-46 @ 7b9a5e2]. The ephemeral in-memory store is
unchanged [SRC swath-core/.../checkpoint/SqliteCheckpointStore.java:90-101 @ 7b9a5e2],
[SRC ListCommand.java:683-685 @ 7b9a5e2]; `CheckpointOptions.resolve` is byte-identical
[SRC CheckpointOptions.java:67-77 @ 7b9a5e2].

**Checkpoint schema: still version 1, with additive columns.** `SCHEMA_VERSION = 1`
and exact-match-or-refuse are unchanged [SRC swath-core/.../checkpoint/CheckpointSchema.java:35,55-62,106-122 @ 7b9a5e2];
`BASE_DDL` moved to [SRC CheckpointSchema.java:201-225 @ 7b9a5e2]. `part_file` gained
nullable `format_version`/`extension_type` via the same idempotent ALTER TABLE
path [SRC CheckpointSchema.java:82-86,176-179 @ 7b9a5e2]. These feed a new `--sort`
resume guard that refuses staging in a different page-run format or with
unsupported metadata and advises `--restart`
[SRC ListCommand.java:868-909 @ 7b9a5e2], [SRC ListRunner.java:703-707 @ 7b9a5e2] — a
0.2.x sorted run interrupted mid-listing cannot be resumed by 0.3.x.

**Resume classification and resume CLI.** `ResumeRegistry.java` has no diff:
identity, sticky and free sets are exactly as described
[SRC swath-cli/.../ResumeRegistry.java:65-109,130-182 @ 7b9a5e2]; every new output flag is
`@Resume(FREE)` [SRC OutputOptions.java:49,126,427,432 @ 7b9a5e2]; bearer-token
reasoning is unchanged [SRC swath-cli/.../BearerTokenOptions.java:15-33 @ 7b9a5e2]. The
`swath resume` option set is identical, but three tune keys are now
resume-applicable: `sort.merge-parallelism`, `sort.keep-staging`,
`sort.ignore-disk-check` [SRC swath-cli/.../TuneOptions.java:34-43,76-109 @ 7b9a5e2].
`sort.merge-parallelism` (1..16) defaults to `max(1, min(8, cores/2))`
[SRC swath-core/.../sort/SortConfig.java:72,117-122 @ 7b9a5e2], so the `--tune help`
default in the published image (4) is host-derived. `parquet.writers` widened to
2..64 with a heap-admission gate above 4 [SRC TuneOptions.java:29-30 @ 7b9a5e2],
[SRC OutputOptions.java:484-485,524-543 @ 7b9a5e2].

**Symlink refusal (new).** A symlinked dataset root, `.swath/`, checkpoint file or
managed artifact is refused with exit 2 before anything is opened, for fresh
runs, explicit-checkpoint runs and `swath resume`
[SRC swath-cli/.../DatasetDirGuard.java:86-89,140-148,213-217 @ 7b9a5e2],
[SRC ResumeCommand.java:133-136 @ 7b9a5e2], [SRC ListCommand.java:767-772 @ 7b9a5e2].

## What did not change

- No shallow/delimiter output mode: every engine dispatch (now five) hard-wires
  `ListingMode.OBJECTS` [SRC ListRunner.java:452,508,539,634,753 @ 7b9a5e2],
  [SRC ListCommand.java:696 @ 7b9a5e2]; the repo's only `--delimiter` is still the
  replay bench command [SRC swath-replay/.../BenchCommand.java:52 @ 7b9a5e2].
- Versioned listing is still dead code: the fetcher throws
  [SRC swath-s3/.../S3PageFetcher.java:160-162 @ 7b9a5e2]; the schema admits it
  [SRC CheckpointSchema.java:206 @ 7b9a5e2]; the doc sentence moved to
  [SRC docs/usage.md:30 @ 7b9a5e2]; `--all-versions` survives in ROADMAP.md:28 and
  javadoc (SortMode.java:11, ArgsHashFields.java:12).
- Filters still run after the checkpoint commit and in-range clamp
  [SRC swath-core/.../engine/WorkStealingScan.java:732-745 @ 7b9a5e2]; the regex filter
  is byte-identical [SRC swath-core/.../filter/IncludeRegexFilter.java:11-24 @ 7b9a5e2];
  prose agrees [SRC docs/usage.md:143-144 @ 7b9a5e2], [SRC docs/cli.md:71 @ 7b9a5e2].
- Both live error strings that name absent flags persist verbatim:
  `--seed`/`--hints` [SRC swath-core/.../engine/SeedStep.java:160-161 @ 7b9a5e2] and
  `--force-sort`, now in the renamed guard
  [SRC swath-core/.../output/sorted/StagingDiskGuard.java:186-193 @ 7b9a5e2] (javadoc
  at 52, 71, 119). The separate startup pre-check correctly names
  `--tune sort.ignore-disk-check=on` [SRC ListCommand.java:1495-1503 @ 7b9a5e2].
- The three engine-default javadoc drift items persist: TailFloorMode:14-15,
  RateAnchoredEstimator:17-18, RemainingWorkEstimator:17-19,59-62, against
  EngineToggles:212,225. D5 (`--no-owner-split`) persists at EngineToggles:246,
  254-255, 293-295. D13 in s3-implementation-compatibility.md:18-28 was reworded,
  not retracted (reader B's call on correctness). D1's flag name left usage.md
  prose but not ROADMAP/javadoc. D7 (`--single-file`) persists at
  OutputOptions.java:512-514.
- Parquet still refuses stdout [SRC OutputOptions.java:628-635 @ 7b9a5e2]; the new
  text datasets are equally path-based [SRC OutputOptions.java:637-640 @ 7b9a5e2];
  `Formatters.text` throws for PARQUET and DISCARD [SRC swath-core/.../output/Formatters.java:16-26 @ 7b9a5e2].
- `crash-resume-works` remains unverified for the same reason: the durable path
  needs a managed Parquet directory the harness cannot mount, and the new
  sinks are non-resumable so they do not open a cheaper route.
