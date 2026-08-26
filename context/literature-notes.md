# Literature notes

Running notes for the literature review. One entry per paper: citation, a
one-paragraph summary, and its relevance to this project.

**Status: outstanding.** This is the last Weeks 1–3 deliverable still to do, and
it is on the critical path for the report rather than the code.

## Areas to cover

Derived from the argument the report has to make. Each area exists because a
specific claim depends on it.

**Prompt injection against LLM agents.** Establishes the first failure mode and
the taxonomy the attack suite uses. Needed to justify the four attack classes as
representative rather than arbitrary.

**MCP and tool-use security.** Establishes that the proxy sits at a real and
newly-standard interface, and that poisoned descriptions and tool shadowing are
live concerns rather than hypothetical ones.

**Existing agent-security benchmarks.** Needed to position the contribution
honestly: generic injection suites are crowded ground, and the report should say
what this benchmark adds rather than implying novelty it does not have.

**Defences inside the model** — instruction hierarchies, delimiting, self-checking,
refusal training. This is the comparison class. The prompting baseline in the
evaluation is the empirical version of this argument; the literature is the
conceptual version.

**Deterministic enforcement and reference monitors.** The older security literature
this project is really an application of. Useful for framing the trust boundary in
`context/threat-model.md`, and for the "why not just validate server-side" section
of the report.

**LLM reliability on rule-governed and compositional tasks.** Supports the second
failure mode — that a model applying nested boolean rules to a transcript is
unreliable in a way that scales with nesting depth. The rule-depth stratification
in the compliance suite is designed to measure exactly this, so prior work here
tells us whether the expected degradation is already established or is a finding.

## Entries

(to be populated)
