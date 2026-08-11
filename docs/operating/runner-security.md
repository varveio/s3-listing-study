# Runner security

This is the authoritative security contract for running third-party subject
images. The tools are cooperative third-party software, not adversarial
workloads. The controls below separate attempts, keep identities narrow, and
limit the consequences of mistakes; they are not a hostile-code sandbox.
Historical receipts describe the profile active when they were produced and
are not rewritten.

## Current execution profiles

### GCP Batch: cooperative production profile

Production benchmark attempts run in an otherwise disposable benchmark project,
one task on one fresh Batch VM. Comparable cases declare the same machine type,
vCPU count, memory, and container ceiling. No manager checkout, unrelated
workload, or second attempt shares that VM.

The Batch VM metadata server is intentionally reachable from the container.
The in-worker GCS uploader needs the task service-account token it supplies.
That access is acceptable because the task identity is bounded to:

- create new objects in the results bucket, with no bucket read or delete;
- pull the pinned derived image from Artifact Registry; and
- report Batch task state and write diagnostic logs.

Authenticated cases use a separate task identity that additionally reads the
one AWS credential secret. Anonymous cases use an identity without that grant.
The tools can use their task identity to create additional objects in the
results bucket; the study accepts that for cooperative tools, while IAM still
prevents reading or deleting results. Artifact uploads are also create-only, so
an execution cannot replace an existing attempt object.

There is no metadata-denial provider adapter or local firewall gate in front of
Batch. Requiring one would break the uploader and would not improve the bounded
identity described above. The production boundary is the fresh one-task VM,
least-privilege service account, digest-pinned image, and disposable project.

The derived image is the one Batch container. Inside it, the worker launches the
tool as a supervised subprocess tree; the tool is not nested in a second
container. Automatic Batch retries are set to 0, but execution identity still
does not assume a one-to-one scheduler outcome. The campaign model owns a stable
`run-<n>` ordinal (`run-1` for the current `reps: 1` campaign); every
worker-container execution mints a fresh attempt UUID and uploads create-only
beneath that manifest-known run prefix. If one run produces multiple UUID
children, the stateless campaign reconciler surfaces every result as duplicate
execution and selects none as canonical.

The manager reconciler lists each expected run prefix with GCS
`delimiter=/` to discover only immediate UUID children, then read each exact
`result.json`. It must not descend into or download raw listing output. Raw
artifacts stay in the same attempt tree and are fetched only for correctness
verification or a specific investigation. Its resumable contract is documented
in [campaign operations](campaign-operations.md).

### Local Docker: stricter manager-host profile

Local runs may share a more-privileged manager/runner host, so they retain the
`s3-listing-study-v1` Docker bridge and metadata-denial profile. The local gate
refuses a host on which recognized GCP, AWS, or Azure metadata is present; this
is a local-host rule, not a prerequisite or provider adapter for Batch.

Every local networked subject and trusted reference container uses the fixed
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

The local profile covers networked run, trusted-reference, and security-probe
containers. It does not sandbox `docker build`, image pulls, or BuildKit. Every
digest-pinned local image must already be present before setup. During a local
run, no build and no mutable-tag pull occurs. Batch instead pulls the frozen
digest from the campaign's Artifact Registry.

Local host-side Docker control-plane calls remain finite: ordinary image inspection,
container creation/start/probe operations have a 30-second bound and cleanup
operations have a 10-second bound. New attempts run the shared Python attempt
runner inside a derived subject image; Docker or Batch schedules that image and
provides an output location, but does not time the tool or collect listing
bytes. The worker times the subject, captures the raw streams, derives the small
summary after timing, and uploads the attempt. The local host preflight, fixed
bridge, firewall policy, digest-pinned/no-pull image discipline, and bounded
cleanup remain outside the image and must pass before a local networked subject
can claim the strict local evidence profile. Diagnostic smoke/e2e runs may omit
the full gate, but their output is not profile-backed evidence. These controls
do not run on Batch.

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

For local Docker, the bridge alone is not the security boundary. The host firewall rejects
bridge-originated access to the host, loopback, link-local/metadata, RFC 1918,
carrier-grade NAT, internal IPv6 ranges, and every Docker network present when
the rules are rendered. Ordinary public egress remains available. Denials use
`REJECT` so a forbidden request fails promptly instead of consuming a benchmark
timeout.

## What the local gate proves

When activated for a strict-profile local invocation, the gate enforces one
property: **no ambient host cloud identity or credential is available to the
subject.** An authenticated attempt may still receive its explicitly selected
AWS listing credential. The gate is not a host-integrity attestation and does
not verify a recorded snapshot of a provisioned box.
[`runner-security-check.sh`](../../harness/runner-security-check.sh) runs before
each local networked invocation that claims the strict profile and checks, live:

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
  [`render-policy.sh`](../../harness/security/render-policy.sh) renders the
  expected owned-chain bodies from
  [`policy.v1.env`](../../harness/security/policy.v1.env) plus the Docker network
  subnets that exist right now, and
  [`validate-firewall-state.sh`](../../harness/security/validate-firewall-state.sh)
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
needs root, and every property it establishes is re-verified per strict-profile
run by the gate above. Before starting:

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

Step 5 is the acceptance test: it is the same gate every profile-backed local
run passes through, so a successful setup and a successful run are the same
evidence. Re-run steps 1–4
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

## Identity claims

Credential starvation in the in-image Python attempt runner remains defense in
depth: anonymous child processes have AWS credential and profile variables
removed and metadata discovery disabled, and receive no mounted profiles.
`AWS_EC2_METADATA_DISABLED=true` is cooperative SDK configuration, not proof
that the runner is identity-free.

For local Docker, the identity claim rests on rejection of recognized cloud
metadata and the firewall-backed bridge. For GCP Batch, the claim is different
and explicit: an identity is attached, its metadata token is available to the
worker, and IAM bounds that identity to the permissions listed in the Batch
profile. Metadata denial is neither required nor desired there.

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

A runner may also hold the credential directly so a credentialed case can run
locally rather than only be submitted; `runner_reads_aws_credentials` gates
that grant and is off unless a deployment asks for it. A direct run still uses
the strict local Docker profile, and the selected credential reaches only an
authenticated attempt through `S3_STUDY_AWS_CREDENTIAL`. Batch uses the separate
authenticated task identity described above.

## Residual risk and future work

Accepted for this phase:

- subjects may contact arbitrary public Internet destinations, including an
  attacker-controlled S3 bucket;
- Batch subjects can reach metadata and use their bounded GCP task identity to
  create objects in the results bucket;
- for local Docker, the shared host kernel and Docker daemon remain part of the
  trusted computing base;
- resource exhaustion (logs, disk, PIDs, sockets/conntrack) is not solved here;
- authenticated execution is implemented and has run locally; Batch has the
  separate authenticated identity and secret grant described above.

Deferred options are S3/exact-bucket-only egress, a transparent S3-aware proxy,
general Internet denial, mandatory non-root/read-only containers, per-tool VMs
or microVMs/gVisor for local runs, resource/log bounds, and CI. These are
possible additions, not prerequisites for the cooperative Batch profile.

Future CI has two distinct lanes: ordinary pull-request checks run the static and
fake-host suites without privileged mutation; a manual or scheduled integration
runs the full setup procedure and gate on an ephemeral disposable runner
matching the strict local profile. A generic hosted CI VM is useful for
experimentation but does not carry the local profile unless that same gate
passes. Batch integration uses the cooperative Batch profile instead.

## Local-profile measurement status

The strict Docker bridge exists for local diagnostics on the manager/runner
host. Production comparative measurements run on fresh Batch VMs instead, so a
host-versus-bridge timing arm is not a benchmark activation gate and local runs
are not substituted for the declared Batch machine shape.

## Source policy

The portable CIDRs and reject modes are versioned in
[`policy.v1.env`](../../harness/security/policy.v1.env). The accepted design rationale
is retained in internal working notes (not published).
