# Literature notes

Running notes for the literature review. One entry per paper: citation, a
one-paragraph summary, and its relevance to this project.

**Status: populated 2026-09-02.** Sixteen entries across the six areas.

**Every citation below was verified against a primary source** — the arXiv
abstract page, the ACL Anthology entry, or the publisher's page — before being
written down. Titles, author lists, dates and venues come from those pages, not
from recall. Papers that could not be verified are listed in §7 as leads to
chase rather than cited here. A fabricated reference in a document arguing that
deterministic checking beats confident guessing would be an unusually poor joke.

Where a venue is given, the source page stated it. Where none is given, the
paper is an arXiv preprint with no venue listed at the time of checking — said
plainly rather than guessed at.

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

---

## Two things the search changed

Stated up front because both affect how the report is written, not just what it
cites.

### The closest prior work is stronger than the proposal assumed

**CaMeL** (Debenedetti et al., 2025, §5) is a defence that separates control and
data flow around an LLM, and reports solving **77% of AgentDojo tasks with
provable security**. That is the same architectural instinct as this project —
enforcement outside the model rather than inside it — executed by the AgentDojo
authors, with a formal guarantee this project does not claim.

The report cannot present "enforcement outside the model" as the contribution.
What survives, and what §4 of `context/project-spec.md` already half-argues, is
narrower and still defensible:

- CaMeL and the design-pattern work (§5) address the **hijacked agent**. Neither
  addresses the **honestly mistaken agent** — a model getting institutional rules
  wrong with no adversary present. That second failure mode is where this
  project's novelty actually lives.
- This project's rules are **published, institutional, and not invented by the
  researcher**. Every benchmark surveyed below uses synthetic or researcher-authored
  policies.

The positioning section should say this explicitly rather than let a reader
discover CaMeL and wonder.

### "Degrades with depth" may be the wrong shape

`context/project-spec.md` §7 calls the expectation that "model accuracy degrades
with nesting depth while the evaluator stays exact" the headline result.
**Logical Phase Transitions** (Zhang et al., ACL 2026, §6) reports that reasoning
performance does **not** degrade gradually: it holds steady within a regime and
then *collapses abruptly* past a critical logical depth, which the authors liken
to a phase transition.

If that generalises, the compliance suite's rule-depth stratification should be
designed to locate a threshold rather than fit a gradient — the interesting
number is where the cliff is, not the slope. Worth deciding before the numbers
are in, because it changes how the depth strata should be spaced.

---

## Entries

### 1. Prompt injection against LLM agents

**Zhan, Q., Liang, Z., Ying, Z., & Kang, D. (2024). InjecAgent: Benchmarking
Indirect Prompt Injections in Tool-Integrated Large Language Model Agents.
Findings of ACL 2024.** arXiv:2403.02691.

A benchmark of 1,054 test cases across 17 user tools and 62 attacker tools,
testing whether tool-integrated agents follow instructions embedded in tool
output. Attack intentions are split into two classes: direct harm to the user,
and exfiltration of private data. Evaluated across 30 agents; ReAct-prompted
GPT-4 was successfully attacked 24% of the time, and success rates nearly
doubled when the injected instruction was reinforced with a hacking prompt.

*Relevance.* This is the closest published precedent for the attack-class
taxonomy in `attacks/README.md`. Its two intention classes map onto this
project's `output-injection` and `exfiltration` classes, which supports the
claim that those classes are representative rather than invented. It also
establishes that a capable model is attackable a substantial fraction of the
time — useful context for the enforcement-off arm, which currently has no
numbers of its own.

---

**Debenedetti, E., Zhang, J., Balunović, M., Beurer-Kellner, L., Fischer, M., &
Tramèr, F. (2024). AgentDojo: A Dynamic Environment to Evaluate Prompt Injection
Attacks and Defenses for LLM Agents.** arXiv:2406.13352.

A dynamic benchmark of 97 user tasks and 629 security test cases across banking,
Slack, travel and workspace domains, pairing each user task with injection tasks
to give 949 (task, injection) pairs. Reports three metrics — benign utility,
utility under attack, and attack success rate. The best agent tested reached 78%
benign utility; GPT-4o fell from 69% to 50% utility under attack.

*Relevance.* The metric structure is nearly identical to `project-spec.md` §7's
2×2, which is reassuring for the design and awkward for the novelty claim. The
utility-under-attack metric in particular is the same instinct as this project's
"benign task completion, without which the first two are meaningless". Cite it as
the model for the evaluation design rather than pretending to have arrived at it
independently.

---

**Shahriar, A., Rahman, M. N., Ahmed, S., Sadeque, F., & Parvez, M. R. (2025).
A Survey on Agentic Security: Applications, Threats and Defenses.**
arXiv:2510.06445 (v3, June 2026).

A survey covering more than 260 papers on LLM agent security, organised into
applications, vulnerabilities and defences. Argues that agentic systems are
"structurally fragile by default" and that securing them requires layered
defences across the agent lifecycle rather than isolated interventions.

*Relevance.* The fastest route to a defensible related-work section, and the
right place to check whether any area of the report's argument has been missed.
Its "structurally fragile by default" framing supports the threat model's
position that in-model defences are insufficient in principle rather than merely
weak in practice.

---

### 2. MCP and tool-use security

**Radosevich, B., & Halloran, J. (2025). MCP Safety Audit: LLMs with the Model
Context Protocol Allow Major Security Exploits.** arXiv:2504.03767.

Demonstrates that MCP-connected LLMs can be driven to malicious code execution
and credential theft, and introduces MCPSafetyScanner, a tool for auditing MCP
servers before deployment.

*Relevance.* Evidence that MCP-specific risk is documented rather than
hypothetical — the claim `project-spec.md` makes when it says the proxy "sits at
a real and newly-standard interface". Note the contrast in approach: this audits
servers ahead of time, where this project mediates calls at runtime. That
difference is worth a sentence in the report.

---

**Wang, Z., Gao, Y., Wang, Y., Liu, S., Sun, H., Cheng, H., Shi, G., Du, H., &
Li, X. (2025). MCPTox: A Benchmark for Tool Poisoning Attack on Real-World MCP
Servers.** arXiv:2508.14925.

A benchmark for tool poisoning — malicious instructions embedded in a tool's
*metadata* rather than in its output or execution — evaluated against real MCP
servers. Reports widespread susceptibility, with o1-mini reaching a 72.8% attack
success rate, and finds that more capable models are *more* vulnerable.

*Relevance.* Directly supports the `poisoned-description` attack class, which
this project currently marks `deliverable: false` because nothing implements
tool-metadata injection. MCPTox is the evidence that the class is worth
implementing, and a source for how to deliver it. The finding that capability
correlates with vulnerability is also a useful counterweight to the assumption
in §7 that "a weaker model typically violates more rules" — that expectation may
hold for the compliance suite and invert for the attack suite.

---

**Zhao, S., Hou, Q., Zhan, Z., Wang, Y., Xie, Y., Guo, Y., Chen, L., Li, S., &
Xue, Z. (2026). Parasites in the Toolchain: A Large-Scale Analysis of Attacks on
the MCP Ecosystem. IEEE Symposium on Security and Privacy 2026.**
arXiv:2509.06572.

A systematic audit identifying 12,230 tools across 1,360 MCP servers with
exploitable weaknesses, and a description of "Parasitic Toolchain Attacks" in
which legitimate tools are coordinated toward a harmful objective by instructions
embedded in the data they process.

*Relevance.* The strongest venue of anything in this review, and the citation to
use when justifying MCP as the interface worth defending. "Coordinating
legitimate tools toward a harmful objective" is precisely the composition-level
threat that `project-spec.md` §4 uses to answer the "why not validate
server-side" objection — a per-endpoint check cannot see the composition. This
paper is the evidence for that argument.

---

### 3. Existing agent-security benchmarks

*AgentDojo and InjecAgent (above) are the two central entries for this area; the
two below cover rule-following benchmarks specifically, which is the half of
this project's evaluation that the injection benchmarks do not touch.*

**Diao, L., Xu, X., Sun, W., Yang, C., & Zhang, Z. (2025). GuideBench:
Benchmarking Domain-Oriented Guideline Following for LLM Agents. ACL 2025.**
arXiv:2505.11368.

Evaluates agents on adherence to domain-specific guidelines along three
dimensions: following diverse rules, robustness to rule updates, and alignment
with human preferences. Finds substantial room for improvement in following
domain-oriented guidelines, particularly where the rules may contradict a
model's general training.

*Relevance.* The closest published analogue to this project's compliance suite,
and the clearest evidence that the second failure mode is a recognised problem
rather than a supposition. Its "rules that contradict general training" framing
fits the enrolment domain well — UTS anti-requisite semantics are not something
a model can infer from priors.

---

**Zhou, R., Hua, W., Pan, L., Cheng, S., Wu, X., Yu, E., & Wang, W. Y. (2025).
RuleArena: A Benchmark for Rule-Guided Reasoning with LLMs in Real-World
Scenarios. ACL 2025.** arXiv:2412.08972.

Evaluates rule-guided reasoning over real-world policy texts in three domains —
airline baggage fees, NBA transactions, and tax regulations — requiring
long-context understanding, logical reasoning and accurate computation.
**Providing models with external tools for oracle math and logic operations
produces notable performance gains.**

*Relevance.* **The single most important entry in this review, and the one that
most complicates the contribution.** RuleArena is the same shape as this
project's compliance half: real published rules, a domain with genuine
complexity, and the finding that an external oracle for logic improves results.
This project's exact requisite evaluator *is* that oracle. So the mechanism is
not novel — what is left is (a) the enforcement framing, where the oracle is a
gate rather than an aid the model may consult, and (b) the composition with the
adversarial half, which RuleArena does not address at all. The report should
concede the first point and lean on the second.

---

### 4. Defences inside the model

**Wallace, E., Xiao, K., Leike, R., Weng, L., Heidecke, J., & Beutel, A. (2024).
The Instruction Hierarchy: Training LLMs to Prioritize Privileged Instructions.**
arXiv:2404.13208.

Proposes training models to rank instructions by the privilege of their source —
system above developer above user above tool output — so that a conflict resolves
in favour of the more privileged instruction. Reports improved robustness
including to attack types not seen during training.

*Relevance.* The canonical statement of the comparison class. The prompting
baseline in `project-spec.md` §7 is the cheap empirical stand-in for this; the
report should be clear that a prompt-level instruction to ignore injected text is
a weaker thing than a trained hierarchy, and should not claim to have beaten the
latter by beating the former.

---

**Chen, S., Piet, J., Sitawarin, C., & Wagner, D. (2025). StruQ: Defending
Against Prompt Injection with Structured Queries. USENIX Security Symposium
2025.** arXiv:2402.06363.

Separates a prompt into a structured pair of channels — instruction and data —
and fine-tunes the model to act only on the instruction channel, so that text
arriving as data cannot be promoted to an instruction.

*Relevance.* The strongest in-model defence in this review by venue, and the
closest in-model analogue to what this project does structurally. StruQ enforces
the instruction/data boundary *inside* the model by training; this project
enforces a different boundary *outside* it by mediation. That contrast is the
cleanest way to frame the thesis in the report's introduction.

---

**Jia, Y., Shao, Z., Liu, Y., Jia, J., Song, D., & Gong, N. Z. (2025). A
Critical Evaluation of Defenses against Prompt Injection Attacks.**
arXiv:2505.18333.

Re-evaluates existing prompt-injection defences under both standard and adaptive
attacks, and checks whether they preserve the model's underlying capabilities.
Concludes that existing defences "are not as successful as previously reported".

*Relevance.* The load-bearing citation for the claim in `README.md` that
"defences inside the model are probabilistic and address neither with certainty".
Also a methodological warning aimed squarely at this project: it insists a
defence be measured against *adaptive* attacks and on retained utility. The
sixteen scenarios here are static and non-adaptive, and that is a threat to
validity the report must state rather than let a reader raise.

---

### 5. Deterministic enforcement and reference monitors

**Saltzer, J. H., & Schroeder, M. D. (1975). The Protection of Information in
Computer Systems.** Verified via the University of Virginia's hosted copy, which
records a *Communications of the ACM* 17(7) version revised to April 1975. The
form most commonly cited elsewhere is *Proceedings of the IEEE* 63(9),
1278–1308, 1975. **Confirm which edition the report should cite before
submission** — both exist and the two are routinely conflated.

States the design principles this project is an application of, including
complete mediation: "every access to every object must be checked for authority",
described as "the primary underpinning of the protection system". Also warns
against caching authorisation decisions without invalidating them.

*Relevance.* The intellectual ancestry of the whole design, and the right frame
for `context/threat-model.md`. Complete mediation is exactly what the proxy
implements and exactly what per-endpoint validation does not — which is the
answer to the "why not validate server-side" objection. Note that the cited text
does not itself use the phrase "reference monitor"; that term comes from the
Anderson report (§7, unverified).

---

**Debenedetti, E., Shumailov, I., Fan, T., Hayes, J., Carlini, N., Fabian, D.,
Kern, C., Shi, C., Terzis, A., & Tramèr, F. (2025). Defeating Prompt Injections
by Design.** arXiv:2503.18813. Widely referred to as **CaMeL**.

Builds a protective layer around the LLM that separates control flow from data
flow, so that untrusted data cannot alter program execution, and enforces
capability-based policies on tool access and data exfiltration. Reports solving
77% of AgentDojo tasks *with provable security*.

*Relevance.* The nearest neighbour to this project and the reason the positioning
section has to be careful — see "Two things the search changed" above. Read it
closely before writing the contribution statement. The useful distinction is that
CaMeL's policies concern *information flow* (may this data reach that sink),
whereas this project's concern *domain rules* (may this student enrol in this
subject). Those are different policy languages solving different problems with
the same architectural move.

---

**Beurer-Kellner, L., Buesser, B., Creţu, A.-M., Debenedetti, E., Dobos, D.,
Fabian, D., Fischer, M., Froelicher, D., Grosse, K., Naeff, D., Ozoani, E.,
Paverd, A., Tramèr, F., & Volhejn, V. (2025). Design Patterns for Securing LLM
Agents against Prompt Injections.** arXiv:2506.08837.

Proposes a set of design patterns for agents with resistance to prompt injection,
and analyses the security–utility trade-off of each through case studies.

*Relevance.* The pattern catalogue is a good check on this project's four policy
families — allowlist, argument validation, taint tracking, rate and scope limits.
If a pattern here has no counterpart in the policy set, that is either a gap or a
deliberate exclusion worth stating. The explicit security–utility framing also
supports reporting benign task completion alongside attack success rather than in
a footnote.

---

**Wang, P., Liu, Y., Lu, Y., Cai, Y., Chen, H., Yang, Q., Zhang, J., Hong, J., &
Wu, Y. (2025). AgentArmor: Enforcing Program Analysis on Agent Runtime Trace to
Defend Against Prompt Injection.** arXiv:2508.01249.

Converts an agent's execution trace into graph intermediate representations —
control flow, data flow and program dependency graphs — and applies static
analysis with a type system to detect sensitive data flows and policy violations.

*Relevance.* Another external-enforcement design, and a useful contrast on
*timing*: AgentArmor analyses the trace, where this project mediates the call
before it is forwarded. The report can use that difference to argue that
mediation prevents where analysis detects — relevant because this project's
ground truth is final database state, which a detect-after-the-fact design cannot
protect.

---

### 6. LLM reliability on rule-governed and compositional tasks

**Dziri, N., Lu, X., Sclar, M., Li, X. L., Jiang, L., Lin, B. Y., West, P.,
Bhagavatula, C., Le Bras, R., Hwang, J. D., Sanyal, S., Welleck, S., Ren, X.,
Ettinger, A., Harchaoui, Z., & Choi, Y. (2023). Faith and Fate: Limits of
Transformers on Compositionality.** arXiv:2305.18654.

Argues that transformers solve compositional tasks by "linearized subgraph
matching" rather than by developing systematic procedures, and shows that
autoregressive performance "can rapidly decay with increased task complexity".

*Relevance.* The foundational citation for the second failure mode. It supports
the claim that a model evaluating a nested boolean requisite expression is doing
pattern completion rather than executing the rule — which is the whole reason an
exact evaluator is worth comparing against.

---

**Zhang, X., Zhang, Y., Chen, Z., Yu, J., Yang, W., & Song, Z. (2026). Logical
Phase Transitions: Understanding Collapse in LLM Logical Reasoning. ACL 2026
(Main Conference).** arXiv:2601.02902.

Reports that logical reasoning performance does not degrade smoothly with
complexity: it is stable within a regime and then collapses abruptly past a
critical logical depth, analogous to a physical phase transition. Proposes
neuro-symbolic curriculum tuning as a mitigation, with modest gains (≈1.26 points
under basic prompting, ≈3.95 under chain-of-thought).

*Relevance.* **Changes what the rule-depth stratification should be looking
for** — see "Two things the search changed" above. Also the most recent work in
this review, and the one to check for a newer version before submission.

---

**Zhou, R., Hua, W., Pan, L., Cheng, S., Wu, X., Yu, E., & Wang, W. Y. (2025).
RuleArena.** *(Full citation in §3.)*

Listed again here because it belongs to both areas: it is simultaneously the
closest benchmark analogue and the strongest evidence that models struggle with
rule-guided reasoning over real policy text.

---

**Diao, L., Xu, X., Sun, W., Yang, C., & Zhang, Z. (2025). GuideBench.**
*(Full citation in §3.)*

Same reason. Its robustness-to-rule-updates dimension has no counterpart in this
project's evaluation and is worth considering — UTS handbook rules change
annually, and a defence that silently encodes 2026 rules is a real limitation.

---

## 7. Leads not yet verified

Recalled but **not confirmed against a primary source**, so deliberately not
cited above. Chase these before the report; do not cite them from this list.

- **Anderson, J. P. (1972), "Computer Security Technology Planning Study"** — the
  origin of the term *reference monitor*. Referenced indirectly by several sources
  found during the search, but the primary document was not retrieved. Needed if
  the report uses the term, which it currently does.
- **Greshake et al., "Not what you've signed up for: Compromising Real-World
  LLM-Integrated Applications with Indirect Prompt Injection"** — believed to be
  the paper that named indirect prompt injection, believed AISec 2023. Not
  verified. If real, it belongs in §1 ahead of InjecAgent as the origin citation.
- **Invariant Labs' 2025 tool-poisoning disclosure** — repeatedly described in
  secondary sources as the first public proof-of-concept for MCP tool poisoning
  and shadowing, April 2025. Appears to be vendor research rather than a
  peer-reviewed paper; cite as a technical report if at all, and only after
  reading the original.
- **A primary source for the "instruction hierarchy is ineffective on diverse
  injected prompts" claim** that appeared in secondary summaries during the
  search. Jia et al. (§4) supports the general point; this specific claim was not
  traced to its source.
- **Willison's coinage of "prompt injection"** — widely attributed, blog-based
  rather than academic. Fine to cite as a blog post if the report wants the
  terminological origin; verify the post and date.

## 8. Gaps worth noting

Areas where the search found less than expected, which are either genuine gaps
or a limitation of this review:

- **No benchmark found that uses real published institutional rules the
  researcher did not author.** RuleArena uses real policy texts (airline, tax,
  NBA) and is the closest. If nothing closer exists, the UTS requisite graph is a
  genuine methodological contribution and the report should say so plainly.
- **No work found that composes an adversarial suite with a rule-compliance suite
  in one system.** The two literatures are largely separate. That composition is
  this project's clearest structural novelty, and it is worth stating as such.
- **Nothing found on enforcement of *domain* rules at the MCP boundary
  specifically.** The MCP security work is about protocol-level threats;
  the policy work is about information flow. Domain-rule enforcement at that
  boundary appears unoccupied — though absence of evidence here is weak evidence
  of absence, given how fast this area is moving.
