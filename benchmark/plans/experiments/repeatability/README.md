# Repeatability experiment plans

These plans describe the experiment, not where it runs; do not fork a plan by
executor. The bounded Docker runner accepts only independent, non-replay,
one-attempt real-S3 work. Repeated, replay, and dependent work runs on GCP Batch.

`noaa-nws-rtofs-pds.yaml` is the one-shot real-S3 invocation canary. A bounded
AWS CLI reconnaissance on 2026-08-27 observed 845,304 keys in 127.73 seconds
from `us-east1-b`, but the public bucket changes daily. Treat cross-tool
agreement as a factual canary result, not proof against an immutable snapshot,
and do not interpret the single timings as comparative results. The 1,800-second
subject timeout is over fourteen times that reconnaissance wall.

The plan deliberately includes retired `s4cmd` for this one real-S3 canary.
The Docker runner requires an explicit exception flag and refuses that
exception for replay or more than one attempt.

Run the complete permutation once, retain every native product, then invoke the
existing verifier with `--include-docker-canaries`. It compares every normalized
listing with the AWS CLI canary on keys and mutually exposed fields. Because the
public bucket may change during the serial pass, investigate a disagreement with
a fresh AWS listing before attributing it to a tool. Larger GCP benchmarks remain
row-count-only.
