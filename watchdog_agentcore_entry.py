"""AgentCore Runtime entrypoint for the Watchdog agent.

Deliberately a separate file from watchdog_agent.py, not a modification of
it: watchdog_agent.py's own `if __name__ == "__main__"` block is the
existing manual/CLI test path (`python watchdog_agent.py`, documented in
the README), and this file's job is only to expose the same
`build_watchdog()` agent behind the BedrockAgentCoreApp HTTP server that
AgentCore Runtime expects. Reuses `build_watchdog()` as-is — no agent
logic is duplicated here.

Pattern (import, decorator, and payload shape) verified directly against
the installed `bedrock_agentcore` package source
(`bedrock_agentcore.runtime.app.BedrockAgentCoreApp._handle_invocation` /
`_invoke_handler`): the framework parses the request body as JSON and
passes that parsed dict straight through as the entrypoint function's
first argument, and an async-generator entrypoint is automatically
streamed back as a server-sent-events response — so `payload.get(...)`
and `async for ... yield` below match the framework's actual contract,
not just documentation examples.

Deploy (run this yourself — needs your own AWS credentials, not something
this repo's CI or a shared sandbox can do on your behalf):

    pip install -r requirements.txt
    agentcore configure --entrypoint watchdog_agentcore_entry.py --requirements-file requirements.txt
    agentcore deploy
    agentcore invoke '{"prompt": "Run today’s recheck pass over every pending flagged tract."}'

`agentcore deploy` builds an ARM64 container in the cloud via CodeBuild and
hosts it on AgentCore Runtime — no local Docker required. `agentcore
configure`/`deploy`/`invoke` come from the `bedrock-agentcore-starter-toolkit`
package; run `agentcore --help` to confirm current flags before deploying,
since this tooling is actively evolving (AWS is migrating it towards a
separate `@aws/agentcore` npm CLI — check `agentcore configure --help`
yourself if commands here don't match what you have installed).
"""

from bedrock_agentcore import BedrockAgentCoreApp

from watchdog_agent import build_watchdog

app = BedrockAgentCoreApp()


@app.entrypoint
async def handler(payload: dict):
    """Run one Watchdog recheck pass, streaming the agent's output back.

    `payload` is this invocation's parsed JSON request body, passed
    through by AgentCore Runtime unchanged — see the module docstring for
    where that's confirmed against the framework's own source.
    """
    prompt = payload.get(
        "prompt", "Run today's recheck pass over every pending flagged tract."
    )
    watchdog = build_watchdog()
    async for event in watchdog.stream_async(prompt):
        yield event


if __name__ == "__main__":
    app.run()
