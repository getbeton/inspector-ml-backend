# Six comparison dimensions and moving medians

Concrete formulas for the cohort-retention-analysis skill. Load this resource when about to write retention SQL or compute moving medians.

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

Calculate all 6 metrics above in at least 5 temporal resolutions — daily, weekly, monthly, quarterly, annual.

## Moving medians across cohorts

One good predictor of growth is the median value of N previous cohorts at week T. If it grows, then your retention is going up as well, which will predict the stacked/joint/total metric value for all N cohorts in period T.

It also makes sense to group retention rate by `periods since signup` and plot those with colour coding. Result is based on what you use for T (or Ox):

- `T of signup month` as `Ox` will show values of all periods since signup; the charts will show how the Nth week of retention behaves over time.
- `T of event month` as `Ox` will show retention value for all cohorts in a particular month; the chart will show users of different ages together.
