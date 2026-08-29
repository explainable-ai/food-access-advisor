# Putting Watchdog on a real recurring schedule

`watchdog_agentcore_entry.py` (deployed via `agentcore deploy`, see the
main README) hosts the Watchdog on Bedrock AgentCore Runtime, but nothing
calls it on a cadence yet. This wires up the missing piece: an EventBridge
Scheduler rule that fires monthly.

**Why not point EventBridge Scheduler straight at AgentCore?** Scheduler
can target `bedrock-agentcore:InvokeAgentRuntime` directly as a "universal
target," but that call is synchronous and Scheduler fails the invocation
if it doesn't return within ~30 seconds. A full recheck pass over the
flagged-tracts backlog (live Overpass calls + a Bedrock call per tract)
will usually take longer than that. So Scheduler instead targets a small
Lambda (`watchdog_scheduler_lambda.py`, next to this file), which has no
such limit (configurable up to 15 minutes) and makes the same
`invoke_agent_runtime` call on Scheduler's behalf.

This needs your own AWS credentials and console/CLI access -- it can't be
run from this session. Steps, in order:

## 1. Deploy the Watchdog to AgentCore Runtime (if you haven't already)

```bash
agentcore configure --entrypoint watchdog_agentcore_entry.py --requirements-file requirements.txt
agentcore deploy
```

Copy the `agentRuntimeArn` the deploy output prints -- the next steps need
it. (Run `agentcore --help` first to confirm current flags; this tooling
is evolving.)

## 2. Create the Lambda

```bash
zip watchdog_scheduler_lambda.zip deploy/watchdog_scheduler_lambda.py

aws lambda create-function \
  --function-name watchdog-scheduler-shim \
  --runtime python3.12 \
  --handler watchdog_scheduler_lambda.handler \
  --timeout 300 \
  --zip-file fileb://watchdog_scheduler_lambda.zip \
  --role <LAMBDA_EXECUTION_ROLE_ARN> \
  --environment "Variables={WATCHDOG_AGENT_RUNTIME_ARN=<AGENT_RUNTIME_ARN>}"
```

`<LAMBDA_EXECUTION_ROLE_ARN>` needs the standard Lambda basic-execution
policy (`AWSLambdaBasicExecutionRole`, for CloudWatch Logs) plus a policy
granting `bedrock-agentcore:InvokeAgentRuntime` scoped to the agent
runtime ARN:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "InvokeWatchdogAgentRuntime",
      "Effect": "Allow",
      "Action": "bedrock-agentcore:InvokeAgentRuntime",
      "Resource": "<AGENT_RUNTIME_ARN>"
    }
  ]
}
```

`--timeout 300` (5 minutes) is a starting point -- raise it (up to 900) if
a real backlog run needs longer; time one manual `python watchdog_agent.py`
run against your production-sized backlog first to pick a number with
headroom, rather than guessing.

## 3. Create the EventBridge Scheduler rule

Monthly, on the 1st at 09:00 UTC, as an example cadence -- adjust to
whatever the design canvas's "Loop design" cell actually calls for:

```bash
aws scheduler create-schedule \
  --name watchdog-monthly-recheck \
  --schedule-expression "cron(0 9 1 * ? *)" \
  --flexible-time-window '{"Mode": "OFF"}' \
  --target '{
    "Arn": "<LAMBDA_FUNCTION_ARN>",
    "RoleArn": "<SCHEDULER_EXECUTION_ROLE_ARN>",
    "Input": "{\"prompt\": \"Run today'\''s recheck pass over every pending flagged tract.\"}"
  }'
```

`<SCHEDULER_EXECUTION_ROLE_ARN>` needs `lambda:InvokeFunction` scoped to
`<LAMBDA_FUNCTION_ARN>` -- a separate role from the Lambda's own execution
role (one lets Scheduler call Lambda; the other lets Lambda call
AgentCore).

## 4. Verify

Fire it once by hand before trusting the cron:

```bash
aws scheduler get-schedule --name watchdog-monthly-recheck   # confirm it saved correctly
aws lambda invoke --function-name watchdog-scheduler-shim --payload '{}' /tmp/out.json && cat /tmp/out.json
```

Then check CloudWatch Logs for `watchdog-scheduler-shim` for the recheck
summary, and open the impact dashboard (`python dashboard.py`) to confirm
the run actually updated `flagged_tracts.db` rows.

## A note on how this was researched

`docs.aws.amazon.com` is blocked from the sandbox this was written in, so
the exact `invoke_agent_runtime` parameter names/lengths above
(`runtimeSessionId` needing >= 33 characters in particular) came from
search-result snippets, not a directly-fetched primary source. Before
wiring real credentials to this, sanity-check locally:

```bash
python -c "import boto3; help(boto3.client('bedrock-agentcore').invoke_agent_runtime)"
```
