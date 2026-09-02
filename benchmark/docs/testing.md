# What a test is for in this harness

The harness is scripting code that drives cloud APIs and parses other people's
output. Write the tests that class of code can actually fail, and no others — a
test that cannot fail is worse than no test, because it makes the untested thing
look verified.

**A test earns its place if it is one of these:**

- **A refusal that protects evidence.** Retry declining a settled outcome,
  adoption declining a job that does not match intent, a plan declining two rows
  that resolve to one case, verify declining an incomplete comparison. These
  encode decisions that are expensive to rediscover and invisible in the code.
- **Identity determinism.** Case IDs, fingerprints, submission and job
  identifiers. A silent identity change files two different measurements in one
  place, which is unrecoverable after the fact.
- **A parser against real input shapes.** The TAB framing contract, the eleven
  capsule normalizers, credential payloads. A wrong answer here corrupts a
  result quietly instead of failing.
- **A drift guard on a committed artifact.** The shipped plans still load, the
  declared modes still exist in the adapters, the pinned roster is still
  eleven. These fail when a real file changes, which is the point.

**Delete on sight:**

- Assertions that a rendered structure contains a key the same test just put
  there.
- A test whose "provider" or "subject" is a fake echoing the request back.
  Both regressions found in the first real campaign — a credential contract no
  estate could satisfy, and an adoption check that failed on every create —
  passed 698 such tests, because the fakes could not disagree with the code.
- One test per validator message. Parametrize the cases, or trust the one that
  proves the validator refuses.
- Restating a constant the code already states, mirroring an implementation
  line for line, or repeating one failure mode across shapes that share a code
  path.

**Unit tests do not cover the provider contract, and no amount of them will.**
The substitute is a `--dry-run` render plus one real job — both regressions above
surface in the first `create_job` within seconds. Prefer that over another fake.

Test volume is not a goal. When a change makes a test churn, ask first whether
the test was ever able to fail for a reason a reader would care about.
