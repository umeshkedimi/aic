# 3. Real Enterprise Use Cases

Each use case names the actor, trigger, flow through AIC, and the measurable value. These are the
scenarios the architecture must serve — every design decision traces back to one of them.

---

## UC-1 — The 3 a.m. page: deploy-induced latency regression

**Organization:** B2B SaaS, ~40 microservices on Kubernetes (EKS), Prometheus + Grafana, GitHub.
**Actor:** On-call backend engineer.
**Trigger:** Alertmanager fires `PaymentServiceP99LatencyHigh` at 03:12.

**Flow:**
1. `aic-ingest` receives the webhook, opens incident `INC-2041`, and the Temporal workflow starts.
2. Triage agent classifies: SEV-2, service `payment-service`, customer-facing. On-call is paged
   (as always) — but the page links to the AIC incident.
3. Investigation agent, in parallel: pulls pod status and events (2 pods CrashLooping after 03:05),
   queries Prometheus (latency step-change at 03:06), lists recent deploys (release `v2.14.1`
   rolled out 03:04), fetches the diff of the deployed PR (connection-pool config change), and
   retrieves two similar past incidents via RAG.
4. RCA: hypothesis #1 (confidence 0.85) — `v2.14.1` pool-size reduction causing connection
   exhaustion; cites the deploy timestamp, the metric step-change, and the diff hunk.
5. Proposal: `RollbackRelease(payment-service, to=v2.14.0)`. Policy: prod rollback = 1 approval.
6. The engineer, still in bed, reads the evidence in Slack and taps **Approve**.
7. `aic-executor` rolls back, verification probe watches p99 for a 10-minute soak, alert clears,
   incident auto-resolves. Post-incident record filed to Jira with full timeline.

**Value:** ~8 minutes from page to approved fix vs. a typical 45–60 minutes of solo spelunking.
The human made exactly one decision, with evidence in front of them.

---

## UC-2 — Regulated fintech: audit-grade change control

**Organization:** Payments company under SOC 2 Type II and PCI-DSS; every production change needs
attributable approval.
**Actor:** SRE lead + compliance officer (consumer of records).
**Trigger:** Quarterly audit requests evidence for all emergency production changes.

**Flow:**
- AIC's policy for this org: *all* prod write actions require two approvals from the `sre-senior`
  role; `DeleteResource` class is forbidden platform-wide; staging allows auto-approval for
  `RestartDeployment` only.
- Every executed action carries: proposing agent version + prompt hash, policy rule that matched,
  both approvers' identities and timestamps, dry-run output, execution result, verification result.
- Compliance exports incident records for the audit period directly from the API.

**Value:** Emergency changes stop being an audit finding. The security team approved AIC's rollout
*because of* the executor privilege split and closed action catalog — the architecture is the
sales pitch.

---

## UC-3 — Alert storm during peak traffic

**Organization:** E-commerce platform during a sales event.
**Actor:** Incident commander running a war room.
**Trigger:** A database failover causes 200+ alerts across 30 services in 4 minutes.

**Flow:**
1. `aic-ingest` deduplicates repeats and correlates the storm into **one** incident (shared time
   window + dependency topology + common label sets), not 200.
2. Triage identifies the earliest-firing, lowest-in-the-stack alert group (Postgres primary) as
   the probable root, and marks 27 service alerts as downstream symptoms.
3. Investigation confirms failover event from CloudWatch RDS events; RCA distinguishes cause from
   symptom cascade.
4. The war room gets a single incident with a causal picture in minutes, instead of triaging 200
   pages by hand.

**Value:** Correlation and cause-vs-symptom separation under load — the moment human triage
capacity is most overwhelmed is the moment AIC contributes most.

---

## UC-4 — Codified tribal knowledge: the runbook that actually gets used

**Organization:** Enterprise with 15 years of accumulated runbooks in Confluence, most stale, plus
postmortems nobody rereads.
**Actor:** Platform engineering team; new on-call hires.
**Trigger:** Ongoing — every incident.

**Flow:**
- Runbooks and postmortems are ingested into the RAG corpus (chunked, embedded in pgvector, tagged
  by service/failure mode).
- During investigation, the agent retrieves and *cites* the relevant runbook section and the two
  most similar past incidents, including what remediation worked last time.
- Every resolved AIC incident generates a structured post-incident record that is itself indexed —
  the corpus compounds instead of rotting.
- A first-week on-call hire performs like someone with two years of context, because the context
  arrives attached to the page.

**Value:** Knowledge retention becomes a platform property instead of a staffing property.

---

## UC-5 — Tiered autonomy across environments

**Organization:** Company with dev/staging/prod on separate clusters, wanting automation without
betting prod on it.
**Actor:** Platform team defining policy.
**Trigger:** Policy configuration, then every incident.

**Flow:**
- Policy matrix: staging → `RestartDeployment`, `ScaleDeployment`, `RollbackRelease` auto-approved
  within blast-radius limits (≤ N replicas, one service at a time). Prod → same actions require
  approval; quiet-hours escalation chain defined. Everywhere → destructive classes forbidden.
- AIC runs identically in all environments; only policy differs. Staging becomes the proving
  ground that builds the confidence (and the eval data) to widen prod autonomy later.

**Value:** Autonomy as a dial, not a switch — the only adoption path enterprises actually accept.

---

## UC-6 — The postmortem that writes itself

**Organization:** Any; postmortem discipline decays under delivery pressure everywhere.
**Actor:** Engineering manager; service owner.
**Trigger:** Incident resolution.

**Flow:**
1. On resolution, the documentation agent assembles the record from the event log: timeline (all
   timestamps machine-recorded), evidence, RCA, actions taken, approvers, verification results,
   time-in-phase metrics.
2. Draft filed as a Jira ticket and a GitHub PR against the postmortem repo; humans edit rather
   than reconstruct.
3. Follow-up actions become tracked Jira issues linked to the incident.

**Value:** 100% postmortem coverage with accurate timelines, at near-zero marginal human cost —
and each one feeds UC-4's corpus.
