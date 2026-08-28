"""Bedrock model selection — one place, read by both agents.

Strands' own default (used automatically if you never pass `model=` to
`Agent(...)`) is a "global cross-region" inference profile
(`global.anthropic.claude-sonnet-4-6` as of this writing). Strands itself
warns that this default "is subject to change," and it also requires a
three-statement IAM policy (inference-profile ARN + in-region
foundation-model ARN + account-less global foundation-model ARN, each with
matching conditions) rather than one simple ARN — more setup surface than
this project needs.

Pinning an explicit model here instead means: a reproducible model choice
that won't silently change under you, and a single-ARN IAM policy. See
docs/bedrock-setup.md for the exact policy JSON and full setup steps.

`.env.example` already documented a `STRANDS_MODEL_ID` override — this
wires that promise up for real; before this file existed, that env var
was read by nothing.
"""

import os

from strands.models.bedrock import BedrockModel

# "us." = geo cross-region inference profile: routes within US regions only
# (simpler IAM policy than "global."), matching AWS_REGION=us-east-1 in
# .env.example. Override via STRANDS_MODEL_ID if your account needs a
# different region group (e.g. "eu.anthropic.claude-haiku-4-5-20251001-v1:0").
DEFAULT_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def build_model() -> BedrockModel:
    """Build the Bedrock model both agents share.

    Reads STRANDS_MODEL_ID from the environment (falls back to
    DEFAULT_MODEL_ID) and AWS_REGION (falls back to whatever boto3's normal
    credential chain resolves — see README > Setup). Doesn't touch AWS at
    construction time; the first real network call happens on the first
    agent invocation, so building this is safe to do even without
    credentials configured yet.
    """
    model_id = os.getenv("STRANDS_MODEL_ID", DEFAULT_MODEL_ID)
    region = os.getenv("AWS_REGION")
    kwargs = {"model_id": model_id}
    if region:
        kwargs["region_name"] = region
    return BedrockModel(**kwargs)
