# Amazon Bedrock setup for the Food-Access Advisor

This is a standalone reference — everything needed to get this project's two
agents (`agent.py`, the on-demand Advisor; `watchdog_agent.py`, the scheduled
Watchdog) actually able to call a real LLM. It's written to be handed to a
different AI assistant (or your future self) with no other context needed.

## Why this is needed at all

This project is built on the **Strands Agents SDK** (`pip install
strands-agents`), whose model provider is **Amazon Bedrock** — every `Agent(...)`
call in this codebase, plus the sub-agent inside `tools/evidence_brief.py`,
makes a real Bedrock API call the moment you run `python agent.py` or
`python watchdog_agent.py`. Nothing else in this repo needs AWS: the
Watchdog's flagged-tracts log is local SQLite, not DynamoDB, despite the
architecture diagram's DynamoDB icon (that diagram documents the *intended*
production shape, not what the MVP actually needs). Bedrock is the one real
AWS dependency standing between "the code runs" and "the agent actually
answers a question."

## What you need — checklist

1. An AWS account with permission to use Bedrock and to attach IAM policies
   (if it's a company/shared account, you may need an admin for step 4).
2. Bedrock itself needs no separate enablement — it's on by default in
   every commercial AWS region now.
3. **The one-time Anthropic model-access form.** As of an AWS change in
   late 2025, most Bedrock foundation models auto-enable, but Anthropic's
   models are the deliberate exception — your account still needs to submit
   a one-time usage acknowledgment (console or API) before it can invoke
   Claude. This is a quick form, not a multi-day approval wait.
4. **IAM permission for one specific model id.** This project pins
   `model.py` to `us.anthropic.claude-sonnet-4-6` rather than relying on
   Strands' own shifting default (see "Why one pinned model" below) — that
   choice means you only need a single-ARN IAM policy, given in full below.
5. **Credentials** on whichever machine actually runs the agents — an IAM
   access key + secret via `aws configure`, or a filled-in `.env` (copy
   `.env.example`), or IAM Identity Center/SSO if your org requires it.
6. **Region: `us-west-2`** — already this project's default
   (`.env.example`). It's one of AWS's primary Bedrock regions and
   supports the `us.` geo cross-region inference profile this project uses.

## How — step by step

1. Sign in to the AWS Console → search **Bedrock** → left nav **Model access**.
2. Find the Anthropic models. If a one-time usage form/agreement appears,
   complete it — this satisfies checklist item 3.
3. Attach this IAM policy to whichever user/role will run the agents (only
   needs the one pinned model, which is the point of pinning it):

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Sid": "InvokeClaudeSonnetViaBedrock",
         "Effect": "Allow",
         "Action": [
           "bedrock:InvokeModel",
           "bedrock:InvokeModelWithResponseStream"
         ],
         "Resource": [
           "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-sonnet-4-6",
           "arn:aws:bedrock:us-west-2:*:inference-profile/us.anthropic.claude-sonnet-4-6"
         ]
       }
     ]
   }
   ```

   Swap `us-west-2` in both ARNs if you change `AWS_REGION` / `STRANDS_MODEL_ID`
   away from the project defaults.

4. Get credentials for that IAM identity: an access key + secret is
   simplest for a hackathon submission; use IAM Identity Center (SSO)
   instead if your organization requires it.
5. On the machine that will actually run `python agent.py` /
   `python watchdog_agent.py`, either:
   - run `aws configure` and enter the access key, secret, and region
     (`us-west-2`), **or**
   - copy `.env.example` to `.env` and fill in the same three values
     (`AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`).
6. Optional: override the model or region via the `STRANDS_MODEL_ID` /
   `AWS_REGION` environment variables — `model.py` reads both, falling back
   to `us.anthropic.claude-sonnet-4-6` / whatever `boto3` otherwise resolves.

## Verify it actually works

First, confirm credentials are wired up at all (this doesn't touch Bedrock,
just AWS identity):

```bash
aws sts get-caller-identity
```

This should print an account id and ARN, not an error. Then confirm Bedrock
access specifically, independent of this project's own code:

```bash
python -c "
import boto3
client = boto3.client('bedrock-runtime', region_name='us-west-2')
resp = client.converse(
    modelId='us.anthropic.claude-sonnet-4-6',
    messages=[{'role': 'user', 'content': [{'text': 'Say OK if you can hear me.'}]}],
)
print(resp['output']['message']['content'][0]['text'])
"
```

A real reply here means the IAM policy, model access, and credentials are
all correct together — the only thing left is running `python agent.py` for
the real thing.

## Why this project pins one exact model instead of Strands' default

If you never pass `model=` to `Agent(...)`, Strands falls back to its own
default — a "global cross-region" inference profile
(`global.anthropic.claude-sonnet-4-6` as of when this was written). Strands'
own source code documents that default as "subject to change," and using it
needs a three-statement IAM policy (the inference-profile ARN, the
in-region foundation-model ARN, and an account-less global foundation-model
ARN, each with matching conditions) instead of the single ARN above. `model.py`
pins an explicit model instead so this setup is reproducible for anyone
else standing the project up — a hackathon judge included.

## Common failure modes

- **`AccessDeniedException` mentioning you don't have access to the
  model** — the one-time Anthropic form (step 2) wasn't completed, or the
  IAM policy's region doesn't match the account/region you're actually
  calling from.
- **`ValidationException` about on-demand throughput not being
  supported** — you (or something) invoked the bare model id
  (`anthropic.claude-sonnet-4-6`) instead of the `us.`-prefixed inference
  profile id; use the inference profile id, which is what `model.py`
  already defaults to.
- **Credentials found but the wrong region** — check that `AWS_REGION` (or
  your `aws configure` region) matches the region baked into the IAM
  policy's ARNs; they have to agree.

## Sources

- [Global cross-Region inference — Amazon Bedrock docs](https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html)
- [Claude Sonnet 4.6 model card — Amazon Bedrock docs](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html)
- [Amazon Bedrock simplifies access with automatic enablement of serverless foundation models (AWS, Oct 2025)](https://aws.amazon.com/about-aws/whats-new/2025/10/amazon-bedrock-automatic-enablement-serverless-foundation-models)
