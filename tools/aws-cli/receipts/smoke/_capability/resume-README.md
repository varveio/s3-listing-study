# Capability probe — resume round-trip (`--max-items` + `--starting-token`)

Directly addresses the dossier's demand (modes table + weakness #3) and
open-questions §3: *"aws-cli's `--starting-token` exists but the caller
must persist it."* Two invocations, anonymous, against `normals-hourly/` (2,549
objects). NOT a wrapper-measured mode (the resume token is dynamic — produced by
leg 1, consumed by leg 2 — so it cannot be a static parameterized `run.sh` mode);
recorded as a capability probe.

    # leg 1 — bounded to 1000 items, emits an opaque NextToken
    aws s3api list-objects-v2 --bucket <b> --region us-east-1 --no-sign-request \
      --prefix normals-hourly/ --max-items 1000 --output json
    # leg 2 — resume from that token
    aws s3api list-objects-v2 --bucket <b> --region us-east-1 --no-sign-request \
      --prefix normals-hourly/ --starting-token <NextToken-from-leg-1> --output json

## Result [RUN capability, aws-cli 2.36.1, 2026-07-17]

| | |
| --- | --- |
| leg 1 keys | 1000 (exit 0), `NextToken` present (opaque, 180 chars) |
| leg 2 keys | 1549 (exit 0) |
| union distinct | **2549** = manifest `normals-hourly/` count exactly |
| cross-leg duplicate keys | **0** |
| gap | none (1000 + 1549 = 2549, no missing) |
| leg1 first / last key | `normals-hourly/1981-2010/access/AQW00061705.csv` / `normals-hourly/2006-2020/access/USW00003759.csv` |
| leg2 first / last key | `normals-hourly/2006-2020/access/USW00003761.csv` / `normals-hourly/doc/NORMAL_HLY_documentation.pdf` |

**Conclusion.** The resume *primitive* round-trips correctly — no gap, no
duplicate across the token boundary. The dossier's other half ("easy to lose")
is a design fact confirmed in source: the token is emitted only to stdout as
`NextToken`; nothing in aws-cli persists it `[SRC awscli/customizations/paginate.py:155-165 @12d962d2]`.

Payloads: `resume-leg1.json.gz`, `resume-leg2.json.gz` (secret-scanned before compression).
