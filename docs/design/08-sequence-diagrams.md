# 8. Sequence Diagrams

Four sequences cover the system's critical behavior: ingestion, investigation, approval, and
execution/verification. Failure branches are drawn in, not hand-waved — the failure paths *are*
the design.

## 8.1 Alert ingestion → durable workflow

```mermaid
sequenceDiagram
    autonumber
    participant AM as Alertmanager
    participant ING as aic-ingest
    participant PG as Postgres
    participant RD as Redis
    participant ST as Redis Streams
    participant CS as Stream consumer
    participant TP as Temporal

    AM->>ING: POST /webhooks/alertmanager (HMAC)
    ING->>ING: verify signature + replay window
    ING->>ING: normalize → AlertEvent
    ING->>PG: INSERT alert_event (persist before ack)
    ING-->>AM: 202 Accepted  (p99 < 500ms)
    ING->>RD: SETNX fingerprint (dedup window)
    alt duplicate within window
        ING->>PG: attach occurrence to existing alert
    else new / correlates to open incident
        ING->>PG: create or update incident
        ING->>ST: XADD IncidentTriggered
    end
    ST-->>CS: deliver (consumer group, at-least-once)
    CS->>TP: start IncidentWorkflow(workflow_id = incident_id)
    Note over CS,TP: duplicate delivery → "already started"<br/>error swallowed: idempotent by workflow ID
    CS->>ST: XACK
```

**Why persist-before-ack:** if `aic-ingest` crashes after the 202, the alert is already in
Postgres; a reconciliation sweep re-publishes unprocessed alerts. NFR-1.2 (never lose an accepted
alert) is satisfied structurally, not by hoping the process stays up.

## 8.2 Investigation and RCA (intelligence loop)

```mermaid
sequenceDiagram
    autonumber
    participant WF as IncidentWorkflow (Temporal)
    participant TA as triage activity
    participant IA as investigate activity
    participant LG as LangGraph agent
    participant T as Read-only tools (K8s, Prom, GitHub)
    participant RAG as RAG (pgvector)
    participant LLM as LLMPort
    participant PG as Postgres

    WF->>TA: execute (retry policy: 3x, backoff)
    TA->>LLM: classify (cheap model, bounded tokens)
    TA->>PG: append IncidentEvent(triaged) + severity
    TA-->>WF: TriageResult
    Note over WF: SEV-1/2 → notify/page immediately<br/>(paging never waits on AI)

    WF->>IA: execute (heartbeat + budget)
    IA->>LG: run investigation graph
    loop bounded: max tool calls / tokens / wall-clock
        LG->>T: tool call (pods, metrics, deploys, diffs)
        T-->>LG: typed result
        LG->>PG: append Evidence (source, query, digest, latency)
        LG->>LLM: reason over evidence (frontier model)
    end
    LG->>RAG: similar incidents + runbooks
    RAG-->>LG: chunks with citations
    LG->>LLM: synthesize → RootCauseAnalysis (structured output)
    alt schema validation fails
        LG->>LLM: retry with validation feedback (max 2)
    end
    IA->>PG: persist RCA (hypotheses cite Evidence IDs)
    IA-->>WF: RCAResult
    alt integration down / budget exhausted
        IA-->>WF: PartialResult(gaps recorded)
        WF->>WF: continue with degraded evidence,<br/>flag for human, never hang
    end
```

## 8.3 Human approval (durable wait, Slack + API)

```mermaid
sequenceDiagram
    autonumber
    participant WF as IncidentWorkflow
    participant PA as propose activity
    participant POL as PolicyEngine
    participant PG as Postgres
    participant API as aic-api
    participant SLK as Slack
    participant U as Approver

    PA->>POL: evaluate each catalog action
    POL-->>PA: auto_approve / require_approval(quorum, roles) / forbid
    PA->>PG: persist proposal + policy decisions (rule id + version)
    alt all actions auto-approved
        PA-->>WF: proceed to execution
    else approval required
        PA->>SLK: post approval card (evidence, action, blast radius, dry-run)
        PA-->>WF: ApprovalRequired
        WF->>WF: await signal (timer: timeout T1)
        U->>SLK: click Approve
        SLK->>API: interaction callback (signed)
        API->>API: verify Slack signature, resolve identity, check RBAC role
        API->>PG: record ApprovalDecision (who, when, what)
        API->>WF: signal approval(action_id, decision)
        alt quorum met
            WF->>WF: proceed to execution
        else awaiting more approvers
            WF->>WF: keep waiting (timer continues)
        end
    end
    alt timeout T1 (no decision)
        WF->>SLK: escalate to next chain level
        WF->>WF: timer T2
    end
    alt timeout T2
        WF->>PG: mark proposal expired
        WF->>SLK: notify — no action taken (safe expiry)
    end
    alt rejected with reason
        WF->>PA: one bounded re-proposal cycle with feedback
    end
```

**Why a Temporal signal:** the workflow can wait minutes or days at zero cost, survive worker
restarts mid-wait, and the wait/decision/timeout history is part of the workflow's own durable,
replayable record.

## 8.4 Execution, verification, rollback

```mermaid
sequenceDiagram
    autonumber
    participant WF as IncidentWorkflow
    participant EX as aic-executor (execution task queue)
    participant PG as Postgres
    participant POL as PolicyEngine (executor's own instance)
    participant K8S as Kubernetes API
    participant PR as Prometheus
    participant SLK as Slack

    WF->>EX: execute_action activity (idempotency key)
    EX->>PG: load approval record + policy decision
    EX->>POL: independent re-evaluation (defense in depth)
    alt gate fails (no approval / policy mismatch / forbidden)
        EX->>PG: append SecurityEvent
        EX-->>WF: REFUSED (fatal, alerts on-call + platform team)
    end
    EX->>PG: acquire per-target remediation lock
    EX->>K8S: apply typed action (e.g. rollback to rev N)
    K8S-->>EX: result
    EX->>PG: append ActionExecuted (inputs, outputs, duration)
    EX-->>WF: ActionResult

    WF->>EX: verify activity (soak window, e.g. 10 min)
    loop poll interval within soak window
        EX->>PR: re-run triggering alert query + health probes
    end
    alt condition cleared and stable
        EX-->>WF: VERIFIED
        WF->>PG: incident → resolved
        WF->>SLK: resolution summary
    else still failing / regressed
        EX-->>WF: VERIFICATION_FAILED
        alt rollback defined for action
            WF->>EX: execute rollback action (same gate path)
        end
        WF->>SLK: escalate to human, incident → escalated
    end
```

## 8.5 Cross-cutting behavior in every sequence

- **Tracing:** each sequence above is one OTel trace (or a linked continuation of the incident
  trace); every arrow is a span. LLM spans carry model/tokens/cost attributes → Langfuse.
- **Retries:** activity-level retry policies (exponential backoff, typed non-retryable errors).
  Retryable: timeouts, 429s, transient 5xx. Non-retryable: validation failures, policy refusals,
  auth errors.
- **Idempotency:** workflow ID = incident ID; action idempotency keys; event appends carry
  deterministic event IDs so at-least-once never becomes duplicate history.
