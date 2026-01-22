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

This is forwarded to the Signal agent that picks most promising
upsell opportunities according to generic analysis and writes SQL queries that perform basic signal analysis. Since such
queries may be harmfull we pair it with LLM judge that checks each query for potential harmful patterns, to prevent their
execution. Queries are then executed in search of possible signals to which then signal agent writes a short description
of the signal and its significance. 

Finally this summary, paired with EDA summary and information about the company is returned
back to the upsell_agent to generate final rankings for upsell opportunities, based on signals, combined with a sales
readable explanation, potential salesplays and next steps for the sales team to perform.

## Agent schema

upsell_agent(user exploration) -> data analyst(eda) -> signal agent(signal analysis) -> upsell_agent(rankings+steps)

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
  - Instruction now asks to pass table_summaries + join_candidates to signal agent in upsell_ranker_full/v0.0.2/dwh_analyst/agent.py.

  Notes on join discovery improvements already applied:

  - Empty samples are only surfaced as "<empty>" if all sampled values are empty.
  - Join proposals are now explicit pairs like:
      - tables: ["authsupabase_users", "peoplegooglesheets_people_clean"]
      - columns: ["email", "email_addresses"]

  If you want the judge to enforce a stricter rule (e.g., at least N detections per signal), say the word.

  Next steps:

  1. Set SIGNAL_AGENT_MAX_QUERIES and SIGNAL_AGENT_MAX_ROWS in your runtime env.
  2. Re-run a full flow and check that signal_summary.signals[].sql + detections are populated from real query results.