# Verifier — `rclone` / mode `delimiter-shallow`

**Verdict: PASS**

| | |
| --- | --- |
| Scope | `delimiter=/ prefix=<none>` |
| Fields checked | size 1/5; etag 0/5; mtime 1/5; storage_class 1/5 rows compared (by policy); 4 row(s) exposed none and were checked on key only |
| Registry | `docs/smoke-bucket.md` (sha256 `254c8cfedd06b1b8671c5bbabc753bfe45462124821eacf44bd27b43c67bbced`, as used by the run) |
| Manifest | `c78a82737dd1982a999912afa89f870c013cb22e01e50b8c4835ddb725992adb` |
| Snapshot date | 2026-07-17 |
| Expected keys | 5 |
| Emitted records | 5 (multiset, pre-dedup) |
| Distinct keys | 5 |
| Duplicates | 0 |
| Missing | 0 |
| Extra | 0 |
| Field mismatches | 0 |
| Inputs | 1 |

Duplicates are counted **before** dedup: the completeness diff needs a
deduplicated set, but a set union would destroy the duplicate evidence,
so the multiset is counted first.
