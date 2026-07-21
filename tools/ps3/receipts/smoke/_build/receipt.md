# Build receipt (non-mode evidence) — pS3 source does NOT compile

`_build/` carries no verifier verdict. This records a build attempt, not a
listing run.

## What was attempted
Compile the pinned checkout (`<sources>/ps3`, HEAD
`9428492291ef3aa824dba0b495583279c3d33760`) from source. Upstream ships no
`go.mod`, so the attempt synthesizes one and pins the exact dependency versions
the shipped binary was built against (read from the binary's build metadata:
`aws-sdk-go v1.44.249`, `cobra v1.7.0`, `viper v1.15.0`, Go `1.20.3`).

- Builder image: `golang@sha256:403f48633fb5ebd49f9a2b6ad6719f912df23dae44974a0c9445be331e72ff5e` (`golang:1.20.3`), run native arm64.
- Script: `build-attempt.sh`; full transcript: `build-output.txt`.
- Result: **BUILD_EXIT=1** — compile errors.

## Errors (from `build-output.txt`, `go build ./...`)
```
# pS3/cmd
cmd/listObjectsV2.go:9:2:   "os" imported and not used
cmd/listObjectsV2.go:66:3:  undefined: log
cmd/listObjectsV2.go:102:3: undefined: log
cmd/listObjectsV2.go:130:3: undefined: log
cmd/listObjectsV2.go:165:5: undefined: atomic
cmd/listObjectsV2.go:186:37: "debug: item count=".atomic undefined (type untyped string has no field or method atomic)
cmd/listObjectsV2.go:218:5: undefined: log
cmd/listObjectsV2.go:316:5: undefined: log
```

## Finding
The source at HEAD does not build: `cmd/listObjectsV2.go` uses `log.*` and
`atomic.*` without importing `log` / `sync/atomic`, imports `os` without using
it, and contains a syntax error at line 186 (`"debug: item count=".` — a `.`
where a `,` was intended). Independently, the shipped binary `pS3.0-1-16`
exposes subcommands (`head-objects`, `list-object-versions`, `list-test`) whose
source files are ABSENT from the checkout — so the repo at HEAD cannot reproduce
the shipped binary even after the compile errors are fixed. The study therefore
runs the upstream-committed prebuilt binary (the only artifact the project ships
that works), not a source build. Details in `../../../research/report.md`
§ Container.
