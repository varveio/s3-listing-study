# TwinStamp (provisional)

TwinStamp is a small, dependency-free Python core for reconciling immutable
evidence units stored beneath an object-store prefix.  It discovers immediate
children, keeps recognized and anomalous evidence visible, lets the caller
validate each canonical leaf, and applies a strict exact-one rule to choose—or
decline to choose—a result.

It is not a scheduler, production object-store adapter, domain-schema
implementation, final-publication system, or a verdict engine. In particular,
a valid seal means that the caller validated a complete marker-last evidence
unit.  It does **not** mean the worker succeeded, the result is correct, the
payload is trustworthy, or the marker is a cryptographic signature.

The name, Python API, and storage convention are provisional and not frozen.

The package root exposes the common reconciliation path. Publication and
provider coordination live in `twinstamp.publication` and
`twinstamp.coordination` so their specialist types do not inflate that surface.
Import `EvidenceProfile` from `twinstamp.profiles`; `SlotResolution`,
`Selection`, `CanonicalEvidenceUnit`, and `UnrecognizedEvidenceUnit` from
`twinstamp.resolution`; `ChildLimitExceeded` from `twinstamp.discovery`; and
`ObjectStoreReader` from `twinstamp.stores`. Test clients can use
`twinstamp.testing.MemoryObjectStore`.

## Model and identities

Callers own the first two coordinates passed to `reconcile()`: an object-store
prefix for one requested answer and the `EvidenceProfile` accepted beneath it.

- `Submission(key)` is one deliberate provider-job generation of that slot.  A
  curated retry gets a new immutable submission; provider-native retries belong
  inside the submission.
- An evidence unit is the immediate child below the slot prefix.  Its concrete
  identity is fixed by the selected profile.

`reconcile()` reads each recognized child's configured marker once and returns
a `SlotResolution` that preserves leaves and groups
them by submission. Its `Selection` is a separate outcome. Absence,
seal validity, duplicate ambiguity, and selection are deliberately not folded
into one status field.

## Physical execution identity

`PHYSICAL_EXECUTION` uses a canonical UUIDv4 key for each physical invocation.
Every invocation gets a disjoint leaf, so multiple launches for one submission
remain separately inspectable.  This is conservative: it makes physical
duplicates visible, but discovery is required because the ID is not predictable.

```python
import uuid

from twinstamp import PHYSICAL_EXECUTION, PhysicalExecutionUnit

unit = PhysicalExecutionUnit(uuid.uuid4())
key = PHYSICAL_EXECUTION.render(unit)  # e.g. "e119..." (canonical UUIDv4)
assert PHYSICAL_EXECUTION.parse(key) == unit
```

The S3 listing study uses this profile. That is a conservative study
policy, not a claim that a specific backend normally launches duplicates.

## Immutable evidence and seals

The intended convention is immutable, create-only publication: write artifacts
in canonical order and write the marker last. `CanonicalJsonMarker` performs a
single bounded read and rejects invalid UTF-8/JSON, duplicate keys, non-object
documents, and noncanonical bytes before domain validation. Missing markers are
reported separately from malformed ones. `Seal(marker_key)` is created only
after the caller's validator has checked the parsed document's domain schema.
TwinStamp does not interpret domain fields. Its optional Python publication
helper applies the same profile binding and marker-last/create-only state
machine to caller-supplied payload streams and storage operations.

Create-only calls alone do not make a namespace immutable if credentials can
delete and recreate objects.  Adapters and deployment policy must provide the
real authority boundary.  Similarly, reconciliation is a snapshot, not proof
that a late leaf cannot appear; final publication needs a separate writer fence
and a fresh scan, neither of which is implemented here.

## State axes and selection

Each leaf has an independent `SealState`:

- `UNSEALED`: incomplete or no seal witness;
- `VALID`: a complete marker-last witness was validated and a `Seal` is present;
- `INVALID`: malformed or failed validation evidence.

A domain `LeafAssessment` can carry opaque `evidence` and `execution_outcome`;
TwinStamp never interprets them. Before domain validation,
the core itself constructs typed assessments for unrecognized units, missing
markers, and invalid markers. An `UnrecognizedEvidenceUnit` therefore remains
visible without invoking caller code or reading its marker.

`SelectionState` is separate. It can be `PENDING`, `MISSING`, `SELECTED`,
`DUPLICATE`, `INVALID`, or `UNSEALED`. `PENDING` skips storage discovery until
the provider effect is settled. Selection is deliberately strict: any two
current children—valid, invalid, or unsealed—produce `DUPLICATE`. Historical
leaves remain visible but are not candidates.

## Discovery and reconciliation

Discovery reads only unique *immediate child prefixes* below the supplied prefix.
Direct objects at the prefix are not units. Children are sorted, and
unrecognized keys are retained as anomalies. More than `max_children` retained
children raises `ChildLimitExceeded` instead of returning a truncated answer.
The bound counts every retained immediate child, including unrecognized keys,
not the underlying paginated listing work.

The caller supplies the storage adapter, marker convention, and leaf validator.
The validator is invoked only with a `CanonicalEvidenceUnit`:
a recognized identity plus one present, canonical, bounded marker document.
Missing, malformed, oversized, or unrecognized evidence never reaches domain code
and cannot become valid or historical. A fully validated assessment may
attribute itself to an earlier submission; TwinStamp retains it as history and
excludes it from current selection.

```python
from twinstamp import (
    CanonicalJsonMarker,
    LeafAssessment,
    MarkerState,
    PHYSICAL_EXECUTION,
    Submission,
    reconcile,
)
from twinstamp.testing import MemoryObjectStore

store = MemoryObjectStore(
    {
        "answers/work/run-1/11111111-1111-4111-8111-111111111111/result.json": b"{}\n",
    }
)


def validate(candidate):
    assert candidate.marker.state is MarkerState.PRESENT
    return LeafAssessment.valid(
        evidence=candidate.marker.document,
        marker_key=candidate.marker.key,
    )


resolution = reconcile(
    store,
    "answers/work/run-1",
    PHYSICAL_EXECUTION,
    Submission("submission-1"),
    CanonicalJsonMarker("result.json", max_bytes=1_000_000),
    validate,
)
assert resolution.selection.selected_key == "11111111-1111-4111-8111-111111111111"
```

An adapter implements `ObjectStoreReader` with `iter_child_prefixes(prefix)`
and bounded `read_object(key, max_bytes=...)`.  Its consistency, version, and
visibility semantics remain adapter responsibilities.  The included
`MemoryObjectStore` is a dependency-free test double, not a production adapter.

## Language-neutral publication convention

`EvidencePublication` binds a caller-resolved destination prefix to a fixed
ordered artifact manifest and one marker. Campaign callers can instead use
`EvidencePublication.for_unit()` to bind and validate a typed unit against an
`EvidenceProfile`. `publish()` validates every
canonical contained relative name and streams every payload through its
declared size and SHA-256 before the first create. It then conditionally creates
artifacts in the caller's exact order and the marker last.

Create results are distinct storage facts: `ObjectCreated` carries the observed
version, `ObjectConflict` refuses the unit, and `ObjectCreateAmbiguous` requires
an exact bounded size/SHA-256 read-back with an observed version before the next
create. A missing, unreadable, versionless, or mismatching read-back refuses the
unit and never reaches its marker. Successful, unambiguous large uploads are not
read back.

The Python helper is optional. `golden/publication-v1.json` is a portable
fixture for the byte/path/order/state convention; its executable meaning is
currently defined by the Python conformance test, not yet proven by independent
implementations.

The vector's `action` selects publication, a stopped/torn write, an injected
conflict or ambiguity, or reader-only validation. `at`, `stop_after`, and
`read_back` identify the affected object. When present, `objects` and `outcome`
state the expected visible tree and seal result. `marker_issue` names a core
marker parse failure. `corrupt_artifact` expects invalid domain evidence because
the test's conformance validator checks artifact bodies against the marker
manifest; TwinStamp's marker reader itself does not read artifacts or define
that schema.

## Submission coordination

TwinStamp provides a small function-first coordination seam for provider jobs
and durable intent. Callers build a `SubmissionSpec` from exact immutable
canonical bytes plus their SHA-256 digest, then supply typed ensure/observe
callables and an `IntentJournal`.

`ensure_submission()` owns the normal reserve/claim → provider ensure/adopt →
durable record path. `ensure_claim()` completes a claim reserved by a
caller-specific retry path. `observe_submissions()` owns one observation pass
and asks the journal to settle only facts it can safely project. Ensure facts
(`Created`, `AdoptedExact`, `RejectedNoEffect`, `Collision`, `Ambiguous`) are
separate from observation facts (`ObservedExact`, `ObservedCollision`,
`NotVisible`, `ObservationAmbiguous`). Every fact carries structural
provider-effect and settlement claims; `NotVisible` and ambiguity are always
unsettled.

`AdoptedExact` is deliberately only a fact.  A caller may treat unexpected exact
adoption as a collision while treating redrive of already-durable intent as
clean, as the S3 listing study does through its SQLite journal adapter.

## Study adapter status

The S3 listing study is the first client. Its report paths, marker bytes,
validator, history derivation, finality behavior, GCP Batch normalization,
request rendering, SQLite schema, canonical `job_json` encoding, and
exact-adoption policy remain study-owned. The study chooses the physical
execution profile and strict duplicate policy; TwinStamp supplies those
mechanisms along with generic evidence, reconciliation, typed provider facts,
and intent/job orchestration seams. It has no imports of the study, cloud SDKs,
benchmarks, or subject tools.

## Current limitations

Today the package does not provide:

- a standalone versioned portable specification or generic domain marker schema;
- a production mutation adapter or portable multipart/resumable transport;
- cryptographic signing, authentication, or a correctness/domain-verdict
  policy;
- a CLI, a packaged non-Python writer kit, or a second production client or
  backend.
