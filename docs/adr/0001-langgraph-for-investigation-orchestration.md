# ADR 0001: LangGraph for investigation orchestration

## Status
Accepted (2026-08-10)

## Context
The signature scenario's investigation flow — gather evidence in parallel, digest it, decide
whether enough is known or another gather pass is needed (bounded loop), synthesize ranked
hypotheses, self-check — is, for this one scenario, simple enough to write as a plain async
Python function. The project mandate is explicit that frameworks must not hide engineering the
author can't explain: *"Do not hide complexity behind LangGraph... Frameworks are implementation
tools. They are NOT the architecture."*

The competing consideration: this is meant to be a genuine demonstration of agent-orchestration
engineering, and the roadmap beyond the signature scenario includes multi-branch investigations,
checkpointed long-running graphs, and eventually multiple cooperating graphs (triage, remediation
planning, documentation). Building the hand-rolled version first and swapping to LangGraph later
would mean re-deriving graph-shaped concerns (state passing, conditional edges, checkpointing) a
second time.

## Decision
Use LangGraph as the graph *executor* starting with the signature scenario, but design the graph
itself — nodes, edges, conditions, budgets, state shape — explicitly and document it in
`docs/design/01-signature-incident-lifecycle.md` §6, so the reasoning topology is something the
author designed and can whiteboard without the framework's docs open, not something LangGraph
decided.

Concretely, LangGraph is responsible for: wiring nodes into a graph, executing conditional edges,
passing typed state between nodes. It is explicitly NOT responsible for: deciding which tools
exist, what the budget/iteration limits are, what "enough evidence" means, or how output is
validated — all of that is our code, reviewable independent of the framework.

## Consequences
- Every node is a plain async function with a typed input/output; LangGraph's `StateGraph` is a
  thin wiring layer over them, not the place business logic lives.
- The graph's structure (§6 of the design doc) must be reviewable as a diagram *and* as code —
  if the two disagree, that's a bug.
- If a future stage needs orchestration LangGraph doesn't help with (e.g. durable, crash-safe
  multi-hour waits), that's evaluated on its own merits, not assumed to be "LangGraph's job."
