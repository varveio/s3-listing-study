#!/usr/bin/env bash
# harness/tests/run-regressions.sh — durable regression suite for the harness.
#
# Covers everything runnable WITHOUT the real bucket or docker, by driving the
# actual scripts (verify-listing.sh --scope union, smoke-run.sh --env guard) over
# synthetic registry/manifest/receipt fixtures built here at runtime:
#
#   * union scenarios (PASS, structural-ERROR, dup-FAIL, overlap-ERROR,
#     undesignated-empty-prefix-ERROR, mixed-mode-ERROR, redaction-refusal,
#     truncated-shard-refusal) against a synthetic registry+manifest;
#   * compare_fields mtime cases (equal Z vs +00:00; malformed -> ERROR; non-UTC
#     offset -> ERROR) via the union path (they die in compare_fields, no docker);
#   * current run-meta-directory payload paths and legacy absolute payload paths
#     through the union verifier;
#   * per-tool env allowlist accept/reject matrix — smoke-run.sh dies at the
#     guard before any Docker call; asserts exit code and message;
#   * the scan fixtures (delegates to scan-fixtures-run.sh).
#
# Every case prints PASS/FAIL; the script exits nonzero if any case fails. Cases
# that genuinely need the bucket or docker are listed at the end as not-covered.
set -euo pipefail
export LC_ALL=C

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
HARNESS="$(cd -- "$HERE/.." && pwd)"
VERIFY="$HARNESS/verify-listing.sh"
SMOKE="$HARNESS/smoke-run.sh"
NORMALIZE="$HERE/adapters/normalize.sh"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fail=0
ok()  { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fail=1; }

# assert_rc <label> <want-rc> <got-rc>
assert_rc() { if [ "$3" -eq "$2" ]; then ok "$1 (exit $3)"; else bad "$1 — exit $3, want $2"; fi; }

mkline() { printf '%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5"; }

# --- synthetic registry + manifest -------------------------------------------
# The union path only reads --digest (and --harness-image, in the re-list branch
# our cases never reach). So the registry file just needs a stable digest and a
# harness-image row for completeness.
REG="$WORK/registry.md"
{
  printf '## Harness client\n\n'
  printf '| | |\n| --- | --- |\n'
  printf '| Image | `busybox@sha256:%064d` |\n' 0
} >"$REG"
REG_SHA="$(sha256sum "$REG" | cut -d' ' -f1)"
export SMOKE_REGISTRY="$REG"

# Primary manifest: two prefixes (a/, b/) plus a root-level key (index.html) that
# only an explicit remainder shard can cover.
MAN_TSV="$WORK/manifest.tsv"
{
  mkline "a/1" 10 etaga1 2026-07-17T00:00:00Z STANDARD
  mkline "a/2" 20 etaga2 2026-07-17T00:00:00Z STANDARD
  mkline "b/1" 30 etagb1 2026-07-17T00:00:00Z STANDARD
  mkline "index.html" 5 etagidx 2026-07-17T00:00:00Z STANDARD
} >"$MAN_TSV"
MAN_GZ="$WORK/manifest.tsv.gz"
gzip -c "$MAN_TSV" >"$MAN_GZ"
MAN_SHA="$(sha256sum "$MAN_GZ" | cut -d' ' -f1)"

# mk_shard <dir> <prefix> <mode> <redaction> <truncated> <payload-content>
mk_shard() {
  local dir="$1" prefix="$2" mode="$3" red="$4" trunc="$5" payload="$6" man_gz="${7:-$MAN_GZ}" man_sha="${8:-$MAN_SHA}"
  mkdir -p "$dir"
  # Newline-terminate: $() strips the trailing newline, and an unterminated last
  # record would merge with the next shard on concatenation.
  printf '%s\n' "$payload" >"$dir/stdout.txt"
  local sha; sha="$(sha256sum "$dir/stdout.txt" | cut -d' ' -f1)"
  {
    printf 'tool=testtool\nmode=%s\nauth=anonymous\nbucket=synthetic\nregion=us-east-1\nprefix=%s\n' "$mode" "$prefix"
    printf 'manifest=%s\nmanifest_sha256=%s\nsnapshot_date=2026-07-17\n' "$man_gz" "$man_sha"
    printf 'registry_sha256=%s\n' "$REG_SHA"
    printf 'redaction_changed_bytes=%s\npayload_path_base=run-meta-directory\n' "$red"
    printf 'stdout_path=stdout.txt\nstdout_sha256=%s\nstdout_truncated=%s\n' "$sha" "$trunc"
    printf 'stderr_path=\nstderr_sha256=\nstderr_truncated=no\n'
  } >"$dir/run.meta"
}

run_union() {  # <out> <args...> -> sets RC
  local out="$1"; shift
  mkdir -p "$out"   # ensure the parent exists before the stderr redirect below
  RC=0
  "$VERIFY" --scope union --normalize "$NORMALIZE" --out "$out" "$@" >/dev/null 2>"$out.err" || RC=$?
}

# --- union: PASS -------------------------------------------------------------
# Prefix shards use mode `passthrough`; the designated remainder uses a DIFFERENT
# mode (`rootlisting`) — a delimiter/root listing needs a different request shape.
# This exercises the remainder's exemption from cross-shard mode binding.
A="$WORK/pass/a"; B="$WORK/pass/b"; R="$WORK/pass/r"; O="$WORK/pass/out"
mk_shard "$A" "a/" passthrough no no "$(mkline a/1 10 etaga1 2026-07-17T00:00:00Z STANDARD; mkline a/2 20 etaga2 2026-07-17T00:00:00Z STANDARD)"
mk_shard "$B" "b/" passthrough no no "$(mkline b/1 30 etagb1 2026-07-17T00:00:00Z STANDARD)"
mk_shard "$R" ""   rootlisting no no "$(mkline index.html 5 etagidx 2026-07-17T00:00:00Z STANDARD)"
run_union "$O" --receipt "$A" --receipt "$B" --receipt "$R" --remainder "$R"
assert_rc "union PASS (remainder mode-exempt: rootlisting vs passthrough)" 0 "$RC"
grep -q 'Verdict: PASS' "$O/union-verify.md" 2>/dev/null && ok "union PASS wrote union-verify.md verdict" || bad "union PASS missing union-verify.md verdict"

# --- union: a full-recursive run cannot serve as the remainder ---------------
# A "remainder" that lists every key (not just the unprefixed ones) fails its own
# scope (extras) and duplicates the prefix shards -> caught, never PASS. It routes
# through the drift-exclusion re-list gate; with no real bucket the re-list cannot
# run, so the synthetic verdict is ERROR (exit 3). With a real bucket it is FAIL.
Rbad="$WORK/badrem/r"; O="$WORK/badrem/out"
mk_shard "$Rbad" "" rootlisting no no "$(mkline a/1 10 etaga1 2026-07-17T00:00:00Z STANDARD; mkline a/2 20 etaga2 2026-07-17T00:00:00Z STANDARD; mkline b/1 30 etagb1 2026-07-17T00:00:00Z STANDARD; mkline index.html 5 etagidx 2026-07-17T00:00:00Z STANDARD)"
run_union "$O" --receipt "$A" --receipt "$B" --receipt "$Rbad" --remainder "$Rbad"
if [ "$RC" -ne 0 ] && { grep -qi 're-list' "$O/union-verify.md" 2>/dev/null \
     || grep -qi 'harness image is not present' "$O.err" 2>/dev/null; }; then
  ok "union wrong-remainder (full recursive) is caught before attribution (exit $RC)"
else
  bad "union wrong-remainder should be caught before attribution — exit $RC"
fi

# --- union: stream selection — listing on STDERR, empty stdout (heuristic) -----
# A shard whose listing is on stderr (empty stdout) is verified from the
# receipt-bound stderr stream by the default heuristic, not a hardcoded stdout.
# mk_streamed_remainder <dir> <stdout-content>: remainder shard whose LISTING is
# on stderr (index.html), with caller-chosen stdout content (empty, or a banner).
mk_streamed_remainder() {
  mkdir -p "$1"
  printf '%s' "$2" >"$1/stdout.txt"
  mkline index.html 5 etagidx 2026-07-17T00:00:00Z STANDARD >"$1/stderr.txt"
  local os es
  os="$(sha256sum "$1/stdout.txt" | cut -d' ' -f1)"
  es="$(sha256sum "$1/stderr.txt" | cut -d' ' -f1)"
  {
    printf 'tool=testtool\nmode=rootlisting\nauth=anonymous\nbucket=synthetic\nregion=us-east-1\nprefix=\n'
    printf 'manifest=%s\nmanifest_sha256=%s\nsnapshot_date=2026-07-17\nregistry_sha256=%s\n' "$MAN_GZ" "$MAN_SHA" "$REG_SHA"
    printf 'redaction_changed_bytes=no\n'
    printf 'stdout_path=%s\nstdout_sha256=%s\nstdout_truncated=no\n' "$1/stdout.txt" "$os"
    printf 'stderr_path=%s\nstderr_sha256=%s\nstderr_truncated=no\n' "$1/stderr.txt" "$es"
  } >"$1/run.meta"
}
Rse="$WORK/stderr/r"; O="$WORK/stderr/out"
mk_streamed_remainder "$Rse" ""            # empty stdout
run_union "$O" --receipt "$A" --receipt "$B" --receipt "$Rse" --remainder "$Rse"
assert_rc "union stderr-stream selection PASS (heuristic, empty stdout)" 0 "$RC"

# --- union: --stream override — banner on stdout, listing on stderr (finding 2)-
# Every shard prints a banner on stdout and its listing on stderr (a fan-out set
# shares tool+mode, so they share the stream). Without --stream the heuristic
# picks the non-empty stdout banner, which is not 5-field -> informative failure.
# --stream stderr pins the listing stream for ALL shards and it PASSes.
mk_stderr_shard() {  # <dir> <prefix> <mode> <stdout-banner> <stderr-listing>
  mkdir -p "$1"; printf '%s' "$4" >"$1/stdout.txt"; printf '%s\n' "$5" >"$1/stderr.txt"
  local os es; os="$(sha256sum "$1/stdout.txt" | cut -d' ' -f1)"; es="$(sha256sum "$1/stderr.txt" | cut -d' ' -f1)"
  {
    printf 'tool=testtool\nmode=%s\nauth=anonymous\nbucket=synthetic\nregion=us-east-1\nprefix=%s\n' "$3" "$2"
    printf 'manifest=%s\nmanifest_sha256=%s\nsnapshot_date=2026-07-17\nregistry_sha256=%s\n' "$MAN_GZ" "$MAN_SHA" "$REG_SHA"
    printf 'redaction_changed_bytes=no\n'
    printf 'stdout_path=%s\nstdout_sha256=%s\nstdout_truncated=no\n' "$1/stdout.txt" "$os"
    printf 'stderr_path=%s\nstderr_sha256=%s\nstderr_truncated=no\n' "$1/stderr.txt" "$es"
  } >"$1/run.meta"
}
BAN='WARNING: using anonymous credentials'
As="$WORK/bn/a"; Bs="$WORK/bn/b"; Rs="$WORK/bn/r"; O="$WORK/bn/out"
mk_stderr_shard "$As" "a/" passthrough  "$BAN" "$(mkline a/1 10 etaga1 2026-07-17T00:00:00Z STANDARD; mkline a/2 20 etaga2 2026-07-17T00:00:00Z STANDARD)"
mk_stderr_shard "$Bs" "b/" passthrough  "$BAN" "$(mkline b/1 30 etagb1 2026-07-17T00:00:00Z STANDARD)"
mk_stderr_shard "$Rs" ""   rootlisting  "$BAN" "$(mkline index.html 5 etagidx 2026-07-17T00:00:00Z STANDARD)"
run_union "$O" --receipt "$As" --receipt "$Bs" --receipt "$Rs" --remainder "$Rs"
if [ "$RC" -ne 0 ]; then ok "union banner-on-stdout fails without --stream (exit $RC)"; else bad "union banner-on-stdout should fail without --stream — exit $RC"; fi
run_union "$O" --receipt "$As" --receipt "$Bs" --receipt "$Rs" --remainder "$Rs" --stream stderr
assert_rc "union banner-on-stdout PASS with --stream stderr" 0 "$RC"

# --- union: structural ERROR (orphan key, no remainder) ----------------------
O="$WORK/struct/out"
run_union "$O" --receipt "$A" --receipt "$B"
assert_rc "union structural-ERROR" 3 "$RC"
grep -q 'STRUCTURAL' "$O/union-verify.md" 2>/dev/null && ok "structural note recorded" || bad "structural note missing"

# --- union: dup-FAIL (cross-shard duplicate, everything else clean) ----------
Ad="$WORK/dup/a"; O="$WORK/dup/out"
mk_shard "$Ad" "a/" passthrough no no "$(mkline a/1 10 etaga1 2026-07-17T00:00:00Z STANDARD; mkline a/1 10 etaga1 2026-07-17T00:00:00Z STANDARD; mkline a/2 20 etaga2 2026-07-17T00:00:00Z STANDARD)"
run_union "$O" --receipt "$Ad" --receipt "$B" --receipt "$R" --remainder "$R"
assert_rc "union dup-FAIL" 1 "$RC"

# --- union: overlap ERROR (a/ is a string-prefix of a/1) ---------------------
Aov="$WORK/ov/a1"; O="$WORK/ov/out"
mk_shard "$Aov" "a/1" passthrough no no "$(mkline a/1 10 etaga1 2026-07-17T00:00:00Z STANDARD)"
run_union "$O" --receipt "$A" --receipt "$Aov"
assert_rc "union overlap-ERROR" 3 "$RC"
grep -q 'overlap' "$O.err" 2>/dev/null && ok "overlap message" || bad "overlap message missing"
# Durability (finding 5): a plan-defect death still writes union-verify.md=ERROR.
if [ -e "$O/union-verify.md" ] && grep -q 'Verdict: ERROR' "$O/union-verify.md"; then
  ok "overlap wrote durable union-verify.md verdict ERROR"
else
  bad "overlap did not write durable union-verify.md ERROR artifact"
fi

# --- union: undesignated empty-prefix ERROR ----------------------------------
O="$WORK/undes/out"
run_union "$O" --receipt "$A" --receipt "$B" --receipt "$R"
assert_rc "union undesignated-empty-prefix-ERROR" 3 "$RC"
grep -q 'designate the remainder' "$O.err" 2>/dev/null && ok "ambiguous-remainder message" || bad "ambiguous-remainder message missing"

# --- union: mixed-mode ERROR -------------------------------------------------
Bm="$WORK/mm/b"; O="$WORK/mm/out"
mk_shard "$Bm" "b/" keysonly no no "$(mkline b/1 30 etagb1 2026-07-17T00:00:00Z STANDARD)"
run_union "$O" --receipt "$A" --receipt "$Bm" --receipt "$R" --remainder "$R"
assert_rc "union mixed-mode-ERROR" 3 "$RC"
grep -q 'mixed-mode' "$O.err" 2>/dev/null && ok "mixed-mode message" || bad "mixed-mode message missing"

# --- union: redaction refusal ------------------------------------------------
Ared="$WORK/red/a"; O="$WORK/red/out"
mk_shard "$Ared" "a/" passthrough yes no "$(mkline a/1 10 etaga1 2026-07-17T00:00:00Z STANDARD; mkline a/2 20 etaga2 2026-07-17T00:00:00Z STANDARD)"
run_union "$O" --receipt "$Ared" --receipt "$B" --receipt "$R" --remainder "$R"
assert_rc "union redaction-refusal" 3 "$RC"
grep -q 'redaction_changed_bytes' "$O.err" 2>/dev/null && ok "redaction message" || bad "redaction message missing"

# --- union: truncated-shard refusal ------------------------------------------
Atr="$WORK/trunc/a"; O="$WORK/trunc/out"
mk_shard "$Atr" "a/" passthrough no yes "$(mkline a/1 10 etaga1 2026-07-17T00:00:00Z STANDARD; mkline a/2 20 etaga2 2026-07-17T00:00:00Z STANDARD)"
run_union "$O" --receipt "$Atr" --receipt "$B" --receipt "$R" --remainder "$R"
assert_rc "union truncated-shard-refusal" 3 "$RC"
grep -q 'TRUNCATED' "$O.err" 2>/dev/null && ok "truncation message" || bad "truncation message missing"

# --- compare_fields mtime cases (single-key manifest, remainder-only) --------
MT_TSV="$WORK/mt.tsv"; mkline k1 10 etag1 2026-07-17T00:00:00Z STANDARD >"$MT_TSV"
MT_GZ="$WORK/mt.tsv.gz"; gzip -c "$MT_TSV" >"$MT_GZ"; MT_SHA="$(sha256sum "$MT_GZ" | cut -d' ' -f1)"

mt_case() {  # <label> <mtime-value> <want-rc>
  local d="$WORK/mt-$3-$RANDOM" o
  mk_shard "$d" "" passthrough no no "$(mkline k1 10 etag1 "$2" STANDARD)" "$MT_GZ" "$MT_SHA"
  o="$d/out"
  run_union "$o" --receipt "$d" --remainder "$d"
  assert_rc "$1" "$3" "$RC"
}
mt_case "compare_fields mtime equal (+00:00 == Z)" "2026-07-17T00:00:00+00:00" 0
mt_case "compare_fields mtime malformed -> ERROR"   "2026-07-17_00:00:00Z"      3
mt_case "compare_fields mtime non-UTC offset -> ERROR" "2026-07-17T00:00:00+0100" 3

# Finding 4: a malformed adapter mtime on an EXTRA key (one the manifest lacks, so
# the inner join drops it) must still ERROR — the up-front full-file scan catches
# it rather than letting it slip toward FAIL. Remainder emits k1 (valid) + kX (not
# in manifest, malformed mtime).
d="$WORK/mt-extra"; mk_shard "$d" "" rootlisting no no "$(mkline k1 10 etag1 2026-07-17T00:00:00Z STANDARD; mkline kX 9 etagx 'garbage-mtime' STANDARD)" "$MT_GZ" "$MT_SHA"
run_union "$d/out" --receipt "$d" --remainder "$d"
assert_rc "compare_fields malformed mtime on EXTRA key -> ERROR" 3 "$RC"
grep -qi 'non-contract-v2 mtime' "$d/out.err" 2>/dev/null && ok "extra-key malformed mtime blamed on adapter" || bad "extra-key malformed mtime message missing"

# Finding 4: a malformed MANIFEST mtime (a later row, not just the first) is a
# corrupt snapshot -> ERROR naming the manifest, never a tool finding.
BADMAN_TSV="$WORK/badman.tsv"
{ mkline k1 10 etag1 2026-07-17T00:00:00Z STANDARD; mkline k2 20 etag2 '2026/07/17 00:00:00' STANDARD; } >"$BADMAN_TSV"
BADMAN_GZ="$WORK/badman.tsv.gz"; gzip -c "$BADMAN_TSV" >"$BADMAN_GZ"; BADMAN_SHA="$(sha256sum "$BADMAN_GZ" | cut -d' ' -f1)"
d="$WORK/mt-badman"; mk_shard "$d" "" rootlisting no no "$(mkline k1 10 etag1 2026-07-17T00:00:00Z STANDARD; mkline k2 20 etag2 2026-07-17T00:00:00Z STANDARD)" "$BADMAN_GZ" "$BADMAN_SHA"
run_union "$d/out" --receipt "$d" --remainder "$d"
assert_rc "compare_fields malformed MANIFEST mtime -> ERROR" 3 "$RC"
grep -qi 'manifest mtime is not contract-v2' "$d/out.err" 2>/dev/null && ok "malformed manifest mtime blamed on manifest" || bad "malformed manifest mtime message missing"

# --- per-tool env matrix (smoke-run.sh dies before any docker) ----------------
RS="$WORK/run.sh"
{ printf '#!/usr/bin/env bash\nprintf '"'"'x\\0'"'"'\n'; } >"$RS"
chmod +x "$RS"
FAKE_IMG="busybox@sha256:$(printf '%064d' 0)"

# reject: expect exit 2 and the guard's "refused" wording, no docker reached.
env_reject() {  # <label> <tool> <ENV=VALUE>
  local rc=0 out="$WORK/envr.$RANDOM.err"
  "$SMOKE" --tool "$2" --mode m --image "$FAKE_IMG" --run-script "$RS" \
    --bucket bogusbucket --auth anonymous --out "$WORK/envout.$RANDOM" \
    --env "$3" >/dev/null 2>"$out" || rc=$?
  if [ "$rc" -eq 2 ] && grep -qiE 'refused|control character|not allowlisted|must be exactly' "$out"; then
    ok "env-guard rejects $1 (exit 2)"
  else
    bad "env-guard should reject $1 — exit $rc; $(head -1 "$out")"
  fi
}
# accept: guard passes; smoke-run then fails later (bogus bucket) WITHOUT any
# "refused" wording — proving the value cleared the guard, still no docker.
env_accept() {  # <label> <tool> <ENV=VALUE>
  local rc=0 out="$WORK/enva.$RANDOM.err"
  "$SMOKE" --tool "$2" --mode m --image "$FAKE_IMG" --run-script "$RS" \
    --bucket bogusbucket --auth anonymous --out "$WORK/envout.$RANDOM" \
    --env "$3" >/dev/null 2>"$out" || rc=$?
  if [ "$rc" -ne 0 ] && ! grep -qiE 'refused|credential class|denylist|control character' "$out"; then
    ok "env-guard accepts $1 (passed guard, failed later at $(grep -oiE 'not registered|registry-lookup' "$out" | head -1))"
  else
    bad "env-guard should accept $1 — exit $rc; $(head -1 "$out")"
  fi
}
env_accept "s3-fast-list RUST_LOG" s3-fast-list "RUST_LOG=s3_fast_list=debug"
env_accept "minio-mc anonymous alias" minio-mc "MC_HOST_s3=https://s3.amazonaws.com"
env_reject "RUST_LOG for wrong tool" aws-cli "RUST_LOG=debug"
env_reject "global retry behavior" aws-cli "AWS_MAX_ATTEMPTS=10"
env_reject "minio-mc endpoint must be exact" minio-mc "MC_HOST_s3=https://user:pass@s3.amazonaws.com"
env_reject "GITHUB_TOKEN (TOKEN class)" s3-fast-list "GITHUB_TOKEN=x"
env_reject "MY_API_KEY (API_KEY class)" s3-fast-list "MY_API_KEY=x"
env_reject "FOO_SECRET (SECRET class)" s3-fast-list "FOO_SECRET=x"
env_reject "DB_PASSWORD (PASSWORD class)" s3-fast-list "DB_PASSWORD=x"
env_reject "SESSION_AUTH (AUTH class)" s3-fast-list "SESSION_AUTH=x"
env_reject "AWS_ACCESS_KEY_ID (ACCESS_KEY)" s3-fast-list "AWS_ACCESS_KEY_ID=x"
env_reject "AWS_PROFILE (denylist)" s3-fast-list "AWS_PROFILE=x"
env_reject "AWS_CONFIG_FILE (denylist)" s3-fast-list "AWS_CONFIG_FILE=/x"
env_reject "AWS_EC2_METADATA_DISABLED (deny)" s3-fast-list "AWS_EC2_METADATA_DISABLED=false"
env_reject "AWS_ENDPOINT_URL (deny)" s3-fast-list "AWS_ENDPOINT_URL=http://evil"
env_reject "AWS_ENDPOINT_URL_S3 (deny)" s3-fast-list "AWS_ENDPOINT_URL_S3=http://evil"
env_reject "AWS_CA_BUNDLE (deny)" s3-fast-list "AWS_CA_BUNDLE=/x"
env_reject "AWS_ROLE_ARN (deny)" s3-fast-list "AWS_ROLE_ARN=arn:x"
env_reject "HTTP_PROXY (proxy class)" s3-fast-list "HTTP_PROXY=http://127.0.0.1:9"
env_reject "HTTPS_PROXY (proxy class)" s3-fast-list "HTTPS_PROXY=http://127.0.0.1:9"
env_reject "https_proxy lowercase (proxy)" s3-fast-list "https_proxy=http://127.0.0.1:9"
env_reject "ALL_PROXY (proxy class)" s3-fast-list "ALL_PROXY=socks5://127.0.0.1:9"
env_reject "NO_PROXY (denylist)" s3-fast-list "NO_PROXY=example.com"
env_reject "SSL_CERT_FILE (trust anchor)" s3-fast-list "SSL_CERT_FILE=/tmp/ca"
env_reject "SSL_CERT_DIR (trust anchor)" s3-fast-list "SSL_CERT_DIR=/tmp/ca.d"
env_reject "CURL_CA_BUNDLE (trust anchor)" s3-fast-list "CURL_CA_BUNDLE=/tmp/ca"
env_reject "REQUESTS_CA_BUNDLE (trust anchor)" s3-fast-list "REQUESTS_CA_BUNDLE=/tmp/ca"
env_reject "NODE_EXTRA_CA_CERTS (trust)" s3-fast-list "NODE_EXTRA_CA_CERTS=/tmp/ca"
env_reject "PATH redirection" s3-fast-list "PATH=/tmp/bin"
env_reject "LD_PRELOAD loader injection" s3-fast-list "LD_PRELOAD=/tmp/x.so"
env_reject "control-char newline in value" s3-fast-list "$(printf 'RUST_LOG=a\nb')"

# --- scan fixtures -----------------------------------------------------------
if "$HERE/scan-fixtures-run.sh" >/dev/null 2>&1; then
  ok "scan fixtures (scan-fixtures-run.sh)"
else
  bad "scan fixtures (scan-fixtures-run.sh) — see: $HERE/scan-fixtures-run.sh"
fi

# --- runner security (faked; never changes Docker/firewall) ------------------
if "$HERE/runner-security-regressions.sh" >/dev/null 2>&1; then
  ok "runner security faked regressions"
else
  bad "runner security faked regressions — run: $HERE/runner-security-regressions.sh"
fi

# --- shellcheck gate ---------------------------------------------------------
# Warning severity over every harness script (owner decision 2026-07-17).
# Skipped with a loud note if shellcheck is absent — a missing linter must not
# masquerade as a lint pass, but also must not block the box-independent suite.
if command -v shellcheck >/dev/null 2>&1; then
  if shellcheck -S warning "$HERE"/../*.sh "$HERE"/../security/*.sh "$HERE"/*.sh; then
    ok "shellcheck -S warning over harness/*.sh harness/security/*.sh harness/tests/*.sh"
  else
    bad "shellcheck found issues (severity >= warning)"
  fi
else
  printf 'NOTE: shellcheck not installed — lint gate SKIPPED, not passed.\n'
fi

# --- summary -----------------------------------------------------------------
printf '\n--- not covered here (needs the real bucket or docker) ---\n'
printf '  * union missing/extra/field-mismatch FAIL vs DRIFT — the reference re-list runs a real\n'
printf '    docker s3api list against the bucket; run one scoped real union to exercise it.\n'
printf '  * single-receipt smoke-run.sh -> verify-listing.sh end-to-end PASS (needs docker + bucket).\n'
printf '  * payload 64 MiB truncation + full-raw secret scan on live output (needs a real run).\n'
printf '  * live runner boundary controls (opt-in: harness/runner-security-live-test.sh).\n'

if [ "$fail" -eq 0 ]; then printf '\nALL REGRESSIONS PASS\n'; exit 0; fi
printf '\nREGRESSION FAILURE\n'; exit 1
