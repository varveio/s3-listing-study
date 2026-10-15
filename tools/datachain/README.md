# DataChain — research and onboarding proposal

[DataChain](https://github.com/datachain-ai/datachain) is a dataset-processing
library that can enumerate S3 metadata into a persistent catalog. It is a
potential listing subject, with a different end-to-end product from a standalone
lister. **This entry is source research only: no tool qualification or benchmark
run has been performed for it.**

[Read the source research and development history](research/report.md).
[Read the proposed onboarding boundaries](research/onboarding.md).

The important distinction, checked on 2026-10-15:

| Subject | What the source establishes | State |
| --- | --- | --- |
| Released 0.60.0 | One flat S3 paginator; `read_storage` persists its listing in a catalog | Released baseline |
| PR #2027 | Parallel ranges, child processes, changed parsing and catalog insertion | Open, unreleased |
| PR #2045 | Explicitly credits Swath ideas; author says changes moved to #2027 | Closed without merge |
| PR #2051 | Public database-free `dc.listing` API | Open draft based on #2027 |

The proposed scheduler is compact: one initial range, observed-character
midpoints, page-age rules and conditional probes. Swath's public source
separates seeding, pivot construction and split policies. This is a source-level
structural comparison; it does not establish which is faster or more complete.
The [recovery/output comparison](research/report.md#recovery-and-output-responsibilities)
also distinguishes DataChain's in-memory range recovery from Swath's transactional
work tracking and managed-Parquet durability, publication and optional sorting.
Swath documents its one-shot text output as non-resumable; the product boundary matters.

DataChain's upstream comparison introduces two useful research candidates:
**obstore** and **Daft**. s3ls-rs is already represented through s7cmd; the cited
Dataflux, gcloud and AzCopy comparisons concern other cloud providers. The
[tool assessment](research/report.md#other-tools-in-the-upstream-comparison)
explains the distinctions and limitations of the upstream timings.

Varve maintains this study and builds Swath. The current
[diagnostic release](../../results/2026-09-scale-diagnostics/REPORT.md) contains
no DataChain rows and remains unchanged. No speed ranking follows from this
research; which workloads favor either tool remains an open empirical question.
