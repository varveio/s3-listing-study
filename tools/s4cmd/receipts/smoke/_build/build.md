# Build receipt — s4cmd 2.1.0 image

Non-mode evidence (`_build/`): how the smoke image was produced. No verifier
verdict attaches to this dir.

| | |
| --- | --- |
| Date (UTC) | 2026-07-17 |
| Tool | s4cmd, tag `2.1.0`, commit `80059bfa4451f513a8f314fb6300e5ecc51587b2` |
| Source | `git+https://github.com/bloomreach/s4cmd@80059bfa4451f513a8f314fb6300e5ecc51587b2` |
| Base image | `python@sha256:b53f496ca43e5af6994f8e316cf03af31050bf7944e0e4a308ad86c001cf028b` (python:3.7-slim) |
| Built image (local tag) | `s4cmd-study:2.1.0` |
| Content digest | `sha256:d458ef5096180e517840712e29b0b8705ec97cebf48f717cad2fea3805105813` |
| Digest-pinned ref (via local registry) | `localhost:5000/s4cmd-study@sha256:d458ef5096180e517840712e29b0b8705ec97cebf48f717cad2fea3805105813` |
| Image arch | arm64 (native on the arm64 runner; no emulation) |
| Entrypoint | `["s4cmd"]` |
| Pinned deps | boto3==1.9.253, botocore==1.12.253, pytz==2018.5 |

## Build command

```sh
cd tools/s4cmd
docker build -t s4cmd-study:2.1.0 -f Dockerfile .
# to obtain a digest-pinned ref the wrapper accepts:
docker run -d -p 5000:5000 --name s4cmd-registry registry:2
docker tag s4cmd-study:2.1.0 localhost:5000/s4cmd-study:2.1.0
docker push localhost:5000/s4cmd-study:2.1.0
docker inspect --format '{{index .RepoDigests 0}}' localhost:5000/s4cmd-study:2.1.0
```

## Why the boto3/botocore pin (reproducibility — NOT a compatibility need)

`s4cmd.py:274` references `botocore.vendored.requests.packages.urllib3...` at
import time. An earlier draft assumed this broke `import s4cmd` under a current
botocore (the vendored requests *library* was removed in 1.13.0, 2019) and
labeled the pin a "capability finding." **That is retracted — it is false.** The
attribute path still resolves and s4cmd 2.1.0 installs, imports, and runs under
current boto3:

- `modern-boto3-import/transcript.txt` — Py 3.7 + botocore **1.33.13**:
  `import s4cmd` → `IMPORT_OK`; `botocore.vendored.requests` present.
- `modern-boto3-import/transcript-py312.txt` — Py 3.12 + botocore **1.43.50**
  (latest): `import s4cmd` → `IMPORT_OK`; `s4cmd --version` → `2.1.0`, exit 0.

The pin to `boto3==1.9.253`/`botocore==1.12.253` is therefore a **reproducibility
choice only**. The benchmark phase should reconsider it and run a current boto3
(differing retry/pooling behavior).

## First-execution checks (inside container)

- `s4cmd --version` → `s4cmd version 2.1.0`, exit 0.
- `s4cmd --help` → full option list; no `--no-sign-request`/unsigned option; no
  list page-size flag; `-c/--num-threads` and `--endpoint-url` present. Matches
  the Stage A doc reading.
