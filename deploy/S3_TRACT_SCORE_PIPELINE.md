# S3 tract-feature and priority-score pipeline

This job rebuilds the deterministic Chicago and Chicagoland rural-fringe
evidence surfaces from immutable S3 inputs. It uses a temporary directory, so
the raw ACS snapshot is not copied into the repository.

## Required S3 inputs

All six objects must include `sha256` object metadata. Source keys can be
overridden with CLI flags.

| Input | Default key |
|---|---|
| USDA 2025 SRAM bundle | `raw/2026-09-05/usda/sram_2025.zip` |
| Census 2020 tract Gazetteer | `raw/2026-09-05/census/2020_tract_gazetteer.zip` |
| Approved ACS 2024 snapshot | `raw/2026-09-05/census/acs_2024_approved_counties_snapshot.json` |
| Prepared Chicago context | `prepared/2026-09-05/urban_scoring_context.json` |
| Verified urban resources | `resource-cache/urban.json` |
| Verified rural resources | `resource-cache/rural.json` |

The two resource snapshots must be non-empty, geocoded, and identify the
correct `scope`. Re-publish them with the current cache command before the
first pipeline run so they receive checksum metadata.

## Run from PowerShell

From the backend repository root:

```powershell
$env:AWS_PROFILE = "food-access-admin"
$env:AWS_REGION = "us-east-1"
$env:EVIDENCE_BUCKET = "food-access-evidence-576951331959-us-east-1"

aws sso login --profile $env:AWS_PROFILE
python -m scripts.refresh_resource_cache --scope all

python .\data\s3_prepare_scores.py `
  --evidence-date 2026-09-05 `
  --report .\tract-score-pipeline-report.json
```

Use `--no-upload --work-dir <outside-the-repository>` for a local validation
run. Do not point `--work-dir` at `data/raw` or another tracked directory.

## Published artifacts

The default prefix is `prepared/2026-09-05/tract-priority/`:

- `atlas_pilot_city.db` — 1,331 Cook County SRAM tracts enriched with ACS
- `atlas_rural_fringe.db` — 72 USDA-classified rural tracts enriched with ACS
- `tract_features_chicago.json` — 792 Chicago tract feature records
- `tract_features_rural.json` — 72 rural-fringe tract feature records
- `priority_scores_chicago.json` — complete deterministic Chicago ranking
- `priority_scores_rural.json` — complete deterministic rural ranking
- `tract_score_pipeline_report.json` — input/output checksums and version IDs

The job fails before publication if an input checksum is absent or wrong, the
Atlas and ACS tract universes do not match, Chicago does not contain 792 scored
tracts, the rural surface does not contain 72 tracts, or resource evidence is
empty. Missing rural transit remains missing in the score components; it is
never converted to zero.

After reviewing the report, promote the two database artifacts to the runtime
keys used by ECS (`prepared-data/atlas_pilot_city.db` and
`prepared-data/atlas_rural_fringe.db`) in a separate, explicit deployment step.

