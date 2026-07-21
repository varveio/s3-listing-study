# Capability probe — `s5cmd run` batch fan-out

`_capability/` carries no verifier verdict and is exempt from the every-mode
expectation. This **[OBS]** probe demonstrates the dossier's mandatory fan-out
workaround — parallelizing listing by hand, one `ls` per prefix, executed in a
single `s5cmd run` process (its worker pool runs the lines in parallel). It is
**not** a `smoke-run.sh` receipt: `s5cmd run` needs a command **file mounted
into the container** (or stdin), which the wrapper does not provide. The
harness-blessed, wrapper-recorded form of the same fan-out is the five receipts
under `receipts/smoke/fanout/` verified with `--scope union` (see
`fanout/union/union-verify.md`, Verdict PASS). This probe proves the *in-process
`run` orchestration* itself executes and covers the bucket.

## Correction to the dossier

The dossier calls the invocation `s5cmd run -f <file>`. In v2.3.0 **`run` takes
the file as a positional argument** (`run [file]`) or reads commands from stdin;
there is **no `-f` flag** — `--json --no-sign-request run -f /work/cmds.txt`
fails with `Incorrect Usage: flag provided but not defined: -f` (exit 1). The
correct form is `s5cmd ... run /work/cmds.txt`. `run --help` states it executes
the declared commands **"in parallel"**, which is the concurrency the fan-out
relies on (the `--numworkers` pool, default 256). [OBS]

## Probe

- Image `peakcom/s5cmd@sha256:2ff939e2ee3c76adcadd78dbfc3e2569b18a3743ed9dcfccb1ec589af7fb9903`
  (v2.3.0-991c9fb), arch arm64, `TZ=UTC`, unsigned (`--no-sign-request`).
- `cmds.txt` (this dir): one `ls` per top-level prefix + the root key —
  `normals-monthly/*`, `normals-daily/*`, `normals-annualseasonal/*`,
  `normals-hourly/*`, `index.html`.
- Command: `docker run --rm --network host -e TZ=UTC -v <dir>:/work:ro <image>
  --json --no-sign-request run /work/cmds.txt`
- **Result: exit 0; 148,917 distinct keys (exact manifest match); 0 bytes
  stderr.**

Raw output (32,098,577 bytes, JSON, absolute keys) is too large for the repo
(no-data-in-repo rule), so it lives at
`<data>/receipts/s5cmd/run-fanout.noaa-normals-pds.run.anonymous.stdout.json`,
sha256 `c4976fc1ae2fc833a2ebb9fc379ded9b174ecaa8531b2b5d1c82cf47867b4580`
(recorded in `run.stdout.sha256`); a 3-line head sits in `run.stdout.sample.json`.
Key-completeness was checked with `jq -r 'select(.key)|.key' | sort -u | wc -l`
= 148917 against the manifest. The earlier `run -f` attempt failed with
`Incorrect Usage: flag provided but not defined: -f` (exit 1) — documented above,
not preserved as a file. Field-level and per-shard-scope assertions are covered
by the wrapper-recorded `fanout/` union, not by this [OBS] probe.
