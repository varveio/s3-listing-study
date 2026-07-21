# S3 listing reference

Every tool in this study drives one S3 call: `ListObjectsV2`. This page records
what AWS documents about it — the behavior the tools depend on, and that the
study reads its results against. Everything here is `supported` by the API
reference (the docs say so); nothing is `confirmed`, since no run in this repo
proves it. Checked against the [`ListObjectsV2` API
reference](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html)
on 2026-07-21. The open questions built on these mechanics live in
[`open-questions.md`](open-questions.md).

Evidence vocabulary is in [`methodology.md`](methodology.md#evidence-language),
and the tool-claim labels in
[`../tools/README.md`](../tools/README.md#what-the-evidence-labels-mean).

## Ordering

General-purpose buckets return objects "in lexicographical order based on their
key names." Keys are UTF-8, so that order is over their raw byte sequences.

Range listing depends on this. Split the keyspace, give each worker a disjoint
range, and the outputs concatenate in order into one complete listing — but only
if the store is actually sorted. Because the comparison is a plain byte ordering
(no locale, case-folding, or Unicode normalization), a tool can put a cut point
at any byte string and know exactly which side each key falls on. Several
S3-compatible stores aren't sorted, though, which is its own question
([`open-questions.md` §6](open-questions.md#6-which-s3-compatible-differences-can-affect-listing)).

Directory buckets (S3 Express One Zone) are not sorted. A tool that assumes the
general-purpose ordering on one will emit wrong output; see
[S3 Express One Zone](#s3-express-one-zone-directory-buckets) below.

## Request parameters

`GET /?list-type=2`, with:

| Parameter | Meaning |
| --- | --- |
| `prefix` | Return only keys beginning with this string. |
| `delimiter` | Roll keys up under `CommonPrefixes` (below). |
| `start-after` | List starts *after* this key. |
| `max-keys` | Cap per response; default and maximum 1,000. |
| `continuation-token` | Resume a truncated listing. |
| `encoding-type` | `url` to percent-encode keys in the response. |
| `fetch-owner` | Include each object's `Owner` (off by default). |
| `x-amz-optional-object-attributes` | Extra fields; only `RestoreStatus` is valid. |

## Delimiter and CommonPrefixes

With a delimiter set, keys sharing the substring between `prefix` and the first
delimiter collapse into a single `CommonPrefixes` entry, and those keys drop out
of the response body. It's the "folder" view: prefix `notes/`, delimiter `/`, key
`notes/summer/july` gives the common prefix `notes/summer/`.

What a lister has to account for:

- `CommonPrefixes` appears only when a delimiter is set.
- Each entry counts as one against `max-keys` and `KeyCount`, so a
  1,000-"result" page can stand for far more than 1,000 keys.
- An entry is dropped if it isn't lexicographically greater than `StartAfter`.
- Entries sit in lexicographic position among the keys — AWS's own example
  returns `photos/2006/February/` before `photos/2006/January/`.

## StartAfter

> `StartAfter` is where you want Amazon S3 to start listing from. Amazon S3
> starts listing after this specified key. `StartAfter` can be any key in the
> bucket.

So it's an exclusive lower bound, and the key need not exist — a tool can pass a
computed boundary. It applies to the first request only; after truncation,
`ContinuationToken` carries the position and `StartAfter` is ignored. Directory
buckets don't support it.

Range-splitting tools build on this: `s3-fast-list` takes supplied cut points,
[S3P computes midpoints](../tools/s3p/README.md#how-it-works). It's a lower bound
only, so two adjacent workers still have to agree who owns the boundary key —
listed once, not zero or twice. The
[s3-fast-list analysis](../tools/s3-fast-list/README.md#what-we-learned) records
an unverified case where a key equal to a cut point can be dropped, so a cut
point buys range listing, not a proof of complete coverage.

## Pagination

- `IsTruncated` is `false` when the response held everything, `true` when more
  remains. A short page is not a substitute for the flag.
- When it's `true`, `NextContinuationToken` comes back — opaque, "not a real
  key." Send it as `continuation-token` for the next page.
- `KeyCount` is how many results the page held (`≤ max-keys`), counting each
  `CommonPrefixes` roll-up as one.

## Key encoding

Keys can hold any Unicode character, but the XML 1.0 response can't carry some of
them (ASCII 0–10 among others). `encoding-type=url` makes S3 percent-encode the
`Key`, `Prefix`, `Delimiter`, and `StartAfter` it returns — `test_file(3).png`
comes back as `test_file%283%29.png` — which the client then decodes exactly
once. Anything comparing listings has to stay byte-exact rather than assume
printable keys. AWS also notes that a `200 OK` body "can contain valid or invalid
XML," so a parser has to be defensive.

## Object fields

Each object carries `Key`, `LastModified`, `ETag`, `Size`, and `StorageClass`.
`Owner` shows up only with `fetch-owner=true`; `RestoreStatus` only when asked
for through `OptionalObjectAttributes` (whose one valid value is `RestoreStatus`).

`ETag` isn't a plain content hash. For a single-part upload it's the MD5; for a
multipart upload it's a composite with a `-N` suffix for the part count.
Comparing ETags across tools means normalizing for that.

## S3 Express One Zone (directory buckets)

Directory buckets drop several general-purpose guarantees:

- results aren't in lexicographical order;
- `StartAfter` isn't supported;
- `/` is the only delimiter, and prefixes have to end in `/`;
- no `OptionalObjectAttributes` / `RestoreStatus`, no requester-pays; zonal
  endpoints and session auth.

A bisecting tool can't treat one like a general-purpose bucket. Whether each tool
refuses or adapts — rather than bisecting anyway and returning wrong keys — is
[`open-questions.md` §4](open-questions.md#4-how-does-s3-express-one-zone-affect-bisection).

Sources: [`ListObjectsV2` API
reference](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html)
and [directory-bucket
differences](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-express-differences.html),
read 2026-07-21.
