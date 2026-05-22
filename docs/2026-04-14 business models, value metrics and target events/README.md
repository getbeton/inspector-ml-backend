# Business Models, Value Metrics & Target Events

## Purpose

A reference for understanding which product events matter for which B2B software business models. Different companies monetize differently, and the unit of value they charge for (the **value metric**) determines which user behaviors signal expansion or churn risk.

**Audience:** Alex (manual company analysis) and, in future iterations, the Inspector ML agent (automated signal discovery).

**How it connects to the pipeline:** The `upsell_agent` extracts a `business_model` field from company websites (`CompanySummary.business_model`). This document maps that business model to value metrics and target events, giving the `signal_agent` direction on which events are worth discovering as signals.

## Terminology

| Term | Definition |
|------|------------|
| **Business model** | How the company monetizes: the combination of pricing structure (seat-based, usage-based, etc.) and go-to-market motion (PLG, sales-led, hybrid). |
| **Value metric** | The unit of consumption pricing is based on. The thing that grows when the customer succeeds with the product. See `memory-bank/monetization-pricing.md` for the full framework. |
| **Target event** | A product event that directly reflects a change in the value metric. These are the events worth measuring and aggregating into signals. |
| **Signal** | A time-comparative, entity-level aggregation of target events at a specific grain (account, user, workspace). What the `signal_agent` discovers and promotes. |

The chain: **value metric** determines which **target events** matter, which determines what **signals** the agent should look for.

## Master Table

| Business Model | Value Metric | Expansion Lever | Target Events (Generic) | Target Events (PostHog Examples) | Signal Patterns |
|---|---|---|---|---|---|
| **Seat-based SaaS** | Active seats / users | Seat expansion, department rollout | User invited, seat activated, seat deactivated, role assigned | `invite_sent`, `user_signed_up`, `$identify` with team property, `seat_activated` | Seat growth % MoM; active/total seat ratio; invite-to-activation rate |
| **Usage-based (metered)** | API calls, compute hours, messages, requests | Organic usage growth, tier upgrade | API call made, compute job started/completed, message sent, request processed | `api_request`, `job_completed`, `message_sent`, `compute_hours_used` | Usage velocity (calls/day trend); % of quota consumed; usage acceleration week-over-week |
| **Transaction/volume-based** | Transactions processed, GMV, payments | More transactions, higher ACV deals | Payment processed, order created, invoice generated, refund issued | `payment_completed`, `order_created`, `invoice_generated` | Transaction volume trend; avg transaction value growth; transaction frequency per account |
| **Storage-based** | GB stored, records managed, files hosted | Data accumulation, compliance needs | File uploaded, record created, storage threshold approached | `file_uploaded`, `record_created`, `storage_warning_shown` | Storage growth rate; days-to-tier-limit; upload frequency trend |
| **Contact/record-based** | Contacts, leads, subscribers, accounts managed | List growth, new campaigns, new segments | Contact added, list imported, contact enriched, segment created | `contact_created`, `list_imported`, `enrichment_completed` | Contact growth rate; % of contact limit used; import frequency |
| **Feature-gated (tiered)** | Plan tier / feature access level | Hitting feature limits, needing advanced capabilities | Feature gate hit, upgrade prompt shown, advanced feature attempted, downgrade | `feature_gate_shown`, `upgrade_clicked`, `advanced_feature_attempted` | Gate-hit frequency; advanced feature attempt rate; time between gate hits |
| **Platform / marketplace** | Listings, storefronts, connected accounts, integrations | More sellers/buyers, more listings, more connections | Listing created, storefront activated, account connected, integration enabled | `listing_published`, `account_connected`, `integration_enabled` | Active listing growth; seller/buyer activation rate; marketplace liquidity ratio |
| **Revenue-share / take-rate** | Revenue processed, bookings, GMV through platform | Higher GMV flowing through platform | Booking completed, payout issued, commission earned | `booking_completed`, `payout_processed`, `commission_calculated` | GMV growth trend; take-rate stability; payout frequency |
| **Credits / token-based** | Credits consumed, tokens used, compute units | Credit top-ups, plan upgrades, auto-refill | Credits purchased, credits consumed, balance low warning, auto-refill triggered | `credits_purchased`, `credits_used`, `low_balance_warning` | Burn rate (credits/day); days-to-zero; top-up frequency; auto-refill adoption |
| **Hybrid (seat + usage)** | Seats + overage on usage metric | Both seat expansion and usage growth | Combines seat-based + usage-based events | Both categories above | Dual signal: seat growth AND usage acceleration; overage frequency |
| **Freemium-to-paid** | Active usage hitting free-tier limits | Conversion triggers, limit encounters | Limit reached, upgrade prompt shown, feature blocked, trial started | `limit_reached`, `upgrade_modal_shown`, `trial_started` | Time-to-limit; limit-hit frequency per account; conversion funnel drop-off points |
| **Event-volume SaaS** | Events tracked, data points ingested, log lines | More instrumentation, more properties, more sources | Event ingested, new event type created, source connected, schema changed | `$pageview`, `$autocapture`, custom events, `source_connected` | Ingestion volume trend; event type diversity; new source activation rate |

## How Value Metrics Drive Signal Discovery

### The core insight

The same raw event can be critical or irrelevant depending on the company's value metric. A `user_signed_up` event is a high-priority expansion signal for a seat-based SaaS (more seats = more revenue), but it's just a vanity metric for a usage-based company where revenue scales with API calls, not headcount.

### The causal chain

```
Value Metric
  --> What "expansion" looks like for this company
    --> Which events indicate that expansion is happening
      --> What aggregations become useful signals
```

For a **seat-based** company: expansion = more seats. Target events = invites sent, users onboarded. Signal = "accounts where seat count grew >20% in the last 30 days." The entity grain is the account, the time window is monthly, and the comparison baseline is the previous month.

For a **usage-based** company: expansion = higher consumption. Target events = API calls, compute jobs. Signal = "accounts where daily API volume exceeded 80% of quota for 3+ consecutive days." Same PostHog data, completely different signal.

### Compound and hybrid models

Many B2B SaaS companies have multiple value metrics. A CRM might charge per seat (primary) but also per number of contacts stored (secondary). When this happens:

1. **Prioritize the metric tied to pricing.** If the company charges per seat, seat-related events are the primary expansion signals.
2. **Use secondary metrics as leading indicators.** Contact growth in a seat-based CRM might predict future seat expansion ("they're running out of capacity for their current team size").
3. **Watch for metric interaction.** When both seat count and usage are growing, the account is expanding on multiple axes — this is a stronger upsell signal than either alone.

### From events to actionable signals

Not every target event becomes a useful signal. The signal must be:

- **Comparative**: measured against a baseline (previous period, cohort average, plan limit)
- **Entity-scoped**: tied to a specific account, user, or workspace — not a global aggregate
- **Time-windowed**: computed over a meaningful period (7-day, 30-day, quarterly)
- **Actionable**: high or low values should suggest a specific follow-up (upsell conversation, churn intervention, feature adoption nudge)

## Reading Guide

When analyzing a new company:

1. **Get the business model** from the `upsell_agent` output (`CompanySummary.business_model` field). It will say something like "B2B SaaS, PLG, seat-based pricing."
2. **Match to a row** in the master table above. If the model is hybrid, check multiple rows.
3. **Know which events to prioritize** during DWH exploration. The "Target Events" columns tell you what to look for in the data warehouse schema.
4. **Use the signal patterns** as hypotheses. These are the kinds of aggregations the `signal_agent` should be proposing as candidate signals.

If the business model doesn't cleanly fit any row, the company likely has a novel or evolving pricing model. In that case, identify the value metric directly: ask "what does the customer get more of when they succeed with this product?" The answer is the value metric, and the events that track changes in that quantity are your target events.
