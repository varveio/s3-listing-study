# Authoring a capsule from a derivation

[`tool-structure.md`](tool-structure.md) says what each file in a capsule must
contain. [`tool-onboarding.md`](tool-onboarding.md) says where capsule-building
sits in the sequence. Neither says **how to actually produce one** from a pile of
research, and the first person to do it re-invented the procedure and got parts
of it wrong. This page is that procedure.

It is working method, not law. Deviate where the subject calls for it, but know
what you are deviating from.

## Build in dependency order, not reading order

The finished capsule reads README → docs → data. **Build it in exactly the
reverse.**

1. **`data/claims.json` first.** It is the backbone. Every current statement in
   the capsule resolves to a claim ID, so the ledger decides what the prose is
   allowed to say. Writing prose first produces confident sentences with no
   claim behind them, and the fix is always to weaken the prose — cheaper to
   never write it.
2. **`data/tool.json`** alongside it: the tested identity the ledger's evidence
   refers to.
3. **`docs/mechanism.md` and `docs/running.md`** from the ledger, citing claim
   IDs rather than restating evidence.
4. **`README.md` last**, from the docs. It is a landing page: it summarises what
   the docs establish and links onward. It cannot introduce a finding.
5. **`adapter/`** can be written any time after the CLI is understood, but it
   must be **validated against the real binary** before the capsule is claimed
   done — see the verification loop below.
6. **`build/`** prefers a checksum-pinned official binary, archive, or package;
   build from source only when the selected release has no matching
   distribution. Follow the exact payload and registration contract in
   [`tool-structure.md`](tool-structure.md) § Executable integration and builds.

The ordering has one hard consequence worth stating plainly: **if a claim cannot
be written, the sentence cannot be written either.** That is the mechanism by
which this repository's evidence rules actually bind, rather than being a
preamble everyone agrees with and then ignores.

## Deriving with agents

The [research brief](tool-research-brief.md) describes one researcher taking a
tool through every stage. That works for a small subject. For a large one —
a multi-module codebase where a single context cannot hold the engine, the
store layer, the CLI and the output paths at once — the shape that worked was:

- **A pathfinder first.** One agent maps the subject: module layout, where
  requests are issued, the CLI surface, the build story. It produces a map, not
  judgements, and it proposes how to split the deep read. Dispatching a fan-out
  before this produces overlapping, uneven coverage.
- **Readers over disjoint file sets.** Give each reader an explicit *exclusive*
  area and name the files it does **not** own, including the awkward cases where
  a file sits in one reader's directory but belongs to another's concern. Ambiguity
  here produces both double-coverage and gaps.
- **An integrator.** Consolidation is a judgement task, not concatenation: its
  value is adjudicating where readers disagree or overlap, and it should be asked
  to state those adjudications explicitly.
- **A different-model reviewer** over the result, per
  [`../../AGENTS.md`](../../AGENTS.md) — writer must not be gater.

Two failure modes, both observed:

- **A researcher who has read the existing capsule cannot un-read it.** This
  applies to the orchestrator too. If you have already read the tool's pages,
  you are contaminated and must dispatch rather than derive. Say so rather than
  quietly proceeding.
- **Agents may be unable to write files.** A harness guardrail can refuse a
  subagent's file writes, and findings then exist only in a transcript. Instruct
  every agent to persist its output to a staged path *as it works*, and treat a
  report that exists only in a returned message as unsaved.

## Evidence rules, applied

The vocabulary is defined in [`../methodology.md`](../methodology.md). What it
means while authoring:

- **Historical `confirmed` claims stay receipt-bound.** Current capsule ledgers
  use `confirmed` for observations backed by committed historical
  `receipt.md`/`run.meta` records, and those records remain the evidence a
  reader can audit. The validator checks local existence; Git tracking is
  enforced separately below. A new minimal `result.json` attempt is diagnostic
  development output until the benchmark methodology/security amendment and a
  correctness path land. Do not promote a claim to `confirmed` from such an
  attempt, and do not invent a replacement evidentiary path in a capsule.
- **Never initiate subject execution outside a documented execution profile.**
  Use the cooperative GCP Batch profile or the strict local Docker profile in
  [`runner-security.md`](runner-security.md). Failure to provision the selected profile is a stop condition, not an
  alternate observation path. If pre-existing output from an external or
  earlier out-of-boundary execution must be preserved, label it `[OBS <how>]`
  in prose and `kind: "observation"` in the ledger, state exactly how it was
  produced, and keep it explicitly non-receipt. Such an observation may retain
  provenance and support only a proposition bounded to what its committed
  commands and artifacts make auditable. It cannot make a claim `confirmed` or
  substitute for receipt-bound historical verification or the future benchmark
  evidentiary path. `receipts/` is scoped to run records **and** preserved
  observations; the capsule must never let one read as the other.
- **Scale-dependent behaviour is not settleable at smoke scale.** Throughput,
  memory cliffs, OOM behaviour, high-concurrency behaviour and crash-resume stay
  `unverified` with a recorded reason, however suggestive a single run looked.
- **A verifier verdict is separate from a clean exit, and drift is not the
  absence of a verdict.** If the manifest is absent the verifier cannot run at
  all, so there is no verdict and completeness rests on whatever weaker check
  you actually ran — say which. Drift is the opposite case: `DRIFT` *is* a
  verdict, and it means the bucket moved, so stop and re-baseline rather than
  recording anything about the tool.
- **Count-and-uniqueness is not completeness.** Matching a recorded key count
  with no duplicates leaves a substituted key, and a missing key compensated by
  an extra one, both undetectable. If that is all you have, say that is all you
  have — and do not name the claim after completeness.

## The verification loop

Run all of it before calling a capsule done. The validator alone is not
sufficient — it checks structure, not truthfulness.

```sh
# structure, links, secrets
uv run s3-listing-study validate-capsule --tool <slug>
uv run s3-listing-study check-links
uv run s3-listing-study receipt scan-tree .

# every source anchor resolves at the commit the evidence cites
uv run s3-listing-study check-source-anchors --tool <slug> --require-checked \
        --source-root <repository-1>=<checkout-1> \
        --source-root <repository-2>=<checkout-2>
uv run s3-listing-study check-source-anchors --tool <slug> --markdown tools/<slug>/research/ \
        --require-checked --source-root <repository-1>=<checkout-1> \
        --source-root <repository-2>=<checkout-2>

Any skipped anchors mean the verification is incomplete.

# Python command and normalization adapters match their shared contracts
uv run pytest -q tests/test_command_adapters.py tests/test_adapters.py

# Final-image integration uses the already-published common base by digest
uv run s3-listing-study build-tool-image --tool <slug> \
  --shared-base-image REGISTRY/...@sha256:<digest> \
  --tag study/tool-<slug>:candidate
# Resolve that tool image by digest, then add the worker layer.
uv run s3-listing-study build-derived-image --tool <slug> \
  --tool-image REGISTRY/...@sha256:<digest>
```

Then the checks no script performs:

- **No current claim is `confirmed`** unless its historical receipt genuinely
  exists. Counting the word is not the check — parse the ledger, and for every
  `confirmed` claim assert it carries `kind: "run"` evidence whose `receipt`
  path exists on disk:

  ```sh
  python3 - <<'EOF'
  import json, os, subprocess
  d = json.load(open('tools/<slug>/data/claims.json'))
  for c in d['claims']:
      if c['status'] != 'confirmed':
          continue
      runs = [e for e in c['evidence'] if e['kind'] == 'run']
      assert runs, f"{c['id']}: confirmed with no run evidence"
      for r in runs:
          p = os.path.normpath(os.path.join('tools/<slug>/data', r['receipt']))
          assert os.path.exists(p), f"{c['id']}: receipt missing at {p}"
          subprocess.run(["git", "ls-files", "--error-unmatch", "--", p], check=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
  print('confirmed claims all cite an existing receipt')
  EOF
  ```
- **No dangling claim IDs.** Every backticked ID in README and docs resolves in
  the ledger. A dangling ID is a defect, and it is invisible to the validator.
- **Every evidence `artifact`/`receipt` path exists** on disk.
- **The adapter round-trips real output.** Execute each mode's argv against the
  real subject, pipe it through `normalize.py`, and assert the field count. A
  normalizer that has only seen synthetic fixtures has not been tested.
- **Cross-mode agreement, where the tool offers more than one output path.**
  If several modes should enumerate the same thing, check they produce the same
  key set. It is cheap, and it catches adapter and engine faults together —
  though it is *not* a completeness check: every arm can agree and be wrong the
  same way.

## Split the independent review by concern

Split independent review by concern. A capsule change is too big for one review.

Split by **concern, not by file count**. Each slice gets its own diff, its own
focus list, and a scope fence telling it that sibling reviews cover the rest:

| Slice | Reviews | Asks |
| --- | --- | --- |
| Evidence | `data/` | Does any claim's status or wording outrun its evidence? |
| Prose | `README.md`, `docs/` | Is every statement backed by a claim, and does it obey the content contracts? |
| Machinery | `schemas/`, `src/s3_listing_study/`, CI | Did a check that used to fire stop firing? Can a gate report success having verified nothing? |
| Adapter | `adapter/` | Would this argv fail against the real binary? Can the normalizer emit or drop a row it should refuse? |
| Rules | operating docs | Does new guidance contradict existing law, or license something it should bound? |

Three things make the split work:

- **Give every slice the same context block** — what evidence exists, what does
  not, and what is deliberately out of scope. Without it each reviewer
  re-discovers "there are no receipts" and spends its budget there.
- **Demand a structured verdict and validate it yourself.** Ask for
  `{verdict, blockers[], withinBudget, coverage}` in the prompt and validate the
  returned JSON against the schema locally — generate loosely, validate
  strictly. A reviewer that ran out of budget must return `PARTIAL`, never
  `READY`.
- **Fix in the same slices.** The findings arrive already partitioned, so
  parallel fixers inherit the partition. One sequencing rule: a slice that
  renames claim IDs must land before the slice that cites them, or the two
  collide.

The failure this avoids is subtler than a timeout. A single reviewer that *does*
finish a huge diff samples it — and sampling by importance systematically misses
defects that cluster in unglamorous places. Both criticals here came from slices
a whole-diff reviewer would have skimmed.

## Traps that cost real time

- **Adapters rot silently across a version bump.** Flags disappear and the
  runner fails at argument parsing, not at listing. Diff the adapter's flags
  against the new `--help` (see [`tool-onboarding.md`](tool-onboarding.md)
  § Re-deriving).
- **The command adapter returns complete subject argv.** Element zero is the
  explicit absolute path installed by the final stage of `build/Dockerfile`;
  follow it with every required subcommand or launcher token. Upstream image
  entrypoints are not part of the production runtime, so no prefix may remain
  implicit in image packaging.
- **Freeze the subject in a detached worktree** before dispatching readers.
  A moving upstream shifts line numbers underneath work in progress.
- **Anchor line numbers are not self-checking.** They were wrong 22 times in one
  derivation, and a priority-sampling reviewer found none of them because they
  clustered in unglamorous files. Run the anchor checker; do not delegate this
  to judgement.
- **Distribution tags may not match the git tag.** Check every official channel
  rather than assuming (`v0.2.0` the git tag, `0.2.0` the container tag, in one
  case), then pin the selected artifact by content digest.
- **Reviews are invalidated by a moving tree.** Do not restructure files while a
  long review runs against them; the review will report on paths that no longer
  exist, and its result must be discarded.
