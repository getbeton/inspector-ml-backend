# Agentic upsale opportunities system

## Terms
User - user of our agentic system, usually a company that wants to find upsell opportunities based on various signals from their customers.
May be a B2B or PLG company.  
Agent - our system that analyzes customer behavior and provides upsell opportunities based on the data provided by the user.
Client - company(B2B) or user(PLG) of our user services that may be potential upsell targets we want to highlight.

## Description
This system will rank upsell opportunities based on the client's behavior that is observed in our user Posthog DWH. 
It uses basic signal analysis to observe clients interactions with users product and predict which products or services are most likely to be upsold to each client.


Entrypoint of this system is upsell_agent agent.
Since we consider two main ways of upsale at the moment: B2B and PLG we also analyze what exactly our user sells and how before proceeding.
We gather this data based on user input parameter for company website or try to deduce it via email.
Then we make initial assumptions on what and how to upsell and forward this summary to data analyst agent.  

Data analyst performs basic EDA in Posthog DWH, connects datasources to provide comprehensive signal summary on each client. 
Consider it a basic version of pd.describe() but with capabilities to adapt to different parameter namings that may be
tailored to specific user database naming conventions. Summary and assumptions on available data is made by this agent as
well as suggestions on possible joins and filtering. 

For the MVP demo we stop after DWH exploration and return a structured JSON summary of discovered tables, columns,
sample values, and join suggestions, plus a website summary from upsell_agent.

## Agent schema

MVP: upsell_agent(user exploration) -> data analyst(eda) -> upsell_agent(demo JSON)

## Next steps

Left off status and next focus (for tomorrow):

Implemented the LLM‑as‑judge loop for signal_agent, added a safe HogQL executor with a hard query budget, and tightened DWH join discovery formatting.

  What changed:

  - Added safe query tool with budget + read‑only enforcement in upsell_ranker_full/v0.0.2/signal_agent/tools.py.
      - Env vars: SIGNAL_AGENT_MAX_QUERIES (default 6), SIGNAL_AGENT_MAX_ROWS (default 200).
  - Rebuilt signal_agent as a LoopAgent with worker + judge in upsell_ranker_full/v0.0.2/signal_agent/agent.py.
      - Worker must run real SQL via run_posthog_query and return detections.
      - Judge enforces actual query usage + read‑only SQL before exiting the loop.
  - Updated DWH EDA join proposals to explicit table/column pairs + overlap evidence in upsell_ranker_full/v0.0.2/dwh_analyst/tools.py.
  - Signal agent is now paused for the MVP demo; dwh_analyst returns table/column summaries and join suggestions directly.

  Notes on join discovery improvements already applied:

  - Empty samples are only surfaced as "<empty>" if all sampled values are empty.
  - Join proposals are now explicit pairs like:
      - tables: ["authsupabase_users", "peoplegooglesheets_people_clean"]
      - columns: ["email", "email_addresses"]

  Signal agent resume (after MVP demo):

  1. Re-enable dwh_analyst -> signal_agent handoff and restore signal_summary in the final JSON.
  2. Set SIGNAL_AGENT_MAX_QUERIES and SIGNAL_AGENT_MAX_ROWS in your runtime env.
  3. Re-run a full flow and check that signal_summary.signals[].sql + detections are populated from real query results.
  4. Decide if the judge should enforce stricter rules (e.g., min detections per signal).

## Demo API payload/response (MVP)

Request payload (example):

```json
{
  "website_url": "https://example.com",
  "posthog_token": "phx_your_personal_api_key",
  "posthog_host": "https://us.posthog.com",
  "posthog_project_id": "12345"
}
```

Response shape (example):

```json
{
  "schema_version": "v0.0.2",
  "upsell_summary_text": "Example summary of the website and where to focus the DWH exploration.",
  "company_summary": {
    "website": "https://example.com",
    "business_model": "B2B",
    "product": "Customer analytics platform",
    "icp": "Mid-market SaaS",
    "assumptions": [
      "Pricing details were not visible on the homepage."
    ]
  },
  "dwh_request_suggestions": {
    "summary": "Request monthly revenue tables and account ownership mappings to refine join accuracy.",
    "items": [
      {"topic": "revenue_table", "detail": "Expose Stripe invoices or MRR snapshots per account."},
      {"topic": "account_owner", "detail": "Map accounts to CSM/AE ownership for prioritization."}
    ]
  },
  "dwh_analysis": {
    "schema_version": "v0.0.1",
    "summary_text": "Found 6 tables with user and billing context; join candidates suggest email-based links.",
    "dwh_summary": {
      "status": "ok",
      "notes": "Sampled 5 rows per table with 8 columns each.",
      "tables": [],
      "joins": [],
      "metrics": []
    },
    "table_summaries": [],
    "join_candidates": []
  },
  "dwh_summary_text": "Primary candidate tables include users and invoices; joins likely via email or customer_id.",
  "notes": []
}
```

## Demo test template

If you have a running ADK api_server, create a session and then call `/run` (adjust paths if your server expects a specific route):

```bash
export API_URL="http://127.0.0.1:8000"
export APP_NAME="upsell_agent"
export USER_ID="u_123"
export SESSION_ID="s_123"
export WEBSITE_URL="https://example.com"
export POSTHOG_TOKEN="phx_your_personal_api_key"
export POSTHOG_HOST="https://us.posthog.com"
export POSTHOG_PROJECT_ID="12345"

curl -sS -X POST "$API_URL/apps/$APP_NAME/users/$USER_ID/sessions/$SESSION_ID" \
  -H "Content-Type: application/json" \
  -d '{"created_by": "mvp_demo"}'

curl -sS -X POST "$API_URL/run" \
  -H "Content-Type: application/json" \
  -d '{
    "appName": "'"$APP_NAME"'",
    "userId": "'"$USER_ID"'",
    "sessionId": "'"$SESSION_ID"'",
    "newMessage": {
      "role": "user",
      "parts": [{
        "text": "Analyze website: '"$WEBSITE_URL"' and use PostHog token '"$POSTHOG_TOKEN"' with host '"$POSTHOG_HOST"' and project '"$POSTHOG_PROJECT_ID"'. Return JSON only."
      }]
    }
  }' | jq .
```
