# Stage B build evidence — `s3p`

Non-mode evidence (brief § Stage C step 3). No verifier verdict attaches to
`_build/`.

## Image

| | |
| --- | --- |
| Dockerfile | `tools/s3p/Dockerfile` (study-authored — upstream ships no image and no Dockerfile) |
| Base image | `node@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0` (node:20-bookworm-slim, multi-arch, resolved 2026-07-17) |
| Build cmd | `docker build -t s3p:3.7.2 -f Dockerfile .` (in `tools/s3p/`) |
| Built image digest | `s3p@sha256:622d7ec0e110f49e8cddf1b65b8bae98f641690b0d6db317df6f21e573894b91` (arm64) |
| Installed tool | `s3p@3.7.2` via `npm install -g --ignore-scripts s3p@3.7.2` |
| Entrypoint | `["s3p"]` |
| Arch | built + smoked on arm64 (host arm64) — native, not emulated |

## Version / help (first execution — brief § Stage B)

- `docker run --rm <img> version` → `3.7.2`
- `docker run --rm <img> help` and `... ls --help` captured; every command's help
  matches the Stage A source reading. Live-only additions over the v3.6.0 source
  I read: a `delete` command (mutating; requires
  `--confirm-delete-items-from-bucket` == `--bucket`), `summarize --group-by`,
  and an advanced `--max-sockets` flag (HTTP pool; "Defaults to match the
  operation's concurrency options: list-concurrency for list-only commands").
- **No `--no-sign-request` / anonymous / unsigned option appears anywhere** in
  `help` or in any command `--help`. The only credential guidance is "s3p uses
  the same creds as the aws-cli" (env / shared config). [OBS live help @ 3.7.2]

## Finding: the published v3.6.0 artifact cannot start

`npm install -g s3p@3.6.0` then `s3p version` →
`Error: Cannot find module 'colors'` (MODULE_NOT_FOUND, required from
`build/S3Parallel/S3P.js`). `colors` is `require`d at runtime but is **absent
from v3.6.0's published dependency closure** (`npm ls colors` → empty). The repo
lockfile *does* contain `colors` (dev tree), so the dev build worked; the publish
dropped it. Fixed in **3.6.1** (commit `5610411` "patch/fix: fixed deps (colors)
and other things"). This is why the smoke image installs **3.7.2** (npm `latest`)
rather than 3.6.0. [RUN — this build] [SRC package.json @ 5a23b22e: no `colors`
in `dependencies`]

## Architecture matrix

s3p is pure JavaScript (CaffeineScript compiled to JS in the npm tarball; no
native addons). It runs on any platform Node.js supports.

| Channel | amd64 | arm64 | Notes |
| --- | --- | --- | --- |
| Upstream Docker image | — | — | none published |
| Upstream Dockerfile | — | — | none in repo |
| npm package (used here) | native | native | interpreted JS; arch = the node base image's arch |
| Prebuilt binaries | — | — | none; distribution is npm-only |

Benchmark phase can run s3p natively on either arch (amd64 is the expected
campaign common denominator). Note: AWS SDK v3 prints a
`NodeVersionSupportWarning` on node 20 (will require node ≥22 after Jan 2027) —
consider a node:22 base for the benchmark image.
