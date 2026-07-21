# Stage E — adversarial cross-model review (codex) + resolutions

Reviewer: `codex exec -m gpt-5.6-sol -c model_reasoning_effort=xhigh --sandbox
read-only` over `report.md`, `reconciliation.md`, every smoke receipt (+ external
payloads under `<data>/receipts/minio-mc/`), `run.sh`,
`normalize.sh`, and the uncommitted dossier edits (`git diff README.md`), with
`[SRC]` anchors re-checked against the pinned checkout (SHA
`7394ce0dd2a80935aded936b09fa12cbb3cb8096`). One round; no severe/blocker
findings, so no second round was required (BRIEF Stage E: re-run only on a severe
finding). minio-go `v7.0.90` anchors were correctly flagged **unverifiable
locally** (SDK not in the checkout) — informational, not failures.

Coverage the review did NOT perform: URL re-fetching for `[DOC]`/`[3P]` citations
(sandbox read-only) and the minio-go SDK anchors (absent from checkout) — both
noted by the reviewer; the SDK anchors remain an open external-verification item.

Verdict: **all Important and Minor findings accepted and fixed** (one adapter
correctness bug among them). Resolutions below; the review text is reproduced
verbatim under each.

## Important

### I1 — "no server-internal parallelism" correction unsupported (README, reconciliation)
> the dossier's "no server-internal parallelism" correction is unsupported … The source and trace establish only that `mc` issues no concurrent LIST requests; they say nothing about how AWS or MinIO internally serves each request.

**Accepted. Fixed.** Reworded the framing in `README.md` (Mechanism note),
`reconciliation.md` (M3), and `report.md` (§2 debug bullet) to claim only that the
**client** issues no concurrent LISTs / no keyspace fan-out, and to state
explicitly that server-internal request handling is neither confirmed nor refuted.

### I2 — `mc find` silently excludes GLACIER objects
> `mc find` silently excludes `GLACIER` objects … Pinned source unconditionally skips that storage class at cmd/find.go:304 … the PASS receipts cannot expose this completeness hole.

**Accepted — verified `[SRC cmd/find.go:304]` (`if content.StorageClass ==
s3StorageClassGlacier { continue }`).** A real completeness hole. Added the caveat
to `report.md` (§3 find row), `reconciliation.md` (T4), and `README.md` (find
note); the find PASS is now explicitly scoped to `STANDARD` objects (the smoke
bucket is all `STANDARD`, so it cannot exercise the hole).

### I3 — capability trace insufficient for wire-level conclusions; versions receipt misattributes the trace
> the capability trace is … a curated, unhashed excerpt without the response bodies, page counts … does not independently prove "1000/1000/549" … versions-json-hourly/receipt.md:106 says that this trace observed `GET ?versions`, but the trace invocation was ordinary recursive ListObjectsV2.

**Accepted. Fixed.** (a) Softened every use of the trace (`report.md` §2 and §8,
`reconciliation.md` M3) to: the excerpt evidences the serial continuation-token
chain and the missing `Authorization` header; the 1000/1000/549 split is
`[INFERRED]` from the 2,549-key count, not read off the trace. (b) Corrected the
`versions-json-hourly` receipt note — it no longer claims the trace observed
`GET ?versions`; the `?versions` shape is now `[INFERRED]` from source, and the
note states the trace was a recursive `list-type=2` run.

### I4 — memory observations misstated / overgeneralized
> report.md:99 says RSS/cgroup memory stayed about 35/16 MB for both 5 and 148,917 keys … The shallow receipt actually records 28.1/9.0 MB … does not establish flat memory or justify "not a concern".

**Accepted — receipt numbers confirmed (shallow 28.1/9.0 MB, full 35.4/16.1 MB).**
Fixed `report.md` §2 (cite both actual figures with receipt anchors) and §6
(replaced "not a concern / O(1)" with "bounded by design; smoke RSS 28–35 MB, no
growth with key count at smoke scale; scale-dependent behaviour not settleable
here").

### I5 — text `normalize.sh` can corrupt valid keys
> the storage-class allowlist at normalize.sh:69 omits current values including `FSX_OPENZFS` and `FSX_ONTAP` … normalize.sh:86 uses `lstrip(" ")`, deleting legitimate leading spaces from keys and violating the raw-key contract.

**Accepted — real adapter bug. Fixed.** (a) Replaced `lstrip(" ")` with consuming
**exactly one** separator space (and one after a recognised SC token), so genuine
leading spaces in a key survive (raw-key contract). (b) Broadened the SC set with
`FSX_OPENZFS`/`FSX_ONTAP` and documented that text-mode key parsing is inherently
best-effort/ambiguous (a key beginning `"<SC> …"` is indistinguishable from the SC
column) and that `*-json` modes are authoritative. Re-checked on the full
recursive payload: **148,917 keys, 0 diff vs manifest** — no regression. (The
committed `recursive` PASS receipt remains valid; NOAA keys have no leading spaces
and are all `STANDARD`, so behaviour is unchanged for this bucket.)

### I6 — W3 "Corroborated by source" exceeds its anchors
> W3 claims absence of Parquet across both `ls` and `find`, resume, and key-range sharding … cites only the `ls` flag list … an unavailable minio-go file for resume, and a bare `[SRC]` … for sharding.

**Accepted. Fixed.** Split W3's verdict in `reconciliation.md`: Parquet/sharding
absence Corroborated against **both** `ls` and `find` live `--help`; the resume
sub-claim (SDK-internal continuation token) rests on minio-go `api-list.go` which
is absent from the checkout → marked **Unaddressed-locally**, not receipt-backed.

### I7 — MinIO-server compatibility marked corroborated using only an AWS receipt
> reconciliation.md:29 covers both MinIO and generic S3/AWS but cites only the AWS smoke run … conflicts with the explicit admission … that no MinIO-server endpoint was exercised.

**Accepted. Fixed.** M1 in `reconciliation.md` now reads "generic-S3/AWS side
Corroborated; MinIO-server side Unaddressed"; the verdict counts and routed items
already carry the MinIO-server axis.

## Minor (all accepted and fixed)

- **M1 evidence-label invariant** — added labels to the "no built-in counter"
  claim (`report.md` §5) and a header note to §9 Notable findings stating each
  bullet's labels live in §2–§8.
- **M2 partial `[SRC]` anchors** — `--storage-class` now cites the client-side
  filter at `[SRC cmd/ls.go:244]`; `--rewind` splits the timestamp-parse anchor
  from the versions-API dispatch anchor; the version-group memory claim keeps its
  `[INFERRED]` label.
- **M3 pager footgun false** — verified pager is `app.HelpWriter`-only
  `[SRC cmd/main.go:525-526]`; listing goes via `console.Println`/`printMsg`
  `[SRC cmd/print.go:35]`. Corrected §4 (pager applies to help text, not listing).
- **M4 trailing-slash mischaracterized** — verified `[SRC cmd/ls-main.go:220-228]`:
  the no-slash form stats, recognises a directory, appends `/`, and lists anyway.
  Corrected §4 (the slash only saves a probe; both forms list).
- **M5 archive metadata / issue count** — `report.md` §1 now says
  `open_issues_count=47` (GitHub counts PRs as issues; ~35 issues + ~12 PRs); the
  "permanent / no future fix" wording is relabelled `[INFERRED]` and notes an
  archived repo can be unarchived (report §6/§9, reconciliation S2, README S2).
- **M6 pre-flight not bound to an artifact** — added
  `receipts/smoke/_capability/preflight/preflight.md` `[OBS]` recording the
  re-list command and the byte-identical `8b5b584…` hash match; `report.md` §8
  now cites it.
- **M7 receipt index incomplete** — `report.md` §11 now lists `find-hourly/`,
  `find-json-hourly/`, and `_capability/preflight/`.
- **M8 verdict count inconsistent** — `reconciliation.md` counts rewritten to
  handle split verdicts explicitly.
- **M9 channel description** — §2 corrected: mc's `contentCh` is unbuffered/cap-0
  `[SRC cmd/client-s3.go:1901]`; the cap-1 channel is minio-go's `objectStatCh`.

## Open items carried forward (not fixable in this phase)

- minio-go `v7.0.90` `[SRC]` anchors (pagination/retry/anonymous) are unverifiable
  against the checkout (SDK is a module dependency, not vendored). An independent
  anchor audit against the SDK at its pinned commit
  `68fb5ee339f2e3a798c14d12ca0e04c51f304d58` is the residual verification gap.
- `[DOC]`/`[3P]` URL citations were not re-fetched (sandbox read-only).

## Consolidation review

Reviewer: `codex exec -m gpt-5.6-sol -c model_reasoning_effort=xhigh --sandbox
read-only --add-dir /tmp/minio-go-audit`, one round, over the consolidation diff
(`git diff HEAD -- tools/minio-mc/{README.md,mechanism.md,running.md}`) against
`research/reconciliation.md` and the pre-consolidation README, hunting for
reconciliation rows with no destination, statuses changed without a receipt,
hypotheses softened beyond review-resolved wording, lost provenance, a weakened
archived-upstream finding, and cross-page contradictions.

**SDK-anchor gap closed here.** This run also discharged the residual gap the Stage
E review left open ("minio-go `v7.0.90` `[SRC]` anchors are unverifiable against the
checkout"): the SDK was cloned at the pinned commit
`68fb5ee339f2e3a798c14d12ca0e04c51f304d58` and every `[SRC minio-go]` anchor cited
in the three new pages (and in `research/report.md`) was re-verified by targeted
lookup. **Result:** the load-bearing anchors all support their claims — the
single-goroutine serial `listObjectsV2` loop (`api-list.go:100-165`), `max-keys`
set only if `maxkeys > 0` (`:224-227`), the V2 query parameters (`:191-222`),
`ListObjects` routing (`:771-789`), empty-cred → `SignatureAnonymous`
(`static.go:55-58`), and `MaxRetry=10`/`DefaultRetryUnit=200ms` (`retry.go:31,41`)
all check out. The review surfaced **anchor-precision** defects (below), which were
corrected in the three consolidated pages. Where the same imprecise anchor also
appears in the **immutable** `research/report.md`/`reconciliation.md`, it is flagged
as an erratum in the resolution and left unedited (research/ is read-only).

### Important findings (verbatim) + resolutions

**C1** — *"[README.md:10] and [mechanism.md:3] claim a completed consolidation
review, independently reverified SDK anchors, and '15 findings' from Stage E.
[codex-review.md:10] contains no consolidation review and explicitly leaves those
anchors unverified/open through [line 123]. It also records 16 Stage E findings—I1–I7
and M1–M9—not 15."*

Accepted. Fixed. Corrected the Stage E count to **16 (I1–I7, M1–M9)** in
`README.md` and `mechanism.md`. The forward references to "the consolidation review"
and "SDK anchors re-verified" are now true: this section is that review, and it
records the closed SDK-anchor gap above.

**C2** — *"[README.md:91] promotes reconciliation row 4 from 'Corroborated
(filled—editorial)' to receipt-backed `CONFIRMED`. The cited receipts do not measure
the version: [run.meta:27] explicitly says `tool_version_source=caller-supplied` …
No receipt binds the executed digest to release `RELEASE.2025…` or source commit
`7394ce0`. [README.md:92] similarly promotes the composite Tier/testability row
although receipts establish only that one arm64 image ran—not the `dl.min.io`
binaries or every advertised architecture."*

Accepted — a status changed without a receipt, exactly the hunt target. Fixed.
Ledger row 4 downgraded to **VERIFIED: no** (filled editorially; the pinned image
digest ran but its release/commit *identity* is caller-supplied, not
receipt-proven). Ledger row 5 downgraded to **VERIFIED: no** (corroborated; the
arm64 image ran to PASS, but the other arches and `dl.min.io` binaries were not each
exercised). Metadata table cells softened to match.

**C3** — *"[README.md:7] says every listing mode was smoked, directly contradicting
[running.md:103], which identifies `--incomplete`, `--rewind`, and `--zip` as
deliberately un-smoked. [running.md:54] also calls the ten receipts '10 modes,'
although its table repeats `recursive-json` at four scopes and represents seven
distinct mode names."*

Accepted. Fixed. README intro now reads "smoked its listing modes (10 runs across
seven modes)"; `running.md` § Every smoked mode now says "10 smoke runs … across
seven distinct mode names (`recursive-json` appears at four scopes)". The
`--incomplete`/`--rewind`/`--zip` un-smoked modes remain recorded in
`running.md` § "Recorded, not smoked".

**C4** — *"[README.md:20], [README.md:42], and [mechanism.md:87] strengthen 'server
default, ≤1000' into a client-hard-wired page size of exactly 1000. The cited
[api-list.go:224] only shows that `max-keys` is omitted when `maxkeys <= 0`; mc
hard-wires `MaxKeys=-1`, not 1000. The server supplies the effective default and may
return fewer than 1000."*

Accepted — a hypothesis hardened beyond its anchor. Fixed. All three loci now say
what is hard-wired is **`MaxKeys=-1`** (mc never sends `max-keys`), so S3 applies its
own server-default page size **≤1000** (1000 being the ceiling and the observed
value), and the server may return fewer. `mechanism.md` § heading and body rewritten
accordingly.

**C5** — *"[README.md:109] says the correction is 'there is no listing parallelism at
all, not merely server-internal.' That regresses the review-resolved scope and
contradicts both [README.md:95] and [mechanism.md:72] … [mechanism.md:63]
additionally calls seriality 'Confirmed' using only an `[OBS]` probe while the ledger
keeps it `VERIFIED: no`."*

Accepted — this is exactly the Stage E I1 wording being softened/regressed. Fixed.
The README summary sentence now states only that the **client** does no listing
parallelism / no keyspace fan-out and that the inherited "server-internal" framing is
set aside as unverifiable-here (server-internal handling neither confirmed nor
refuted), not replaced by a stronger negative. `mechanism.md:63` changed "Confirmed
serial at the wire" → "**Observed** serial at the wire (not receipt-promoted)" to
avoid clashing with the ledger's `VERIFIED: no`.

**C6** — *"[mechanism.md:142], [README.md:155], and [report.md:79] misstate the retry
anchor and count. `retry.go:31-42` establishes only `MaxRetry=10` and
`DefaultRetryUnit=200ms`; the exponential-jitter implementation begins at
[retry.go:48]. Moreover, [retry.go:81] yields ten total request attempts, so the
maximum is nine retries after the initial attempt—not 'retried up to 10 times.'"*

Accepted. Fixed in the two editable pages. `mechanism.md` now cites `retry.go:31`
(`MaxRetry`), `retry.go:41` (`DefaultRetryUnit`), and `retry.go:49-92` (the
`newRetryTimer` jitter implementation, `for i := range maxRetry`), and states each
request is **attempted up to 10 times total** (initial + up to 9 retries); the
source-anchors list matches. README § Retry hypothesis keeps "10×/200ms-jitter"
(10 attempts). **Erratum (immutable):** `research/report.md:79` carries the older
`retry.go:31-42` / "retried up to 10 times" wording — flagged, not edited (research/
is read-only).

**C7** — *"[mechanism.md:95] and [report.md:74] label V2 query parameters as applying
to 'every LIST.' [api-list.go:191] supports only `ListObjectsV2` … [mechanism.md:217]
and [report.md:128] cite `api-list.go:772` for `GET ?versions`, but that line merely
selects `listObjectVersions`; the actual `versions` query and GET are at
[api-list.go:543] through 588."*

Accepted. Fixed. `mechanism.md` query-parameter bullet now scopes to "every
`ListObjectsV2` request (the default object-listing path; `--versions`/V1 use
different query builders)"; the `--versions` modes-table row now cites routing
`api-list.go:771-773` and the versions query `api-list.go:543-588`. **Erratum
(immutable):** `research/report.md:74,128` carry the "every LIST" phrasing and the
`api-list.go:772` versions anchor — flagged, not edited.

**C8** — *"[README.md:122], [mechanism.md:153], and [report.md:93] claim the cited
outer guard emits the quoted 'S3 server is incompatible' error. In the pinned SDK,
[api-list.go:252] detects the same truncated-without-token condition first and
returns a different `NotImplemented` error, making the cited branch at lines 157–163
unreachable on this path. The broader no-loop finding holds, but the claimed emitted
message and anchor do not."*

Accepted. Fixed. Verified in the clone: `listObjectsV2Query` returns
`NotImplemented` "Truncated response should have continuation token set" at
`api-list.go:253-257` when `IsTruncated && NextContinuationToken == ""`, pre-empting
the outer `:157-163` backstop on the V2 path. `mechanism.md` and README now cite
`:253-257` as the **reachable** guard (with its actual message) and `:157-163` as a
defensive backstop; the no-infinite-loop guarantee is retained. **Erratum
(immutable):** `research/report.md:93` cites `:157-163` with the "S3 server is
incompatible" message — flagged, not edited.

**C9** — *"[README.md:105] retains W3's resume half as 'unaddressed,' even though the
same row says its formerly missing SDK anchor has now been reverified … The reason
for 'Unaddressed-locally' … —SDK absent—no longer applies. It should remain
`VERIFIED: no`, but be described as source-corroborated/inferred rather than
unaddressed."*

Accepted. Fixed. W3's resume half is now "**source-corroborated, no receipt**" —
the SDK anchor (`api-list.go:100-165`) is re-verified so the "Unaddressed-locally"
reason no longer applies, but with no interrupt/resume receipt it stays
`VERIFIED: no`.

**C10** — *"[README.md:22] loses the archived-upstream inference boundary in the most
prominent verdict, stating that the release is effectively terminal without
`[INFERRED]` or the unarchiving caveat. That qualification was explicit in the
pre-consolidation README at lines 84–86 and remains required by [reconciliation.md:53]
… leaving inconsistent provenance within the page."*

Accepted — a weakened archived-upstream finding, a hunt target. Fixed. The verdict
sentence now carries `[INFERRED]` and the "an archived repo *can* be unarchived —
maintenance-posture inference, not a permanence proof" caveat, matching the metadata
table, headline finding, and ledger row S2.

### Minor findings (verbatim) + resolutions

**C11** — *"[README.md:133] says the benchmark hypotheses were carried forward
'verbatim,' but [README.md:146] softens the report's 'much faster' to 'faster,' and
the consolidation omits the report's common-denominator-architecture item at
[report.md:400]."*

Accepted. Fixed. Dropped the "verbatim" claim (now "carried forward … with
provenance"); restored "**much faster**" in the serial-throughput item; added the
common-denominator-arch item (report § 10 item 4) as open hypothesis 7.

**C12** — *"[README.md:55], [mechanism.md:130], and [report.md:150] cite only
`api.go:905` for returning an anonymous request unsigned. [api.go:905] is merely the
conditional; the unsigned return is at line 911 … The anchor needs to cover
`api.go:904-912`."*

Accepted. Fixed. README and `mechanism.md` now cite `api.go:904-912` for the
anonymous unsigned-return path. **Erratum (immutable):** `research/report.md:150`
cites `api.go:905` — flagged, not edited.

### Disposition

All 10 Important and 2 Minor findings accepted and fixed in the three consolidated
pages. Four findings (C6, C7, C8, C12) also implicate anchors in the immutable
`research/report.md`/`reconciliation.md`; those are flagged as errata above and left
unedited per the immutability rule — the corrected anchors live in the consolidated
pages. No reconciliation row was left without a destination; no status was promoted
past `VERIFIED: no` without a receipt after the C2 fix.
