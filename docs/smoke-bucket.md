# Smoke-bucket registry

The historical bucket registry for capsule-groundwork smoke receipts. It records
the bucket and manifest identities those immutable receipts used; it is not a
current benchmark plan. Current comparative inputs live under
[`../benchmark/plans/`](../benchmark/plans/).

[`data/registry.toml`](../data/registry.toml) was the machine-readable source for
the retired groundwork harness. It is retained with these tables as historical
capsule evidence; no current benchmark component resolves bucket facts from it.
Current target declarations live under
[`benchmark/plans/`](../benchmark/plans/), and current execution and comparison
behavior is documented in [`benchmark/README.md`](../benchmark/README.md).

The deleted harness was designed to take anonymous snapshots with the pinned
client, compare smoke output with the recorded manifest, and stop on detected
drift. Historical engine attempts did not produce verifier verdicts, so those
intended controls must not be read as proof that every recorded attempt was
verified. The current benchmark neither consumes this manifest nor implements
that pre-flight/mid-campaign drift gate; its verifier compares completed
attempts and explicitly treats agreement as different from ground truth.

## Historical harness client

| | |
| --- | --- |
| Image | `amazon/aws-cli@sha256:eb85b2c72442c9eab0bdbe608095b9b909bc2a7136924124d63fe0c03b2ec334` |

Historical invocation shape (evidence, not current instructions):

```sh
docker run --rm --network s3-listing-study-subjects \
  --cap-drop ALL --security-opt no-new-privileges:true \
  -e AWS_EC2_METADATA_DISABLED=true -e TZ=UTC \
  <image> s3api list-objects-v2 --bucket <b> --region <r> \
  --no-sign-request \
  --query 'Contents[].[Key,Size,ETag,LastModified,StorageClass]' \
  --output text
```

**Historical canonicalization design.** The API returns ETags wrapped in literal
quotes; the deleted pipeline stripped them before writing TSV. `LastModified` came back as
`YYYY-MM-DDTHH:MM:SS+00:00` (the container runs `TZ=UTC`, so it is genuinely
UTC); the pipeline rewrote the trailing `+00:00` to `Z`, giving the
contract-v2 canonical `YYYY-MM-DDTHH:MM:SSZ`. Snapshot, pre-flight, and
mismatch re-list were designed to use this exact pipeline — same image, same
query, same canonicalization. The retired mismatch re-list was intended to
capture the full five-field record.

## Primary: `noaa-normals-pds`

| | |
| --- | --- |
| Bucket | `noaa-normals-pds` (AWS Open Data — NOAA U.S. Climate Normals; sponsor-paid requests) |
| Region | `us-east-1` |
| Access | Anonymous (`--no-sign-request`) in the recorded 2026-07-17 capsule smoke; no current verifier verdict is implied. |
| Manifest | `<data>/manifests/noaa-normals-pds.2026-07-17.tsv.gz` — `key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class` (contract v2), ETag unquoted, mtime `YYYY-MM-DDTHH:MM:SSZ` UTC. |
| Manifest sha256 | `c78a82737dd1982a999912afa89f870c013cb22e01e50b8c4835ddb725992adb` |
| Snapshot date | 2026-07-17 (UTC) |
| Keys | 148,917 — the historical snapshot's recorded strict-byte-order count |

**Data artifacts never enter the repo.** The manifest sha256 in the table is
the binding, and the artifact is published as an immutable release asset when
the repo goes public.

> **History.** The 2026-07-16 snapshot (`noaa-normals-pds.tsv.gz`, sha256
> `07e8e189…785a12`, three fields `key/size/etag`, 148,917 keys) is
> **superseded** by the contract-v2 re-baseline above (2026-07-17,
> five fields adding `mtime`/`storage_class`). The bucket did **not** drift:
> the new manifest's key/size/etag columns are byte-identical to the old
> snapshot (148,917 → 148,917, no delta); the re-baseline only widens the
> captured field set. The old file is retained in the manifests directory,
> orphaned by design (receipts bind to the registry digest, so no v1 receipt
> can verify against the v2 registry).

> **Observed drift, 2026-08-02.** A live anonymous re-list of this bucket,
> compared against the 2026-07-17 snapshot recorded above: the **key set is
> unchanged** — 148,917 keys returned, zero duplicates, all unique — but
> **129,227 of 148,917 objects (87%) now report a `last_modified` later than
> the snapshot date**, the newest `2026-07-22T13:20:38Z`. So the
> key/size/etag columns appear stable while the `mtime` column is stale for
> most of the bucket: the identical-byte-overwrite case the retired verifier's
> `DRIFT` verdict covered. **Consequence:** a contract-v2 5-field verification against the
> current manifest would report mass `mtime` mismatches that belong to the
> bucket, not to any tool. The snapshot figures above are deliberately **not**
> edited — this note is an observation about them. **Re-baselining has not
> been done and remains the owner's call**.
>
> Recorded at the same time: **the manifest artifact is not present on a fresh
> machine.** It lives outside the repo by the no-data-in-repo rule above, so a
> clone alone can verify nothing until the artifact is fetched — a distinct
> prerequisite distinct from subject execution, and one that cost this run real
> time before it was understood. See
> [`operating/artifact-availability.md`](operating/artifact-availability.md).

### Measured shape

- **Top level**: 4 prefixes + 1 root-level key —
  `normals-monthly/` 48,796 · `normals-daily/` 48,787 ·
  `normals-annualseasonal/` 48,784 · `normals-hourly/` 2,549 · `index.html` 1.
- **Depth histogram** (`/`-count): depth 0 → 1 key, depth 2 → 29,986,
  depth 3 → 118,930. No deep nesting; the tree is broad and shallow.
- **Largest second-level prefixes**: `normals-monthly/1991-2020/` 15,625 ·
  `normals-daily/1991-2020/` 15,624 · `normals-annualseasonal/1991-2020/`
  15,623 · the `2006-2020/` trio ≈ 13,480 each · the `1981-2010/` trio ≈
  9,840 each.
- ≥149 LIST pages un-delimited at the 1,000-key page cap.

### Designated scoped-check prefixes

| Prefix | Keys | Why |
| --- | --- | --- |
| `normals-hourly/` | 2,549 | Small top-level branch — cheap scoped run (~3 pages) |
| `normals-monthly/1991-2020/` | 15,625 | Large dense second-level prefix (~16 pages) |
| `normals-annualseasonal/1981-2010/access/` | ≤9,841 | Depth-3 leaf directory |

Root-level `index.html` sits beside the four prefixes — useful for
delimiter-mode checks (4 CommonPrefixes + 1 Key expected at `/`, no prefix).

## Optional edge-case fixture: not seeded

`EDGE_BUCKET=none` for all agents until the owner seeds the fixture defined
in the brief (§ The optional edge-case fixture). Until then every agent
records unicode/weird-key/multipart-ETag checks as **deferred**.
