<!-- Generated from the published proposal artifact. Canonical formatted version: https://claude.ai/code/artifact/04b9ef0b-49f6-43b1-893c-7106216f070f -->

# Diagnostics That Abstain

**Accenture Innovation Challenge 2026 · Round 2 · BusinessIntelligence.ai**

A KPI intelligence-to-action engine that computes its numbers in SQL, argues about their causes through opposed agents, and refuses to answer when the data cannot support one.

- **Team**: Team Cognition

- **Product**: Enterprise BI Engine

- **Track**: BusinessIntelligence.ai

- **Stage**: Working prototype

- **Verification**: 82 tests passing

Every enterprise dashboard can tell you **what** moved. Almost none can tell you **why**. The ones now being built with language models will tell you why **even when they don't know** — and a confident wrong diagnosis is more expensive than no diagnosis at all.

## 01. Problem framing

Enterprise BI has solved detection and left diagnosis to people. The gap between the two is measured in days, and the obvious AI fix makes the most dangerous failure mode worse.

### Three failures, stacked

**Dashboards report movement, not cause.** A revenue chart showing a 4% drop is a question, not an answer. The work of answering it — reconciling a billing warehouse against telemetry, then against Slack threads and support tickets, across systems with different grains, calendars and refresh cadences — is manual, and it belongs to a small number of expensive analysts.

**The reconciliation is slow because the evidence is scattered and inconsistent.** MRR closes nightly; telemetry streams by the minute; support tickets arrive hourly; a newly migrated service has three days of history and no baseline at all. Nothing joins them but a human holding several tabs open.

**And the naive AI answer makes it worse.** Point a language model at the warehouse and it will generate SQL, compute a percentage, and narrate a cause. When that SQL is subtly wrong the number is still plausible, and the plausible wrong number propagates through the narrative into an executive decision with no signal that anything failed. Worse, when the evidence genuinely supports two contradictory explanations, an unconstrained model picks one — because fluent text is what it optimises for, and hedging reads as weakness.

> **The failure that matters**
> 
In enterprise diagnosis the expensive error is not *slow*. It is *confidently wrong*. A team that rolls back the wrong service, or discounts to defend against a competitor who was never the cause, has spent real money acting on a fluent sentence. Any system that cannot say **"I don't know"** will eventually say something false with total conviction.

The Round 2 brief names this directly: the LLM must not be treated as the source of quantitative truth, and the system must communicate uncertainty — including abstaining when evidence is contradictory or insufficient. We built the architecture around that constraint rather than adding it afterwards.

## 02. Solution design

One architectural decision drives everything else: the mathematical source of truth is SQL, the language models reason over numbers they cannot alter, and the decision to stay silent is enforced in Python — above the model layer, where no prompt can reach it.

### The pipeline

| Stage | Layer | Responsibility |
|---|---|---|
| Semantic contract | declarative | A YAML KPI dictionary: definition, calculation, grain, refresh cadence, lineage, drivers, materiality thresholds and access rules. Read at runtime — changing a definition changes behaviour, not just documentation. |
| Watcher | SQL / Python | Every value, delta, percentage, baseline mean, standard deviation, z-score, materiality test and driver contribution. No model is constructed at this stage. |
| Researcher | retrieval + LLM | Vector retrieval over the unstructured corpus, filtered to the anomaly's time window, then a factual summary of what was found. |
| Bear | LLM | Argues the strongest honest case for an internal, controllable cause. |
| Bull | LLM | Argues the strongest honest case for an external, market cause. |
| Arbitrator | LLM | Did not argue, so it can judge. Returns a confidence distribution across both hypotheses, not a verdict. |
| Safety gate | Python | Three independent checks. Decides whether a recommendation may exist at all. |
| Storyteller | LLM | Persona-specific narrative and the seven-field action. Only constructed if the gate passes. |
| Validator | Pydantic | Rejects malformed output rather than coercing it. Nothing unvalidated reaches the screen. |

### Why an adversarial debate, and not one prompt

A single model asked for a balanced view optimises for a balanced-*sounding* answer and tends to anchor on whichever hypothesis it stated first. Separating the roles makes each agent accountable for not letting its cause be dropped, and — critically — the agent that decides is not an agent that argued. The Arbitrator has no stake in either position, which is what makes its split meaningful as a measure of *evidential conflict* rather than a summary of its own reasoning.

The debate explains causes. It does not weigh them. Ranking is arithmetic, computed in SQL before any agent speaks.

### Three gates, all outside the model

- **Confidence below threshold → abstain.** The Storyteller is never constructed.
- **Attribution non-separable → abstain, regardless of confidence.** If the schema cannot decompose the movement, a 90%-confident verdict still cannot pass.
- **Unparsable arbitration → confidence zero → abstain.** The system fails closed, never open.

> **The load-bearing property**
> 
A language model cannot talk its way past a function it is never handed. On the abstain path the recommendation agent is not prompted more carefully — it is **not instantiated**. This is the difference between a guardrail and a request.

### Security posture in the prototype

Role entitlements are enforced at the *query layer*, not the render layer. When the engineering persona is active, the restricted financial query is never issued, so the value never enters application state, the agent context, or the served markup. Hiding a field that has already been fetched is not authorisation; four tests assert the value is genuinely absent, including a raw-string search of the rendered page.

The persona selector is a demonstration mechanism, not authentication, as the technical requirements specify. What transfers to production is where enforcement sits — swap the selector for an identity provider and the same check becomes row-level policy in the warehouse without the application logic changing shape.

## 03. Business case & impact

Measured from the working prototype, not projected. Where we model forward, the assumptions are stated so a reader can substitute their own.

### What the prototype actually measures

**8.2 ms** — 
Median deterministic path — 4 KPIs computed and evidence retrieved

**92.2%** — 
Internal driver share, attributed in SQL for the multi-factor incident

**~5.5k** — 
Median tokens per diagnosis across the four run types

**82** — 
Acceptance tests passing, backend and UI

Latency above covers the deterministic layer only — SQL, statistics and vector retrieval — measured over 20 runs. End-to-end latency in live mode is dominated by model round-trips and is not claimed here, because the honest number depends on the deployed model and we will not quote one we have not measured.

### Where the value comes from

Three sources, in descending order of confidence that they are real:

- **Analyst time reclaimed on diagnosis.** The manual cross-referencing work is what the pipeline automates. This is the most directly attributable saving.
- **Faster time-to-remediation.** A payment failure diagnosed in minutes rather than at end of day is a shorter window of failing charges. The value scales with transaction volume and is highly business-specific.
- **Wrong actions avoided.** The hardest to measure and plausibly the largest. Every abstention is a discount campaign not launched against a competitor who was not the cause, or a rollback not performed on a service that was not broken.

> **On the ROI arithmetic**
> 
We are deliberately not presenting a headline ROI multiple. The inputs that would drive it — analyst loaded cost, incident frequency, transaction exposure per hour — are properties of the customer, not of this product, and any figure we invented would be a number about an imaginary company. The structure below is the model; a pilot supplies the values.

| Value driver | Formula | Measured by the prototype | Needs a pilot |
|---|---|---|---|
| Diagnosis labour | incidents × analyst-hours × rate | Pipeline replaces the cross-referencing step | Incident rate, loaded cost |
| Remediation speed | Δ hours × exposure per hour | Diagnosis available at detection time | Exposure per hour |
| Errors avoided | abstentions × cost of wrong action | Abstain path verified and reproducible | Base rate of wrong actions today |
| Cost to serve | tokens × model rate | Metered per run in the UI footer | Production model choice |

### Cost behaviour

The economics are favourable in a specific, defensible way: **cost scales with incidents, not with data volume or dashboard traffic.** The deterministic layer is ordinary analytical SQL and scales like the warehouse beneath it. The expensive layer — the debate — only runs on movements the watcher has already flagged material by both a statistical and a business test. A five-agent exchange over a bounded context object is a fixed, predictable unit cost, and the console displays that cost per diagnosis to the operator rather than burying it in a monthly bill.

## 04. Target users

Two personas are implemented end to end, receiving different data, different narratives and different actions from the same incident.

| | VP of Revenue | Director of Engineering |
|---|---|---|
| Question they ask | How much did we lose, and what recovers it? | What broke, and how do I roll it back? |
| Financial KPIs | Full access | Query not executed |
| Driver ranking | Shares and dollar exposure | Shares only |
| Narrative | Business impact, commercial action | Root cause, component, rollback procedure |
| Owns the levers | Pricing, retention offers, customer comms | API versioning, rollback, deploy freeze, capacity |

### Who buys, who uses, who must be convinced

The **buyer** is typically the executive whose metric is deteriorating — the VP persona — because they feel the pain of slow diagnosis most directly. The **daily user** is more often the analyst or engineering lead who currently performs the manual reconciliation. These are different people with different success criteria, and a deployment that satisfies only the buyer gets abandoned by the user within a quarter.

The third constituency decides whether it ships at all: **the data governance function**. Their question is not whether the diagnosis is clever but whether entitlements hold, whether every conclusion is traceable to its sources and method, and whether an auditor can reconstruct why the system said what it said. Query-layer enforcement, the semantic contract, per-KPI lineage and the full debate transcript exist for that conversation.

Natural expansion personas — Customer Success on churn, FinOps on cloud spend, Supply Chain on fulfilment — need no architectural change, only additional entries in the semantic contract. That is the intended shape of the product: the engine is domain-neutral, the contract is where the domain lives.

## 05. Key risks & mitigations

Ordered by our honest assessment of what breaks first on real data. Where a mitigation is designed but not yet built, it says so.

`HIGHEST` 
#### Retrieval quality on a real corpus

The prototype's corpus is six curated documents where the relevant evidence is guaranteed present and unambiguous. A real Slack workspace is mostly noise, contains contradictory opinions from people who were guessing, and may simply not contain the evidence. The failure is quiet: agents argue confidently over an incomplete picture.

**Mitigation**Retrieval quality is the first pilot metric, not an afterthought. The time-window filter already discards topically-similar but temporally-irrelevant evidence, and the system reports how many documents fell inside the window — an empty or one-sided result is visible rather than silently tolerated. Evidence-sufficiency as an explicit fourth gate is the next increment.

`HIGHEST` 
#### Confidence is not calibrated

The Arbitrator's score is an ordinal judgement, not a calibrated probability. We do not claim otherwise, and a system whose safety property appeared to depend on calibration would be making a promise it cannot keep.

**Mitigation**The safety property depends on the threshold's *location* — in Python, failing closed — not on calibration. The second gate reinforces this: non-separable attribution abstains regardless of the score. Calibration itself requires outcome data, which is exactly what the human-in-the-loop layer accumulates: every Accept, Modify and Reject is a labelled instance.

`HIGH` 
#### Prompt injection through ingested content

The Researcher reads Slack messages and support tickets. Both are written by people, and support tickets are written by people *outside the company*. A crafted ticket is a direct path to instructions entering agent context.

**Mitigation**Structurally reduced but not eliminated. Agents hold no tools and no code execution, so a successful injection cannot reach the database, exfiltrate data, or alter a computed number — the blast radius is the narrative text. Production requires treating retrieved content as untrusted data with explicit delimiting, plus output screening before display. *Not yet implemented; scoped for Phase 1.*

`HIGH` 
#### Automation bias — over-trust in the confident case

A system that abstains well trains users to trust it when it does not abstain. That is the intended effect and also the danger: a 92% verdict may be acted on without the scrutiny it deserves.

**Mitigation**Confidence is displayed as a two-sided split rather than a single reassuring number, the losing hypothesis stays visible, and the full debate transcript plus per-KPI method and lineage are one click away. The human-in-the-loop step is a required action, not a dismissible toast.

`MEDIUM` 
#### Sensitive data in unstructured evidence

Slack and ticket text carries customer names, emails and occasionally credentials. Embedding it wholesale into a vector store creates a retrieval surface that ignores the entitlements enforced so carefully on the structured side.

**Mitigation**The prototype's corpus is synthetic, so nothing sensitive exists to leak. Production needs PII redaction at ingestion and entitlement metadata carried on each document so retrieval respects the same contract the SQL layer does. *Designed, not built — Phase 1.*

`MANAGED` 
#### Quantitative hallucination

The classic failure of LLM-on-warehouse systems: a fluent, wrong number.

**Mitigation**Structurally addressed. Agents receive computed values as read-only facts and are explicitly barred from recalculating; the action card is Pydantic-validated field by field rather than parsed from prose; and the arithmetic runs with no dependency on the model at all — set the API key to garbage and every KPI figure is still correct. Residual exposure is free-text narrative, for which templating from validated fields is the production answer.

`MANAGED` 
#### Cost drift at scale

Multi-agent systems consume tokens quadratically if debates are allowed to run long.

**Mitigation**Turn counts are bounded, the context object is bounded, and the debate only runs on movements already flagged material by two independent tests. Per-run cost is metered and displayed rather than discovered in a monthly invoice.

## 06. Phased roadmap

Each phase ends with something falsifiable. We would rather be told the retrieval is inadequate in a pilot than discover it after a production rollout.

**Phase 0Complete**

**Prototype — the architecture is real**

Deterministic watcher over DuckDB, local vector retrieval, five-agent AutoGen debate, three-gate safety layer, query-layer entitlements, seven-field validated action contract, human-in-the-loop feedback written to a persistent graph, and runtime telemetry. Three scenarios reproduce end to end; 82 acceptance tests pass.

**Phase 1Pilot**

**One customer, one KPI family, real evidence**

The point of the pilot is to break the retrieval assumption on purpose, on a narrow surface where being wrong is cheap.

- Connect two live sources — one warehouse, one communication channel — behind read-only credentials
- PII redaction and per-document entitlement metadata at ingestion
- Prompt-injection handling: retrieved content delimited as untrusted, output screened
- Evidence sufficiency as an explicit fourth gate
- Populate the semantic contract with the customer's real definitions — usually the step that surfaces the fact that two departments define the same KPI differently

**Exit criterion:** analysts agree with the diagnosis, or explain why not, on a majority of real incidents — and the abstention rate is neither zero nor most of them.

**Phase 2Production**

**Hardening, memory recall, and a real front end**

- Identity provider replaces the persona selector; entitlements move to warehouse row-level policy
- **The memory graph starts being read.** Today it is written; Phase 2 seeds new diagnoses from validated structural matches so recurring incidents resolve against known history
- Confidence calibrated against accumulated human verdicts
- Narrative templated from validated fields, closing the last free-text surface
- React console replacing Streamlit; audit log export to standard enterprise tooling

**Phase 3Scale**

**Domain expansion through the contract, not the code**

- New KPI families — cloud spend, fulfilment, support load — added as semantic contract entries
- Continuous watching replaces on-demand runs; the engine proposes which movements deserve a debate
- Cross-incident pattern detection over the accumulated memory graph

Deliberately excluded from every phase: autonomous execution of recommended actions. A system whose central claim is that it knows when to stay silent should not also be the system that acts unsupervised.

## 07. Why this wins on the criteria

| Criterion | Our claim |
|---|---|
| Creativity | Adversarial dialectic replaces single-prompt summarisation, and the arbitration produces a confidence distribution — which turns the abstain state from a hedge into a visible, structural outcome. |
| Practicality | It runs. Three scenarios reproduce from a clean clone, 82 tests pass, entitlements are enforced where they cannot be bypassed, and cost is metered per diagnosis. The limitations are named rather than hidden. |
| Fresh thinking | The differentiator is not that the system answers. It is that it is architecturally incapable of answering when it should not — and the control that guarantees this sits outside the model, where no prompt, jailbreak or model upgrade can move it. |

Deterministic where it must be. **Adversarial** where it helps. **Silent** where it should be.

Team Cognition · Enterprise BI Engine
Detailed Business Proposal · Round 2
