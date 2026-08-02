# Errata — derivation records

`report.md` and the `reader-*.md` files are frozen derivation records: they are
preserved as written so the derivation can be audited, and defects found later
are recorded here rather than edited into them.

## E1 — 22 source anchors cite line numbers past the end of their files

Every `[SRC path:lines @ cef8ec2]` label in this directory was machine-checked
against the pinned checkout with
[`scripts/check-source-anchors.py`](../../../scripts/check-source-anchors.py).
Of 1,229 anchors resolved from 1,078 labels, **22 cite a line past the end of
the file they name**:

| Page | File | Cited | Actual length |
| --- | --- | --- | --- |
| `report.md` | `output/Json.java` | `:85-107` | 38 |
| `report.md`, `reader-D-output.md` | `output/AlignedFormatter.java` | `:116-119`, `:123-124`, `:128`, `:135-138`, `:141-166`, `:168-179` | 84 |
| `report.md` | `output/RowTally.java` | `:141-144` | 60 |
| `report.md` | `output/OutputFormat.java` | `:159-161` | 19 |
| `report.md` | `output/Formatters.java` | `:134-142` | 25 |
| `report.md` | `filter/IncludeRegexFilter.java` | `:114-116` | 30 |
| `report.md`, `reader-D-output.md` | `output/parquet/ParquetFormatter.java` | `:283`, `:310-313` | 82 |
| `reader-D-output.md` | `output/BrokenPipe.java` | `:216-219` | 33 |
| `report.md`, `reader-C-cli.md` | `checkpoint/Node.java` | `:170-176` | 32 |
| `report.md`, `reader-C-cli.md` | `runtime/RunMeta.java` | `:88-99` | 55 |
| `report.md` | `checkpoint/SortPhase.java` | `:247-251` | 19 |

**The propositions are correct; the line numbers are not.** Each was re-derived
from the pinned checkout when `data/claims.json` was written, and the ledger
cites the corrected ranges.

**`data/claims.json` is clean.** All 124 structured source anchors in the
canonical ledger check out at `cef8ec2` — 124 checked, 0 failed. The defect is
confined to the prose derivation records, the layer a reader consults for *how*
a conclusion was reached rather than *what* is currently claimed.

Two smaller anchor drifts of the same kind, corrected in the ledger only:
`docs/usage.md:620` (the filters sentence is at 622) and
`swath.java-conventions.gradle.kts:23` (`JavaLanguageVersion.of(25)` is line 24).

### What this says about the derivation

An earlier version of this note recorded five bad anchors and attributed them
all to one reader's area. Both figures were wrong, and a mechanical sweep is
what corrected them:

- The count is **22**, not five.
- They are **not** one reader's problem. `reader-C-cli.md` carries two, in the
  checkpoint area, and `report.md` carries the majority. The clustering in
  output-formatting files is real but partial.
- The independent cross-model review (`codex-review.md`) sampled anchors by
  priority — headline and correctness claims first — and surfaced none of these,
  because they sit under output formatting and checkpoint plumbing. Sampling by
  importance systematically misses defects concentrated in unglamorous areas.

The practical lesson is that anchor-range checking is **mechanical and should
not be delegated to judgement**. `scripts/check-source-anchors.py` performs it;
run it against any new derivation before its claims are promoted:

```sh
python3 scripts/check-source-anchors.py --tool <slug> --source-root <checkout>
python3 scripts/check-source-anchors.py --tool <slug> --markdown tools/<slug>/research/ --source-root <checkout>
```

The Markdown mode is best-effort — 129 of this directory's anchors were skipped
as abbreviated or unparseable prose paths — so a clean Markdown run is weaker
evidence than a clean ledger run.

**Not corrected in place**, per the lifecycle rule in
[`../../../docs/operating/tool-structure.md`](../../../docs/operating/tool-structure.md):
derivation records are append-only, and `data/claims.json` is the canonical
current record.
