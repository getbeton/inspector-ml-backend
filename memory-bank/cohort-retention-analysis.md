# Cohort Retention Analysis

A theoretical guide for measuring and comparing user retention across cohorts. The agent should apply these concepts when querying PostHog data to produce retention reports.

## What is a cohort?

A group of users who share a common characteristic within a defined period. The most common cohort key is **signup period** (day/week/month/quarter/year). Other useful cohort keys: acquisition channel, plan type, geography, first feature used.

It usually makes sense to look at least at 1-2 different temporal windows. They can be defined based on the natural frequency of events in a dataset. 

You can also combine temporal resolution (to account for changes in external environment like growth of traffic prices or new campaigns launched) and qualitative dimensions (e.g. traffic channel or customer size)

Sometimes you also combine different events – e.g. match people with cohorts based on **signup date** but calculate share of users who did a **purchase** on Nth week

## Two types of retention

### N-day retention (classic / bracket retention)

**Definition**: Of users in cohort C, what percentage performed the target action after exactly N days after first occurrence of event (or in week N)?

```
N-day retention = Users active on day N / Cohort size
```

- Shows **stickiness at specific time points** (day 1, day 7, day 30).
- A user who is active on day 14 but not day 7 counts as retained on day 14 but NOT on day 7.
- The curve drops sharply early, then (ideally) flattens into a **retention floor** — the percentage of users who become habitual. Most companies want this because if retention plateus – then each new cohort pays indefinitely and they stack up

### Rolling retention (unbounded retention)

**Definition**: Of users in cohort C, what percentage performed the target action on day N **or any day after N**?

```
Rolling retention on day N = Users active on day N or later / Cohort size
```

- Shows **long-term survival** — whether users come back at all, regardless of exact timing.
- A user who skips day 7 but returns on day 20 IS counted as retained at day 7.
- Always >= N-day retention for the same time period.

### When to use which

Nth-period retention helps you understand natural usage frequency and how it changes over user's lifetime. So, tracking Nth-day retention rate definitely makes sense when there's at lezst 4-5 periods worth of data in the dataset. 

Rolling retention helps you estimate speed with which leave your application for good. 

| Question | Use |
|------|-----|
| "Are users coming back regularly?" | N-day retention |
| "Are users still alive at all?" | Rolling retention |
| "Is our product sticky on a daily/weekly/monthly etc basis?" | N-day retention |
| "What is the long-term survival rate?" | Rolling retention |

Present both side-by-side. The gap between them reveals how many users are retained but on irregular schedules.

## Six comparison dimensions

For each cohort, at each time period (week 0, week 1, week 2, ...), compute these six metrics:

### 1. Absolute users retained

Raw count of unique users who were active in that period.

```sql
COUNT(DISTINCT user_id) WHERE cohort = C AND period = N
```

Why it matters: shows the actual scale of your retained base. A 50% retention rate on a cohort of 10 is very different from 50% on a cohort of 10,000.

### 2. Percentage of users retained

```
% users = Active users in period N / Cohort size (period 0)
```

Why it matters: normalizes across cohorts of different sizes. This is the classic retention curve.

### 3. Absolute actions performed

Raw count of target events performed by the cohort in that period.

```sql
COUNT(event_id) WHERE cohort = C AND period = N
```

Why it matters: even if user count is flat, action volume might be growing (power users deepening) or shrinking (disengaging before churning).

### 4. Percentage of actions

```
% actions = Actions in period N / Actions in period 0 (or period 1)
```

Why it matters: shows whether engagement intensity is maintained relative to the honeymoon period.

### 5. Frequency (actions per active user)

```
Frequency = Total actions in period N / Active users in period N
```

Why it matters: separates "how many users" from "how deeply they use it." Rising frequency with flat user retention = power users forming. Falling frequency with flat user count = pre-churn signal.

### 6. Weeks as time axis

Calculate all 6 metrics above in at least 5 temporal resolutions – daily, weekly, monthly, quarterly, annual

## Moving medians across cohorts

One of good predictors of growth is a median value of N previous cohorts at week T. If it grows – then your retention is going up as well which will predict stacked/joint/total metric value for all N cohorts in the period T. 

It also makes sense to group retention rate by `periods since signup` and plot those with colour coding. Result is based on what will you use for T (or Ox):

- `T of signup month` as `Ox` will show values of all periods since signup; so the charts will show how Nth week of retention behaves over time
- `T of event month` as `Ox` will show retention value for all cohorts in a particular month; this means the chart will show users of different age together

## Interpretation guide

These are "ELI5" rules of thumb:

- **Flattening curve** (retention stabilizes after initial drop): healthy. The flat part is your retention floor — these are your core users.
- **Continuously declining curve** (never flattens): the product is not reaching habit formation. Investigate activation flow and core value delivery.
- **Smile curve** (dips then rises): uncommon but possible if re-engagement campaigns or delayed aha-moments kick in. Verify this isn't a data artifact.
- **Frequency rising while user % drops**: power users are forming but casual users are churning. May be fine for a niche product, concerning for a broad one.
- **Frequency dropping while user % holds**: early warning of disengagement wave. Users are technically active but doing less. Churn will follow.
- **Multuple cohorts move together simultaneously**: some external event might have occurred (e.g. marketin campaign, PR crisis, new product/feature/launch etc) which has influenced all of the users

## How the agent should use this

When asked to analyze retention:

1. Define the cohort key (usually signup week) and the target action (the key activity that represents value delivery). It's always good to use both signups and activity event as starting event; the first will show how much time passes between users convert for the first time, the second – real frequency between 1st and 2nd conversion
2. Query PostHog for all six dimensions across the relevant time window. You should have known the key events from the preliminary research
3. Compute both N-day and rolling retention for each 6 metrics on at least 10-12 intervals since start
4. Compute moving medians across the last 10 cohorts.
5. Add more dimensions into the equation and compare retention of users across those dimensions
5. Present the retention curves, the six-metric table, and an interpretation citing the patterns above.
6. Flag any cohorts that deviate significantly from the moving median — these may correlate with product changes, campaigns, or bugs.
