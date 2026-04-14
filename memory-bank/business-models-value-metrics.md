# Business Models & Value Metrics — Signal Discovery Guide

A lookup table for the agent: given a company's business model, which value metric drives their monetization, and what events should the signal discovery pipeline prioritize?

## Value metric identification

Ask: **"What does the customer get more of when they succeed with this product?"** The answer is the value metric. The value metric is the unit of consumption pricing is based on. Different value metrics make different events matter for signal discovery.

## Business model → value metric → target events

| Business Model | Value Metric | What expansion looks like | Key events to search for in DWH |
|---|---|---|---|
| Seat-based SaaS | Active seats / users | More users invited, departments onboarded | `invite_sent`, `user_signed_up`, `seat_activated`, `$identify` with team properties |
| Usage-based (metered) | API calls, compute hours, messages | Higher consumption volume, tier upgrades | `api_request`, `job_completed`, `message_sent`, events with volume/count properties |
| Transaction-based | Transactions processed, GMV | More transactions, higher values | `payment_completed`, `order_created`, `invoice_generated` |
| Storage-based | GB stored, records managed | Data accumulation, approaching limits | `file_uploaded`, `record_created`, storage-related properties |
| Contact/record-based | Contacts, leads, subscribers | List growth, more imports | `contact_created`, `list_imported`, `enrichment_completed` |
| Feature-gated (tiered) | Plan tier / feature access | Hitting gates, attempting advanced features | `feature_gate_shown`, `upgrade_clicked`, `advanced_feature_attempted` |
| Platform / marketplace | Listings, accounts, integrations | More sellers/buyers, more connections | `listing_published`, `account_connected`, `integration_enabled` |
| Revenue-share | Revenue processed, bookings | Higher GMV through platform | `booking_completed`, `payout_processed` |
| Credits / token-based | Credits consumed, tokens used | Faster burn rate, top-ups | `credits_purchased`, `credits_used`, `low_balance_warning` |
| Hybrid (seat + usage) | Seats + usage overage | Both seat expansion and usage growth | Combine seat-based + usage-based events |
| Freemium-to-paid | Usage hitting free-tier limits | Limit encounters, conversion triggers | `limit_reached`, `upgrade_modal_shown`, `trial_started` |
| Event-volume SaaS | Events tracked, data points | More instrumentation, more sources | `$pageview`, `$autocapture`, `source_connected`, custom events |

## How to apply during signal discovery

1. After `upsell_agent` extracts `CompanySummary.business_model`, classify the company into one of the rows above.
2. During DWH exploration, prioritize searching for the events listed in the matching row.
3. When proposing `CandidateSignalDraft` entries, ensure the signal measures changes in the identified value metric.
4. For hybrid models, propose signals for each value metric separately, then look for compound signals that combine both axes.

## Signal quality criteria by value metric

A good signal for any value metric must be:
- **Comparative**: measured against a baseline (previous period, cohort average, plan limit)
- **Entity-scoped**: tied to a specific account, user, or workspace
- **Time-windowed**: 7-day, 30-day, or quarterly aggregation
- **Actionable**: high/low values suggest a concrete follow-up (upsell, churn intervention, feature nudge)

See `docs/2026-04-14 business models, value metrics and target events/README.md` for the full reference with worked examples and PostHog-specific event patterns.
