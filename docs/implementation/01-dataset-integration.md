# Workstream 1: Dataset Integration

## Objective

Add authoritative evidence without introducing machine learning. All joins, classifications, scores, verification states, and citations must be deterministic, versioned, reproducible, and testable.

## Required sources

| Priority | Source | Geography | Use |
|---:|---|---|---|
| 1 | USDA LRAM/SRAM | Tract | Food-access gaps |
| 2 | Census ACS 5-year | Tract | Population, poverty, SNAP, vehicles, age, disability |
| 3 | Census TIGER/Line and relationship files | Tract | Boundaries and vintage harmonization |
| 4 | OpenStreetMap/Overpass | Point | Existing food resources |
| 5 | CTA static GTFS | Stop/route/trip | Chicago transit access |
| 6 | Amazon Location | Road network | Travel times, matrices, isolines |
| 7 | Chicago business licenses | Establishment | Resource operating evidence |
| 8 | Chicago food inspections | Establishment | Recent operating evidence |
| 9 | CDC PLACES | Tract | Optional contextual health indicators |
| 10 | USDA Food Environment Atlas | County | Optional regional context |

Official entry points:

- USDA FARA: https://www.ers.usda.gov/data-products/food-access-research-atlas/download-the-data
- USDA geospatial services: https://www.ers.usda.gov/developer/geospatial-apis
- ACS API: https://www.census.gov/data/developers/data-sets/acs-5year.html
- CTA GTFS: https://www.transitchicago.com/developers/gtfs/
- Chicago licenses: dataset `r5kz-chrr`
- Chicago inspections: dataset `4ijn-s7e5`
- CDC PLACES: https://www.cdc.gov/places/tools/data-portal.html

## Data-source adapters

Add source adapters rather than placing HTTP and parsing logic in agent tools:

```text
data_sources/
  base.py
  usda_fara.py
  census_acs.py
  census_geography.py
  osm_resources.py
  cta_gtfs.py
  amazon_location.py
  chicago_licenses.py
  chicago_inspections.py
  cdc_places.py
  usda_food_environment.py
services/
  tract_enrichment.py
  accessibility.py
  resource_resolution.py
  evidence_registry.py
```

Adapt names to existing repository patterns; do not rewrite sound modules solely to match this example.

## USDA requirements

- Support LRAM and SRAM as distinct products.
- Preserve product, vintage, methodology, tract vintage, distance method, official URL, retrieval time, and checksum.
- Never treat all SNAP-authorized retailers as equivalent to full-service grocery stores.
- Complete real Alexander County ingestion.
- Retain an explicit offline sample mode; never silently substitute samples.
- Report row counts, missing fields, invalid GEOIDs, and join quality.

## ACS requirements

Use `CENSUS_API_KEY` and configurable `CENSUS_ACS_YEAR`. Validate selected variables through Census group metadata.

Retrieve or derive:

- Total population
- Under-18 and 65+ populations
- Poverty population/rate
- Median household income
- SNAP households/rate
- No-vehicle households/rate
- Disability prevalence
- Unemployment
- Estimate and margin of error

Potential tables include `B01001`, `B17001`, `B19013`, `B22003`, `B08201`, and `B23025`; verify definitions for the configured release.

Never turn missing/suppressed values into zero. Race and ethnicity support descriptive equity auditing and must not change scores without an approved policy.

## Geography requirements

- Attach geography vintage to every record.
- Never directly join 2010 and 2020 tracts as if identical.
- Use official relationship files where crosswalks are necessary.
- Record allocation method and weights.
- Produce exact-match, crosswalk, one-to-many, many-to-one, unmatched, and exclusion counts.
- Fail or return a partial result when join quality is below a configured threshold.

## OSM requirements

Keep full-service groceries, convenience stores, pantries, farmers markets, gardens, farms, marketplaces, and mobile stops separate. Preserve source element ID/type, coordinates, tags, query region/hash, retrieval time, and classification rule. Add caching, retries, endpoint fallback, and deduplication. OSM-only evidence is `unverified`.

## Transportation requirements

For CTA static GTFS, implement stop proximity, routes serving a tract, scheduled service frequency, and then grocery reachability. Preserve feed effective dates and never call scheduled data real time.

Use Amazon Location for road time/distance, route matrices, isolines, and route geometry. Cache matrices and explicitly label any straight-line fallback.

## Resource verification

Use deterministic states:

- `verified_active`
- `likely_active`
- `unverified`
- `possibly_closed`
- `conflicting_evidence`
- `human_review_required`

Resolve entities using stable identifiers, exact normalized addresses, strong name/address agreement, then strong name plus geographic proximity. Never merge solely by proximity. Preserve rule ID, match method, confidence, sources, dates, and explanation.

## Evidence contract

Every evidence item must include source, dataset ID, official URL, vintage, retrieval time, geography level/vintage, GEOID, raw value, normalized value, unit, margin of error where relevant, missing status, and warnings.

Recommended Pydantic models:

- `SourceCitation`
- `DataQualityReport`
- `FoodAccessEvidence`
- `DemographicEvidence`
- `TransportationEvidence`
- `ResourceEvidence`
- `ResourceVerification`
- `TractEvidence`

## Storage and reproducibility

- S3 raw prefixes retain immutable downloads and checksums.
- Processed evidence uses Parquet/GeoParquet.
- Analysis snapshots connect question → source versions → evidence → score → recommendation.
- Cache expiration is source-specific.
- External calls use timeouts, bounded retries, rate-limit handling, and visible stale-cache warnings.
- Results are labeled `complete`, `partial`, `stale_cache`, `sample_mode`, or `failed`.

## Tests

Add network-free fixtures and unit tests for parsing, missing values, margins of error, GEOIDs, crosswalks, LRAM/SRAM separation, GTFS, routing fallbacks, OSM classification, deduplication, license/inspection parsing, entity resolution, verification rules, citations, caching, and data-quality thresholds. Live API tests are optional and clearly marked.
