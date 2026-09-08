# Cognito staff authentication and production CORS

Public rankings, maps, routes, and evidence remain readable. Only staff review
and verification actions require a Cognito access token with membership in the
`staff` group.

Do not deploy this stack until the final Lovable or custom URL is known. Cognito
callback URLs and production CORS origins must be exact, stable HTTPS URLs.

## 1. Deploy the Cognito resources

PowerShell:

```powershell
$FinalUrl = "https://YOUR-FINAL-DOMAIN"
$DomainPrefix = "food-access-advisor-576951331959"

aws cloudformation deploy `
  --stack-name FoodAccessAdvisorStaffAuth `
  --template-file infra/cognito-staff-auth.yaml `
  --parameter-overrides `
      DomainPrefix=$DomainPrefix `
      CallbackUrl="$FinalUrl/auth/callback" `
      LogoutUrl=$FinalUrl `
  --region us-east-1
```

Read the configuration values:

```powershell
aws cloudformation describe-stacks `
  --stack-name FoodAccessAdvisorStaffAuth `
  --query "Stacks[0].Outputs" `
  --output table `
  --region us-east-1
```

The browser client has no client secret and uses Authorization Code + PKCE.

## 2. Create the first staff user

```powershell
$UserPoolId = "PASTE-USER-POOL-ID"
$StaffEmail = "YOUR-EMAIL"

aws cognito-idp admin-create-user `
  --user-pool-id $UserPoolId `
  --username $StaffEmail `
  --user-attributes Name=email,Value=$StaffEmail Name=email_verified,Value=true `
  --region us-east-1

aws cognito-idp admin-add-user-to-group `
  --user-pool-id $UserPoolId `
  --username $StaffEmail `
  --group-name staff `
  --region us-east-1
```

Do not create shared judge credentials. The public demonstration remains
read-only; only named staff accounts can persist review actions.

## 3. Configure Lovable without committing values

Set these project environment variables in Lovable:

```text
VITE_COGNITO_AUTHORITY=<Authority stack output>
VITE_COGNITO_CLIENT_ID=<AppClientId stack output>
VITE_COGNITO_REDIRECT_URI=https://YOUR-FINAL-DOMAIN/auth/callback
VITE_COGNITO_LOGOUT_URI=https://YOUR-FINAL-DOMAIN
```

These identifiers are not passwords, but they still belong in deployment
configuration rather than source. Never add a client secret to a browser app.

The same four `VITE_COGNITO_*` variables configure the separate
`explainable-ai/food-equity-navigator` frontend repository. Its authentication
callback completes the PKCE redirect, and verification requests attach the access
token. Public pages and read requests remain unauthenticated.

## 4. Configure the API task

Export the CloudFormation outputs, render the checked ECS Express template,
and deploy the rendered file. The renderer fails before deployment if either
Cognito identifier is missing; it never substitutes demo credentials.

```text
FOOD_ACCESS_CORS_ORIGINS=https://YOUR-FINAL-DOMAIN
FOOD_ACCESS_AUTH_MODE=required
COGNITO_REGION=us-east-1
COGNITO_USER_POOL_ID=<UserPoolId stack output>
COGNITO_APP_CLIENT_ID=<AppClientId stack output>
COGNITO_STAFF_GROUP=staff
```

```powershell
$env:COGNITO_USER_POOL_ID = "PASTE-USER-POOL-ID"
$env:COGNITO_APP_CLIENT_ID = "PASTE-APP-CLIENT-ID"
python deploy/render_ecs_express_service.py
aws ecs create-express-gateway-service `
  --cli-input-json file://ecs-express-service.rendered.json `
  --region us-east-1
```

`ecs-express-service.json` also pins `ROUTING_PROVIDER=aws_location`; no
third-party routing key is required by a Crew run. Attach
`deploy/food-access-runtime-policy.json` to `FoodAccessApiTaskRole` before
deploying so the task can call the signed Routes V2 matrix and directions APIs.

Production must never use `FOOD_ACCESS_CORS_ORIGINS=*`. Localhost belongs only
in a local `.env` file. Bearer-token requests do not require CORS credentials.

## 5. Smoke test the boundary

The public feed should succeed:

```powershell
Invoke-RestMethod "$ApiUrl/api/watchdog/changes?limit=10"
```

An unauthenticated review must return `401`:

```powershell
$Body = @{
  source_scope = "chicago_farmers_markets#urban"
  record_key = "REPLACE-WITH-A-REAL-CHANGE-KEY"
  action = "acknowledged"
  note = "Staff reviewed the source issue"
} | ConvertTo-Json

Invoke-WebRequest `
  -Uri "$ApiUrl/api/watchdog/changes/review" `
  -Method Post `
  -ContentType "application/json" `
  -Body $Body `
  -SkipHttpErrorCheck
```

After signing in through the frontend, the same action sends the Cognito access
token in the `Authorization: Bearer ...` header and should return
`review_recorded`. The response explicitly states that no ranking or route was
changed automatically.
