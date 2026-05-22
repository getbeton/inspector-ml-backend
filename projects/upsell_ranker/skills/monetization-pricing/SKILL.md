---
name: monetization-pricing
description: Monetization framework (Verna / Reforge) covering the awareness-to-expansion map, value-metric selection, willingness-to-pay (Van Westendorp), COGS, CAC, CAC payback period by segment, LTV:CAC ratio, packaging tiers, and expansion-revenue / Net Dollar Retention. Use when proposing or reviewing revenue-shaped or pricing-shaped signal hypotheses, expansion-revenue analyses, or when sanity-checking unit economics for a candidate signal.
---

Monetization is the act of generating revenue from your user base through value creation, capture, and optimization. It is distinct from growth (acquiring users) and engagement (retaining users), though all three interact.

These guides explain how to approach search for the best customer segments and monetization opportunities.

## Monetization map

```
Awareness -> Evaluation -> Purchase -> Expansion
```

- **Awareness**: User knows the paid offering exists.
- **Evaluation**: User assesses whether the value justifies the price.
- **Purchase**: User converts to paying customer.
- **Expansion**: Customer increases spend over time (upsell, cross-sell, seat expansion).

Each stage has its own conversion rate. Measure and optimize them independently.

## Value metric

The unit of consumption your pricing is based on. This is the single most important pricing decision. Companies might need different signals based on that.

**Good value metrics:**
- Scale with the customer's success (seats, contacts managed, revenue processed, API calls)
- Are easy for the customer to understand and predict
- Grow naturally as the customer gets more value

**Bad value metrics:**
- Are disconnected from value delivered (flat fee regardless of usage)
- Punish the customer for using the product more
- Are hard to measure or explain

To identify the value metric: ask "what does the customer get more of when they succeed with our product?". This is what they're actually paying for and what they'll use to normalize the price per unit they compare among vendors.

Understanding of value metric is crucial to know why users might want to upgrade at all. You can get it by reading customer's website pages, especially pricing structure.

Once you've identified the value metric, it's worth segmenting users by their price elasticity.

## Willingness to pay (WTP)

Use the Van Westendorp Price Sensitivity Meter. Ask four questions to a sample of target customers:

1. At what price would this be **so cheap** you'd question the quality?
2. At what price is this a **bargain** — great value for money?
3. At what price is this **getting expensive** — you'd still consider it but think hard?
4. At what price is this **too expensive** — you'd never buy it?

Plot cumulative distributions. The intersections define:
- **Point of marginal cheapness**: "too cheap" crosses "expensive"
- **Point of marginal expensiveness**: "bargain" crosses "too expensive"
- **Optimal price point**: "too cheap" crosses "too expensive"
- **Acceptable range**: between marginal cheapness and marginal expensiveness

Segment the results by persona, company size, or use case — WTP varies dramatically across segments.

## Cost of revenue (COGS)

For SaaS, cost of revenue includes everything required to deliver the product to a paying customer:

| Component | Examples |
|-----------|---------|
| **Infrastructure** | Cloud hosting, CDN, data storage, third-party APIs |
| **Support** | Customer support staff, tooling (Zendesk, Intercom) |
| **Customer success** | CSM salaries, onboarding costs, QBR time |
| **Third-party services** | Payment processing fees, data providers, embedded tools |

**Gross margin** = (Revenue - COGS) / Revenue. Healthy SaaS targets 70-85% gross margin. Below 60% signals a structural problem in cost of delivery.

## Customer Acquisition Cost (CAC)

```
CAC = Total sales and marketing spend / Number of new customers acquired
```

Include: salaries (sales + marketing teams), ad spend, tools (CRM, email, ABM), events, content production. Exclude: customer success costs (those are COGS).

Segment CAC by channel and by customer segment. Blended CAC hides the truth — your organic channel might have CAC of $50 while paid has CAC of $2,000.

## CAC payback period

```
CAC Payback (months) = CAC / (Monthly revenue per customer x Gross margin %)
```

This tells you how many months of a customer's gross margin it takes to recoup what you spent acquiring them.

Faster payback with the same metrics means your acquisition budgets are turned around more quickly, which still creates faster growth.

Payback will differ by industry, marketing mix, company stage. It should be factored into your approach to finding signals and best-performing segments.

| Segment | Healthy payback |
|---------|----------------|
| Self-serve / SMB | < 6 months |
| Mid-market | < 12 months |
| Enterprise | < 18 months |

Payback > 18 months for any segment is a red flag. Either CAC is too high, pricing is too low, or gross margin is too thin.

## LTV:CAC ratio

```
LTV = ARPU x Gross margin % x (1 / Monthly churn rate)
```

**Target: LTV:CAC > 3:1.** Below 3:1 the business is spending too much to acquire customers relative to their lifetime value. Above 5:1 you may be under-investing in growth.

## Packaging vs pricing

Packaging = what is included in each tier. Pricing = what each tier costs.

**Good/Better/Best structure:**
- **Good**: Core value, hooks the user, demonstrates the product. Low or free.
- **Better**: Full value for the primary use case. This is where most customers should land.
- **Best**: Advanced features, higher limits, premium support. For power users and larger teams.

The fence between tiers should be a **natural scaling dimension** (users, volume, features that only matter at scale), not artificial restrictions that frustrate customers.

Use this to understand how customers will be guided through the upgrades ladder once they're ready to grow.

## Expansion revenue

Net Dollar Retention (NDR) measures how much existing customer revenue grows or shrinks:

```
NDR = (Starting MRR + Expansion - Contraction - Churn) / Starting MRR
```

NDR > 100% means your existing customers are growing faster than you're losing them. Elite SaaS companies achieve 120-140% NDR.

Expansion levers: seat expansion, usage growth past tier thresholds, upsell to higher tier, cross-sell additional products.

## Sources

Verna, E. Reforge Monetization & Pricing course materials.
Nagle, T. & Muller, G. (2017). *The Strategy and Tactics of Pricing*. Routledge.
