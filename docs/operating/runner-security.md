# Runner security

This is the authoritative security contract for running third-party subject
images. Anyone setting up a runner or executing networked subjects must
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
digest-pinned campaign image must already be present before setup. Prefer a
separate disposable, identity-free builder; the acceptable fallback is the same
disposable host used sequentially for build/pull first and gated execution
second. During a campaign, no build and no mutable-tag pull occurs.

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
the rules are rendered. Ordinary public egress remains available. Denials use
`REJECT` so a forbidden request fails promptly instead of consuming a benchmark
timeout.

## What the gate proves

The gate enforces one property, on every networked invocation: **this run is
anonymous by construction.** It is not a host-integrity attestation and does not
verify a recorded snapshot of a provisioned box.
[`runner-security-check.sh`](../harness/runner-security-check.sh) runs before
every networked harness container and checks, live:

- **no ambient credentials** — the AWS/GCP/Azure credential variables an SDK
  would silently pick up are absent from the environment this run inherits;
- **no instance-metadata service on the host** — recognized GCP, AWS, and Azure
  metadata endpoints do not answer, so the `local` adapter refuses a cloud
  runner;
- **the subject bridge is the fixed profile** — `s3-listing-study-subjects` is
  an IPv4-only bridge on `s3study0` with the fixed subnet/gateway and
  inter-container communication disabled, and Docker's firewall backend is
  `iptables`. The firewall hooks are keyed on the bridge interface, so a bridge
  that is not this one would leave the policy covering nothing;
- **the private-network deny is in force** —
  [`render-policy.sh`](../harness/security/render-policy.sh) renders the
  expected owned-chain bodies from
  [`policy.v1.env`](../harness/security/policy.v1.env) plus the Docker network
  subnets that exist right now, and
  [`validate-firewall-state.sh`](../harness/security/validate-firewall-state.sh)
  requires the live filter table to carry exactly those bodies, reached through
  a bridge-specific jump that is rule 1 of both `INPUT` and `FORWARD`, unique,
  in both IPv4 and IPv6. Live state is read through a root-owned, no-argument
  helper the runner may call with passwordless `sudo`;
- **the canary agrees** — a digest-pinned probe container on that bridge finds
  `169.254.169.254:80` unreachable and public S3 reachable over HTTPS.

The expected policy is rendered per run from files in this repository, so there
is no readiness record to drift, no host/boot binding, and no digest of one
box's `iptables-save` output that a reader could not re-derive. A Docker network
created or removed after the rules were installed changes the rendered policy,
which no longer matches the live one, and the run stops until the rules are
reinstalled. The rules are not a persistent boot policy: after a reboot they are
gone, the live table no longer matches, and the gate fails closed.

What this does *not* prove: it is not a complete host-firewall attestation (the
comparison covers the filter table's study path, not NAT/mangle), the probe is a
canary for the canonical policy rather than proof by absence, and the bridge is
IPv4-only so the IPv6 path is validated structurally with no behavioral canary.
Receipts cite `firewall_policy_sha256` — the digest of the versioned policy
source found in force, not of a live capture.

The current MVP supports Docker's `iptables` firewall backend only. It detects
the backend and refuses nftables or an unreportable backend. Adding nftables
requires an equivalent, inspectable forward/input hook; silently falling back to
a bridge without host policy is forbidden.

## Operator procedure: local runner

Setup is a documented human procedure, not a script: it runs once per runner,
needs root, and every property it establishes is re-verified per run by the gate
above. Before starting:

- Use a disposable host and quiesce subject execution while setting it up.
  Never run this on a shared workstation.
- Install Docker, `iptables`/`ip6tables`, `jq`, `curl`, Python 3, and `sudo`.
- Pull or build all campaign images first, including the probe image.
- Select a small trusted probe image that contains POSIX `sh`, `wget`, and an
  `nc` implementation supporting `-z`, and pass it by digest — never by a
  mutable tag.
- Disable `firewalld` and any other manager that can rewrite iptables while a
  campaign is running.

Run as root, substituting the actual unprivileged harness user, the probe image
digest, and the host's default-uplink MTU:

```sh
# 1. the study bridge: IPv4-only, ICC off, MTU pinned to the uplink
docker network create --driver bridge \
  --subnet 172.30.0.0/24 --gateway 172.30.0.1 \
  --opt com.docker.network.bridge.name=s3study0 \
  --opt com.docker.network.bridge.enable_icc=false \
  --opt com.docker.network.driver.mtu=<uplink-mtu> \
  --ipv6=false s3-listing-study-subjects

# 2. the deny policy, rendered from the versioned source and the current
#    Docker networks — the same rendering the gate compares against
harness/security/render-policy.sh /tmp/rules.v4 /tmp/rules.v6
iptables  -w -N S3STUDY_FWD; iptables  -w -N S3STUDY_IN
ip6tables -w -N S3STUDY_FWD; ip6tables -w -N S3STUDY_IN
iptables-restore  --wait --noflush </tmp/rules.v4
ip6tables-restore --wait --noflush </tmp/rules.v6
iptables  -w -I INPUT   1 -i s3study0 -j S3STUDY_IN
iptables  -w -I FORWARD 1 -i s3study0 -j S3STUDY_FWD
ip6tables -w -I INPUT   1 -i s3study0 -j S3STUDY_IN
ip6tables -w -I FORWARD 1 -i s3study0 -j S3STUDY_FWD

# 3. the read-only state helper and its no-argument sudo grant
install -d -o root -g root -m 0755 /usr/local/libexec /etc/s3-listing-study
install -o root -g root -m 0755 harness/security/firewall-state.sh \
  /usr/local/libexec/s3-study-firewall-state
printf '%s ALL=(root) NOPASSWD: /usr/local/libexec/s3-study-firewall-state ""\n' \
  <runner-user> >/etc/sudoers.d/s3-listing-study-runner-security
chmod 0440 /etc/sudoers.d/s3-listing-study-runner-security
visudo -c

# 4. which probe image carries the canary
printf 'probe_image=example/probe@sha256:<64-hex-digest>\n' \
  >/etc/s3-listing-study/runner.env
chmod 0644 /etc/s3-listing-study/runner.env

# 5. prove it, as the harness user
harness/runner-security-check.sh --bucket <registered-public-bucket> \
  --region <bucket-region>
```

Step 5 is the acceptance test: it is the same gate every run passes through, so
a successful setup and a successful run are the same evidence. Re-run steps 1–4
after a reboot, after adding or removing a Docker network, or after changing
the policy source — the gate fails closed until you do.

The S3 canary uses virtual-hosted HTTPS and intentionally supports only dotless
3–63 character bucket names made from lowercase letters, digits, and hyphens;
this avoids wildcard-certificate ambiguity. All current registered buckets fit
that contract.

`/etc/s3-listing-study/runner.env` is operator configuration, not an
attestation: it names the probe image and nothing else. It stays root-owned and
not group/world writable so an unprivileged process cannot swap the canary for
an image that always exits 0. The runner user gets passwordless access only to
the argument-less helper that prints filter-table state, never to general
`iptables`. Deleting the config immediately fails closed without changing
firewall state.

The policy blocks the study bridge from every Docker network present when the
rules are rendered. BuildKit/build networks and mutable-tag resolution are
therefore outside the execution phase, not exceptions to the boundary.

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
runs the full setup procedure and gate on an ephemeral disposable runner
matching this profile. A generic hosted CI VM is useful for experimentation but is not
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
