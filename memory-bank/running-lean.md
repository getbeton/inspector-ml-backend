# Running Lean — Ash Maurya

A systematic process for iterating from Plan A to a plan that works, before running out of resources.

This is a mental model for thinking about what in the product gives value to your end customers. Drafting one such canvas for segments provides an insight on the behaviours we want users to take in our product and their end goals/their natural frequency. 

## Core principle

Your product is NOT "the product." Your business model is the product. The solution box is less than one-ninth of the whole picture. Customers care about their problems, not your solution.

## Lean Canvas

A one-page business model with 9 blocks. Sketch it in under 15 minutes. Create one per customer segment.

```
+------------------+------------------+------------------+------------------+------------------+
|   PROBLEM        |   SOLUTION       | UNIQUE VALUE     | UNFAIR ADVANTAGE | CUSTOMER         |
|   Top 3 problems |   Top 3 features | PROPOSITION      | Can't be easily  | SEGMENTS         |
|                  |                  | Single clear     | copied or bought | Target customers |
|                  |                  | compelling       |                  |                  |
|                  |                  | message          |                  |                  |
|   Existing       +------------------+                  +------------------+                  |
|   alternatives   |   KEY METRICS    |                  |   CHANNELS       | Early adopter    |
|                  |   Key activities |                  |   Path to        |                  |
|                  |   you measure    |                  |   customers      |                  |
+------------------+------------------+------------------+------------------+------------------+
|           COST STRUCTURE                               |       REVENUE STREAMS               |
|           Customer Acquisition, Distribution, People   |       Revenue Model, LTV, Margins   |
+--------------------------------------------------------+-------------------------------------+
```

Order of filling: (1) Customer Segments, (2) Problem, (3) UVP, (4) Solution, (5) Unfair Advantage, (6) Channels, (7) Cost Structure, (8) Key Metrics, (9) Revenue Streams.

## Three stages of a startup

| Stage | Key question | Focus |
|-------|-------------|-------|
| 1. Problem/Solution Fit | Do I have a problem worth solving? | Is it a must-have? Will they pay? Can it be solved? |
| 2. Product/Market Fit | Have I built something people want? | Qualitative then quantitative validation |
| 3. Scale | How do I accelerate growth? | Optimize engine of growth |

Before product/market fit: **pivot** (course-correct to find a working plan).
After product/market fit: **optimize** (accelerate a working plan).

## Risk categories

Every box on the canvas maps to one of three risk types:

- **Product risk** (P): Getting the product right — Problem, Solution, UVP, Key Metrics
- **Customer risk** (C): Building a path to customers — Channels, Customer Segments, Early Adopters
- **Market risk** (M): Building a viable business — Cost Structure, Revenue Streams, Unfair Advantage

## Prioritization framework

Rank multiple Lean Canvases (or hypotheses) by these criteria, highest weight first:

1. **Customer pain level** — Is this a must-have? Prioritize segments with acute pain.
2. **Ease of reach** — Can you get to these customers quickly? Faster access = faster learning.
3. **Price / gross margin** — Higher margins mean fewer customers needed to break even.
4. **Market size** — Big enough to justify the effort given your goals.
5. **Technical feasibility** — Can you build the minimum solution? Is the MVP achievable?

## Experiment design

Every experiment is one cycle of the Build-Measure-Learn loop:

1. **Formulate a falsifiable hypothesis** — "X% of [segment] will [action] when shown [artifact]."
2. **Build the smallest artifact** that tests the hypothesis (mock-up, landing page, MVP).
3. **Measure** with a mix of qualitative (interviews) and quantitative (metrics) data.
4. **Learn** — validate or invalidate the hypothesis. Decide: persevere, pivot, or reset.

Before product/market fit, pick **bold outcomes** (maximize learning), not incremental tweaks.

## Three interview types

| Interview | When | Goal |
|-----------|------|------|
| Problem Interview | Before building anything | Validate the problem exists and is painful enough |
| Solution Interview | After problem validation | Test solution mock-ups, nail down pricing, define MVP scope |
| MVP Interview | After MVP is built | Test messaging, pricing, and activation flow face-to-face |

## Metrics

**Actionable metrics** tie specific, repeatable actions to observed results. The 3 A's: Actionable, Accessible, Auditable.

**Vanity metrics** (web hits, total downloads) only go up and to the right — they tell you nothing about what to do next.

**Cohort analysis** is the fix: group users by join date (or any property) and track their lifecycle over time. Cohorts handle traffic fluctuations, show real progress, and enable segmentation.

**Key macro metrics** (Dave McClure's pirate metrics): Acquisition → Activation → Retention → Revenue → Referral (AARRR).

## SaaS pricing guidance

- Start with a **single plan** + free trial. You don't have data to segment yet.
- **Charge from day one.** If you don't charge, people won't take you seriously or use the product.
- Set price against **existing alternatives** (reference points in the customer's mind).
- **LTV must exceed CAC by at least 3x** (David Skok rule of thumb).
- Do a back-of-the-envelope break-even calculation: people costs + hosting / subscription price = customers needed.
- Freemium delays pricing validation and shifts focus to acquisition over retention. Avoid it early.

## Source

Maurya, A. (2012). *Running Lean: Iterate from Plan A to a Plan That Works* (2nd ed.). O'Reilly Media.
