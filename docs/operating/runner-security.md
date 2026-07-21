# Runner security

This is the authoritative security contract for running third-party subject
images. Anyone provisioning a runner or executing networked subjects must
follow it. It applies to future runs; historical receipts describe the profile
that was active when they were produced and are not rewritten.

## Current profile

`s3-listing-study-v1` runs on a dedicated, disposable Linux host with Docker and
no attached workload identity, ambient cloud credentials, private checkouts, or
unrelated workloads. The first implemented provider adapter is `local`: it is
for a genuinely local or bare-metal runner and refuses a host on which known
GCP, AWS, or Azure metadata is present. It is not a substitute for a cloud
control-plane identity check. A cloud VM needs a provider adapter that proves
that no service account or instance profile is attached.

Every networked subject and trusted reference container uses the fixed
`s3-listing-study-subjects` user-defined bridge. The bridge is IPv4-only, has
inter-container communication disabled, and pins its MTU to the host's default
uplink. Containers publish no ports and receive no host-directory or Docker
socket mounts. The harness adds:

```text
--pull=never
--network s3-listing-study-subjects
--cap-drop ALL
--security-opt no-new-privileges:true
```

Offline helpers, such as best-effort version detection, retain `--network none`
and also use `--pull=never`; execution never resolves a missing image.
Forced non-root execution and read-only root filesystems are deferred until they
can be compatibility-tested across all upstream images.

This profile covers networked run, trusted-reference, and security-probe
containers. It does not sandbox `docker build`, image pulls, or BuildKit. Every
digest-pinned campaign image must already be present before provisioning. Prefer
a separate disposable, identity-free builder; the acceptable fallback is the
same disposable host used sequentially for build/pull first and provisioned
execution second. During a campaign, no build and no mutable-tag pull occurs.

Harness Docker control-plane calls are finite: ordinary inspect/create/start/
wait/log/probe operations have a 30-second bound and cleanup operations have a
10-second bound. Smoke lifecycle errors and version/reference/probe failure
messages that include a wrapper status label both status 124 (TERM deadline) and
137 (follow-up KILL) as timeouts. Readiness checks that report a failed
invariant without retaining a raw wrapper status describe the bounded operation
instead.
Cleanup reconciliation is required after every
nonzero Docker client result that could have created or started stable-name
state—including status 125—not only after timeout statuses. Smoke subjects use
a wrapper-owned stable name across separate create and start calls, so a timed-
out call still leaves a deterministic bounded cleanup target. Probes and
reference re-lists also use stable names when a timed-out `docker run` could
otherwise outlive its client; the offline version probe is likewise networkless,
no-pull, bounded, and stably named. These bounds stop the harness hanging
indefinitely; they cannot prove cleanup succeeded while the Docker daemon
remains unavailable, so discard rather than reuse a disposable runner after an
unresolved cleanup failure.

Every evidentiary listing container is created with
`--log-driver=json-file --log-opt max-size=-1`: a local driver with rotation and
size truncation disabled. Before starting a smoke subject, the harness inspects
the effective container configuration and requires exactly `json-file` plus the
single `max-size=-1` option. A rotating, size-limited, remote, additional, or
unknown option fails closed. Only after that check does the smoke record capture
the driver, a SHA-256 of the canonical configuration, and safely encoded option-
key names. Raw option values are never persisted because rejected remote logging
options can contain endpoints or credentials.

Smoke output is later collected with `docker logs`, so the exact inspected
contract is part of its completeness claim. Reference re-lists consume Docker's
attached stdout directly rather than calling `docker logs`, but use the same
explicit non-rotating local configuration to avoid daemon-default differences.
Security probes do not produce listing evidence and may use the daemon's logging
default; their output is never promoted into a run receipt. The unlimited log
contract prevents Docker rotation, not host disk exhaustion; disposable-runner
capacity remains an operator responsibility.

The bridge alone is not the security boundary. The host firewall rejects
bridge-originated access to the host, loopback, link-local/metadata, RFC 1918,
carrier-grade NAT, internal IPv6 ranges, and every Docker network present when
the runner is provisioned. Ordinary public egress remains available. Denials use
`REJECT` so a forbidden request fails promptly instead of consuming a benchmark
timeout.

## What the harness proves

Provisioning and execution have separate responsibilities:

- [`runner-security-provision.sh`](../harness/runner-security-provision.sh)
  creates/verifies the bridge, installs the versioned iptables policy, runs the
  live controls, and atomically mints a root-owned, host-and-boot-bound readiness
  record only after all checks pass.
- [`runner-security-check.sh`](../harness/runner-security-check.sh) runs before
  every networked harness invocation. It checks the host, boot, Docker daemon,
  complete Docker-network inventory, bridge configuration, firewall backend,
  policy and installed-helper digests, canonical live firewall state, absence of
  recognized cloud metadata and ambient credential variables, link-local denial
  from the subject bridge, and public S3 reachability from that bridge.
- [`runner-security-live-test.sh`](../harness/runner-security-live-test.sh) is an
  operator-run integration validator. On disposable control resources it proves
  the probe can detect host and peer reachability, then proves the production
  bridge denies host, same-bridge peer, other-network, and metadata access while
  retaining public S3 HTTPS.

The readiness check requires the bridge-specific jump to be rule 1 of both
`INPUT` and `FORWARD` for IPv4 and IPv6, requires each jump to be unique, and
compares the exact owned-chain bodies with the installed rendered policy. The
canonicalized IPv4 and IPv6 **filter-table** hash is additional drift detection,
including rule order. `iptables-save` timestamp comments are removed and mutable
chain counters are zeroed before hashing; neither is policy state. The hash does
not cover NAT/mangle tables and is not a complete host-firewall attestation. It
is not the safety proof by itself. Probes are canaries for the canonical policy,
not proof by absence. A Docker network created or removed after provisioning
changes the inventory digest and stops the harness until the policy is
reprovisioned.
A reboot also invalidates readiness because this MVP deliberately does not
pretend its live rules are a persistent boot policy.

The current MVP supports Docker's `iptables` firewall backend only. It detects
the backend and refuses nftables or an unreportable backend. Adding nftables
requires an equivalent, inspectable forward/input hook; silently falling back to
a bridge without host policy is forbidden.

The bridge itself is IPv4-only. Both IPv4 and IPv6 filter paths are validated
structurally, but this profile has no behavioral IPv6 network canary.

## Operator procedure: local runner

Before provisioning:

- Use a disposable host and quiesce subject execution while provisioning.
  Never run provisioning on a shared workstation.
- Install Docker, `iptables`/`ip6tables`, `jq`, `curl`, Python 3, and `sudo`.
- Pull or build all campaign images first.
- Select a small trusted probe image that contains POSIX `sh`, `wget`, and an
  `nc` implementation supporting `-z`, `-l`, `-k`, and `-p`, and pass it by
  digest—never by a mutable tag. Plain BusyBox `nc` is not sufficient.
- Disable `firewalld` and any other manager that can rewrite iptables while a
  campaign is running; provisioning refuses an active `firewalld` service.

Run as root, substituting the actual unprivileged harness user and a registered
public bucket:

```sh
sudo harness/runner-security-provision.sh \
  --runner-user study-runner \
  --probe-image example/probe@sha256:<64-hex-digest> \
  --bucket <registered-public-bucket> \
  --region <bucket-region>
```

The S3 canary uses virtual-hosted HTTPS and intentionally supports only dotless
3–63 character bucket names made from lowercase letters, digits, and hyphens;
this avoids wildcard-certificate ambiguity. All current registered buckets fit
that contract.

The script refuses known cloud metadata, a non-iptables Docker backend, a
conflicting fixed subnet, a mutable/missing probe image, or a mismatched existing
bridge. It installs only study-named chains and hooks. Initial hook creation is
performed while the runner is quiescent; each owned chain body is then applied
with `iptables-restore`/`ip6tables-restore`, and no readiness record exists until
both families and the live validator pass.

The unprivileged runner gets passwordless access only to a root-owned helper that
prints the filter-table state. It does not get general passwordless `iptables`
access. The readiness and rendered policy files
live under `/etc/s3-listing-study/`; deleting the readiness record immediately
fails closed without changing firewall state.

To show the integration-test plan without touching Docker:

```sh
harness/runner-security-live-test.sh \
  --probe-image example/probe@sha256:<64-hex-digest> \
  --bucket <registered-public-bucket> --region <bucket-region> --print-plan
```

The policy intentionally blocks
the study bridge from all Docker networks known at provisioning and requires the
network inventory to remain fixed for the campaign. BuildKit/build networks and
mutable-tag resolution are therefore outside the execution phase, not exceptions
to the boundary.

## Orchestrator workspace staging

Workspace staging happens outside subject containers, but it still handles a
mutable path supplied by the operator. `stage-workspace.sh` therefore accepts
only registered tool names, rejects broad and repository-owned staging roots,
enters the canonical root with `cd -P`, and thereafter mutates only fixed
single-component names in that directory. An exclusive `flock` on the root
serializes cooperating publishers.

Each dispatch is assembled and fully validated as a sibling
`.<tool>-work.new.*` generation before the stable name changes. An existing
stable entry—directory or symlink—is renamed without following it to a unique
`.<tool>-work.retired.*/workspace`; the new generation is then renamed to
`<tool>-work`. Both transitions use GNU
`mv --no-copy --no-target-directory`, so a failed rename cannot silently fall
back to a recursive copy. There is a short stable-name gap between retirement
and publication.

The staging script does not recursively delete or install a failure cleanup
trap. Validation and rename failures retain and report unpublished/retired
generation paths for inspection. Reclaim them only as a deliberate operator
action, or by disposing of the dedicated staging filesystem/runner. The host
must provide util-linux `flock` and GNU `mv` with `--no-copy` and
`--no-target-directory` support.

## Identity claim

Credential starvation in `smoke-run.sh` remains defense in depth: anonymous
runs receive empty AWS credential values, nonexistent credential/config paths,
and no mounted profiles. `AWS_EC2_METADATA_DISABLED=true` is cooperative SDK
configuration, not proof that the runner is identity-free.

For the `local` adapter, the identity claim rests on using a dedicated non-cloud
host plus rejection of recognized cloud metadata. A future GCP/AWS adapter must
prove attachment state through the provider control plane and provide its own
metadata targets. Metadata blocking and identity proof are deliberately separate:
a blocked metadata endpoint does not prove that no identity is attached.

## Residual risk and future work

Accepted for this phase:

- subjects may contact arbitrary public Internet destinations, including an
  attacker-controlled S3 bucket;
- the shared host kernel and Docker daemon remain part of the trusted computing
  base;
- resource exhaustion (logs, disk, PIDs, sockets/conntrack) is not solved here;
- credentialed subject execution remains unimplemented.

Deferred options are S3/exact-bucket-only egress, a transparent S3-aware proxy,
general Internet denial, mandatory non-root/read-only containers, per-tool VMs
or microVMs/gVisor, cloud provider adapters, resource/log bounds, and CI. These
are additions to this contract, not properties of `s3-listing-study-v1`.

Future CI has two distinct lanes: ordinary pull-request checks run the static and
fake-host suites without privileged mutation; a manual or scheduled integration
runs the full activation sequence on an ephemeral disposable runner matching
this profile. A generic hosted CI VM is useful for experimentation but is not
identity or firewall proof unless an adapter validates that environment.

## Benchmark gate

A user-defined bridge adds NAT/connection-tracking CPU work; it does not add a
network round trip. Before benchmark methodology freezes, compare host versus
bridge using pinned trusted controls only, alternating arms and recording DNS,
connect, TLS, first-byte, total time, host CPU, conntrack pressure, and socket
occupancy. Pre-register equivalence/no-regression from the study's meaningful
effect and variance policy. No third-party subject regains host networking for
this test, and this document adopts no arbitrary fixed sub-millisecond threshold.

## Source policy

The portable CIDRs and reject modes are versioned in
[`policy.v1.env`](../harness/security/policy.v1.env). The accepted design rationale
is retained in internal working notes (not published).
