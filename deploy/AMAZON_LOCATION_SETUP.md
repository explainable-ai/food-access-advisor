# Amazon Location road-network setup

The route-scenario API uses Amazon Location Service Routes V2 through the
Boto3 `geo-routes` client. It does not create or require a legacy route
calculator resource. The frontend never receives AWS credentials.

## IAM

Attach this least-privilege statement to the role running FastAPI or the
AgentCore runtime. Replace the Region if `AWS_LOCATION_REGION` differs.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CalculateFoodAccessRouteMatrix",
      "Effect": "Allow",
      "Action": [
        "geo-routes:CalculateRouteMatrix",
        "geo-routes:CalculateRoutes"
      ],
      "Resource": "arn:aws:geo-routes:us-east-1::provider/default"
    }
  ]
}
```

Set the runtime environment variables:

```text
AWS_LOCATION_REGION=us-east-1
ROUTING_PROVIDER=aws_location
ROUTING_DEPART_NOW=true
ROUTING_TRAVEL_MODE=Car
```

`ROUTING_TRAVEL_MODE` may be `Car` or `Truck`. `ROUTING_DEPART_NOW=true` is
the production default: the backend asks Amazon Location to calculate using
current traffic data and closure conditions. Set it to `false` only when you
intentionally want a non-live planning calculation.

Normal AWS credential resolution applies locally (`aws configure`, SSO, or
environment credentials). Deployed workloads should use their IAM role.

## Behavior and cost boundary

The backend sends the depot and candidate stops as both origins and
destinations to `CalculateRouteMatrix`, using fastest-route optimization,
`Traffic.Usage=UseTrafficData`, the configured travel mode, and `DepartNow`
when enabled. Durations are returned in seconds and converted to minutes
before deterministic route optimization.

Because live traffic changes, two `DepartNow` calculations can legitimately
produce different durations or route choices. The backend uses a short-lived
route cache; tune `ROUTING_CACHE_TTL_SECONDS` when testing current conditions.

A scenario with N candidates requests `(N + 1)²` matrix cells because the
depot is included. The optimizer hard-limits input to fifteen candidates.
Amazon bills route-matrix work by origin/destination pairs, so keep that limit
in place.

Provider errors fail closed with HTTP 502. The backend never silently
substitutes straight-line estimates. A planner can deliberately choose
**Haversine estimate (demo)** in the UI; those results are visibly labelled
`haversine_drive_time_estimate`.

## Verification

With AWS credentials configured, start the API and submit a route scenario
from the Route Advisor workspace using **Amazon Location road network**. Verify
that the response uses a road-network matrix and that current-traffic mode is
enabled in the runtime environment. An access-denied response means the
runtime identity is missing the IAM statement above or is using a different
Region.

## Hybrid web map

The browser uses MapLibre GL with Amazon Location's dynamic `Hybrid` style.
This is separate from the backend route-matrix permission. It uses a
browser-visible Amazon Location API key restricted to map rendering and the
final website referrer; it never uses an AWS access key.

Wait until the final Lovable/custom URL is known, then create the key in the
Amazon Location console:

1. Open **Amazon Location Service → API keys → Create API key**.
2. Name it `FoodAccessAdvisorWebMap`.
3. Allow only map-rendering actions (`geo:GetMap*` or the equivalent current
   `geo-maps` map actions shown by the console).
4. Add the exact final HTTPS domain as the allowed web referrer.
5. Set an expiration date and calendar a rotation before it expires.
6. Put the returned value in Lovable's `VITE_AWS_LOCATION_API_KEY` deployment
   variable and set `VITE_AWS_LOCATION_REGION=us-east-1`.

Do not grant Places, Routes, trackers, geofences, or resource-management
actions to this browser key. Backend route calculations continue to use the
ECS task role. When the map key is absent or rejected, the UI retains its
explicitly labelled illustrative map rather than hiding the failure.
