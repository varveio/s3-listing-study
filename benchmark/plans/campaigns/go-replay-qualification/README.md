# Go replay qualification

Bounded diagnostic qualification of the replay instrument. Varve maintains this
study and builds Swath; Swath here is the Java 0.3.2 listing subject.

Each Java/Go pair takes its subject settings, fixture, deadline profile and
warm-up from the release attempt named at the top of its plan. The Java image
is that attempt's exact digest. Use one rebuilt toolbox image on both sides.
All jobs use Spot provisioning with the study VPC and subnet. Retain failures;
only a preempted job may be resubmitted once. No ranking or calibrated benchmark
claim follows from these diagnostic plans.

The blockchain Go ladder uses replay 64 vCPUs / 20 GiB (two pair repetitions),
32/10, 16/5 and 8/4 (one each), all on n4-highcpu-80 with subject8/8. The 64/20
arm is the control for the smaller allocations. Keep the reader-decode ceiling
128 across rungs: it bounds outstanding reads, not active CPU threads; the
8-vCPU rung can test whether that ceiling causes pressure. All other Go
settings are fixed across rungs except GOMAXPROCS and the memory-derived
GOMEMLIMIT. In particular: 4,096 request slots, a 1 GiB response budget, cache
on with 192 windows of 12,500 rows. Warm-up is unchanged from each source cell;
there is no extra warm-up contrast or default-settings sweep.

Before inspecting fresh results, the agreement band is ±10%: each smaller
blockchain arm's subject wall versus the median of the two 64/20 Go controls;
the s5cmd Go median versus the Java median. Retain and report individual
repetitions and their range. A single ladder repetition supports a bounded
diagnostic recommendation, not a precision claim about run-to-run variation.

Use the existing per-attempt delivered-treatment grader without weakening its
requirements. Require completed row counts matching the fixture, TIMING_VALID,
zero server errors/refusals and the agreement band for a recommended ladder
rung. Headroom additionally requires mean replay cpuset utilization below 60%,
p90 below 80%, server lifetime peak RSS below 75% of its container ceiling, and
response-buffer peak below 75% of the budget. Report CPU maxima, parquet-reader
peak versus limit, request occupancy samples, and cache hit/fill deltas even
when the headroom conditions fail. Sampled occupancy is not a true peak.

Recommendations for smaller machine types are fit-based inferences from these
allocations and observed headroom on n4-highcpu-80, not measurements on smaller
VMs. NARA and FourCast have no allocation ladder here; report that limitation.
Runs too short for the grader's five 10-second resource samples retain their
INSUFFICIENT_EVIDENCE grade; do not stretch the workload or relax the gate.

There is one parallel wave of 21 jobs. At most one follow-up wave of six jobs is
allowed, only to fix a server knob demonstrated to fail in the first wave. The
total cloud budget is $10. Record a priced estimate before submission; poll at
least five minutes apart and stop any stuck job after 90 minutes.
