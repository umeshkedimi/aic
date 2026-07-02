# 1. Executive Summary

**AIC (AI Incident Commander)** is an enterprise agentic AI platform that acts as an autonomous
first responder for production incidents. When an alert fires — from Alertmanager, Datadog, or
CloudWatch — AIC opens an incident, dispatches an investigation agent that gathers evidence from
the organization's own observability and infrastructure stack (Kubernetes, Prometheus, Grafana,
GitHub, Jira), produces a ranked root-cause analysis with cited evidence, proposes remediation as
**typed, policy-checked action plans**, obtains human approval through Slack or the web console,
executes approved actions behind guardrails, verifies recovery against the original alert
condition, and closes the loop with an audit-grade post-incident record.

## The core thesis

The hard part of "AI for incident response" is not the LLM call — it is everything around it.
AIC is therefore designed as a *platform*, not an agent script:

- **Durable by construction.** Every investigation is a Temporal workflow. A pod restart, an LLM
  outage, or a 4-hour wait for human approval never loses state. There is no in-memory
  orchestration anywhere in the critical path.
- **Agents propose, policies dispose.** LLM output is never executed. Agents emit structured
  `RemediationProposal` objects; a deterministic policy engine decides whether each action is
  auto-approvable, requires human approval, or is forbidden. The approval gate is enforced in code
  paths the agent cannot reach.
- **Privilege separation at the process boundary.** The agent runtime holds *read-only*
  credentials. Write credentials to Kubernetes/GitHub/cloud exist only inside a separate Execution
  service with its own service account, network policy, and audit log. A prompt-injected agent
  physically cannot mutate infrastructure.
- **Audit-first.** Every observation, hypothesis, tool call, token count, approval, and action
  lands in an append-only incident event log — enough to reconstruct any incident end-to-end for a
  compliance review.
- **Provider-agnostic by design.** LLM (OpenAI → Anthropic/Azure), vector store (pgvector →
  Pinecone/Qdrant/Weaviate), and message bus (Redis Streams → Kafka) sit behind ports; swapping
  providers is a configuration change, not a rewrite.
- **Measured, not vibed.** LLM traces flow to Langfuse/Phoenix; an offline evaluation harness
  scores RCA quality against curated incident datasets, gating releases the same way tests gate
  merges.

## What AIC is not

It is not a chat assistant bolted onto PagerDuty, and it is not an auto-remediation daemon. It is
a human-in-the-loop system whose autonomy is a *policy setting per action class per environment* —
full auto for `restart deployment in staging`, mandatory two-person approval for
`scale down in prod`, hard-forbidden for `delete PVC` anywhere.

## Success metrics

What an enterprise buyer would measure:

| Metric | Target |
|---|---|
| Time-to-first-hypothesis from alert | < 3 minutes |
| MTTR reduction on incident classes with runbook coverage | measurable per class |
| Incidents with accepted (human-confirmed) RCA | tracked as adoption KPI |
| Write actions attributable to a named approver or explicit policy rule | 100% |
