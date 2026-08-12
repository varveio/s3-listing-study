# TwinStamp (provisional)

TwinStamp is a small, dependency-free Python core for reconciling immutable
evidence units stored beneath an object-store prefix.  It discovers immediate
children, keeps recognized and anomalous evidence visible, lets the caller
validate each leaf, and applies a caller-selected rule to choose—or decline to
choose—a result.

It is not a scheduler, object-store writer, domain-schema implementation,
provider adapter, final-publication system, or a verdict engine. In particular,
a valid seal means that the caller validated a complete marker-last evidence
unit.  It does **not** mean the worker succeeded, the result is correct, the
payload is trustworthy, or the marker is a cryptographic signature.

The name, Python API, and storage convention are provisional.  The package is
being extracted from the S3 listing study as a reusable evidence core; a second
client, a versioned convention, and broader adapters are needed before its API
is frozen.

## Model and identities

Callers own the first two coordinates:

- `ResultSlot(prefix, profile)` is one requested answer at an object-store
  prefix. A slot has exactly one
  `EvidenceProfile`; child keys under it must not mix profiles.
- `Submission(key)` is one deliberate provider-job generation of that slot.  A
  curated retry gets a new immutable submission; provider-native retries belong
  inside the submission.
- An evidence unit is the immediate child below the slot prefix.  Its concrete
  identity is fixed by the selected profile.

`reconcile()` reads each recognized child's configured marker once and returns
a `SlotResolution` that preserves leaves and groups
them by submission.  Its `Selection` is a separate policy outcome.  Absence,
seal validity, duplicate ambiguity, and selection are deliberately not folded
into one status field.

## Two non-equivalent assurance profiles

TwinStamp includes two mutually non-parseable key grammars.  Choosing one is an
assurance decision, not a formatting preference.

### Physical execution

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

The S3 listing study currently uses this profile.  That is a conservative study
policy, not a claim that a specific backend normally launches duplicates.

### Logical attempt

`LOGICAL_ATTEMPT` uses the backend's complete scheduler coordinate:
`logical-task-<task>-retry-<retry>-runnable-<runnable>`.  Normal retries with a
new retry coordinate are distinct units and directly addressable.

```python
from twinstamp import LOGICAL_ATTEMPT, LogicalAttemptUnit

unit = LogicalAttemptUnit(task_index=3, retry_index=1, runnable_index=0)
key = LOGICAL_ATTEMPT.render(unit)
assert key == "logical-task-3-retry-1-runnable-0"
assert LOGICAL_ATTEMPT.parse(key) == unit
```

This profile trusts the backend not to run multiple physical invocations under
the same complete coordinate.  It cannot preserve a same-coordinate duplicate
as a separate unit: a validator reports it as `PublicationConflict`, making
the leaf invalid.  Writers should create artifacts conditionally in fixed order,
refuse to seal after a conflict, and resolve ambiguous creates with a
version/generation and digest read-back.

## Immutable evidence and seals

The intended convention is immutable, create-only publication: write artifacts
in canonical order and write the marker last. `CanonicalJsonMarker` performs a
single bounded read and rejects invalid UTF-8/JSON, duplicate keys, non-object
documents, and noncanonical bytes before domain validation. Missing markers are
reported separately from malformed ones. `Seal(marker_key)` is created only
after the caller's validator has checked the parsed document's domain schema.
TwinStamp does not perform writes or interpret domain fields.

Create-only calls alone do not make a namespace immutable if credentials can
delete and recreate objects.  Adapters and deployment policy must provide the
real authority boundary.  Similarly, reconciliation is a snapshot, not proof
that a late leaf cannot appear; final publication needs a separate writer fence
and a fresh scan, neither of which is implemented here.

## State axes and selection

Each leaf has an independent `SealState`:

- `UNSEALED`: incomplete or no seal witness;
- `VALID`: a complete marker-last witness was validated and a `Seal` is present;
- `INVALID`: malformed, conflicting, or failed validation evidence.

A domain `LeafAssessment` can carry opaque `evidence`, `execution_outcome`, and
`domain_verdict`; TwinStamp never interprets them. Before domain validation,
the core itself constructs typed assessments for unrecognized units, missing
markers, and invalid markers. An `UnrecognizedEvidenceUnit` therefore remains
visible without invoking caller code or reading its marker.

`SelectionState` is separate.  It can be `PENDING`, `MISSING`, `SELECTED`,
`DUPLICATE`, `INVALID`, `UNSEALED`, or `PUBLICATION_CONFLICT`.  `PENDING` skips
storage discovery until the provider effect is settled.  A policy selects from
current leaves only; historical leaves remain visible but are not candidates.

Two supplied policies intentionally differ:

- `SelectExactlyOne` treats any two
  current children—valid, invalid, or unsealed—produce `DUPLICATE`.
- `ValidSealsOnly` selects exactly one valid, conflict-free current leaf.  Torn
  or invalid siblings do not hide it, but multiple valid leaves are duplicates.

## Discovery and reconciliation

Discovery reads only unique *immediate child prefixes* below `ResultSlot.prefix`.
Direct objects at the prefix are not units.  Children are sorted, invalid and
foreign-profile keys are retained as anomalies, and more than `max_children`
accepted children raises `ChildLimitExceeded` instead of returning a truncated
answer.  The limit bounds accepted children, not the underlying paginated list
work.

The caller supplies the storage adapter, marker convention, leaf validator, and
selection policy. The validator is invoked only with a `CanonicalEvidenceUnit`:
a recognized identity plus one present, canonical, bounded marker document.
Missing, malformed, oversized, or foreign evidence never reaches domain code
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


def validate(candidate, submission):
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

## Submission coordination

TwinStamp now also provides a small function-first coordination seam for
provider jobs and durable intent.  Callers build a `SubmissionSpec` from exact
immutable canonical bytes plus their SHA-256 digest, then supply a `JobBackend`
and `IntentJournal`.

`ensure_submission()` owns the reserve/claim → provider ensure/adopt → durable
record path. `observe_submissions()` owns one observation pass and asks the
journal to settle only facts it can safely project. Ensure facts
(`Created`, `AdoptedExact`, `RejectedNoEffect`, `Collision`, `Ambiguous`) are
separate from observation facts (`ObservedExact`, `ObservedCollision`,
`NotVisible`, `ObservationAmbiguous`). Every fact carries structural
provider-effect and settlement claims; `NotVisible` and ambiguity are always
unsettled.

`AdoptedExact` is deliberately only a fact.  A caller may treat unexpected exact
adoption as a collision while treating redrive of already-durable intent as
clean, as the S3 listing study does through its SQLite journal adapter.

## Study adapter status

The S3 listing study is the first client. Its existing report paths,
marker bytes, validator, strict duplicate behavior, history derivation, and
finality behavior, GCP Batch normalization, request rendering, SQLite schema,
canonical `job_json` encoding, and exact adoption policy remain study-owned.
TwinStamp supplies only generic evidence, reconciliation, typed provider facts,
and intent/job orchestration seams.  It has no imports of the study, cloud SDKs,
benchmarks, or subject tools.

## Current limitations and roadmap

Today the package does not provide:

- a versioned portable storage convention or domain marker schema;
- object-store mutation, conditional-create, multipart/resumable upload, or a
  production backend/store adapter;
- final-publication writer fencing or an atomic namespace snapshot;
- cryptographic signing, authentication, or a correctness/domain-verdict
  policy;
- a CLI, non-Python writer kit, or a second production store/backend/client.

Next work is to version the convention and golden trees, add a reader-side
validator and minimal writer support, add a post-fence fresh scan for final
publication, and validate the abstraction against materially different adapters
and a second client.  Those are separate changes so this core stays a small,
behavior-preserving extraction.
