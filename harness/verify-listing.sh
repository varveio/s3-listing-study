#!/usr/bin/env bash
# harness/verify-listing.sh — the shared output verifier.
#
# The only thing in this study that issues a verdict on a tool's listing output.
# Centralised because a negative verdict here is an accusation about someone
# else's software (AGENTS.md § Evidence), and because twelve agents each rolling
# their own diff is twelve chances to blame a tool for a bucket that moved.
#
# Usage:
#   verify-listing.sh --tool T --mode M --normalize PATH --bucket B \
#                     --scope full | prefix:PFX | delimiter:DELIM[:PFX] \
#                     --input FILE [--input FILE ...] [--receipt DIR]
#
# Verdicts:
#   PASS   — complete, no duplicates, fields match where the mode exposes them
#   FAIL   — a real discrepancy, and a reference re-list says the bucket did not
#            move — so it belongs to the tool OR to this mode's normalize.sh
#            adapter. This verdict does not distinguish them.
#   DRIFT  — the bucket moved since the snapshot. STOP. Not a tool finding.
#            Only the orchestrator re-baselines.
#   ERROR  — the verifier could not run. Not a pass.
#
set -euo pipefail
export LC_ALL=C   # MANDATORY. sort/comm/join must agree on byte order. Under a
                  # locale with collation rules, `sort` and `comm` disagree and
                  # the diff invents missing/extra keys — inventing a finding
                  # against a tool that did nothing wrong.

die() { printf '\nverify-listing: %s\n' "$*" >&2; exit 3; }   # 3 = verifier ERROR
say() { printf 'verify-listing: %s\n' "$*" >&2; }

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
LOOKUP="$REPO_ROOT/harness/registry-lookup.sh"
# shellcheck source=harness/runner-security-lib.sh
. "$REPO_ROOT/harness/runner-security-lib.sh"

# --------------------------------------------------- field comparison, by policy
# Contract v2: key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class. A field is
# asserted against the manifest ONLY where the adapter emitted a non-`-` value —
# what a mode exposes, it is held to; what it does not, it is not failed for.
# Emits one line per mismatch: <field>\t<key>\ttool=..\tmanifest=..
# mtime is compared by a canonical form (digits of the UTC instant): a manifest
# `...Z` and an adapter `...Z`/`...+00:00` that denote the same second compare
# equal, WITHOUT forking a `date` process per row (single awk pass over the join).
# A contract-v2 mtime is a full UTC instant, digit-shaped, with an explicit UTC
# zone marker. Canonicalisation (digits only) equates `…Z` and `…+00:00`, but it
# ALSO equates garbage that happens to share those digits, so the shape is
# asserted FIRST: a non-`-` adapter mtime that does not match this is not a tool
# FAIL and not a PASS — it is an adapter-contract violation the caller must fix.
# (No `{n}` interval quantifiers: keep the pattern portable across awk variants.)
MTIME_RE_AWK='^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9](Z|[+]00:00|[+]0000)$'
compare_fields() {  # <actual.tsv> <expected.tsv> <out-mismatches> <adapter-id>
  local a="$1" e="$2" out="$3" adapter="${4:-normalize adapter}"
  # Validate the mtime SHAPE on EVERY row of BOTH sides FIRST — before dedup/join.
  # canon_mt (digits only) equates `…Z` and `…+00:00` but ALSO equates garbage
  # sharing those digits, and a malformed mtime on a key that the inner join drops
  # (an extra/missing key, or a later manifest row) would otherwise slip past a
  # first-row-only check and reach a FAIL/PASS. So scan the full files:
  #   * a malformed ADAPTER mtime is an adapter-contract violation -> die naming it
  #     (never a tool FAIL, never a PASS);
  #   * a malformed MANIFEST mtime is a corrupt snapshot -> die naming the manifest
  #     (an orchestrator artifact problem, not a tool finding).
  awk -F'\t' -v re="$MTIME_RE_AWK" '$4!="-" && $4 !~ re {print $1"\t"$4}' "$a" >"$out.badmt.a" \
    || die "mtime scan of actual failed — verifier ERROR, not a verdict"
  if [ -s "$out.badmt.a" ]; then
    die "$adapter (mode ${MODE:-?}) emitted a non-contract-v2 mtime — must be YYYY-MM-DDTHH:MM:SS with Z/+00:00/+0000, never a bare or timezone-less string. This is an adapter-contract violation, not a tool FAIL and not a PASS. Offending (key<TAB>mtime), first 3:
$(head -3 "$out.badmt.a")"
  fi
  awk -F'\t' -v re="$MTIME_RE_AWK" '$4!="-" && $4 !~ re {print $1"\t"$4}' "$e" >"$out.badmt.e" \
    || die "mtime scan of expected failed — verifier ERROR, not a verdict"
  if [ -s "$out.badmt.e" ]; then
    die "manifest mtime is not contract-v2 form (YYYY-MM-DDTHH:MM:SS with Z/+00:00/+0000) — the snapshot is corrupt, not a tool finding; orchestrator re-baselines. Offending (key<TAB>mtime), first 3:
$(head -3 "$out.badmt.e")"
  fi
  sort -u -t$'\t' -k1,1 "$a" >"$out.a" || die "sort of actual failed — verifier ERROR, not a verdict"
  sort -u -t$'\t' -k1,1 "$e" >"$out.e" || die "sort of expected failed — verifier ERROR, not a verdict"
  join -t$'\t' -j1 -o 0,1.2,1.3,1.4,1.5,2.2,2.3,2.4,2.5 "$out.a" "$out.e" >"$out.j" \
    || die "join of actual against expected failed — verifier ERROR, not a verdict"
  awk -F'\t' '
    function canon_mt(s){ sub(/(Z|\+00:00|\+0000)$/,"",s); gsub(/[^0-9]/,"",s); return s }
    { key=$1; a_sz=$2; a_et=$3; a_mt=$4; a_sc=$5; e_sz=$6; e_et=$7; e_mt=$8; e_sc=$9 }
    a_sz!="-" && a_sz!=e_sz                        { print "size\t"  key "\ttool=" a_sz "\tmanifest=" e_sz }
    a_et!="-" && tolower(a_et)!=tolower(e_et)       { print "etag\t"  key "\ttool=" a_et "\tmanifest=" e_et }
    a_mt!="-" && canon_mt(a_mt)!=canon_mt(e_mt)     { print "mtime\t" key "\ttool=" a_mt "\tmanifest=" e_mt }
    a_sc!="-" && a_sc!=e_sc                          { print "storage_class\t" key "\ttool=" a_sc "\tmanifest=" e_sc }
  ' "$out.j" >"$out" || die "field comparison failed — verifier ERROR, not a verdict"
}

# A union death AFTER --out is parsed must leave a durable ERROR artifact: every
# plan-defect verdict (duplicate receipts, mixed modes, overlaps, undesignated
# empty prefix, redaction/truncation refusal, …) is an ERROR the same as
# structural incompleteness, and README promises the union verdict lands in
# union-verify.md. Writing it here keeps that promise for the early deaths too,
# instead of only for the ones that reach the main writer.
union_die() {  # <message>  — requires OUT_DIR set
  mkdir -p "$OUT_DIR" 2>/dev/null || true
  {
    printf '# Verifier (union)\n\n'
    printf '**Verdict: ERROR**\n\n'
    printf -- '- Generated (UTC): %s\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '%s\n' "$*"
  } >"$OUT_DIR/union-verify.md" 2>/dev/null || true
  printf '\nverify-listing: %s\n' "$*" >&2
  exit 3
}

# Resolve a stream path under the contract that produced its run.meta.
# Current records declare run-meta-directory and store inline streams as sibling
# filenames. Historical records have no declaration; preserve their original
# working-directory interpretation rather than guessing a new base and silently
# binding different bytes. Absolute external paths are absolute under either
# contract.
resolve_payload_path() {  # <run.meta> <recorded-path>
  local rm_="$1" raw="$2" base
  case "$raw" in
    /*) printf '%s\n' "$raw"; return 0 ;;
  esac
  base="$(awk -F= '$1=="payload_path_base"{sub(/^[^=]*=/,"");print;exit}' "$rm_")"
  case "$base" in
    run-meta-directory) printf '%s/%s\n' "${rm_%/*}" "$raw" ;;
    '') printf '%s\n' "$raw" ;;  # legacy records: caller working directory
    *) return 1 ;;
  esac
}

# ------------------------------------------------------------- --scope union
# Fan-out completeness. Each shard is a wrapper receipt (repeatable --receipt or
# --receipts-dir). This RE-DERIVES every shard against its own prefix scope
# (verdicts are not trusted), then: (a) concatenates all shards' normalized
# outputs as a MULTISET and counts cross-shard duplicates BEFORE dedup; (b)
# checks the combined output against the FULL manifest exactly; (c) verifies
# scope coverage — the union of shard prefixes plus an EXPLICITLY DESIGNATED
# remainder shard (--remainder, whose run.meta prefix must be empty) must cover
# the manifest's keyspace, or a root-level key belongs to no shard and the union
# is STRUCTURALLY incomplete — a plan defect (ERROR), NOT a tool finding.
#
# The union path mirrors the single-receipt path everywhere it issues a verdict:
# it binds mode across the PREFIX shards (the designated --remainder shard is
# EXEMPT — covering the unprefixed remainder legitimately needs a different
# request shape, typically a delimiter/root listing, hence a different mode; it is
# normalized under its OWN mode against the orphan keys), refuses redaction-altered
# or truncated payloads, selects each shard's verified stream from run.meta (a
# --stream override applies to all shards; default heuristic is stdout unless it is
# empty and stderr is non-empty), copies-then-hashes-then-judges (no TOCTOU), and
# re-lists the reference before ANY FAIL so bucket drift is never charged to the
# tool. All shards must cite the current registry digest, the same bucket, tool,
# and auth mode. Plan-defect deaths write a durable ERROR union-verify.md.
run_union() {
  local UT; UT="$(mktemp -d)"; trap 'rm -rf "${UT:-}"' EXIT
  [ "${#RECEIPTS[@]}" -ge 1 ] || die "--scope union needs at least one --receipt or a --receipts-dir"
  [ -z "$SCOPE_PREFIX_ARG" ] && [ -z "$SCOPE_DELIM_ARG" ] \
    || die "--scope union takes no --scope-prefix / --scope-delimiter; coverage is derived from the shards"
  [ -n "$OUT_DIR" ] || die "--scope union requires --out <dir> — the union verdict must land in a durable artifact (union-verify.md), not only on stdout"

  local caller_mode="$MODE"   # a caller-supplied --mode is a constraint, not an override

  # --remainder designates WHICH receipt is the unprefixed remainder. Resolve it
  # to a realpath so it can be matched against each shard below.
  local remainder_real=""
  if [ -n "$REMAINDER" ]; then
    [ -d "$REMAINDER" ] && [ -r "$REMAINDER/run.meta" ] \
      || union_die "--remainder is not a receipt directory with a run.meta: $REMAINDER"
    remainder_real="$(readlink -f -- "$REMAINDER" 2>/dev/null || printf '%s' "$REMAINDER")"
    # Ensure the designated remainder is part of the shard set.
    local present=0 rr
    for rr in "${RECEIPTS[@]}"; do
      [ "$(readlink -f -- "$rr" 2>/dev/null || printf '%s' "$rr")" = "$remainder_real" ] && { present=1; break; }
    done
    [ "$present" -eq 1 ] || RECEIPTS+=("$REMAINDER")
  fi

  # Pre-check (invocation-plan defect, never a tool FAIL): the same receipt named
  # twice double-counts its keys. realpath-dedup so two spellings of one dir are
  # caught. Duplication cannot be bucket drift — it is a defect in the plan.
  local -A seen_real
  local r rreal
  for r in "${RECEIPTS[@]}"; do
    rreal="$(readlink -f -- "$r" 2>/dev/null || printf '%s' "$r")"
    [ -z "${seen_real[$rreal]:-}" ] \
      || union_die "union names the same receipt twice ($r) — a duplicated shard double-counts its keys. That is an invocation-plan defect (ERROR), not a tool finding."
    seen_real[$rreal]=1
  done

  local now_reg; now_reg="$("$LOOKUP" --digest 2>/dev/null || echo unknown)"
  local ref_tool="" ref_bucket="" ref_auth="" ref_reg="" ref_man="" ref_manpath="" ref_snap="" ref_region="" ref_mode=""
  local -a S_PREFIX S_INPUT S_RECEIPT S_MODE S_ISREM
  local i=0 rm_ t b a reg man manpath snap region pfx mode_ red_
  for r in "${RECEIPTS[@]}"; do
    rm_="$r/run.meta"
    [ -r "$rm_" ] || union_die "no run.meta in $r — receipts produced outside the wrapper do not count"
    t="$(awk -F= '$1=="tool"{sub(/^[^=]*=/,"");print;exit}' "$rm_")"
    b="$(awk -F= '$1=="bucket"{sub(/^[^=]*=/,"");print;exit}' "$rm_")"
    a="$(awk -F= '$1=="auth"{sub(/^[^=]*=/,"");print;exit}' "$rm_")"
    reg="$(awk -F= '$1=="registry_sha256"{sub(/^[^=]*=/,"");print;exit}' "$rm_")"
    man="$(awk -F= '$1=="manifest_sha256"{sub(/^[^=]*=/,"");print;exit}' "$rm_")"
    manpath="$(awk -F= '$1=="manifest"{sub(/^[^=]*=/,"");print;exit}' "$rm_")"
    snap="$(awk -F= '$1=="snapshot_date"{sub(/^[^=]*=/,"");print;exit}' "$rm_")"
    region="$(awk -F= '$1=="region"{sub(/^[^=]*=/,"");print;exit}' "$rm_")"
    pfx="$(awk -F= '$1=="prefix"{sub(/^[^=]*=/,"");print;exit}' "$rm_")"
    mode_="$(awk -F= '$1=="mode"{sub(/^[^=]*=/,"");print;exit}' "$rm_")"
    red_="$(awk -F= '$1=="redaction_changed_bytes"{sub(/^[^=]*=/,"");print;exit}' "$rm_")"
    if [ "$i" -eq 0 ]; then
      ref_tool="$t"; ref_bucket="$b"; ref_auth="$a"; ref_reg="$reg"; ref_man="$man"
      ref_manpath="$manpath"; ref_snap="$snap"; ref_region="$region"
    else
      [ "$t" = "$ref_tool" ]   || union_die "union shard $r is tool '$t', not '$ref_tool' — refusing a mixed set"
      [ "$b" = "$ref_bucket" ] || union_die "union shard $r is bucket '$b', not '$ref_bucket'"
      [ "$a" = "$ref_auth" ]   || union_die "union shard $r is auth '$a', not '$ref_auth' — refusing to union across auth modes"
      [ "$reg" = "$ref_reg" ]  || union_die "union shard $r cites a different registry digest than the first shard"
      [ "$man" = "$ref_man" ]  || union_die "union shard $r cites a different manifest than the first shard"
    fi
    # Mode is captured per shard and bound POST-LOOP across the PREFIX shards only
    # (the designated remainder is exempt — see below).
    S_MODE[i]="$mode_"
    [ "$reg" = "$now_reg" ] \
      || union_die "union shard $r cites registry $reg, current registry is $now_reg — re-run the shard, or check out the registry it used"
    # Redaction rewrites bytes; a verdict over scrubbed output is about our
    # scrubber, not the tool. Same refusal as the single-receipt path.
    [ "${red_:-}" = no ] \
      || union_die "union shard $r has redaction_changed_bytes=${red_:-<unset>} — refusing to verify redacted bytes (a verdict would judge the scrubber's output, not the tool's). Orchestrator reviews."

    # Select the verified stream from run.meta, not a hardcoded stdout: a tool
    # that emits its listing on stderr (found in the s3-fast-list pilot) records
    # both streams; the union picks the one carrying the listing. Copy FIRST, then
    # hash the copy, then judge the copy — no window to swap the payload between
    # the hash and the read (the TOCTOU hole closed in the single-receipt path).
    local so_raw="" so_tr="no" se_raw="" se_tr="no" stream spth_raw spth ssha str got
    for stream in stdout stderr; do
      spth_raw="$(awk -F= -v k="${stream}_path" '$1==k{sub(/^[^=]*=/,"");print;exit}' "$rm_")"
      ssha="$(awk -F= -v k="${stream}_sha256" '$1==k{sub(/^[^=]*=/,"");print;exit}' "$rm_")"
      str="$(awk -F= -v k="${stream}_truncated" '$1==k{sub(/^[^=]*=/,"");print;exit}' "$rm_")"
      [ -n "$spth_raw" ] && [ -n "$ssha" ] || continue
      spth="$(resolve_payload_path "$rm_" "$spth_raw")" \
        || union_die "union shard $r declares an unsupported payload_path_base in run.meta"
      [ -r "$spth" ] || union_die "union shard $r $stream payload not readable: $spth"
      cp -- "$spth" "$UT/shard.$i.$stream.raw"
      got="$(sha256sum "$UT/shard.$i.$stream.raw" | cut -d' ' -f1)"
      [ "$got" = "$ssha" ] \
        || union_die "union shard $r $stream payload no longer matches the sha256 its run.meta cites"
      if [ "$stream" = stdout ]; then so_raw="$UT/shard.$i.stdout.raw"; so_tr="${str:-no}"
      else se_raw="$UT/shard.$i.stderr.raw"; se_tr="${str:-no}"; fi
    done
    # Stream selection. A --stream override pins the stream for ALL shards (fan-out
    # shards share tool+mode, so they share the stream that carries the listing);
    # otherwise the default heuristic prefers the stream that actually carries
    # content, stdout winning ties. The heuristic is a guess — a tool that prints a
    # banner on stdout and its listing on stderr needs the override.
    local sel_raw="" sel_tr="no"
    if [ -n "$STREAM_ARG" ]; then
      case "$STREAM_ARG" in
        stdout) [ -n "$so_raw" ] || union_die "union shard $r has no stdout payload but --stream stdout was given"; sel_raw="$so_raw"; sel_tr="$so_tr" ;;
        stderr) [ -n "$se_raw" ] || union_die "union shard $r has no stderr payload but --stream stderr was given"; sel_raw="$se_raw"; sel_tr="$se_tr" ;;
      esac
    elif [ -n "$so_raw" ] && [ -s "$so_raw" ]; then sel_raw="$so_raw"; sel_tr="$so_tr"
    elif [ -n "$se_raw" ] && [ -s "$se_raw" ]; then sel_raw="$se_raw"; sel_tr="$se_tr"
    elif [ -n "$so_raw" ]; then sel_raw="$so_raw"; sel_tr="$so_tr"
    elif [ -n "$se_raw" ]; then sel_raw="$se_raw"; sel_tr="$se_tr"
    else union_die "union shard $r has no stdout/stderr payload recorded in run.meta"; fi
    # A truncated verified stream cannot prove completeness. Same refusal as the
    # single-receipt path, scoped to the stream actually being verified.
    [ "$sel_tr" != yes ] \
      || union_die "union shard $r verified stream was TRUNCATED at the 64 MiB payload cap — a truncated shard cannot prove completeness; refusing the union verdict"

    # Remainder disambiguation: an empty run.meta prefix is BOTH what a designated
    # remainder records AND what an ordinary full-bucket root run records. Refuse
    # to guess — the caller must designate the remainder with --remainder.
    S_ISREM[i]=0
    if [ -n "$remainder_real" ] && [ "$(readlink -f -- "$r" 2>/dev/null || printf '%s' "$r")" = "$remainder_real" ]; then
      [ -z "$pfx" ] \
        || union_die "--remainder shard $r has a non-empty run.meta prefix ('$pfx') — the remainder is the UNPREFIXED complement; a prefixed run cannot be it"
      pfx=""   # normalise: the remainder carries no prefix
      S_ISREM[i]=1
    else
      [ -n "$pfx" ] \
        || union_die "union shard $r has an empty run.meta prefix but is not the designated --remainder — ambiguous: empty prefix is also what a full-bucket run records; designate the remainder explicitly with --remainder"
    fi

    S_PREFIX[i]="$pfx"; S_INPUT[i]="$sel_raw"; S_RECEIPT[i]="$r"
    i=$((i+1))
  done
  local nshard="$i"

  # Bind the mode across the PREFIX shards only. The designated remainder is
  # EXEMPT: covering the unprefixed remainder legitimately needs a different
  # request shape (typically a delimiter/root listing), hence a different mode; it
  # is normalized under its OWN mode (S_MODE) against the orphan keys. A caller
  # --mode constrains the prefix-shard mode. If the union is remainder-only,
  # ref_mode falls back to the remainder's mode for display and the caller check.
  local ref_mode_set=0
  for j in $(seq 0 $((nshard - 1))); do
    [ "${S_ISREM[j]}" = 1 ] && continue
    if [ "$ref_mode_set" -eq 0 ]; then ref_mode="${S_MODE[j]}"; ref_mode_set=1
    else
      [ "${S_MODE[j]}" = "$ref_mode" ] \
        || union_die "union prefix shard ${S_RECEIPT[j]} is mode '${S_MODE[j]}', not '$ref_mode' — refusing a mixed-mode set (a mode that did not produce a payload cannot certify it)"
    fi
  done
  [ "$ref_mode_set" -eq 1 ] || ref_mode="${S_MODE[0]}"   # remainder-only union
  if [ -n "$caller_mode" ]; then
    [ "$caller_mode" = "$ref_mode" ] \
      || union_die "--mode '$caller_mode' contradicts the prefix shards' run.meta mode ('$ref_mode') — refusing to normalize under a mode that did not produce the payloads"
  fi
  MODE="$ref_mode"

  # Pre-check (invocation-plan defect): overlapping prefixes make a correct tool
  # double-emit the nested keys, which then reads as cross-shard duplicates and a
  # FAIL wrongly charged to the tool. `a/` is a string-prefix of `a/b/`; equal
  # prefixes overlap too. Compared over NON-empty prefixes only (the remainder's
  # empty prefix is a string-prefix of everything, by design).
  local -a PREFIXES; local np=0 j x y
  for j in $(seq 0 $((nshard - 1))); do
    [ -n "${S_PREFIX[j]}" ] && { PREFIXES[np]="${S_PREFIX[j]}"; np=$((np + 1)); }
  done
  for ((x = 0; x < np; x++)); do
    for ((y = 0; y < np; y++)); do
      [ "$x" -eq "$y" ] && continue
      case "${PREFIXES[y]}" in
        "${PREFIXES[x]}"*)
          union_die "union shards overlap: prefix '${PREFIXES[x]}' is a string-prefix of '${PREFIXES[y]}' — a correct tool double-emits nested keys under both. That is an invocation-plan defect (ERROR), not a tool finding." ;;
      esac
    done
  done

  # Manifest snapshot, verified against the digest the shards cite.
  [ -r "$ref_manpath" ] || union_die "manifest not readable: $ref_manpath"
  cp -- "$ref_manpath" "$UT/manifest.gz"
  [ "$(sha256sum "$UT/manifest.gz" | cut -d' ' -f1)" = "$ref_man" ] \
    || union_die "manifest digest mismatch — registry $ref_man vs file. Orchestrator re-baselines."
  gzip -dc -- "$UT/manifest.gz" >"$UT/manifest.tsv"
  local mf; mf="$(head -1 "$UT/manifest.tsv" | awk -F'\t' '{print NF; exit}')"
  [ "$mf" = 5 ] \
    || union_die "manifest has $mf fields — contract v2 expects 5 (key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class). This looks like a pre-2026-07-17 artifact."

  local has_remainder=0
  for j in $(seq 0 $((nshard - 1))); do [ -z "${S_PREFIX[j]}" ] && has_remainder=1; done
  : >"$UT/prefixes"
  [ "$np" -gt 0 ] && printf '%s\n' "${PREFIXES[@]}" >"$UT/prefixes"

  # Structural coverage: a manifest key attributable to no shard prefix can only
  # belong to the designated remainder. Compute the orphan keys once.
  cut -f1 "$UT/manifest.tsv" | awk -v pf="$UT/prefixes" '
    BEGIN{ n=0; while((getline p < pf) > 0){ if(p!="") pref[++n]=p } }
    { covered=0; for(x=1;x<=n;x++){ if(index($0,pref[x])==1){covered=1;break} } if(!covered) print }' \
    >"$UT/orphan.keys"
  local orphan; orphan="$(awk 'END{print NR}' "$UT/orphan.keys")"
  local structural=0
  [ "$orphan" -gt 0 ] && [ "$has_remainder" -eq 0 ] && structural=1

  # Per-shard re-derivation against its own scope; concatenate as a multiset.
  : >"$UT/union.actual"
  local shard_fail=0 shard_notes="" sm se
  for j in $(seq 0 $((nshard - 1))); do
    # Each shard is normalized under ITS OWN mode (the remainder's may differ).
    "$NORMALIZE" "${S_MODE[$j]}" "${S_PREFIX[$j]}" <"${S_INPUT[$j]}" >"$UT/shard.$j.tsv" \
      || union_die "normalize adapter failed on union shard $j (mode ${S_MODE[$j]})"
    awk -F'\t' 'NF!=5{exit 1}' "$UT/shard.$j.tsv" \
      || union_die "union shard $j adapter output is not 5-field (key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class) — a 3-field output is a pre-2026-07-17 artifact"
    cat "$UT/shard.$j.tsv" >>"$UT/union.actual"
    if [ -n "${S_PREFIX[$j]}" ]; then
      awk -F'\t' -v p="${S_PREFIX[$j]}" 'index($1,p)==1' "$UT/manifest.tsv" >"$UT/shard.$j.exp"
    else
      awk -F'\t' 'NR==FNR{k[$1]=1; next} ($1 in k)' "$UT/orphan.keys" "$UT/manifest.tsv" >"$UT/shard.$j.exp"
    fi
    cut -f1 "$UT/shard.$j.tsv" | sort -u >"$UT/shard.$j.akeys"
    cut -f1 "$UT/shard.$j.exp" | sort -u >"$UT/shard.$j.ekeys"
    sm="$(comm -23 "$UT/shard.$j.ekeys" "$UT/shard.$j.akeys" | awk 'END{print NR}')"
    se="$(comm -13 "$UT/shard.$j.ekeys" "$UT/shard.$j.akeys" | awk 'END{print NR}')"
    if [ "$sm" -gt 0 ] || [ "$se" -gt 0 ]; then
      shard_fail=1
      shard_notes="${shard_notes}"$'\n'"  shard $j (prefix='${S_PREFIX[$j]:-<remainder>}'): missing=$sm extra=$se against its own scope"
    fi
  done

  # Union multiset vs full manifest.
  local total distinct dup missing extra fmm
  total="$(awk 'END{print NR}' "$UT/union.actual")"
  cut -f1 "$UT/union.actual" | sort >"$UT/union.keys.all"
  sort -u "$UT/union.keys.all" >"$UT/union.keys"
  distinct="$(awk 'END{print NR}' "$UT/union.keys")"
  dup=$(( total - distinct ))
  uniq -d "$UT/union.keys.all" >"$UT/union.dup.keys" || true
  cut -f1 "$UT/manifest.tsv" | sort -u >"$UT/manifest.keys"
  local expected_keys; expected_keys="$(awk 'END{print NR}' "$UT/manifest.keys")"
  comm -23 "$UT/manifest.keys" "$UT/union.keys" >"$UT/union.missing.keys"
  comm -13 "$UT/manifest.keys" "$UT/union.keys" >"$UT/union.extra.keys"
  missing="$(awk 'END{print NR}' "$UT/union.missing.keys")"
  extra="$(awk 'END{print NR}' "$UT/union.extra.keys")"
  compare_fields "$UT/union.actual" "$UT/manifest.tsv" "$UT/union.field.mismatches" "$NORMALIZE"
  fmm="$(awk 'END{print NR}' "$UT/union.field.mismatches")"

  # Verdict.
  #
  # Structural incompleteness is reported FIRST and as an ERROR (exit 3): a
  # missing remainder shard is a coverage defect in the FAN-OUT PLAN, not the
  # tool losing keys. FAIL stays reserved for a tool/adapter discrepancy after
  # drift exclusion.
  #
  # Drift exclusion: the union re-lists the reference before issuing ANY FAIL —
  # missing/extra AND field mismatches AND a shard that fails its own scope — so
  # an identical-byte overwrite that changes only mtime classifies as DRIFT, not
  # a false accusation. Only pure duplication (dups>0, everything else clean) may
  # FAIL without a re-list: duplication cannot be bucket drift.
  local verdict note="" relist_note=""
  local need_relist=0
  if [ "$missing" -gt 0 ] || [ "$extra" -gt 0 ] || [ "$fmm" -gt 0 ] || [ "$shard_fail" -eq 1 ]; then need_relist=1; fi

  if [ "$structural" -eq 1 ]; then
    verdict=ERROR
    note="**STRUCTURAL INCOMPLETENESS** — $orphan manifest key(s) live under no shard prefix, and no explicit unprefixed-remainder shard (--remainder) was supplied to attribute them to. A union of prefixes never lists a root-level key (e.g. \`index.html\`): this is a coverage defect in the FAN-OUT PLAN, **distinct from the tool dropping keys**, so it is an ERROR, not a tool FAIL. Add a remainder shard (an empty-prefix run) and designate it with --remainder."
  elif [ "$need_relist" -eq 1 ]; then
    say "union discrepancy — re-listing the reference before blaming the tool"
    local himg; himg="$("$LOOKUP" --harness-image)"
    security_docker_control image inspect "$himg" >/dev/null 2>&1 \
      || union_die "digest-pinned harness image is not present locally; campaign execution never pulls"
    security_preflight "$ref_bucket" "$ref_region" \
      || union_die "runner security preflight failed; reference re-list was not started"
    local -a ref_cmd=()
    security_append_docker_control_prefix ref_cmd
    local ref_name="s3study-union-reference-$$-$RANDOM" relist_rc=0
    ref_cmd+=(run --rm --name "$ref_name")
    security_append_evidence_log_args ref_cmd
    security_append_network_args ref_cmd
    ref_cmd+=(-e AWS_EC2_METADATA_DISABLED=true -e TZ=UTC "$himg")
    # Full 5-field capture with the manifest's exact canonicalization (TZ=UTC,
    # ETag unquoted, mtime +00:00 -> Z), so the drift check compares FULL records
    # and an mtime-only overwrite reads as DRIFT rather than a tool FAIL.
    "${ref_cmd[@]}" \
          s3api list-objects-v2 --bucket "$ref_bucket" --region "$ref_region" --no-sign-request \
          --query 'Contents[].[Key,Size,ETag,LastModified,StorageClass]' --output text 2>"$UT/relist.err" \
          | sed 's/"//g' \
          | awk -F'\t' -v OFS='\t' '{ sub(/\+00:00$/,"Z",$4); print $1,$2,$3,$4,$5 }' >"$UT/reference.tsv" || relist_rc=$?
    if [ "$relist_rc" -ne 0 ]; then
      security_reconcile_container_absent "$ref_name" \
        || union_die "reference re-list $(security_docker_status "$relist_rc") and bounded cleanup/absence could not be confirmed; discard this runner"
    fi
    if [ "$relist_rc" -ne 0 ]; then
      verdict=ERROR
      relist_note="reference re-list FAILED"
      note="reference re-list $(security_docker_status "$relist_rc"); cannot attribute the union discrepancy. $(head -2 "$UT/relist.err")"
    else
      awk -F'\t' -v OFS='\t' '{print $1,$2,$3,$4,$5}' "$UT/manifest.tsv" | sort >"$UT/man.rec"
      sort "$UT/reference.tsv" >"$UT/ref.rec"
      if ! cmp -s "$UT/man.rec" "$UT/ref.rec"; then
        verdict=DRIFT
        relist_note="reference re-list DISAGREES with the manifest (bucket moved)"
        note="reference re-list disagrees with the manifest (full 5-field records: a size/ETag/mtime change on a shared key counts) — the bucket moved since $ref_snap. **This is not a tool finding.** Stop; the orchestrator re-baselines."
      else
        verdict=FAIL
        relist_note="reference re-list AGREES with the manifest (bucket did not move)"
        note="reference agrees with the manifest (the bucket did not move): the union has missing/extra keys, field mismatches, or a shard that fails its own scope, so the discrepancy is **in the tool or in this mode's \`normalize.sh\` adapter**. Confirm the adapter before any negative finding.${shard_notes:+ Shard-level:${shard_notes}}"
      fi
    fi
  elif [ "$dup" -gt 0 ]; then
    # Duplication alone — no re-list. Duplication cannot be bucket drift; it is a
    # multiset defect in the tool or adapter.
    verdict=FAIL
    note="cross-shard duplicates ($dup) with no missing/extra/field discrepancy — attributable to the tool or this mode's \`normalize.sh\` adapter, not bucket drift (duplication cannot be drift)."
  else
    verdict=PASS
  fi

  mkdir -p "$OUT_DIR"
  local union_md="$OUT_DIR/union-verify.md"
  {
    printf '# Verifier (union) — `%s` / mode `%s`\n\n' "$ref_tool" "$MODE"
    printf '**Verdict: %s**\n\n' "$verdict"
    printf -- '- Generated (UTC): %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf -- '- Registry digest: `%s`\n' "$now_reg"
    printf -- '- Bucket: `%s`  region `%s`  auth `%s`\n' "$ref_bucket" "$ref_region" "$ref_auth"
    printf -- '- Manifest sha256: `%s`  snapshot %s\n' "$ref_man" "$ref_snap"
    [ -n "$relist_note" ] && printf -- '- Reference re-list: %s\n' "$relist_note"
    printf '\n'
    [ -n "$note" ] && printf '%s\n\n' "$note"
    printf '## Shards\n\n| # | prefix | receipt |\n| --- | --- | --- |\n'
    for j in $(seq 0 $((nshard - 1))); do
      printf '| %s | %s | `%s` |\n' "$j" "${S_PREFIX[$j]:-<remainder>}" "${S_RECEIPT[$j]}"
    done
    printf '\n## Counts\n\n| | |\n| --- | --- |\n'
    printf '| Scope | union of %s shard(s) |\n' "$nshard"
    printf '| Prefix shards | %s |\n' "$np"
    printf '| Remainder shard | %s |\n' "$([ "$has_remainder" -eq 1 ] && echo present || echo absent)"
    printf '| Root-level keys under no prefix | %s |\n' "$orphan"
    printf '| Structural status | %s |\n' "$([ "$structural" -eq 1 ] && echo INCOMPLETE || echo complete)"
    printf '| Manifest keys | %s |\n' "$expected_keys"
    printf '| Emitted records | %s (multiset, pre-dedup) |\n' "$total"
    printf '| Distinct keys | %s |\n' "$distinct"
    printf '| Cross-shard duplicates (before dedup) | %s |\n' "$dup"
    printf '| Missing | %s |\n' "$missing"
    printf '| Extra | %s |\n' "$extra"
    printf '| Field mismatches | %s |\n' "$fmm"
    for k in union.missing union.extra union.dup; do
      f="$UT/$k.keys"; [ -s "$f" ] || continue
      printf '\n### %s (first 20)\n\n```\n' "$k"; head -20 "$f"; printf '```\n'
    done
    [ -s "$UT/union.field.mismatches" ] && { printf '\n### field mismatches (first 20)\n\n```\n'; head -20 "$UT/union.field.mismatches"; printf '```\n'; }
  } >"$union_md"

  say "[union/$ref_tool] $verdict — shards=$nshard expected=$expected_keys distinct=$distinct dups=$dup missing=$missing extra=$extra fields=$fmm root_uncovered=$orphan structural=$structural → $union_md"
  case "$verdict" in
    PASS)  exit 0 ;;
    FAIL)  exit 1 ;;
    DRIFT) exit 4 ;;
    *)     exit 3 ;;
  esac
}

TOOL=""; MODE=""; NORMALIZE=""; BUCKET=""; SCOPE=""; RECEIPT=""; INPUTS=(); RECEIPTS=()
SCOPE_DELIM_ARG=""; SCOPE_PREFIX_ARG=""; SCOPE_KIND_ARG=""; RECEIPTS_DIR=""
OUT_DIR=""; REMAINDER=""; STREAM_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tool) TOOL="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --normalize) NORMALIZE="$2"; shift 2 ;;
    --bucket) BUCKET="$2"; shift 2 ;;
    --scope) SCOPE_KIND_ARG="$2"; shift 2 ;;
    --scope-prefix) SCOPE_PREFIX_ARG="$2"; shift 2 ;;
    --scope-delimiter) SCOPE_DELIM_ARG="$2"; shift 2 ;;
    --input) INPUTS+=("$2"); shift 2 ;;
    --receipt) RECEIPTS+=("$2"); RECEIPT="$2"; shift 2 ;;
    --receipts-dir) RECEIPTS_DIR="$2"; shift 2 ;;
    --remainder) REMAINDER="$2"; shift 2 ;;   # --scope union: designates the unprefixed remainder shard
    --out) OUT_DIR="$2"; shift 2 ;;           # --scope union: durable verdict artifact directory
    --stream) STREAM_ARG="$2"; shift 2 ;;     # --scope union: pin the verified stream (stdout|stderr) for all shards
    *) die "unknown argument: $1" ;;
  esac
done
[ -n "$SCOPE_KIND_ARG" ] || die "--scope is required (full|prefix|delimiter|union)"
[ -n "$NORMALIZE" ] && [ -x "$NORMALIZE" ] || die "--normalize must be an executable adapter"
case "$STREAM_ARG" in
  ""|stdout|stderr) ;;
  *) die "--stream must be stdout or stderr (it pins the verified stream for --scope union)" ;;
esac

# --receipts-dir expands to every immediate subdirectory carrying a run.meta — a
# convenience for a fan-out mode whose shards were each staged as a receipt dir.
if [ -n "$RECEIPTS_DIR" ]; then
  [ -d "$RECEIPTS_DIR" ] || die "--receipts-dir is not a directory: $RECEIPTS_DIR"
  while IFS= read -r d; do
    RECEIPTS+=("$d"); RECEIPT="$d"
  done < <(find "$RECEIPTS_DIR" -mindepth 1 -maxdepth 1 -type d -exec test -e '{}/run.meta' ';' -print | sort)
fi
[ -n "$RECEIPT" ] || die "--receipt is required: the verifier binds to the run that produced the output"

# --scope union is a distinct fan-out path: it derives its shards from receipts,
# re-derives each against its own prefix scope, and checks the multiset union
# against the FULL manifest with explicit root-remainder coverage. It runs here
# and exits, bypassing the single-receipt binding/stamping flow below.
if [ "$SCOPE_KIND_ARG" = union ]; then
  run_union
fi

[ "${#INPUTS[@]}" -gt 0 ] || die "at least one --input is required"

# ------------------------------------------------------- bind to the actual run
# Without this, tool/mode/bucket/inputs are independent claims: one mode's output
# can be checked against another mode's scope and stamped into a third mode's
# receipt, and every artifact still looks consistent. run.meta is the wrapper's
# record of what actually ran; the verifier validates against it rather than
# trusting its own arguments.
META="$RECEIPT/run.meta"
[ -r "$META" ] || die "no run.meta in $RECEIPT — this receipt was not produced by harness/smoke-run.sh, and receipts produced outside the wrapper do not count"
meta() { awk -F= -v k="$1" '$1==k{sub(/^[^=]*=/,""); print; exit}' "$META"; }

M_TOOL="$(meta tool)"; M_MODE="$(meta mode)"; M_BUCKET="$(meta bucket)"
M_PREFIX="$(meta prefix)"
M_REDACTED="$(meta redaction_changed_bytes)"; M_EXIT="$(meta exit_code)"; M_TIMEDOUT="$(meta timed_out)"

[ -z "$TOOL" ]   || [ "$TOOL" = "$M_TOOL" ]     || die "--tool '$TOOL' contradicts run.meta ('$M_TOOL')"
[ -z "$MODE" ]   || [ "$MODE" = "$M_MODE" ]     || die "--mode '$MODE' contradicts run.meta ('$M_MODE')"
[ -z "$BUCKET" ] || [ "$BUCKET" = "$M_BUCKET" ] || die "--bucket '$BUCKET' contradicts run.meta ('$M_BUCKET')"
TOOL="$M_TOOL"; MODE="$M_MODE"; BUCKET="$M_BUCKET"

# Every input must BE a payload this run produced, identified by content and
# resolved path — not merely "not contradicting" one. The first version only
# hash-checked an input whose path string exactly equalled stdout_path, so any
# other readable file (including a different spelling of the same path) sailed
# through unchecked: a failed run could be verified against an unrelated complete
# listing and stamped PASS.
declare -A PAYLOAD_OK PAYLOAD_TRUNC
for r in "${RECEIPTS[@]}"; do
  rm_="$r/run.meta"
  [ -r "$rm_" ] || die "no run.meta in $r — receipts produced outside the wrapper do not count"
  rmeta() { awk -F= -v k="$1" -v f="$2" '$1==k{sub(/^[^=]*=/,""); print; exit}' "$2"; }
  r_tool="$(rmeta tool "$rm_")"; r_mode="$(rmeta mode "$rm_")"; r_bucket="$(rmeta bucket "$rm_")"
  r_reg="$(rmeta registry_sha256 "$rm_")"; r_man="$(rmeta manifest_sha256 "$rm_")"
  r_red="$(rmeta redaction_changed_bytes "$rm_")"
  [ "$r_tool" = "$M_TOOL" ] && [ "$r_mode" = "$M_MODE" ] && [ "$r_bucket" = "$M_BUCKET" ] \
    || die "receipt $r is for $r_tool/$r_mode/$r_bucket, not $M_TOOL/$M_MODE/$M_BUCKET — refusing to verify a mixed set"
  [ "$r_reg" = "$(rmeta registry_sha256 "$META")" ] && [ "$r_man" = "$(rmeta manifest_sha256 "$META")" ] \
    || die "receipt $r cites a different registry/manifest than $RECEIPT — refusing to verify against two references"
  [ "$r_red" = no ] || die "receipt $r has redaction_changed_bytes=$r_red — see $RECEIPT note"
  # BOTH streams. run.meta records stderr_path/stderr_sha256 too, and mapping
  # only stdout made any tool that emits its listing to stderr unverifiable —
  # passing the stderr payload THE RECEIPT ITSELF CITES died with "not a payload
  # recorded by any --receipt given". Found by the s3-fast-list pilot; an
  # oversight in the round-2 input-binding fix, not a decision.
  for stream in stdout stderr; do
    pth_raw="$(rmeta "${stream}_path" "$rm_")"; sha="$(rmeta "${stream}_sha256" "$rm_")"
    [ -n "$pth_raw" ] && [ -n "$sha" ] || continue
    pth="$(resolve_payload_path "$rm_" "$pth_raw")" \
      || die "receipt $r declares an unsupported payload_path_base in run.meta"
    real="$(readlink -f -- "$pth" 2>/dev/null || printf '%s' "$pth")"
    PAYLOAD_OK["$real"]="$sha"
    # Per-stream truncation flag (contract v2 payload cap). A verified payload
    # that was truncated cannot prove completeness — checked at input-binding time.
    trunc="$(rmeta "${stream}_truncated" "$rm_")"
    PAYLOAD_TRUNC["$real"]="${trunc:-no}"
  done
done

# Every input must BE a payload one of these receipts produced, matched by
# resolved path AND content. The first version only hash-checked an input whose
# path string exactly equalled stdout_path, so any other readable file — or a
# different spelling of the same path — sailed through: a failed run could be
# verified against an unrelated complete listing and stamped PASS.
for f in "${INPUTS[@]}"; do
  [ -r "$f" ] || die "input not readable: $f"
  fr="$(readlink -f -- "$f" 2>/dev/null || printf '%s' "$f")"
  want="${PAYLOAD_OK[$fr]:-}"
  [ -n "$want" ] \
    || die "input '$f' is not a payload recorded by any --receipt given.
        The verifier only judges bytes the wrapper produced. For a fan-out mode, pass every
        invocation's --receipt alongside its --input."
  fs="$(sha256sum "$f" | cut -d' ' -f1)"
  [ "$fs" = "$want" ] \
    || die "input $f no longer matches the sha256 its receipt cites ($want) — the evidence changed under us"
  # A truncated stream cannot certify completeness. Refuse a verdict on it — this
  # is scoped to the STREAM being verified: truncation of stderr alone does not
  # block verifying a complete stdout listing, because only the verified input's
  # stream matters here.
  [ "${PAYLOAD_TRUNC[$fr]:-no}" != yes ] \
    || die "input $f is from a payload stream the wrapper TRUNCATED at the 64 MiB cap (contract v2).
        A truncated listing cannot prove it listed everything — refusing to issue a completeness verdict.
        (Truncation of stderr alone does not block verifying a complete stdout listing.)"
done
INPUT_SNAPSHOT_PENDING=1   # snapshotted below, once TMP exists

# Redaction rewrites bytes. If it changed anything, the verifier is judging our
# scrubber's output, not the tool's — a legitimate key shaped like a credential
# would be altered and then failed against the manifest.
[ "$M_REDACTED" = no ] \
  || die "run.meta says redaction altered the payload bytes. Refusing to verify: a verdict here
        would be about the redacted text, not about what the tool emitted. Orchestrator must review."

if [ "$M_TIMEDOUT" = 1 ]; then
  say "note: the run timed out (exit $M_EXIT) — its output is by definition partial, and a completeness FAIL is expected rather than a tool defect"
fi

# Manifest identity comes from run.meta — the registry the RUN used — not from a
# fresh lookup. Running under SMOKE_REGISTRY=A and verifying under B would
# otherwise compare the run against B's snapshot, and receipt.md and verify.md
# would cite different manifests while both looking authoritative.
M_REG_PATH="$(meta registry_path)"; M_REG_SHA="$(meta registry_sha256)"
MANIFEST="$(meta manifest)"; MANIFEST_SHA="$(meta manifest_sha256)"
SNAPSHOT_DATE="$(meta snapshot_date)"; REGION="$(meta region)"
[ -n "$MANIFEST" ] && [ -n "$MANIFEST_SHA" ] || die "run.meta lacks manifest identity — not a wrapper receipt"
now_reg_sha="$("$LOOKUP" --digest 2>/dev/null || echo unknown)"
if [ "$now_reg_sha" != "$M_REG_SHA" ]; then
  die "the registry changed since this run: receipt cites sha256 $M_REG_SHA, current registry is $now_reg_sha.
        Verifying now would judge the run against a registry it never saw. Re-run the mode, or
        check out the registry the run used."
fi
HARNESS_IMAGE="$("$LOOKUP" --harness-image)"

# Refuse a re-verify BEFORE writing anything. The first version checked this
# *after* verify.md had already been overwritten, so re-verifying a stamped PASS
# wrote a fresh FAIL report and then died — leaving receipt.md saying PASS and
# verify.md saying FAIL: precisely the inconsistency the guard exists to prevent,
# created by the guard.
PLACEHOLDER='_(filled in by `harness/verify-listing.sh`)_'
[ -f "$RECEIPT/receipt.md" ] || die "no receipt.md in $RECEIPT to stamp"
ph_hits="$(grep -cF -- "$PLACEHOLDER" "$RECEIPT/receipt.md" || true)"
case "$ph_hits" in
  1) ;;
  0) die "receipt $RECEIPT/receipt.md carries no verdict placeholder — it was already verified.
        Nothing has been written. Re-run the mode to produce a fresh receipt rather than
        restamping this one." ;;
  *) die "receipt has $ph_hits verdict placeholders — refusing to guess which is the verdict" ;;
esac

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# Snapshot the manifest, hash the snapshot, and use the snapshot throughout.
# Hashing a path and then reopening it lets the file be replaced (or a symlink
# retargeted) in between, so the expectations get derived from bytes whose cited
# digest was never checked.
[ -r "$MANIFEST" ] || die "manifest not readable: $MANIFEST"
cp -- "$MANIFEST" "$TMP/manifest.gz"
actual_sha="$(sha256sum "$TMP/manifest.gz" | cut -d' ' -f1)"
[ "$actual_sha" = "$MANIFEST_SHA" ] \
  || die "manifest digest mismatch — registry $MANIFEST_SHA, file $actual_sha. Orchestrator re-baselines."

read_manifest() { gzip -dc -- "$TMP/manifest.gz"; }

# Contract v2: the manifest is 5-field. A 3-field manifest is a pre-2026-07-17
# artifact and must fail loudly rather than be half-verified. Consume the whole
# stream (no early `head` close): closing the gzip pipe mid-write SIGPIPEs the
# decompressor, and under `set -o pipefail` that aborts the verifier silently.
mf_fields="$(read_manifest | awk -F'\t' 'NR==1{n=NF} END{print n+0}')"
[ "$mf_fields" = 5 ] \
  || die "manifest has $mf_fields field(s) — contract v2 expects 5 (key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class).
        This looks like a pre-2026-07-17 artifact; re-baseline with the pinned client before verifying."

# Snapshot the inputs as well, and re-verify the hash against the snapshot. The
# round-1 TOCTOU fix covered the manifest but not the evidence: hashing an input
# and reopening it later lets a concurrent rerun or symlink retarget swap the
# bytes being judged for bytes nobody checked.
SNAP_INPUTS=()
if [ "${INPUT_SNAPSHOT_PENDING:-0}" = 1 ]; then
  i=0
  for f in "${INPUTS[@]}"; do
    cp -- "$f" "$TMP/input.$i"
    fr="$(readlink -f -- "$f" 2>/dev/null || printf '%s' "$f")"
    [ "$(sha256sum "$TMP/input.$i" | cut -d' ' -f1)" = "${PAYLOAD_OK[$fr]}" ] \
      || die "input $f changed while being snapshotted — refusing to judge unstable evidence"
    SNAP_INPUTS+=("$TMP/input.$i"); i=$((i+1))
  done
fi

# ------------------------------------------------------- expected set by scope
# Derived from the manifest — never from a second listing. The manifest is the
# ground truth; deriving the expectation from a fresh listing would compare the
# tool against whatever the bucket happens to be doing right now.
# Scope comes in as separate arguments. A packed `delimiter:D:P` string cannot
# represent a delimiter that is itself `:`, and silently mis-splits when it tries.
SCOPE_KIND="$SCOPE_KIND_ARG"; SCOPE_PREFIX="$SCOPE_PREFIX_ARG"; SCOPE_DELIM="$SCOPE_DELIM_ARG"
SCOPE="$SCOPE_KIND"
case "$SCOPE_KIND" in
  full)
    [ -z "$SCOPE_PREFIX" ] && [ -z "$SCOPE_DELIM" ] || die "scope 'full' takes no prefix or delimiter"
    read_manifest >"$TMP/expected.tsv" ;;
  prefix)
    [ -n "$SCOPE_PREFIX" ] || die "scope 'prefix' needs --scope-prefix"
    SCOPE="prefix=$SCOPE_PREFIX"
    read_manifest | awk -F'\t' -v p="$SCOPE_PREFIX" 'index($1,p)==1' >"$TMP/expected.tsv" ;;
  delimiter)
    [ -n "$SCOPE_DELIM" ] || die "scope 'delimiter' needs --scope-delimiter"
    SCOPE="delimiter=$SCOPE_DELIM prefix=${SCOPE_PREFIX:-<none>}"
    # S3 delimiter semantics, derived: a key under PFX whose remainder contains
    # DELIM collapses into a CommonPrefix (through the first DELIM inclusive);
    # every other key is returned whole. CommonPrefixes carry no size/etag, so
    # they normalise to `-`.
    #
    # substr(rest, 1, i + length(d) - 1), NOT substr(rest, 1, i): index() returns
    # where the delimiter STARTS, so a multi-character delimiter would be cut
    # mid-token — `a--b` with delimiter `--` yielding CommonPrefix `a-`.
    read_manifest | awk -F'\t' -v OFS='\t' -v p="$SCOPE_PREFIX" -v d="$SCOPE_DELIM" '
      index($1,p)==1 {
        rest = substr($1, length(p)+1)
        i = index(rest, d)
        if (i > 0) { cp[p substr(rest, 1, i + length(d) - 1)] = 1 }
        else { print $1, $2, $3, $4, $5 }
      }
      END { for (k in cp) print k, "-", "-", "-", "-" }' >"$TMP/expected.tsv" ;;
  *) die "unknown scope '$SCOPE_KIND' (full | prefix | delimiter)" ;;
esac
[ -s "$TMP/expected.tsv" ] || die "expected set for scope '$SCOPE' is empty — the scope is wrong, or the manifest is"

# The wrapper recorded which prefix the tool was actually pointed at. Verifying a
# prefix-scoped run against a differently-scoped expectation manufactures
# missing/extra keys out of nothing.
# Guarded in BOTH directions. Checking only "M_PREFIX nonempty and differs" let a
# full-bucket run (M_PREFIX empty) be verified as prefix-scoped: every key outside
# the prefix becomes an "extra", the reference confirms the prefix did not drift,
# and the tool eats a FAIL for listing exactly what it was asked to list.
if [ "$SCOPE_KIND" = prefix ] && [ "$M_PREFIX" != "$SCOPE_PREFIX" ]; then
  die "--scope-prefix '${SCOPE_PREFIX}' contradicts the prefix the run actually used ('${M_PREFIX:-<none: full-bucket run>}')"
fi
if [ "$SCOPE_KIND" = full ] && [ -n "$M_PREFIX" ]; then
  die "scope 'full' but the run was scoped to prefix '$M_PREFIX' — verifying a prefix run against the whole manifest would fabricate missing keys"
fi
if [ "$SCOPE_KIND" = delimiter ] && [ -n "$M_PREFIX" ] && [ "$M_PREFIX" != "$SCOPE_PREFIX" ]; then
  die "--scope-prefix '${SCOPE_PREFIX:-<none>}' contradicts the run's prefix ('$M_PREFIX')"
fi

# ----------------------------------------------------------------- actual set
# Fan-out modes emit several outputs; they concatenate as a MULTISET. Duplicates
# are counted BEFORE any dedup — a set union would destroy exactly the evidence
# we are here to collect.
: >"$TMP/actual.tsv"
for f in "${SNAP_INPUTS[@]}"; do
  # The run's prefix (from run.meta) is passed as $2 so a mode that prints
  # path-relative names can reconstruct full keys; empty string for full-bucket.
  "$NORMALIZE" "$MODE" "$M_PREFIX" <"$f" >>"$TMP/actual.tsv" \
    || die "normalize adapter failed on $f (mode $MODE)"
done
[ -s "$TMP/actual.tsv" ] || die "normalized output is empty — adapter bug, or the tool emitted nothing"

adapter_rc=0
awk -F'\t' 'NF!=5 {print NR": "NF" fields"; bad=1} END{exit bad?1:0}' "$TMP/actual.tsv" >"$TMP/badrows" 2>&1 \
  || adapter_rc=$?
if [ "$adapter_rc" != 0 ]; then
  hint=""
  head -1 "$TMP/actual.tsv" | awk -F'\t' 'END{exit (NF==3)?0:1}' \
    && hint=" — 3 fields is a pre-2026-07-17 adapter; contract v2 expects key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class (\`-\` for any field the mode does not expose)"
  die "normalize adapter emitted rows that are not 5-field key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class$hint:
$(head -3 "$TMP/badrows")"
fi

# awk NR, not `wc -l`: wc counts newline CHARACTERS, so output whose last record
# lacks a trailing newline is undercounted by one. Two identical records with no
# final newline would give total=1, distinct=1, dup_count=0 — a duplicate that
# PASSES. Counting records is the whole job here.
total_records="$(awk 'END{print NR}' "$TMP/actual.tsv")"
cut -f1 "$TMP/actual.tsv" | sort >"$TMP/actual.keys.all"
sort -u "$TMP/actual.keys.all" >"$TMP/actual.keys"
distinct_keys="$(awk 'END{print NR}' "$TMP/actual.keys")"
dup_count=$(( total_records - distinct_keys ))
uniq -d "$TMP/actual.keys.all" >"$TMP/dup.keys" || true

cut -f1 "$TMP/expected.tsv" | sort -u >"$TMP/expected.keys"
expected_keys="$(awk 'END{print NR}' "$TMP/expected.keys")"

comm -23 "$TMP/expected.keys" "$TMP/actual.keys" >"$TMP/missing.keys"
comm -13 "$TMP/expected.keys" "$TMP/actual.keys" >"$TMP/extra.keys"
missing_count="$(awk 'END{print NR}' "$TMP/missing.keys")"
extra_count="$(awk 'END{print NR}' "$TMP/extra.keys")"

# ------------------------------------------------- field assertions (size/etag)
# Only where the mode actually exposes them. A mode that emits keys only is
# checked on keys only, and the receipt says so — rather than being failed for
# not producing data it never claimed to.
# Field coverage is REPORTED, not inferred and then overstated. An adapter that
# emits a value for some rows and `-` for others previously produced a report
# claiming "key + size + etag" while silently skipping every `-` row. Say how
# many rows were actually field-checked and how many were exempted.
# Size and ETag are counted SEPARATELY. An "either one exists" count reports
# "key + size + etag on all rows" when every row carries a size and `-` for ETag —
# a PASS receipt materially overstating what was actually compared.
# Each field is counted SEPARATELY and asserted only where the adapter emitted a
# non-`-` value (by policy). An "either one exists" count would report "all four
# checked" when every row carries a size and `-` for the rest — a PASS receipt
# materially overstating what was compared.
rows_with_size="$(awk -F'\t'  '$2!="-" {n++} END{print n+0}' "$TMP/actual.tsv")"
rows_with_etag="$(awk -F'\t'  '$3!="-" {n++} END{print n+0}' "$TMP/actual.tsv")"
rows_with_mtime="$(awk -F'\t' '$4!="-" {n++} END{print n+0}' "$TMP/actual.tsv")"
rows_with_sc="$(awk -F'\t'    '$5!="-" {n++} END{print n+0}' "$TMP/actual.tsv")"
rows_without="$(awk -F'\t' '$2=="-" && $3=="-" && $4=="-" && $5=="-" {n++} END{print n+0}' "$TMP/actual.tsv")"
any_fields=$(( rows_with_size + rows_with_etag + rows_with_mtime + rows_with_sc ))
field_mismatches=0
if [ "$any_fields" -eq 0 ]; then
  fields_checked="keys only — this mode exposed none of size/etag/mtime/storage_class on any of $total_records rows"
else
  compare_fields "$TMP/actual.tsv" "$TMP/expected.tsv" "$TMP/field.mismatches" "$NORMALIZE"
  field_mismatches="$(awk 'END{print NR}' "$TMP/field.mismatches")"
  fields_checked="size $rows_with_size/$total_records; etag $rows_with_etag/$total_records; mtime $rows_with_mtime/$total_records; storage_class $rows_with_sc/$total_records rows compared (by policy)"
  [ "$rows_without" -gt 0 ] && fields_checked="$fields_checked; $rows_without row(s) exposed none and were checked on key only"
fi

# ------------------------------------------------------- drift before blame
# The verifier's first move on ANY mismatch is a fresh reference re-list. A
# third-party bucket can move under us mid-campaign, and recording drift as a
# tool failure would be a false public accusation. Reference is re-listed at the
# scope in question, with the pinned harness client, via the registry's exact
# canonicalisation.
VERDICT=""; DRIFT_NOTE=""
discrepancy=0
[ "$missing_count" -gt 0 ] && discrepancy=1
[ "$extra_count" -gt 0 ] && discrepancy=1
[ "$dup_count" -gt 0 ] && discrepancy=1
[ "${field_mismatches:-0}" != "0" ] && [ "${field_mismatches:-0}" -gt 0 ] 2>/dev/null && discrepancy=1

if [ "$discrepancy" -eq 1 ]; then
  say "discrepancy found — re-listing the reference before blaming the tool"
  relist_prefix="$SCOPE_PREFIX"
  security_docker_control image inspect "$HARNESS_IMAGE" >/dev/null 2>&1 \
    || die "digest-pinned harness image is not present locally; campaign execution never pulls"
  security_preflight "$BUCKET" "$REGION" \
    || die "runner security preflight failed; reference re-list was not started"
  REF_CMD=()
  security_append_docker_control_prefix REF_CMD
  REF_NAME="s3study-reference-$$-$RANDOM"; relist_rc=0
  REF_CMD+=(run --rm --name "$REF_NAME")
  security_append_evidence_log_args REF_CMD
  security_append_network_args REF_CMD
  REF_CMD+=(-e AWS_EC2_METADATA_DISABLED=true -e TZ=UTC "$HARNESS_IMAGE")
  # Capture the FULL 5-field record with the manifest's exact canonicalization —
  # TZ=UTC (an mtime read under a local TZ would false-positive drift), ETag
  # unquoted, and mtime `+00:00` rewritten to `Z`. A key-only or key/size/etag
  # drift check misses an identical-byte overwrite that changes ONLY mtime: the
  # key set is identical, drift reads as "no drift", and the tool eats a FAIL for
  # correctly reporting the new mtime. That is the precise false accusation this
  # whole re-list exists to prevent, and overwrites are far more common than
  # re-keying, so the drift comparison must be over full records.
  "${REF_CMD[@]}" \
        s3api list-objects-v2 --bucket "$BUCKET" --region "$REGION" --no-sign-request \
        ${relist_prefix:+--prefix "$relist_prefix"} \
        --query 'Contents[].[Key,Size,ETag,LastModified,StorageClass]' --output text 2>"$TMP/relist.err" \
        | sed 's/"//g' \
        | awk -F'\t' -v OFS='\t' '{ sub(/\+00:00$/,"Z",$4); print $1,$2,$3,$4,$5 }' >"$TMP/reference.tsv" || relist_rc=$?
  if [ "$relist_rc" -ne 0 ]; then
    security_reconcile_container_absent "$REF_NAME" \
      || die "reference re-list $(security_docker_status "$relist_rc") and bounded cleanup/absence could not be confirmed; discard this runner"
  fi
  if [ "$relist_rc" -ne 0 ]; then
    VERDICT=ERROR
    DRIFT_NOTE="reference re-list $(security_docker_status "$relist_rc"); cannot attribute the discrepancy. $(head -2 "$TMP/relist.err")"
  else
    read_manifest | awk -F'\t' -v OFS='\t' -v p="$SCOPE_PREFIX" 'index($1,p)==1 {print $1,$2,$3,$4,$5}' \
      | sort >"$TMP/manifest.scope.rec"
    sort "$TMP/reference.tsv" >"$TMP/reference.rec"
    if ! cmp -s "$TMP/manifest.scope.rec" "$TMP/reference.rec"; then
      ref_only="$(comm -13 "$TMP/manifest.scope.rec" "$TMP/reference.rec" | awk 'END{print NR}')"
      man_only="$(comm -23 "$TMP/manifest.scope.rec" "$TMP/reference.rec" | awk 'END{print NR}')"
      changed="$(comm -12 <(cut -f1 "$TMP/manifest.scope.rec" | sort -u) <(cut -f1 "$TMP/reference.rec" | sort -u) \
                 | wc -l)"
      VERDICT=DRIFT
      DRIFT_NOTE="reference re-list disagrees with the manifest for this scope: ${ref_only} record(s) present now but not in the snapshot, ${man_only} in the snapshot but not now (${changed} keys common to both — a size/ETag/mtime change on a shared key counts here). The bucket moved since ${SNAPSHOT_DATE}. **This is not a tool finding.** Stop; the orchestrator re-baselines (single manifest owner)."
    fi
  fi
fi

if [ -z "$VERDICT" ]; then
  if [ "$discrepancy" -eq 1 ]; then
    VERDICT=FAIL
    # "the tool" would overclaim. The reference re-list establishes only that the
    # BUCKET did not move; it says nothing about whether the tool or the
    # normalize.sh adapter produced the discrepancy — and the adapter is written
    # by the same agent, against this same tool, and is far newer than the tool.
    # Blaming the tool for an adapter bug is exactly the accusation AGENTS.md
    # § Evidence forbids, so name both suspects and let the agent separate them.
    DRIFT_NOTE="reference re-list agrees with the manifest for this scope, so the bucket did not move. The discrepancy is therefore **in the tool or in this mode's \`normalize.sh\` adapter** — this verdict does not distinguish them. Before recording any negative finding about the tool, confirm the adapter faithfully represents the raw output (methodology: negative findings ship with exact invocation and raw output, or they don't ship)."
  else
    VERDICT=PASS
  fi
fi

# ------------------------------------------------------------------- report
report="${RECEIPT:+$RECEIPT/verify.md}"
{
  printf '# Verifier — `%s` / mode `%s`\n\n' "$TOOL" "$MODE"
  printf '**Verdict: %s**\n\n' "$VERDICT"
  [ -n "$DRIFT_NOTE" ] && printf '%s\n\n' "$DRIFT_NOTE"
  printf '| | |\n| --- | --- |\n'
  printf '| Scope | `%s` |\n' "$SCOPE"
  printf '| Fields checked | %s |\n' "$fields_checked"
  printf '| Registry | `%s` (sha256 `%s`, as used by the run) |\n' "$M_REG_PATH" "$M_REG_SHA"
  printf '| Manifest | `%s` |\n' "$MANIFEST_SHA"
  printf '| Snapshot date | %s |\n' "$SNAPSHOT_DATE"
  printf '| Expected keys | %s |\n' "$expected_keys"
  printf '| Emitted records | %s (multiset, pre-dedup) |\n' "$total_records"
  printf '| Distinct keys | %s |\n' "$distinct_keys"
  printf '| Duplicates | %s |\n' "$dup_count"
  printf '| Missing | %s |\n' "$missing_count"
  printf '| Extra | %s |\n' "$extra_count"
  printf '| Field mismatches | %s |\n' "${field_mismatches:-n/a}"
  printf '| Inputs | %s |\n' "${#INPUTS[@]}"
  printf '\nDuplicates are counted **before** dedup: the completeness diff needs a\n'
  printf 'deduplicated set, but a set union would destroy the duplicate evidence,\n'
  printf 'so the multiset is counted first.\n'
  for k in missing extra dup; do
    f="$TMP/$k.keys"
    [ -s "$f" ] || continue
    printf '\n### %s (first 20)\n\n```\n' "$k"
    head -20 "$f"; printf '```\n'
  done
  if [ -s "$TMP/field.mismatches" ]; then
    printf '\n### field mismatches (first 20)\n\n```\n'; head -20 "$TMP/field.mismatches"; printf '```\n'
  fi
} >"${report:-/dev/stdout}"

# Stamp the verdict into the receipt. Requires the placeholder to be present and
# to be replaced exactly once: a best-effort sed silently no-ops on an
# already-stamped receipt, so re-verifying a PASS as FAIL would rewrite
# verify.md while leaving receipt.md still saying PASS — the two artifacts
# disagreeing, with the wrong one being the readable summary.
case "$ph_hits" in
  1) tmp="$(mktemp)"
     # Literal splice via index/substr. NOT sub()/sed: both take a REGEX, and
     # this placeholder contains ( ) . — so sub() matches nothing while index()
     # in the guard matches fine. The file gets rewritten unchanged, the verdict
     # is silently dropped, and receipt.md keeps its placeholder forever. That
     # bug shipped in the first draft of this very fix and was caught only
     # because a test re-verified an already-stamped receipt and got rc=0.
     awk -v ph="$PLACEHOLDER" -v v="**$VERDICT** — see \`verify.md\`" '
       { i = index($0, ph)
         if (i > 0 && !done) { $0 = substr($0,1,i-1) v substr($0, i+length(ph)); done=1 }
         print }
       END { if (!done) exit 1 }' "$RECEIPT/receipt.md" >"$tmp" \
       || die "verdict stamp failed to apply — refusing to leave receipt.md unstamped while verify.md claims a verdict"
     mv "$tmp" "$RECEIPT/receipt.md"
     grep -qF -- "$PLACEHOLDER" "$RECEIPT/receipt.md" \
       && die "verdict stamp did not take — placeholder survives in receipt.md"
     : ;;
  *) die "placeholder count changed under us ($ph_hits) — refusing to stamp" ;;
esac

# Every number that can produce a FAIL appears here, including fields: a summary
# reading "FAIL — dups=0 missing=0 extra=0" looks like a verifier bug, and an
# agent that believes it is a bug is an agent that ignores a real finding.
say "[$TOOL/$MODE] $VERDICT — expected=$expected_keys distinct=$distinct_keys dups=$dup_count missing=$missing_count extra=$extra_count fields=${field_mismatches:-n/a}"
case "$VERDICT" in
  PASS)  exit 0 ;;
  FAIL)  exit 1 ;;
  DRIFT) exit 4 ;;
  *)     exit 3 ;;
esac
