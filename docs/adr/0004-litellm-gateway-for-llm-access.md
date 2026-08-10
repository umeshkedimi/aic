# ADR 0004: LiteLLM gateway for LLM access

## Status
Accepted (2026-08-10)

## Context
The Reason/Plan/Learn stages need LLM calls. A direct provider SDK (e.g. `anthropic`) wrapped in a
small `LLMPort` Protocol would be the fewer-moving-parts choice for a scenario that only calls one
provider today. The project's stated goal is model-agnostic architecture across Claude, OpenAI,
Gemini, and local models via Ollama — a stated requirement, not a hypothetical future need.

## Decision
Run LiteLLM as a proxy from day one, even though the signature scenario only calls one provider.
`aic_agents`/investigation code talks to LiteLLM's OpenAI-compatible endpoint through one
`LLMPort` implementation; provider/model selection is a LiteLLM config change (`litellm_config.yaml`),
not an application-code change. Per-agent-role model routing (cheap model for digest/title/scribe,
frontier model for synthesize) is expressed there too.

## Consequences
- One more local service to run (the LiteLLM proxy container), plus its config file, versioned
  in-repo like everything else.
- Token/cost accounting can come from LiteLLM's own usage tracking (it logs cost per call) in
  addition to AIC's own `llm_call` ledger — worth reconciling the two rather than trusting either
  blindly.
- Structured-output mode (Anthropic tool-use / OpenAI JSON mode) must be verified to pass through
  LiteLLM correctly for each provider used — this is the actual risk of a gateway layer: provider
  -specific features sometimes leak through imperfectly. Contract tests against the real proxy,
  not just against a direct provider call, are required before trusting this for structured RCA
  output.
- `LLMPort` stays a thin Protocol regardless — LiteLLM is the concrete adapter behind it, not a
  replacement for the abstraction boundary.
