# Memory Bank

Reference knowledge for the Inspector ML agent. Each file is a self-contained guide on a business framework or analytical method. The agent should load the relevant file when the task requires that domain knowledge.

## Guides

| File | When to use |
|------|-------------|
| `running-lean.md` | Evaluating business model hypotheses, prioritizing risks, designing experiments, assessing problem/solution and product/market fit |
| `founding-sales.md` | Qualifying sales opportunities, building sales narratives, structuring outreach, understanding pipeline stages and ICP |
| `rice-prioritization.md` | Scoring and ranking hypotheses, features, or experiments by expected impact |
| `monetization-pricing.md` | Analyzing pricing strategy, willingness to pay, CAC payback, cost of revenue, packaging decisions |
| `cohort-retention-analysis.md` | Building retention reports from PostHog data, comparing N-day vs rolling retention, computing moving averages across cohorts |
| `business-models-value-metrics.md` | Mapping business models to value metrics and target events for signal discovery — which events matter for seat-based vs usage-based vs transaction-based companies |

## How to use

1. Identify which domain the current task falls into.
2. Load the corresponding guide.
3. Apply the frameworks and formulas to the data available via PostHog SQL queries.
4. When writing recommendations, cite the framework being applied so the human reviewer can follow the reasoning.
