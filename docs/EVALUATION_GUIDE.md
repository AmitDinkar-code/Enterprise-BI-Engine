<!-- Generated from the published playbook artifact.
     Canonical formatted version:
     https://claude.ai/code/artifact/22aadbf7-ebab-4dce-984b-d1e325227083 -->

# Prototype Evaluation & Demonstration Guide

**Enterprise BI Engine — Accenture Innovation Challenge 2026, Round 2**

QA checklist, Accenture-brief coverage record, demo video script and judge
Q&A defence for the built prototype.

---

## A. Document conflicts — resolved in the build

Five places where the six documents disagree with each other or with Build Step 6. All five are now resolved in the codebase and recorded in the README's *Documented deviations* section — a judge who spots the conflict finds the decision already written down. Kept here because you will be asked about each one.

#### [CLOSED] HITL feedback never reaches Neo4j

PRD `FR-H-02` and `AC-07` require the Accept/Modify/Reject outcome to be persisted as a Neo4j relationship. TRD `TR-004` mandates local Neo4j. Implementation Plan Step 6 lists "Ensure Accept updates the specified Neo4j persistent-memory relationship." But Build Step 6 §6 states the callback "currently simulates persistence with the required toast; no Neo4j network operation is introduced" and the acceptance checklist ends with "No Neo4j network dependency was added."

As built, the toast says "logged to Persistent Memory Graph" while nothing is written. That is the one claim in the whole demo a judge can falsify by asking to see the graph — and `neo4j` is already in `requirements.txt`, so it reads as unfinished rather than out of scope.

> **Built**Write the real relationship inside a `try/except`, falling back to the same toast if the local instance is down. One `MERGE` is enough: a `Diagnosis` node keyed by scenario + driver, a `Persona` node, and a `:VALIDATED {outcome, ts}` edge between them. Then screenshot Neo4j Browser showing the edge and put it in the README — that single image converts AC-07 from a claim into evidence.

#### [CLOSED] PRD says Next.js; TRD forbids it

PRD `FR-U-01` and `AC-08` require a Next.js frontend. TRD `TR-005` requires Streamlit and lists "Next.js / React — do not implement" on the rejected-technology list. App Flow, UI/UX Brief and all six build steps are Streamlit.

TRD §Document Control instructs the agent to stop and flag conflicts rather than choose silently. You have chosen — correctly, since the TRD is the later engineering contract — but the PRD still carries an acceptance criterion your prototype fails on its face.

> **Built**Add a "Documented deviations" block to the README and one line in the proposal: *AC-08 superseded by TR-005; Streamlit is the approved MVP frontend, Next.js is the Phase-2 console.* Put it in your phased roadmap so it reads as a decision, not an oversight.

#### [CLOSED] Three different names for the same seven fields

PRD §6 fixes the canonical key set with spaces — `"controllable lever"`, `"expected impact"`, `"monitoring plan"` — and `AC-10` says "exactly the required keys." App Flow §7.2 lists them as `lever` and `impact`. Build Step 4 defines the Pydantic model in snake_case: `controllable_lever`, `expected_impact`, `monitoring_plan`.

> **Built**Keep snake_case as the Python field names — they must be valid identifiers — and add Pydantic `Field(alias=...)` plus `model_config = ConfigDict(populate_by_name=True)` so the serialized JSON emits the PRD's exact keys. Render with `model_dump(by_alias=True)`. One line of config satisfies AC-10 literally, and the `st.json` block in the demo then shows keys a judge can diff against the PRD.

#### [CLOSED] RBAC enforced at render time, not at query time

PRD `SEC-04`: "hiding a field in the UI is not sufficient authorization." TRD §7: "UI rendering and data selection must both respect the persona state." Build Step 3 §4 describes the shell opening DuckDB and retrieving `billing_mrr.total_mrr` unconditionally, with the persona check happening on the MRR card at render.

That means the restricted value is fetched, held in the process, and only suppressed at paint. It is the exact failure mode SEC-04 names, and it is the first thing a technically-minded judge will probe.

> **Built**Move the persona check above the query: for Director of Engineering, never execute the `billing_mrr` SELECT and never place MRR in `st.session_state`. Then say so out loud in the demo — "the query doesn't run" is a far stronger line than "the card is hidden," and it costs you four lines of code.

#### [CLOSED] Estimated cost will render as $0.0000

Build Step 4 §6: if no cost rates are supplied, "the default estimate is $0 rather than inventing a provider price." Defensible engineering — but `AC-11` and `TR-012` put Estimated Cost (USD) in the footer as a visible product contract, and a zero reads on camera as a broken metric.

> **Built**Set the USD-per-million-token variables in `.env` before filming and commit them to `.env.example` with a comment naming the model they correspond to. A footer reading `$0.004182` is one of the cheapest credibility signals in the whole run.

---

## B. Where the build docs fall short of the Accenture brief

Your six documents are narrower than the Round 2 brief they were derived from. The brief lists ten "minimum prototype expectations" and the PRD carries six of them. The strategy transcript marked all eleven alignment items *Complete* — but that graded the *plan*, not the PRD and TRD the build actually follows, and several never survived the translation. That mismatch is the dangerous part: the team believes it is covered on items the build docs silently dropped.

Every row below is something the brief scores you on. All are now implemented — the table records what was missing and where it now lives, because the gap is what a judge probes and the fix is your answer.

Judges at the Grand Finale may have your Round 1 deck in front of them. The confidence distribution is now real. The 50 ms structural-recall claim is not, and the proposal must say so rather than let the deck stand.

#### [PROMISE] "Confidence Probability Distribution"

The Round 1 slide says both agents "present empirical evidence to generate a Confidence Probability Distribution." The prototype emits a single scalar confidence from the Arbitrator.

> **Built**Either have the Arbitrator return a split across the two hypotheses — which also makes the 50/50 abstain visually self-evident — or retire the phrase and say "confidence score" consistently in the proposal and the video. Do not leave the deck claiming a distribution the demo cannot show.

#### [OPEN] Sub-50ms graph similarity retrieval for recurring incidents

Round 1 promised the Persistent Memory Graph would match structural signatures of recurring anomalies "in under 50 milliseconds." The MVP writes feedback to Neo4j but never reads it back — and per Build Step 6 it does not currently write either.

> **Still open — scope it as roadmap**The MVP now *writes* the graph (and `recall_validated()` reads the journal back), but no diagnosis is yet seeded from prior structural matches, and no latency claim is measured. Say “the MVP builds the memory, Phase 2 queries it” in the proposal and drop the 50 ms figure until you can show it.

| Brief requirement | Where it lives now | Verdict | Now implemented in |
|---|---|---|---|
| Semantic layer — KPI dictionary covering calculations, definitions, drivers, thresholds, access restrictions, lineage | Designed in the strategy transcript as the "KPI Semantic Contract", then never carried into the PRD, TRD, App Flow or any build step. | Built | `config/semantic_contract.yaml` — all four KPIs with definition, calculation, grain, cadence, lineage, drivers, materiality and access. Read at runtime by the watcher; four tests assert every facet is present. |
| Ranking the drivers behind a KPI movement | The brief's core objective says "identifying and *ranking* the drivers." Your output contract has one `driver` string. Scenario 1 has two simultaneous causes and no way to say which mattered more. | Built | `watcher/deterministic.py` → `_attribute_drivers()`. Measured adverse exposure per signal, ranked: **92.2% internal / 7.8% external**. Computed in SQL, shown in the narrative. |
| Traceability — source freshness, analytical method used, contribution, lineage shown in the UI | You show narrative, confidence and the debate transcript. Freshness, method and lineage appear nowhere in the App Flow. | Built | Caption under every KPI card — source table, grain, refresh cadence, method, z-score or *z withheld*, and a `LOW POWER` flag where the baseline is too thin. |
| LLM vs non-LLM processing breakdown | Enforced as an architectural principle throughout the TRD, but never produced as an artifact a judge can look at. | Built | README architecture diagram, plus a live *Execution trace* expander in the UI listing what Python decided versus what the models wrote. |
| Runtime telemetry: *model calls*, latency, tokens, cost | PRD §7 and TR-012 specify three of the brief's four metrics. Model-call count is missing from every document. | Built | `telemetry/meter.py` counts calls; the sidebar footer now shows latency, **model calls**, tokens and cost. |
| Materiality — prioritising movements by business impact *and* statistical significance | The watcher computes a z-score, which is the statistical half. Business-impact ranking is absent. | Built | Every KPI carries a two-part materiality test in the contract — statistical *and* business — evaluated in the watcher and reported per KPI. |
| 3–5 connected KPIs, 2–3 sources, differing grains and refresh cadences | Four KPIs across DuckDB and ChromaDB at daily, minute and hourly grain — seeded correctly in Build Step 2. | Covered | Grain and refresh cadence render on each card, so the heterogeneity is visible rather than asserted. |
| Personas · multi-factor · abstain · sparse history · RBAC · structured action · feedback learning | All specified in the PRD and built across Steps 3–6. | Covered | All built. The Neo4j write behind “feedback learning” is now real (Section A). |

> **How this was verified —** case-insensitive search across all ten build PDFs. lineage **0** · freshness **0** · contribution **0** · "model call" **0** · materiality **0** · rank **0** · cadence **0** · caching **0** semantic **1** — "Required semantic meaning", a column header in the TRD output contract, not a semantic layer. dictionary **1** · grain **1** — "session-state dictionary" in Step 5 and a table column header in Step 2. All three are false positives.

---

## C. Evaluation checklist

Nine gates, 63 checks, traced to the requirement IDs in your own documents. Run them in order — a failure in an early gate invalidates the evidence produced by a later one. Progress is saved in this browser only.

### G1 — Environment & repository hygiene

*Traces to: TR-014 · TR-016 · TR-017 · Step 1*

| | ID | Check | How to verify | Pass condition |
|---|---|---|---|---|
| [ ] | `E-01` | requirements.txt holds exactly the seven approved dependencies | `cat requirements.txt` | **Pass:** streamlit, pyautogen, duckdb, chromadb, neo4j, pydantic, python-dotenv — nothing else |
| [ ] | `E-02` | No rejected technology anywhere in the tree | `grep -riE "langgraph\|langchain\|pinecone\|supabase\|nextauth\|from react\|next\.js" . --include=*.py --include=*.txt --include=*.toml` | **Pass:** zero hits outside documentation prose |
| [ ] | `E-03` | Streamlit theme matches the four exact values | `cat .streamlit/config.toml` | **Pass:** #A100FF / #FFFFFF / #F4F6F8 / #0F1115 |
| [ ] | `E-04` | Cold start from a clean virtualenv raises no exception | Fresh venv → `pip install -r requirements.txt` → `streamlit run app.py` | **Pass:** page renders, terminal shows no traceback |
| [ ] | `E-05` | Secrets and local databases are git-ignored | `git check-ignore -v .env chroma_db enterprise_bi.duckdb` | **Pass:** all three matched by a .gitignore rule |
| [ ] | `E-06` | No API key has ever been committed *(check history, not just HEAD)* | `git log -p \| grep -nE "sk-[A-Za-z0-9]{20}"` | **Pass:** no match; if there is one, rotate the key and rewrite history |
| [ ] | `E-07` | .env.example documents every variable the app reads | Diff `os.getenv` call sites against `.env.example` | **Pass:** OPENAI_API_KEY plus both token-cost rates present, no real values |

### G2 — Seeded data determinism

*Traces to: AC-12 · TR-013 · Step 2*

| | ID | Check | How to verify | Pass condition |
|---|---|---|---|---|
| [ ] | `D-01` | setup_db.py is idempotent — two runs, identical state | `python setup_db.py > a.txt; python setup_db.py > b.txt; diff a.txt b.txt` | **Pass:** empty diff, no duplicate rows or duplicate Chroma collections |
| [ ] | `D-02` | billing_mrr carries the exact seeded anomaly | `SELECT * FROM billing_mrr ORDER BY 1;` | **Pass:** 08-24 = 1,000,000 · 08-25 = 960,000 · failed_transactions_usd = 45,000 |
| [ ] | `D-03` | platform_telemetry contains the 10:02 gateway spike | `SELECT * FROM platform_telemetry ORDER BY ts;` | **Pass:** gateway_timeout_errors peaks at 500 on 2026-08-25 10:02 |
| [ ] | `D-04` | Sparse-history constraint is exactly three calendar days | `SELECT COUNT(DISTINCT CAST(ts AS DATE)) FROM aws_cloudwatch_latency;` | **Pass:** returns 3, spanning 2026-08-23 → 2026-08-25, spike on the 25th |
| [ ] | `D-05` | Both Chroma collections hold their two documents | `collection.count()` on zendesk_tickets and slack_engineering | **Pass:** 2 and 2 — one internal-cause and one external-cause document in each |
| [ ] | `D-06` | Retrieval actually discriminates between the two hypotheses | Query "payment gateway timeout", then "competitor discount campaign" | **Pass:** each query returns its matching document at rank 1, not the same doc twice |
| [ ] | `D-07` | Scenario 3 retrieval degrades honestly | Run the Researcher for the AWS latency window and read what comes back | **Pass:** weak or absent evidence is reported as such — no gateway/competitor doc smuggled in as AWS evidence |

### G3 — The LLM boundary — your headline claim

*Traces to: AC-04 · TR-007 · TR-008 · TR-009*

| | ID | Check | How to verify | Pass condition |
|---|---|---|---|---|
| [ ] | `B-01` | All KPI arithmetic lives in *query_kpi_metrics()* | Read agents.py: every %, mean, stdev, z-score, total computed before the chat starts | **Pass:** no arithmetic anywhere downstream of the first agent message |
| [ ] | `B-02` | Agents hold no tool that could reach the database | `grep -n "code_execution_config\\|register_function\\|function_map" agents.py` | **Pass:** code execution disabled on every agent; no SQL-capable function registered |
| [ ] | `B-03` | *Kill-the-LLM test* — math survives with no model access | Set `OPENAI_API_KEY=invalid`, then call `query_kpi_metrics(1)` directly | **Pass:** returns −4.0% and the z-score unchanged. This is the single strongest artefact you own — capture the terminal output |
| [ ] | `B-04` | System prompts explicitly forbid recalculation | Read each agent's system message | **Pass:** Bear, Bull, Arbitrator and Storyteller are all told to treat supplied figures as authoritative |
| [ ] | `B-05` | Every number in the narrative traces to the watcher payload | Pick three figures from the rendered narrative and grep for them in the watcher context object | **Pass:** exact matches — no rounded, restated or invented values |
| [ ] | `B-06` | Sparse history does not break the statistics | Run Scenario 3; watch for ZeroDivisionError, NaN or inf from a 3-point sample | **Pass:** guarded path returns a finite value or an explicit insufficient-history flag; workflow never aborts (AC-03) |
| [ ] | `B-07` | Deterministic layer has its own unit tests | `pytest tests/ -q` | **Pass:** known input → expected delta, stdev and z-score, with no network call in the test path (TRD §15) |

### G4 — Agent contract & strict structured output

*Traces to: AC-06 · AC-10 · TR-001 · TR-010*

| | ID | Check | How to verify | Pass condition |
|---|---|---|---|---|
| [ ] | `S-01` | All five agent roles are instantiated and speak in order | `python agents.py`, read the transcript | **Pass:** Researcher → Bear → Bull → Arbitrator → Storyteller |
| [ ] | `S-02` | GroupChat completes with zero human input | `grep -n "human_input_mode" agents.py` | **Pass:** "NEVER" on every agent; the run never blocks on a prompt |
| [ ] | `S-03` | Bear and Bull genuinely disagree | Read both positions in the Scenario 1 transcript | **Pass:** Bear argues Stripe gateway timeout, Bull argues competitor discounting — not two paraphrases of one cause |
| [ ] | `S-04` | Pydantic model enforces seven fields and a bounded confidence | Inspect `ActionRecommendation` | **Pass:** all seven required; confidence is `int` constrained to 0–100 |
| [ ] | `S-05` | *Negative test* — malformed output is rejected, never coerced | Stub the Storyteller reply with a missing field, then with `confidence: 150` | **Pass:** ValidationError → RuntimeError; the UI shows an error state, never a half-filled card |
| [ ] | `S-06` | Serialized keys match the PRD canonical set exactly | Compare `model_dump(by_alias=True)` keys against PRD §6 | **Pass:** driver, controllable lever, action, expected impact, owner, confidence, monitoring plan (AC-10) |
| [ ] | `S-07` | Storyteller preserves the Arbitrator's confidence value | Compare the arbitration confidence in the transcript with the card's confidence field | **Pass:** identical — the Storyteller may not restate or round it (TRD §6) |
| [ ] | `S-08` | Scenario 1 identifies *both* simultaneous causes | Read the narrative and driver field | **Pass:** internal Stripe timeout *and* external competitor campaign both named (AC-01) — a single-cause answer fails |

### G5 — Persona RBAC

*Traces to: AC-09 · SEC-01–04 · TR-006 · FLOW-02/03*

| | ID | Check | How to verify | Pass condition |
|---|---|---|---|---|
| [ ] | `R-01` | VP of Revenue sees the numeric MRR card | Default load | **Pass:** $960,000 rendered alongside the other three KPIs |
| [ ] | `R-02` | Director of Engineering sees the restricted placeholder | Switch the persona selectbox | **Pass:** `[RESTRICTED]` with delta "RBAC Enforced" in the MRR position |
| [ ] | `R-03` | Switching persona re-renders immediately | Toggle back and forth without touching Run Diagnostics | **Pass:** KPI grid updates on the rerun, no second click needed |
| [ ] | `R-04` | *SEC-04* — restricted data is never fetched, not merely hidden | With Engineering active, inspect the code path and `st.session_state`; search the served page for "960" | **Pass:** the billing_mrr query does not execute and the value appears nowhere in state or markup |
| [ ] | `R-05` | Narrative detail is persona-specific, not relabelled | Run Scenario 1 as each persona and compare | **Pass:** VP gets business impact and business action; Engineering gets root cause and rollback detail |
| [ ] | `R-06` | Restricted persona's action card names an appropriate owner | Read the `owner` field on the Engineering run | **Pass:** maps to a supported persona; no revenue figure leaks through the impact field |
| [ ] | `R-07` | Exactly two personas and three scenarios exist | Open both sidebar controls | **Pass:** no third persona, no fourth scenario, no login flow (FLOW-02, FLOW-04) |

### G6 — Abstain safety gate

*Traces to: AC-02 · TR-011 · FLOW-07/08 · Step 6*

| | ID | Check | How to verify | Pass condition |
|---|---|---|---|---|
| [ ] | `A-01` | Scenario 2 returns confidence 50 and status abstain | Run Scenario 2 from the CLI and read the payload | **Pass:** `status="abstain"`, `structured_action=None` |
| [ ] | `A-02` | The Storyteller is never invoked below the threshold | Instrument the Storyteller entry point with a counter or log line | **Pass:** call count is 0 on the abstain path — the gate is in Python, upstream of the model |
| [ ] | `A-03` | The banner uses the exact specified wording | Compare rendered text to App Flow §8.2 character for character | **Pass:** "Low Confidence: Evidence is contradictory. System is abstaining from automated action recommendations." |
| [ ] | `A-04` | No recommendation and no HITL controls in the abstain state | Scan the whole abstain page | **Pass:** no action card, no JSON block, no Accept/Modify/Reject buttons |
| [ ] | `A-05` | Conflicting evidence and the manual-review prompt are shown | Read the body below the banner | **Pass:** internal-suggests-X / external-suggests-Y stated, user directed to investigate |
| [ ] | `A-06` | Transcript stays available for human investigation | Open the expander in the abstain state | **Pass:** full Bear/Bull exchange visible — abstain withholds the recommendation, not the evidence |
| [ ] | `A-07` | *Fail-closed* — unparsable confidence defaults to 0 | Stub the Arbitrator reply with prose containing no score | **Pass:** routes to abstain, not to a recommendation. Rehearse this — a judge may ask for it live |
| [ ] | `A-08` | *Demo stability* — success scenarios stay above 60 | Run Scenarios 1 and 3 five consecutive times; record every confidence value | **Pass:** 10/10 runs ≥ 60. If any run dips, lower the LLM temperature before you film |

### G7 — Runtime telemetry

*Traces to: AC-11 · TR-012 · FLOW-10*

| | ID | Check | How to verify | Pass condition |
|---|---|---|---|---|
| [ ] | `T-01` | Footer persists across all four UI states | Observe in Default, Loading, Success and Abstain | **Pass:** visible in every state, never unmounted while results are on screen |
| [ ] | `T-02` | Latency is a real measurement | Compare the footer value against a stopwatch on the run | **Pass:** within the same order of magnitude as observed wall time; never a constant |
| [ ] | `T-03` | Token count comes from the AutoGen usage record | Cross-check the footer against the client's usage summary | **Pass:** non-zero and it changes between scenarios |
| [ ] | `T-04` | Cost is derived from tokens × configured rate | Recompute by hand from the displayed token count | **Pass:** arithmetic reconciles and the figure is not $0.0000 (see gap A-5) |
| [ ] | `T-05` | Telemetry survives the rerun | Complete a run and let `st.rerun()` fire | **Pass:** values persist via session state; results do not vanish (Step 5 §6) |
| [ ] | `T-06` | Abstain runs also update telemetry | Run Scenario 2 from a fresh reload | **Pass:** latency and tokens reflect the partial run — abstaining still costs money, and showing that is the point |

### G8 — UI flow contract

*Traces to: FLOW-01 – FLOW-12 · Step 5 · Step 6*

| | ID | Check | How to verify | Pass condition |
|---|---|---|---|---|
| [ ] | `F-01` | Four status messages in exact order and exact wording | Record the run and step through frame by frame | **Pass:** Watcher → Researcher → Bear vs. Bull → Storyteller, each visible before the next |
| [ ] | `F-02` | Expander label is verbatim | Read the collapsed control | **Pass:** "View Agent Debate Transcript", collapsed by default |
| [ ] | `F-03` | HITL toast is verbatim on all three buttons | Click Accept, Modify, Reject in turn | **Pass:** "Feedback logged to Persistent Memory Graph" each time |
| [ ] | `F-04` | Any HITL action resets to Default / Empty | Click one and observe | **Pass:** narrative, card and transcript clear; KPIs and Run button return |
| [ ] | `F-05` | Changing persona or scenario resets the diagnostic surface | Complete a run, then change the scenario radio | **Pass:** stale results are cleared — never show Scenario 1's answer under Scenario 2's label |
| [ ] | `F-06` | Modify is feedback logging only | Click Modify | **Pass:** toast and reset, no edit form (App Flow §12 forbids one) |
| [ ] | `F-07` | Backend failure surfaces as an error, never as a partial answer | Break the API key mid-session and click Run Diagnostics | **Pass:** st.status flips to error state; no half-rendered recommendation (Step 5 §4) |
| [ ] | `F-08` | No route, tab, chat box or settings screen beyond the flow | Walk the whole app | **Pass:** one dashboard, one sidebar, nothing else (FLOW-12) |

### G9 — Submission package

*Traces to: Round 2 deliverables*

| | ID | Check | How to verify | Pass condition |
|---|---|---|---|---|
| [ ] | `P-01` | Fresh-clone test on a machine that has never seen the project | Clone → install → `python setup_db.py` → `streamlit run app.py` | **Pass:** works from the README alone, with no undocumented step |
| [ ] | `P-02` | README carries architecture, run path, scenarios and limitations | Read it as a stranger would | **Pass:** includes the deterministic-vs-LLM boundary diagram and the documented deviations block |
| [ ] | `P-03` | Demo video is linked at the top of the README | Open the link in a private window | **Pass:** plays without a login or access request |
| [ ] | `P-04` | Proposal covers all six required sections | Check against the brief | **Pass:** problem framing, solution design, business case, target users, risk mitigations, phased roadmap |
| [ ] | `P-05` | Repository is public and the history is clean | Open in a logged-out browser | **Pass:** loads for anonymous visitors; no key, no .duckdb, no chroma_db/ in the tree |

---

## D. Demo video — three minutes, eleven beats

The reel has one job: prove the three things nobody else in the track will have. A calculator the LLM cannot touch, a debate that resolves to a number, and a system that refuses to answer. Everything else is context. Narration below is written to be read aloud at roughly 150 words a minute — time it once and trim your own filler, not the substance.

### Pre-flight — do all of this before you hit record

- Run every scenario once to warm imports, the Chroma client and the model connection. A cold first run adds seconds of dead air.
- Browser at 1920×1080, zoom 100%, bookmarks bar hidden, no extension badges, notifications silenced.
- Confirm the telemetry footer shows a non-zero cost (gap A-5) and that Scenario 1 cleared A-08 five times running.
- Have Neo4j Browser open in a second tab, pre-scrolled to the feedback edge, if you closed the Neo4j gap.
- Record beats in order but as separate takes. Cut the LLM wait time — cutting to the finished result is honest; a fake progress bar is not.
- Never say "our AI calculates." Say "our SQL layer calculates, our agents argue." The distinction is your entire thesis.

### 0:00–0:16 · cold open — The dashboard that can't answer the question

**DO:** **On screen:** app loaded as VP of Revenue, Scenario 1, KPI row filling the frame. Cursor rests on the MRR card. Do not click.

> **SAY:** Monthly recurring revenue is down four percent. Churn is up twelve. Every dashboard in the enterprise can tell you that. None of them can tell you why — and the analysts who can take three days to say it. That gap is what we built for.

### 0:16–0:34 · thesis — Name the architecture before you show it

**DO:** **On screen:** pan the sidebar — persona selector, three scenarios, telemetry footer. Optionally cut to the architecture diagram from the README.

> **SAY:** The Enterprise BI Engine splits the problem in two. Every number is computed in DuckDB by deterministic SQL. The language models never see a calculator — they receive those numbers as fact and argue about what caused them. One agent prosecutes the internal cause, one prosecutes the external cause, and a third arbitrates and scores its own confidence.

### 0:34–0:52 · pipeline — Run Diagnostics — the four stages

**DO:** **Click:** Run Diagnostics. Hold on the st.status block through all four labels. **Cut** the model latency between stage 3 and stage 4.

> **SAY:** Watcher checks the SQL baseline. Researcher pulls the unstructured evidence — Slack threads, Zendesk tickets — anchored to the anomaly's timestamp. Then the debate opens. Storyteller only speaks last, and only if it earns the right to.

### 0:52–1:12 · payload — Two causes, not one

**DO:** **On screen:** narrative block, then scroll to the seven-field action card. Let the JSON keys be readable for a full second.

> **SAY:** Two causes, running simultaneously. Internally, a Stripe payment gateway timeout spiking to five hundred errors at 10:02. Externally, a competitor discount campaign landing the same week. A single-cause system picks one and is half wrong. And the output isn't prose — it's a validated contract: driver, controllable lever, action, expected impact, owner, confidence, monitoring plan. Seven fields, every time, or the run fails.

### 1:12–1:30 · traceability — Open the transcript

**DO:** **Click:** "View Agent Debate Transcript". Scroll slowly through the Bear position, then the Bull position, then stop on the Arbitrator's confidence line.

> **SAY:** Nothing here is a black box. This is the raw debate. Bear's case for the gateway, Bull's case for the competitor, and the arbitration that weighed both against the evidence and returned a confidence score. An auditor can read exactly why the system concluded what it concluded.

### 1:30–1:48 · rbac — Switch persona — the query stops running

**DO:** **Click:** persona selector → Director of Engineering. Land on the MRR card turning to `[RESTRICTED]`. Then scroll to show the narrative has changed too.

> **SAY:** Same incident, different seat. Engineering is blocked from raw revenue — and not by hiding the card. The billing query never executes for this persona, so the number never enters the session at all. The narrative changes with it: the VP gets business impact and a revenue-recovery action, engineering gets the root cause and the rollback.

### 1:48–2:20 · the differentiator — Scenario 2 — the system refuses

**DO:** **Click:** Scenario 2 (Ambiguous Data) → Run Diagnostics. Hold on the red banner for two full seconds before you resume speaking.

> **SAY:** Now the hard case. The evidence contradicts itself, and arbitration comes back at fifty. Watch what doesn't happen. No recommendation. No confident guess. The system says: evidence is contradictory, I am abstaining from automated action. And the control that stopped it isn't a prompt — it's a Python threshold sitting upstream of the Storyteller. Below sixty, that agent is never called. A language model cannot talk its way past a gate it was never given access to. The evidence stays open for a human, because the answer here is a person, not an algorithm.

### 2:20–2:34 · hitl — Human validation writes back to the graph

**DO:** **Click:** back to Scenario 1 → Run → Accept. Catch the toast. If Neo4j is wired, cut to the browser tab showing the new relationship.

> **SAY:** When the system does commit, a human still signs it off. Accept, Modify or Reject — and that verdict is written into the persistent graph as a validated causal edge, so the next diagnosis starts from what we already know is true.

### 2:34–2:44 · edge case — Sparse history doesn't break it

**DO:** **Click:** Scenario 3 → Run. Show it completing. Ten seconds, no lingering.

> **SAY:** A newly instrumented KPI with three days of history — not enough for a stable baseline. The engine degrades gracefully instead of crashing or inventing a statistic it can't support.

### 2:44–2:54 · telemetry — What the run actually cost

**DO:** **On screen:** zoom the sidebar footer — latency, tokens, cost in USD.

> **SAY:** And every run is metered. Latency, tokens, dollars — visible to the operator, per diagnosis. You can't govern what you can't cost.

### 2:54–3:00 · close — Land the thesis

**DO:** **On screen:** the abstain banner, held. End card with the repo URL.

> **SAY:** Deterministic where it must be. Adversarial where it helps. Silent where it should be.

---

## E. Live defense

You face two different rooms. **Round 3** is an AI-facilitated solution discussion covering technical and AI proficiency, stack expertise and problem-solving — expect breadth, mechanism-level detail, and follow-ups that chase whatever you were vaguest about. The **Grand Finale** in Bengaluru is a 10-minute pitch and a 5-minute human jury Q&A on a solution they were told would be "production-ready and scalable" — expect maybe four questions total, so each answer has to land in under a minute and lead with the mechanism.

Answer with the mechanism, not the marketing. Where the honest answer is a limitation, give the limitation — a team that names its own weak point reads as more credible than one that doesn't, and it usually ends the line of questioning.

### Three sentences to have memorized

1. The mathematical source of truth is the SQL layer; the LLM is a reasoning layer over facts it cannot alter.
2. The abstain gate is a Python threshold upstream of the recommendation agent — the model cannot generate its way past a function it is never handed.
3. Every field the UI renders is a validated Pydantic contract, not text parsed out of a paragraph.

### Q1 — Why not just let the LLM write and run the SQL? Modern models are good at that.

They're good at it on average, which is exactly the problem. A generated query that is subtly wrong returns a plausible number, and a plausible wrong number propagates through the narrative, the action card and the executive decision with no signal that anything failed. **Our KPI logic is a fixed, version-controlled artefact.** It produces the same delta and z-score on every run, it's unit-tested against known inputs, and when it changes, the change appears in a diff and a code review.

The second reason is regression testing. We can assert that Scenario 1 yields exactly −4.0% and fail the build if it doesn't. You cannot write that assertion against a model that regenerates its SQL each time.

If you want the demonstration: set the API key to garbage and the watcher still returns the correct figures. The arithmetic has no dependency on the model at all.

### Q2 — Isn't the Bear/Bull debate theatre? One prompt could produce both sides.

One prompt could *list* both sides. The difference is that a single model asked for a balanced view optimises for a balanced-sounding answer, and it tends to converge on whichever hypothesis it stated first. Separating the roles means each agent is instructed to build the **strongest** case for its position and to engage the opposing evidence rather than acknowledge it.

More importantly, the agent that decides is not an agent that argued. The Arbitrator has no stake in either position, which is what makes its confidence score meaningful as a measure of evidential conflict rather than a summary of its own reasoning.

Scenario 1 is the proof: a single-hypothesis system picks the gateway timeout or the competitor campaign. Ours surfaces both because two agents were each accountable for not letting their cause be dropped.

### Q3 — What does a confidence of 74 actually mean? Is it calibrated?

**No, and we don't claim it is.** It's an ordinal judgement produced by the Arbitrator — useful for ranking and for thresholding, not a calibrated probability, and we'd be overselling it to say otherwise.

The safety property doesn't depend on calibration. It comes from where the threshold sits: in application code, outside the model, with a fail-closed default. If the Arbitrator returns something we cannot parse into a number, confidence becomes zero and the system abstains. We don't need the score to be perfectly calibrated for the gate to be sound — we need it to be low when evidence conflicts, and we need the consequence of low to be enforced somewhere the model cannot reach.

Calibrating it properly needs outcome data, which is precisely what the human-in-the-loop layer is accumulating: every Accept, Modify and Reject is a labelled instance for the graph.

### Q4 — Scenario 2 is hardcoded to 50. You've staged the abstention.

The *fixture* is staged — deliberately. Scenario 2 is a seeded dataset built to contain genuinely contradictory evidence, in the same way a test suite seeds a known input. What is not staged is the routing: the pipeline reads the arbitration confidence, compares it against 60 in Python, and skips the Storyteller call entirely when it fails.

You can verify that independently of the fixture. Force the Arbitrator to return an unparsable reply on *any* scenario and the same branch fires — confidence defaults to zero, the recommendation agent is never invoked. The gate is real and scenario-independent; the fixture just makes it reproducible on camera. **Happy to run that live if you'd like to see it.**

### Q5 — How do you stop the narrative from hallucinating a number?

Three layers. The agents receive computed values in a structured context object and their system prompts explicitly forbid recalculating percentages, averages, variances or totals. The action card is never parsed from prose — it's a Pydantic model validated field by field, with confidence bounded to 0–100, and a validation failure raises rather than renders. And any figure in the narrative is checkable against the watcher payload, which is what our QA pass does.

Where we're honest about residual risk: free-text narrative is the softest surface in the system. Prompt constraint reduces it, validation doesn't cover it. The production answer is to template the narrative from validated fields rather than generate it — that's on our roadmap, not in the MVP.

### Q6 — A persona dropdown isn't authentication. Isn't your RBAC decorative?

The dropdown is a demo mechanism and our TRD says so explicitly — no identity provider, no account system, no production security claim. What we'd point at instead is **where the enforcement sits**. Blocking at the render layer is decorative; a restricted value that reaches the client and gets hidden by CSS has already leaked. Ours is enforced at data selection — for the engineering persona the billing query does not execute, so the value never enters application state.

That's the part that survives contact with production. Swap the dropdown for an OIDC session and the same authorization check moves to row-level policy in the warehouse without the application logic changing shape.

### Q7 — Why Streamlit? That won't scale to an enterprise deployment.

Correct, and it isn't meant to. The TRD fixes Streamlit as the MVP frontend precisely so the effort goes into the diagnostic engine rather than the console. Our PRD originally specified Next.js; we documented the supersession rather than quietly shipping against a criterion we'd abandoned.

Architecturally it costs us nothing: `agents.py` runs standalone from the CLI with no Streamlit import, which is how we test it. The presentation layer is genuinely swappable — that's Phase 2 in the roadmap.

### Q8 — What does this cost per diagnosis, and does it scale?

Look at the footer — every run reports latency, token count and dollar cost, and we put that in the operator's view on purpose rather than burying it in a billing dashboard. That's the governance answer as much as the cost answer.

On scaling: the deterministic layer is ordinary analytical SQL and scales like your warehouse. The expensive layer is the debate, and it only runs on anomalies the watcher has already flagged — so cost scales with incidents, not with data volume or dashboard traffic. A five-agent chat over a bounded context object is a fixed, predictable unit cost.

### Q9 — What breaks first when you put this on real data?

Evidence retrieval. Our Chroma corpus is four curated documents where the relevant evidence is guaranteed to be present and unambiguous. Against a real Slack workspace, the debate is only as good as what the Researcher surfaces, and the failure mode is quiet: agents argue confidently over incomplete evidence. Retrieval quality — not agent design — is the first real engineering problem.

Second is confidence calibration against real outcomes, which needs the HITL feedback loop running long enough to produce labels. We've built the mechanism; we haven't earned the data yet.

### Q10 — The brief asked for a semantic layer. Where is your KPI dictionary?

Answer this one only if you've closed it — and close it, because it's a direct hit on a listed expectation. **The honest version:** the semantic contract governs our four KPIs — definition, calculation, grain, refresh cadence, drivers, threshold and access level — and it's a declarative config the watcher reads, not logic buried in code. Point at the file.

If you haven't closed it, don't bluff that the seeded schema counts. Say the contract is specified and the MVP implements the access-restriction and threshold halves of it, with the full dictionary as the next increment. A judge who wrote the brief will know the difference immediately.

### Q11 — Scenario 1 has two causes. Which one mattered more?

This is the ranking requirement in disguise, and "both contributed" is a losing answer. The strong version is arithmetic: **the failed transactions are a measurable share of the MRR delta**, computed in SQL, and the residual is what the competitor campaign has to explain. That gives you a defensible split rather than an LLM's opinion about which cause feels bigger.

It also sharpens the demo. "Both, and here's the split" is a materially better line than "both" — and it's the same deterministic-math argument you're already making, applied one level deeper.

### Q12 — Why AutoGen rather than LangGraph or a custom orchestrator?

Our TRD fixed it, and we held the line rather than re-litigating the stack mid-build. On the merits: AutoGen's GroupChat maps directly onto the thing we're modelling — a multi-party conversation with a speaker order and a terminating agent. A graph framework would have us express a debate as a state machine, which is more machinery for the same result.

The orchestration choice is also the least load-bearing decision in the system. The two properties we actually defend — deterministic math and a Python-side abstain gate — sit outside the agent framework entirely and would survive replacing it.

- **Never say "our AI calculates"** — It concedes the one claim the whole architecture exists to make. Say: SQL calculates, agents argue.
- **Never call confidence a probability** — Say ordinal judgement. A judge with an ML background will hold you to calibration you don't have.
- **Never claim production security** — The TRD forbids implying it. "Demo authorization mechanism, enforced at the data layer" is the accurate phrasing.
- **Never demo an unrehearsed scenario** — If A-08 wasn't run five times, the abstain gate can fire mid-pitch on a scenario you promised would succeed.
