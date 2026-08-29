# Workstream 2: Geospatial Prioritization and Route Optimization

## Product boundary

The Site Advisor ranks census tracts or candidate service areas for investigation; it does not select an exact property. Exact siting requires parcel, zoning, ownership, cost, utility, host-willingness, and feasibility evidence.

The Route Advisor compares candidate mobile-service scenarios under explicit assumptions; it does not claim operational readiness without organization-supplied constraints.

## Site priority score

For tract (t):

```text
Priority(t) =
  w1 * food_access_gap
+ w2 * economic_need
+ w3 * no_vehicle_vulnerability
+ w4 * population_potentially_served
+ w5 * transportation_burden
- w6 * existing_verified_coverage
```

A starting profile is 30%, 20%, 15%, 15%, 15%, and 5%, respectively. Treat these as policy assumptions requiring review, not objective truth.

Normalize components to 0–100. Return raw values, normalized values, weights, weighted contributions, score version, data completeness, and warnings.

Avoid double counting: USDA food-access indicators already contain income/access concepts. Define the food-gap component narrowly and document correlations with ACS poverty.

## Component guidance

- **Food-access gap:** affected population or share beyond a documented LRAM/SRAM threshold.
- **Economic need:** transparent combination of poverty and SNAP participation.
- **No vehicle:** households without a vehicle divided by total households.
- **Population served:** population inside a service area, preferably using block groups or population-weighted points.
- **Transportation burden:** stop proximity, service frequency, and grocery travel time/reachability.
- **Existing coverage:** resource-type, verification, capacity, and service-area-aware adjustment.

Do not count all resources equally. Keep resource-equivalency assumptions visible, configurable, versioned, and tested.

## Adjustable priorities and sensitivity

The UI must:

- Validate weight totals and bounds.
- Restore the default profile.
- Explain every component.
- Recalculate without an LLM.
- Show rank changes from default.
- Save the profile with the recommendation.
- Warn when small weight changes reverse the top recommendation.

## Candidate stops

Candidates may come from community centers, libraries, feasible schools, faith organizations, municipal buildings, pantries, public parking locations, user-entered sites, or population-weighted centers of high-need geographies.

Each candidate includes coordinates, address, type, tract GEOID, covered population, estimated demand, road access, suitability/verification state, and source. Analytical centroids are candidate areas, not approved stops.

## Route inputs

A real route scenario requires:

- Depot/start point
- Vehicle count and capacity
- Maximum route duration
- Candidate, existing, and required stops
- Service time at each stop
- Service days/time windows
- Estimated demand
- Origin-destination travel matrix
- Maximum number of stops

Public data does not supply an organization's capacity, staffing, depot, inventory, schedule, budget, or actual demand. Obtain those from a partner or present them as explicit scenario assumptions.

## Service-area calculation

For each candidate:

1. Generate a configured drive-time isoline.
2. Intersect it with Census geographies.
3. Estimate total and high-need population.
4. Adjust for overlapping selected-stop coverage.
5. Calculate newly covered, already covered, losing coverage, and still-unserved populations.

## Optimization

Use Amazon Location to calculate road matrices/isolines and open-source OR-Tools for need-aware selection and constraints.

A suitable objective is:

```text
maximize:
  sum(need_i * newly_served_population_i)
  - lambda * total_travel_time
```

Subject to route duration, capacity, stop count, service time, required-stop, and time-window constraints.

Implement in two stages:

1. Select candidate stops that maximize weighted coverage.
2. Sequence selected stops under route constraints.

Do not let Bedrock choose stops or solve the optimization.

## Scenarios

Return:

- **Baseline:** existing or explicitly assumed coverage.
- **Maximum coverage:** maximizes newly served population.
- **Equity priority:** emphasizes documented high need and transportation vulnerability.
- **Custom:** planner-selected weights and constraints.

Each scenario returns selected stops, sequence, travel/service time, demand, utilization, total/new/high-need population served, gaining/losing communities, remaining gaps, assumptions, and warnings.

## Result contracts

A site result includes:

- Analysis ID, tract GEOID, rank, score/version
- Weight profile
- Component breakdown
- Data completeness and warnings
- Sources and snapshot IDs

A route scenario includes:

- Scenario ID/version and assumptions
- Candidate and selected stops
- Route geometry and ordered stops
- Matrix/provider metadata
- Constraint utilization
- Coverage gains/losses
- Sources and warnings

## Hackathon scope

Complete real site prioritization. Demonstrate rural optimization with visible assumptions such as one vehicle, six-hour route, six candidate stops, 30-minute service time, configurable capacity, and a chosen drive-time radius. Add OR-Tools only after data joins, scoring, map boundaries, and API contracts are stable.
