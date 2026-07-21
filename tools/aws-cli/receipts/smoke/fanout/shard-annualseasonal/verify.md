# Verifier — `aws-cli` / mode `s3api-v2-text`

**Verdict: PASS**

| | |
| --- | --- |
| Scope | `prefix=normals-annualseasonal/` |
| Fields checked | size 48784/48784; etag 48784/48784; mtime 48784/48784; storage_class 48784/48784 rows compared (by policy) |
| Registry | `docs/smoke-bucket.md` (sha256 `254c8cfedd06b1b8671c5bbabc753bfe45462124821eacf44bd27b43c67bbced`, as used by the run) |
| Manifest | `c78a82737dd1982a999912afa89f870c013cb22e01e50b8c4835ddb725992adb` |
| Snapshot date | 2026-07-17 |
| Expected keys | 48784 |
| Emitted records | 48784 (multiset, pre-dedup) |
| Distinct keys | 48784 |
| Duplicates | 0 |
| Missing | 0 |
| Extra | 0 |
| Field mismatches | 0 |
| Inputs | 1 |

Duplicates are counted **before** dedup: the completeness diff needs a
deduplicated set, but a set union would destroy the duplicate evidence,
so the multiset is counted first.
