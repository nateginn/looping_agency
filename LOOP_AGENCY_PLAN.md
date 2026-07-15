# Loop Agency Plan

## What We Learned From The Tutorial

The transcript describes "loop engineering" as a repeating cycle with four practical parts:

1. Build or change something.
2. Verify it against a real metric.
3. Learn from the result.
4. Repeat on a schedule until a stop condition is reached.

The strongest idea in the tutorial is not "let AI do everything."
It is "give an agent one narrow job, one measurable KPI, the tools it needs, and a memory of prior experiments."

The examples shown were:

- Product loop: build -> verify -> learn
- SEO loop: write/build -> check Search Console / competitor data -> learn
- Ads loop: create content / adjust budget -> check Meta data -> learn

The screenshots also show an existing packaged example:

`npx atom-eve add seo-improver --target eve`

That appears to be a prebuilt SEO agent example from Atom Eve, and it is useful as a reference implementation even if we do not adopt that stack directly.

## What This Means For Loop Agency

Loop Agency should not begin as "one super-agent that runs a whole company."
It should begin as a small platform for running a few durable business loops safely.

The right first goal is:

"Build a reusable loop system that can run one business process at a time, track experiments, and report outcomes."

## Recommended Strategy

### Phase 1: Define The Core Loop Framework

Build a simple shared structure that every loop will use:

- `objective`: what business result we want
- `metric`: how success is measured
- `inputs`: APIs, files, prompts, credentials, repos
- `actions`: what the agent is allowed to change
- `memory`: log of prior runs, decisions, and results
- `schedule`: how often the loop runs
- `stop_condition`: when it should stop or ask for review
- `approval_mode`: what needs human signoff before publishing or spending money

Deliverable:

- A standard loop spec file format, likely Markdown, YAML, or JSON
- A run log format for memory and auditing
- A per-loop workspace structure

### Phase 2: Start With One Minimal Viable Loop

Do not start with ads or product feedback first.
Start with the SEO loop because it is:

- lower risk
- relatively cheap
- easy to measure
- slow enough for human review

First loop candidate:

- Goal: improve rankings for a small set of target keywords
- Verify metric: Google Search Console positions / clicks / impressions
- Inputs: website repo, content pages, target keywords, optional competitor data
- Cadence: weekly or monthly
- Safety rule: no auto-publish without approval at first

Deliverable:

- A single SEO loop that can inspect content, propose changes, record experiments, and prepare a review summary

### Phase 3: Build Human-In-The-Loop Operations

Before adding more loops, add operating discipline:

- pre-run checklist
- post-run summary
- experiment log
- rollback path
- approval gate for risky actions

This is important because the transcript repeatedly assumes reversibility.
If we cannot clearly see what changed and undo it, the loop is not safe enough yet.

Deliverable:

- Review workflow for every loop run
- Change summaries and diffs
- Manual approve / reject step

### Phase 4: Add Additional Loops In Order Of Safety

Recommended order:

1. SEO loop
2. Content / social post ideation loop
3. Internal product improvement loop
4. Ads loop with limited budget
5. Broader autonomous product-feedback loop

Why this order:

- SEO and content loops are slower and easier to supervise
- product loops require tighter repo controls and testing
- ad loops can burn money quickly if guardrails are weak

## Technical Architecture Recommendation

Keep the first version simple.
We do not need a complex agent platform on day one.

### Suggested V1 Components

- `loops/`
  - one folder per loop
- `specs/`
  - loop definitions
- `memory/`
  - experiment history and outcome logs
- `reports/`
  - human-readable summaries
- `connectors/`
  - API wrappers for tools like Search Console
- `prompts/`
  - reusable system and task prompts

### Suggested Loop Runtime

Each run should do this:

1. Load loop spec
2. Load previous memory
3. Pull current metrics
4. Compare against prior result
5. Generate recommendations
6. Make draft changes or proposals
7. Produce a report
8. Wait for approval or stop automatically if not safe to continue

## Guidance On Atom Eve

The Atom Eve SEO improver shown in the screenshot is worth treating as:

- inspiration
- a scaffold to inspect
- a prompt reference

But not necessarily the core foundation of Loop Agency yet.

Reason:

- We do not yet know whether we want to adopt its framework deeply
- We should first define our own loop contract and workflow
- Then we can either borrow ideas from Atom Eve or integrate it later

## Concrete Next Steps

### Immediate

1. Create the Loop Agency project structure.
2. Define a `loop spec` format.
3. Define a `run report` format.
4. Define a `memory log` format.
5. Stub the first loop: `seo-improver`.

### After That

1. Connect Google Search Console.
2. Add keyword targets and site context.
3. Run a dry-run mode that only produces recommendations.
4. Review results manually.
5. Enable controlled content edits after the dry run looks good.

## Proposed First Success Criteria

We should consider the first milestone complete when:

- one loop can run end-to-end locally
- it reads a spec
- it pulls or accepts metrics
- it writes a memory log
- it produces a human review report
- it does not make unapproved public changes

## The Main Trap To Avoid

The tutorial is exciting, but the biggest practical risk is starting too wide.

If we try to build:

- SEO automation
- ads optimization
- full product feedback
- agency-wide orchestration

all at once, we will likely end up with unclear state, weak safety, and noisy outputs.

The better path is:

"One loop, one KPI, one approval process, one memory model."

Once that works, the rest becomes repeatable.
