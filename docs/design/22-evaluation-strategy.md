# 22. Evaluation Strategy

An agent platform without evaluation is a system whose quality is unknown and whose regressions
ship silently. AIC treats evals the way ordinary software treats tests: **versioned datasets,
deterministic harness, CI gate, and a defined bar for "good enough to release."**

## 22.1 What gets evaluated (one unit per agent, plus end-to-end)

| Suite | Unit under test | Primary scores |
|---|---|---|
| `triage` | Triage agent | severity accuracy (exact + off-by-one), service attribution, impact classification F1 |
| `investigation` | Investigation graph | evidence sufficiency (did it fetch the discriminating evidence?), tool-call efficiency (calls vs. optimal), budget compliance |
| `rca` | RCA synthesis | root-cause hit rate (top-1 / top-3), citation validity (do cited Evidence IDs support the claim?), calibration (confidence vs. correctness) |
| `proposal` | Remediation planner | action appropriateness, **safety invariants** (below), rollback pairing correctness |
| `retrieval` | RAG pipeline | recall@k / MRR against labeled query→chunk pairs; independent of any LLM |
| `e2e` | Whole pipeline vs. simulated environment | end-state correctness: right incident record, right action proposed, right approvals requested |

**Safety invariants are pass/fail, not scored.** A release candidate that *ever* proposes a
non-catalog action, an action for the wrong environment, or omits a required approval in the
suite is rejected regardless of how well it scores elsewhere. Quality is a dial; safety is a
gate.

## 22.2 Datasets

- **Synthetic scenario bank** (`evals/datasets/`): versioned YAML scenarios — a fictional but
  internally consistent org (services, topology, runbooks) plus incident cases: alert payload,
  mocked tool responses (the *world state*), and labels (true root cause, expected severity,
  acceptable actions). Authored to cover the failure taxonomy: bad deploy, resource exhaustion,
  dependency outage, config error, alert storm, *red-herring cases* (symptom looks like a known
  cause but isn't — the anti-overfitting set), and adversarial cases (injection strings in
  logs — feeding §17's T1 controls their regression tests).
- **Replay set** (grows with usage): resolved real incidents exported (redacted) into the same
  scenario format, labeled by what actually turned out to be true. This is the set that keeps
  evals honest about distribution shift.
- Datasets are versioned like code: schema-validated, reviewed in PRs, `dataset_version`
  recorded on every run. Label changes are diffs with rationale.

## 22.3 Scoring: deterministic first, judge second, humans third

1. **Deterministic scorers** wherever possible: severity is label comparison; citation validity
   is a set check against the scenario's evidence; safety invariants are assertions; retrieval
   is recall math. Cheap, unarguable, run on every PR.
2. **LLM-as-judge** only where judgment is genuinely required (is this RCA narrative *correct
   and well-supported*?): rubric-based, pinned judge model + version, temperature 0, and —
   critically — **calibrated against a human-labeled subset** (~50 cases): we report judge
   agreement with humans, and a judge change is itself an evaluated change. Judge scores carry
   confidence intervals, never single points.
3. **Human review** for the calibration subset and for every case the judge flags as
   borderline. Reviewer decisions update labels → the datasets improve monotonically.

## 22.4 Harness mechanics

`evals/run.py` — same agent code, same context packer, same telemetry as production
(§21.6); only the adapters are swapped for scenario-backed fakes (the same fakes the
integration tests use — one fake per adapter, maintained with the adapter). Determinism knobs:
temperature 0 where supported, seeded sampling, pinned model versions, N=3 repetitions with
variance reported (a scorer whose variance exceeds its effect size is flagged as
non-discriminating). Results: JSON artifacts → Phoenix for exploration (score distributions,
per-case drill-down, diffing runs) → summary table posted on the PR.

## 22.5 Gating: what blocks a merge/release

Runs on: any change to `aic_agents` (prompts, graphs, tools), `aic_contracts.agents`, model/
provider config, or datasets. Nightly full runs catch provider-side drift (same pinned model,
shifted behavior — it happens).

| Check | Gate |
|---|---|
| Safety invariants | hard fail on any violation |
| Primary scores vs. `main` baseline | fail if drop > noise band (from repetition variance) on any suite |
| Cost/latency per scenario | fail if median cost ↑ >25% without an accompanying score gain (efficiency is a feature) |
| Judge–human agreement (when judge changed) | fail below agreement floor |

Baselines are stored per-suite (`evals/baselines/`), updated only by explicit
`eval-baseline: update` PR label — improving the baseline is a reviewed decision, not a side
effect.

## 22.6 Online evaluation (production signals)

Offline evals ask "is it good on our cases?"; online signals ask "is it good on reality?"

- **RCA acceptance**: did a human mark the top hypothesis correct at close? (one-click field in
  the close flow — deliberately cheap to answer honestly)
- **Proposal outcomes**: approval rate, rejection reasons (categorized enum + free text),
  verification pass rate of executed actions — *the ground-truthiest signal we have*
- **Confidence calibration**: predicted confidence vs. accepted-RCA outcomes, plotted quarterly
  (an agent that says 0.9 and is right 60% of the time is lying to approvers — this is a
  trust-safety metric, not a vanity metric)
- **Time-to-first-hypothesis and phase durations** (already §21 metrics) as the value proxy

Online signals feed back in two loops: rejected/failed cases become replay-set candidates
(labeled by what actually happened), and calibration drift triggers prompt/threshold review.

## 22.7 Change management for AI behavior

Prompt or graph changes follow the same lifecycle as schema migrations: PR → offline gate →
merge → **shadow or staged rollout** where feasible (run new agent version on a sample of real
incidents *without* acting — compare artifacts) → promote. The `agent_version` + `prompt_hash`
recorded on every artifact (§13.5) is what makes before/after analysis a query instead of an
archaeology project.
