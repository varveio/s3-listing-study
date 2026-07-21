# s3p — no upstream Docker image and no upstream Dockerfile exist (v3.6.0).
# s3p is distributed only as an npm package (run via `npx s3p` or a global
# install), so this image is study-authored per BRIEF.md Stage B: base pinned by
# digest, the tool installed at the exact pinned version.
#
# node:20-bookworm-slim (arm64 + amd64 multi-arch; digest below is the manifest
# list digest, resolved 2026-07-17). s3p is pure JavaScript (CaffeineScript
# compiled to JS) so it runs natively on whatever arch the node base provides.
#
# NOTE: aws-cli is intentionally NOT installed. s3p only shells out to `aws s3 cp`
# for large-file COPIES (>100 MiB); all listing commands (ls, summarize, compare)
# use the in-process @aws-sdk/client-s3 and need no aws-cli. This study runs
# list-only, so node alone is sufficient.
FROM node@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0

# VERSION CHOICE (see research/report.md § Container/Metadata):
#   Source is anchored for [SRC] at git tag v3.6.0 (SHA 5a23b22e...), the latest
#   GitHub *release tag*. But npm's `latest` dist-tag is 3.7.2 — the maintainer
#   publishes to npm beyond what is git-tagged — so `npm i -g s3p` / `npx s3p`
#   installs 3.7.2, which is what real users run. We therefore smoke 3.7.2.
#   Additional reason: the published v3.6.0 artifact cannot start at all — it
#   `require`s `colors`, which is absent from its dependency closure (a publish
#   bug fixed in 3.6.1, commit 5610411 "fixed deps (colors)"). 3.7.2's listing
#   architecture and its absence of any anonymous/unsigned access path are
#   identical to v3.6.0 (verified against the installed build).
# --ignore-scripts: s3p ships a prebuilt build/ in its npm tarball; no build step.
RUN npm install -g --ignore-scripts s3p@3.7.2 \
 && npm cache clean --force

# ENTRYPOINT is the tool binary, so run.sh argv starts at the SUBCOMMAND
# (see harness README "run.sh argv is appended to the image ENTRYPOINT").
ENTRYPOINT ["s3p"]
