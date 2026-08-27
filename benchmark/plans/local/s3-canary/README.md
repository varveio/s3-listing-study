# Local real-S3 canary

This directory holds non-comparative local-Docker qualification plans. They
enter through the current toolbox worker and retain complete local evidence;
they do not replace or rewrite historical `tools/*/receipts/smoke/` evidence.

## RTOFS target selection

`noaa-nws-rtofs-pds` is a public `us-east-1` bucket with a medium listing size.
Fixture-selection reconnaissance on 2026-08-27 counted **845,304 objects** with
the toolbox's AWS CLI 2.36.1 in 127.73 seconds at 2 vCPU / 4 GiB. That bounded
count was target reconnaissance, not a benchmark or correctness receipt. The
plan's 1,800-second subject timeout is therefore more than fourteen times that
reference duration while still bounding a pathological capsule.

The bucket publishes new dated prefixes and is mutable. The eleven-tool pass is
one canary attempt per tool, serialized and labelled `purpose: canary`; its
durations cannot estimate variance or rank tools. Before execution, record a
fresh independent object count and timestamp. Record the same immediately after
the group. If the counts differ, content disagreement is corpus drift until a
more specific investigation proves otherwise. Repeated consistency and
resource-response work uses an immutable replay capture, not this live target.

At the S3 page limit, eleven ordinary full scans imply roughly 9,300 LIST pages
at the observed count, before any tool-specific probes, overlap, or retries.
The exact request total remains run evidence; the timeout is the hard operational
bound when a subject behaves unexpectedly.
