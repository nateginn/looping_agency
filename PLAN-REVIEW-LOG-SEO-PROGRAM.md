# Plan Review Log: integrating `SEO_PROGRAM.md` into the Loop Agency process

Started 2026-08-11, ~00:45 local (MDT). `MAX_ROUNDS=10` (operator-set). **Converged at
round 8 with `VERDICT: APPROVED`.**

- Plan under review: `PLAN-SEO-PROGRAM-INTEGRATION.md`
- Builder / orchestrator: Claude (Opus 5)
- Adversarial critic: OpenAI Codex (`codex-cli 0.144.1`), read-only sandbox every round,
  single resumed session `019fefa2-3ea8-7d71-8ca5-78c1bb916ed7` so it could not
  re-litigate settled points
- Context: Nate asked for this to be worked out overnight, unattended, and stated he would
  be unavailable to answer questions — so disagreements were resolved between the two
  models and every unresolved judgment call is surfaced explicitly in the plan rather than
  silently decided.

## How the plan changed, in one table

| Round | Codex verdict | The finding that mattered most |
|---|---|---|
| 1 | REVISE (18 findings) | The proposed CTR-curve selector is empirically degenerate on this site's data. Forced the measurement check that reframed the whole plan. |
| 2 | REVISE (16) | The local-pack explanation was being stated as a finding when it is a hypothesis; "no organic presence" overreached. |
| 3 | REVISE (16) | P0 was described as free and safe while it touches a live, scheduled, auto-implementing loop. Recommended freezing automation first. |
| 4 | REVISE (10) | The re-enable gate was set after P3, far too early; and my backfill claim was factually wrong. |
| 5 | REVISE (6) | **The freeze recommendation named the wrong switches** — `approval_mode` is the only one that fails closed on both apply paths. |
| 6 | REVISE (1) | P7's cannibalization signal was sequenced after the step that requires it. |
| 7 | REVISE (1) | The page→primary-query map was sequenced after the step that requires it — the sequence was literally unexecutable. |
| 8 | **APPROVED** | Dependency order confirmed executable end to end. |

## What the argument actually produced

Three defects in live code that nobody had recorded, all found while checking Codex's
claims rather than accepting them:

- **D1** — `dataforseo.py:165/:261` match SERP results by URL path substring with no domain
  check, so a competitor's URL containing `/physical-therapy/` is reported as this site's
  rank. Paid connector, silently wrong data, feeds an attention threshold.
- **D2** — both call sites discard the SERP composition (`local_pack`, `ai_overview`, …)
  that the loop already pays to receive, and which would answer the central diagnostic
  question.
- **D3** — `min_sample_size` is compared against the run's *total* GSC sample, so the test
  passes unconditionally and "verified winner" currently means only "position did not get
  five places worse".

And one measurement that reordered the plan's entire priority list: `art`'s organic
web-search channel produced **23 clicks in 28 days, 20 of them brand-navigational**, with
zero clicks on the five highest-impression commercial queries — three of which sit at
average position ~1.0.

---


## Round 1 — Codex

VERDICT: REVISE

The plan preserves R6/R10/R14/R15/R16 boundaries: it does not authorize push, deploy, rollback, or a new auto-implementable action. It still has material problems.

1. **Critical — A1 is empirically degenerate for this site.** The latest GSC snapshot has 910 rows and 10,308 impressions, not “thousands of rows”; [`gsc.py:77`](</D:/Dev/Looping _agency/tools/gsc.py:77>) fetches at most 1,000 rows with no pagination. In the latest data, position buckets 3–8 and 10–20 have zero clicks, so the proposed site curve produces `expected_ctr = 0` and `expected_click_gain = 0` for most useful candidates. A one-row bucket also self-calibrates to the candidate’s own CTR and scores zero.

   Fix: do not ship this curve as specified; first fetch all rows, use pooled/smoothed position estimates with minimum bucket impressions and uncertainty handling, and explicitly return “insufficient signal” when the site cannot estimate CTR.

2. **High — `expected_click_gain` is not the claimed objective.** `impressions × CTR gap` estimates clicks under an observational site average; it does not estimate the uplift from changing a title or meta description. It also ranks position-11–20 content/authority problems even though the proposed actions cannot address them, and it ignores calls, forms, bookings, and revenue.

   Fix: use the score only as a diagnostic ranking, require an actionability diagnosis before proposing a rewrite, and do not call it expected business value until conversion data exists.

3. **High — the 100-impression floor contradicts `SEO_PROGRAM.md` Part 7, Conflict 1.** That section explicitly concludes that high-intent local terms may justify volumes in the tens, while the plan adopts 100 without CPC, conversion, or intent data available in GSC. The claim that a “high-CPC local term” would clear the floor is impossible to evaluate from the current inputs.

   Fix: make the floor tiered by approved intent/page class, or use a calibrated uncertainty rule; do not install 100 as the default.

4. **High — the `2.0` priority multiplier is arbitrary and currently mismatches URLs.** `priority_pages` stores HTTPS absolute URLs, while current GSC rows include HTTP URLs; direct comparison will miss pages such as `/massage/`. The homepage is also not in `priority_pages`, despite current proposals targeting it. A multiplier does not enforce priority and is not supported by Part 2’s “two dashboards” principle.

   Fix: normalize to page paths and use explicit priority tiers or a human-approved target allowlist instead of an unexplained default multiplier.

5. **High — “choose action type from diagnosis” cannot work in the current structure.** [`_pick_new_actions`](</D:/Dev/Looping _agency/tools/run_loop.py:668>) receives only GSC rows and a cooldown set; it has no current title/meta, rendered HTML, SERP features, conversion data, or page-contract context. It also cannot distinguish whether a CTR issue needs a title or a meta rewrite. The existing cooldown key is page-only, so scoring `(query,page)` still blocks every other query on that page after one proposal.

   Fix: split candidate scoring, diagnosis, and action selection; persist a page-two/insufficient-evidence finding without proposing a rewrite, and make cooldown identity explicitly query-plus-page if that is the intended unit.

6. **High — A2’s new evidence can be inconsistent with the proposal that was created.** [`codex_review_proposal.py:188`](</D:/Dev/Looping _agency/tools/codex_review_proposal.py:188>) builds packets from the latest snapshot, not `proposal.created_run_id`; technical and daily-rank runs can carry stale GSC data. Recomputing A1’s score later can therefore show a different candidate rank or curve than the one used to create the proposal.

   Fix: store immutable selector inputs and outputs on proposal creation—source snapshot/run, curve version, score, rank, and policy—and build the packet from that creation snapshot.

7. **High — the proposed spec fields are not addressed in the review hash.** [`SPEC_HASH_FIELDS`](</D:/Dev/Looping _agency/tools/lib/review_protocol.py:21>) excludes `min_impressions_for_proposal`, `priority_page_weight`, and `candidate_position_band`. They may not change implementation authorization directly, but they change why a proposal was selected. Changing them after review would leave the same `spec_content_hash` and permit an old review to remain eligible with stale selection evidence.

   Fix: either include those fields in the authorization hash or add a separate selector-policy hash to the evidence packet and proposal-content hash; add corresponding tests.

8. **High — A2’s hash-prefix fix requires code and skill changes, not documentation.** [`record_round`](</D:/Dev/Looping _agency/tools/codex_review_proposal.py:199>) currently requires exact 64-character equality, while the skill prompt requires exact echoing. The event log also has a bounded extra-field allowlist at [`event_log.py:31`](</D:/Dev/Looping _agency/tools/lib/event_log.py:31>); new selection evidence will be silently dropped unless that schema is extended.

   Fix: implement prefix validation against the persisted full hash, update `.claude/skills/codex-seo-review/SKILL.md`, and add event/hash regression tests.

9. **High — B1 is not a cheap connector and its stated checks exceed plain HTTP GET.** A GET can inspect response HTML, but not rendered DOM completeness, client-side execution, mobile layout, or actual browser link behavior. The connector does not exist in `connector_registry.py`, `_dispatch_connector`, `spec_validate.py`, snapshot schema, or report rendering. Existing `technical_health` is a fixed pagespeed/indexation structure and would require deliberate schema changes.

   Fix: scope B1 honestly as a server-response audit with a separate `page_audit` snapshot section, or budget a headless-browser implementation; crawl all proposal targets if uniqueness is a gate, not just `priority_pages`.

10. **High — A5 does not match the program’s machine criteria.** `SEO_PROGRAM.md` Part 6 requires the primary query in the title, H1, and first 100 words; the plan proposes title and meta presence instead. “Naturally” cannot be machine-checked, and `draft_copy.py` currently validates only string shape/length. B1’s priority-page-only inventory also cannot prove uniqueness across the site, while A1 still proposes from all GSC pages.

   Fix: keep naturalness and medical meaning as Codex/human criteria, implement only deterministic checks, and either restrict proposals to audited pages or audit the full canonical sitemap.

11. **High — the sequencing claim that A1+A2 “unblocks” the two held proposals is false.** Those proposals remain `review-revision-needed`; A1 affects only future selection, and A2 cannot manufacture evidence for 2 clicks/15 impressions or 0 clicks/1 impression. Cooldown continues to block new homepage proposals until those proposals become terminal. The review skill also still contains the contradictory “never runs against `art`” line despite its prerequisite saying the opposite.

   Fix: add a separate disposition step for the existing proposals and correct the skill contradiction before claiming the next cycle is operational.

12. **Medium — the plan overstates the existing `#1` guardrail.** `3 < position <= 20` only constrains new proposals from `_pick_new_actions`; it excludes positions 1–3, not merely #1, and [`apply.py`](</D:/Dev/Looping _agency/tools/apply.py:214>) does not enforce the rule against manually or externally created proposals.

   Fix: describe this as a selector heuristic, or enforce the guardrail at review/apply time with an explicit exception process.

13. **Medium — A3/A4 are not “cheap, no-behavior-change” additions.** A3 requires new search-analytics logic in `_evaluate_attention`, report output, `run.json` fields, tests, and likely snapshot-history handling. A4 needs deduplication, decision identity, persistence semantics, and a CLI; appending the same below-floor/cooldown candidate every scheduled run will create a noisy queue. It also creates a second durable history beside `events.jsonl`.

   Fix: define an idempotent queue key and lifecycle, acquire the existing run lock, decide whether queue writes are authoritative or best-effort, and add the new findings explicitly to the run contract/tests.

14. **High — the plan omits the highest-value safety context for this medical business.** `SEO_PROGRAM.md` Phase 0.1/P1 requires a business brief and `PROJECT_RULES.md` covering services actually sold, prohibited claims, credentials, pricing, locations, and proof. The plan identifies this gap but only proposes voice files in C3. That is inadequate for physical therapy/chiropractic copy and medical claims.

   Fix: add an owner-approved ART business brief and rules artifact before A5 or any content drafting.

15. **High — it omits the business dashboard from Part 2 and Part 6.** The loop optimizes GSC position/clicks, while the stated project goal is leads and calls. A priority-page multiplier is a weak proxy for business value without call, form, booking, or qualified-lead measurement.

   Fix: add a conversion-measurement track, or explicitly downgrade “lead-intent weighting” to a human assumption until conversion data is connected.

16. **Medium — it covers information-page capsules before fully specifying the money pages that generate clinic revenue.** `SEO_PROGRAM.md` Parts 2–3 and P5 require service-page contracts, H1/CTA clarity, proof, schema, FAQs, local substance, and publishability. C1/C2 do not create a service-page audit/drafting track for the six actual ART service pages.

   Fix: add a service-page contract and human evidence review before investing in capsule production.

17. **Medium — B3 understates the GBP integration cost and credential difference.** Google’s Business Profile APIs require OAuth 2.0 and API enablement/setup, not simply reuse of the existing GSC service-account arrangement; quota/access approval can also be separate. See [Google’s OAuth requirements](https://developers.google.com/my-business/content/implement-oauth) and [Business Performance API reference](https://developers.google.com/my-business/reference/performance/rest). ART’s spec also contains a UNC Campus footer location that must not be treated as a third GBP profile.

   Fix: model two explicit Greeley/Denver GBP identities, obtain OAuth/API access as a separate prerequisite, and keep UNC Campus footer-only.

18. **Medium — local authority work is omitted despite Part 5.4/5.5 and Appendix A.** The plan has GBP measurement/checklists but no manual workflow for citations, chambers, local partnerships, competitor profile audits, or ethical review operations—all likely higher-value for a two-location clinic than AI-visibility measurement.

   Fix: add a human local-operations checklist and competitor/authority backlog, with no automated review solicitation or link acquisition.

VERDICT: REVISE

### Claude's response

Verified every checkable claim rather than accepting it, and Codex was right on all of
them — including the one that mattered. Computing the CTR-by-position curve from the actual
snapshot returned 910 rows, 10,308 impressions and **23 clicks**, with positions 4–8 and
10–20 containing zero clicks in total. The proposed `expected_click_gain` objective is
therefore zero almost everywhere.

Checking further found three defects Codex had not named (D1, D2, D3 above), and the
DataForSEO local-rank section turned out to be reporting competitors' URLs as this site's
rank.

**Accepted:** 1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18 — including the
promotion of GBP/local work, the business brief for a medical practice, and the business
dashboard.

**Partially rejected:** #3. Codex read the 100-impression floor as contradicting
`SEO_PROGRAM.md` Conflict 1 ("set the floor by expected value, not volume"). I argued these
answer two different questions — whether a term deserves a page, versus whether an
experiment can be measured — and that both thresholds are needed. I did accept removing the
arbitrary 100 default. Codex conceded the distinction in Round 2 while correctly attacking
the implementation.

**Rewrote the plan around the data**: P0 correctness/diagnosis first, GBP second, selector
work demoted from first to fifth.


## Round 2 — Codex

Round 1 is substantially better, and the P0-first direction is correct. It still needs revision.

1. **High — the local-pack hypothesis is plausible but not yet supported by the evidence presented.** GSC position is a position for a result/link, not a `local_pack` classification; the current GSC pull has no search-appearance or SERP-feature dimension. Google documents Web as the default search type and position as the average/topmost result position, but that does not identify which SERP element produced it. [Search Console metrics](https://support.google.com/webmasters/answer/7042828), [Search Console reports](https://support.google.com/webmasters/answer/10268906).

   Fix: run P0.2 against the exact high-impression GSC queries—`auto injury chiropractor`, `sports chiropractic near me`, `chiropractor near me`, etc.—not merely the 12 city-level spec targets, using matching geography/device; call the result a hypothesis until then.

2. **High — the “no organic presence” claim overreaches even after D1.** `dataforseo.py` currently asks whether the configured target page appears in the top 100; a null result after domain/path matching means “that exact page was not found,” not “the domain had no organic result.” Another ART URL, canonical URL, redirect target, or homepage could rank.

   Fix: report both `target_page_rank` and `domain_presence_top100`, scanning every organic result for the domain and recording the matching URL(s).

3. **High — D2 is useful for SERP diagnosis but does not measure ART’s AI visibility.** Recording `ai_overview` proves only that an AI Overview appeared for the query; it does not show whether ART was cited, which ART URL was cited, or whether the citation was visible. Likewise, `local_pack` presence does not prove ART appeared in the pack.

   Fix: name `serp_features` “SERP composition,” separately record `site_in_local_pack` and `site_in_ai_overview`, and keep actual AI citation/mention measurement as unresolved.

4. **High — P0.1 needs a more complete implementation contract.** Both matching functions lack a domain argument: `pull_metrics()` and `pull_local_rank()` currently receive targets but not `spec.domain`. The fix must propagate the domain through `_dispatch_connector`; otherwise the proposed host check cannot actually be performed. It must also handle absolute versus relative target paths, redirects/canonicals, ports, query strings, and trailing slashes.

   Fix: add a shared URL-normalization/matching helper with explicit `domain` parameters and tests for canonical/redirect aliases, not just `www.` and prefix collisions.

5. **High — P0.5 is not a small change and can break the existing evaluation contract.** To determine “zero clicks both before and after,” proposals need baseline clicks; current proposals store baseline position only. Moving the sample check to target-row impressions also requires matching the row before the sample test, changing packet semantics and potentially leaving low-impression proposals in `applied` indefinitely.

   Fix: persist `baseline_clicks` and baseline impressions or bind evaluation to the creation snapshot; define a distinct `not-evaluable: insufficient-target-impressions` outcome.

6. **High — `verified-no-signal` must be specified as either a status or an outcome.** Current consumers hard-code `TERMINAL_PROPOSAL_STATUSES`, `evaluated_now`, event emission, report rendering, and reconciliation around `verified` and `breached`. The plan says “outcome alongside” them but does not say whether status remains `verified`.

   Fix: preferably retain terminal status `verified`, set `evaluation_outcome: verified-no-signal`, and update reports/events/tests explicitly; otherwise update every state-machine consumer for a new terminal status.

7. **Medium — P0.3’s pagination language is too strong.** The Search Analytics API does not guarantee that pagination retrieves every query/page row; Google documents internal row limits and truncation. [Search Analytics API](https://developers.google.com/webmaster-tools/v1/searchanalytics), [data grouping and truncation](https://support.google.com/webmasters/answer/17011259).

   Fix: paginate where possible, but call the result `returned_row_count`; do not claim a true total unless using an appropriate aggregate or bulk-export source.

8. **Medium — the finding says “zero clicks on every commercial non-brand query,” but the same snapshot has three clicks on non-brand service-like queries.** `dry needling near me`, `stellate ganglion block near me`, and `best vestibular doctor near me` each receive one click. The listed five high-impression queries have zero clicks, but the broader sentence is false.

   Fix: say “zero clicks on the five highest-impression commercial queries listed” and separately identify the three off-target/non-priority non-brand clicks.

9. **Medium — the measurability-floor distinction is conceptually valid, but the proposed implementation is still wrong.** `SEO_PROGRAM.md` Part 7, Conflict 1 answers whether a topic deserves a page; experiment measurability answers whether a title/meta change can be evaluated. Those are different decisions. However, the current `min_sample_size` field is used by `_evaluate_prior_experiments` as an evaluation gate, not as a page-targeting floor, and a useful floor requires an explicit minimum detectable effect/power assumption. A fixed impression count alone does not establish that.

   Fix: create separate fields such as `target_opportunity_floor` and `min_target_impressions_for_evaluation`; use the latter only to block auto-implementation/evaluation, and route low-volume opportunities to human/content work rather than rejecting them.

10. **Medium — the P5 fallback can still produce meaningless rankings.** On this site, many position buckets have enough impressions to “estimate” CTR but zero clicks, so “insufficient signal” cannot mean only “too few impressions.” Zero observed CTR with 700 impressions is mathematically an estimate, but not evidence that a rewrite can produce uplift.

   Fix: distinguish `no_curve_estimate`, `zero-observed-CTR`, and `no-actionable-uplift-signal`; do not fall back to impression ranking unless the downstream path guarantees a finding rather than a rewrite proposal.

11. **Medium — P2’s “links are not this site’s binding constraint” is unsupported.** Eighty-one referring domains says nothing about their quality, relevance, local authority, or comparison with ranking competitors. The current loop has no competitor backlink comparison.

   Fix: rephrase this as “no conclusion yet”; make competitor-quality comparison the prerequisite for deprioritizing authority work.

12. **Medium — P3’s proposed business brief gate is not enforceable by the current code.** `PROJECT_RULES.md` would be an artifact, but `apply.py` does not read it, `review_protocol.py` does not hash it, and the Codex skill prompt does not currently require or verify it. The current three gates would still allow auto-implementation if the artifact were absent or changed.

   Fix: either make P3 an explicit operational prerequisite before re-enabling ART auto-implementation, or add a rules-file presence/hash check to the review/apply path.

13. **Medium — P4 is correctly promoted, but the measurement definition is too weak.** Monthly human-entered calls/forms plus GBP calls/directions are not necessarily organic leads, booked patients, or qualified conversions.

   Fix: define source, period, attribution, qualified-lead criteria, and booking outcome in a small business-dashboard artifact before using those metrics to rank SEO actions.

14. **Sequencing issue — P4 should precede P5/P6, not follow them.** Even the rescoped selector needs to know whether it is optimizing search visibility, local discovery, or business outcomes. P5 can be implemented as a diagnostic-only selector before P4, but the plan should not call the ordering “P5 + P6, then P4” if P4 determines what priority means.

   Fix: move the minimum business-measurement definition before P5; defer only the connector implementation if necessary.

15. **Missing for Nate tomorrow — documentation drift remains beyond the skill contradiction.** `projects/art/project.md` still describes ART as `propose-only` with all actions manual, contradicting the active `spec.md` and current Phase 7 documentation. P0.4 fixes the skill contradiction but does not include this operator-facing contradiction.

   Fix: add a P0 documentation-sync step covering `project.md`, `spec.md`, `CLAUDE.md`, `HANDOFF.md`, and the risk register.

16. **Missing from the new diagnosis — verify indexability/canonical identity before interpreting zero clicks.** The plan jumps from SERP features to GBP, but P0 does not explicitly confirm that the queried pages are indexed, canonical, and eligible for the relevant search appearance. P8 audits this later, though it can affect the local-pack conclusion now.

   Fix: add a targeted P0 check for the five high-impression commercial query/page pairs: canonical URL, indexation state, rendered title/snippet, and whether the page—not merely the domain—was the shown result.

The reorder is directionally right, but P0.2/P0.3 currently support a local-pack hypothesis only indirectly, and P0.5 needs a fuller state/provenance design before implementation. The floor distinction is not wrong; the plan must separate opportunity qualification from experiment evaluability in the schema and evaluation logic.

VERDICT: REVISE

### Claude's response

All 16 accepted. The two that changed the plan's character:

- The local-pack reading was being presented as a conclusion. GSC reports a position for a
  result, not a classification of which SERP element produced it. It is now labelled a
  hypothesis everywhere it appears, with P0.2 and P0.7 designed to test it against the exact
  five queries.
- "No organic presence" overreached: a null DataForSEO result means *the configured page*
  was not found, not that the domain is absent. P0.1 now reports `target_page_rank` and
  `domain_presence_top100` separately.

Also verified and accepted: `spec.domain` is not propagated to `pull_local_rank`
(`run_loop.py:348` passes targets/locations/language_code/device but not domain, while the
adjacent backlinks branch at `:370` does), so the domain check Codex asked for needs
plumbing before it is even possible; `TERMINAL_PROPOSAL_STATUSES` is a fixed set at
`run_loop.py:63`, so `verified-no-signal` became an `evaluation_outcome` rather than a new
status; and `project.md:53` still describes the loop as `propose-only`, contradicting the
live spec.

Corrected a factual error of my own: "zero clicks on every commercial non-brand query" was
false — three non-brand clicks exist, on `dry needling near me`, `stellate ganglion block
near me` and `best vestibular doctor near me`. None is a tracked target, which is itself a
finding.


## Round 3 — Codex

Round 2 is materially safer and the central diagnosis is now honest. The sequencing still has live-loop hazards and one important circular dependency.

1. **Critical — P0 is not “no new surface” or zero-cost.** P0.2’s five `diagnostic_queries` require new DataForSEO SERP tasks; the current connector only submits the 12 configured target tasks. That is additional runtime and likely additional paid usage. P0.3 pagination changes the returned dataset, and P0.5 changes evaluation semantics.

   Fix: split P0 into offline code/tests, one controlled live diagnostic run, and only then scheduled activation; obtain explicit cost approval for the extra SERP tasks.

2. **Critical — freeze auto-implementation before P0 begins.** P3’s “operational prerequisite” is not enforced by the current code, and an approved rules file is not yet a gate. Until P3’s hash check exists, `art` remains eligible under its existing three gates. Documentation sync alone does not prevent a future Codex-reviewed proposal from reaching `apply.py`.

   Fix: before changing evaluation or selector behavior, set `auto_implementation_enabled: false` or make the two rewrite actions `manual_approval_only: true`; restore them only after P3’s artifacts, hash check, migration, and offline/live smoke tests pass.

3. **High — P0.2 still lacks an executable cost/location design.** `diagnostic_queries` needs geography, device, and probably one or more locations; the current `pull_local_rank()` contract is target-page-oriented and would otherwise either fabricate a page target or run the five queries only once at an arbitrary location. “Exact queries” is correct, but “matching geography” is not yet specified.

   Fix: define each diagnostic as `{query, locations, device}`, state the number of new SERP tasks, and run it outside the normal weekly full pass until cost and runtime are confirmed.

4. **High — P0.7 does not currently inspect the pages behind the five rows.** Four of the five high-impression queries map to the homepage, while `gsc-indexation` only inspects `priority_pages`, which excludes the homepage. URL Inspection also does not provide the actual rendered title/snippet that P0.7 says it will examine.

   Fix: derive `diagnostic_pages` from the five GSC rows, include the homepage explicitly, and use a live HTTP/page-audit fetch for title/snippet; reserve URL Inspection for indexation/canonical status.

5. **High — P0.5 needs an existing-proposal migration rule.** Changing `min_sample_size` from total-run impressions to target-row impressions changes the meaning of every existing proposal. The current repository has no applied proposals, but the plan should not rely on that remaining true. Older proposals also lack `baseline_clicks` and `baseline_impressions`.

   Fix: before deployment, enumerate `applied`/`implemented` proposals; backfill provenance where possible, otherwise mark them `not-evaluable` with a recorded reason and preserve their cooldown.

6. **High — “bounded not-evaluable escape” is underspecified.** If a proposal remains `status: applied`, it remains non-terminal and continues to block cooldown. If it becomes terminal, `TERMINAL_PROPOSAL_STATUSES`, event reconciliation, and reporting must all agree. The plan does not define the state transition after the streak threshold.

   Fix: specify whether the escape is terminal or merely an attention outcome; retain `status: applied` only if the cooldown behavior is explicitly desired and visible.

7. **High — P5/P6 precede the evidence needed for P5’s action diagnosis.** P5 says action selection requires current title/meta, rendered HTML, SERP composition, and conversion context, but P8 supplies the page audit later. Therefore P5 cannot safely propose rewrites in the stated sequence; it can only emit findings.

   Fix: either declare P5 diagnostic-only until P8 ships, or move the minimal P8 page audit and page-to-primary-query map before the proposal-producing part of P5.

8. **High — P8 itself depends on P9 contracts.** “Primary query present in title/H1/first 100 words” cannot be checked for every page until each page has an approved primary query. The GSC query list is not a page-contract authority and includes noisy/unrelated queries.

   Fix: move a minimal approved page/primary-query map from P9.2 ahead of P8, or defer query-alignment checks until after P9.2 and keep P8 structural-only initially.

9. **High — loosening cooldown to `(query,page)` can permit multiple same-page changes.** `_pick_new_actions` does not reserve newly selected targets during the same run. A query-plus-page cooldown can therefore create several proposals for one page in one pass, including competing title/meta changes, increasing the auto-implementation blast radius.

   Fix: reserve pages during selection and cap one in-flight rewrite per page until the previous experiment is terminal, even if cooldown identity becomes query-plus-page.

10. **Medium — P0.3’s “paginate” fix still needs a selector policy.** Google explicitly warns that Search Analytics has internal truncation and does not guarantee every row, so the revised `returned_row_count` language is correct. But adding pagination can materially change candidate ordering and proposal creation.

   Fix: deploy pagination as reporting-only first, compare old/new row sets, and do not switch selector behavior in the same live run.

11. **Medium — P0.1 can still create false negatives if canonical/redirect aliases are common.** The plan correctly says the SERP response cannot resolve aliases, but then treats exact target-page matching as the primary rank. A canonicalized or redirected ART URL may be the correct ranking result.

   Fix: report exact target rank, domain rank, matched URL, and a separate canonical/redirect reconciliation result; never collapse those into one position.

12. **Medium — P3’s rules-file hash check needs more design before it is executable.** The plan names presence and content hash but does not specify canonicalization, storage location in the evidence packet, which hash is persisted in events, or whether changes invalidate only auto-implementation or all review.

   Fix: define a deterministic UTF-8 content hash, persist it in the packet/proposal/event, and make `apply.py` refuse auto-implementation when the current hash differs.

13. **Medium — the P7 queue’s “upsert” model can overwrite human decisions.** An occurrence-count update under the run lock is safe for machine fields, but a human decision field must not be replaced by the next run. The plan does not define ownership of that field.

   Fix: separate machine observations from human decisions, or enforce field-level merge semantics that never overwrite a non-empty human decision.

14. **Medium — P8’s “14-page canonical set” is not yet a canonical set.** Fourteen unique GSC pages are merely pages returned by one query/page export; they exclude pages with no GSC impressions and may include protocol variants. HTTP-fetching 14 pages is cheap, but discovering all canonical indexable pages requires sitemap/crawl input.

   Fix: define the crawl source as sitemap plus discovered canonical links, and report GSC-only coverage separately.

15. **Medium — P1’s review metrics should not be assumed to come from the Performance API.** Profile performance metrics and review count/rating/recency are distinct data surfaces and may have different API permissions and endpoints.

   Fix: scope GBP performance, reviews, and NAP as separate connector capabilities with separate access checks and graceful degradation.

16. **Medium — the guardrail statement about service areas remains too broad.** Verifying the three footer locations does not prove that future GBP service-area claims, neighborhood pages, or local authority artifacts describe areas actually served.

   Fix: narrow the statement to “configured physical locations match the footer” and keep service-area approval as a human rule.

What I would not do as written:

- I would not add the five diagnostic queries to the recurring scheduled run before confirming DataForSEO task cost and location semantics.
- I would not deploy P0.5 while auto-implementation remains enabled without first checking/migrating all applied proposals.
- I would not allow P5 to create rewrite proposals before P8 supplies page evidence and P9 supplies primary-query mapping.
- I would do P0.1, P0.3, P0.6, and a narrowly scoped P0.7 first, but in a paused or manual-only operating state.

VERDICT: REVISE

### Claude's response

All 16 accepted. Codex's central point — that this plan touches a live, scheduled,
auto-implementing loop and had been describing its first phase as free and safe — was
correct, and P0 is now staged into offline / one controlled live run / scheduled activation.

**Adopted Codex's recommendation to freeze auto-implementation**, as a new "Step 0" section.

**One factual correction pushed back on Codex's framing**: it treated the freeze as
critical-urgent. Verified with `Get-ScheduledTask` that the next proposal-generating run is
Monday 2026-08-17 06:00; the Thursday and daily jobs run `technical-only` and skip
`_pick_new_actions` entirely; and `/codex-seo-review` has no scheduler at all (R14). So
nothing can auto-implement unless Nate types a command, and there is a six-day runway. The
plan says it that way — a considered recommendation, not an alarm. Codex accepted this
framing in Round 4 as "factually correct".

Also verified and sharpened Codex's #4: **all five** zero-click query rows resolve to the
homepage, and the homepage is not in `priority_pages`, so `gsc-indexation` has never
inspected the page this entire diagnosis is about.

**Deliberately not done:** I did not flip the spec myself. It alters the live operating
state of Nate's own site's loop, nothing forced it overnight, and it is his call.


## Round 4 — Codex

Round 3 is substantially safer, and the six-day framing is factually correct. It does not understate overnight urgency; it does become misleading if Nate reads it as permission to restore automation after P3. Material issues remain.

1. **Critical — sequencing re-enables automation too early.** The sequence restores `auto_implementation_enabled` after P3, while P0.5, P0.3, P8, and the new selector are incomplete. The old `_pick_new_actions` can still generate proposals, and an on-demand review can still create a local commit.

   Fix: keep automation disabled through P0.5, P0.3, P8/P9.2, evidence/hash changes, and a final human go/no-go. The six-day runway is a reason not to rush, not a reason to re-enable.

2. **High — P0-b has no executable one-shot mechanism.** `run_loop.py` reads `spec.inputs`; adding `diagnostic_queries` to the spec risks making the daily `dataforseo-local-rank` task run all 10 paid queries repeatedly. `gsc-indexation` likewise currently derives pages from `_absolute_pages(spec)`, so `diagnostic_pages` needs an activation boundary.

   Fix: define a non-scheduled diagnostic mode/command with explicit inputs, separate output, no proposal/evaluation path, and tests proving scheduled jobs cannot invoke it.

3. **High — P0-a falsely promises “no scheduled-run behaviour change.”** A corrected `dataforseo.py` or `_evaluate_prior_experiments` in the live workspace is immediately used by scheduled Python jobs; there is no deployment isolation.

   Fix: either make P0-a design/tests only, or require Step 0 freeze before activating code and state exactly which scheduled outputs may change.

4. **High — the historical backfill claim is false.** The plan says every historical `organic_rank_position` for those two queries is wrong, but several snapshots contain ART’s own URL, including 2026-08-04 and 2026-08-07. The defect makes rows untrusted; it does not make every row false.

   Fix: quarantine only rows whose `result_url` host is not ART’s domain; preserve host-validated historical rows and label the rest unverifiable.

5. **Medium — P0.2 still contradicts itself.** Its heading says “zero marginal cost,” while the body correctly says it creates 10 new paid tasks.

   Fix: rename it “capture SERP composition; 10 paid diagnostic tasks.”

6. **Medium — P0.2 does not specify the persistence path.** `dataforseo.py` returns SERP data, but `_merge_metrics()` currently preserves only `serp_position`; discarded `item_types` will remain discarded unless merge, snapshot, report, and tests are explicitly changed.

   Fix: name the `dataforseo.py` → `_merge_metrics()` → snapshot/report fields and add a fixture asserting they survive the merge.

7. **Medium — P0.5’s terminal escape will be invisible or misreported.** Current `run_loop.py` emits verification events only for outcomes `"verified"`/`"breached"`, while `event_log.py --reconcile` maps other outcomes to `proposal_verified`. `never-evaluable` could therefore release cooldown without an accurate event or MMC briefing.

   Fix: specify the event type and update `run_loop.py`, `event_log.py`, reconciliation, report rendering, MMC fields, and tests together.

8. **Medium — the “two floors” decision is conceptually right but not executable yet.** `spec.md` still contains `min_sample_size: 100/150`; P5 introduces new names without defining their schema, required values, or fail-closed behavior.

   Fix: explicitly migrate/remove `min_sample_size`, require an authored MDE/evaluation policy, and reject missing evaluation configuration rather than silently retaining 100.

9. **Low but misleading — “rendered title and snippet.”** A live HTTP fetch returns server HTML (`<title>` and meta description), not Google’s rendered SERP snippet. P8 correctly limits itself to server response, but P0.7 does not.

   Fix: say “server HTML title and meta description”; leave Google snippet measurement unresolved.

10. **Medium — P0.6’s documentation inventory is incomplete.** `projects/art/loops/seo/instructions.md` still says the loop is propose-only and every action is manual. Several `HANDOFF.md` sections also retain stale current-state language.

   Fix: explicitly update or mark historical `instructions.md`, `HANDOFF.md`, `project.md`, and the skill together.

I would do P0.1’s correctness fix and P0.5’s design work, but only under the freeze. I would not activate P0.2/P0.7 through the recurring spec, deploy P0.5 semantics while automation is enabled, or restore automation after P3 alone.

VERDICT: REVISE

### Claude's response

All 10 accepted. The two that mattered:

- **#1, the re-enable gate.** Round 3 had automation restored after P3. That was the most
  dangerous error in it: P3 makes an automated rewrite's *content* defensible but says
  nothing about target selection, measurability, or page evidence. Restoring automation
  moved to step 10, behind P0.5, P0.3, P8-structural, P7's detector, P9.2's map and P6's
  evidence/hash changes, on an explicit recorded go/no-go.
- **#4, my backfill claim was false.** I had written that every historical
  `organic_rank_position` for the two affected queries was wrong. Checking every snapshot
  showed ART's own URL at ranks 37/33/34/50 on 2026-08-04 (×2), 08-07 and 08-08, with
  `mgmc.org` on the other seven runs. So the rows are *untrusted, not uniformly wrong*, the
  remedy is to quarantine only non-ART-host rows — and ART genuinely ranks around position
  33–50 in Greeley, which is a real finding the plan now states.

Also accepted: there is **no deployment isolation** — the scheduled tasks run
`tools/scheduled/task.cmd` against this working tree, so "offline" can only mean "no new
live API surface", never "scheduled jobs are unaffected". And putting `diagnostic_queries`
in `spec.md` would hand the daily job 10 extra paid tasks every day forever.


## Round 5 — Codex

Round 4 fixes most substantive issues, but it is not yet safe to hand over unchanged. Two factual contradictions remain, including one affecting the live freeze.

1. **High — the freeze mechanism is overstated.** `apply.py` rechecks `auto_implementation_enabled` only for `approved-for-implementation`. A proposal manually moved to `approved` can still proceed through `apply.py`; existing proposals also retain their cached `manual_approval_only: false`. Thus the two spec edits do not “genuinely disable the path” if Nate manually approves and applies an existing proposal.

   Fix: state that the switch disables unattended Codex auto-implementation only, or add an explicit global freeze enforced for both apply paths and migrate/hold existing proposals.

2. **High — P0.2 still contradicts its one-shot design.** It says to add `diagnostic_queries` to `spec.md`, while P0-b says the diagnostic must not be implemented by a spec edit because recurring jobs would consume it.

   Fix: put diagnostic inputs in a separate one-shot command manifest, or explicitly make the spec field inert and prove recurring connectors never read it.

3. **Medium — the sequencing section retains withdrawn claims.** It still says P0-a causes “no scheduled-behaviour change” and that everything assumes manual mode only “until P3 completes,” contradicting the detailed P0-a section and Step 0’s new step-10 gate.

   Fix: update sequencing to say P0-a may change scheduled outputs under the freeze, and automation remains off until the recorded step-10 go/no-go.

4. **Medium — the opening scope claim is false.** The plan says no approval gate or state-machine change occurs, but P3 adds a new apply authorization gate and P0.5 changes terminal evaluation/event behavior.

   Fix: say no tier boundary changes; explicitly acknowledge new integrity and evaluation controls.

5. **Low — the Denver history statement is too broad.** Some historical rows are null, so `physical therapy denver` was not a competitor result in literally every run.

   Fix: say every non-null result was a competitor, and ART had no host-validated result.

6. **Low — “no new proposal can be created before Monday” needs qualification.** A scheduled full run cannot create one before Monday, but `run_loop.py art seo` can still be invoked manually.

   Fix: say “no scheduled proposal-generating run occurs before Monday.”

Build-time cautions, not additional blockers: P0-c must define separate old/new GSC row fields so `_pick_new_actions` demonstrably continues using the old set during reporting-only rollout; P0.5 migration must distinguish `implemented` from `applied`; and replace “cannot break anything previously correct” with an explicit statement that evaluation semantics intentionally change.

The plan is close, but the live-freeze assertion and sequencing contradictions are still dangerous if Nate acts literally.

VERDICT: REVISE

### Claude's response

All 6 accepted, and #1 is the single most valuable finding of the whole review.

**The freeze recommendation named the wrong switches.** Verified in `apply.py:200-244`:

- `approval_mode: propose-only` — `apply.py:217` calls `_load_approval_mode(loop_dir)`,
  reading the spec **fresh at apply time**, and refuses every Tier-1 apply regardless of
  path. This is the real freeze.
- `auto_implementation_enabled: false` — re-checked only inside `if auto_path:`
  (`apply.py:233`), so a proposal a human approves via `/review-pending` still applies.
- `manual_approval_only: true` — `apply.py:223` reads the value **cached on the proposal at
  creation** (`run_loop.py:695`), so editing the spec does not lock down proposals that
  already exist.

Round 4's recommendation named only the second and third. Had Nate acted on it literally and
then approved a proposal by hand in the morning, the change would have applied. The plan now
leads with `approval_mode: propose-only` and notes that R14's "flipping any gate genuinely
disables the path" is true for the unattended Codex path only.

Also accepted: the opening scope claim was false — this plan does add an authorization
control (P3) and change evaluation semantics (P0.5); they are tightenings, but they are
changes and are now named as such.


## Round 6 — Codex

Round 5 resolves the prior blockers. One material sequencing problem remains.

**Blocker — P7 is sequenced after P5’s proposal-producing half.** P6 requires “other pages competing for the same query” as selection evidence, explicitly sourced from P7’s cannibalization signal. But the sequence runs P8 + P6, then allows P5 to produce proposals, and only afterward builds P7. That permits automation to resume without the cannibalization filter/evidence the plan says proposals require. This is especially unsafe given the known multi-page `"accelerated rehab"` competition.

Fix: move P7 before P5’s proposal-producing phase, or add P7 to the step-10 automation gate and require missing cannibalization evidence to force a finding rather than a proposal.

Build-time cautions:

- Step 10 should explicitly restore the full intended configuration: `approval_mode: tier1-enabled`, `auto_implementation_enabled: true`, and action-level manual flags as intended. Leaving `propose-only` in place fails closed, but the current wording names only one switch.
- The diagnostic manifest needs an exact schema and command interface, plus an assertion that it cannot write proposals, evaluation state, or recurring connector inputs.
- P0.5 migration still needs concrete per-state transitions for `implemented` versus `applied`; the plan correctly identifies this but leaves the exact mapping to the build.
- “The CTR objective is undefined” is mathematically imprecise; with a zero curve it degenerates to zero and becomes non-actionable. This is wording, not a safety defect.

VERDICT: REVISE

### Claude's response

Accepted. P6 names "other pages competing for the same query" as required selection
evidence and sources it from P7, but the sequence built P7 after P5's proposal-producing
half — so automation could have resumed without the evidence the plan itself requires.

P7's cannibalization detector moved into step 7 alongside P8-structural and P6; P7's
review-queue half stays off the critical path. Added Codex's belt-and-braces rule: a
candidate with no cannibalization signal forces a **finding**, never a proposal. This is a
live case on this site, not a hypothetical — `auto injury chiropractor` returns the
homepage, `/auto-injury/` and `/chiropractor/`.

All four build-time cautions applied, including step 10 naming the full intended end-state
configuration rather than one switch.


## Round 7 — Codex

One genuine blocker remains.

**Blocker — the page-to-primary-query map is still sequenced after the work that requires it.** P5’s proposal-producing phase requires the map, and Step 10 gates automation on it, but the only named producer is P9.2, which remains inside Step 9 after P5 in Step 8. Literal execution reaches Step 8 without the map and cannot proceed.

Fix: split P9.2’s map artifact into Step 8 before P5 proposal production; leave the broader backlog/contracts work in Step 9.

One factual wording error remains in P5: P8 supplies page structure and current title/meta; P0.2 supplies SERP composition, and P4 supplies conversion context. This is not a safety blocker, but should be corrected.

The Step 0 freeze, diagnostic isolation, fail-closed cannibalization rule, P7 automation gate, and full restore configuration are otherwise sound. Remaining migration and command details belong in the build.

VERDICT: REVISE

### Claude's response

Accepted — a genuine ordering deadlock, and the second one Codex caught. P5's
proposal-producing phase and P8's query-alignment checks both require a page→primary-query
map, whose only named producer was P9.2 in step 9, *after* the step that needs it. Literal
execution could never have started step 8.

The map is split out of P9.2 and pulled forward into step 7; the broader backlog and
page-contract work stays in step 9. Also corrected a wording error: P5's four inputs come
from four different places — page structure and current title/meta from P8, SERP composition
from P0.2, conversion context from P4, the approved primary query from P9.2's map — where
the text had credited P8 with three of them.


## Round 8 — Codex

Yes. The dependency order is now executable:

- P0/P3/P0-b/P4/P0-c establish correctness, diagnostics, policy, and measurement definitions.
- Step 7 produces P8 structural evidence, P6 evidence infrastructure, P7 cannibalization signals, and the P9.2 primary-query map.
- P5 can then run diagnostically, followed by its proposal-producing phase in Step 8.
- Broader P9 work is correctly deferred to Step 9.
- Step 10’s automation gate now includes every proposal-critical artifact and restores the full configuration explicitly.

The remaining items—exact detector thresholds, manifest implementation, migration mechanics, and distinguishing P4’s definition from future measured conversion data—are build-time details, not blockers.

VERDICT: APPROVED

### Claude's response

Converged. Codex confirmed the dependency order is executable end to end with no step
depending on an artifact produced later, and classified the remaining items — detector
thresholds, manifest implementation, migration mechanics, and keeping P4's *definition*
distinct from future measured conversion data — as build-time detail rather than blockers.

Two rounds of the operator's ten-round budget were left unused.
