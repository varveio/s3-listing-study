# Harness

Shared measurement and verification infrastructure for the smoke campaign
([`docs/methodology.md`](../docs/methodology.md) § Execution order). Built
**once**, by the orchestrator. Dispatch discipline and what blind agents may read are defined in
[`../docs/operating/tool-research-brief.md`](../docs/operating/tool-research-brief.md).

## Status of the run-once prerequisites

**Check this table before dispatching an agent** — rationale in
[the brief](../docs/operating/tool-research-brief.md).

| # | Prerequisite | Status |
| --- | --- | --- |
| 1 | `docs/smoke-bucket.md` — bucket registry | **live** — `noaa-normals-pds`, snapshot 2026-07-17 (contract-v2 re-baseline) |
| 2 | Reference manifest, pinned harness client | **live** — 148,917 keys, **contract v2** (`key/size/etag/mtime/storage_class`), sha256 in the registry, in `<data>/manifests/` |
| 3 | `smoke-run.sh` — run wrapper | **requires provisioned runner** — contained-network security profile must pass its activation gate |
| 4 | `verify-listing.sh` — output verifier | **requires provisioned runner for reference re-lists** — same contained-network profile and gate |
| 5 | `base.Dockerfile` — shared base layer | **not built** — deliberately. The brief marks it optional and it is only needed by a tool that ships no upstream image. The first such tool builds it; speculative infrastructure for nobody is worse than none. |

Campaign parameters currently in force: `CREDS=none` (every run anonymous),
`EDGE_BUCKET=none` (edge-case checks recorded **deferred**, not dropped).

## Mandatory runner boundary

Further networked execution requires the provisioned
[`s3-listing-study-v1`](../docs/operating/runner-security.md) profile. The container
hardening, host firewall, identity claims, and build/pull boundary are
specified there; that document is the authoritative contract.

`smoke-run.sh` and each reference re-list fail closed through
`runner-security-check.sh`. The check binds a root-owned readiness record to the
current host, boot, Docker daemon, complete Docker-network inventory, bridge
configuration, firewall backend, policy artifacts, and canonical live rules. It
also rejects recognized cloud metadata in the `local` profile and probes
link-local denial plus public S3 from the subject bridge.

## Contract

```
                 registry-lookup.sh ── docs/smoke-bucket.md (single source)
                          │
   passed --run-script PATH ┤ argv only, NUL-delimited, never executes
                          ▼
                    smoke-run.sh  ── owns docker run, auth, timeout, measurement
                          │            → receipts/smoke/<mode>/receipt.md
                          ▼
   passed --normalize PATH ───► verify-listing.sh ── manifest (sha256-bound)
```

Adapter locations are caller-supplied, not inferred by the harness. Every
capsule passes `tools/<tool>/adapter/run.sh` and
`tools/<tool>/adapter/normalize.sh` (migration wave completed 2026-07-20);
historical receipts cite the pre-migration root paths as run facts.

- **`registry-lookup.sh <bucket> <field>`** — resolves `region`, `manifest`,
  `manifest_sha256`, `snapshot_date`, `keys`, `shape` from the registry. Exists
  so a receipt cites what the registry *says* rather than what somebody retyped:
  a 64-hex digest transcribed by hand across twelve tools is a typo waiting to
  certify a manifest that verified nothing. Strict — any ambiguity is a hard,
  loud failure. `--path` / `--digest` bind a receipt to the registry it came
  from.
- **`smoke-run.sh`** — owns `docker run` entirely. **Receipts produced outside
  this wrapper do not count** (methodology § Run records (receipts)). Writes `run.meta`
  alongside `receipt.md`. Current records declare
  `payload_path_base=run-meta-directory`: inline `stdout_path`/`stderr_path`
  values are resolved relative to their sibling `run.meta`; large external
  payloads remain absolute and hash-bound until the release-asset index gives
  them stable public identities. Records without the base field are historical
  and retain their original working-directory interpretation.
  - **Payload hygiene** follows the brief's **redact → scan →
    truncate → hash** order: it redacts the **whole raw stream** first (a legitimate
    object key shaped like a credential is scrubbed, not falsely flagged), then
    secret-scans the **full redacted stream** — every byte, *before* truncation — so
    a credential redaction missed is still caught even when it sits beyond the
    **64 MiB** cap, never silently dropped with the truncated tail. On a scan hit the
    offending redacted stream is **quarantined to `<out>/quarantine/` before dying**
    (the cleanup trap clears only `$TMP` and the container, never the quarantine
    dir). Only then does it cap each stream at 64 MiB (keeping the head, recording
    `truncated=yes` with the dropped byte count and saying so loudly), and hash.
  - **Per-tool env guard.** Repeatable **`--env NAME=VALUE`** is a positive
    per-tool allowlist, not a general passthrough. Current demonstrated needs are
    `s3-fast-list:RUST_LOG` (observability) and
    `minio-mc:MC_HOST_s3=https://s3.amazonaws.com` (validated functional
    endpoint/anonymous-alias configuration). Receipts and `run.meta` record
    observability and functional configuration separately. Credential, proxy,
    endpoint/trust-anchor, loader, and path-redirection classes remain denied;
    credentialed execution remains unimplemented.
  - **Bounded, no-pull Docker lifecycle.** Every harness-owned container uses
    `--pull=never`. Docker control-plane operations have a 30-second bound and
    cleanup calls a 10-second bound. Timeout statuses 124 (TERM deadline) and
    137 (follow-up KILL) are labeled as timeouts, but cleanup reconciliation is
    required after **any** nonzero Docker client result because other failures
    (including 125) can also leave daemon state uncertain. A smoke subject is
    created under a random, wrapper-owned stable name and then started; its EXIT
    path can still address that name after any lifecycle failure. The offline
    version probe is also no-pull, networkless, bounded, and stably named. Each
    evidentiary listing container receives
    `--log-driver=json-file --log-opt max-size=-1`. Before a smoke subject starts,
    its inspected effective config must be exactly that non-rotating local
    contract; rotating, limited, remote, extra, or unknown options fail closed.
    New `run.meta` records the validated driver, canonical-config SHA-256, and
    base64-encoded option-key names only after validation. Raw option values are
    never persisted. Reference re-lists use the same explicit config but consume
    attached stdout directly; probes produce no listing evidence and need no
    evidentiary log contract. Unlimited Docker logs avoid rotation, not host-disk
    exhaustion.
  - **Flat-record safety.** Every string emitted to line-oriented `run.meta` is
    checked immediately before emission and rejected if it contains a control
    byte below 0x20, including caller input and auto-detected Docker/version/host
    values. An embedded newline or CR can therefore not forge a later field.
    Dynamic human-receipt values use one HTML-entity renderer before entering
    Markdown; pipes, backticks, and HTML delimiters cannot create cells or close
    code spans, while controls are refused.
  - **Version + TODO warnings.** Auto-fills the tool version (caller `--tool-version` wins, else
    best-effort `--version` on the image), and any receipt field left as `TODO`
    produces a loud warning on the wrapper's final summary.
- **`verify-listing.sh`** — the only thing that issues a verdict on output.
  Requires `--receipt` and refuses to run without the wrapper's `run.meta`.
  Parses the **contract-v2 5-field manifest and adapter output**
  (`key/size/etag/mtime/storage_class`) and fails loudly if handed a 3-field
  pre-2026-07-17 artifact. Field checking is **by policy**: keys are always
  asserted; each of size/etag/mtime/storage_class is asserted only where the
  adapter emitted a non-`-` value. `mtime` is compared by a canonical UTC form
  (a manifest `…Z` and an adapter `…Z`/`…+00:00` denoting the same second compare
  equal) in a single awk pass — no per-row `date` fork. **Refuses a verdict on a
  truncated verified payload** (a cut-off listing cannot establish completeness;
  stderr truncation alone does not block verifying a complete stdout). Passes the
  run's prefix (from `run.meta`) to `normalize.sh` as `$2`. Gains
  **`--scope union`** (below).

### `--scope union` — fan-out completeness across shards

Takes multiple receipts (repeatable `--receipt`, or `--receipts-dir`) and a
required **`--out <dir>`** — the union verdict is durable, written to
`<dir>/union-verify.md` (verdict line, shard list with prefixes and receipt
paths, counts including duplicates-before-dedup, structural status, the reference
re-list result if one ran, the registry digest, and a UTC timestamp), not left on
stdout. Each shard is **re-derived, not trusted**: it is normalized against its
own prefix scope, then (a) all shards' outputs concatenate as a **multiset** with
cross-shard duplicates counted **before** dedup, (b) the combined output is
checked against the **full manifest** exactly, and (c) **scope coverage** is
verified explicitly.

Coverage needs an *explicit unprefixed-remainder shard* to attribute root-level
keys (e.g. `index.html`) that live under no prefix.

- An empty `run.meta` prefix is
  **ambiguous** — it is also what an ordinary full-bucket root run records — so the
  remainder must be **designated with `--remainder <receipt-dir>`** (whose
  `run.meta` prefix must be empty); an empty-prefix shard that is *not* the
  designated remainder is a hard `ERROR`.
- **The remainder shard is typically a
  delimiter-mode root listing** (`--delimiter /`, no prefix), which returns exactly
  the root-level objects — so **its output must contain exactly the unprefixed keys;
  a full recursive run cannot serve as the remainder** (it re-lists every prefixed
  key, which then reads as out-of-scope extras and cross-shard duplicates).
- Because
  that root listing is a different request shape, **the remainder is EXEMPT from
  mode binding**: it is normalized under its own `run.meta` mode against the orphan
  keys, while the prefix shards are still held to one shared mode.
- A union of
  prefixes with a needed remainder missing is **structurally incomplete** — a
  coverage defect in the fan-out **plan**, reported as **`ERROR` (exit 3)**,
  distinct from and never mistaken for the tool dropping keys (`FAIL`).

The union mirrors the single-receipt path everywhere it judges: it **binds mode
across the prefix shards** (they must share one `mode`; a caller `--mode` must
equal it; the remainder is exempt as above), **refuses** any shard with
`redaction_changed_bytes=yes` or a **truncated** verified stream, and
copies-then-hashes-then-judges. **Stream selection**: by default it picks each
shard's verified stream by heuristic — stdout unless stdout is empty and stderr is
non-empty — which is a guess; a tool that prints a banner on stdout and its
listing on stderr needs **`--stream stdout|stderr`**, which pins the stream for
**all** shards (a fan-out set shares tool+mode, so it shares the stream). It
**re-lists the reference before issuing any `FAIL`** — missing/extra keys, field
mismatches, or a shard failing its own scope — so bucket drift is never charged to
the tool; only pure duplication (dups > 0, everything else clean) may `FAIL`
without a re-list, since a bucket cannot drift into duplicates. Every plan-defect
death after `--out` is parsed still writes a durable `union-verify.md` with
`Verdict: ERROR`.

Invocation-plan defects are `ERROR`, never a tool `FAIL`: a receipt named twice
(realpath-deduped), or two shard prefixes where one is a string-prefix of another
(overlap makes a correct tool double-emit the nested keys). All shards must cite
the current registry digest, the same bucket, tool, and auth mode.

### `run.meta` — the binding between a run and its verdict

The verifier does **not** trust its own arguments. `smoke-run.sh` records what
actually ran — tool, mode, auth, bucket, prefix, image, exit code, payload paths
and their sha256 — and the verifier validates against it. Without this, tool,
mode, bucket, scope and inputs are five independent claims: one mode's output
can be checked against another mode's scope and stamped into a third mode's
receipt, and every artifact still looks internally consistent. The verifier
refuses when `--scope-prefix` contradicts the prefix the run actually used, and
when an input's bytes no longer match the hash its receipt cites.

Scope is passed as separate arguments — `--scope full|prefix|delimiter` plus
`--scope-prefix` / `--scope-delimiter`. A packed `delimiter:D:P` string cannot
represent a delimiter that is itself `:`.

### Verdicts

| | |
| --- | --- |
| `PASS` | Complete, no duplicates, fields match where the mode exposes them |
| `FAIL` | A real discrepancy in **the tool output or this mode's `normalize.sh`** (the verdict does not distinguish them, and says so). **Completeness and field `FAIL`s are always preceded by a fresh reference re-list that confirms the bucket did *not* move**. The **one exception** is duplication-only: cross-shard/multiset duplicates can be identified without a re-list, because bucket drift cannot create duplicate emitted records. Both the single-receipt and `--scope union` paths follow this rule. |
| `DRIFT` | The bucket moved since the snapshot. **Stop — not a tool finding.** Only the orchestrator re-baselines. The mismatch re-list captures the full **5-field** record (`key/size/etag/mtime/storage_class`) with the manifest's exact canonicalization (`TZ=UTC`, ETag unquoted, mtime `+00:00`→`Z`) and compares full records — because an object replaced under the same key leaves the key set identical, and an *identical-byte* overwrite changes only `mtime`, which a key/size/etag-only check would miss and wrongly `FAIL`. |
| `ERROR` | The verifier could not run. **Not a pass.** |

### Credentialed mode is deliberately unimplemented

`--auth credentialed` hard-fails. The obvious implementation — mount `~/.aws`
read-only — hands every profile, SSO cache and `source_profile` chain on the box
to a third-party binary, when the brief calls for a **list-only identity scoped
to the registered buckets**, and a tool that ignores `AWS_PROFILE` reaches all of
it. The campaign is `CREDS=none`, so nothing needs this path; building it
approximately now, ready for a future `CREDS`, would be worse than its absence.
Implement a minimal materialised single-profile bundle before enabling.

### Footgun: `run.sh` argv is appended to the image `ENTRYPOINT`

`run.sh` prints *"the argv to execute inside the container"*, which reads like a
full command line. It is not. It is appended to whatever `ENTRYPOINT` the image
declares. Many tool images set the tool binary itself as the entrypoint — in
that case correct argv starts at the *subcommand*, not the binary name, and
prefixing the binary name produces a confusing failure on the first run.
Image-dependent and invisible until it bites. Check before writing `run.sh`:

```sh
docker inspect -f '{{json .Config.Entrypoint}}' <image>   # json form: prints null / ["/usr/local/bin/aws"] unambiguously
```

Use `--entrypoint` on the wrapper if a tool needs it overridden.

### Measurement boundary — adapters are never on the clock

The wrapper's wall-clock is the container's lifetime (`StartedAt→FinishedAt`,
from `docker inspect`). `normalize.sh`, `verify-listing.sh`, and all
post-processing run **after the clock stops**. This is an invariant, not an
implementation detail: adapter cost is the study's cost, not the tool's, and
it must never sit inside a timed window at smoke scale or in the benchmark
phase. Any future timing path that shells out to an adapter mid-window is a
defect.

### Bucket names are parameters, always

The owner's rule — *no executable artifact embeds a bucket name* — is
**enforced, not requested**: `smoke-run.sh` greps the tool's `run.sh` for the
bucket name and refuses to run if it finds it.

## Orchestrator-side staging — `stage-workspace.sh`

`stage-workspace.sh <tool>` stages a blind research agent's workspace at
`$S3_STUDY_SOURCES/<tool>-work` (default `<sources>`):

- accepts only the fixed runnable-subject set, refuses broad or repository-owned
  roots, enters the canonical staging directory with `cd -P`, and uses only
  fixed single-component names beneath that directory;
- takes an exclusive `flock` on the staging root so cooperating dispatches are
  single-writer;
- copies `harness/` and `docs/smoke-bucket.md`, and extracts the brief's
  **Part 2** as `BRIEF.md` (Part 1 is orchestrator-only);
- **refuses to stage from a dirty tree** — if `harness/`, the brief, or the
  registry have uncommitted changes, the staged copy would diverge from what
  the repo can show was dispatched;
- runs a **contamination check**: no *other* study subject may be named in
  anything a blind agent reads (it caught its first real leak the day it was
  promoted into `harness/`);
- writes `PROVENANCE.txt` recording the source commit;
- fully validates a sibling `.<tool>-work.new.*` generation before changing the
  stable name, retires any existing stable entry—including a symlink—under a
  unique `.<tool>-work.retired.*/workspace`, and publishes with same-directory
  rename-only `mv --no-copy --no-target-directory` operations.

The retirement and publication renames leave a short interval in which the
stable name is absent. A failed validation or rename leaves the unpublished and
retired generations in place and reports their paths; the script deliberately
performs no recursive deletion and has no cleanup trap. Inspect and reclaim
those paths explicitly, or reclaim them by disposing of the dedicated staging
filesystem/runner. This staging path requires `flock` (util-linux) and GNU
`mv` with `--no-copy` and `--no-target-directory` support.

Re-run it at **every** dispatch — staged copies go stale the moment the
harness changes. Companion rule (journal, 2026-07-17): the orchestrator also
archives the tool's external payload dir under
`<data>/receipts/` at every re-dispatch, or the anti-clobber
guard will refuse the fresh run.

## Changing the smoke bucket

Owner requirement (2026-07-17): a bucket change must be **script-driven, not
agentic**. The design already supports that — the per-tool `run.sh` +
`normalize.sh` adapters are parameterized by bucket/region/prefix, and
nothing tool-specific encodes the bucket (the scan gate enforces it). The
procedure:

1. **Update the registry** (`docs/smoke-bucket.md`): bucket, region, shape,
   key count, snapshot date. The registry is the single source every receipt
   binds to via `registry-lookup.sh` — nothing else needs editing.
2. **Re-baseline the reference manifest** with the pinned harness client:
   full 5-field listing (`key/size/etag/mtime/storage_class`), the
   canonicalization the verifier expects (`TZ=UTC`, ETag unquoted, mtime
   `…Z`), gzipped into `<data>/manifests/`, sha256 recorded in
   the registry.
3. **Re-smoke the finished tools as a scripted sweep**: for each tool, loop
   its `run.sh` modes through `smoke-run.sh` against the new registry entry.
   No agent, no research — the expensive agentic work (adapters, mode
   discovery) is already done and reusable by construction.
4. **One decision to take when it first happens**: the receipt layout for a
   second bucket (`receipts/smoke/` currently implies the registry bucket —
   e.g. `receipts/smoke-<bucket>/` keeps snapshots separable). Decide once,
   apply to all tools.

Receipts already committed stay valid evidence **for their snapshot** —
claims cite the bucket and manifest they were verified against; a new bucket
adds receipts, it does not invalidate old ones.

## The two memory numbers

Both are **sampled** (polled while the process is alive; interval recorded in
every receipt). The container's cgroup is destroyed the instant it exits, so
neither can be read post-mortem. Both are kernel-maintained high-water marks, so
a poll returns the true peak as of that read; the unmeasured window is between
the last poll and exit.

| | Counts | Does **not** count |
| --- | --- | --- |
| `peak_rss` (`VmHWM`) | Resident pages in the **main process's** address space, including shared/file-backed pages charged to another cgroup | Child processes — a fan-out mode's children are invisible; the receipt says so |
| `cgroup_peak_mem` (`memory.peak`) | Memory **charged to this container's cgroup**, whole tree: anon + page cache it caused + kernel/socket | Pages charged elsewhere, e.g. image layers already hot in host page cache |

**Neither bounds the other, and neither is a sanity check on the other.**
`peak_rss > cgroup_peak_mem` is normal on a box where the image is already hot in
page cache — observed here, stable across runs at a ~14 MB gap. An earlier draft
of the receipt template asserted `cgroup_peak_mem ≥ peak_rss` "always"; the first
run falsified it. See internal working notes (not published).

Never present `cgroup_peak_mem` as RSS. `peak_rss` is the field methodology
§ Run records (receipts) asks for.

## Known limits, carried to the benchmark phase

These are fine for smoke — which produces **no comparative numbers by design** —
and are recorded so they cannot silently graduate:

1. **Runner placement is not frozen for the benchmark.** Historical smoke used
   GCP `us-east1-b` against AWS `us-east-1`; that is a fact about those receipts,
   not the future campaign. Cross-internet latency changes the network-to-CPU
   ratio and may interact with client CPU, output back-pressure, throttling, and
   concurrency, so do not assume it preserves a ranking. Choose the benchmark
   location deliberately, keep it fixed within a campaign, and record it with
   CPU time and wall-clock in every receipt.
2. **The memory tail window.** Between the last poll and exit, unmeasured.
   Memory is a headline claim in the benchmark (the rclone exit-0-on-OOM
   allegation), so this may need revisiting — options are `getrusage` via an
   in-container wrapper (invasive: it changes the container under test) or a
   non-systemd cgroup (a parent slice was tried and rejected — it races
   systemd's GC; `memory.peak` reset is a silent no-op on this kernel).
3. **`peak_rss` measures the container's main process, whatever that is.** Not
   just a fan-out caveat: if an image's entrypoint is a shell that forks the tool
   **without `exec`**, the wrapper measures the idle shell and never sees the
   tool — potentially reporting a dramatically understated RSS that looks like a
   finding. Every receipt records the measured process name (`Measured process`);
   if it is `sh`/`bash` rather than the tool, the number is about the shell.
   Check it before quoting any memory figure.
4. **`PASS` does not re-list the reference.** The reference is re-listed only
   when a discrepancy is found. So a tool returning stale cached output that
   happens to match an outdated manifest exactly would `PASS`, and the drift
   would go unnoticed. Closing this means re-listing on every check — 149 pages
   per full-bucket verification, per mode, per tool. The brief's design is
   pre-flight once plus re-list on mismatch; changing it is an owner's
   methodology decision, not an implementer's.
5. **Aggregate concurrency ≤ 32 is not enforced by the wrapper.** The cap is on
   the product of concurrent invocations and each invocation's *internal* listing
   concurrency, which the wrapper cannot know — it is a per-mode fact the agent
   establishes by reading the tool's flags. Enforcement stays with the agent and
   the reviewer; the wrapper does not claim it.
6. **PID reuse** is theoretically possible over a long run; the poll loop keys on
   the container's main PID. Not observed, not defended against.
7. **`run.sh` runs on the host**, before the container boundary. It only prints
   argv, and the wrapper rejects credential-shaped argv, but a hostile `run.sh`
   is not defended against — consistent with the brief's threat model
   (accidental error, not a deliberately evasive agent).
8. **The 64 MiB payload cap bounds what we *keep*, not what Docker *materializes*.**
   The wrapper redacts and secret-scans the **full stream** before truncating (so a
   credential beyond the cap is flagged and **quarantined to `<out>/quarantine/`**,
   never silently dropped) and then keeps only the head — but `docker logs` has
   already written the entire stream to Docker's log store on disk before we read
   it, so a run emitting gigabytes can pressure Docker/TMP storage regardless of our
   cap. The bounding control is the **300 s timeout**, which caps how long a runaway
   can emit; the cap itself is about capture size, not disk safety. If a tool is
   expected to emit enormous output, watch disk during the run.
