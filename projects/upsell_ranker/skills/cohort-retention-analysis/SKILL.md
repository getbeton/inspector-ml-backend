---
name: cohort-retention-analysis
description: PostHog cohort and retention analysis playbook — defining cohorts, choosing between N-day and rolling retention, interpreting retention curves, and connecting retention shape to product health diagnoses (habit formation, pre-churn, power-user emergence). Use when proposing or reviewing retention-style signals, when designing cohort-based SQL, or when reading retention curves for diagnosis. Load the references/retention-metrics.md resource for the six concrete comparison dimensions and the moving-median formula.
---

A theoretical guide for measuring and comparing user retention across cohorts. Apply these concepts when querying PostHog data to produce or analyze retention reports.

## What is a cohort?

A group of users who share a common characteristic within a defined period. The most common cohort key is **signup period** (day/week/month/quarter/year). Other useful cohort keys: acquisition channel, plan type, geography, first feature used.

It usually makes sense to look at least at 1-2 different temporal windows. They can be defined based on the natural frequency of events in a dataset.

You can also combine temporal resolution (to account for changes in external environment like growth of traffic prices or new campaigns launched) and qualitative dimensions (e.g. traffic channel or customer size).

Sometimes you also combine different events – e.g. match people with cohorts based on **signup date** but calculate share of users who did a **purchase** on Nth week.

In this example, retention based on signup date would show retention of *all* users, while retention based on first purchase data would show retention of buyers only. The difference is that the former will show how many users that came during particular period converted to first purchase at all after N periods, while the latter would track conversion of *repeated* purchase after N periods.

## Two types of retention

### N-day retention (classic / bracket retention)

**Definition:** Of users in cohort C, what percentage performed the target action after exactly N days after first occurrence of event (or in week N)?

```
N-day retention = Users active on day N / Cohort size
```

- Shows **stickiness at specific time points** (day 1, day 7, day 30).
- A user who is active on day 14 but not day 7 counts as retained on day 14 but NOT on day 7.
- The curve drops sharply early, then (ideally) flattens into a **retention floor** — the percentage of users who become habitual. Most companies want this because if retention plateaus then each new cohort pays indefinitely and they stack up.

### Rolling retention (unbounded retention)

**Definition:** Of users in cohort C, what percentage performed the target action on day N **or any day after N**?

```
Rolling retention on day N = Users active on day N or later / Cohort size
```

- Shows **long-term survival** — whether users come back at all, regardless of exact timing.
- A user who skips day 7 but returns on day 20 IS counted as retained at day 7.
- Always >= N-day retention for the same time period.

### When to use which

Nth-period retention helps you understand natural usage frequency and how it changes over a user's lifetime. Tracking Nth-day retention rate makes sense when there's at least 4-5 periods worth of data in the dataset.

Rolling retention helps you estimate the speed with which users leave your application for good.

| Question | Use |
|------|-----|
| "Are users coming back regularly?" | N-day retention |
| "Are users still alive at all?" | Rolling retention |
| "Is our product sticky on a daily/weekly/monthly basis?" | N-day retention |
| "What is the long-term survival rate?" | Rolling retention |

Present both side-by-side. The gap between them reveals how many users are retained but on irregular schedules.

## Interpretation guide

These are "ELI5" rules of thumb:

- **Flattening curve** (retention stabilizes after initial drop): healthy. The flat part is your retention floor — these are your core users.
- **Continuously declining curve** (never flattens): the product is not reaching habit formation. Investigate activation flow and core value delivery.
- **Smile curve** (dips then rises): uncommon but possible if re-engagement campaigns or delayed aha-moments kick in. Verify this isn't a data artifact.
- **Frequency rising while user % drops**: power users are forming but casual users are churning. May be fine for a niche product, concerning for a broad one.
- **Frequency dropping while user % holds**: early warning of disengagement wave. Users are technically active but doing less. Churn will follow.
- **Multiple cohorts move together simultaneously**: some external event might have occurred (e.g. marketing campaign, PR crisis, new product/feature launch) which has influenced all of the users.

## How the agent should use this

When asked to analyze retention:

1. Define the cohort key (usually signup week) and the target action (the key activity that represents value delivery). It's always good to use both signups and activity events as starting events; the first will show how much time passes between users converting for the first time, the second the real frequency between 1st and 2nd conversion.
2. Query PostHog for all six dimensions across the relevant time window (see references/retention-metrics.md). You should already have the key events from preliminary research.
3. Compute both N-day and rolling retention for each of the 6 metrics on at least 10-12 intervals since start.
4. Compute moving medians across the last 10 cohorts (see references/retention-metrics.md for the formula).
5. Add more dimensions into the equation and compare retention of users across those dimensions.
6. Present the retention curves, the six-metric table, and an interpretation citing the patterns above.
7. Flag any cohorts that deviate significantly from the moving median — these may correlate with product changes, campaigns, or bugs.

Load `references/retention-metrics.md` when you need the concrete formulas for the six comparison dimensions or for moving medians.
