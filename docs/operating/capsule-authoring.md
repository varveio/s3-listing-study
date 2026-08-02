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

- **No receipt means nothing is `confirmed`.** The schema enforces it —
  `confirmed` requires `kind: "run"` evidence, and a run evidence entry must
  point at a committed receipt. If the runner-security profile was not
  provisioned, `harness/smoke-run.sh` was not used and no receipt exists, full
  stop.
- **A real run that the wrapper could not record is still evidence.** Label it
  `[OBS <how>]` in prose and `kind: "observation"` in the ledger, put the
  artifacts under `receipts/`, state exactly what blocked recording, and qualify
  the claim to the single run that produced it. `receipts/` is scoped to "run
  records **and** observations", so observations belong there — but the capsule
  must never let one read as the other.
- **Scale-dependent behaviour is not settleable at smoke scale.** Throughput,
  memory cliffs, OOM behaviour, high-concurrency behaviour and crash-resume stay
  `unverified` with a recorded reason, however suggestive a single run looked.
- **A verifier verdict is separate from a clean exit.** If the manifest was
  absent or the bucket drifted, there is no verdict, and completeness rests on
  whatever weaker check you actually ran. Say which.

## The verification loop

Run all of it before calling a capsule done. The validator alone is not
sufficient — it checks structure, not truthfulness.

```sh
# structure, links, secrets
python3 scripts/validate-tool-capsule.py --tool <slug>
python3 scripts/check-links.py
harness/scan-tree.sh .

# every source anchor resolves at the commit the evidence cites
python3 scripts/check-source-anchors.py --tool <slug> --source-root <checkout>
python3 scripts/check-source-anchors.py --tool <slug> --markdown tools/<slug>/research/ \
        --source-root <checkout>

# adapter is executable and matches the harness contract
shellcheck -S warning tools/<slug>/adapter/*.sh
```

Then the checks no script performs:

- **No claim is `confirmed`** unless a receipt genuinely exists:
  `grep -c '"confirmed"' tools/<slug>/data/claims.json`.
- **No dangling claim IDs.** Every backticked ID in README and docs resolves in
  the ledger. A dangling ID is a defect, and it is invisible to the validator.
- **Every evidence `artifact`/`receipt` path exists** on disk.
- **The adapter round-trips real output.** Execute each mode's argv against the
  real subject, pipe it through `normalize.sh`, and assert the field count. A
  normalizer that has only seen synthetic fixtures has not been tested.
- **Cross-mode agreement, where the tool offers more than one output path.**
  If several modes should enumerate the same thing, check they produce the same
  key set. It is cheap, and it catches adapter and engine faults together —
  though it is *not* a completeness check: every arm can agree and be wrong the
  same way.

## Traps that cost real time

- **Adapters rot silently across a version bump.** Flags disappear and the
  runner fails at argument parsing, not at listing. Diff the adapter's flags
  against the new `--help` (see [`tool-onboarding.md`](tool-onboarding.md)
  § Re-deriving).
- **`run.sh` argv is appended to the image `ENTRYPOINT`.** Where the entrypoint
  is already the binary, correct argv starts at the subcommand. Check with
  `docker inspect -f '{{json .Config.Entrypoint}}'` before writing a mode.
- **Freeze the subject in a detached worktree** before dispatching readers.
  A moving upstream shifts line numbers underneath work in progress.
- **Anchor line numbers are not self-checking.** They were wrong 22 times in one
  derivation, and a priority-sampling reviewer found none of them because they
  clustered in unglamorous files. Run the anchor checker; do not delegate this
  to judgement.
- **A registry image tag may not match the git tag.** Check the registry rather
  than assuming (`v0.2.0` the git tag, `0.2.0` the image tag, in one case).
- **Reviews are invalidated by a moving tree.** Do not restructure files while a
  long review runs against them; the review will report on paths that no longer
  exist, and its result must be discarded.
