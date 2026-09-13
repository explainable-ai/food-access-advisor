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
      "Sid": "CalculateLastMileRoutes",
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

`ROUTING_TRAVEL_MODE` may be `Car` or `Truck`. Use `Truck` when the deployed
mobile-market vehicle should be routed with truck-specific road restrictions.
Normal AWS credential resolution applies locally (`aws configure`, SSO, or
environment credentials). Deployed workloads should use their IAM role.

## Behavior and cost boundary

The backend sends the depot and candidate stops as both origins and
destinations to `CalculateRouteMatrix`, using fastest-route optimization and
`Traffic.Usage=UseTrafficData`.

By default, `ROUTING_DEPART_NOW=true`, so Amazon Location receives
`DepartNow=true`. That means the travel-time matrix and returned road route can
reflect current traffic and closure conditions available to the provider. The
same setting is used by `CalculateRoutes` when the frontend requests route
geometry and instructions.

Because current conditions can change, live-route results are intentionally not
expected to be bit-for-bit reproducible across runs. For a non-live planning
comparison, set:

```text
ROUTING_DEPART_NOW=false
```

That opt-out preserves traffic-enabled routing configuration but omits the live
departure context. Durations returned by Amazon Location are converted from
seconds to minutes before deterministic optimization.

A scenario with N candidates requests `(N + 1)²` matrix cells because the
depot is included. The optimizer hard-limits input to fifteen candidates.
Amazon bills route-matrix work by origin/destination pairs, so keep that limit
in place.

Provider errors fail closed with HTTP 502 on direct route API calls. The Crew
route path preserves its existing behavior if the optional inventory preflight
is unavailable, but it does not silently substitute a different road-routing
provider. A planner can deliberately choose **Haversine estimate (demo)** where
that option is exposed; those results remain visibly labelled
`haversine_drive_time_estimate`.

## Verification

With AWS credentials configured, start the API and submit a route scenario from
the Route Advisor workspace using Amazon Location. Verify that:

1. the route succeeds with `ROUTING_PROVIDER=aws_location`;
2. `ROUTING_DEPART_NOW=true` is present in the runtime environment;
3. changing `ROUTING_TRAVEL_MODE` between `Car` and `Truck` changes the request
   mode without exposing AWS credentials to the browser;
4. the returned route geometry follows roads and the optimizer reports a
   road-network travel-time source.

An access-denied response means the runtime identity is missing one of the
`geo-routes` actions above or is using a different Region.

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
