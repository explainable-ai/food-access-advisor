# Deploy the scheduled Watchdog

This stack runs the deployed Watchdog every day at **6:00 AM America/Chicago** through:

1. Amazon EventBridge Scheduler
2. A small AWS Lambda invocation bridge
3. Amazon Bedrock AgentCore Runtime

The Lambda bridge drains the AgentCore streaming response, writes the result to CloudWatch Logs, and returns a normal success or failure to Scheduler. Failed Scheduler deliveries are retained in an encrypted Amazon SQS dead-letter queue for 14 days.

The schedule is **disabled by default** so it can be deployed and smoke-tested before automation begins.

## Prerequisites

- The Watchdog AgentCore runtime is deployed and reports `READY`.
- Your deployment identity can create or update CloudFormation, IAM, Lambda, Scheduler, SQS, and CloudWatch Logs resources.
- AWS CLI is SSO-authenticated in the same region as AgentCore.

## 1. Authenticate and set local values

From PowerShell:

```powershell
$AwsProfile = "food-access-admin"
$AwsRegion = "us-east-1"
$WatchdogRuntimeArn = "PASTE_YOUR_WATCHDOG_RUNTIME_ARN_HERE"

aws sso login --profile $AwsProfile
$env:AWS_PROFILE = $AwsProfile
$env:AWS_REGION = $AwsRegion

aws sts get-caller-identity
```

Do not commit the populated runtime ARN or other environment-specific identifiers.

## 2. Deploy with the schedule disabled

Run from the repository root:

```powershell
aws cloudformation deploy `
  --template-file .\infra\eventbridge-watchdog.yaml `
  --stack-name FoodAccessWatchdogSchedule `
  --capabilities CAPABILITY_NAMED_IAM `
  --parameter-overrides `
    AgentRuntimeArn=$WatchdogRuntimeArn `
    ScheduleState=DISABLED `
  --region $AwsRegion
```

Verify the stack:

```powershell
aws cloudformation describe-stacks `
  --stack-name FoodAccessWatchdogSchedule `
  --query "Stacks[0].{Status:StackStatus,Outputs:Outputs}" `
  --region $AwsRegion
```

Expected stack status: `CREATE_COMPLETE` or `UPDATE_COMPLETE`.

## 3. Perform one controlled invocation

```powershell
$ResponseFile = Join-Path $env:TEMP "food-access-watchdog-scheduler-response.json"

aws lambda invoke `
  --function-name food-access-watchdog-scheduler `
  --cli-binary-format raw-in-base64-out `
  --payload '{}' `
  --region $AwsRegion `
  $ResponseFile

Get-Content $ResponseFile
```

The command response should show `StatusCode: 200`. The response file should contain a `statusCode` of `200`, a runtime session ID, and a positive response length.

Inspect the invocation log:

```powershell
aws logs tail /aws/lambda/food-access-watchdog-scheduler `
  --since 15m `
  --region $AwsRegion
```

Confirm that the log contains `Watchdog completed: status_code=200`.

## 4. Check the dead-letter queue

```powershell
$DlqUrl = aws cloudformation describe-stacks `
  --stack-name FoodAccessWatchdogSchedule `
  --query "Stacks[0].Outputs[?OutputKey=='DeadLetterQueueUrl'].OutputValue | [0]" `
  --output text `
  --region $AwsRegion

aws sqs get-queue-attributes `
  --queue-url $DlqUrl `
  --attribute-names ApproximateNumberOfMessages `
  --region $AwsRegion
```

Before enabling the schedule, `ApproximateNumberOfMessages` should be `0`.

## 5. Enable the daily schedule

Use the same deployment command with `ScheduleState=ENABLED`:

```powershell
aws cloudformation deploy `
  --template-file .\infra\eventbridge-watchdog.yaml `
  --stack-name FoodAccessWatchdogSchedule `
  --capabilities CAPABILITY_NAMED_IAM `
  --parameter-overrides `
    AgentRuntimeArn=$WatchdogRuntimeArn `
    ScheduleState=ENABLED `
  --region $AwsRegion
```

Verify the schedule:

```powershell
aws scheduler get-schedule `
  --name food-access-watchdog-daily `
  --query "{State:State,Expression:ScheduleExpression,Timezone:ScheduleExpressionTimezone,Target:Target.Arn}" `
  --region $AwsRegion
```

Expected values:

- `State`: `ENABLED`
- `Expression`: `cron(0 6 * * ? *)`
- `Timezone`: `America/Chicago`
- `Target`: the `food-access-watchdog-scheduler` Lambda ARN

## Operations

To pause automation without deleting resources, redeploy with `ScheduleState=DISABLED`.

To check recent runs:

```powershell
aws logs tail /aws/lambda/food-access-watchdog-scheduler `
  --since 1d `
  --region $AwsRegion
```

To inspect a failed Scheduler delivery:

```powershell
aws sqs receive-message `
  --queue-url $DlqUrl `
  --max-number-of-messages 10 `
  --attribute-names All `
  --message-attribute-names All `
  --region $AwsRegion
```

Do not delete a DLQ message until its failure has been understood and the Watchdog run has either succeeded on retry or been rerun manually.
