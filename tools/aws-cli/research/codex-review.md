# Stage E — adversarial cross-model review (codex) + resolutions

Independent read-only review by **codex `gpt-5.6-sol`** (reasoning effort high,
`--sandbox read-only`), run over the aws-cli groundwork on branch
`groundwork/aws-cli`. The pinned checkout SHA
(`12d962d239b9fd0669951c4d27dc366388abba2d`) was confirmed by the reviewer before
any `[SRC]` anchor was trusted.

> **Process note.** The first two invocations of `codex exec` derailed: with the
> repo's `review`/`codex-review` **skills** present under `~/.codex/skills/`, codex
> tried to follow them and dispatch its own sub-reviewers (which can't write logs
> under the read-only sandbox) instead of reviewing directly. The third invocation
> — with an explicit "you ARE the reviewer; do not load skills, do not spawn
> sub-agents, do not invoke another CLI" directive and stdin from `/dev/null` —
> ran cleanly and produced the findings below. Citation re-fetching over the
> network was not required; all anchor checks were performed locally against the
> pinned checkout.

**Overall reviewer verdict: NEEDS-WORK** — 4 major, 7 minor, 1 nit. All addressed
below; every major fixed.

---

## Findings (verbatim) + resolutions

### 1. [major] report.md — `--output text` is not buffered; text streams
> The report incorrectly says `--output text` uses `FullyBufferedFormatter`, that
> `--query` forces full-result buffering, and that `yaml-stream` is the only
> streaming `s3api` format. Pinned `awscli/formatter.py:330-355` shows
> `TextFormatter(Formatter)` iterating `for i, page in enumerate(response)` and
> formatting each page independently. Only JSON, table, and YAML inherit
> `FullyBufferedFormatter`; this invalidates several memory-model conclusions and
> the proposed format comparison.

**Verdict: correct — FIXED.** Confirmed in source: `TextFormatter(Formatter)`
`[SRC formatter.py:330-355]` streams per page; only `JSONFormatter` (:94),
`YAMLFormatter` (:141), `TableFormatter` (:197) inherit `FullyBufferedFormatter`
(:65/:76); `--query` on a streaming formatter is applied per page via
`result_keys` (:337-346). Corrected everywhere the memory model appears: report
§2 (memory model), §3 (modes table: `s3api-v2-text` → **streamed**), §3 (tunable
`--output`), §4 (footgun), §6 (failure surface), §9 (notable finding), §10 (open
question 1). The streaming set is now **`s3 ls` + `--output text` + `yaml-stream`**;
the buffered set is **`--output json`/`yaml`/`table`**.

### 2. [major] "serial single-threaded" promoted too broadly to CONFIRMED (smoke)
> `_capability/README.md` records only one three-page `s3api list-objects-v2`
> invocation. The ordinary PASS receipts verify completeness, not request
> concurrency, and there is no corresponding `s3 ls --debug` receipt. Source
> corroborates synchronous paginator loops, but the committed run evidence does
> not smoke-confirm both surfaces or "regardless of flags."

**Verdict: correct — FIXED.** The dossier's `Verification status` now splits this:
anonymous access / fan-out / resume stay **CONFIRMED (smoke)**; serial pagination
is downgraded to **"CONFIRMED by source + one probe (not broadly smoke-proven)"**,
stating the `--debug` probe is one s3api invocation and `s3 ls` serial-ness rests
on `[SRC subcommands.py:852,865]`. Reconciliation M1/M2/M4 changed from "Settled
by smoke" to "Corroborated", each noting that every-mode PASS proves completeness,
not concurrency, and that "regardless of flags" rests on source.

### 3. [major] resume probe overstated as crash/interruption resume
> No process was killed: leg 1 stopped cleanly with `--max-items 1000`, which
> caused a `NextToken` to be emitted, and leg 2 consumed it. A normally
> interrupted unbounded invocation does not persist or expose each page's token.
> The receipt proves deliberate chunk continuation, not recovery after process
> death.

**Verdict: correct — FIXED.** Relabeled throughout as **chunked continuation**,
explicitly *not* the dossier's kill-and-resume test: report §8 resume paragraph,
dossier CONFIRMED bullet, reconciliation M6/T4/W3. Each now states no process was
killed and that a killed *unbounded* run emits no token to save — so the "lost if
it dies" half rests on `[SRC paginate.py:155-165]`, not the probe.

### 4. [major] normalize.sh `s3-ls-delimiter` wrong for non-`/`-terminated prefixes
> For prefix `foo` and returned key `foobar.txt`, it emits `foofoobar.txt`; a
> common prefix `foobar/` is corrupted similarly. The root-only smoke receipt does
> not exercise this path.

**Verdict: correct — FIXED.** `_display_page` prints only the last path component
(`Key.split('/')[-1]`, `Prefix.split('/')[-2]`) `[SRC subcommands.py:865-889]`, so
the adapter now prepends the **directory portion of the prefix** (up to and
including its last `/`, empty if none), not the whole prefix. Verified: root run
output unchanged (its committed PASS stands), and a synthetic `prefix=normals-hourly/1981`
case now reconstructs `normals-hourly/1981-2010/` and `normals-hourly/leaf.txt`
correctly (previously would have doubled the stem).

### 5. [major] shard `verify.md` untracked; receipt edits unstaged
> All three shard verifier files are untracked (`??`) … a commit of the current
> index would omit the cited verifier evidence entirely.

**Verdict: correct — FIXED.** A staging artifact from an early `git add -A`. The
final commit runs `git add -A` after all edits, so the three
`fanout/shard-*/verify.md` files and every receipt-verdict edit are included. Diff
verified before commit (see the commit's file list).

### 6. [minor] S3-Inventory redirect: DOC citation doesn't support it
> Inventory guidance comes from the source classified by this report as `[3P]`.

**Verdict: correct — FIXED.** Reconciliation M7/W5 now attribute the Inventory
redirect solely to `[3P]` AWS re:Post; the `[DOC]` CLI user-guide is cited only for
the narrower "pagination knobs are the in-CLI lever" point, not as the Inventory
source.

### 7. [minor] "S3 returns keys lexicographically" over-reaches its anchor
> `[SRC subcommands.py:862-889]` supports "no client-side sort" but not "S3 returns
> keys lexicographically"; directory-bucket ordering is not lexicographic.

**Verdict: correct — FIXED.** Report §2 Ordering now: `[SRC]` supports only
no-client-sort; UTF-8 binary ordering is attributed to `[DOC ListObjectsV2]` and
scoped to **general-purpose** buckets (directory/S3-Express excluded); the smoke
match is noted as confirming completeness/fields, not ordering.

### 8. [minor] receipt metadata inconsistent (duplicated prefix; wrong adapter mode)
> prefix values are duplicated (`normals-hourly/normals-hourly/`), and the claimed
> adapter modes include receipt suffixes such as `s3api-v2-json-hourly`, which
> `normalize.sh` does not accept.

**Verdict: partly mine, partly harness — FIXED (mine) + FLAGGED (harness).** The
wrong adapter mode was in *my* appended `## Verifier verdict` section (I used the
receipt-directory name); corrected to the real `run.meta` mode across all receipts.
The `Prefix scope | \`x\`x` duplication is in the **wrapper-generated** `## Bucket`
block — a `smoke-run.sh` receipt-template bug (it renders the prefix twice); the
machine-readable `run.meta prefix` is correct and single. I did **not** hand-edit
wrapper output (that would falsify "a receipt records what ran"); **flagged to the
orchestrator** as a harness defect (see handoff).

### 9. [minor] remainder receipt: Result-table verdict vs final section contradiction
**Verdict: correct — FIXED.** The remainder's Result-table verdict cell now reads
"**PASS (union shard)** — verified only in `../union/union-verify.md`", and the
verdict section explains the remainder is exempt from single-receipt scope by
design (no standalone `verify.md`).

### 10. [minor] pre-flight `8b5b584e…` byte-identity unlabelled / no receipt
**Verdict: correct — FIXED.** Added `receipts/smoke/_build/preflight.md` recording
the harness-client re-list, both sorted sha256 values (`8b5b584e…`), the verified
registry digest, and the "no drift" verdict; report §8 now cites
`[RUN _build/preflight.md]`.

### 11. [minor] "`s3 ls` ignores `--output` and `--query` (documented)" over-cites
> That source documents only `--output` and `--no-paginate` as ignored; not
> `--query`.

**Verdict: correct — FIXED.** Report §4 now says `s3 ls` ignores `--output`
(documented in its help `[SRC subcommands.py:786-788][RUN help]`) and separately
that it does not honour `--query` — marked `[INFERRED]` from the fixed high-level
formatter, not documented.

### 12. [nit] open-question 5 duplicated
**Verdict: not reproduced — NO CHANGE.** The current §10 list is numbered 1–6 with
no duplicate (Architecture denominator is the sole #5). The cited line numbers
(380-381) predate this phase's edits, which shifted the section; likely a stale
line reference. Left as-is.

---

## Round 2

No second codex round was run: every **major** finding was a concrete,
locally-verifiable correction (source anchor, adapter logic, scoping, staging),
all fixed and re-checked against the pinned source and receipts rather than
re-argued. The one item left unfixed (F8's harness template duplication) is a
shared-infrastructure defect outside this branch's scope, flagged for the
orchestrator; the one item not reproduced (F12) has no defect to fix.
