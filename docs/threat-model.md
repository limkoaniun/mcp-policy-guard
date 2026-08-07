# Threat model (working draft)

## Assets
- User data reachable through connected MCP servers
- The agent's ability to perform side-effectful actions

## Attacker capabilities
- Controls content returned by one or more untrusted tools (web pages, emails, files)
- May publish a malicious MCP server (poisoned descriptions, shadowed tool names)
- Cannot modify the agent, the proxy, or the model weights

## Trust boundaries
- Agent <-> proxy: trusted
- Proxy <-> tool servers: per-server trust labels (trusted / untrusted)
- Tool results from untrusted servers: always tainted

## Out of scope
- Model-level jailbreaks in the user's own prompt
- Compromise of the host machine
