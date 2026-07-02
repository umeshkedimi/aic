# 2. Business Problem

## 2.1 The cost of incidents

For any company running revenue-critical software, production incidents are among the most
expensive recurring events in engineering:

- **Direct downtime cost.** Industry analyses put the cost of downtime for large enterprises in
  the range of thousands of dollars per minute; for payment, trading, and commerce platforms it is
  materially higher. MTTR is a board-level metric.
- **Human cost.** Incident response is performed by the most senior (most expensive, hardest to
  retain) engineers, frequently outside working hours. On-call fatigue is a documented driver of
  attrition on platform and SRE teams.
- **Opportunity cost.** Every hour spent firefighting is an hour not spent shipping. Organizations
  with weak incident tooling routinely lose 10–20% of senior engineering capacity to unplanned
  work.

## 2.2 Where the time actually goes

MTTR decomposes into detection → triage → investigation → decision → remediation → verification.
Detection is largely solved (Prometheus, Datadog, CloudWatch alert within seconds). The dominant
cost sits in the middle:

| Phase | What actually happens today | Why it is slow |
|---|---|---|
| Triage | On-call gets paged, reads a terse alert, decides severity half-awake | Context lives in 6 different tools; alert says *what* fired, not *what it means* |
| Investigation | Engineer tabs between Grafana, `kubectl`, GitHub, logs, Slack history | Evidence gathering is manual, serial, and repeated identically for every similar incident |
| Decision | "Has this happened before? What did we do?" | Tribal knowledge: the answer lives in a departed engineer's head or an unsearched postmortem doc |
| Remediation | Someone runs commands under pressure | Highest-risk moment of the incident; fat-fingered remediation causes secondary incidents |
| Documentation | Postmortem written days later, if at all | Nobody reconstructs an accurate timeline after the fact; learning is lost |

The investigation phase alone — pure context gathering that a machine could do — commonly consumes
**30–50% of total incident time**, and it is the same mechanical work every time: check recent
deploys, check pod events, check the dashboards for this service, find the runbook, find similar
past incidents.

## 2.3 Why existing tooling doesn't solve it

- **Observability platforms** (Datadog, Grafana) show you data; they do not investigate, reason
  across tools, or act. Their "AI" features summarize within their own silo.
- **Incident management tools** (PagerDuty, incident.io, FireHydrant) orchestrate *humans* —
  paging, status pages, Slack channels. The cognitive work remains fully manual.
- **Runbook automation** (Rundeck, StackStorm, Ansible) executes pre-written scripts for
  pre-anticipated failures. It cannot handle the novel-but-similar incidents that dominate real
  on-call load, and it has no reasoning layer to decide *which* runbook applies.
- **Raw LLM chatbots** ("paste your logs into a chat window") have no live tool access, no memory
  of the organization's history, no guardrails, no audit trail — and are therefore banned from
  production access at any enterprise with a security team.

## 2.4 Why now, and why it must be a platform

The ingredients finally exist: observability data is rich and API-accessible; LLMs can genuinely
synthesize evidence across sources and produce ranked hypotheses; agent frameworks make multi-step
tool use tractable.

What blocks enterprise adoption is not model capability — it is **trust infrastructure**:

1. *"What exactly can it touch?"* → requires least-privilege architecture and a closed action
   catalog, not a shell.
2. *"Who approved that action?"* → requires human-in-the-loop workflow with recorded identity,
   not a config flag.
3. *"Prove what it did during the March 3rd incident."* → requires an append-only audit log and
   full tracing, not chat history.
4. *"What happens if it hangs mid-incident?"* → requires durable workflows that survive crashes
   and long waits, not a Python loop.
5. *"Is it actually good, and is it getting worse?"* → requires an evaluation harness and LLM
   observability, not anecdotes.

AIC is the answer to those five questions. The agent is ~15% of the system; the other 85% — the
part enterprises actually buy — is durability, guardrails, auditability, and measurement.

## 2.5 Target outcomes

| Outcome | Mechanism |
|---|---|
| Cut investigation time from tens of minutes to ~3 minutes | Parallel automated evidence gathering + RAG over org history, delivered before a human finishes logging in |
| Reduce remediation risk | Typed actions, policy gates, dry-run, approval, automatic verification and rollback |
| Stop losing organizational knowledge | Every incident auto-documented and indexed; the platform's RAG corpus compounds |
| Satisfy audit/compliance (SOC 2, ISO 27001 change-management controls) | 100% attributable actions, immutable event log, exportable incident records |
| Reduce on-call burden | The 3 a.m. page arrives with triage, evidence, RCA hypotheses, and a one-click (approved) fix attached |

## 2.6 Non-goals

- AIC does not replace the on-call human or the paging system; it makes them faster. Paging is
  never gated on AI.
- AIC is not an observability platform; it consumes existing ones.
- AIC does not aim for fully autonomous production remediation as a default; autonomy is a policy
  decision made per action class, per environment, by the customer.
