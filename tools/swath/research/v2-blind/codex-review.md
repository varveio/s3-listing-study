# Independent cross-model review — v2-blind groundwork (swath v0.2.0)

Reviewer: OpenAI Codex CLI, `gpt-5.6-sol`, reasoning effort `xhigh`, `--sandbox read-only`.
Subject: the artifacts in this directory, against the frozen source at `cef8ec2` (tag v0.2.0).
Scope note: the reviewer was explicitly kept away from the pre-existing `tools/swath/`
capsule, so this artifact stays independently derived and can still be compared with it.

Findings are reproduced below, followed by resolutions. The reviewer emitted its
locations as markdown links to absolute machine-local paths; that link syntax has
been flattened to code spans so the repository link checker does not read them as
broken repo links. No wording was changed.

Findings, otherwise verbatim: Eight findings:
four major, four minor. All eight were independently re-verified against source
before being acted on, and all eight were accepted — none was disputed.

## Findings (verbatim)

1. **Major — JSONL normalization fails to reject tab/newline keys.** `normalize.sh:25–33` runs the guard after `join("\t")`. At that point a tab has already become a sixth field and a newline a second record, while `k=$1` hides the offending character. Synthetic inputs produced malformed output with exit 0, violating the five-field verifier contract stated at `report.md:333`.

2. **Major — probe overhead is calculated incorrectly.** `report.md:583–585` subtracts the theoretical minimum page count from total calls and labels the remainder “non-page.” The observations already record actual worker-page fetches: 49 for hourly and 165 for full, alongside 75/240 total calls (`hourly stderr:12–13`, `full stderr:12–13`). With no retries and readahead off, non-worker calls were therefore 26 and 75—not 72 and 91. Probe calls did not dominate the hourly run. The same false figures recur at `report.md:709`.

3. **Major — ephemeral SQLite is mischaracterized in opposite directions.** `report.md:36` says every split is durable, although stdout/`--checkpoint none` uses non-durable SQLite `:memory:`. Conversely, `report.md:602` and `report.md:712` claim those runs may not have exercised `sqlite-jdbc`. They necessarily did: ephemeral runs call `SqliteCheckpointStore.openEphemeral`, which opens `jdbc:sqlite::memory:` (`ListCommand.java:627`, `SqliteCheckpointStore.java:85`). The arm64 runs close the SQLite native-extraction gap, though not the Zstd/Parquet gap.

4. **Major — hypothetical endpoint hazards are generalized as observed behavior.** `report.md:714` says the `+`/`EncodingType` hazards “become live” on MinIO, Ceph, R2, or LocalStack and produce wrong answers. The derivation establishes only conditional behavior for a nonconforming endpoint; the tested LocalStack build preserved `+`, the tested MinIO build was conformant, and nothing cited tests Ceph or R2 (`reader-B-store.md:195`, `reader-B-store.md:209`). `report.md:437` likewise presents real-S3 behavior beside an anchor that tests only LocalStack.

5. **Minor — “FREE” resume classifications imply controls the resume CLI does not expose.** The tunables table marks concurrency, queue size, request rate, and Parquet controls as `FREE` at `report.md:203–219`. Reader C defines that as “may change freely on a resume” at `reader-C-cli.md:194`, then contradicts it at `reader-C-cli.md:215`: `swath resume` accepts none of those flags. The golden help confirms the restricted surface (`swath-resume.txt:1`).

6. **Minor — the artifacts repeat and omit another stale live flag name.** `reader-D-output.md:175` says the disk guard is bypassed with `--force-sort`, contradicting the correct `--tune sort.ignore-disk-check=on` surface in `report.md:218`. Worse, the shipped guard’s runtime exhaustion message itself tells users to pass nonexistent `--force-sort` (`SortDiskGuard.java:186`). This fifteenth drift item is absent from the supposedly consolidated fourteen-item set at `report.md:630`.

7. **Minor — the sustained-timeout shed is overstated as impossible during progress.** `report.md:118` and `reader-A-engine.md:160` say a progressing run “never sheds.” The code permits up to `max(1,T/32)` successes in the window (`ConcurrencyGauge.java:489`); for example, at `T=4`, three timeouts plus one success still triggers a shed.

8. **Minor — the unused atomic API counter is said to drive cost reporting.** `report.md:360` and `reader-B-store.md:334` say `S3PageFetcher.apiCalls()` drives the cost line and efficiency guard. Targeted lookup finds no production caller of that accessor; `cost.api_calls` comes from the summed `swath.api.calls` meter at `RunMetrics.java:2169`.
## Resolutions

Every finding was accepted and fixed. Verification of each fix was performed
independently of the agent that applied it.

| # | Sev | Finding | Resolution |
| --- | --- | --- | --- |
| 1 | major | jsonl guard ran after `join("\t")`, so a TAB in a key emitted a malformed 6-field row with exit 0 | Refusal moved inside `jq`, onto the raw `.key`, before the join. Re-verified: normal row → 5 fields exit 0; TAB key → exit 3, no output; newline key → exit 3; 5,000 real rows → uniformly 5 fields. `adapter/normalize.sh:29-45` |
| 2 | major | "non-page calls" derived from a *theoretical minimum* page count, inflating probe overhead to 72/91 and wrongly concluding probes dominate the small run | Corrected to the actual fetched-page counts recorded in the observations: 26/75 (hourly) and 75/240 (full) — 35% vs 31%. Conclusion inverted: probe overhead dominates neither run. `report.md:581-591`, `:716` |
| 3 | major | Ephemeral SQLite described wrongly in both directions — splits called durable, and arm64 runs said possibly not to exercise `sqlite-jdbc` | Both corrected and anchored. `--checkpoint none` uses an in-process `:memory:` SQLite, so commits are not durable, and the arm64 runs *did* load `sqlite-jdbc`. The `zstd-jni`/Parquet native gap remains open. `report.md:36`, `:608`, `:719` |
| 4 | major | Conditional endpoint hazards (`+`-to-space, `EncodingType` gating) presented as live behaviour on MinIO/Ceph/R2/LocalStack | Rewritten as conditional and `[INFERRED]`, naming LocalStack and MinIO as actually tested and Ceph/R2 as untested speculation. `report.md:439`, `:721` |
| 5 | minor | `FREE` resume class implied flags settable on `swath resume`, which its golden help refutes | Qualification added: `FREE` means not identity-bearing / not refused, not settable on the resume command line. `report.md:223` |
| 6 | minor | Drift set claimed fourteen items but omitted `SortDiskGuard`'s message naming the nonexistent `--force-sort` | Added as D15, count corrected. `report.md:636`, `:658`. `reader-D-output.md:176` annotated inline rather than overwritten |
| 7 | minor | Sustained-timeout shed described as never firing on a progressing run | Softened to what the code guarantees — up to `max(1, T/32)` successes are permitted, so a progressing run can still shed. `report.md:118` |
| 8 | minor | `S3PageFetcher.apiCalls()` credited with driving the cost line | Corrected: that accessor has no `src/main` caller; `cost.api_calls` is the summed `swath.api.calls` meter. `report.md:362`, `reader-B-store.md:336` annotated |

### Raised during the fix pass, beyond the review's own findings

- **Two different `pages` fields exist in the observation files** and would confuse anyone
  re-checking finding 2: `list_run_summary` reports committed pages (18 / 159),
  `list_run_diagnostics` reports pages fetched (49 / 165). Traced to source
  (`RunMetrics` raw-count vs committed-page counters). The fetch count is the correct
  denominator, so the corrected 26/75 stands. Recorded in `report.md:587`.
- **Open lead, not acted on:** run 1 records `fetched_keys=37524` for a prefix holding
  2,549 keys — roughly 15x over-fetch, because parallel range workers each pull a full
  1,000-key page and discard everything past their `hi`. On these two runs that, not probe
  count, is the dominant small-scope inefficiency. No claim is made about it; it is a
  candidate finding for a future pass with a provisioned runner.
- `reader-B-store.md:205` asserts real-S3 `+` encoding without a real-S3 anchor — the same
  weakness finding 4 corrected in the report. Left in place, flagged here, because the
  reader files are derivation records rather than current claims.
