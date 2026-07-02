# 15. Memory Architecture

"Memory" in AIC is not a vendor feature bolted onto an agent framework — it is domain data with
explicit ownership, retention, and access rules. Three tiers, by scope and lifetime.

## 15.1 The three tiers

| Tier | Scope | Store | Written by | Read by |
|---|---|---|---|---|
| **Working memory** | One agent execution | LangGraph state (in-process) | the running graph | the running graph |
| **Incident memory** | One incident | Postgres (`incident.evidence`, `incident_event`, RCA, proposals) | activities | every later phase of the same incident; humans; the Scribe |
| **Organizational memory** | The whole org, forever | Postgres `knowledge.*` + pgvector embeddings | knowledge API (runbooks/postmortems), post-incident indexer | investigation agent (RAG), knowledge search API |

The deliberate omission: **no cross-incident "agent memory" of the free-form kind** (no vector
store of past agent musings, no automatic memory extraction). Cross-incident knowledge enters
organizational memory only through two typed doors — ingested documents and *resolved-incident
records* — both reviewable artifacts. An agent hallucination can therefore never silently become
"remembered fact" that poisons future incidents; it would have to survive incident resolution and
land in a structured record first.

## 15.2 Working memory: disposable by design

Graph state carries the current plan, tool results awaiting digestion, and draft outputs. It
dies with the activity — and that's a feature: Temporal retries re-enter with clean state,
avoiding the classic "agent retries with its own confused scratchpad" failure. Anything worth
keeping is persisted as Evidence *during* the run, not at the end (a crash loses scratch, never
gathered facts).

## 15.3 Incident memory: the evidence ledger as shared context

Phases don't pass context to each other directly — they read it back from Postgres:

- Triage writes `TriageResult` → investigation reads it.
- Investigation appends `Evidence` rows as it gathers → RCA synthesis reads digests.
- The Scribe reads the *entire* event log to write the record.

This indirection is what makes phases independently retryable and the whole incident
reconstructable: the prompt context of any phase is a deterministic function of durable rows,
so "what did the agent know when it said X?" is answerable byte-for-byte.

## 15.4 Context assembly: a budgeted, ranked packer

Every LLM call gets its context from one code path (`aic_agents.context`), never ad-hoc string
concatenation:

```
ContextBudget (per agent role, in tokens)
├── fixed:    system prompt (versioned template)
├── fixed:    incident header (title, severity, service, env, timeline skeleton)
├── ranked:   evidence digests      — newest + highest-salience first, until budget
├── ranked:   RAG chunks            — top-k by similarity × recency × doc-type weight
└── reserve:  output token headroom
```

Rules:

1. **Digests, not raw payloads.** Raw tool output was already compressed at gather time (§13.3).
   The packer never re-reads raw payloads.
2. **Deterministic ranking** (salience score computed from evidence type, recency, and
   reference count) — same inputs, same context, reproducible evals.
3. **Truncation is visible.** If evidence was dropped for budget, the prompt says so
   (`[13 additional evidence items omitted]`) and the omission is logged — the model should
   know it has partial vision, and so should the human reading the RCA.
4. **Redaction runs before packing** (second pass; first was at persistence). Patterns +
   entropy-based secret detection; redactions are logged with rule IDs.

## 15.5 Organizational memory: the RAG corpus and its hygiene

Content classes and their weights in retrieval: `runbook` (highest — operational ground truth),
`postmortem` / `incident_record` (what actually happened before), `architecture` (how systems
relate). Hygiene rules that keep the corpus an asset instead of a liability:

- **Versioned, soft-deactivated documents** (§9.3): retrieval only sees active versions;
  re-ingesting a runbook supersedes rather than duplicates. `content_hash` skips no-op
  re-embeddings.
- **The compounding loop is curated:** on resolution, the Scribe's incident record is indexed
  *after* it reaches `closed` (post-review) — human sign-off is the quality gate between "what
  the agent believes happened" and "what the org remembers."
- **Metadata is first-class** (service, failure mode, doc type): retrieval filters before
  similarity, so `payment-service` incidents pull payment runbooks, not a lookalike from an
  unrelated stack.
- **Citations are mandatory.** Retrieved chunks enter context with stable chunk IDs; RCA
  hypotheses citing corpus knowledge cite chunk IDs the same way they cite Evidence. Retrieval
  quality is therefore measurable per incident (§22), not vibes.

## 15.6 Caching (the fourth, unofficial tier)

Redis holds derived, rebuildable artifacts only: incident read-model cache for the API,
embedding cache (`content_hash → vector`) to avoid re-embedding repeated queries, and the
idempotency replay cache. Explicitly **no semantic/LLM response cache in MVP**: incident
contexts rarely repeat exactly, and a stale cached RCA is worse than a slow fresh one. Revisit
with data (§27).

## 15.7 Retention

| Data | Retention | Rationale |
|---|---|---|
| Incident events, RCA, approvals, executions | 400 days | audit horizon (NFR-5.3) |
| Raw evidence payloads | 90 days (digests kept 400) | bulk vs. usefulness curve |
| LLM call ledger + Langfuse traces | 90 days full, aggregates kept | debugging window |
| Organizational memory | indefinite, versioned | it's the product's compounding asset |
| Redis keys | TTL ≤ 24 h | nothing durable lives there |
