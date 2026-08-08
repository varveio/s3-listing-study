# Reader E findings — build, packaging, container, release provenance
Subject: `swath` @ `cef8ec24a74ffae14ee6a9462e4b7f6c334fbc32` (tag `v0.2.0`), SOURCE_ROOT `/home/vscode/.s3-listing-study/sources/swath`

---

## Q1 (GATING) — Yes. `ghcr.io/varveio/swath:0.2.0` exists, is public, signed, and points at our exact pinned SHA.

**Stage B should pull upstream's image. No fallback build is needed.**

Important timing caveat that changes the answer mid-session: when I started, the image did **not** exist. The `Release` workflow run for `v0.2.0` (`id=30746967874`, head_sha `cef8ec2`) had `build` = success but `publish` = **`waiting`**, blocked on the protected `public-release` environment with reviewer `sagiba` [3P https://api.github.com/repos/varveio/swath/actions/runs/30746967874]. `docker manifest inspect ghcr.io/varveio/swath:v0.2.0` returned `manifest unknown` and the anonymous GHCR tag list contained only `0.1.0`/`0.1`/`latest`. **The environment was approved during my pass**; the run completed `success` at `2026-08-02T12:14:55Z` and everything below is post-approval fact, re-verified twice.

### Registry answer

| Fact | Value |
|---|---|
| Tag name | **`0.2.0`** — note: **no `v` prefix**. `v0.2.0` is a 404; the git tag is `v0.2.0`, the image tag is `0.2.0` |
| Manifest-list (index) digest | **`sha256:ef1aca9ab473f133acceb5730ff88d52abaaa89e773801cdb62deff51f9909b0`** |
| `linux/amd64` manifest digest | `sha256:c782ad1194463ada4cd15c6c633a07b2abbacc7a9ef27357c85c93f67341c072` (167.8 MB compressed) |
| `linux/arm64` manifest digest | `sha256:7c60fd25c6ae8f8273bfa24ec2d48dfdf01424b032f9b4a860095e3843a6bf52` (165.9 MB compressed) |
| Index media type | `application/vnd.oci.image.index.v1+json` |
| Also-pointing tags | `0.2` and `latest` — all three resolve to the identical index digest |
| Anonymous pull | **Yes, public.** Anonymous `ghcr.io/token?scope=repository:varveio/swath:pull` succeeds; all manifests fetched with no credentials, and `docker manifest inspect` works with no `docker login` |
| Signature | **cosign keyless signature present**: tag `sha256-ef1aca9ab473f133acceb5730ff88d52abaaa89e773801cdb62deff51f9909b0.sig` → HTTP 200, layer `application/vnd.dev.cosign.simplesigning.v1+json` with `dev.cosignproject.cosign/{signature}`, `dev.sigstore.cosign/{bundle,certificate,chain}` |
| Attestations | Two BuildKit `attestation-manifest` entries in the index (`unknown/unknown` platform, `vnd.docker.reference.type=attestation-manifest`) — SLSA provenance + SBOM from `provenance: mode=max` / `sbom: true`. A GitHub `attest-build-provenance` was also pushed to the registry (`push-to-registry: true`); GHCR does not serve the OCI `/referrers/` API (404 `MANIFEST_UNKNOWN`), so verify it via `gh attestation verify oci://…` rather than referrers |

[3P ghcr.io registry API, anonymous pull token] for all of the above.

### The image is byte-provenanced to our pinned SHA

Both per-arch config blobs carry `org.opencontainers.image.revision = cef8ec24a74ffae14ee6a9462e4b7f6c334fbc32` and `org.opencontainers.image.version = 0.2.0` [3P image config blobs]. This is not a coincidence of tagging: the image's jar is the **exact tested uber-jar** promoted from the release `build` job via a BuildKit `--build-context build=promote` override of the Dockerfile's compile-from-source stage, checksum-verified before use [SRC .github/workflows/release.yml:185 @ cef8ec2], [SRC scripts/ci/verify-container-promotion.sh:22-40 @ cef8ec2].

The publish path was: push-by-digest untagged → deep container smoke against that digest → `imagetools create` the tags onto the smoked digest → cosign sign → attest → draft GitHub release → self-verify (`sha256sum --check`, `cosign verify-blob`, `cosign verify`, `gh attestation verify`) → un-draft. All 24 steps `success` [SRC .github/workflows/release.yml:179-341 @ cef8ec2], [3P run 30746967874 job `publish`].

### Also now available (was not, an hour ago)

GitHub release **`v0.2.0`**, published `2026-08-02T12:14:49Z`, not a draft, not a pre-release [3P GitHub Releases API]:
- `swath-0.2.0.jar` — 77,382,761 B, `sha256:9031656aeee0f769b9eab770e961bd79cee5ce3325d200664caf5abaa278b866`
- `swath-0.2.0.zip` (72.6 MB), `swath-0.2.0.tar.gz` (72.6 MB), `swath-cli-shadow-0.2.0.zip` (71.7 MB)
- `swath-0.2.0.spdx.json` (SPDX SBOM), `SHA256SUMS`
- a `.sigstore.json` keyless-signature bundle for **every** asset above

### Docs defect worth relaying to the other readers

At the pinned revision the shipped docs are **stale and now wrong**: `docs/install.md` says "**No release has been cut yet.** Until the first `vX.Y.Z` tag ships, **build from source** (below) is the only path" [DOC docs/install.md:8-10], and `docs/packaging-and-docker.md` says "**No release has been published yet**" [DOC docs/packaging-and-docker.md:225] — while its own verification example already reads `TAG=v0.1.0` [DOC docs/install.md:106]. A v0.1.0 release and `0.1.0` image had existed since 2026-07-27. Upstream commit `8311ede` on `main` ("docs: retire the pre-release install story") fixes exactly this, but it lands **after** the tag, so the v0.2.0 tarball ships the false claim. Reader C should not take install.md's pre-release framing at face value.

---

## Q2 — Build route, if you ever need it

**Command:** `docker build -t swath:dev .` from the repo root. Multi-arch: `just docker-build` → `docker buildx build --platform linux/amd64,linux/arm64 --builder swath-builder -t swath:dev --load .` with an isolated `DOCKER_CONFIG` to dodge devcontainer credential-helper breakage [SRC justfile:69-73 @ cef8ec2].

**Self-contained: yes.** No prior host Gradle run is required — the build stage copies source and runs `./gradlew --no-daemon :swath-cli:shadowJar` itself [SRC Dockerfile:57-60 @ cef8ec2]. A local `docker build` gets the from-source path; only CI substitutes the promoted jar [SRC Dockerfile:18-27 @ cef8ec2].

**Base image digests (both pinned, index digests not per-arch):**
- build stage: `eclipse-temurin:25-jdk-noble@sha256:3eb81ed94d8c1a34422f19f8188548bdf02cae69c91d0328afdbb7abed90f617`, forced to `--platform=$BUILDPLATFORM` [SRC Dockerfile:34 @ cef8ec2]
- runtime stage: `eclipse-temurin:25-jre-noble@sha256:2f1da100788559b397bcf48c736169ea5b070bde84e55f203bbee8e83d87a175` [SRC Dockerfile:87 @ cef8ec2]

**Network the build needs** (all outbound, no auth):
- Docker Hub for both base images — 133.4 MB (jdk, compressed, either arch) + 99.4/97.5 MB (jre amd64/arm64) [3P registry-1.docker.io]
- Ubuntu archives for `apt-get install python3` — needed because the shadowJar depends on `verifyThirdPartyNotices`, which shells out to `python3 scripts/legal/render-third-party-notices.py` [SRC Dockerfile:37-40 @ cef8ec2], [SRC build.gradle.kts:121-145 @ cef8ec2], [SRC swath-cli/build.gradle.kts:137 @ cef8ec2]
- `services.gradle.org` → `gradle-9.0.0-bin.zip`, **134,491,514 B** with `validateDistributionUrl=true` [SRC gradle/wrapper/gradle-wrapper.properties @ cef8ec2], [3P HTTP HEAD]
- Maven Central + Gradle Plugin Portal for the dependency graph. The shipped runtime closure alone is **107 artifacts** [SRC THIRD_PARTY_NOTICES.md @ cef8ec2]; the build additionally resolves the Shadow, jk1-license-report, spotless, JMH plugins and the whole test stack.
- **No JDK toolchain download.** `JavaLanguageVersion.of(25)` [SRC build-logic/src/main/kotlin/swath.java-conventions.gradle.kts:23 @ cef8ec2] with **no foojay/toolchain resolver configured anywhere** — the temurin-25 base JDK satisfies it in-container. On a bare host this means a **local JDK 25 is mandatory**; Gradle will not auto-provision one.

---

## Q3 — Architecture matrix (deliverable)

| Channel | linux/amd64 | linux/arm64 | Evidence / caveat |
|---|---|---|---|
| **Upstream GHCR image `0.2.0`** | ✅ native | ✅ native | Both present in the OCI index with real per-arch digests [3P] |
| **Uber-jar** (`swath-0.2.0.jar`) | ✅ | ✅ | Arch-neutral bytecode; the two native-code deps (`sqlite-jdbc`, `zstd-jni`) bundle libraries for every arch and select at runtime [SRC Dockerfile:12-14 @ cef8ec2]. Needs a **JDK 25** runtime, no `--enable-preview` [DOC docs/packaging-and-docker.md:24-28] |
| **installDist / distZip / distTar** | ✅ | ✅ | Same classes + a Gradle launcher script; explicitly "not native per-platform binaries" [DOC docs/install.md:43-45] |
| **Source build** | ✅ | ✅ | Whatever arch the host JDK 25 is; Dockerfile build stage is pinned to `$BUILDPLATFORM` so it compiles once natively [SRC Dockerfile:11-16,34 @ cef8ec2] |

**The "RUN-free runtime stage ⇒ no QEMU" claim is CONFIRMED**, from three independent places:
1. The runtime stage contains only `FROM`, four `COPY --from=build --chown`, `USER`, `WORKDIR`, `ENTRYPOINT` — **zero `RUN`** [SRC Dockerfile:87-97 @ cef8ec2].
2. `docker-check` builds `linux/amd64,linux/arm64` on an amd64 runner with **no `docker/setup-qemu-action`** anywhere in the repo (grep across `.github/`, `justfile`, `Dockerfile` finds only prose mentions) [SRC .github/workflows/ci.yml:214-218,229-238 @ cef8ec2]. The comment states this is deliberately a *guard*: adding a `RUN` makes the arm64 build start needing QEMU and fail.
3. The release publish job builds both platforms the same way with no QEMU setup [SRC .github/workflows/release.yml:186 @ cef8ec2].

**Caveat worth carrying into any arm64 conclusion:** arm64 is validated to *build*, but is **never runtime-smoked**. `docker-publish` says so outright [SRC .github/workflows/ci.yml:382-384 @ cef8ec2], and the release smoke runs `docker run` on an amd64 runner against the pushed digest [SRC .github/workflows/release.yml:210-214 @ cef8ec2], so it too exercises only the amd64 manifest. [INFERRED] Risk is low (identical arch-neutral jar on a stock arm64 JRE) but non-zero for the `sqlite-jdbc` / `zstd-jni` native extraction paths, which are precisely what the smoke exists to cover.

---

## Q4 — Entrypoint (exact)

```
ENTRYPOINT ["java", "-jar", "/opt/swath/swath.jar"]
```
[SRC Dockerfile:97 @ cef8ec2], and confirmed in the **published** 0.2.0 image config for *both* arches: `Entrypoint: ["java","-jar","/opt/swath/swath.jar"]`, `Cmd: null` [3P image config blobs].

**Where a correct argv starts: at the subcommand.** There is no `CMD`, no shell wrapper, no launcher script. Downstream argv is appended directly after `swath.jar`, so the first appended token is what the CLI sees as `argv[0]` — i.e. `list`, `resume`, … or a global flag such as `--help` / `--version`. Do **not** prepend `swath` or `java -jar`.

Correct: `docker run --rm ghcr.io/varveio/swath@sha256:ef1aca…  list s3://bucket/prefix/ --no-sign-request -o /out/data --format parquet`

Independently corroborated by CI, which smokes exactly this shape: `docker run --rm swath:ci --help` [SRC .github/workflows/ci.yml:249 @ cef8ec2] and `docker run --rm -v "$workdir:/out" "$image" \ …` [SRC scripts/ci/smoke-container.sh:80 @ cef8ec2].

---

## Q5 — Runtime shape

- **User/UID:** `USER 10001:10001` — a **numeric** UID with no named user, deliberately, so the stage stays `RUN`-free (no `useradd`), so Kubernetes can verify `runAsNonRoot: true` at admission, and so it works under OpenShift's arbitrary-UID model; 10001 is high to avoid host-UID collisions on shared volumes [SRC Dockerfile:75-83,94 @ cef8ec2]. Confirmed in the published config: `User: "10001:10001"` [3P].
- **Workdir:** `/opt/swath` [SRC Dockerfile:95 @ cef8ec2], confirmed [3P]. Contents: `swath.jar`, `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md`, all `--chown 10001:10001` [SRC Dockerfile:89-92 @ cef8ec2].
- **PID 1 / signals:** `java` is PID 1 in exec form, so SIGTERM/SIGINT reach the JVM directly; no tini/init because swath spawns no child processes [SRC Dockerfile:68-70 @ cef8ec2].
- **JVM options: `JAVA_TOOL_OPTIONS`, confirmed, and there is no launcher script.** `DEFAULT_JVM_OPTS` is empty and nothing is baked into the entrypoint [SRC Dockerfile:70-73 @ cef8ec2], [DOC docs/packaging-and-docker.md:197-206]. Critically, **`JAVA_OPTS` / `SWATH_OPTS` have NO effect in the container** — those are read by the `installDist` Gradle launcher script, which the image does not contain. The fat jar instead carries `Enable-Native-Access: ALL-UNNAMED` as a **manifest attribute** so `--enable-native-access` need not be passed on the command line [SRC swath-cli/build.gradle.kts:147-157 @ cef8ec2]. No `JAVA_TOOL_OPTIONS` is set in the image `Env` (only the base image's `PATH`/`JAVA_HOME`/locale/`JAVA_VERSION=jdk-25.0.3+9`) [3P].
- **Read-only root filesystem:** **not supported as-is** without a writable temp. [INFERRED] `sqlite-jdbc` extracts a native `.so` from its packaged resource path at runtime — this is the stated reason shading does no `relocate(...)` [SRC swath-cli/build.gradle.kts:161-163 @ cef8ec2], and the smoke script exists partly to cover "the sqlite native-library extraction" [SRC scripts/ci/smoke-container.sh:6-8 @ cef8ec2]. That extraction targets `java.io.tmpdir`, and nothing in the repo overrides it (no `java.io.tmpdir` / `org.sqlite.tmpdir` reference in any `src/main`). So `--read-only` would need `--tmpfs /tmp` (plus a writable output mount). Neither the Dockerfile nor any doc mentions read-only rootfs at all — grep for `read-only`/`readOnlyRootFilesystem`/`tmpfs` across `docs/*.md` returns nothing. Treat read-only as **untested by upstream**.
- **No `VOLUME`, no `EXPOSE`** in the image config [3P] — the output directory must be bind-mounted by the caller.
- **Write-permission trap, worth surfacing to whoever runs it:** the UID-10001 default means a host output dir you own is not writable by the container; upstream's own guidance is `--user "$(id -u):$(id -g)"` rather than `chmod 777` [DOC docs/install.md:128-142], while the CI smoke deliberately does *not* pass `--user` so it exercises the default identity [SRC scripts/ci/smoke-container.sh:54-58 @ cef8ec2].

---

## Q6 — Provenance & health, for a Metadata section

**License.** Apache-2.0. `LICENSE` is the stock Apache 2.0 text; `NOTICE` is `Copyright 2026 Varve Systems Ltd`, and explicitly names `THIRD_PARTY_NOTICES.md` — not itself — as the authoritative attribution record [SRC NOTICE:1-12 @ cef8ec2], [SRC LICENSE @ cef8ec2]. GitHub reports `spdx_id: Apache-2.0` [3P GitHub repo API], and the published image carries `org.opencontainers.image.licenses=Apache-2.0` on dev builds [3P]. All three legal files are shipped **inside** the image at `/opt/swath/` [SRC Dockerfile:90-92 @ cef8ec2] and inside the jar and dist archives [SRC swath-cli/build.gradle.kts:136,166-172 @ cef8ec2].

**Third-party licensing posture — unusually rigorous, and worth saying so.**
- `THIRD_PARTY_NOTICES.md` is 1,787 lines covering **107 runtime artifacts**, machine-generated from the resolved `:swath-cli:runtimeClasspath` (the exact shaded closure) and **staleness-gated**: `verifyThirdPartyNotices` re-renders and byte-compares, failing the build with "THIRD_PARTY_NOTICES.md is stale" [SRC build.gradle.kts:121-145 @ cef8ec2]. `shadowJar` `dependsOn` it, so you cannot produce a jar with drifted notices [SRC swath-cli/build.gradle.kts:137 @ cef8ec2].
- A jk1 `checkLicense` allow-list gate runs on every PR [SRC .github/workflows/ci.yml:69-75 @ cef8ec2]. Bare GPL/LGPL/AGPL are deliberately absent from the allow-list, so a GPL/AGPL-only transitive dep **fails the build**; there are no per-module exemptions and the config says none should be added [SRC config/license/allowed-licenses.json @ cef8ec2]. Dual-licensed deps (Logback EPL/LGPL, CDDL/GPL+CPE) pass on their permissive option.
- Hadoop's HDFS/YARN/ZooKeeper/Curator/Kerberos stack is excluded at `configurations.all { exclude(...) }` level so it never enters any distribution [DOC docs/packaging-and-docker.md:104-116].

**Supply chain.** Every GitHub Action is SHA-pinned; both Dockerfile `FROM`s are digest-pinned; `.github/dependabot.yml` exists specifically to keep those pins from rotting, grouped weekly per ecosystem [SRC .github/dependabot.yml:1-40 @ cef8ec2]. The Gradle wrapper is checksum-validated before any other gate can run [SRC .github/workflows/ci.yml:57-58 @ cef8ec2]. The DuckDB CLI downloaded in the publish job is SHA256-checked before execution, with the reasoning spelled out [SRC .github/workflows/release.yml:196-204 @ cef8ec2]. Release publishing is double-gated: the protected `public-release` environment (manual approval — I watched it hold for 12 minutes) **and** a `PUBLIC_RELEASE_ENABLED` repo-variable kill-switch, plus a repository-visibility check [SRC .github/workflows/release.yml:100-110 @ cef8ec2].

**Activity / maturity.**
- Repo created `2026-07-25`; last push `2026-08-02T12:07:48Z` — **development is hot, roughly ten days old** [3P GitHub repo API]. Public, 0 stars.
- **Solo maintainer**: `sagiba`, 48 commits, sole contributor [3P GitHub contributors API]. Also the sole `public-release` environment reviewer — a single point of failure for releases.
- **Release cadence: two releases in six days** — v0.1.0 on 2026-07-27, v0.2.0 on 2026-08-02 [3P GitHub Releases API].
- **23 open issues, 4 open PRs** (of which 4 are Dependabot branches) [3P GitHub search API].
- **CI health flag:** the `Nightly deep verification` scheduled workflow has been `failure` on **every** run I can see — 2026-07-30, 07-31, 08-01, 08-02, all on the same stale head `bf0bac8` [3P GitHub Actions runs API]. PR/`main` CI is otherwise green. Worth flagging to whoever writes the report's maturity assessment; I did not investigate the cause (out of my area).
- Version discipline is enforced mechanically: `verifyReleaseVersion` fails unless the git tag equals `v` + `gradle.properties`'s `version`, and only `X.Y.Z` / `X.Y.Z-rc.N` grammar is accepted [SRC build.gradle.kts:22-47 @ cef8ec2], [SRC gradle.properties:12 @ cef8ec2]. `just release` produces two commits (`Release vX.Y.Z`, tagged; then `Begin … development` restoring `-SNAPSHOT`) so `main` never sits on a released version [SRC justfile:107-157 @ cef8ec2].
- Container tag scheme: releases own `X.Y.Z`, `X.Y`, `latest` (bare major only from 1.0 onward — a `0` tag is deliberately suppressed); merges to `main` publish `sha-<gitsha>` + `main`; dispatch publishes `sha-` only, to keep internal branch names off the public package [SRC .github/workflows/release.yml:153-173 @ cef8ec2], [SRC .github/workflows/ci.yml:360-375 @ cef8ec2], [DOC RELEASING.md:22-32].

---

## Two operational notes for Stage B

1. **Use the digest, and use the un-prefixed tag.** `ghcr.io/varveio/swath@sha256:ef1aca9ab473f133acceb5730ff88d52abaaa89e773801cdb62deff51f9909b0` is the pinned, signed, smoked artifact whose `image.revision` label equals our frozen SHA. If you must use a tag it is `0.2.0` — **`v0.2.0` is a 404**, a trap that follows naturally from the git tag being `v`-prefixed.
2. **A near-equivalent fallback exists but you no longer need it.** `ghcr.io/varveio/swath:main` = `sha-b521167` = `sha256:d2a4b4a496bb266bc6d5d3cf5f8d594afb13e771388fe5ce563eed5081a9a26a`, built from `b521167`, which differs from `cef8ec2` by **one line** — `version=0.2.0` → `version=0.2.1-SNAPSHOT` in `gradle.properties` (verified with `git diff cef8ec2 b521167`, a single-file, one-line diff). Identical code; it would merely self-report `0.2.1-SNAPSHOT`. Prefer the release image.
