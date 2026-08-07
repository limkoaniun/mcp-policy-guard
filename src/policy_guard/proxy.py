"""The MCP proxy.

Sits between an LLM agent (MCP client) and one or more MCP tool servers.
Intercepts every message in both directions and consults the policy engine
before forwarding.

Design sketch (to be implemented):

    agent  <-- MCP -->  PolicyProxy  <-- MCP -->  tool servers

Responsibilities:
- speak MCP on both sides (stdio and/or SSE transports)
- tag every tool result with a trust label (see policies/taint.py)
- consult PolicyEngine.evaluate(call) before forwarding tool calls
- log every decision for the audit trail / experiments
"""


class PolicyProxy:
    """Transparent MCP proxy with policy enforcement. Not yet implemented."""

    def __init__(self, policy_engine, upstream_servers):
        self.policy_engine = policy_engine
        self.upstream_servers = upstream_servers

    async def run(self):
        raise NotImplementedError
