# s4cmd — reconciliation with the inherited dossier

Stage D. Walks **every inherited claim** in `tools/s4cmd/README.md` against my
independent research (`research/report.md`) and committed receipts. Verdicts:
**Corroborated** / **Contradicted** / **Unaddressed** / **Settled by smoke run**.

Scope note carried throughout: s4cmd has **no unsigned/anonymous access path**
and this campaign is `CREDS=none`, so **no listing mode could be executed**. That
bars any receipt-backed promotion of a *mechanism* claim — mechanism verdicts
below rest on `[SRC]` at commit `80059bfa4451f513a8f314fb6300e5ecc51587b2` and
stay `VERIFIED: no` in the dossier (source reading never promotes past it). The
one thing a committed receipt settles is the capability block itself.

Pinned subject: s4cmd **2.1.0**, commit `80059bf`, image
`sha256:d458ef5096180e517840712e29b0b8705ec97cebf48f717cad2fea3805105813`.

## Metadata claims

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| M1 | Repo `github.com/bloomreach/s4cmd` | **Corroborated** | `[DOC README]` `[SRC @ 80059bf]` — canonical, not a fork |
| M2 | Language: Python | **Corroborated** | `[SRC s4cmd.py @ 80059bf]` single-file Python |
| M3 | License: Apache-2.0 | **Corroborated** | `[SRC LICENSE @ 80059bf]` |
| M4 | Version reviewed: unknown | **Corrected (editorial)** | Reviewed **2.1.0** (latest release tag), commit `80059bf` `[SRC git tag]` |
| M5 | Tier 2 (study-design) | **Unaddressed** | Not a behavioral claim; study-design decision, not mine to verify |
| M6 | Testability: "Trivial — `pip install s4cmd`" | **Corroborated** | `pip install s4cmd` installs 2.1.0 and it **imports and runs** — verified under botocore 1.33.13 (Py 3.7) and the latest 1.43.50 (Py 3.12): `s4cmd --version` → exit 0. `[RUN receipts/smoke/_build/modern-boto3-import/{transcript,transcript-py312}.txt]`. **Self-correction:** an earlier draft marked this Contradicted, believing `botocore.vendored.requests` (s4cmd.py:274, "removed 2019") broke import; that was an **untested assumption and is false** — the attribute path still resolves. The image pins old boto3 for reproducibility, not necessity. |

## Mechanism claims (the dossier's core) — all remain `VERIFIED: no`

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| C1 | "Super S3 CLI, alternative to s3cmd, for large-file / data-intensive scripted workflows" | **Corroborated** | `[DOC README.md]` — verbatim positioning |
| C2 | "Threadpool **across distinct CLI-supplied prefixes**" | **Contradicted (refined)** | Parallelism is real but its unit is the **pseudo-directory discovered by delimiter recursion**, not CLI-supplied prefixes. `ThreadUtil.s3walk` always sends `Delimiter='/'` and re-queues each discovered `CommonPrefix` as a new pool task. `[SRC s4cmd.py:1176,1184-1185 @ 80059bf]` |
| C3 | "**Serial within any single prefix** — no keyspace discovery or sharding inside one prefix" | **Contradicted** | A single CLI prefix **with `/`-delimited substructure parallelizes automatically** — each sub-directory is a separate thread task. Positive source evidence: the recursion at `[SRC s4cmd.py:1184-1185 @ 80059bf]`. Residual truth: a **delimiter-free flat** prefix (no interior `/`) does collapse to one serial paginated scan on one thread — so the claim holds only for that degenerate shape, not "any single prefix." |
| C4 | Parallelism "only if the caller supplies multiple distinct prefixes … effectively the same manual-sharding burden as s5cmd's `run` fan-out" | **Contradicted** | s4cmd auto-discovers the keyspace by delimiter recursion; **no** caller sharding is needed. Moreover `ls` accepts **exactly one** path argument (`args[1]`, guarded by `validate('cmd|s3')`), so "multiple distinct prefixes on one invocation" is not even a supported invocation. `[SRC s4cmd.py:1625-1632 @ 80059bf]` `[OBS receipts/smoke/_capability/obs-multiprefix.stderr.txt: "[Invalid Argument] Invalid number of parameters", exit 1]` |

## Modes / tunables to exercise

| # | Inherited item | Verdict | Evidence |
| --- | --- | --- | --- |
| T1 | `ls s3://bucket/prefix` (single prefix) = "believed-serial baseline" | **Contradicted (premise)** | Not serial when the prefix has substructure (see C3). It is a real listing mode (my *recursive* / *shallow* modes) but the "serial baseline" framing is wrong. `[SRC @ 80059bf]` |
| T2 | `ls` with "multiple distinct prefixes on one invocation" = "threadpool-across-prefixes mode … closest thing to a fair best mode" | **Contradicted (does not exist)** | `ls` rejects >1 path. The believed best-mode is a misconception. `[SRC s4cmd.py:1625-1632]` `[OBS obs-multiprefix]` |
| T3 | Thread-count flag "if present in the installed version" — sweep | **Corroborated (present)** | `-c/--num-threads` exists; default `cpu_count*4`; `S4CMD_NUM_THREADS` env alias. Flagged for the benchmark sweep. `[SRC s4cmd.py:121,1859 @ 80059bf]` `[RUN _build/build.md --help]` |

## Claimed strengths

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| S1 | "Genuine multi-prefix parallelism out of the box … just multiple prefix arguments" | **Contradicted (misattributed)** | Parallelism out of the box: **yes**. Mechanism "multiple prefix arguments": **no** — it comes from delimiter recursion, and `ls` takes one path. `[SRC @ 80059bf]` `[OBS obs-multiprefix]` |
| S2 | "Positioned specifically for large-file / data-intensive scripted use" | **Corroborated** | `[DOC README.md]` |

## Claimed weaknesses (hypotheses)

| # | Inherited hypothesis | Verdict | Evidence |
| --- | --- | --- | --- |
| W1 | "Serial within a single prefix — no keyspace discovery/sharding inside one prefix" | **Contradicted** | Same as C3: delimiter recursion is in-prefix keyspace discovery. `[SRC s4cmd.py:1184-1185 @ 80059bf]` |
| W2 | "Parallelism only across caller-supplied prefixes = manual sharding, like s5cmd `run`" | **Contradicted** | Same as C4. `[SRC]` `[OBS]` |
| W3 | "Maintenance status unconfirmed — verify release cadence & current-S3 compatibility" | **Corroborated (concern confirmed)** | Dormant: latest **release** tag 2.1.0 is 2018-08-14; default branch only +14 commits (newest 2024-07-21), mostly dependabot; won't import under current boto3. `[SRC git log @ 80059bf]` `[RUN _build/build.md]`. (Not a *smoke* receipt for listing behavior — a source/build fact.) |
| WV | "What to verify first: is threadpool-across-prefixes real; compare best-case to s5cmd `run`" | **Contradicted (premise) + deferred (benchmark)** | The premise (threadpool-across-prefixes) is false (C2/C4). The real benchmark question becomes: how does **delimiter-recursion** parallelism scale with `-c` and tree shape, and its LIST-request amplification vs a flat scan. Needs credentials + scale — deferred to the benchmark phase (report §10). |

## New firsthand finding not present in the dossier

| # | Finding | Status | Evidence |
| --- | --- | --- | --- |
| N1 | s4cmd has **no unsigned/anonymous access** (no `--no-sign-request` equivalent; boto3 client built without `signature_version=UNSIGNED`). Under credential starvation it fails before listing. | **Settled by smoke run** (capability, `recursive`) | `[SRC s4cmd.py:380-386 @ 80059bf]` `[RUN receipts/smoke/_capability/anon-nocredentials/receipt.md — auth=anonymous, exit 1, fails at BotoClient.__init__ before any request]` `[3P github.com/bloomreach/s4cmd/issues/139]`. Scope: 2.1.0, this image, credential-starved, `recursive` mode. The other modes (shallow/show-directory/du) share the same `BotoClient` constructor path (s4cmd.py:1557,1563,674-688) — so they are blocked by **`[INFERRED]` source extension**, not four independent receipts. Consequence: **every listing mode is blocked, not skipped**, under `CREDS=none`. |

## Verdict counts

- Corroborated: **8** — M1, M2, M3, M6, C1, S2, T3, and W3 (concern confirmed).
- Contradicted: **9 distinct** — C2, C3, C4, T1, T2, S1, W1, W2, WV. (S1/W1/W2
  restate the C-family; counted once each here.)
- Unaddressed: 1 (M5, study-design tier).
- Settled by smoke run: 1 (N1, capability block — `recursive`; other modes `[INFERRED]`).
- Corrected (editorial, non-promoting): M4 (version).

## Dossier edits made (conservative)

Per the brief, the dossier stays the hypothesis sheet; I changed only:
1. **Editorial**: `Version reviewed: unknown → 2.1.0` `[SRC]`. (`Testability`
   stays "Trivial" — **Corroborated** by the modern-boto3 run; an earlier
   over-correction was reverted.)
2. **Receipts** section: added the capability + build receipts (was "None yet").
3. **Status banner + Provenance**: updated to mixed lineage — an independent
   report and this reconciliation now stand beside the page; the capability
   finding is firsthand + receipt. The pre-existing **Mixed provenance** callout
   (Language/License read firsthand) is **preserved**.
No mechanism/modes/strengths/weaknesses *behavioral* text was rewritten — those
stay `VERIFIED: no` and are reconciled here, not promoted.

## Claims about other tools / S3, routed to the orchestrator

- **s5cmd**: the dossier framed s4cmd's parallelism as "comparable in spirit and
  burden to s5cmd's `run` fan-out." That analogy is **wrong for s4cmd** (C4) and
  makes an implicit claim about s5cmd's `run` being manual sharding — accurate
  for s5cmd but not the right comparison. Do not edit s5cmd's page on my say-so;
  flagged for routing.
- **`docs/open-questions.md` §2 (language bottleneck)**: s4cmd is correctly
  listed under Python. Supporting context from my read: s4cmd does real
  client-side work per listing (full in-memory accumulate + sort in
  `pretty_print`, `[SRC s4cmd.py:722-741,1592]`), so it is a good witness for the
  Python-client-CPU hypothesis at scale. Context only — `VERIFIED: no` stands.
- **`docs/open-questions.md` §3 (crash-resume)**: s4cmd has **no** listing
  resume/checkpoint `[INFERRED — none in the listing path @ 80059bf]`, consistent
  with "nobody else has it." Context only.
