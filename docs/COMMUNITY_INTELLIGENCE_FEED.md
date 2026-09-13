# Community Intelligence Feed

LastMile Market can use a community preference matrix at `community/preferences.json` in the configured inventory/evidence bucket. Dispatch reads this file through `COMMUNITY_PREFERENCES_KEY` and uses it only as an explicit demand signal for nutrition/category matching.

This file is optional. If it is not present, the backend returns a clearly labelled `synthetic_demo` fallback so the demo can show the workflow without pretending the data is real.

## Production rule

Do not label community preferences as verified until they come from one or more explicit intake sources:

- LastMile Market app requests
- SMS request intake
- partner pantry or community organization feedback
- operator-entered post-mission feedback

Do not infer food preferences from race, ethnicity, income, religion, age, gender, neighborhood demographics, or any protected/personal demographic signal.

## S3 location

```text
s3://food-access-evidence-576951331959-us-east-1/community/preferences.json
```

Runtime configuration:

```text
COMMUNITY_PREFERENCES_KEY=community/preferences.json
COMMUNITY_INTELLIGENCE_ALLOW_SYNTHETIC_FALLBACK=false
```

Use `COMMUNITY_INTELLIGENCE_ALLOW_SYNTHETIC_FALLBACK=true` only for demos or other clearly labelled unavailable-source scenarios. For real dispatch, upload a verified `community/preferences.json` and keep the synthetic fallback disabled so a missing or unreadable feed stays blocked instead of silently reverting to `synthetic_demo`.

## JSON shape

The file must be a JSON array of objects.

The checked-in example file is illustrative only. Do not upload it unchanged as verified production data.

```json
[
  {
    "profile_id": "austin-app-sms-2026-09",
    "community_area": "Austin",
    "tract_fips": "17031837800",
    "requested_categories": ["fresh produce", "protein", "whole grain"],
    "preference_tags": ["greens", "beans", "rice"],
    "request_count": 34,
    "confidence": 0.82,
    "source_type": ["app", "sms", "partner_pantry"],
    "source_window_start": "2026-09-01",
    "source_window_end": "2026-09-12",
    "last_updated": "2026-09-12T23:59:00-05:00",
    "verification_status": "operator_reviewed"
  }
]
```

## Required fields

| Field | Purpose |
| --- | --- |
| `profile_id` | Stable row ID for auditability. |
| `requested_categories` | Explicit category demand used by Dispatch nutrition matching. |
| `preference_tags` | Optional item-level tags such as greens, beans, rice, milk, eggs. |
| `request_count` | Number of explicit requests or feedback events behind the row. |
| `confidence` | 0 to 1 confidence from source quality and recency. |
| `source_type` | One or more of `app`, `sms`, `partner_pantry`, `operator_feedback`. |
| `verification_status` | Should be `operator_reviewed` before use in real dispatch. |

At least one geographic matching field should be present:

- `tract_fips`
- `stop_id`
- `community_area`
- `region_name`
- `county_name`

## Accepted source types

```text
app
sms
partner_pantry
operator_feedback
```

## Confidence guidance

| Confidence | Meaning |
| --- | --- |
| 0.80-1.00 | Multiple recent verified sources agree. |
| 0.60-0.79 | One verified source or older multi-source signal. |
| 0.40-0.59 | Demo/low-confidence signal; use for planning review only. |
| below 0.40 | Do not use for allocation without operator review. |

## Operator checklist before real dispatch

- Confirm the file came from explicit app, SMS, partner pantry, or operator feedback.
- Confirm there is no demographic inference.
- Confirm each row has a geography match.
- Confirm `verification_status=operator_reviewed` for production rows.
- Confirm the upload path is `community/preferences.json`.
- Run Brief the Crew and verify Mission Review shows the Community Intelligence check as a readable non-synthetic source instead of `synthetic_demo`.
- Treat that Mission Review result as a readability/configuration check only; it does not validate per-row provenance, required fields, or operator review, so the manual checklist above remains the verification gate for real dispatch.

## Current hackathon/demo status

Until this file is produced from a verified intake workflow, the system should continue labeling Community Intelligence as `synthetic_demo` and Mission Review should show that it must be replaced before real dispatch.
