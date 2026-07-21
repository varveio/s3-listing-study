# Third-party tools

This repository evaluates third-party object-store listing tools. It vendors no
third-party source code (with one narrow exception noted below). Each tool is
fetched or built from upstream at a pinned revision, invoked as a separate
program, and never redistributed from here. Some are exercised only at a
capability probe (they have no unsigned request path, so their listing modes are
blocked without credentials) rather than in a full listing run — see
[`tools/README.md`](tools/README.md) for which.

This file, unlike [`NOTICE`](NOTICE), is a maintained research record — it is
expected to change as the study verifies more of what it currently only
believes, and it carries no downstream redistribution obligation.

## Licenses

Entries marked `*` were read firsthand from the project's own LICENSE file; the
anchor, and the revision where one is pinned, are recorded on that tool's page
under [`tools/`](tools/). Unmarked entries are as understood from
secondhand research and are not yet verified — see
[`docs/methodology.md`](docs/methodology.md).

| Tool | Firsthand | License | Upstream |
| --- | --- | --- | --- |
| aws-cli | `*` | Apache-2.0 | https://github.com/aws/aws-cli |
| s5cmd | `*` | MIT | https://github.com/peak/s5cmd |
| rclone | `*` | MIT | https://github.com/rclone/rclone |
| s3ls-rs | | Apache-2.0 | https://github.com/nidor1998/s3ls-rs |
| s7cmd | | Apache-2.0 | https://github.com/nidor1998/s7cmd |
| s3-fast-list | `*` | MIT-0 | https://github.com/aws-samples/s3-fast-list |
| S3P | `*` | ISC | https://github.com/generalui/s3p |
| PS3 | `*` | GPL-3.0 | https://github.com/jboothomas/ps3 |
| s4cmd | `*` | Apache-2.0 | https://github.com/bloomreach/s4cmd |
| MinIO mc | `*` | AGPL-3.0 | https://github.com/minio/mc |
| s3kor | `*` | GPL-3.0 | https://github.com/sethkor/s3kor |
| swath | | TBD | https://github.com/varveio/swath |

`s7cmd` and `s3ls-rs` are unmarked deliberately: their tool pages
record Apache-2.0 from `[DOC LICENSE]` (the repo's documented license), not a
firsthand read of the file, so they don't yet meet the firsthand bar even though
we're confident in the value.

`s3ls-rs` is not a separate subject: `s7cmd`'s `ls` **is** that crate, pinned.
It is listed because its code is exercised whenever `s7cmd` is — see
[`tools/README.md`](tools/README.md).

## Build recipes and copyleft posture

No third-party source is vendored, **with one exception**:
[`tools/s3-fast-list/build/Dockerfile`](tools/s3-fast-list/build/Dockerfile) is upstream's
own build recipe, carried with one documented toolchain deviation (see
[`tools/s3-fast-list/docs/running.md`](tools/s3-fast-list/docs/running.md)). s3-fast-list
is MIT-0 (MIT No Attribution), which waives the attribution requirement; it is
named here for accuracy, not obligation.

The other Dockerfiles under `tools/` (`s3kor`, `s3p`, `s4cmd`, `ps3`) are this
study's own work, written because upstream ships none.

**Copyleft.** Three tools are copyleft-licensed: MinIO mc (AGPL-3.0), s3kor
(GPL-3.0), and PS3 (GPL-3.0). This repository triggers no copyleft obligation
for any of them:

- Nothing is vendored — no source from any tool is copied in.
- Nothing copyleft is redistributed. `mc` is pulled as upstream's own
  already-published image, by digest; `s3kor` and `ps3` are built locally from
  source into local images (`ps3` from a study Dockerfile, since upstream ships
  none). No image, and no binary, is pushed to a registry or otherwise conveyed.
  A copyleft obligation to offer Corresponding Source attaches on *conveying*
  object code — the operative fact here is that nothing is conveyed.
- The tools are run as separate programs. The study's own `run.sh` /
  `normalize.sh` never link against them — `run.sh` emits argv and does not
  execute, and `normalize.sh` parses captured stdout after the fact. Apache-2.0
  scripts alongside a GPL/AGPL binary in a container is mere aggregation, not a
  derivative work.
- AGPL §13's network clause is not engaged: `mc` is run as an unmodified
  upstream binary as a local CLI, not offered to remote users as a modified
  service.

If this project ever publishes an image or binary of a copyleft tool to a
registry, that step would convey object code and the Corresponding Source
obligation would apply. It does not do so today.

## Study output vs. tool property

Benchmark results, quoted claims, and descriptions of these tools' behavior are
this study's own work and carry this repository's license
([`LICENSE`](LICENSE), Apache-2.0). They are not endorsed by, nor produced in
cooperation with, the tools' authors.

Verbatim tool output captured in receipts (help text, error strings, listing
output) remains the property of the respective tool under its own license; a
program's output is not a derivative work of this repository.
