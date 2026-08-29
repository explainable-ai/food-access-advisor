"""Lambda shim between EventBridge Scheduler and the deployed Watchdog
AgentCore Runtime endpoint.

EventBridge Scheduler *can* target bedrock-agentcore's InvokeAgentRuntime
directly as a "universal target," but that call is synchronous and
Scheduler gives up and marks the invocation failed around 30 seconds in.
A single Watchdog recheck pass loops over the whole flagged-tracts
backlog -- live Overpass calls plus a Bedrock call per tract -- so it
will routinely run longer than that. Lambda has no such 30-second limit
(configurable up to 15 minutes), so EventBridge Scheduler targets this
function instead of AgentCore directly, and this function makes the
(still synchronous) invoke_agent_runtime call on Scheduler's behalf.

Deploy this AFTER `agentcore deploy` (see deploy/EVENTBRIDGE_SETUP.md) --
it needs the resulting agentRuntimeArn.
"""

import json
import os
import uuid

import boto3

AGENT_RUNTIME_ARN = os.environ["WATCHDOG_AGENT_RUNTIME_ARN"]
DEFAULT_PROMPT = "Run today's recheck pass over every pending flagged tract."

client = boto3.client("bedrock-agentcore")


def handler(event, context):
    prompt = (event or {}).get("prompt", DEFAULT_PROMPT)

    # runtimeSessionId must be >= 33 characters; a fresh uuid4 hex (32
    # chars) plus a prefix comfortably clears that on every invocation --
    # each scheduled run is its own session, not a continued conversation.
    session_id = f"watchdog-{uuid.uuid4().hex}"

    response = client.invoke_agent_runtime(
        agentRuntimeArn=AGENT_RUNTIME_ARN,
        runtimeSessionId=session_id,
        payload=json.dumps({"prompt": prompt}).encode(),
    )

    body = response["response"].read().decode()
    print(body)  # lands in CloudWatch Logs for this Lambda -- the recheck summary
    return {"statusCode": 200, "sessionId": session_id}
