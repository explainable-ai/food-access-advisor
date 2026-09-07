# Eventbrite Community Access Watch setup

The integration reads only events owned by the Eventbrite organization authorized by the private token. Eventbrite retired the public Event Search API, so the service fetches expanded organization events and applies the food-access keyword, category, format, and Chicago-area rules locally.

## 1. Store the private token

Run this in a trusted Codespace terminal. The token is read without echoing and is never written to shell history.

```bash
read -rsp "Eventbrite private token: " EVENTBRITE_TOKEN_INPUT; echo

if aws secretsmanager describe-secret \
  --secret-id food-access/eventbrite-api-token \
  --region us-east-1 >/dev/null 2>&1; then
  aws secretsmanager put-secret-value \
    --secret-id food-access/eventbrite-api-token \
    --secret-string "$EVENTBRITE_TOKEN_INPUT" \
    --region us-east-1
else
  aws secretsmanager create-secret \
    --name food-access/eventbrite-api-token \
    --description "Private Eventbrite API token for Food Access Advisor" \
    --secret-string "$EVENTBRITE_TOKEN_INPUT" \
    --region us-east-1
fi

unset EVENTBRITE_TOKEN_INPUT
```

## 2. Find the organization ID

This prints organization names and numeric IDs, not the token.

```bash
EVENTBRITE_TOKEN_RUNTIME="$(aws secretsmanager get-secret-value \
  --secret-id food-access/eventbrite-api-token \
  --region us-east-1 \
  --query SecretString \
  --output text)"

curl --fail --silent --show-error \
  --header "Authorization: Bearer $EVENTBRITE_TOKEN_RUNTIME" \
  "https://www.eventbriteapi.com/v3/users/me/organizations/" \
  | jq '.organizations[] | {id, name}'

unset EVENTBRITE_TOKEN_RUNTIME
```

Set the chosen numeric ID on the ECS container as `EVENTBRITE_ORGANIZATION_ID`.

## 3. Create the webhook secret

```bash
EVENTBRITE_WEBHOOK_SECRET_INPUT="$(openssl rand -hex 32)"

aws secretsmanager create-secret \
  --name food-access/eventbrite-webhook-secret \
  --description "Shared callback token for Eventbrite webhooks" \
  --secret-string "$EVENTBRITE_WEBHOOK_SECRET_INPUT" \
  --region us-east-1

unset EVENTBRITE_WEBHOOK_SECRET_INPUT
```

If the secret already exists, use `aws secretsmanager put-secret-value` instead of `create-secret`.

## 4. Add ECS task settings

Create a new task-definition revision with:

- container secret `EVENTBRITE_API_TOKEN` → Secrets Manager ARN for `food-access/eventbrite-api-token`
- container secret `EVENTBRITE_WEBHOOK_SECRET` → Secrets Manager ARN for `food-access/eventbrite-webhook-secret`
- container environment variable `EVENTBRITE_ORGANIZATION_ID` → the numeric organization ID

The ECS task role needs `secretsmanager:GetSecretValue` for exactly those two secret ARNs. Deploy the new revision to the `food-access-advisor-api` service and wait for rollout completion.

## 5. Configure Eventbrite webhooks

Create an Eventbrite webhook for event publication, update, and cancellation actions. Use this callback URL, substituting the generated shared secret:

```text
https://YOUR_API_HOST/api/integrations/eventbrite/webhook?token=YOUR_HIGH_ENTROPY_WEBHOOK_SECRET
```

The receiver validates the callback token, rejects non-Eventbrite API URLs, and re-fetches the complete event with `expand=venue,organization,ticket_availability`. It records a human-review finding only; it never changes a ranking, route, or dispatch automatically.

## 6. Verify

After deployment, use an authenticated staff token to run an initial snapshot:

```bash
curl --fail --silent --show-error \
  --request POST \
  --header "Authorization: Bearer $STAFF_ACCESS_TOKEN" \
  "https://YOUR_API_HOST/api/community-signals/eventbrite/refresh" \
  | jq
```

Then edit or cancel a relevant Eventbrite event and confirm it appears in `GET /api/watchdog/changes` for human review.
