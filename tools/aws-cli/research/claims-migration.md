# aws-cli claim-ledger migration

This is the human-auditable conservation map for the aws-cli capsule. It maps
every status-bearing row and status-bearing prose proposition from the
pre-restructure landing page ([`tool-page.md`](tool-page.md)) to the atomic
records in [`../data/claims.json`](../data/claims.json). Each legacy origin
appears in exactly one row below; a row may map to several atomic claims, and
one atomic claim may conserve propositions from more than one origin.

## Legacy source and status semantics

The legacy ledger is the inherited landing page's tables plus its two prose
findings. Its status legend maps to the canonical vocabulary as follows, and
the migration never promotes evidence:

- Legacy **CONFIRMED** becomes `confirmed` only when a committed receipt records
  the exact proposition. A legacy CONFIRMED whose only runtime evidence is an
  `[OBS]` `--debug` probe becomes `supported` (source plus observation), because
  one probe is not a receipted run of the proposition. This demotion applies to
  M1, M2, M3, and M4, whose "serial / single-thread / page-cap / no-parallelism"
  claims rest on source or documentation plus one s3api probe rather than a
  receipted run of the proposition.
- Legacy **CORRECTED** becomes `disposition: corrected` with the evidence-based
  status; MD4, T5, W4, and S3 carry that disposition.
- Legacy **VERIFIED: no** becomes `unverified` with a `none` reason (M5, N1, N2,
  N3, T3 sweep, T7 behavior, W2).
- Legacy **UNVERIFIABLE** becomes `unverifiable` with a `none` reason (MD5, a
  study-design tier assignment that is not a testable tool property).

## Labeling scheme

- The landing page's tables already carry typed labels, so those labels are
  kept verbatim: `MD1`-`MD6` (Metadata), `M1`-`M7` (Mechanism), `T1`-`T8`
  (Modes and tunables), `N1`-`N3` (Published numbers), `S1`-`S3` (Where the
  approach may fit), and `W1`-`W5` (Tradeoffs and questions to test).
- Two `PROSE` labels cover genuinely table-external propositions: `PROSE-1`, the
  "What we saw" headline that memory behavior splits by output format (naming
  the full streaming and buffering sets, beyond the S3 cell), and `PROSE-2`, the
  Errata that merge commit `85e561e` reverses that format-split finding. Both
  are conserved by `memory-format-split`; the commit-message erratum itself is
  derivation history preserved in the frozen tool page and this file, and is not
  reified as a separate tool claim.
- The remaining "What we saw" bullets purely restate table rows and get no
  separate label: "Serial pagination confirmed" restates M1/M2/M4; "manual
  prefix fan-out" restates T8/W1; "Resume primitive round-trips" restates
  M6/T4/W3; "`--output` has more members" restates T5/W4; and the closing
  "Scale-dependent observations remain unverified" restates M5/N1/N2/N3/W2.

## Split judgments

- MD4 splits the receipt-backed version self-report (`tested-version-is-2-36-1`,
  `confirmed`) from the source/doc commit pin (`tested-commit-is-pinned`,
  `supported`), because the version-help receipt records the version string but
  not the git SHA.
- M6/T4/W3 split the source-only "nothing is persisted" fact
  (`resume-token-not-persisted`, `supported`) from the receipted chunked-
  continuation round-trip (`resume-primitive-round-trips`, `confirmed`); no
  process was killed, so crash-resume stays unproven.
- M3 splits the documented server-side 1,000-key page cap
  (`server-caps-page-at-1000`, documentation) from the single `[OBS]` `--debug`
  probe that made three requests for a 2,549-key prefix
  (`probe-observed-three-requests-for-2549-keys`, observation); the legacy
  CONFIRMED M3 row rested on doc plus one probe, so neither half is a receipted
  run and both stay `supported`.
- T3 splits the source-backed `--page-size` mapping (`page-size-maps-to-maxkeys`)
  from the unrun above/below-1000 sweep (`page-size-ceiling-sweep`).
- T7 splits the source-backed defaults (`timeout-and-retry-defaults`) from the
  unrun fault-injection behavior (`timeout-retry-behavior-under-fault`).
- N3 conserves both the one-million-round-trips arithmetic (shared with M5) and
  the eight-hour estimate.
- S3 splits its two corrections of "nothing hidden": the buffering-versus-
  streaming memory split (`memory-format-split`) and the non-recursive `s3 ls`
  first-delimiter-level footgun (`s3-ls-nonrecursive-lists-first-level`).

## Conservation table

| Legacy origin | Conserved subject | Atomic claim IDs |
| --- | --- | --- |
| MD1 | Canonical repository identity | `upstream-repo-is-canonical` |
| MD2 | Language is Python | `language-is-python` |
| MD3 | License is Apache-2.0 | `license-is-apache-2` |
| MD4 | Pinned version and commit, corrected from unknown | `tested-version-is-2-36-1`, `tested-commit-is-pinned` |
| MD5 | Tier-1 study-design assignment | `tier-assignment-is-study-design` |
| MD6 | Trivial testability, ran unmodified | `runs-unmodified-in-official-image` |
| M1 | Both surfaces paginate ListObjectsV2 | `both-surfaces-paginate-listobjectsv2` |
| M2 | Single thread, one call outstanding | `serial-single-thread-one-probe`, `s3-ls-serial-by-source` |
| M3 | 1,000 keys per page | `server-caps-page-at-1000`, `probe-observed-three-requests-for-2549-keys` |
| M4 | No parallelism in either command | `no-listing-parallelism` |
| M5 | One billion objects is about one million round trips | `billion-objects-serial-roundtrips` |
| M6 | Manual resume; nothing persists the token | `resume-token-not-persisted`, `resume-primitive-round-trips` |
| M7 | Docs redirect large-scale listing to S3 Inventory | `docs-redirect-to-inventory` |
| T1 | s3 ls --recursive high-level listing | `s3-ls-recursive-full-run` |
| T2 | s3api list-objects-v2 low-level listing | `s3api-v2-text-full-run` |
| T3 | --page-size mapping and ceiling sweep | `page-size-maps-to-maxkeys`, `page-size-ceiling-sweep` |
| T4 | Resume flags and round-trip round-trip | `s3api-has-resume-flags`, `resume-primitive-round-trips` |
| T5 | --output format list correction | `output-formats-include-yaml-stream-and-off` |
| T6 | --no-sign-request anonymous access | `anonymous-via-no-sign-request` |
| T7 | Timeout and retry defaults and unrun behavior | `timeout-and-retry-defaults`, `timeout-retry-behavior-under-fault` |
| T8 | No concurrency mode; manual fan-out is the only path | `no-listing-parallelism`, `fanout-union-reconstructs-bucket` |
| N1 | 15M/1110s secondhand throughput | `throughput-15m-1110s-secondhand` |
| N2 | About 12M-object mid-run failure (issue 1118) | `twelve-million-midrun-failure-thirdparty` |
| N3 | One-billion-object round-trip and eight-hour estimate | `billion-objects-serial-roundtrips`, `billion-objects-eight-hour-estimate` |
| S1 | Universal, official, both arches | `universally-available-both-arches` |
| S2 | Simple surface with a memory caveat | `simple-surface-with-memory-caveat` |
| S3 | "Nothing hidden" corrected: buffering split and delimiter footgun | `memory-format-split`, `s3-ls-nonrecursive-lists-first-level` |
| W1 | No parallelism; only external fan-out | `no-listing-parallelism`, `fanout-union-reconstructs-bucket` |
| W2 | About 12M-object mid-run failure (issue 1118) | `twelve-million-midrun-failure-thirdparty` |
| W3 | Resume manual and easy to lose | `resume-token-not-persisted`, `resume-primitive-round-trips` |
| W4 | No Parquet; format list not exhaustive | `no-parquet-output`, `output-formats-include-yaml-stream-and-off` |
| W5 | Docs redirect large-scale listing to S3 Inventory | `docs-redirect-to-inventory` |
| PROSE-1 | Memory-format-split headline observation | `memory-format-split` |
| PROSE-2 | Errata: a merge commit reverses the format split | `memory-format-split` |

The validator compares this 34-origin set against every `legacy_origins` value
in [`../data/claims.json`](../data/claims.json) in both directions. Reviewers
should additionally compare this table with the preserved tables in
[`tool-page.md`](tool-page.md), because where a compound row or sentence splits
remains a human judgment.
