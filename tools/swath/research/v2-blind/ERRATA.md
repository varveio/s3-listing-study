# Errata — v2-blind derivation record

`report.md` and the `reader-*.md` files are frozen derivation records: they are
preserved as written so the derivation can be audited, and defects found later
are recorded here rather than edited into them.

## E1 — Five source anchors cite line numbers past the end of their files

Found while deriving `data/claims.json` from the report, and independently
confirmed against the pinned checkout at `cef8ec2`.

| Cited in report | File length at `cef8ec2` |
| --- | --- |
| `Json.java:85-107` | 38 lines |
| `AlignedFormatter.java:123-124`, `:135-166`, `:168-179` | 84 lines |
| `RowTally.java:141-144` | 60 lines |
| `IncludeRegexFilter.java:114-116` | 30 lines |
| `Formatters.java:134-142` | 25 lines |

All five originate in reader D's area (output formatting and filters). **The
propositions themselves are correct** — each was re-derived from the pinned
checkout and the corrected ranges are what `data/claims.json` cites. Only the
line numbers were wrong.

Two smaller anchor drifts of the same kind, also corrected in the ledger only:
`docs/usage.md:620` (the filters sentence is at 622) and
`swath.java-conventions.gradle.kts:23` (`JavaLanguageVersion.of(25)` is line 24).

**Why this matters beyond the fix.** The independent cross-model review
(`codex-review.md`) re-verified anchors by targeted sampling, prioritising
headline and correctness claims; this cluster sat in the output-formatting area
and was not sampled. So the derivation's anchor accuracy is demonstrably better
than uniform — a reader should treat an unsampled `[SRC]` line number as
approximate until checked, while the surrounding proposition is well supported.
Anchor-range checking is cheap and mechanical, and is worth adding as a gate
before any future derivation is promoted.

**Not corrected in place**, per the lifecycle rule in
[`../../../../docs/operating/tool-structure.md`](../../../../docs/operating/tool-structure.md):
derivation records are append-only, and `data/claims.json` is the canonical
current record.
