---
name: review-pending
description: Show pending proposals for a project's loop and let the human approve, reject, or resolve a guardrail breach. The only path that can move a proposal to "applied" (via tools/apply.mjs) after explicit human approval. Use for "/review-pending <project> <loop>".
---

# /review-pending

This is the human approval gate in the approval state machine (`draft -> reviewed -> approved -> applied -> verified`). Nothing in this workspace auto-applies a proposal; this skill exists specifically so a human decision is required before any Tier-1 or Tier-2 side effect happens.

## Steps

1. List proposals:
   ```
   node tools/review-pending.mjs <project> <loop> --list
   ```
   Present each to the human: id, status, tier, action type, target, and whether it's flagged `[STALE]` (undecided for 2+ run cycles).
2. If the loop's `state.json` shows `paused-breach`, lead with that: explain the breach reason and ask whether the human wants to resolve it now via:
   ```
   node tools/review-pending.mjs <project> <loop> --resolve-breach --reason "<human's reasoning>"
   ```
   Do not resolve a breach without an explicit human decision — this is exactly the gate the plan requires ("blocks all new proposals until a human resolves the failed experiment").
3. For each proposal the human wants to act on, use `AskUserQuestion` to get an explicit approve/reject/hold decision — never assume approval from silence or from a general "looks good."
4. Apply the decision. `draft -> approved` is allowed directly (a single human review action can both review and approve, per AgentColabPlan.md's "human review moves it to reviewed/approved"), but if the human wants the fuller `draft -> reviewed -> approved` progression, offer `--review` as an intermediate step:
   ```
   node tools/review-pending.mjs <project> <loop> --review <id> --reason "<why, if just marking as seen/under consideration>"
   node tools/review-pending.mjs <project> <loop> --approve <id> --reason "<why>"
   node tools/review-pending.mjs <project> <loop> --reject <id> --reason "<why>"
   ```
5. **Applying an approved Tier-1 proposal is a separate, explicit step** — only run it if the human asks you to apply (not just approve) a specific proposal:
   ```
   node tools/apply.mjs <project> <loop> <proposal-id>
   ```
   `apply.mjs` re-checks the proposal is `approved` and Tier ≤ 1 itself and will refuse otherwise — treat that refusal as authoritative, do not retry with a workaround.
6. **Tier 2 proposals are always human-only outside this tool entirely.** Never call `apply.mjs` on a Tier 2 proposal (it refuses anyway) and never suggest pushing to a repo that auto-deploys on push (e.g. the operator's website repo — see RISK-REGISTER.md R6) as a way to "apply" something.
7. Phase 1 boundary: for `_demo` and any project without live connectors wired, `apply.mjs` only writes a local simulated marker file — it does not touch a real repo, API, or credential. Do not represent it to the human as a real external action while Phase 1 is in effect.
