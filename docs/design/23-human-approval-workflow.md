# 23. Human Approval Workflow

The approval gate is AIC's product-defining moment: a human, probably tired, possibly on a
phone, deciding whether software may change production. The design goal is a **decision-ready
30 seconds** — everything the approver needs, nothing they don't, and every failure mode of
the humans themselves (asleep, on a plane, disagreeing) handled by the machine.

Mechanics recap (specified elsewhere, consolidated here): the workflow pauses on a Temporal
signal (§8.3); quorum/role/expiry semantics are §19.3; Slack identity binding is §18.4; the
policy decisions that *create* approval requirements are §19.6.

## 23.1 The approval card (Slack Block Kit and web render the same contract)

```
🔶 APPROVAL REQUIRED — INC-2041 · SEV-2 · payment-service · prod
   Rollback payment-service to v2.14.0            [rule: prod-rollback v3 · quorum 1/1 · approver]

WHY  (confidence 0.85)
  v2.14.1 pool-size reduction is causing connection exhaustion.
  • Deploy v2.14.1 at 03:04 → p99 step-change at 03:06         [ev_01 ev_04]
  • 2/6 pods CrashLooping, connection-timeout errors           [ev_02]
  • PR #4312 reduced pool_size 50→10                           [ev_07]
  • Similar: INC-1854 (May) — rollback resolved in 6 min       [kb_112]

ACTION                                             DRY RUN
  RollbackRelease                                    deployment.apps/payment-service
  target: prod/payments/payment-service              image: v2.14.1 → v2.14.0
  rollback pairing: re-rollforward available          replicas unchanged (6)

⚠ none of the cited evidence is taint-flagged
⏱ expires 03:45 (in 28 min) → escalates to platform-oncall

[ ✅ Approve ]  [ ❌ Reject… ]  [ 🔍 Full incident ]
```

Contract rules, enforced by the card builder (`aic_contracts` model → renderers):

- **Evidence citations are links**, not prose claims — one tap to the timeline entry. The card
  never asserts anything the incident record can't back.
- **Dry-run output is mandatory** for actions that support it; its absence is displayed as a
  warning, not silently omitted.
- **Taint status is always stated** — including its absence. A missing "no taint" line (card
  built from stale contract) is a rendering error, visibly broken rather than silently reassuring.
- Reject requires a reason — but the picker offers categorized reasons (wrong diagnosis /
  right diagnosis wrong fix / not now / needs senior) so §22's online signals get structured
  data, and the human gets a one-tap path.
- The card shows *policy provenance*: which rule (and version) demanded this approval — the
  approver knows why they're being asked, and policy misconfigurations get spotted by the
  people they inconvenience.

## 23.2 The decision path (trust chain)

Slack tap → signed callback → identity binding → RBAC + request-role check → fresh-role DB
check → decision row (immutable) → quorum evaluation (serializable txn) → Temporal signal →
workflow proceeds/waits. The web path (`POST /approvals/{id}/decision`) joins at the RBAC
check. Every hop appends to the audit spine. Median human-side latency budget: the platform
adds < 2 s end-to-end (NFR-2.5's 10 s includes Slack's own delivery).

## 23.3 Escalation ladder (timeouts are policy, per rule)

```
requested ──T1 (e.g. 15 min)──► escalate level 1: re-notify + widen to escalation group
          ──T2 (e.g. +15 min)─► escalate level 2: page platform on-call (via org pager)
          ──T3 (e.g. +30 min)─► EXPIRE: no action taken, incident → escalated (human-owned)
```

Design commitments: **expiry is safe by definition** (the absence of approval never executes
anything — the incident simply escalates to fully human handling, which is where it would have
been without AIC anyway); escalation *widens* the audience, never *lowers* the required role;
SEV-1 ladders are shorter by policy, not by special-case code.

## 23.4 Human failure modes, handled

| Failure | Handling |
|---|---|
| Approver asleep / unreachable | ladder above; `aic_approvals_pending` age alert catches systemic slowness |
| Slack outage | web inbox is always-on parity (§12.2); escalation pages point at the web URL; Slack delivery failure fast-fails to level-1 escalation immediately |
| Two approvers race | idempotent, serializable decisions (§19.3) — second identical decision is a no-op `409`, conflicting decision loses to reject-wins |
| Approver approves then regrets | decisions are immutable; the remedy is operational: `POST /incidents/{id}/escalate` halts further actions; executed actions have rollback pairing |
| Rubber-stamping (approve-all culture) | time-to-decision histograms + per-approver decision latency in §21 dashboards; sub-5-second approvals on SEV-1 prod actions are a review-culture finding surfaced in the metrics, plus periodic "canary" scrutiny is a roadmap idea (§36) |
| Nobody has the required role at 3 a.m. | policy simulation (§12.4) + a boot-time policy lint: every `require_approval` rule must reference a role with ≥ N assigned members with paging coverage — misconfiguration is caught at policy write time, not incident time |

## 23.5 The rejection feedback loop (bounded)

On reject-with-reason, the workflow offers the planner **one** re-proposal cycle (FR-5.4): the
rejection category + text is added to the planner's context; a new proposal goes through the
full policy + approval path (no shortcut — a re-proposal is a new request). A second rejection
ends automation for this incident → `escalated`. Bounded on purpose: an agent negotiating with
a human who has already said no twice is not a feature, it's a nuisance with a token bill.

## 23.6 Auto-approval is still an approval

`auto_approve` outcomes get the same record shape: an `approval_request` auto-closed by
`actor_type=policy, actor_id=<rule@version>`, same card posted to the incident channel
(informational, with a **"Halt"** button that signals escalation) and the same audit trail. One
mental model, one query surface, one UI — whether the approver was a person or a rule. The Halt
button is load-bearing for trust: staged-autonomy customers (UC-5) get a human override on
exactly the actions they've chosen not to gate.
