---
name: rice-prioritization
description: Score and rank candidate signals or hypotheses by Reach x Impact x Confidence / Effort (Intercom's RICE framework). Use when comparing competing hypotheses, deciding which signal to test first, or assembling a final ranked candidate list. Output should cite the data source for Reach and justify Impact and Confidence.
---

Developed at Intercom for scoring and ranking ideas, features, and experiments by expected impact relative to effort.

This is a mental model for comparing hypotheses to test first when doing product or revenue analytics.

## Formula

```
RICE Score = (Reach x Impact x Confidence) / Effort
```

## Components

### Reach

How many users or events will this affect in a defined time period (e.g., per quarter)?

Use real data when possible. Pull from PostHog: unique users who hit the relevant event or page in the last 90 days. If no data exists, estimate conservatively and note it.

### Impact

How much will this change behavior or outcomes for each affected user?

| Label | Multiplier | Meaning |
|-------|-----------|---------|
| Massive | 3 | Fundamentally changes the workflow |
| High | 2 | Significant improvement to a core task |
| Medium | 1 | Noticeable improvement |
| Low | 0.5 | Minor improvement |
| Minimal | 0.25 | Barely noticeable |

Impact is the most subjective component. Use interview data, support tickets, or observed drop-off rates to justify the choice. Avoid defaulting to "Medium" for everything.

### Confidence

How sure are you about the Reach and Impact estimates?

| Level | Percentage | When to use |
|-------|-----------|-------------|
| High | 100% | Backed by data (PostHog metrics, customer interviews, prior experiments) |
| Medium | 80% | Some supporting evidence but gaps remain |
| Low | 50% | Mostly intuition, no hard data |

If confidence is below 50%, the hypothesis needs more research before scoring, not more debate.

### Effort

Estimated person-months of work to ship. Include design, engineering, QA, and rollout. Round to nearest 0.5.

Smaller effort scores produce higher RICE scores, which correctly biases toward quick wins — but don't game it by underestimating.

## Applying RICE to hypotheses

When the agent generates hypotheses from PostHog data analysis:

1. **List hypotheses** — one row per hypothesis.
2. **Estimate each component** — cite the data source for Reach, justify Impact, state Confidence level, estimate Effort.
3. **Compute scores** — rank descending.
4. **Present the top 3-5** with reasoning, not just numbers. The human reviewer needs to understand *why* each component was scored that way.

## Example

| Hypothesis | Reach | Impact | Confidence | Effort | RICE |
|-----------|-------|--------|------------|--------|------|
| Add onboarding checklist to reduce activation drop-off | 500/qtr | 2 (High) | 80% | 1 mo | 800 |
| Rebuild settings page for faster load | 200/qtr | 0.5 (Low) | 100% | 2 mo | 50 |
| Personalized dashboard based on role | 400/qtr | 1 (Medium) | 50% | 3 mo | 67 |

The onboarding checklist wins by a wide margin and should be tested first.

## Limitations

- RICE does not account for strategic alignment, dependencies, or sequencing. Use it as an input to prioritization, not the final word.
- It rewards high-reach, low-effort items. For foundational or platform work that enables future features, apply a strategic override and note it explicitly.

## Source

Rice, S. (2018). RICE: Simple prioritization for product managers. Intercom Blog.
