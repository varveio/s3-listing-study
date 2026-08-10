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
> observation rather than receipt: its runs were made outside the retired
> wrapper-era evidence path, because the runner-security profile was not
> provisioned. Historical
receipts remain immutable while their subject stands: they are never edited,
and are removed only when the whole subject is retired under
[`tool-structure.md`](tool-structure.md) § Lifecycle — as Swath's were, above.
Missing artifacts must be recovered and published,
or explicitly waived with the dependent claim kept qualified; they are never
recreated and presented as the bytes from an old run.

## Receipt inventory

The tree contains 74 `receipt.md` files. Seventy-three are standard
wrapper-era records with sibling `run.meta`; one is the separate pS3
build-attempt receipt. Of the 73 wrapper records, 57 have an ordinary
`verify.md`, and two fan-out groups have `union-verify.md` records. The remaining
wrapper records are blockers, capability/debug probes, union shards, or
procedures for which ordinary single-run completeness verification does not
apply.

Payload paths in the 73 historical `run.meta` records fall into four classes:

| Stream | Portable repo-root path | Relative path with undeclared tool-root base | Absolute historical path with matching bytes also committed | Absolute external path whose bytes are not in this clone |
| --- | ---: | ---: | ---: | ---: |
| stdout | 10 | 3 | 7 | 53 |
| stderr | 49 | 3 | 21 | 0 |

This is why the audit also describes 60 stdout and 21 stderr references as
machine-local: those metadata fields contain absolute historical paths. Seven
of those stdout streams and all 21 stderr streams have matching committed copies,
but the old pointer itself is not portable. The three relative-path records are
the two s3kor capability runs and the s4cmd capability run; their paths resolve
from the tool root and their available bytes match the recorded hashes, but
`run.meta` did not declare that base.

All 93 payload copies resolvable from this clone across those categories were
re-hashed during this inventory and matched their recorded SHA-256 values. This
is an availability result, not proof about the 53 absent stdout streams.

The historical smoke manifest is likewise recorded by path and SHA-256 but is
not committed. Existing receipts remain bound to that digest; the registry is
closed for new execution after observed bucket drift. Publication needs the
original manifest and the 53 absent stdout streams, not a fresh listing passed
off as the old artifact.

## Historical receipt errata

- Fifty-five historical receipts describe an external stdout/stderr stream as
  "published as a release asset at publication." That line is a forward-looking
  statement from when the receipt was written, not a claim that the asset exists
  today: the release payloads are **not yet published** (see the Public evidence
  gate below and the 53 absent stdout streams in the inventory). Receipt bytes
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
| Swath | **Public upstream digest reference** (`ghcr.io/varveio/swath@sha256:ef1aca9ab473f133acceb5730ff88d52abaaa89e773801cdb62deff51f9909b0`, pulled anonymously) | Not needed | Registry bytes identified by digest (committed observation); an unreceipted observation found the `org.opencontainers.image.revision` label equal to the tested commit |
| s3p | Local-only study image | Yes | No; dependency closure is not locked |
| s3kor | Local-only study image | Yes | No exact-byte rebuild demonstrated |
| s4cmd | Throwaway local-registry image | Yes | No exact-byte rebuild demonstrated |
| pS3 | Local-only study image | Yes | No; it packages a shipped binary that the available source cannot rebuild |

A digest is still the correct identity for what ran. It is not proof that a
reader can retrieve that image or regenerate it from current package indexes.

## Rebase onto a uniform glibc base (2026-08-10)

The table above describes provenance as it stood for the receipts recorded
before this date. Three subjects have since moved: **rclone**, **s5cmd** and
**s3kor** ran on Alpine images and now run on the study's pinned
`debian:12-slim` base.

The reason is not preference. The payload carries its own CPython, which links
the base image's libc, and compiled Python wheels are published per libc.
DuckDB — which every `normalize.py` adapter imports — publishes manylinux
wheels only, with no musllinux build at any version. On a musl base the worker
cannot run the study's own code, so the subject's packaging was deciding the
harness runtime.

What this does and does not change:

- **s3kor** is a study build already; only its runtime stage changed base. Same
  source commit, same `CGO_ENABLED=0` build stage.
- **rclone** and **s5cmd** move from a public upstream *image* reference to
  upstream's published *release archive*, fetched by URL and verified by digest
  at build time (`ADD --checksum`), then placed on the base. Nothing is rebuilt
  from source.

  **The subject changed, and the change is recorded rather than absorbed.** The
  binary in a vendor's image is not the binary in their release archive:

  | Tool | In upstream's image | In upstream's release archive |
  | --- | --- | --- |
  | rclone | `d67c485534687d1f2d5fbe467104a8c9f82cc491796f9cd13acc33100852527f` | `9f56ca5edfac24a3ed37226c2ba1de69f1ec9e05fa2526cddee5cd97e202be6b` |
  | s5cmd | `6a645f4f53ffe03911e531586c167b35e36e2d33e0f10a9404cb1f665eeaaa98` | `672299fea8941281702bd52a4e51c330a4e39c1540f4bdfc3b4e737823ac2878` |

  Both pairs self-report the same version (`rclone v1.74.4`,
  `s5cmd v2.3.0-991c9fb`), so these are the same releases built in different
  environments — not different versions. Receipts, research reports and tool
  pages recorded before this date describe the *image* binary; anything citing
  them for rclone or s5cmd is citing a different artifact from the one now
  registered.

  The release archive is preferred because it is content-addressed and a reader
  can fetch and verify it directly, without pulling and unpacking a container
  image to reach the bytes. Both are static Go binaries, so the base supplies
  nothing but a kernel and the CA bundle the recipes install. Each returned
  2,549 rows against `noaa-normals-pds/normals-hourly/` after the move — the
  same count the other nine subjects return.

The cost is that two subjects no longer run upstream's published image, and
their pre-2026-08-10 evidence describes a different binary. The gain is that
every worker can run the study's code, that the registered artifact is one a
reader can verify by digest from upstream's own download, and that libc,
malloc, the DNS resolver and the TLS stack stop varying between subjects inside
a comparison whose premise is that only the tool differs.

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
