#!/usr/bin/env bash
# Box-independent security regressions. All Docker, metadata, public-network,
# and privileged behavior is faked; this script never changes the host.
set -euo pipefail
export LC_ALL=C

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
HARNESS="$(cd -- "$HERE/.." && pwd)"
REPO_ROOT="$(cd -- "$HARNESS/.." && pwd)"
# shellcheck source=harness/runner-security-lib.sh
. "$HARNESS/runner-security-lib.sh"
work="$(mktemp -d)"; trap 'rm -rf -- "$work"' EXIT
fail=0
ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fail=1; }

args=(docker run --rm); security_append_network_args args
got="$(printf '<%s>' "${args[@]}")"
want='<docker><run><--rm><--pull=never><--network><s3-listing-study-subjects><--cap-drop><ALL><--security-opt><no-new-privileges:true>'
[ "$got" = "$want" ] && ok "required network/hardening argv is exact" || bad "network/hardening argv: $got"
bounded=(); security_append_docker_control_prefix bounded; bounded+=(image inspect pinned-image)
bounded_got="$(printf '<%s>' "${bounded[@]}")"
[ "$bounded_got" = '<timeout><-k><2s><30s><docker><image><inspect><pinned-image>' ] \
  && ok "Docker control-plane argv is bounded" || bad "Docker control-plane argv: $bounded_got"
security_is_timeout_status 124 && security_is_timeout_status 137 \
  && ! security_is_timeout_status 1 && ok "timeout status classifier covers TERM and KILL outcomes" \
  || bad "timeout status classifier does not implement 124/137 exactly"
grep -Eq '^for command_name in .*\btimeout\b.*; do$' "$HARNESS/runner-security-check.sh" \
  && ok "readiness preflight declares timeout dependency" \
  || bad "readiness preflight does not declare timeout dependency"
security_validate_bucket noaa-normals-pds && ok "registered-style bucket accepted" || bad "registered-style bucket rejected"
if security_validate_bucket dotted.bucket; then bad "dotted bucket accepted"; else ok "dotted bucket rejected"; fi
if security_validate_bucket Uppercase; then bad "uppercase bucket accepted"; else ok "uppercase bucket rejected"; fi

digest="$(printf '%064d' 0)"

# A fake Docker control plane. Everything the gate learns about the box comes
# through it, so the whole suite stays hermetic.
fake="$work/bin"; mkdir -p "$fake"
network_json='[{"Name":"s3-listing-study-subjects","Id":"network-test","Driver":"bridge","Internal":false,"EnableIPv6":false,"IPAM":{"Config":[{"Subnet":"172.30.0.0/24","Gateway":"172.30.0.1"}]},"Options":{"com.docker.network.bridge.name":"s3study0","com.docker.network.bridge.enable_icc":"false","com.docker.network.driver.mtu":"1500"}}]'
foreign_network_json="${network_json%]}, {\"Name\":\"other\",\"Id\":\"other-test\",\"Driver\":\"bridge\",\"Internal\":false,\"EnableIPv6\":false,\"IPAM\":{\"Config\":[{\"Subnet\":\"203.0.113.0/24\"}]},\"Options\":{}}]"
cat >"$fake/docker" <<'SH'
#!/usr/bin/env bash
[ -z "${FAKE_DOCKER_LOG:-}" ] || printf '%s\n' "$*" >>"$FAKE_DOCKER_LOG"
case "$1 $2" in
  'info --format') printf '%s\n' "${FAKE_BACKEND:-iptables}" ;;
  'network ls') printf 'network-test\n'; [ "${FAKE_FOREIGN_NETWORK:-no}" = yes ] && printf 'other-test\n'; true ;;
  'network inspect')
    if [[ " $* " == *" s3-listing-study-subjects "* ]]; then
      printf '%s\n' "${FAKE_NETWORK_JSON_OVERRIDE:-$FAKE_NETWORK_JSON}"
    else
      printf '%s\n' "${FAKE_ALL_NETWORKS_JSON:-$FAKE_NETWORK_JSON}"
    fi ;;
  'image inspect') exit 0 ;;
  'run --rm') exit "${FAKE_RUN_RC:-0}" ;;
  'rm -f') exit "${FAKE_CLEANUP_RC:-0}" ;;
  'container ls') [ "${FAKE_ABSENCE_RC:-0}" -eq 0 ] || exit "$FAKE_ABSENCE_RC" ;;
  *) exit 90 ;;
esac
SH
cat >"$fake/curl" <<'SH'
#!/usr/bin/env bash
[ "${FAKE_METADATA:-no}" = yes ] && exit 0
exit 1
SH
chmod +x "$fake/docker" "$fake/curl"
render_policy() { # <v4-out> <v6-out> [env...]
  env PATH="$fake:$PATH" FAKE_NETWORK_JSON="$network_json" "${@:3}" \
    "$HARNESS/security/render-policy.sh" "$1" "$2"
}

# The expected chain bodies are rendered from the versioned policy, not typed
# out here: this is the same code path the gate runs on every invocation.
rules4="$work/rules4"; rules6="$work/rules6"; live_valid="$work/live.valid"
render_policy "$rules4" "$rules6"
missing_deny=""
while IFS= read -r cidr; do
  grep -qFx -- "-A S3STUDY_FWD -d $cidr -j REJECT --reject-with icmp-port-unreachable" "$rules4" \
    || missing_deny="$missing_deny $cidr"
done < <(sed -n 's/^ipv4_deny=//p' "$HARNESS/security/policy.v1.env" | tr ',' '\n')
if [ -z "$missing_deny" ] && grep -qFx -- '-A S3STUDY_FWD -d 192.168.0.0/16 -j REJECT --reject-with icmp-port-unreachable' "$rules4" \
   && grep -qFx -- '-A S3STUDY_FWD -d fc00::/7 -j REJECT --reject-with icmp6-adm-prohibited' "$rules6"; then
  ok "rendered policy rejects every private range the policy file names"
else
  bad "rendered policy is missing a private-network deny:$missing_deny"
fi
render_policy "$work/rules4.foreign" "$work/rules6.foreign" \
  FAKE_FOREIGN_NETWORK=yes FAKE_ALL_NETWORKS_JSON="$foreign_network_json"
grep -qFx -- '-A S3STUDY_FWD -d 203.0.113.0/24 -j REJECT --reject-with icmp-port-unreachable' "$work/rules4.foreign" \
  && ok "rendered policy denies a Docker network outside the private ranges" \
  || bad "rendered policy ignored a foreign Docker network subnet"

{
  printf '[ipv4]\n*filter\n:INPUT ACCEPT [0:0]\n:FORWARD ACCEPT [0:0]\n:S3STUDY_FWD - [0:0]\n:S3STUDY_IN - [0:0]\n'
  printf '%s\n' '-A INPUT -i s3study0 -j S3STUDY_IN' '-A FORWARD -i s3study0 -j S3STUDY_FWD'
  grep '^-A S3STUDY_' "$rules4"; printf 'COMMIT\n[ipv6]\n*filter\n:INPUT ACCEPT [0:0]\n:FORWARD ACCEPT [0:0]\n:S3STUDY_FWD - [0:0]\n:S3STUDY_IN - [0:0]\n'
  printf '%s\n' '-A INPUT -i s3study0 -j S3STUDY_IN' '-A FORWARD -i s3study0 -j S3STUDY_FWD'
  grep '^-A S3STUDY_' "$rules6"; printf 'COMMIT\n'
} >"$live_valid"

"$HARNESS/security/validate-firewall-state.sh" "$live_valid" "$rules4" "$rules6" && ok "canonical firewall accepted" || bad "canonical firewall rejected"

# Real iptables-save output changes its generated/completed timestamps and may
# report non-zero chain counters even when policy and rule order are unchanged.
# Exercise the installed helper's actual canonicalization through two fake save
# binaries whose decorations change on every call.
save_fake="$work/save-bin"; mkdir -p "$save_fake"
cat >"$save_fake/save" <<'SH'
#!/bin/bash
set -euo pipefail
state="${FAKE_SAVE_STATE:?}.${0##*/}"
n=0; [ ! -f "$state" ] || n="$(cat "$state")"
n=$((n + 1)); printf '%s\n' "$n" >"$state"
printf '# Generated by %s on Thu Jul 19 00:00:%02d 2026\n' "${0##*/}" "$n"
printf '*filter\n:INPUT ACCEPT [%d:%d]\n:FORWARD ACCEPT [%d:%d]\nCOMMIT\n' "$n" "$((n * 11))" "$((n + 1))" "$((n * 17))"
printf '# Completed on Thu Jul 19 00:00:%02d 2026\n' "$n"
SH
chmod +x "$save_fake/save"
cp "$save_fake/save" "$save_fake/iptables-save"
cp "$save_fake/save" "$save_fake/ip6tables-save"
helper_under_test="$work/firewall-state"
sed -e "s|/usr/sbin/iptables-save|$save_fake/iptables-save|" \
  -e "s|/usr/sbin/ip6tables-save|$save_fake/ip6tables-save|" \
  "$HARNESS/security/firewall-state.sh" >"$helper_under_test"
chmod +x "$helper_under_test"
first_capture="$(FAKE_SAVE_STATE="$work/save-state" "$helper_under_test")"
second_capture="$(FAKE_SAVE_STATE="$work/save-state" "$helper_under_test")"
if [ "$first_capture" = "$second_capture" ] \
   && ! grep -q '^#' <<<"$first_capture" \
   && ! grep -Eq '^:[^ ]+ [^ ]+ \[[1-9][0-9]*:' <<<"$first_capture"; then
  ok "firewall helper canonicalizes timestamps and counters"
else
  bad "firewall helper digest input changes with timestamps or counters"
fi

live_decorated="$work/live.decorated"
awk '
  /^\[ipv4\]$/ { print; print "# Generated by iptables-save on first timestamp"; next }
  /^\[ipv6\]$/ { print "# Completed on first timestamp"; print; print "# Generated by ip6tables-save on second timestamp"; next }
  { gsub(/\[0:0\]/, "[23:4096]"); print }
  END { print "# Completed on second timestamp" }
' "$live_valid" >"$live_decorated"
"$HARNESS/security/validate-firewall-state.sh" "$live_decorated" "$rules4" "$rules6" \
  && ok "structural validator tolerates save decorations" \
  || bad "structural validator rejected realistic save decorations"

[ "$(head -1 "$HARNESS/security/firewall-state.sh")" = '#!/bin/bash' ] \
  && grep -qFx 'export PATH=/usr/sbin:/usr/bin:/sbin:/bin' "$HARNESS/security/firewall-state.sh" \
  && grep -qF 'NOPASSWD: /usr/local/libexec/s3-study-firewall-state ""' "$HARNESS/security/firewall-state.sh" \
  && ok "privileged helper has an absolute shell, trusted PATH, and no-argument sudo grant" \
  || bad "privileged helper or documented sudo grant is not confined"

expect_bad_firewall() {
  local label="$1" fixture="$2"
  if "$HARNESS/security/validate-firewall-state.sh" "$fixture" "$rules4" "$rules6" >/dev/null 2>&1; then bad "$label accepted"; else ok "$label rejected"; fi
}
for kind in earlier_return targeted_accept broad_accept duplicate_hook late_hook altered_body dropped_private_deny; do
  fixture="$work/live.$kind"; cp "$live_valid" "$fixture"
  case "$kind" in
    earlier_return) sed -i '0,/-A INPUT /s//-A INPUT -j RETURN\n-A INPUT /' "$fixture" ;;
    targeted_accept) sed -i '0,/-A FORWARD /s//-A FORWARD -i s3study0 -j ACCEPT\n-A FORWARD /' "$fixture" ;;
    broad_accept) sed -i '0,/-A FORWARD /s//-A FORWARD -j ACCEPT\n-A FORWARD /' "$fixture" ;;
    duplicate_hook) sed -i '0,/-A INPUT -i s3study0 -j S3STUDY_IN/a -A INPUT -i s3study0 -j S3STUDY_IN' "$fixture" ;;
    late_hook) sed -i '0,/-A INPUT /s//-A INPUT -p tcp -j ACCEPT\n-A INPUT /' "$fixture" ;;
    altered_body) sed -i '0,/-A S3STUDY_FWD -j RETURN/s//-A S3STUDY_FWD -j ACCEPT/' "$fixture" ;;
    dropped_private_deny) sed -i '/-A S3STUDY_FWD -d 192.168.0.0\/16 /d' "$fixture" ;;
  esac
  expect_bad_firewall "$kind" "$fixture"
done

helper="$work/helper"
cat >"$helper" <<'SH'
#!/usr/bin/env bash
cat "$FAKE_LIVE_FILE"
SH
chmod +x "$helper"
config="$work/runner.env"
write_config() { printf 'probe_image=probe@sha256:%s\n' "$digest" >"$config"; }
run_check() {
  set +e
  env -u AWS_PROFILE -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
    PATH="$fake:$PATH" FAKE_NETWORK_JSON="$network_json" FAKE_LIVE_FILE="${CHECK_LIVE_FILE:-$live_valid}" \
    FAKE_DOCKER_LOG="${CHECK_DOCKER_LOG:-}" \
    S3_STUDY_SECURITY_STATE_FILE="$config" S3_STUDY_SECURITY_INSTALLED_HELPER="$helper" \
    S3_STUDY_SECURITY_ALLOW_UNPRIVILEGED_STATE=yes "$@" \
    "$HARNESS/runner-security-check.sh" --quiet --bucket bucket --region us-east-1 >"$work/check.out" 2>&1
  check_rc=$?; set -e
}
reject_check() { if [ "$check_rc" -eq 2 ]; then ok "$1"; else bad "$1 rc=$check_rc"; fi; }

write_config; run_check
[ "$check_rc" -eq 0 ] && ok "fully faked readiness validates" || bad "valid readiness: $(head -1 "$work/check.out")"
check_probe_log="$work/check-probe.log"; : >"$check_probe_log"
CHECK_DOCKER_LOG="$check_probe_log" run_check
if grep -q '^run --rm .*--pull=never' "$check_probe_log"; then ok "readiness probe is no-pull"; else bad "readiness probe omitted --pull=never"; fi
: >"$check_probe_log"
CHECK_DOCKER_LOG="$check_probe_log" run_check FAKE_RUN_RC=125
if [ "$check_rc" -eq 2 ] && grep -q '^rm -f s3study-preflight-' "$check_probe_log"; then ok "readiness probe rc=125 triggers stable-name reconciliation"; else bad "readiness probe rc=125 reconciliation missing"; fi
: >"$check_probe_log"
CHECK_DOCKER_LOG="$check_probe_log" run_check FAKE_RUN_RC=137 FAKE_CLEANUP_RC=137 FAKE_ABSENCE_RC=125
if [ "$check_rc" -eq 2 ] && grep -q 'discard this runner' "$work/check.out"; then ok "probe cleanup rc=137 fails closed with discard-runner warning"; else bad "probe cleanup rc=137 did not fail closed"; fi
unset CHECK_DOCKER_LOG

S3_STUDY_SECURITY_STATE_FILE="$work/missing" "$HARNESS/runner-security-check.sh" \
  --bucket bucket --region us-east-1 >"$work/missing.out" 2>&1 && missing_rc=0 || missing_rc=$?
[ "$missing_rc" -eq 2 ] && ok "missing runner config fails closed" || bad "missing runner config rc=$missing_rc"
printf 'probe_image=probe:latest\n' >"$config"; run_check; reject_check "mutable-tag probe image rejected"
write_config; printf 'unknown=x\n' >>"$config"; run_check; reject_check "unknown runner-config field rejected"
write_config; printf 'probe_image=probe@sha256:%s\n' "$digest" >>"$config"; run_check; reject_check "duplicate probe_image rejected"

write_config
run_check AWS_SECURITY_TOKEN=x; reject_check "ambient credential rejected"
run_check FAKE_METADATA=yes; reject_check "metadata-positive local runner rejected"
run_check FAKE_RUN_RC=1; reject_check "subject-boundary probe failure rejected"
run_check FAKE_BACKEND=nftables; reject_check "non-iptables Docker firewall backend rejected"
bad_bridge="${network_json/s3study0/wrong0}"
run_check FAKE_NETWORK_JSON_OVERRIDE="$bad_bridge"; reject_check "bridge drift rejected"
icc_on="${network_json/\"com.docker.network.bridge.enable_icc\":\"false\"/\"com.docker.network.bridge.enable_icc\":\"true\"}"
run_check FAKE_NETWORK_JSON_OVERRIDE="$icc_on"; reject_check "inter-container communication drift rejected"

# The private-network deny is asserted against the live firewall on every run:
# a rule removed from the running kernel fails the gate even though nothing on
# disk changed.
CHECK_LIVE_FILE="$work/live.dropped_private_deny" run_check
reject_check "live firewall missing an RFC1918 deny rejected"
CHECK_LIVE_FILE="$work/live.earlier_return" run_check
reject_check "live firewall with an earlier RETURN rejected"
unset CHECK_LIVE_FILE

# A Docker network created after the rules were installed is not covered by
# them, and the run stops until they are reinstalled.
run_check FAKE_FOREIGN_NETWORK=yes FAKE_ALL_NETWORKS_JSON="$foreign_network_json"
reject_check "Docker network added after installation rejected"

if grep -Fn -- '--network host' "$HARNESS/smoke-run.sh" >/dev/null; then bad "normal scripts still use host networking"; else ok "normal scripts contain no host networking"; fi

# Rename-only staging is exercised in an isolated committed fixture repository;
# the suite's top-level trap reclaims this private mktemp tree.
stage_repo="$work/stage-repo"; mkdir -p "$stage_repo/harness" "$stage_repo/docs"
cp "$HARNESS/stage-workspace.sh" "$stage_repo/harness/stage-workspace.sh"
brief_template="$work/brief.valid"
cat >"$brief_template" <<'EOF'
# Brief
## Part 2 — the agent prompt
Research this subject without comparisons.
EOF
mkdir -p "$stage_repo/docs/operating"
cp "$brief_template" "$stage_repo/docs/operating/tool-research-brief.md"
printf 'synthetic bucket registry\n' >"$stage_repo/docs/smoke-bucket.md"
git -C "$stage_repo" init -q
git -C "$stage_repo" config user.email test@example.invalid
git -C "$stage_repo" config user.name test
git -C "$stage_repo" add harness docs
git -C "$stage_repo" commit -qm fixture

run_stage() { # <root> [PATH-prefix]
  local root="$1" path_prefix="${2:-}"; stage_rc=0
  S3_STUDY_SOURCES="$root" PATH="${path_prefix:+$path_prefix:}$PATH" \
    "$stage_repo/harness/stage-workspace.sh" minio-mc >"$work/stage.out" 2>"$work/stage.err" || stage_rc=$?
}

set +e
S3_STUDY_SOURCES=/ "$stage_repo/harness/stage-workspace.sh" minio-mc >"$work/stage-root.out" 2>&1
stage_root_rc=$?
S3_STUDY_SOURCES="$work/rejected-root" "$stage_repo/harness/stage-workspace.sh" ../escape >"$work/stage-tool.out" 2>&1
stage_tool_rc=$?
S3_STUDY_SOURCES="$stage_repo" "$stage_repo/harness/stage-workspace.sh" minio-mc >"$work/stage-repo-root.out" 2>&1
stage_repo_root_rc=$?
set -e
if [ "$stage_root_rc" -ne 0 ] && [ "$stage_tool_rc" -ne 0 ] && [ "$stage_repo_root_rc" -ne 0 ]; then
  ok "staging rejects broad/repository roots and unknown tool paths"
else
  bad "staging accepted an unsafe root or unknown tool"
fi

printf 'dirty\n' >"$stage_repo/harness/dirty.tmp"
run_stage "$work/dirty-root"
/bin/rm -f -- "$stage_repo/harness/dirty.tmp"
if [ "$stage_rc" -ne 0 ] && [ ! -e "$work/dirty-root/.stage-workspace.lock" ]; then
  ok "dirty-tree gate refuses staging before root mutation"
else
  bad "dirty-tree gate staged or mutated the requested root"
fi

stage_root="$work/stage"; mkdir -p "$stage_root"
run_stage "$stage_root"
[ "$stage_rc" -eq 0 ] && [ -d "$stage_root/minio-mc-work" ] \
  && ok "staging publishes first validated generation" || bad "first staging publish failed rc=$stage_rc"
printf 'prior\n' >"$stage_root/minio-mc-work/prior.txt"
run_stage "$stage_root"
shopt -s nullglob
retired_workspaces=("$stage_root"/.minio-mc-work.retired.*/workspace)
shopt -u nullglob
if [ "$stage_rc" -eq 0 ] && [ "${#retired_workspaces[@]}" -ge 1 ] \
   && [ -f "${retired_workspaces[0]}/prior.txt" ]; then
  ok "second publish retains prior generation"
else
  bad "second publish did not retain prior content"
fi

stage_outside="$work/stage-outside"; mkdir -p "$stage_outside"; printf 'keep\n' >"$stage_outside/sentinel"
/usr/bin/mv -- "$stage_root/minio-mc-work" "$stage_root/saved-current"
ln -s "$stage_outside" "$stage_root/minio-mc-work"
run_stage "$stage_root"
symlink_retired=no
for candidate in "$stage_root"/.minio-mc-work.retired.*/workspace; do [ -L "$candidate" ] && symlink_retired=yes; done
if [ "$stage_rc" -eq 0 ] && [ "$symlink_retired" = yes ] && [ -f "$stage_outside/sentinel" ]; then
  ok "stable symlink is retired without following"
else
  bad "stable symlink retirement followed or damaged outside target"
fi

# A committed invalid brief passes the dirty-tree gate, creates an unpublished
# generation, then fails validation without moving the stable entry.
stable_before="$(sha256sum "$stage_root/minio-mc-work/PROVENANCE.txt" | cut -d' ' -f1)"
printf '# invalid brief\n' >"$stage_repo/docs/operating/tool-research-brief.md"
git -C "$stage_repo" add docs/operating/tool-research-brief.md
git -C "$stage_repo" commit -qm invalid-brief
run_stage "$stage_root"
shopt -s nullglob; abandoned=("$stage_root"/.minio-mc-work.new.*); shopt -u nullglob
stable_after="$(sha256sum "$stage_root/minio-mc-work/PROVENANCE.txt" | cut -d' ' -f1)"
if [ "$stage_rc" -ne 0 ] && [ "$stable_before" = "$stable_after" ] && [ "${#abandoned[@]}" -ge 1 ]; then
  ok "validation failure preserves stable and unpublished generation"
else
  bad "validation failure changed stable or removed unpublished generation"
fi
cp "$brief_template" "$stage_repo/docs/operating/tool-research-brief.md"
git -C "$stage_repo" add docs/operating/tool-research-brief.md
git -C "$stage_repo" commit -qm restore-brief

fake_mv_bin="$work/fake-mv-bin"; mkdir -p "$fake_mv_bin"
cat >"$fake_mv_bin/mv" <<'SH'
#!/usr/bin/env bash
if [ "$*" = "--no-copy --no-target-directory --version" ]; then
  exec /usr/bin/mv "$@"
fi
printf '%s\n' "$*" >>"${FAKE_MV_LOG:?}"
n=0; [ ! -f "${FAKE_MV_STATE:?}" ] || n="$(cat "$FAKE_MV_STATE")"
n=$((n + 1)); printf '%s\n' "$n" >"$FAKE_MV_STATE"
if [ "$n" -eq "${FAKE_MV_FAIL_CALL:-999}" ]; then
  printf 'mv: simulated %s\n' "${FAKE_MV_ERROR:-EBUSY}" >&2
  exit 1
fi
if [ "$n" -eq "${FAKE_MV_BLOCK_CALL:-999}" ]; then
  : >"${FAKE_MV_MARKER:?}"
  while [ ! -e "${FAKE_MV_RELEASE:?}" ]; do sleep 0.02; done
fi
exec /usr/bin/mv "$@"
SH
chmod +x "$fake_mv_bin/mv"

# Retirement rename failure: current and new generations both survive.
: >"$work/mv.log"; rm -f -- "$work/mv.state"
FAKE_MV_LOG="$work/mv.log" FAKE_MV_STATE="$work/mv.state" FAKE_MV_FAIL_CALL=1 FAKE_MV_ERROR=EBUSY \
  run_stage "$stage_root" "$fake_mv_bin"
shopt -s nullglob; ebusy_new=("$stage_root"/.minio-mc-work.new.*); shopt -u nullglob
if [ "$stage_rc" -ne 0 ] && [ -d "$stage_root/minio-mc-work" ] && [ "${#ebusy_new[@]}" -ge 1 ]; then
  ok "simulated EBUSY preserves stable and unpublished generation"
else
  bad "simulated EBUSY lost a generation"
fi

# Publish rename failure: prior stable is retired and unpublished new survives.
: >"$work/mv.log"; rm -f -- "$work/mv.state"
FAKE_MV_LOG="$work/mv.log" FAKE_MV_STATE="$work/mv.state" FAKE_MV_FAIL_CALL=2 FAKE_MV_ERROR=EXDEV \
  run_stage "$stage_root" "$fake_mv_bin"
shopt -s nullglob
exdev_new=("$stage_root"/.minio-mc-work.new.*)
exdev_retired=("$stage_root"/.minio-mc-work.retired.*/workspace)
shopt -u nullglob
if [ "$stage_rc" -ne 0 ] && [ ! -e "$stage_root/minio-mc-work" ] \
   && [ "${#exdev_new[@]}" -ge 1 ] && [ "${#exdev_retired[@]}" -ge 1 ]; then
  ok "simulated EXDEV preserves unpublished and retired generations"
else
  bad "simulated EXDEV lost a generation or published by copy"
fi
if [ -s "$work/mv.log" ] && ! grep -v -- '--no-copy --no-target-directory --' "$work/mv.log" >/dev/null; then
  ok "all production staging mv calls require no-copy and no-target-directory"
else
  bad "staging mv omitted exact rename-only flags"
fi
# Restore a stable generation for the remaining race tests.
restore_new="$(sed -n 's/^stage-workspace: unpublished generation retained at //p' "$work/stage.err" | tail -1)"
if [ -d "$restore_new" ]; then
  /usr/bin/mv -- "$restore_new" "$stage_root/minio-mc-work"
else
  bad "publish failure did not report its retained generation"
fi

# Single-writer lock: a blocked first population owns the only unpublished
# generation; the second invocation cannot create another until release.
fake_cp_bin="$work/fake-cp-bin"; mkdir -p "$fake_cp_bin"
cat >"$fake_cp_bin/cp" <<'SH'
#!/usr/bin/env bash
if [ ! -e "${FAKE_CP_MARKER:?}" ]; then
  : >"$FAKE_CP_MARKER"
  while [ ! -e "${FAKE_CP_RELEASE:?}" ]; do sleep 0.02; done
fi
exec /bin/cp "$@"
SH
chmod +x "$fake_cp_bin/cp"
lock_root="$work/lock-root"; mkdir -p "$lock_root"
FAKE_CP_MARKER="$work/cp.marker" FAKE_CP_RELEASE="$work/cp.release" S3_STUDY_SOURCES="$lock_root" PATH="$fake_cp_bin:$PATH" \
  "$stage_repo/harness/stage-workspace.sh" minio-mc >"$work/lock1.out" 2>&1 & lock1=$!
for _ in $(seq 1 100); do [ -e "$work/cp.marker" ] && break; sleep 0.02; done
FAKE_CP_MARKER="$work/cp.marker" FAKE_CP_RELEASE="$work/cp.release" S3_STUDY_SOURCES="$lock_root" PATH="$fake_cp_bin:$PATH" \
  "$stage_repo/harness/stage-workspace.sh" minio-mc >"$work/lock2.out" 2>&1 & lock2=$!
sleep 0.1
shopt -s nullglob; locked_new=("$lock_root"/.minio-mc-work.new.*); shopt -u nullglob
lock_ok=no; [ "${#locked_new[@]}" -eq 1 ] && lock_ok=yes
: >"$work/cp.release"
set +e
wait "$lock1"; lock1_rc=$?
wait "$lock2"; lock2_rc=$?
set -e
[ "$lock_ok" = yes ] && [ "$lock1_rc" -eq 0 ] && [ "$lock2_rc" -eq 0 ] \
  && ok "flock serializes generation creation and publication" || bad "single-writer flock did not serialize publishers"

# Root retarget race: after cd -P and lock, renaming the external root path and
# replacing it with an outside symlink cannot redirect relative generation work.
race_root="$work/race-root"; race_moved="$work/race-moved"; race_outside="$work/race-outside"
mkdir -p "$race_root" "$race_outside"; printf 'keep\n' >"$race_outside/sentinel"
rm -f -- "$work/race.marker" "$work/race.release"
FAKE_CP_MARKER="$work/race.marker" FAKE_CP_RELEASE="$work/race.release" S3_STUDY_SOURCES="$race_root" PATH="$fake_cp_bin:$PATH" \
  "$stage_repo/harness/stage-workspace.sh" minio-mc >"$work/race.out" 2>&1 & race_pid=$!
for _ in $(seq 1 100); do [ -e "$work/race.marker" ] && break; sleep 0.02; done
/usr/bin/mv -- "$race_root" "$race_moved"
ln -s "$race_outside" "$race_root"
: >"$work/race.release"
set +e
wait "$race_pid"; race_rc=$?
set -e
if [ "$race_rc" -eq 0 ] && [ -d "$race_moved/minio-mc-work" ] && [ -f "$race_outside/sentinel" ]; then
  ok "root retarget race remains on locked directory inode"
else
  bad "root retarget race redirected or lost generation"
fi

# Leaf retarget race: publication treats the stable name as an entry, never as
# a directory to follow.  A symlink introduced during the stable-name gap is
# either atomically replaced or left intact beside the retained new generation.
leaf_root="$work/leaf-root"; leaf_outside="$work/leaf-outside"
mkdir -p "$leaf_root" "$leaf_outside"; printf 'keep\n' >"$leaf_outside/sentinel"
run_stage "$leaf_root"
: >"$work/leaf-mv.log"
/bin/rm -f -- "$work/leaf-mv.state" "$work/leaf.marker" "$work/leaf.release"
FAKE_MV_LOG="$work/leaf-mv.log" FAKE_MV_STATE="$work/leaf-mv.state" FAKE_MV_BLOCK_CALL=2 \
  FAKE_MV_MARKER="$work/leaf.marker" FAKE_MV_RELEASE="$work/leaf.release" \
  S3_STUDY_SOURCES="$leaf_root" PATH="$fake_mv_bin:$PATH" \
  "$stage_repo/harness/stage-workspace.sh" minio-mc >"$work/leaf.out" 2>&1 & leaf_pid=$!
for _ in $(seq 1 100); do [ -e "$work/leaf.marker" ] && break; sleep 0.02; done
if [ -e "$work/leaf.marker" ] && [ ! -e "$leaf_root/minio-mc-work" ]; then
  ln -s "$leaf_outside" "$leaf_root/minio-mc-work"
  : >"$work/leaf.release"
  set +e
  wait "$leaf_pid"; leaf_rc=$?
  set -e
  shopt -s nullglob; leaf_new=("$leaf_root"/.minio-mc-work.new.*); shopt -u nullglob
  leaf_result_safe=no
  if [ "$leaf_rc" -eq 0 ] && [ -d "$leaf_root/minio-mc-work" ] && [ ! -L "$leaf_root/minio-mc-work" ]; then
    leaf_result_safe=yes
  elif [ "$leaf_rc" -ne 0 ] && [ -L "$leaf_root/minio-mc-work" ] && [ "${#leaf_new[@]}" -ge 1 ]; then
    leaf_result_safe=yes
  fi
  if [ "$leaf_result_safe" = yes ] && [ -f "$leaf_outside/sentinel" ]; then
    ok "leaf retarget race never follows outside symlink"
  else
    bad "leaf retarget race followed a symlink or lost the new generation"
  fi
else
  : >"$work/leaf.release"
  set +e; wait "$leaf_pid"; set -e
  bad "leaf retarget fixture did not reach the stable-name gap"
fi

if grep -E '^[[:space:]]*(rm[[:space:]].*-[A-Za-z]*r|find[[:space:]].*-delete)' "$HARNESS/stage-workspace.sh" >/dev/null; then
  bad "production staging script contains recursive deletion"
else
  ok "production staging script contains no recursive deletion/find-delete"
fi

# Drive smoke-run through a fully fake Docker lifecycle.  This exercises the
# actual create/start/wait/logs/inspect/cleanup argv without Docker or a network.
smoke_repo="$work/smoke-repo"; mkdir -p "$smoke_repo/harness" "$smoke_repo/docs"
cp "$HARNESS/smoke-run.sh" "$HARNESS/runner-security-lib.sh" "$smoke_repo/harness/"
# The wrapper cites the versioned deny policy by digest, so the staged copy
# carries the policy file the preflight enforces.
mkdir -p "$smoke_repo/harness/security"
cp "$HARNESS/security/policy.v1.env" "$smoke_repo/harness/security/"
# The staged copy computes REPO_ROOT from its own location and imports the
# wrapper's offline half from $REPO_ROOT/src, so src/ is staged too: everything
# this fake-Docker harness needs lives inside $smoke_repo. Leaving it out would
# make the suite pass only where an editable install happens to be present and
# fail on a clean checkout — and it would resolve the redactor and the credential
# scanner through the ambient interpreter, which is the very shadowing seam the
# wrapper pins with `-P`.
cp -R "$REPO_ROOT/src" "$smoke_repo/src"
cat >"$smoke_repo/harness/runner-security-check.sh" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$smoke_repo/harness/runner-security-check.sh"
smoke_manifest="$work/smoke.manifest"; : >"$smoke_manifest"
smoke_manifest_sha="$(sha256sum "$smoke_manifest" | cut -d' ' -f1)"
# A synthetic registry, in the real format, read by the real resolver — the
# staged copy of smoke-run.sh is pointed at it through the same file-level seam
# used below for SECURITY_STATE and PROC_ROOT. No environment override and no
# argument reaches it; the two cases below pin that.
smoke_registry="$work/registry.toml"
{
  printf '[harness_client]\n'
  printf 'image = "example/harness@sha256:%064d"\n\n' 3
  printf '[buckets.testbucket]\n'
  printf 'region = "us-east-1"\n'
  printf 'manifest = "%s"\n' "$smoke_manifest"
  printf 'manifest_sha256 = "%s"\n' "$smoke_manifest_sha"
  printf 'snapshot_date = "2026-07-20"\n'
  printf 'keys = 0\n'
  printf 'shape = "synthetic"\n'
} >"$smoke_registry"
sed -i "s|^REGISTRY=.*|REGISTRY=\"$smoke_registry\"|" "$smoke_repo/harness/smoke-run.sh"
fake_proc="$work/fake-proc"; mkdir -p "$fake_proc/4242"
sed -i "s|PROC_ROOT=/proc|PROC_ROOT=$fake_proc|" "$smoke_repo/harness/smoke-run.sh"
smoke_run_script="$work/smoke-run-adapter.sh"
printf '%s\n' '#!/usr/bin/env bash' "printf 'list\\0'" >"$smoke_run_script"; chmod +x "$smoke_run_script"
smoke_bin="$work/smoke-bin"; mkdir -p "$smoke_bin"
cat >"$smoke_bin/docker" <<'SH'
#!/usr/bin/env bash
[ -z "${FAKE_DOCKER_LOG:-}" ] || printf '%s\n' "$*" >>"$FAKE_DOCKER_LOG"
case "$1 $2" in
  'image inspect') printf 'amd64\n' ;;
  'network inspect') printf '1500\n' ;;
  'create --name')
    [ "${FAKE_CREATE_RC:-0}" -eq 0 ] || exit "$FAKE_CREATE_RC"
    printf '%064d\n' 3 ;;
  'inspect -f')
    case "$3" in
      *LogConfig.Type*) [ "${FAKE_LOG_INSPECT_RC:-0}" -eq 0 ] || exit "$FAKE_LOG_INSPECT_RC"; printf '%s\n' "${FAKE_LOG_DRIVER:-json-file}" ;;
      *LogConfig.Config*)
        [ "${FAKE_LOG_INSPECT_RC:-0}" -eq 0 ] || exit "$FAKE_LOG_INSPECT_RC"
        if [ "${FAKE_LOG_CONFIG_CONTROL:-no}" = yes ]; then
          printf '{}\nforged=yes\n'
        elif [ "${FAKE_LOG_CONFIG_LIMITED:-no}" = yes ]; then
          printf '{"max-size":"10m"}\n'
        elif [ "${FAKE_LOG_CONFIG_MAX_FILE:-no}" = yes ]; then
          printf '{"max-file":"3","max-size":"-1"}\n'
        elif [ "${FAKE_LOG_CONFIG_SECRET:-no}" = yes ]; then
          printf '{"auth-token":"SUPERSECRET","max-size":"-1"}\n'
        else
          printf '{"max-size":"-1"}\n'
        fi ;;
      *State.Pid*) [ "${FAKE_PID_INSPECT_RC:-0}" -eq 0 ] || exit "$FAKE_PID_INSPECT_RC"; printf '%s\n' "${FAKE_PID:-0}" ;;
      *StartedAt*) [ "${FAKE_TIME_INSPECT_RC:-0}" -eq 0 ] || exit "$FAKE_TIME_INSPECT_RC"; printf '2026-07-20T00:00:00.000000000Z\n' ;;
      *FinishedAt*) [ "${FAKE_TIME_INSPECT_RC:-0}" -eq 0 ] || exit "$FAKE_TIME_INSPECT_RC"; printf '2026-07-20T00:00:01.000000000Z\n' ;;
    esac ;;
  'start s3study-smoke-'*) exit "${FAKE_START_RC:-0}" ;;
  'wait 000000000000'*) [ "${FAKE_WAIT_RC:-0}" -eq 0 ] || exit "$FAKE_WAIT_RC"; printf '0\n' ;;
  'logs 000000000000'*) exit "${FAKE_LOGS_RC:-0}" ;;
  'rm -f') exit "${FAKE_CLEANUP_RC:-0}" ;;
  'container ls') [ "${FAKE_ABSENCE_RC:-0}" -eq 0 ] || exit "$FAKE_ABSENCE_RC" ;;
  'kill 000000000000'*) exit 0 ;;
  'run --rm')
    [ "${FAKE_VERSION_RC:-0}" -eq 0 ] || exit "$FAKE_VERSION_RC"
    case "${FAKE_VERSION_CONTROL:-none}" in
      cr) printf 'fake\rversion\n' ;;
      unit) printf 'fake\001version\n' ;;
      *) printf 'fake-version\n' ;;
    esac ;;
  *) exit 90 ;;
esac
SH
cat >"$smoke_bin/curl" <<'SH'
#!/usr/bin/env bash
exit 1
SH
chmod +x "$smoke_bin/docker" "$smoke_bin/curl"
smoke_image="fake@sha256:$digest"
run_fake_smoke() {
  local out="$1"; shift
  local -a version_args=(--tool-version fake)
  [ "${FAKE_USE_AUTO_VERSION:-no}" = yes ] && version_args=()
  smoke_rc=0
  # The working directory is a parameter because it is part of the wrapper's
  # attack surface: `python3 -m` puts the invocation directory on sys.path ahead
  # of the wrapper's own src/, so one case below runs from a directory carrying a
  # planted `s3_listing_study/`. Every path handed to the wrapper is absolute.
  ( cd -- "${FAKE_SMOKE_CWD:-$PWD}" \
    && env -u AWS_PROFILE -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
      PATH="$smoke_bin:$PATH" S3_STUDY_DATA="$work/data" FAKE_DOCKER_LOG="$work/smoke-docker.log" \
      "$smoke_repo/harness/smoke-run.sh" --tool minio-mc --mode recursive --image "$smoke_image" \
        --run-script "$smoke_run_script" --bucket testbucket --auth anonymous --out "$out" \
        "${version_args[@]}" --env MC_HOST_s3=https://s3.amazonaws.com "$@" \
  ) >"$out.stdout" 2>"$out.stderr" || smoke_rc=$?
}
: >"$work/smoke-docker.log"; run_fake_smoke "$work/smoke-ok"
if [ "$smoke_rc" -eq 0 ] \
   && grep -q '^docker_log_driver=json-file$' "$work/smoke-ok/run.meta" \
   && grep -q '^docker_log_config_sha256=' "$work/smoke-ok/run.meta" \
   && grep -q '^docker_log_option_keys_base64=bWF4LXNpemU=$' "$work/smoke-ok/run.meta" \
   && grep -q '^functional_env=MC_HOST_s3=https://s3.amazonaws.com$' "$work/smoke-ok/run.meta" \
   && grep -Eq '^create --name s3study-smoke-[^ ]+ --log-driver=json-file --log-opt max-size=-1 --pull=never ' "$work/smoke-docker.log"; then
  ok "fake smoke enforces exact unlimited local-log create contract"
else
  bad "fake smoke unlimited local-log contract rc=$smoke_rc: $(tail -3 "$work/smoke-ok.stderr" | tr '\n' ' ')"
fi

# --- the import path is not redirectable -------------------------------------
# The wrapper's offline half owns the redactor and the credential scanner, so
# replacing the MODULE is strictly worse than redirecting the registry: it
# forges the whole receipt, scan included, and still exits 0. Two seams, both
# pinned here — the invocation directory, which `python3 -m` would place at
# sys.path[0], and an ambient PYTHONPATH, which precedes the standard library
# and can shadow `tomllib` itself.
plant="$work/plant"
mkdir -p "$plant/s3_listing_study/receipt" "$plant/stdlib-shadow"
: >"$plant/s3_listing_study/__init__.py"
: >"$plant/s3_listing_study/receipt/__init__.py"
cat >"$plant/s3_listing_study/receipt/__main__.py" <<'PY'
import os
open(os.environ["PLANT_MARKER"], "a").write("planted module ran\n")
raise SystemExit(0)
PY
printf 'raise SystemExit("planted tomllib won")\n' >"$plant/stdlib-shadow/tomllib.py"
/bin/rm -f -- "$work/plant.marker"
FAKE_SMOKE_CWD="$plant" PLANT_MARKER="$work/plant.marker" PYTHONPATH="$plant/stdlib-shadow" \
  run_fake_smoke "$work/smoke-plant"
if [ "$smoke_rc" -eq 0 ] && [ ! -e "$work/plant.marker" ] \
   && grep -qF "registry_path=$smoke_registry" "$work/smoke-plant/run.meta"; then
  ok "planted s3_listing_study/ in cwd and ambient PYTHONPATH cannot replace the offline half"
else
  bad "wrapper import path is shadowable rc=$smoke_rc: $(tail -3 "$work/smoke-plant.stderr" | tr '\n' ' ')"
fi

# --- the registry is not redirectable ----------------------------------------
# A valid decoy, not a broken file: if any of these seams worked the run would
# succeed and cite the decoy's snapshot date, which is the failure that looks
# like evidence.
decoy_registry="$work/decoy-registry.toml"
{
  printf '[harness_client]\n'
  printf 'image = "example/harness@sha256:%064d"\n\n' 3
  printf '[buckets.testbucket]\n'
  printf 'region = "us-east-1"\n'
  printf 'manifest = "%s"\n' "$smoke_manifest"
  printf 'manifest_sha256 = "%s"\n' "$smoke_manifest_sha"
  printf 'snapshot_date = "1999-12-31"\n'
  printf 'keys = 0\n'
  printf 'shape = "decoy"\n'
} >"$decoy_registry"
SMOKE_REGISTRY="$decoy_registry" S3_STUDY_REGISTRY="$decoy_registry" REGISTRY="$decoy_registry" \
  run_fake_smoke "$work/smoke-registry-env"
if [ "$smoke_rc" -eq 0 ] \
   && grep -qF "registry_path=$smoke_registry" "$work/smoke-registry-env/run.meta" \
   && grep -q '^snapshot_date=2026-07-20$' "$work/smoke-registry-env/run.meta" \
   && ! grep -q '1999-12-31' "$work/smoke-registry-env/run.meta"; then
  ok "registry constant is not redirectable through the environment"
else
  bad "environment redirected the registry rc=$smoke_rc: $(tail -3 "$work/smoke-registry-env.stderr" | tr '\n' ' ')"
fi

run_fake_smoke "$work/smoke-registry-arg" --registry "$decoy_registry"
if [ "$smoke_rc" -eq 2 ] && grep -q 'unknown argument: --registry' "$work/smoke-registry-arg.stderr"; then
  ok "registry constant is not redirectable through argv"
else
  bad "argv redirected the registry rc=$smoke_rc: $(tail -3 "$work/smoke-registry-arg.stderr" | tr '\n' ' ')"
fi

run_fake_smoke "$work/smoke-caller-control" --mode "$(printf 'recursive\nforged=yes')"
[ "$smoke_rc" -eq 2 ] && grep -q 'run.meta field' "$work/smoke-caller-control.stderr" \
  && ok "caller metadata control character rejected at emission" \
  || bad "caller metadata control character was not rejected rc=$smoke_rc"
run_fake_smoke "$work/smoke-caller-cr" --mode "$(printf 'recursive\rforged=yes')"
[ "$smoke_rc" -eq 2 ] && grep -q 'run.meta field' "$work/smoke-caller-cr.stderr" \
  && ok "caller metadata CR rejected at emission" \
  || bad "caller metadata CR was not rejected rc=$smoke_rc"
run_fake_smoke "$work/smoke-caller-tab" --mode "$(printf 'recursive\tforged=yes')"
[ "$smoke_rc" -eq 2 ] && grep -q 'run.meta field' "$work/smoke-caller-tab.stderr" \
  && ok "caller metadata tab rejected at emission" \
  || bad "caller metadata tab was not rejected rc=$smoke_rc"

FAKE_LOG_CONFIG_CONTROL=yes run_fake_smoke "$work/smoke-auto-control"
[ "$smoke_rc" -eq 2 ] && grep -q 'malformed effective log configuration' "$work/smoke-auto-control.stderr" \
  && ok "malformed auto-detected log configuration rejected without persistence" \
  || bad "malformed auto-detected log configuration was not rejected rc=$smoke_rc"

FAKE_LOG_CONFIG_LIMITED=yes run_fake_smoke "$work/smoke-log-limited"
[ "$smoke_rc" -eq 2 ] && grep -q 'not exactly json-file with max-size=-1' "$work/smoke-log-limited.stderr" \
  && ok "size-limited json-file logging is rejected" \
  || bad "size-limited json-file logging was accepted rc=$smoke_rc"

FAKE_LOG_CONFIG_MAX_FILE=yes run_fake_smoke "$work/smoke-log-max-file"
[ "$smoke_rc" -eq 2 ] && grep -q 'not exactly json-file with max-size=-1' "$work/smoke-log-max-file.stderr" \
  && ok "extra max-file rotation option is rejected" \
  || bad "max-file rotation option was accepted rc=$smoke_rc"

FAKE_LOG_DRIVER=fluentd run_fake_smoke "$work/smoke-log-remote"
[ "$smoke_rc" -eq 2 ] && grep -q 'not exactly json-file with max-size=-1' "$work/smoke-log-remote.stderr" \
  && ok "remote logging driver is rejected" \
  || bad "remote logging driver was accepted rc=$smoke_rc"

FAKE_LOG_CONFIG_SECRET=yes run_fake_smoke "$work/smoke-log-secret"
if [ "$smoke_rc" -eq 2 ] && ! grep -R -q 'SUPERSECRET' "$work/smoke-log-secret"; then
  ok "unrecognized raw log-option values are rejected without persistence"
else
  bad "unsafe Docker log option was accepted or persisted rc=$smoke_rc"
fi

: >"$work/smoke-docker.log"
FAKE_USE_AUTO_VERSION=yes FAKE_VERSION_CONTROL=cr run_fake_smoke "$work/smoke-version-control"
if [ "$smoke_rc" -eq 2 ] && grep -q "tool_version.*control character" "$work/smoke-version-control.stderr" \
   && grep -q '^run --rm --pull=never --network none --name s3study-version-' "$work/smoke-docker.log"; then
  ok "auto-detected tool-version CR rejected and offline probe is named/no-pull"
else
  bad "auto-detected tool-version control/no-pull contract failed rc=$smoke_rc"
fi
: >"$work/smoke-docker.log"
FAKE_USE_AUTO_VERSION=yes FAKE_VERSION_RC=125 run_fake_smoke "$work/smoke-version-timeout"
if [ "$smoke_rc" -eq 0 ] && grep -q '^rm -f s3study-version-' "$work/smoke-docker.log" \
   && grep -q 'tool_version_source=unavailable — --version failed (Docker rc=125)' "$work/smoke-version-timeout/run.meta"; then
  ok "offline version probe failure is labeled and reconciled"
else
  bad "offline version probe failure label/reconciliation missing rc=$smoke_rc"
fi

: >"$work/smoke-docker.log"
FAKE_CREATE_RC=124 run_fake_smoke "$work/smoke-create-timeout"
if [ "$smoke_rc" -eq 2 ] && grep -q '^rm -f s3study-smoke-' "$work/smoke-docker.log"; then
  ok "timed-out create cleans deterministic stable container name"
else
  bad "timed-out create did not clean stable target rc=$smoke_rc"
fi

smoke_timeout_case() { # <label> <env-name> <124|137>
  local label="$1" var="$2" value="$3" out="$work/smoke-${2,,}-$3"
  : >"$work/smoke-docker.log"
  printf -v "$var" '%s' "$value"; export "${var?}"
  run_fake_smoke "$out"
  unset "$var"
  if [ "$smoke_rc" -eq 2 ] && grep -q '^rm -f s3study-smoke-' "$work/smoke-docker.log"; then
    ok "$label rc=$value triggers stable-name cleanup"
  else
    bad "$label rc=$value cleanup missing (smoke rc=$smoke_rc)"
  fi
}
smoke_timeout_case "timed-out start" FAKE_START_RC 137
smoke_timeout_case "timed-out log-driver inspect" FAKE_LOG_INSPECT_RC 124
smoke_timeout_case "timed-out PID inspect" FAKE_PID_INSPECT_RC 137
smoke_timeout_case "timed-out wait" FAKE_WAIT_RC 124
smoke_timeout_case "timed-out logs" FAKE_LOGS_RC 137
smoke_timeout_case "timed-out timestamp inspect" FAKE_TIME_INSPECT_RC 124

: >"$work/smoke-docker.log"
FAKE_CLEANUP_RC=137 FAKE_ABSENCE_RC=125 run_fake_smoke "$work/smoke-cleanup-timeout"
if [ "$smoke_rc" -eq 2 ] && grep -q 'discard this runner' "$work/smoke-cleanup-timeout.stderr"; then
  ok "cleanup rc=137 fails closed with discard-runner warning"
else
  bad "cleanup rc=137 did not produce discard-runner warning"
fi

: >"$work/smoke-docker.log"
FAKE_START_RC=125 run_fake_smoke "$work/smoke-start-125"
if [ "$smoke_rc" -eq 2 ] && grep -q '^rm -f s3study-smoke-' "$work/smoke-docker.log"; then
  ok "general docker rc=125 reconciles stable subject name"
else
  bad "docker rc=125 did not reconcile subject name rc=$smoke_rc"
fi

printf 'evil|name`tick\n' >"$fake_proc/4242/comm"
FAKE_PID=4242 run_fake_smoke "$work/smoke-comm-safe"
if [ "$smoke_rc" -eq 0 ] && grep -q 'evil&#124;name&#96;tick' "$work/smoke-comm-safe/receipt.md" \
   && ! grep -q 'evil|name`tick' "$work/smoke-comm-safe/receipt.md"; then
  ok "subject process name is safely encoded in receipt"
else
  bad "subject process name was not safely encoded rc=$smoke_rc"
fi

printf 'evil\nforged|row\n' >"$fake_proc/4242/comm"
FAKE_PID=4242 run_fake_smoke "$work/smoke-comm-control"
[ "$smoke_rc" -eq 2 ] && grep -q "measured_process.*control character" "$work/smoke-comm-control.stderr" \
  && ok "subject process-name LF rejected before receipt" \
  || bad "subject process-name LF was not rejected rc=$smoke_rc"

run_fake_smoke "$work/smoke-entrypoint-safe" --entrypoint 'entry|point`tick'
if [ "$smoke_rc" -eq 0 ] && grep -q 'entry&#124;point&#96;tick' "$work/smoke-entrypoint-safe/receipt.md"; then
  ok "caller entrypoint is safely encoded in receipt"
else
  bad "caller entrypoint was not safely encoded rc=$smoke_rc"
fi

run_fake_smoke "$work/smoke-entrypoint-control" --entrypoint "$(printf 'entry\nforged')"
[ "$smoke_rc" -eq 2 ] && grep -q -- '--entrypoint contains a control character' "$work/smoke-entrypoint-control.stderr" \
  && ok "caller entrypoint LF rejected" \
  || bad "caller entrypoint LF was not rejected rc=$smoke_rc"

# The reference re-list argv is no longer asserted by grepping a shell file: the
# verifier is Python, and `tests/test_verify.py` pins the produced argv element by
# element, the sentinel `run_prefix`, and that the preflight seam cannot widen —
# over the code that runs, not over its text.

[ "$fail" -eq 0 ] || exit 1
