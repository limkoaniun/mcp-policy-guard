# Threat model

**Last updated:** 2026-08-26 · **Specification:** `context/project-spec.md`

This project defends against two distinct failure modes with one mechanism. The
threat model has to cover both, because only one of them involves an attacker.

## Failure mode 1 — the hijacked agent

An adversary controls content the agent will read, and uses it to redirect the
agent's actions.

### Assets
- Student record data reachable through connected MCP servers
- The agent's ability to perform side-effectful actions (`enrol`, `drop`,
  `submit_erequest`)

### Attacker capabilities
- Controls content the student will upload — templates, letters, transcripts —
  and, in the retired original suite, subject descriptions
- May publish a malicious MCP server, with poisoned tool descriptions or shadowed
  tool names
- Cannot modify the agent, the proxy, or the model weights

### Attack classes
1. **Output injection** — instructions embedded in tool results
2. **Poisoned tool descriptions** — malicious instructions in a server's declared
   tool metadata
3. **Tool shadowing** — lookalike tool names overriding legitimate ones
4. **Exfiltration via tool arguments** — student data smuggled out through the
   arguments of an otherwise permitted call

## Failure mode 2 — the honestly mistaken agent

No adversary. The agent is asked to help with a task governed by deterministic
rules, and applies them incorrectly.

### Assets
- Correctness of enrolment outcomes against published policy
- The student's trust in an assistant that acts on their record

### Sources of error
- Nested boolean requisite expressions evaluated incorrectly against a transcript
- Anti-requisites overlooked
- Credit-point thresholds miscounted
- Admission requisites assumed rather than checked
- Census dates and session offerings ignored

### Why this belongs in a threat model

The consequences are identical to the adversarial case — a student enrolled in a
subject they may not take, or dropped from one they needed — and the mitigation is
the same deterministic layer. Treating only the adversarial half as a security
concern would leave the more probable failure unaddressed.

## Trust boundaries

- **Agent ⇄ proxy:** trusted
- **Proxy ⇄ tool servers:** trust labels are declared **per tool**, defaulting to
  the server's label. Per-server alone is too coarse for this domain: the
  enrolment server returns both attacker-influenced catalogue text
  (`get_subject_details`) and trustworthy computed results
  (`check_prerequisites`).
- **Tool results from untrusted servers:** always tainted
- **The model's reasoning:** never trusted to enforce a rule that can be checked
  deterministically
- **Uploaded documents:** the one untrusted tool result in the enrolment pack;
  reading one taints the run and holds later side-effecting calls for
  confirmation

That last boundary is the project's thesis stated as a trust assumption.

## Out of scope

- Model-level jailbreaks in the user's own prompt
- Compromise of the host machine
- Authentication and authorisation of the student to the assistant
- Broader eRequest workflows (leave of absence, credit recognition, special
  permission) — noted as future work
