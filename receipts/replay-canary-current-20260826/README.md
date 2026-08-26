# Campaign receipt draft: replay-canary-current-20260826

- Suite: `runner-replay-canary`
- Purpose labels: canary
- Resolved cases: 3
- Attempts: 3

| attempt | tool | mode | purpose | provider | evidence | subject exit | worker exit | rows | wall s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| s3-fast-list.90992fe12168.s1 | s3-fast-list | list | canary | SUCCEEDED | BOUND | 0 | 0 | 2048 | 5.033424 |
| s3p.276aea2ef416.s1 | s3p | ls | canary | SUCCEEDED | BOUND | 0 | 0 | 2048 | 0.734464 |
| s7cmd.ee7094a12693.s1 | s7cmd | recursive-tsv | canary | SUCCEEDED | BOUND | 0 | 0 | 2048 | 0.264072 |

> Draft only: this is a factual export of recorded state and bound evidence. It does not promote a claim or turn diagnostics into measurements.

The exact submit, poll, status, report, and export commands and their recorded
outputs are in [`operator-log.md`](operator-log.md).
