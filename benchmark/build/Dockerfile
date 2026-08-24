# syntax=docker/dockerfile:1
# One self-contained toolbox build. External bases are digest-pinned and every
# downloaded subject artifact is checksum-verified using its capsule recipe.

FROM debian@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241 AS runtime_base
USER root
RUN test -f /etc/apt/sources.list.d/debian.sources \
    && sed -i \
      -e 's|http://deb.debian.org/debian-security|http://snapshot.debian.org/archive/debian-security/20260803T000000Z|g' \
      -e 's|http://deb.debian.org/debian|http://snapshot.debian.org/archive/debian/20260803T000000Z|g' \
      /etc/apt/sources.list.d/debian.sources \
    && printf '%s\n' 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/99snapshot \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       ca-certificates=20230311+deb12u1 libssl3=3.0.20-1~deb12u2 \
       openssl=3.0.20-1~deb12u2 python3=3.11.2-1+b1 \
       python3-minimal=3.11.2-1+b1 python3-pip=23.0.1+dfsg-1 \
       python3-venv=3.11.2-1+b1 \
    && rm -rf /var/lib/apt/lists/*

FROM runtime_base AS aws_cli_install
ADD --checksum=sha256:6b36b85980ab783db7ba0ff24ebb85924682fa6da1db7ca3c2fa6f27083c0f52 \
    https://awscli.amazonaws.com/awscli-exe-linux-x86_64-2.36.1.zip /tmp/aws.zip
RUN /usr/bin/python3 -m zipfile -e /tmp/aws.zip /tmp/aws \
    && chmod 0755 /tmp/aws/aws/install /tmp/aws/aws/dist/aws /tmp/aws/aws/dist/aws_completer \
    && /tmp/aws/aws/install --install-dir /opt/aws-cli --bin-dir /usr/local/bin

FROM runtime_base AS minio_mc_install
ADD --checksum=sha256:01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891 \
    https://dl.min.io/client/mc/release/linux-amd64/archive/mc.RELEASE.2025-08-13T08-35-41Z /usr/bin/mc
RUN chmod 0755 /usr/bin/mc

FROM runtime_base AS ps3_install
ADD --checksum=sha256:3bc7bbbb0d45d6f96b0130f98859ea7cfe693b13a8438461240423511322a9c2 \
    https://raw.githubusercontent.com/jboothomas/ps3/9428492291ef3aa824dba0b495583279c3d33760/pS3.0-1-16 /usr/local/bin/pS3
RUN chmod 0755 /usr/local/bin/pS3

FROM runtime_base AS rclone_install
ADD --checksum=sha256:fe435e0c36228e7c2f116a8701f01127bb1f694005fc11d1f27186c8bca4115d \
    https://downloads.rclone.org/v1.74.4/rclone-v1.74.4-linux-amd64.zip /tmp/rclone.zip
RUN /usr/bin/python3 -m zipfile -e /tmp/rclone.zip /tmp/rclone \
    && install -m 0755 /tmp/rclone/rclone-v1.74.4-linux-amd64/rclone /usr/local/bin/rclone

FROM rust@sha256:cf9dd0ec73e75f827fe59123fff9dc65af1a1c8363c3c31ee8d7f8ad0b6a5fb2 AS s3_fast_list_build
WORKDIR /usr/src/app
ADD --checksum=sha256:43bebce8cc64c85c25ff4d7b5fe497b46849825b4ff37fb0c7c230dcdf4bb6a0 \
    https://github.com/sagiba/s3-fast-list/archive/6c72f596e2ffe7311dec8cb7de29b114c0251207.tar.gz /tmp/source.tar.gz
RUN tar xzf /tmp/source.tar.gz --strip-components=1
COPY tools/s3-fast-list/build/Cargo.lock ./Cargo.lock
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    cargo build --release --locked

FROM runtime_base AS s3kor_install
ADD --checksum=sha256:47047e8a40a84992914f02bb5c8dfc525abff0a42c3ed76a4ed042a8ef976103 \
    https://github.com/sethkor/s3kor/releases/download/v0.0.37/s3kor_0.0.37_Linux_amd64.tar.gz /tmp/s3kor.tar.gz
RUN tar xzf /tmp/s3kor.tar.gz -C /tmp s3kor \
    && install -m 0755 /tmp/s3kor /usr/local/bin/s3kor

FROM node@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 AS s3p_install
WORKDIR /build
ADD --checksum=sha256:56e51536d2d34fa2988e53e04b2eb0a81aee695abe8fe4525ec0cc879ef24a51 \
    https://registry.npmjs.org/s3p/-/s3p-3.7.2.tgz /tmp/s3p.tgz
COPY tools/s3p/build/package.json tools/s3p/build/package-lock.json ./
RUN node - <<'JS'
const crypto = require('crypto');
const fs = require('fs');
const lock = require('/build/package-lock.json');
const expected = lock.packages['node_modules/s3p'].integrity;
const actual = 'sha512-' + crypto.createHash('sha512').update(fs.readFileSync('/tmp/s3p.tgz')).digest('base64');
if (actual !== expected) throw new Error('s3p tarball disagrees with package-lock integrity');
JS
RUN npm ci --ignore-scripts && npm cache clean --force

FROM runtime_base AS s4cmd_install
ADD --checksum=sha256:42566058a74d3e1e553351966efaaffa08e4b6ac28a19e72a51be21151ea9534 \
    https://files.pythonhosted.org/packages/42/b4/0061f4930958cd790098738659c1c39f8feaf688e698142435eedaa4ae34/s4cmd-2.1.0.tar.gz /tmp/s4cmd.tar.gz
COPY tools/s4cmd/build/requirements.txt /tmp/requirements.txt
RUN /usr/bin/python3 -m venv /opt/s4cmd-venv \
    && /opt/s4cmd-venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements.txt \
    && mkdir -p /opt/s4cmd \
    && tar xzf /tmp/s4cmd.tar.gz -C /opt/s4cmd --strip-components=1 s4cmd-2.1.0/s4cmd.py \
    && printf '#!/bin/sh\nexec /opt/s4cmd-venv/bin/python /opt/s4cmd/s4cmd.py "$@"\n' > /usr/local/bin/s4cmd \
    && chmod 0755 /usr/local/bin/s4cmd

FROM runtime_base AS s5cmd_install
ADD --checksum=sha256:de0fdbfa3aceae55e069ba81a0fc17b2026567637603734a387b2fca06c299b4 \
    https://github.com/peak/s5cmd/releases/download/v2.3.0/s5cmd_2.3.0_Linux-64bit.tar.gz /tmp/s5cmd.tar.gz
RUN tar xzf /tmp/s5cmd.tar.gz -C / s5cmd && chmod 0755 /s5cmd

FROM runtime_base AS s7cmd_install
ADD --checksum=sha256:34ecf3a725e835e88ad115a73d6e7b3a324c95677439c43f5baec536d8a6b3ad \
    https://github.com/nidor1998/s7cmd/releases/download/v1.5.0/s7cmd-1.5.0-linux-glibc2.28-x86_64.tar.gz /tmp/s7cmd.tar.gz
RUN tar xzf /tmp/s7cmd.tar.gz -C /tmp \
    && install -m 0755 /tmp/s7cmd /usr/local/bin/s7cmd

FROM ghcr.io/varveio/swath@sha256:5528ac5c238d5d0ca51bd221e43c398b3a0ac5464dcb30bef9691c94d10fa735 AS swath_install

# Only subject runtime binaries and closures cross into the final toolbox.
FROM runtime_base AS toolbox
COPY --from=aws_cli_install /opt/aws-cli/ /opt/aws-cli/
COPY --from=minio_mc_install /usr/bin/mc /usr/bin/mc
COPY --from=ps3_install /usr/local/bin/pS3 /usr/local/bin/pS3
COPY --from=rclone_install /usr/local/bin/rclone /usr/local/bin/rclone
COPY --from=s3_fast_list_build /usr/src/app/target/release/s3-fast-list /usr/bin/s3-fast-list
COPY --from=s3_fast_list_build /usr/src/app/target/release/ks-tool /usr/bin/ks-tool
COPY --from=s3kor_install /usr/local/bin/s3kor /usr/local/bin/s3kor
COPY --from=s3p_install /usr/local/bin/node /usr/local/bin/node
COPY --from=s3p_install /build/node_modules/ /opt/s3p/node_modules/
COPY --from=s4cmd_install /opt/s4cmd-venv/ /opt/s4cmd-venv/
COPY --from=s4cmd_install /opt/s4cmd/ /opt/s4cmd/
COPY --from=s4cmd_install /usr/local/bin/s4cmd /usr/local/bin/s4cmd
COPY --from=s5cmd_install /s5cmd /s5cmd
COPY --from=s7cmd_install /usr/local/bin/s7cmd /usr/local/bin/s7cmd
COPY --from=swath_install /opt/java/openjdk/ /opt/java/openjdk/
COPY --from=swath_install /opt/swath/swath.jar /opt/swath/swath.jar
# Per-tool work stays in its own instruction so a `# slice:` marker can attribute
# it; an unmarked line is platform, which over-invalidates rather than hides.
# slice: aws-cli
RUN ln -s /opt/aws-cli/v2/current/bin/aws /usr/local/bin/aws \
    && ln -s /opt/aws-cli/v2/current/bin/aws_completer /usr/local/bin/aws_completer
# slice: s3p
RUN printf '#!/bin/sh\nexec /usr/local/bin/node /opt/s3p/node_modules/s3p/bin/s3p "$@"\n' > /usr/local/bin/s3p \
    && chmod 0755 /usr/local/bin/s3p
# slice: s7cmd
RUN install -d -o 10001 -g 10001 /home/s7cmd
# /aws is subject_workdir for both aws-cli and s5cmd, so it is platform.
RUN install -d -o 10001 -g 10001 /aws /data /home/s3study

COPY benchmark/build/requirements-worker.txt /tmp/requirements-worker.txt
RUN /usr/bin/python3 -m pip install --break-system-packages --no-cache-dir -r /tmp/requirements-worker.txt \
    && /usr/bin/python3 -m pip check \
    && /usr/bin/python3 -c 'import duckdb, ijson, google.cloud.storage, zstandard'

# Only the importable package. Plans, tests, build inputs, and the README sit
# outside src/, so the worker image cannot pick up orchestrator-side code.
COPY benchmark/src/benchmark/ /opt/benchmark/benchmark/
COPY tools/ /opt/benchmark/tools/
ARG IMAGE_METADATA_B64
ARG TOOLBOX_MANIFEST_SHA256
ARG TOOLBOX_RECIPE_SHA256
ARG HARNESS_REVISION
RUN printf '%s\n' "${TOOLBOX_MANIFEST_SHA256}" | grep -Eq '^[0-9a-f]{64}$' \
    && printf '%s\n' "${TOOLBOX_RECIPE_SHA256}" | grep -Eq '^[0-9a-f]{64}$' \
    && printf '%s\n' "${HARNESS_REVISION}" | grep -Eq '^[0-9a-f]{40}$' \
    && printf '%s' "${IMAGE_METADATA_B64}" | base64 -d > /opt/benchmark/image-metadata.json
RUN TOOLBOX_MANIFEST_SHA256="${TOOLBOX_MANIFEST_SHA256}" \
    TOOLBOX_RECIPE_SHA256="${TOOLBOX_RECIPE_SHA256}" \
    HARNESS_REVISION="${HARNESS_REVISION}" \
    /usr/bin/python3 <<'PY'
import hashlib
import json
import os
from pathlib import Path, PurePosixPath

expected_tools = {
    "aws-cli", "minio-mc", "ps3", "rclone", "s3-fast-list", "s3kor",
    "s3p", "s4cmd", "s5cmd", "s7cmd", "swath",
}
metadata_path = Path("/opt/benchmark/image-metadata.json")
metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
if set(metadata) != {
    "schema_version", "toolbox_manifest_sha256", "toolbox_recipe_sha256",
    "harness_revision", "tools",
}:
    raise SystemExit("toolbox metadata has unexpected top-level fields")
if metadata["schema_version"] != 5:
    raise SystemExit("toolbox metadata schema is not 5")
if metadata["toolbox_recipe_sha256"] != os.environ["TOOLBOX_RECIPE_SHA256"]:
    raise SystemExit("toolbox recipe does not match the build argument")
if metadata["harness_revision"] != os.environ["HARNESS_REVISION"]:
    raise SystemExit("harness revision does not match the build argument")
tools = metadata.get("tools")
if not isinstance(tools, dict) or set(tools) != expected_tools:
    raise SystemExit("toolbox metadata does not contain the exact registered roster")
adapter_roster = {
    path.parent.name for path in Path("/opt/benchmark/tools").glob("*/adapter") if path.is_dir()
}
if adapter_roster != expected_tools:
    raise SystemExit("toolbox adapters do not contain the exact registered roster")
manifest_tools = {}
for tool, facts in tools.items():
    if not isinstance(facts, dict):
        raise SystemExit(f"{tool}: metadata facts are malformed")
    workdir = facts.get("subject_workdir")
    if not isinstance(workdir, str) or not PurePosixPath(workdir).is_absolute():
        raise SystemExit(f"{tool}: subject_workdir is not absolute")
    if not Path(workdir).is_dir():
        raise SystemExit(f"{tool}: subject_workdir does not exist")
    executable = facts.get("executable")
    if not isinstance(executable, list) or not executable or not all(
        isinstance(token, str) and token for token in executable
    ):
        raise SystemExit(f"{tool}: executable is malformed")
    command = Path(executable[0])
    if not command.is_absolute() or not command.is_file() or not os.access(command, os.X_OK):
        raise SystemExit(f"{tool}: executable path is absent or not executable")
    for token in executable[1:]:
        if token.startswith("/") and not Path(token).exists():
            raise SystemExit(f"{tool}: absolute executable argument does not exist: {token}")
    manifest_tools[tool] = {
        key: value for key, value in facts.items() if key != "adapter_bundle_sha256"
    }
manifest = {
    "schema_version": 3,
    "toolbox_recipe_sha256": metadata["toolbox_recipe_sha256"],
    "tools": manifest_tools,
}
canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
computed_manifest = hashlib.sha256(canonical).hexdigest()
if computed_manifest != metadata["toolbox_manifest_sha256"]:
    raise SystemExit("toolbox metadata manifest digest is internally inconsistent")
if computed_manifest != os.environ["TOOLBOX_MANIFEST_SHA256"]:
    raise SystemExit("toolbox manifest does not match the build argument")
PY
RUN chown -R root:root /opt/benchmark \
    && find /opt/benchmark -type f -exec chmod 0444 {} + \
    && find /opt/benchmark -type d -exec chmod 0555 {} +
LABEL io.varve.s3-listing-study.toolbox-manifest-sha256="${TOOLBOX_MANIFEST_SHA256}" \
      io.varve.s3-listing-study.toolbox-recipe-sha256="${TOOLBOX_RECIPE_SHA256}" \
      io.varve.s3-listing-study.harness-revision="${HARNESS_REVISION}"
ENV PYTHONPATH=/opt/benchmark TZ=UTC HOME=/home/s3study
USER 10001:10001
WORKDIR /home/s3study
ENTRYPOINT ["/usr/bin/python3", "/opt/benchmark/benchmark/measure.py"]
CMD []
