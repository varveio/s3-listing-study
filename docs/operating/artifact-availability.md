# Artifact availability

This page records which groundwork evidence a fresh clone can actually resolve.
Identity and availability are different: a digest can identify exact bytes even
when those bytes are not published, and a Dockerfile can describe a build
without reproducing the historical image byte-for-byte.

**Status (2026-08-02): receipts are committed and internally hash-consistent,
but the complete public evidence package is not yet available.**

> **Swath's subject was retired on 2026-08-02.** Its capsule was retargeted from
> a v0.1.0-era subject to v0.2.0, and the earlier subject — never released — was
> retired wholesale under the subject-retirement rule in
> [`tool-structure.md`](tool-structure.md) § Lifecycle: its receipts, derivation
> records and migration stratum were removed together, and no claim cites them.
> Every count on this page is stated after that removal, and the figures below
> therefore differ from the 2026-07-20 wave totals. Swath's current evidence is
> observation rather than receipt: its runs were made outside
> `harness/smoke-run.sh`, because the runner-security profile was not
> provisioned. Historical
receipts remain immutable while their subject stands: they are never edited,
and are removed only when the whole subject is retired under
[`tool-structure.md`](tool-structure.md) § Lifecycle — as Swath's were, above.
Missing artifacts must be recovered and published,
or explicitly waived with the dependent claim kept qualified; they are never
recreated and presented as the bytes from an old run.

## Receipt inventory

The tree contains 74 `receipt.md` files. Seventy-three are standard
`harness/smoke-run.sh` records with sibling `run.meta`; one is the separate pS3
build-attempt receipt. Of the 73 wrapper records, 57 have an ordinary
`verify.md`, and two fan-out groups have `union-verify.md` records. The remaining
wrapper records are blockers, capability/debug probes, union shards, or
procedures for which ordinary single-run completeness verification does not
apply.

Payload paths in the 85 historical `run.meta` records fall into four classes:

| Stream | Portable repo-root path | Relative path with undeclared tool-root base | Absolute historical path with matching bytes also committed | Absolute external path whose bytes are not in this clone |
| --- | ---: | ---: | ---: | ---: |
| stdout | 12 | 3 | 7 | 63 |
| stderr | 61 | 3 | 21 | 0 |

This is why the audit also describes 70 stdout and 21 stderr references as
machine-local: those metadata fields contain absolute historical paths. Seven
of those stdout streams and all 21 stderr streams have matching committed copies,
but the old pointer itself is not portable. The three relative-path records are
the two s3kor capability runs and the s4cmd capability run; their paths resolve
from the tool root and their available bytes match the recorded hashes, but
`run.meta` did not declare that base.

All 107 payload copies resolvable from this clone across those categories were
re-hashed during this inventory and matched their recorded SHA-256 values. This
is an availability result, not proof about the 63 absent stdout streams.

The historical smoke manifest is likewise recorded by path and SHA-256 but is
not committed. Existing receipts remain bound to that digest; the registry is
closed for new execution after observed bucket drift. Publication needs the
original manifest and the 63 absent stdout streams, not a fresh listing passed
off as the old artifact.

## Historical receipt errata

- Sixty-five historical receipts describe an external stdout/stderr stream as
  "published as a release asset at publication." That line is a forward-looking
  statement from when the receipt was written, not a claim that the asset exists
  today: the release payloads are **not yet published** (see the Public evidence
  gate below and the 63 absent stdout streams in the inventory). Receipt bytes
  are immutable and are not edited, so this page is the authoritative record that
  the promise is currently unfulfilled. A reader who cannot resolve a receipt's
  external stream should treat it as an absent-payload exception, not an error.
- Twenty-six human `receipt.md` files duplicate the prefix text in the
  `Prefix scope` cell. Their `run.meta`, payload hashes, and verifier outcomes
  are unaffected. The current renderer is fixed; immutable historical receipt
  bytes are not edited.
- Three capability receipts use tool-root-relative payload paths without
  declaring the base, as described above. Future wrapper records declare
  `payload_path_base=run-meta-directory` and write inline stream paths relative
  to the sibling `run.meta`. The verifier retains legacy behavior only for old
  records without that field.

## Container-image availability

Every wrapper receipt pins an image digest. Four subjects used public upstream
image references; seven used images built or materialized locally. This table
describes the recorded provenance, not a new registry pull or rebuild test.

| Tool | Historical image availability | Tracked build recipe | Exact-byte rebuild established? |
| --- | --- | --- | --- |
| aws-cli | Public upstream digest reference | Not needed | Registry bytes identified by digest |
| s5cmd | Public upstream digest reference | Not needed | Registry bytes identified by digest |
| rclone | Public upstream digest reference | Not needed | Registry bytes identified by digest |
| minio-mc | Public upstream digest reference | Not needed | Registry bytes identified by digest |
| s7cmd | Local-only image | No recipe in this repository | No |
| s3-fast-list | Local-only image | Yes | No; dependency/toolchain closure differs from upstream |
| Swath | **Public upstream digest reference** (`ghcr.io/varveio/swath`, pulled anonymously) | Not needed | Registry bytes identified by digest; the image's `org.opencontainers.image.revision` label equals the tested commit, so the source-to-image binding is embedded rather than merely recorded |
| s3p | Local-only study image | Yes | No; dependency closure is not locked |
| s3kor | Local-only study image | Yes | No exact-byte rebuild demonstrated |
| s4cmd | Throwaway local-registry image | Yes | No exact-byte rebuild demonstrated |
| pS3 | Local-only study image | Yes | No; it packages a shipped binary that the available source cannot rebuild |

A digest is still the correct identity for what ran. It is not proof that a
reader can retrieve that image or regenerate it from current package indexes.

## Public evidence gate

Before calling the evidence package reproducible from a fresh clone:

1. Choose an artifact host and retention period with immutable,
   content-addressed names.
2. Recover and publish the historical manifest and every required external
   payload. If bytes cannot be recovered, record the exception and keep any
   dependent claim explicitly limited.
3. Publish a machine-readable asset index mapping each digest to URL, size,
   media type, tool/mode/snapshot, and receipt.
4. Export each required local-only OCI image, or prove a closed rebuild that
   produces the relied-on artifact. Keep image identity, retrievability, and
   rebuildability as separate fields.
5. From a clean clone, fetch every indexed artifact, verify every digest, check
   every receipt reference, and fail the release if any required byte is absent.

New runs use a new snapshot namespace and the current path-base contract. They
add evidence; they do not repair or overwrite historical receipts.
