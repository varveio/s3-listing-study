# Verifier (union) — `aws-cli` / mode `s3api-v2-text`

**Verdict: PASS**

- Generated (UTC): 2026-07-17T08:19:56Z
- Registry digest: `254c8cfedd06b1b8671c5bbabc753bfe45462124821eacf44bd27b43c67bbced`
- Bucket: `noaa-normals-pds`  region `us-east-1`  auth `anonymous`
- Manifest sha256: `c78a82737dd1982a999912afa89f870c013cb22e01e50b8c4835ddb725992adb`  snapshot 2026-07-17

## Shards

| # | prefix | receipt |
| --- | --- | --- |
| 0 | normals-monthly/ | `tools/aws-cli/receipts/smoke/fanout/shard-monthly` |
| 1 | normals-daily/ | `tools/aws-cli/receipts/smoke/fanout/shard-daily` |
| 2 | normals-annualseasonal/ | `tools/aws-cli/receipts/smoke/fanout/shard-annualseasonal` |
| 3 | normals-hourly/ | `tools/aws-cli/receipts/smoke/s3api-v2-text-hourly` |
| 4 | <remainder> | `tools/aws-cli/receipts/smoke/fanout/remainder` |

## Counts

| | |
| --- | --- |
| Scope | union of 5 shard(s) |
| Prefix shards | 4 |
| Remainder shard | present |
| Root-level keys under no prefix | 1 |
| Structural status | complete |
| Manifest keys | 148917 |
| Emitted records | 148917 (multiset, pre-dedup) |
| Distinct keys | 148917 |
| Cross-shard duplicates (before dedup) | 0 |
| Missing | 0 |
| Extra | 0 |
| Field mismatches | 0 |
