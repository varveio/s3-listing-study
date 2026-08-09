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

Host-side Docker control-plane calls remain finite: ordinary image inspection,
container creation/start/probe operations have a 30-second bound and cleanup
operations have a 10-second bound. New attempts run the shared Python attempt
runner inside a derived subject image; Docker or Batch schedules that image and
provides an output location, but does not time the tool, collect listing bytes,
normalize output, or interpret the result. Those lifecycle and artifact
semantics belong to the in-image runner. The host preflight, fixed bridge,
firewall policy, digest-pinned/no-pull image discipline, and bounded cleanup
remain outside the image and must pass before any networked subject starts.

New attempt output is captured by the in-image runner as byte streams on
worker-local storage and then committed as attempt artifacts. Docker or Batch
text logs are diagnostics only; they are not the listing-data channel and are
not part of a completeness claim. A result whose raw bytes exist only in
`docker logs`, a remote log driver, or a scheduler log stream is not a completed
new attempt.

Historical smoke receipts are not rewritten. They were produced by the retired
shell wrapper, which created evidence containers with
`--log-driver=json-file --log-opt max-size=-1`, inspected that effective logging
configuration before start, captured stdout/stderr with `docker logs`, and
recorded the validated driver/config hash in `run.meta`. Those fields remain
facts about the committed historical `receipt.md`/`run.meta` records and the
read-only verifier paths that audit them; they are not the channel for new
attempt evidence. Reference re-lists for those historical verifier paths retain
their recorded behavior. Security probes still do not produce listing evidence,
and their output must not be promoted into a run record.

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

Credential starvation in the in-image Python attempt runner remains defense in
depth: anonymous child processes have AWS credential and profile variables
removed and metadata discovery disabled, and receive no mounted profiles.
`AWS_EC2_METADATA_DISABLED=true` is cooperative SDK configuration, not proof
that the runner is identity-free.

For the `local` adapter, the identity claim rests on using a dedicated non-cloud
host plus rejection of recognized cloud metadata. A future GCP/AWS adapter must
prove attachment state through the provider control plane and provide its own
metadata targets. Metadata blocking and identity proof are deliberately separate:
a blocked metadata endpoint does not prove that no identity is attached.

## The authenticated stratum's AWS credential

Most of the study lists public buckets anonymously. Some questions cannot be
asked that way — versioned listings on a bucket that does not expose them
publicly, requester-pays, a corpus that is simply not public — so those cases run
in an authenticated stratum with an AWS credential.

That credential is a **blast-radius decision before it is a convenience**. It
lives in a secret that a benchmark task reads, and the tools reading it are
eleven third-party programs the study does not control. So it is scoped to do the
one thing the study needs — list other people's buckets — and explicitly denied
the ability to touch anything of the operator's own.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyOwnOrganization",
      "Effect": "Deny",
      "Action": "s3:*",
      "Resource": "*",
      "Condition": {
        "StringEquals": { "aws:ResourceOrgID": "${aws:PrincipalOrgID}" }
      }
    },
    {
      "Sid": "DenyOwnAccount",
      "Effect": "Deny",
      "Action": "s3:*",
      "Resource": "*",
      "Condition": {
        "StringEquals": { "aws:ResourceAccount": "${aws:PrincipalAccount}" }
      }
    },
    {
      "Sid": "AllowListingAnyOtherBucket",
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket",
        "s3:ListBucketVersions",
        "s3:GetBucketLocation"
      ],
      "Resource": "*"
    }
  ]
}
```

Four things about this policy are deliberate.

**No account or organization ID appears in it.** Both denies compare a resource
key against the caller's own via a policy variable —
`aws:ResourceAccount` against `${aws:PrincipalAccount}`, and `aws:ResourceOrgID`
against `${aws:PrincipalOrgID}`. The policy is therefore publishable as written
and correct in any account that applies it, with no edit and nothing to leak.

**Both denies, not one.** `aws:PrincipalOrgID` is absent for a principal outside
an organization, and an unresolved variable is not a safe thing to rest a deny
on. The account-level deny uses a key that is always present, so it holds
regardless. The organization-level deny then widens the same guard to sibling
accounts.

**A bucket outside any organization is unaffected by the org deny.**
`aws:ResourceOrgID` is absent for such a bucket, so `StringEquals` is false and
the deny does not fire — which is the intended outcome, since those are exactly
the buckets the study measures.

**No `s3:GetObject`.** The study lists; it does not read object bodies. Granting
object reads would widen a credential that eleven third-party binaries handle,
for no measurement this repository performs. Add it only alongside a case that
needs it, and say so in that case's record.

`s3:ListBucketVersions` is not optional despite looking like it. Five tools carry
a versioned-listing mode — `aws-cli s3api-versions-text`, `s5cmd allversions`,
`minio-mc versions-json`, `s3kor list-versions`, `ps3 list-versions` — and every
one of them calls `ListObjectVersions`, which that permission governs. A policy
granting only `s3:ListBucket` fails all five in a way that reads as a tool defect
rather than a permissions gap.

Delivery is an identity, not a flag: only the authenticated stratum's service
account can read the secret, so an anonymous case cannot obtain the credential
even if its job spec asks for it. See the `create_aws_credentials_secret` variable
in [`infra/terraform/modules/gcp/s3-listing-study`](../../infra/terraform/modules/gcp/s3-listing-study/README.md).

That is a statement about who can *obtain* the credential. It is not a statement
about how the credential reaches a subject once a job legitimately holds it.
That step is a flag and an ambient variable — `--auth authenticated` plus
`S3_STUDY_AWS_CREDENTIAL` — and the engine fails closed on the dangerous half of
the pairing: a run declaring itself anonymous while credential material sits in
its environment is refused rather than recorded, so no anonymous receipt can come
from a credentialed process. See
[`harness/README.md`](../../harness/README.md) § Authenticated attempts.

A runner may also hold the credential directly, so that a credentialed case can
be run rather than only submitted; `runner_reads_aws_credentials` gates it and is
off unless a deployment asks for it. Custody and execution are separable, and it
is execution that the profile above constrains: a host holding the credential
must not also be the host that executes subject containers. Where the two
coexist on a development box, subjects run on a container network that cannot
reach the metadata endpoint, the host-level check does not pass because the host
is a real cloud VM, and the resulting attempts are local development captures
that do not carry the `s3-listing-study-v1` profile. The provider adapter this
section calls for remains the prerequisite for anything that claims it.

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
