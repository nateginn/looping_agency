# Google Business Profile — human-run structural checklist

Referenced from `PLAN-SEO-PROGRAM-INTEGRATION.md` P1 since 2026-08-12 ("the checklist half
costs nothing and is not blocked on anything") and created as part of the GBP Posts loop
design (`projects/art/loops/gbp/`). **This file is a human checklist, not a connector.**
Nothing in `tools/` reads it, and nothing here is ever automated — see "Never automated, at
any point" below. It exists because a Post can only ever be a content-freshness/engagement
lever (see `projects/art/loops/gbp/spec.md`'s framing note); the structural profile gaps that
actually govern local-pack eligibility — category, verification, NAP, review/photo volume —
have no mechanical fix and must be worked by a human against the live GBP dashboard.

For `art`, cross-reference `projects/art/gbp-profiles.md` (the durable record of what the
profiles actually contain) and `projects/art/loops/gbp/known-gaps.yaml` (the machine-readable
sibling that routes each known gap to either this checklist or a content-gap Post proposal)
before working this list, so a gap already tracked there isn't duplicated here from scratch.

## Per-location items (run for each real location — Greeley and Denver for `art`; never for a
footer-only address like UNC Campus, which is not a GBP profile at all)

- [ ] **Primary category** confirmed against the live profile (not assumed from a prior
  assessment) and unambiguous — a profile has exactly one primary category. If a second
  category appears to be marked primary with no services attached, that is a configuration
  error to fix, not a second primary.
- [ ] **Secondary categories** reviewed for completeness and relevance; remove anything that
  doesn't reflect a real service line.
- [ ] **The hidden ~300-character service-description field** is filled in for every listed
  service (not just the service name) — this field is easy to miss and commonly left blank.
- [ ] **Products-as-services** — where the profile supports a "Products" tab distinct from
  "Services", confirm the clinic's service lines are represented there too if relevant, not
  just under Services.
- [ ] **Service areas** are genuine and realistic — a radius the clinic can actually serve,
  not a padded list of nearby cities chasing keyword coverage.
- [ ] **Weekly posts** — a Post published at least once a week, per Google's own cadence
  recommendation independent of whether an SEO-loop signal suggested a topic that week.
- [ ] **15–20+ real work photos** per profile (not generic clip-art) — a general best-practice
  target, not a competitor-derived number — this is a genuine Prominence factor per Google's
  Relevance/Distance/Prominence ranking model, and — per `gbp-profiles.md`'s "why this
  matters" note — the actual measured gap for `art` today (Greeley 7 photos, Denver 5).
- [ ] **Messaging response speed** — if messaging is enabled on the profile, confirm replies
  happen quickly; a slow or unanswered messaging inbox is a visible negative signal.
- [ ] **The CID footer link** (or equivalent GBP link) is present and correct on the
  corresponding website page for that location.
- [ ] **Hours are accurate** — cross-check every day, not just business-critical days; a
  wrong "closed" reads as unavailable to a potential patient and to Google's own eligibility
  signals.
- [ ] **NAP (name/address/phone) consistency** between the site footer (see
  `locations-detected.json`, the SEO loop's automated footer-address check), `spec.md`'s
  `locations`, and the live profile. A mismatch anywhere undermines Prominence.

## Monthly

- [ ] **Competitor-profile audit** — a human review of the pack incumbents' profiles (review
  count/recency, photo count, categories, posting cadence) to keep this checklist's targets
  realistic. This is a human observation exercise only — **no competitor data is ever
  recorded into any file `tools/` reads or writes** (standing decision 4, `CURRENT-WORK.md`).
  Keep any notes from this audit in a human document (or nowhere), never in `known-gaps.yaml`
  or any loop artifact.
- [ ] **Review count and recency** trend reviewed. Fresh reviews are a genuine leading
  indicator independent of total count; a listing that stopped accumulating reviews recently
  is worth noting even if its total is still ahead of a competitor's.

## Never automated, at any point

Per `PLAN-SEO-PROGRAM-INTEGRATION.md` P1's explicit guardrails — none of the following is
ever performed by any tool in this workspace, regardless of what future phases build:

- **Review solicitation.** Automated review requests can trigger a platform gating action
  that wipes a profile's accumulated reviews. Human-only, always.
- **Business-name changes.** A name edit is a high-risk, rarely-needed action with real
  ranking and trust consequences if done incorrectly. Human-only, always.
- **Service-area claims.** Expanding or editing service areas is a judgment call about what
  the business can genuinely serve, not a data-driven optimization. Human-only, always.
