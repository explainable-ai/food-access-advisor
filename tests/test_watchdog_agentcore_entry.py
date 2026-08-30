"""Contract tests for the AgentCore Watchdog entrypoint."""

import asyncio

import pytest

from watchdog_agentcore_entry import handler


async def _consume(payload):
    return [event async for event in handler(payload)]


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"prompt": None},
        {"prompt": []},
        {"prompt": {}},
        {"prompt": "   "},
    ],
)
def test_handler_rejects_non_string_or_empty_prompts(payload):
    with pytest.raises(ValueError, match="payload|prompt"):
        asyncio.run(_consume(payload))
