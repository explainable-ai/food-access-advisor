# LastMile Market backend

LastMile Market is an AWS-native mobile-market planning and operations backend built with FastAPI, the Strands Agents SDK, Amazon Bedrock, Amazon Location Routes V2, S3, and DynamoDB-backed operational state. The user-facing Last Mile Crew is Scout, Router, Dispatch, and Sentry.

The backend keeps deterministic calculations authoritative: Scout's tract ranking, Router's constrained route optimization, Dispatch's inventory and mission calculations, and Sentry's evidence checks are tool-backed. LLMs coordinate and explain; they do not invent scores, route order, or load quantities.

## Dispatch mathematical objective

Dispatch now includes a deterministic joint item-to-stop allocation stage called **Knapsack of Equity**. After a feasible payload is selected from current S3 inventory, Dispatch allocates item quantities `Q[i,s]` across planned stops with the configurable objective:

```text
maximize sum(Q[i,s] * (w_v*V[s] + w_n*N[i,s] + w_s*S[i]))
```

Where:

- `V[s]` is Scout's transparent need/vulnerability score normalized to 0–1.
- `N[i,s]` is an explicit nutrition/community-request match. If no preference evidence exists, Dispatch uses a neutral score rather than inferring preferences from demographic characteristics.
- `S[i]` is inverse days-to-spoil priority, so near-expiration inventory receives a higher waste-reduction benefit.
- the default policy weights are 0.50 vulnerability, 0.30 nutrition match, and 0.20 spoilage; they are runtime configuration and are normalized before use.

Guardrails include allocatable on-hand quantity, warehouse minimum reserve, vehicle/load capacity, stop reserve protection, multi-stop route demand or explicit stop-allocation caps, optional `max_allocation_per_household`, expired-inventory exclusion, cold-chain evidence, and human review. Rescue recommendations never automatically reprice, donate, transfer, or mutate inventory.

## Development

Install dependencies and run tests:

```bash
python -m pip install -r requirements.txt
python -m pytest -q
```

Run the API:

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

See the repository wiki and `deploy/` documentation for AWS deployment, Cognito, Amazon Location, and operational setup details.
