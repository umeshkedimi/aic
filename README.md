# AIC — Agentic Incident Command

AIC is a production-grade Agentic Reliability Platform: a control loop that detects, investigates,
explains, and safely remediates production incidents, and learns from every one it handles.

> **Status: green-field reset.** Prior design docs and a Phase 1 scaffold existed in this repo's
> history and were deliberately cleared to rebuild around a single, deeply-engineered signature
> incident lifecycle rather than broad upfront scaffolding. See git history for that prior work.

## The standard this project is held to

This is not a demo, a tutorial, or "an LLM connected to some tools." It is a reliable production
control loop that uses AI agents where reasoning is genuinely valuable, and deterministic
engineering everywhere else. Depth on one complete lifecycle beats breadth across many shallow
ones — every stage below is engineered deliberately, not stubbed.

## The signature lifecycle

```
OBSERVE → DETECT → CORRELATE → INVESTIGATE → REASON → FORM RCA → PLAN REMEDIATION
        → APPLY POLICY → APPROVE → ACT → VERIFY → RESOLVE → LEARN
```

The first milestone is one polished, end-to-end walkthrough of this loop against a single
synthetic scenario: a bad deployment causes latency, 5xx errors, database connection pool
exhaustion, and downstream checkout failures. Everything else — more sources, more agents, more
integrations — is a depth pass added after that one path is solid, explainable, and tested.

## License

MIT
