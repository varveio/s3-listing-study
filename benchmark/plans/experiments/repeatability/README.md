# Repeatability experiment plans

These plans describe the experiment, not where it runs. Select Docker or GCP
Batch with `campaign.py submit --executor ...`; do not fork the plan by
executor.

`noaa-nws-rtofs-pds.yaml` is the one-shot real-S3 invocation canary. A bounded
AWS CLI reconnaissance on 2026-08-27 observed 845,304 keys in 127.73 seconds
from `us-east1-b`, but the public bucket changes daily. Record independent
boundary manifests before, between, and after the eleven attempts and do not
interpret the single timings as comparative results. The 1,800-second subject
timeout is over fourteen times that reconnaissance wall.

The plan deliberately includes retired `s4cmd` for this one real-S3 canary.
The executor requires an explicit exception flag and refuses that exception for
replay or more than one attempt.

Execution of the full permutation is additionally gated on the reference
manifest hook described in the methodology proposal. Until it exists, the
Docker path permits a complete dry render and one selected provider canary, but
refuses the eleven-tool run so mutable-bucket drift cannot be misreported as a
tool correctness difference.
